"""D0 measurement-validity smoke (plan Q0-05). **Real run**: LLM + search.

Runs the first N ``final_answer`` questions through the production
orchestrator with the transport observer attached and writes
``runtime/quality/<date>-validity/validity.json`` carrying the seven D0
metrics, their thresholds and an overall ``passed`` verdict::

    python -m tests.quality.validity --max-queries 5
    python -m tests.quality.validity --max-queries 5 --tag validity-smoke

Metrics (design D0): ``token_capture_rate``, ``usage_reconciliation_gap``,
``tool_call_capture_ratio``, ``search_call_capture_ratio``,
``param_forwarding_pass`` (from ``tests/quality/test_param_forwarding.py``),
``trace_completeness`` (+ ``truncated`` question count) and
``audit_truncation_rate``.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from tests.quality.common import (
    ROOT,
    build_run_meta,
    load_config,
    mean,
    new_run_dir,
    read_csv_rows,
    read_jsonl,
    utc_now,
    write_json,
    write_jsonl,
)

SEARCH_PROVIDERS = {"brave", "firecrawl", "tavily", "parallel", "anysearch", "brightdata", "google"}
PROVIDER_REQUEST_KINDS = {"search", "extract", "resolver_discovery", "resolver_verify", "resolver_probe", "skill_provider"}
THRESHOLDS = {
    "token_capture_rate": ("==", 1.0),
    "usage_reconciliation_gap": ("<=", 0.05),
    "tool_call_capture_ratio": (">=", 0.95),
    "search_call_capture_ratio": (">=", 0.95),
    "param_forwarding_pass": ("==", 1.0),
    "trace_completeness": (">=", 0.95),
    "audit_truncation_rate": ("<=", 0.05),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=None)
    parser.add_argument("--dataset", default="dataset/final_answer_dataset.csv")
    parser.add_argument("--max-queries", type=int, default=5)
    parser.add_argument("--num-results", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=4000)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--autonomy", choices=["guided", "autonomous"], default="guided")
    parser.add_argument("--data-path", default=None, help="Local-doc directory (default: empty temp dir inside the run).")
    parser.add_argument("--tag", default="validity")
    parser.add_argument("--run-dir", default=None, help="Explicit output directory (default runtime/quality/<date>-<tag>).")
    parser.add_argument("--skip-param-probe", action="store_true", help="Do not run the offline param-forwarding pytest.")
    return parser.parse_args()


def _count_kinds(tool_calls: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for entry in tool_calls:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or entry.get("tool") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def run_param_probe(run_dir: Path) -> Dict[str, Any]:
    """Execute the offline param-forwarding test and summarise pass / xfail / fail."""
    junit = run_dir / "param_forwarding_junit.xml"
    probe_out = run_dir / "param_forwarding_probe.json"
    env = dict(os.environ, ISE_QUALITY_PROBE_OUT=str(probe_out))
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}", "tests/quality/test_param_forwarding.py"]
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    summary: Dict[str, Any] = {"command": " ".join(command), "returncode": completed.returncode, "passed": 0, "failed": 0, "xfailed": 0, "xpassed": 0, "errors": 0, "xfail_reasons": []}
    if junit.exists():
        tree = ET.parse(junit)
        for case in tree.iter("testcase"):
            children = list(case)
            if not children:
                summary["passed"] += 1
                continue
            child = children[0]
            if child.tag == "skipped" and "xfail" in (child.get("type") or "").lower() + (child.get("message") or "").lower():
                summary["xfailed"] += 1
                summary["xfail_reasons"].append(child.get("message") or "")
            elif child.tag == "skipped":
                summary["xfailed"] += 1
                summary["xfail_reasons"].append(child.get("message") or "")
            elif child.tag == "failure":
                summary["failed"] += 1
            elif child.tag == "error":
                summary["errors"] += 1
    strict = summary["passed"] + summary["failed"] + summary["errors"]
    summary["param_forwarding_pass"] = (summary["passed"] / strict) if strict else None
    summary["xfail_defects_registered"] = all("QD-" in reason for reason in summary["xfail_reasons"]) if summary["xfail_reasons"] else True
    if probe_out.exists():
        summary["probe"] = json.loads(probe_out.read_text(encoding="utf-8"))
    summary["stdout_tail"] = completed.stdout[-2000:]
    return summary


def evaluate_thresholds(metrics: Dict[str, Any]) -> Dict[str, Any]:
    verdicts: Dict[str, Any] = {}
    for name, (op, limit) in THRESHOLDS.items():
        value = metrics.get(name)
        if value is None:
            verdicts[name] = None
            continue
        if op == "==":
            verdicts[name] = abs(float(value) - limit) < 1e-9
        elif op == ">=":
            verdicts[name] = float(value) >= limit
        else:
            verdicts[name] = float(value) <= limit
    return verdicts


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    run_dir = Path(args.run_dir) if args.run_dir else new_run_dir(args.tag)
    run_dir.mkdir(parents=True, exist_ok=True)
    audit_dir = run_dir / "audit"
    data_path = args.data_path or str(run_dir / "uploads")
    os.makedirs(data_path, exist_ok=True)

    cfg = copy.deepcopy(config)
    cfg.setdefault("audit", {}).update({"enabled": True, "dir": str(audit_dir), "include_answer": True, "include_full_result": True, "max_files": 0})
    cfg.setdefault("conversation", {})["checkpoint_path"] = str(run_dir / "checkpoint.sqlite")

    rows = read_csv_rows(ROOT / args.dataset)[: max(1, args.max_queries)]
    meta = build_run_meta(
        tag=args.tag,
        config=cfg,
        datasets=[ROOT / args.dataset],
        extra={"suite": "validity", "parameters": vars(args), "questions": [row.get("qid") for row in rows]},
    )
    write_json(run_dir / "run_meta.json", meta)

    from tests import baseline_runner as baseline
    from tests.study_transport import TransportObserver

    observer = TransportObserver(run_dir / "transport.jsonl")
    observer.install()
    orchestrator = baseline.build_orchestrator(cfg, data_path=data_path)

    per_query: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        query = str(row.get("query") or "").strip()
        qid = row.get("qid")
        print(f"[validity] {index}/{len(rows)} {qid} {query}", flush=True)
        llm_before = len(observer.records)
        ext_before = len(observer.external_records)
        try:
            result = orchestrator.answer(
                query,
                num_search_results=args.num_results,
                per_source_search_results=args.num_results,
                num_retrieved_docs=args.num_results,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                allow_search=True,
                autonomy_mode=args.autonomy,
                conversation_id=f"validity-{qid}",
            )
        except Exception as exc:  # noqa: BLE001 - failures are data
            result = {"answer": "", "llm_error": f"{type(exc).__name__}: {exc}", "control": {}, "response_times": {}}
        llm_records = observer.records[llm_before:]
        ext_records = observer.external_records[ext_before:]
        response_times = result.get("response_times") or {}
        tool_calls = [entry for entry in (response_times.get("tool_calls") or []) if isinstance(entry, dict)]
        control = result.get("control") or {}
        trace = control.get("execution_trace") or {}
        events = [event for event in (trace.get("events") or []) if isinstance(event, dict)]
        loop_tool_events = sum(1 for event in events if event.get("kind") == "tool_call" and int(event.get("iteration") or 0) >= 1)
        budgets = ((control.get("termination_policy") or {}).get("tool_budgets") or {})
        budget_used = sum(int((entry or {}).get("used") or 0) for entry in budgets.values() if isinstance(entry, dict))
        llm_stats = baseline.extract_llm_stats(result)
        transport_tokens = sum(int((r.get("usage") or {}).get("total_tokens") or 0) for r in llm_records)
        provider_requests = [entry for entry in tool_calls if entry.get("kind") in PROVIDER_REQUEST_KINDS]
        ext_by_provider: Dict[str, int] = {}
        for record in ext_records:
            provider = str(record.get("provider") or "other")
            ext_by_provider[provider] = ext_by_provider.get(provider, 0) + 1
        ext_search = sum(count for provider, count in ext_by_provider.items() if provider in SEARCH_PROVIDERS)
        app_search = sum(1 for entry in provider_requests if entry.get("kind") in {"search", "resolver_discovery"})
        per_query.append(
            {
                "qid": qid,
                "query": query,
                "llm_error": result.get("llm_error"),
                "loop_status": control.get("loop_status"),
                "loop_iterations": control.get("loop_iterations"),
                **llm_stats,
                "transport_llm_requests": len(llm_records),
                "transport_total_tokens": transport_tokens,
                "app_tool_calls": len(tool_calls),
                "app_tool_call_kinds": _count_kinds(tool_calls),
                "app_provider_requests": len(provider_requests),
                "app_search_requests": app_search,
                "external_requests": len(ext_records),
                "external_requests_by_provider": ext_by_provider,
                "external_search_requests": ext_search,
                "trace_tool_events": loop_tool_events,
                "trace_truncated": bool(trace.get("truncated")),
                "tool_budget_used": budget_used,
            }
        )
        write_jsonl(run_dir / "validity_details.jsonl", per_query)

    observer.uninstall()

    # Audit truncation over everything the run wrote.
    audit_rows: List[Dict[str, Any]] = []
    if audit_dir.is_dir():
        for path in sorted(audit_dir.glob("*.jsonl")):
            audit_rows.extend(read_jsonl(path))
    truncated_rows = [row for row in audit_rows if row.get("truncated")]

    total_calls = sum(int(row.get("llm_call_count") or 0) for row in per_query)
    calls_with_tokens = sum(int(row.get("llm_calls_with_tokens") or 0) for row in per_query)
    app_tokens = sum(int(row.get("total_tokens") or 0) for row in per_query)
    transport_tokens_total = sum(int(row.get("transport_total_tokens") or 0) for row in per_query)
    app_provider = sum(int(row["app_provider_requests"]) for row in per_query)
    ext_total = sum(int(row["external_requests"]) for row in per_query)
    app_search_total = sum(int(row["app_search_requests"]) for row in per_query)
    ext_search_total = sum(int(row["external_search_requests"]) for row in per_query)
    trace_events_total = sum(int(row["trace_tool_events"]) for row in per_query)
    budget_used_total = sum(int(row["tool_budget_used"]) for row in per_query)

    metrics: Dict[str, Any] = {
        "token_capture_rate": (calls_with_tokens / total_calls) if total_calls else None,
        "usage_reconciliation_gap": (abs(app_tokens - transport_tokens_total) / transport_tokens_total) if transport_tokens_total else None,
        "tool_call_capture_ratio": (min(1.0, app_provider / ext_total) if ext_total else (1.0 if app_provider == 0 else None)),
        "tool_call_capture_ratio_raw": (app_provider / ext_total) if ext_total else None,
        "search_call_capture_ratio": (min(1.0, app_search_total / ext_search_total) if ext_search_total else (1.0 if app_search_total == 0 else None)),
        "trace_completeness": (min(1.0, trace_events_total / budget_used_total) if budget_used_total else None),
        "trace_truncated_questions": sum(1 for row in per_query if row["trace_truncated"]),
        "audit_truncation_rate": (len(truncated_rows) / len(audit_rows)) if audit_rows else None,
        "audit_records": len(audit_rows),
        "audit_truncated_fields": sorted({field for row in truncated_rows for field in (row.get("truncated_fields") or [])}),
    }
    param_probe = None if args.skip_param_probe else run_param_probe(run_dir)
    if param_probe is not None:
        metrics["param_forwarding_pass"] = param_probe.get("param_forwarding_pass")
        metrics["param_forwarding_xfail_defects"] = param_probe.get("xfail_reasons")
    verdicts = evaluate_thresholds(metrics)
    gate_names = ("token_capture_rate", "param_forwarding_pass", "tool_call_capture_ratio")
    gate_passed = all(verdicts.get(name) is True for name in gate_names)
    if param_probe is not None and not param_probe.get("xfail_defects_registered", True):
        gate_passed = False
    payload = {
        "created_at": utc_now(),
        "run_dir": str(run_dir),
        "questions": len(per_query),
        "metrics": metrics,
        "thresholds": {name: {"op": op, "limit": limit} for name, (op, limit) in THRESHOLDS.items()},
        "verdicts": verdicts,
        "exit_gate": list(gate_names),
        "passed": gate_passed,
        "notes": [
            "tool_call_capture_ratio 的分母是 TransportObserver 观察到的全部非 LLM HTTP 请求；分子是 response_times.tool_calls 中 kind ∈ search/extract/resolver_*/skill_provider 的条目。",
            "usage_reconciliation_gap 以 transport 旁观 usage 为准；应用记账缺失时该值 > 0。",
            "param_forwarding_pass 只统计严格断言；带 QD- 编号的 xfail 记入 param_forwarding_xfail_defects，不计失败。",
        ],
        "param_probe": param_probe,
        "per_query": per_query,
        "run_meta": meta,
    }
    write_json(run_dir / "validity.json", payload)
    print(json.dumps({"passed": gate_passed, "metrics": {k: v for k, v in metrics.items() if not isinstance(v, (list, dict))}}, ensure_ascii=False, indent=2))
    print(f"[validity] wrote {run_dir / 'validity.json'}")


if __name__ == "__main__":
    main()
