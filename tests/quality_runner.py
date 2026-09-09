"""Quality evaluation runner: schedules the existing scripts into one run directory (plan Q5-01).

    python -m tests.quality_runner --suite offline                 # zero cost, asserts no network
    python -m tests.quality_runner --suite validity --max-queries 5
    python -m tests.quality_runner --suite search,local            # real search / embedding runs
    python -m tests.quality_runner --suite answer --datasets final_answer,open_task
    python -m tests.quality_runner --suite loop,cost --source runtime/baseline/autonomy-20260908-measured/r2
    python -m tests.quality_runner --suite all --tag formal --dry-run
    python -m tests.quality_runner --suite answer --quality-config config.quality.local.json

Flag defaults come from the ``runner`` block of ``config.quality.json``
(``--quality-config`` / ``ISE_QUALITY_CONFIG`` point elsewhere); an explicit flag
still wins, and an unknown key in that block is fatal. Credentials stay in
``config.json``. The file's path and digest go into ``run_meta.json``.

Suites: validity / search / local / offline / answer / loop / cost / reliability / safety / all.
Every run writes ``runtime/quality/<date>-<tag>/run_meta.json`` (commit, secret-free
config summary, dataset digests, rubric version, judge model, searchFallback chain,
autonomy mode, concurrency and timeouts). Results are written exclusively; a resumed
run skips artefacts that already exist. Real suites refuse to start when the
provider credit ledger already exceeds ``providerUsage.daily_credit_limits``.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.quality.common import (  # noqa: E402
    ROOT,
    apply_config_defaults,
    build_run_meta,
    load_config,
    load_quality_config,
    new_run_dir,
    quality_config_path,
    quality_defaults,
    read_csv_rows,
    sha256_file,
    today,
    utc_now,
    write_json,
    write_jsonl,
)

SUITES = ("validity", "search", "local", "offline", "answer", "loop", "cost", "reliability", "safety")
REAL_SUITES = {"validity", "search", "local", "answer", "reliability"}
ANSWER_DATASETS = {
    "final_answer": "dataset/final_answer_dataset.csv",
    "open_task": "dataset/open_task_dataset.csv",
    "abstention": "dataset/abstention_set.csv",
    "hard_loop": "dataset/hard_loop_set.csv",
    "multi_turn": "dataset/multi_turn_set.csv",
}
OFFLINE_DATASETS = [
    "dataset/query_analysis_gold.csv", "dataset/source_tier_gold.csv", "dataset/official_domain_gold.csv",
    "dataset/local_chunk_gold.csv", "dataset/route_intent_dataset.csv", "dataset/full_text_trigger_dataset.csv",
    "dataset/adversarial_pages/index.csv", "tests/fixtures/official_domains_replay.sqlite",
]
PY = sys.executable


def parse_args() -> argparse.Namespace:
    # Two-stage parse: --quality-config decides the defaults the real parser starts from.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--quality-config", default=None)
    quality_config = load_quality_config(pre.parse_known_args()[0].quality_config)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quality-config", default=None, help="Evaluation defaults file (default config.quality.json; also ISE_QUALITY_CONFIG).")
    parser.add_argument("--suite", required=True, help="Comma-separated suites or 'all'.")
    parser.add_argument("--tag", default="quality")
    parser.add_argument("--run-dir", default=None, help="Explicit run directory (default runtime/quality/<date>-<tag>).")
    parser.add_argument("--config", default=None)
    parser.add_argument("--source", default=None, help="Answer records for loop/cost/safety when not produced by this run.")
    parser.add_argument("--datasets", default="final_answer,open_task", help="Answer suite datasets: " + ",".join(ANSWER_DATASETS))
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--num-results", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=4000)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--autonomy", choices=["guided", "autonomous"], default=None)
    parser.add_argument("--soft-timeout", type=int, default=600, help="Seconds before a node-boundary cancel is requested.")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3, help="Reliability suite: repeats per question.")
    parser.add_argument("--multi-turn", action="store_true", help="Answer suite: also consume dataset/multi_turn_set.csv (3 turns per group).")
    parser.add_argument("--skip-review", action="store_true", help="Answer suite: do not run the v4 judge.")
    parser.add_argument("--judge-model", default="glm-5.2")
    parser.add_argument("--judge-provider", default="opencode-go")
    parser.add_argument("--data-path", default="tests/fixtures/local_corpus")
    parser.add_argument("--dry-run", action="store_true", help="Print the commands without executing anything.")
    apply_config_defaults(parser, quality_defaults(quality_config, "runner"), source=str(quality_config_path(pre.parse_known_args()[0].quality_config)))
    return parser.parse_args()


def selected_suites(raw: str) -> List[str]:
    tokens = [token.strip() for token in raw.split(",") if token.strip()]
    if "all" in tokens:
        return list(SUITES)
    unknown = [token for token in tokens if token not in SUITES]
    if unknown:
        raise SystemExit(f"unknown suite(s): {', '.join(unknown)}")
    return tokens


def run_command(command: List[str], *, dry_run: bool, log: Path, cwd: Path = ROOT) -> int:
    printable = " ".join(shlex.quote(part) for part in command)
    print(f"[quality_runner] {printable}", flush=True)
    if dry_run:
        return 0
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"\n=== {utc_now()} {printable}\n")
        handle.flush()
        completed = subprocess.run(command, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT, text=True)
    return completed.returncode


def credit_preflight(config: Dict[str, Any]) -> Dict[str, Any]:
    """Refuse real runs when today's known credits already exceed a configured limit."""
    from tests.quality.provider_usage import check_daily_limits, load_usage_rows, summarize_usage

    block = config.get("providerUsage") if isinstance(config.get("providerUsage"), dict) else {}
    limits = block.get("daily_credit_limits") if isinstance(block.get("daily_credit_limits"), dict) else {}
    summary = summarize_usage(load_usage_rows(block.get("dir") or "runtime/provider_usage"), days=1)
    violations = check_daily_limits(summary, limits)
    return {"limits": limits, "today": summary["days"], "violations": violations}


