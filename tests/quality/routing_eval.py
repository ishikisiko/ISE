"""D2 routing / tool-selection metrics (Q6-06).

Offline against a run's answer records (``--source``): joins each record's
``execution_trace`` tool calls with ``dataset/route_intent_dataset.csv``
(``allowed_first_tools`` / ``allowed_tool_sequences``) and
``dataset/full_text_trigger_dataset.csv`` (``need_fulltext``)::

    first_tool_correct, tool_sequence_admissible, unnecessary_search_rate,
    missing_search_rate, redundant_call_rate, budget_hit_rate,
    fulltext_decision_correct, route_coverage_gap (listed separately)

Without ``--source`` it only validates the dataset columns (offline suite).

    python -m tests.quality.routing_eval --source runtime/quality/<run> --output-file <...>/routing_eval.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.baseline_runner import infer_route  # noqa: E402
from tests.quality.common import ROOT, load_answer_records, macro_average, mean, rate, read_csv_rows, utc_now, write_json  # noqa: E402
from tests.search_quality_pipeline import normalize_query_key  # noqa: E402

ROUTE_DATASET = "dataset/route_intent_dataset.csv"
FULLTEXT_DATASET = "dataset/full_text_trigger_dataset.csv"
WEB_TOOLS = {"web_search", "search_recovery", "fetch_url"}
FETCH_TOOLS = {"fetch_url"}


def tool_calls_of(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = ((record.get("control") or {}).get("execution_trace") or {}).get("events") or []
    return [event for event in events if isinstance(event, dict) and event.get("kind") == "tool_call"]


def sequence_admissible(sequence: List[str], pattern: str) -> bool:
    """``pattern`` is a tiny regex over comma-joined tool names; ``none`` means no tool call."""
    text = ",".join(sequence) if sequence else "none"
    alternatives = [part.strip() for part in pattern.split("|") if part.strip()] if "(" not in pattern else [pattern.strip()]
    for alternative in alternatives:
        try:
            if re.fullmatch(alternative, text):
                return True
        except re.error:
            if alternative == text:
                return True
    return False


def evaluate_record(record: Dict[str, Any], route_row: Optional[Dict[str, Any]], fulltext_row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    calls = tool_calls_of(record)
    tools = [str(call.get("tool") or "") for call in calls]
    control = record.get("control") or {}
    budgets = ((control.get("termination_policy") or {}).get("tool_budgets") or control.get("tool_budgets") or {})
    fingerprints = [f"{call.get('tool')}::{str(call.get('query') or '').strip().casefold()}" for call in calls]
    redundant = len(fingerprints) - len(set(fingerprints))
    item: Dict[str, Any] = {
        "qid": record.get("qid"),
        "query": record.get("query"),
        "tools": tools,
        "redundant_calls": redundant,
        "redundant_call_rate": (redundant / len(fingerprints)) if fingerprints else None,
        "budget_hits": [tool for tool, entry in budgets.items() if isinstance(entry, dict) and int(entry.get("limit") or 0) and int(entry.get("used") or 0) >= int(entry.get("limit") or 0)],
        "used_web": any(tool in WEB_TOOLS for tool in tools),
    }
    if route_row:
        expected = str(route_row.get("expected_route") or "")
        allowed_first = [part.strip() for part in str(route_row.get("allowed_first_tools") or "").split("|") if part.strip()]
        first = tools[0] if tools else "none"
        item.update(
            {
                "intent_label": route_row.get("intent_label"),
                "expected_route": expected,
                "inferred_route": infer_route(control),
                "route_correct": infer_route(control) == expected,
                "route_coverage_gap": str(route_row.get("route_coverage_gap") or "0") == "1",
                "first_tool": first,
                "first_tool_correct": (first in allowed_first) if allowed_first else None,
                "tool_sequence_admissible": sequence_admissible(tools, str(route_row.get("allowed_tool_sequences") or "")),
                "needs_web_evidence": str(route_row.get("needs_web_evidence") or "0") == "1",
                "unnecessary_search": (expected in {"chat"} or expected.endswith("_api") and expected not in {"time_api"}) and item["used_web"] and expected != "general_web",
                "missing_search": str(route_row.get("needs_web_evidence") or "0") == "1" and not item["used_web"],
            }
        )
    if fulltext_row:
        need = str(fulltext_row.get("need_fulltext") or "").strip().lower() == "true"
        fetched = any(tool in FETCH_TOOLS for tool in tools) or any(str(call.get("source_tier") or "") == "official" and call.get("tool") == "search_recovery" for call in calls)
        item.update({"need_fulltext": need, "fetched_fulltext": fetched, "fulltext_decision_correct": need == fetched})
    return item


def summarize(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    routed = [item for item in items if "expected_route" in item]
    scored = [item for item in routed if not item["route_coverage_gap"]]
    by_label: Dict[str, List[float]] = {}
    for item in scored:
        by_label.setdefault(str(item.get("intent_label") or "unknown"), []).append(1.0 if item["route_correct"] else 0.0)
    fulltext = [item for item in items if "fulltext_decision_correct" in item]
    small_talk = [item for item in routed if item["expected_route"] == "chat"]
    return {
        "questions": len(items),
        "routed_questions": len(routed),
        "route_coverage_gap": {"count": sum(1 for item in routed if item["route_coverage_gap"]), "qids": [item["qid"] for item in routed if item["route_coverage_gap"]]},
        "route_accuracy": rate(item["route_correct"] for item in scored),
        "route_accuracy_by_intent": macro_average(by_label),
        "first_tool_correct": rate(item["first_tool_correct"] for item in scored),
        "tool_sequence_admissible": rate(item["tool_sequence_admissible"] for item in scored),
        "unnecessary_search_rate": rate(item["unnecessary_search"] for item in routed if item["expected_route"] != "general_web"),
        "unnecessary_search_rate_small_talk": rate(item["used_web"] for item in small_talk),
        "missing_search_rate": rate(item["missing_search"] for item in routed if item["needs_web_evidence"]),
        "redundant_call_rate": mean(item["redundant_call_rate"] for item in items),
        "budget_hit_rate": rate(bool(item["budget_hits"]) for item in items),
        "fulltext_decision_correct": rate(item["fulltext_decision_correct"] for item in fulltext),
        "fulltext_questions": len(fulltext),
    }


def validate_datasets() -> Dict[str, Any]:
    route_rows = read_csv_rows(ROOT / ROUTE_DATASET)
    missing = [row.get("qid") for row in route_rows if not row.get("allowed_first_tools") or not row.get("allowed_tool_sequences")]
    fulltext_rows = read_csv_rows(ROOT / FULLTEXT_DATASET)
    return {"route_rows": len(route_rows), "rows_missing_tool_columns": missing, "fulltext_rows": len(fulltext_rows), "ok": not missing}


def run(source: Optional[str] = None) -> Dict[str, Any]:
    datasets = validate_datasets()
    report: Dict[str, Any] = {"created_at": utc_now(), "datasets": datasets, "source": source}
    if not source:
        report["summary"] = {"status": "dataset validation only (no --source)"}
        return report
    route_index = {normalize_query_key(str(row.get("query") or "")): row for row in read_csv_rows(ROOT / ROUTE_DATASET)}
    fulltext_index = {normalize_query_key(str(row.get("query") or "")): row for row in read_csv_rows(ROOT / FULLTEXT_DATASET)}
    items = []
    for record in load_answer_records(source):
        key = normalize_query_key(str(record.get("query") or ""))
        items.append(evaluate_record(record, route_index.get(key), fulltext_index.get(key)))
    report["summary"] = summarize(items)
    report["per_query"] = items
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=None)
    parser.add_argument("--output-file", default=None)
    args = parser.parse_args()
    report = run(args.source)
    if args.output_file:
        write_json(args.output_file, report)
    print(json.dumps({key: value for key, value in report.items() if key != "per_query"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
