"""Durable, isolated per-query runner for the two-round live autonomy study.

Uses the production orchestrator and baseline metrics, with immutable results,
per-query process boundaries and no secret configuration written to disk.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

from tests import baseline_runner as baseline


ROOT = Path(__file__).resolve().parents[1]


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Publish only a fully serialized file. The unique temp is on the same FS;
    # link is atomic and refuses replacement of an existing immutable result.
    import tempfile
    payload = json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n"
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(name, path)
    finally:
        os.unlink(name)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def source_manifest() -> dict[str, str]:
    names = sorted(set(subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode().split("\0")))
    return {
        name: digest((ROOT / name).read_bytes())
        for name in names
        if name and (ROOT / name).is_file() and (
            (name.endswith(".py") and not name.startswith("tests/"))
            or name.startswith("skills/") or name == "config.example.json"
        )
    }


def planned_queries() -> list[dict[str, Any]]:
    facts = baseline.read_csv_rows(str(ROOT / baseline.DEFAULT_ANSWER_DATASET))
    opened = baseline.read_csv_rows(str(ROOT / baseline.DEFAULT_OPEN_DATASET))
    rows = []
    # The study froze the first 20 fact questions (final001-final020); the
    # dataset grew on 2026-09-09 (quality plan Q6-02) without changing them.
    for fact, task in zip(facts[: len(opened)], opened, strict=True):
        rows.extend([dict(fact, dataset="final_answer"), dict(task, dataset="open_task")])
    return rows


def child_run(config: dict, row: dict, mode: str, output: str, soft_timeout: int) -> None:
    os.setsid()
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    clock = time.monotonic()
    # Keep provider diagnostics private and separate from user-facing summaries.
    with (out / "execution.log").open("a", encoding="utf-8", buffering=1) as log:
        sys.stdout = log
        sys.stderr = log
        from utils.audit_log import sanitize_audit_value
        from tests.study_transport import TransportObserver

        observer = TransportObserver(out / "transport.jsonl")
        observer.install()

        cfg = copy.deepcopy(config)
        cfg.setdefault("conversation", {})["checkpoint_path"] = str(out / "checkpoint.sqlite")
        cfg.setdefault("audit", {}).update({
            "enabled": True, "dir": str(out / "audit"),
            "include_answer": True, "include_full_result": True,
            "max_files": 0, "max_bytes_per_record": 0,
        })
        uploads = out / "uploads"
        uploads.mkdir(exist_ok=True)
        event = threading.Event()
        timer = threading.Timer(soft_timeout, event.set)
        timer.daemon = True
        result: dict = {}
        outcome = "returned"
        timer.start()
        try:
            orchestrator = baseline.build_orchestrator(cfg, data_path=str(uploads))
            result = orchestrator.answer(
                row["query"], num_search_results=5, per_source_search_results=5,
                num_retrieved_docs=5, max_tokens=4000, temperature=0.2,
                allow_search=True, autonomy_mode=mode, cancel_event=event,
                conversation_id=f"study-{mode}-{row['qid']}",
            )
            if event.is_set():
                outcome = "deadline_cancelled"
        except Exception as exc:
            import traceback
            traceback.print_exc()
            outcome = "exception"
            result = {"answer": "", "llm_error": f"{type(exc).__name__}: {exc}"}
        finally:
            timer.cancel()
        metrics = {
            **baseline.extract_llm_stats(result), **baseline.extract_loop_stats(result),
            "external_api_calls": baseline.extract_external_api_calls(result),
            "latency_ms": baseline.extract_latency_ms(result),
            "wall_ms": round((time.monotonic() - clock) * 1000, 2),
            "llm_error": sanitize_audit_value(result.get("llm_error")),
            "usd": None,
            **observer.summary(),
        }
        if row["dataset"] == "final_answer":
            metrics.update(baseline.score_answer_quality(result, row.get("must_include_facts", "")))
        captured = metrics["llm_calls_with_tokens"]
        calls = metrics["llm_call_count"]
        metrics["token_capture_complete"] = calls > 0 and captured == calls
        if not captured:
            for key in ("input_tokens", "output_tokens", "total_tokens", "peak_input_tokens"):
                metrics[key] = None
        write_new(out / "result.json", {
            "schema_version": 1, "qid": row["qid"], "dataset": row["dataset"],
            "requested_mode": mode, "query": row["query"],
            "started_at": started, "finished_at": utc_now(), "outcome": outcome,
            "metrics": metrics, "result": sanitize_audit_value(result, max_depth=None),
        })


def summarize(round_dir: Path, manifest: dict) -> dict:
    results = []
    missing = []
    for run in manifest["runs"]:
        path = round_dir / "runs" / run["id"] / "result.json"
        if path.exists():
            results.append(json.loads(path.read_text()))
        else:
            missing.append(run["id"])
    groups = {}
    for dataset in ("final_answer", "open_task"):
        for mode in ("guided", "autonomous"):
            rows = [r for r in results if r["dataset"] == dataset and r["requested_mode"] == mode]
            states: dict[str, int] = {}
            for r in rows:
                state = r["metrics"].get("loop_status") or r["outcome"]
                states[state] = states.get(state, 0) + 1
            groups[f"{dataset}/{mode}"] = {
                "rows": len(rows), "states": states,
                **{key: baseline.summarise([r["metrics"].get(key) for r in rows]) for key in (
                    "fact_coverage", "latency_ms", "wall_ms", "total_tokens", "input_tokens",
                    "output_tokens", "llm_call_count", "external_api_calls", "loop_iterations",
                    "advisory_gap_count", "compactions", "peak_context_ratio",
                    "transport_total_tokens", "transport_input_tokens", "transport_output_tokens",
                    "transport_cached_input_tokens", "transport_requests", "transport_http_errors",
                )},
            }
    return {"planned": len(manifest["runs"]), "completed": len(results), "missing": missing,
            "groups": groups, "human_review": "not_performed"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", required=True)
    parser.add_argument("--round", choices=("r1", "r2"), required=True)
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--soft-timeout", type=int, default=600)
    parser.add_argument("--max-pairs", type=int)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    if not args.study.replace("-", "").isalnum():
        parser.error("study must contain only letters, digits and hyphens")
    round_dir = ROOT / "runtime" / "baseline" / args.study / args.round
    round_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = round_dir / "manifest.json"
    if args.summary:
        print(json.dumps(summarize(round_dir, json.loads(manifest_path.read_text())), ensure_ascii=False, indent=2))
        return
    import fcntl
    lock_handle = (round_dir / "runner.lock").open("a")
    fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    config = baseline.load_config(args.config)
    rows = planned_queries()
    runs = []
    for index, row in enumerate(rows):
        modes = ["guided", "autonomous"]
        if (index + (args.round == "r2")) % 2:
            modes.reverse()
        runs.extend({"id": f"{row['qid']}-{mode}", "mode": mode, "row": row} for mode in modes)
    current = {
        "source_files": source_manifest(),
        "config_digest": digest(json.dumps(config, sort_keys=True).encode()),
        "dataset_digests": {name: digest((ROOT / name).read_bytes()) for name in (
            baseline.DEFAULT_ANSWER_DATASET, baseline.DEFAULT_OPEN_DATASET,
        )},
        "protocol_digest": digest((ROOT / "docs/reports/autonomy_evaluation_20260908/protocol.md").read_bytes()),
    }
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        for key, value in current.items():
            if manifest[key] != value:
                raise SystemExit(f"Cannot resume changed study input: {key}")
        if manifest["workers"] != args.workers or manifest["soft_timeout"] != args.soft_timeout:
            raise SystemExit("Cannot resume with different worker/deadline settings")
    else:
        provider = config["LLM_PROVIDER"]
        manifest = {
            "schema_version": 1, "created_at": utc_now(), "study": args.study, "round": args.round,
            "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
            **current, "runs": runs, "provider": provider,
            "model": config["providers"][provider].get("model"),
            "autonomy_config": config.get("autonomy"),
            "workers": args.workers, "soft_timeout": args.soft_timeout,
            "hard_timeout": args.soft_timeout + 60,
            "parameters": {"num_results": 5, "max_tokens": 4000, "temperature": 0.2},
        }
        write_new(manifest_path, manifest)
    # Include new source files that do not yet belong to HEAD. Hashes alone
    # would not let a later reviewer reconstruct the measured implementation.
    bundle = round_dir / "source.zip"
    if not bundle.exists():
        import zipfile
        with zipfile.ZipFile(bundle, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, expected in manifest["source_files"].items():
                content = (ROOT / name).read_bytes()
                if digest(content) != expected:
                    raise SystemExit(f"Source changed before freeze: {name}")
                archive.writestr(name, content)
            for name in ("tests/baseline_runner.py", "tests/autonomy_study.py", "tests/study_transport.py",
                         "docs/reports/autonomy_evaluation_20260908/protocol.md", baseline.DEFAULT_ANSWER_DATASET,
                         baseline.DEFAULT_OPEN_DATASET):
                archive.writestr(name, (ROOT / name).read_bytes())
    queue = manifest["runs"][:args.max_pairs * 2] if args.max_pairs else manifest["runs"]
    queue = [r for r in queue if not (round_dir / "runs" / r["id"] / "result.json").exists()]
    context = mp.get_context("spawn")
    active = []
    print(json.dumps({"study": args.study, "round": args.round, "pending": len(queue)}, ensure_ascii=False), flush=True)
    try:
        while queue or active:
            while queue and len(active) < args.workers:
                if source_manifest() != manifest["source_files"]:
                    raise SystemExit("Production source changed during measurement; refusing mixed results")
                run = queue.pop(0)
                out = round_dir / "runs" / run["id"]
                process = context.Process(target=child_run, args=(config, run["row"], run["mode"], str(out), args.soft_timeout))
                process.start()
                active.append((process, run, time.monotonic()))
                print(f"START {run['id']} pid={process.pid}", flush=True)
            for process, run, started in list(active):
                expired = time.monotonic() - started > manifest["hard_timeout"]
                if process.is_alive() and not expired:
                    continue
                if expired and process.is_alive():
                    os.killpg(process.pid, signal.SIGTERM)
                    process.join(2)
                    if process.is_alive():
                        os.killpg(process.pid, signal.SIGKILL)
                process.join()
                path = round_dir / "runs" / run["id"] / "result.json"
                if not path.exists():
                    write_new(path, {
                        "qid": run["row"]["qid"], "dataset": run["row"]["dataset"],
                        "requested_mode": run["mode"], "query": run["row"]["query"],
                        "outcome": "harness_timeout" if expired else "process_error",
                        "finished_at": utc_now(), "exitcode": process.exitcode,
                        "metrics": {"wall_ms": round((time.monotonic() - started) * 1000, 2), "usd": None},
                        "result": {"answer": ""},
                    })
                result = json.loads(path.read_text())
                m = result["metrics"]
                print(f"DONE {run['id']} {result['outcome']} status={m.get('loop_status')} tokens={m.get('total_tokens')} sec={m.get('wall_ms', 0)/1000:.1f}", flush=True)
                active.remove((process, run, started))
            time.sleep(0.25)
    finally:
        for process, _, _ in active:
            if process.is_alive():
                # Children own process groups; never signal the shell/root group.
                os.killpg(process.pid, signal.SIGTERM)
                process.join(3)
                if process.is_alive():
                    os.killpg(process.pid, signal.SIGKILL)
                    process.join()
    report = summarize(round_dir, manifest)
    if not report["missing"] and not (round_dir / "summary.json").exists():
        write_new(round_dir / "summary.json", report)
    print(json.dumps({"completed": report["completed"], "planned": report["planned"]}), flush=True)


if __name__ == "__main__":
    main()