# ------------------------------------------------------------------ suites
def suite_offline(run_dir: Path, args: argparse.Namespace) -> Dict[str, Any]:
    """Run every zero-cost evaluator in-process under the transport observer (must stay at 0 requests)."""
    from tests.study_transport import TransportObserver

    results: Dict[str, Any] = {}
    if args.dry_run:
        for name in ("analysis_eval", "tiering_eval", "preflight_eval", "routing_eval", "injection_eval", "local_gold_check", "dataset_lint"):
            print(f"[quality_runner] (in-process) tests.quality.{name} -> {run_dir}")
        print(f"[quality_runner] {PY} -m pytest -q -m quality_offline --junitxml={run_dir}/offline_pytest.xml")
        return results
    observer = TransportObserver(run_dir / "offline_transport.jsonl")
    observer.install()
    try:
        from tests.quality import analysis_eval, local_gold_check, preflight_eval, tiering_eval

        results["analysis_eval"] = analysis_eval.run()
        write_json(run_dir / "analysis_eval.json", results["analysis_eval"])
        results["evidence_eval_offline"] = tiering_eval.run()
        write_json(run_dir / "evidence_eval_offline.json", results["evidence_eval_offline"])
        results["preflight_eval"] = preflight_eval.run()
        write_json(run_dir / "preflight_eval.json", results["preflight_eval"])
        results["local_gold_check"] = local_gold_check.check_gold()
        write_json(run_dir / "local_gold_check.json", results["local_gold_check"])
        for module_name, output in (("routing_eval", "routing_eval.json"), ("injection_eval", "injection_eval.json"), ("dataset_lint", "dataset_lint.json")):
            try:
                module = __import__(f"tests.quality.{module_name}", fromlist=["run"])
                payload = module.run()
                results[module_name] = payload
                write_json(run_dir / output, payload)
            except ImportError:
                results[module_name] = {"status": "module missing"}
    finally:
        observer.uninstall()
    network = observer.summary()
    results["network"] = {"external_requests_total": network["external_requests_total"], "llm_requests": network["transport_requests"]}
    if network["external_requests_total"] or network["transport_requests"]:
        raise SystemExit(f"offline suite made network requests: {network}")
    junit = run_dir / "offline_pytest.xml"
    code = run_command([PY, "-m", "pytest", "-q", "-m", "quality_offline", "-p", "no:cacheprovider", f"--junitxml={junit}"], dry_run=False, log=run_dir / "offline_pytest.log")
    fault = {"returncode": code}
    if junit.is_file():
        import xml.etree.ElementTree as ET

        cases = [case for case in ET.parse(junit).iter("testcase") if "fault_injection" in (case.get("classname") or "")]
        passed = sum(1 for case in cases if not [child for child in case if child.tag in {"failure", "error"}])
        fault.update({"cases": len(cases), "passed": passed, "pass_rate": (passed / len(cases)) if cases else None})
    results["fault_injection"] = fault
    write_json(run_dir / "offline_results.json", {key: value for key, value in results.items() if key in {"network", "fault_injection"}} | {"pytest_returncode": code})
    return results


def suite_validity(run_dir: Path, args: argparse.Namespace) -> None:
    command = [PY, "-m", "tests.quality.validity", "--run-dir", str(run_dir), "--max-queries", str(args.max_queries or 5), "--num-results", str(args.num_results), "--max-tokens", str(args.max_tokens), "--temperature", str(args.temperature), "--autonomy", args.autonomy or "guided"]
    if args.config:
        command += ["--config", args.config]
    if (run_dir / "validity.json").is_file():
        print("[quality_runner] validity.json exists; skipping (resume)")
        return
    run_command(command, dry_run=args.dry_run, log=run_dir / "validity.log")


def suite_search(run_dir: Path, args: argparse.Namespace) -> None:
    for name, dataset, gold in (
        ("minimal", "tests/search_quality_minimal_search_queries.txt", None),
        ("gold_doc", "dataset/gold_doc_dataset.csv", "dataset/gold_doc_dataset.csv"),
        ("web_gold_zh", "dataset/web_gold_zh.csv", "dataset/web_gold_zh.csv"),
    ):
        output = run_dir / f"search_collect_{name}.json"
        if output.is_file():
            print(f"[quality_runner] {output.name} exists; skipping (resume)")
            continue
        command = [PY, "tests/search_quality_pipeline.py", "collect", "--output-file", str(output), "--num-results", str(args.num_results), "--force-search", "--show-timings", "--max-tokens", str(args.max_tokens), "--temperature", str(args.temperature)]
        command += ["--dataset-file", dataset] if dataset.endswith(".csv") else ["--queries-file", dataset]
        if gold:
            command += ["--gold-doc-file", gold]
        if args.autonomy:
            command += ["--autonomy", args.autonomy]
        if args.config:
            command += ["--config", args.config]
        run_command(command, dry_run=args.dry_run, log=run_dir / "search.log")
    all_providers = run_dir / "search_collect_all_providers.json"
    if not all_providers.is_file():
        command = [PY, "tests/search_quality_pipeline.py", "collect", "--all-providers", "--dataset-file", "dataset/gold_doc_dataset.csv", "--gold-doc-file", "dataset/gold_doc_dataset.csv", "--output-file", str(all_providers), "--num-results", str(args.num_results)]
        if args.config:
            command += ["--config", args.config]
        run_command(command, dry_run=args.dry_run, log=run_dir / "search.log")
    print("[quality_runner] annotate search_collect_*.json (dataset/annotations/search_<date>.json), then: "
          f"{PY} tests/search_quality_pipeline.py evaluate --annotations-file <annotated> --gold-doc-file dataset/gold_doc_dataset.csv --output-file {run_dir}/search_report.json")


def suite_local(run_dir: Path, args: argparse.Namespace) -> None:
    output = run_dir / "local_rag_eval.json"
    if output.is_file():
        print("[quality_runner] local_rag_eval.json exists; skipping (resume)")
        return
    command = [PY, "tests/local_chunk_grid_search.py", "--data-path", args.data_path, "--dataset-file", "dataset/local_chunk_gold.csv", "--top-k", "3,5", "--output-file", str(output)]
    if args.config:
        command += ["--config", args.config]
    run_command(command, dry_run=args.dry_run, log=run_dir / "local.log")


def _answer_rows(names: List[str], *, multi_turn: bool) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name in names:
        path = ANSWER_DATASETS.get(name)
        if not path or not (ROOT / path).is_file():
            print(f"[quality_runner] dataset {name} not found; skipped")
            continue
        for row in read_csv_rows(ROOT / path):
            rows.append(dict(row, dataset=name))
    if multi_turn and (ROOT / ANSWER_DATASETS["multi_turn"]).is_file():
        for row in read_csv_rows(ROOT / ANSWER_DATASETS["multi_turn"]):
            rows.append(dict(row, dataset="multi_turn"))
    return rows


def suite_answer(run_dir: Path, args: argparse.Namespace, config: Dict[str, Any]) -> None:
    names = [part.strip() for part in args.datasets.split(",") if part.strip()]
    rows = _answer_rows(names, multi_turn=args.multi_turn)
    if args.max_queries:
        rows = rows[: args.max_queries]
    records_dir = run_dir / "answer_records"
    if args.dry_run:
        print(f"[quality_runner] (in-process) answer {len(rows)} rows -> {records_dir} -> answer_details.jsonl; judge v4 via tests.quality_review")
        return
    records_dir.mkdir(parents=True, exist_ok=True)
    from tests import baseline_runner as baseline
    from tests.autonomy_study import write_new
    from tests.study_transport import TransportObserver
    from utils.audit_log import sanitize_audit_value

    cfg = copy.deepcopy(config)
    cfg.setdefault("audit", {}).update({"enabled": True, "dir": str(run_dir / "audit"), "include_answer": True, "include_full_result": True, "max_files": 0})
    cfg.setdefault("conversation", {})["checkpoint_path"] = str(run_dir / "checkpoint.sqlite")
    uploads = run_dir / "uploads"
    uploads.mkdir(exist_ok=True)
    observer = TransportObserver(run_dir / "transport.jsonl")
    observer.install()
    orchestrator = baseline.build_orchestrator(cfg, data_path=str(uploads))
    mode = args.autonomy or (config.get("autonomy") or {}).get("mode", "guided")
    conversation_ids: Dict[str, str] = {}
    for index, row in enumerate(rows, start=1):
        qid = str(row.get("qid") or f"row{index}")
        dataset = row["dataset"]
        turn = str(row.get("turn_index") or "")
        run_id = f"{qid}-{mode}" + (f"-t{turn}" if turn else "")
        out = records_dir / f"{run_id}.json"
        if out.exists():
            continue
        query = str(row.get("query") or "").strip()
        conversation_id = None
        if dataset == "multi_turn":
            group = str(row.get("group_id") or qid.rsplit("-", 1)[0])
            conversation_id = conversation_ids.setdefault(group, f"quality-{group}-{mode}")
        print(f"[quality_runner/answer] {index}/{len(rows)} {run_id} {query}", flush=True)
        started = utc_now()
        clock = time.monotonic()
        llm_before = len(observer.records)
        ext_before = len(observer.external_records)
        event = threading.Event()
        timer = threading.Timer(args.soft_timeout, event.set)
        timer.daemon = True
        timer.start()
        outcome = "returned"
        try:
            result = orchestrator.answer(
                query, num_search_results=args.num_results, per_source_search_results=args.num_results, num_retrieved_docs=args.num_results,
                max_tokens=args.max_tokens, temperature=args.temperature, allow_search=True, autonomy_mode=mode, cancel_event=event,
                conversation_id=conversation_id or f"quality-{run_id}",
            )
            if event.is_set():
                outcome = "deadline_cancelled"
        except Exception as exc:  # noqa: BLE001 - failures are data
            outcome = "exception"
            result = {"answer": "", "llm_error": f"{type(exc).__name__}: {exc}", "control": {}}
        finally:
            timer.cancel()
        llm_records = observer.records[llm_before:]
        ext_records = observer.external_records[ext_before:]
        metrics = {
            **baseline.extract_llm_stats(result), **baseline.extract_loop_stats(result),
            "external_api_calls": baseline.extract_external_api_calls(result), "latency_ms": baseline.extract_latency_ms(result),
            "wall_ms": round((time.monotonic() - clock) * 1000, 2), "llm_error": sanitize_audit_value(result.get("llm_error")), "usd": None,
            "transport_requests": len(llm_records), "transport_total_tokens": sum(int((r.get("usage") or {}).get("total_tokens") or 0) for r in llm_records),
            "transport_usage_complete": bool(llm_records) and all(r.get("usage") for r in llm_records if r.get("status") == 200),
            "external_requests_total": len(ext_records),
        }
        if dataset == "final_answer":
            metrics.update(baseline.score_answer_quality(result, str(row.get("must_include_facts") or "")))
        write_new(out, {
            "schema_version": 1, "qid": qid, "dataset": dataset, "requested_mode": mode, "query": query, "row": row,
            "started_at": started, "finished_at": utc_now(), "outcome": outcome, "metrics": metrics,
            "result": sanitize_audit_value(result, max_depth=None),
        })
    observer.uninstall()
    from tests.quality.common import iter_study_results, normalize_answer_record, read_json

    details = []
    for path in sorted(records_dir.glob("*.json")):
        record = read_json(path)
        row = normalize_answer_record(record)
        row["run_id"] = path.stem
        row["category"] = (record.get("row") or {}).get("task_type") or (record.get("row") or {}).get("category") or row.get("dataset")
        details.append(row)
    write_jsonl(run_dir / "answer_details.jsonl", details)
    if not args.skip_review:
        review_cmd = [PY, "-m", "tests.quality_review", "--source", str(run_dir), "--provider", args.judge_provider, "--model", args.judge_model]
    if args.quality_config:
        review_cmd += ["--quality-config", args.quality_config]
    run_command(review_cmd + (["--config", args.config] if args.config else []), dry_run=False, log=run_dir / "review.log")
    for module, output in (("citation_eval", "citation_eval.json"), ("evidence_eval", "evidence_eval.json"), ("loop_eval", "loop_eval.json"), ("cost_eval", "cost.json"), ("fetch_eval", "fetch_eval.json")):
        run_command([PY, "-m", f"tests.quality.{module}", "--source", str(run_dir), "--output-file", str(run_dir / output)], dry_run=False, log=run_dir / "evaluators.log")


def suite_recompute(run_dir: Path, args: argparse.Namespace, modules: List[tuple]) -> None:
    source = args.source or str(run_dir)
    for module, output in modules:
        run_command([PY, "-m", f"tests.quality.{module}", "--source", source, "--output-file", str(run_dir / output)], dry_run=args.dry_run, log=run_dir / "evaluators.log")


def suite_reliability(run_dir: Path, args: argparse.Namespace, config: Dict[str, Any]) -> None:
    """Repeat the final_answer questions N times (alternating order) and score consistency."""
    if args.dry_run:
        print(f"[quality_runner] (in-process) reliability: final_answer x{args.repeats} -> reliability_records/, reliability.json")
        return
    from tests import baseline_runner as baseline
    from tests.autonomy_study import write_new
    from tests.quality.common import core_correct_of, normalize_answer_record, read_json

    rows = read_csv_rows(ROOT / ANSWER_DATASETS["final_answer"])
    if args.max_queries:
        rows = rows[: args.max_queries]
    records_dir = run_dir / "reliability_records"
    records_dir.mkdir(parents=True, exist_ok=True)
    cfg = copy.deepcopy(config)
    cfg.setdefault("conversation", {})["checkpoint_path"] = str(run_dir / "checkpoint_reliability.sqlite")
    orchestrator = baseline.build_orchestrator(cfg, data_path=str(run_dir / "uploads"))
    (run_dir / "uploads").mkdir(exist_ok=True)
    mode = args.autonomy or (config.get("autonomy") or {}).get("mode", "guided")
    for repeat in range(args.repeats):
        ordered = rows if repeat % 2 == 0 else list(reversed(rows))
        for row in ordered:
            qid = str(row.get("qid"))
            out = records_dir / f"{qid}-{mode}-r{repeat + 1}.json"
            if out.exists():
                continue
            event = threading.Event()
            timer = threading.Timer(args.soft_timeout, event.set)
            timer.daemon = True
            timer.start()
            clock = time.monotonic()
            try:
                result = orchestrator.answer(str(row.get("query")), num_search_results=args.num_results, per_source_search_results=args.num_results, num_retrieved_docs=args.num_results,
                                             max_tokens=args.max_tokens, temperature=args.temperature, allow_search=True, autonomy_mode=mode, cancel_event=event, conversation_id=f"reliability-{qid}-{repeat}")
                outcome = "deadline_cancelled" if event.is_set() else "returned"
            except Exception as exc:  # noqa: BLE001
                outcome = "exception"
                result = {"answer": "", "llm_error": f"{type(exc).__name__}: {exc}", "control": {}}
            finally:
                timer.cancel()
            metrics = {**baseline.extract_llm_stats(result), **baseline.extract_loop_stats(result), "latency_ms": baseline.extract_latency_ms(result), "wall_ms": round((time.monotonic() - clock) * 1000, 2), **baseline.score_answer_quality(result, str(row.get("must_include_facts") or ""))}
            write_new(out, {"schema_version": 1, "qid": qid, "dataset": "final_answer", "requested_mode": mode, "repeat": repeat + 1, "query": row.get("query"), "outcome": outcome, "metrics": metrics, "result": result})
    if not args.skip_review:
        # Judge every repeat so consistency can use core_correct; the review script reads runs/ layout, so expose one.
        runs_dir = run_dir / "reliability_runs" / "runs"
        for path in records_dir.glob("*.json"):
            target = runs_dir / path.stem / "result.json"
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
        run_command([PY, "-m", "tests.quality_review", "--source", str(run_dir / "reliability_runs"), "--provider", args.judge_provider, "--model", args.judge_model] + (["--config", args.config] if args.config else []), dry_run=False, log=run_dir / "review.log")
    from tests.quality.common import load_answer_records

    records = load_answer_records(run_dir / "reliability_runs") if (run_dir / "reliability_runs" / "runs").is_dir() else [normalize_answer_record(read_json(p)) for p in records_dir.glob("*.json")]
    by_qid: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        by_qid.setdefault(str(record.get("qid")), []).append(record)
    consistent = []
    pass_at_1 = []
    pass_at_n = []
    inconsistent: List[str] = []
    timeouts = 0
    cancels_ok = 0
    cancels = 0
    for qid, group in sorted(by_qid.items()):
        cores = [core_correct_of(record) for record in group]
        known = [core for core in cores if core is not None]
        if known:
            consistent.append(len(set(known)) == 1)
            pass_at_1.append(known[0] == 2)
            pass_at_n.append(any(core == 2 for core in known))
            if len(set(known)) != 1:
                inconsistent.append(qid)
        for record in group:
            if record.get("outcome") == "harness_timeout":
                timeouts += 1
            if record.get("outcome") == "deadline_cancelled":
                cancels += 1
                control = record.get("control") or {}
                if control.get("loop_status") == "cancelled" and (record.get("answer") or "").strip():
                    cancels_ok += 1
    payload = {
        "created_at": utc_now(), "repeats": args.repeats, "questions": len(by_qid), "runs": len(records),
        "consistency_at_3": (sum(consistent) / len(consistent)) if consistent else None,
        "pass_at_1": (sum(pass_at_1) / len(pass_at_1)) if pass_at_1 else None,
        "pass_at_3": (sum(pass_at_n) / len(pass_at_n)) if pass_at_n else None,
        "inconsistent_qids": inconsistent,
        "hard_timeout_rate": (timeouts / len(records)) if records else None,
        "soft_cancel_correctness": (cancels_ok / cancels) if cancels else None,
        "answer_variance": None,
        "note": "answer_variance (judge-rated semantic equivalence across repeats) requires an extra judge pass; consistency uses judged core_correct.",
    }
    write_json(run_dir / "reliability.json", payload)


def main() -> None:
    args = parse_args()
    suites = selected_suites(args.suite)
    run_dir = Path(args.run_dir) if args.run_dir else new_run_dir(args.tag)
    run_dir.mkdir(parents=True, exist_ok=True)
    config: Dict[str, Any] = {}
    try:
        config = load_config(args.config)
    except (OSError, ValueError):
        if any(suite in REAL_SUITES for suite in suites) and not args.dry_run:
            raise SystemExit("config.json is required for real suites")
    judge_cfg = (config.get("termination") or {}).get("judge") or {}
    meta = build_run_meta(
        tag=args.tag, config=config or None,
        datasets=[ROOT / path for path in OFFLINE_DATASETS + list(ANSWER_DATASETS.values()) if (ROOT / path).is_file()],
        extra={
            "suites": suites, "arguments": {key: value for key, value in vars(args).items() if key != "config"},
            "quality_config": {"path": str(quality_config_path(args.quality_config).relative_to(ROOT)) if quality_config_path(args.quality_config).is_relative_to(ROOT) else str(quality_config_path(args.quality_config)),
                               "digest": sha256_file(quality_config_path(args.quality_config))},
            "rubric_version": "v4", "review_model": {"provider": args.judge_provider, "model": args.judge_model},
            "termination_judge": {key: judge_cfg.get(key) for key in ("provider", "model", "enabled")},
            "search_fallback": config.get("searchFallback"), "autonomy_mode": args.autonomy or (config.get("autonomy") or {}).get("mode", "guided"),
            "concurrency": args.workers, "soft_timeout_s": args.soft_timeout, "hard_timeout_s": args.soft_timeout + 60,
        },
    )
    meta_path = run_dir / "run_meta.json"
    if not meta_path.exists():
        write_json(meta_path, meta, exclusive=not args.dry_run)
    else:
        print(f"[quality_runner] resuming {run_dir}")
    if any(suite in REAL_SUITES for suite in suites) and not args.dry_run:
        preflight = credit_preflight(config)
        write_json(run_dir / "credit_preflight.json", preflight)
        if preflight["violations"]:
            raise SystemExit(f"daily credit limit exceeded: {preflight['violations']}")
    for suite in suites:
        print(f"[quality_runner] === suite {suite} ===", flush=True)
        if suite == "offline":
            suite_offline(run_dir, args)
        elif suite == "validity":
            suite_validity(run_dir, args)
        elif suite == "search":
            suite_search(run_dir, args)
        elif suite == "local":
            suite_local(run_dir, args)
        elif suite == "answer":
            suite_answer(run_dir, args, config)
        elif suite == "loop":
            suite_recompute(run_dir, args, [("loop_eval", "loop_eval.json"), ("evidence_eval", "evidence_eval.json"), ("citation_eval", "citation_eval.json"), ("fetch_eval", "fetch_eval.json")])
        elif suite == "cost":
            suite_recompute(run_dir, args, [("cost_eval", "cost.json")])
        elif suite == "reliability":
            suite_reliability(run_dir, args, config)
        elif suite == "safety":
            run_command([PY, "-m", "tests.quality.safety_eval", "--run", args.source or str(run_dir), "--output-file", str(run_dir / "safety.json")] + (["--config", args.config] if args.config else []), dry_run=args.dry_run, log=run_dir / "safety.log")
    report_cmd = [PY, "-m", "tests.quality_report", "--run", str(run_dir)] + (["--answers", args.source] if args.source else [])
    run_command(report_cmd, dry_run=args.dry_run, log=run_dir / "report.log")
    print(f"[quality_runner] done: {run_dir}")


if __name__ == "__main__":
    main()
