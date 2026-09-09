"""D8 loop / termination metrics from ``loop_verdicts``, ``loop_status`` and friends (Q3-03).

Distributions and rates are automatic; ``premature_success_rate`` uses the
judged ``core_correct`` when a review / human annotation is attached and
otherwise only the ``delivered`` half. Rows without a loop status (hard
timeouts, harness errors) are reported in a separate denominator::

    python -m tests.quality.loop_eval --source runtime/baseline/autonomy-20260908-measured/r2 --output-file <...>/loop_eval.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.quality.common import (  # noqa: E402
    core_correct_of,
    dataset_of,
    delivered,
    distribution,
    load_answer_records,
    mean,
    percentile,
    rate,
    utc_now,
    write_json,
)

EXHAUSTED_STATUSES = {"exhausted", "stagnated"}
ANSWER_REASONS = {"final_answer_rejected", "constraints_satisfied", "model_self_wrap", "ready_to_synthesize", "succeeded"}
WEB_TOOLS = {"web_search", "search_recovery", "fetch_url"}


def _tool_calls_by_iteration(control: Dict[str, Any]) -> Dict[int, List[str]]:
    events = ((control.get("execution_trace") or {}).get("events") or [])
    calls: Dict[int, List[str]] = {}
    for event in events:
        if isinstance(event, dict) and event.get("kind") == "tool_call":
            calls.setdefault(int(event.get("iteration") or 0), []).append(str(event.get("tool") or ""))
    return calls


def _gap_addressed(gap: Dict[str, Any], next_tools: List[str]) -> bool:
    kind = str(gap.get("kind") or "")
    if kind == "citation":
        return any(tool in WEB_TOOLS for tool in next_tools)
    return bool(next_tools)


def no_progress_streak(verdicts: List[Dict[str, Any]]) -> int:
    best = current = 0
    for verdict in verdicts:
        if verdict.get("new_evidence") is False:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def evaluate_row(row: Dict[str, Any]) -> Dict[str, Any]:
    control = row.get("control") or {}
    verdicts = [v for v in (control.get("loop_verdicts") or []) if isinstance(v, dict)]
    status = control.get("loop_status")
    has_loop = bool(status)
    reason = control.get("loop_termination_reason") or control.get("termination_reason")
    coverage = control.get("evidence_coverage") or {}
    retained = int(coverage.get("retained") or 0)
    last = verdicts[-1] if verdicts else {}
    sufficiency = str(last.get("evidence_sufficiency") or "unknown")
    false_exhaustion = bool(status in EXHAUSTED_STATUSES and (sufficiency in {"sufficient", "partial"} or retained >= 3))
    core = core_correct_of(row)
    is_delivered = delivered(row)
    premature: Optional[bool] = None
    if status == "succeeded":
        premature = (not is_delivered) or (core == 0)
    judge_used = [bool(v.get("judge_used")) for v in verdicts]
    judge_errors = [bool(v.get("judge_error")) for v in verdicts]
    reasons = [str(v.get("reason") or "") for v in verdicts]
    calls_by_iteration = _tool_calls_by_iteration(control)
    advisory_total = 0
    advisory_ignored = 0
    for verdict in verdicts:
        gaps = [gap for gap in (verdict.get("advisory_gaps") or []) if isinstance(gap, dict)]
        if not gaps:
            continue
        next_tools = calls_by_iteration.get(int(verdict.get("iteration") or 0) + 1, [])
        for gap in gaps:
            advisory_total += 1
            if not _gap_addressed(gap, next_tools):
                advisory_ignored += 1
    budgets = ((control.get("termination_policy") or {}).get("tool_budgets") or control.get("tool_budgets") or {})
    utilization: Dict[str, Optional[float]] = {}
    budget_hits: List[str] = []
    for tool, entry in budgets.items():
        if not isinstance(entry, dict):
            continue
        limit = int(entry.get("limit") or 0)
        used = int(entry.get("used") or 0)
        utilization[tool] = (used / limit) if limit else None
        if limit and used >= limit:
            budget_hits.append(tool)
    first_answer = next((int(v.get("iteration") or 0) for v in verdicts if str(v.get("reason") or "") in ANSWER_REASONS or v.get("action") in {"return", "synthesize"}), None)
    return {
        "qid": row.get("qid"),
        "mode": row.get("mode") or ((control.get("autonomy") or {}).get("mode")),
        "dataset": dataset_of(row),
        "outcome": row.get("outcome"),
        "has_loop_status": has_loop,
        "loop_status": status,
        "termination_reason": reason,
        "iterations": control.get("loop_iterations"),
        "verdicts": len(verdicts),
        "retained": retained,
        "evidence_sufficiency_last": sufficiency,
        "false_exhaustion": false_exhaustion if has_loop else None,
        "delivered": is_delivered,
        "core_correct": core,
        "premature_success": premature,
        "forced_synthesis": bool(control.get("loop_forced_synthesis")),
        "degraded_synthesis": any(r == "degraded_synthesis" for r in reasons),
        "final_answer_rejected_count": sum(1 for r in reasons if r == "final_answer_rejected"),
        "judge_invocations": sum(judge_used),
        "judge_errors": sum(judge_errors),
        "invalid_tool_requests": sum(1 for r in reasons if r == "invalid_tool_request"),
        "narration_guard_triggers": sum(1 for r in reasons if r == "process_narration"),
        "no_progress_streak": no_progress_streak(verdicts),
        "iterations_to_first_answer": first_answer,
        "advisory_gaps": advisory_total,
        "advisory_gaps_ignored": advisory_ignored,
        "advisory_gap_count_control": control.get("advisory_gap_count"),
        "budget_utilization": utilization,
        "budget_hits": budget_hits,
        "compactions": int(control.get("compactions") or 0),
        "peak_context_ratio": control.get("peak_context_ratio"),
        "clarification": bool(control.get("model_clarification")) or status == "clarification_required",
        "cancelled": bool(control.get("cancelled")),
    }


def summarize(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    with_loop = [item for item in items if item["has_loop_status"]]
    exhausted = [item for item in with_loop if item["loop_status"] in EXHAUSTED_STATUSES]
    succeeded = [item for item in with_loop if item["loop_status"] == "succeeded"]
    verdict_total = sum(item["verdicts"] for item in with_loop)
    forced = [item for item in with_loop if item["forced_synthesis"]]
    degraded = [item for item in with_loop if item["degraded_synthesis"]]
    rejected = [item for item in with_loop if item["final_answer_rejected_count"]]
    advisory_total = sum(item["advisory_gaps"] for item in with_loop)
    budget_tools: Dict[str, List[float]] = {}
    hits: Dict[str, int] = {}
    for item in with_loop:
        for tool, value in item["budget_utilization"].items():
            if value is not None:
                budget_tools.setdefault(tool, []).append(value)
        for tool in item["budget_hits"]:
            hits[tool] = hits.get(tool, 0) + 1
    with_core = [item for item in succeeded if item["core_correct"] is not None]
    return {
        "questions": len(items),
        "with_loop_status": len(with_loop),
        "without_loop_status": [item["qid"] for item in items if not item["has_loop_status"]],
        "loop_status_dist": distribution(item["loop_status"] for item in with_loop),
        "termination_reason_dist": distribution(item["termination_reason"] for item in with_loop),
        "iterations_mean": mean(item["iterations"] for item in with_loop),
        "hard_gate": {
            "false_exhaustion_count": sum(1 for item in exhausted if item["false_exhaustion"]),
            "false_exhaustion_qids": [item["qid"] for item in exhausted if item["false_exhaustion"]],
        },
        "false_exhaustion_rate": (sum(1 for item in exhausted if item["false_exhaustion"]) / len(exhausted)) if exhausted else None,
        "exhausted_questions": len(exhausted),
        "premature_success_rate": (sum(1 for item in succeeded if item["premature_success"]) / len(succeeded)) if succeeded else None,
        "premature_success_qids": [item["qid"] for item in succeeded if item["premature_success"]],
        "premature_success_delivered_only": rate((not item["delivered"]) for item in succeeded),
        "premature_success_with_core_correct": {"n": len(with_core), "rate": (sum(1 for item in with_core if item["core_correct"] == 0) / len(with_core)) if with_core else None},
        "forced_synthesis_rate": (len(forced) / len(with_loop)) if with_loop else None,
        "forced_synthesis_core_correct_rate": rate((item["core_correct"] == 2) if item["core_correct"] is not None else None for item in forced),
        "degraded_synthesis_rate": (len(degraded) / len(with_loop)) if with_loop else None,
        "degraded_synthesis_core_correct_rate": rate((item["core_correct"] == 2) if item["core_correct"] is not None else None for item in degraded),
        "final_answer_rejected": {"questions": len(rejected), "count_mean": mean(item["final_answer_rejected_count"] for item in with_loop),
                                  "core_correct_after_rejection": rate((item["core_correct"] == 2) if item["core_correct"] is not None else None for item in rejected)},
        "judge_invocation_rate": (sum(item["judge_invocations"] for item in with_loop) / verdict_total) if verdict_total else None,
        "judge_error_rate": (sum(item["judge_errors"] for item in with_loop) / verdict_total) if verdict_total else None,
        "invalid_tool_request_rate": (sum(item["invalid_tool_requests"] for item in with_loop) / verdict_total) if verdict_total else None,
        "narration_guard_trigger_rate": (sum(item["narration_guard_triggers"] for item in with_loop) / verdict_total) if verdict_total else None,
        "no_progress_streak_p95": percentile([item["no_progress_streak"] for item in with_loop], 0.95),
        "iterations_to_first_answer": {"mean": mean(item["iterations_to_first_answer"] for item in with_loop), "p50": percentile([item["iterations_to_first_answer"] for item in with_loop if item["iterations_to_first_answer"] is not None], 0.5)},
        "advisory_gaps_total": advisory_total,
        "advisory_gaps_per_question": mean(item["advisory_gaps"] for item in with_loop),
        "advisory_gap_ignore_rate": (sum(item["advisory_gaps_ignored"] for item in with_loop) / advisory_total) if advisory_total else None,
        "budget_utilization": {tool: {"mean": mean(values), "p95": percentile(values, 0.95), "hit_rate": hits.get(tool, 0) / len(with_loop)} for tool, values in sorted(budget_tools.items())},
        "compactions_total": sum(item["compactions"] for item in with_loop),
        "compactions_per_question": mean(item["compactions"] for item in with_loop),
        "peak_context_ratio_max": max((item["peak_context_ratio"] or 0) for item in with_loop) if with_loop else None,
        "clarifications": sum(1 for item in items if item["clarification"]),
        "cancelled": sum(1 for item in items if item["cancelled"]),
    }


def run(source: str) -> Dict[str, Any]:
    rows = load_answer_records(source)
    items = [evaluate_row(row) for row in rows]
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(f"{item['dataset']}/{item['mode']}", []).append(item)
    return {
        "created_at": utc_now(),
        "source": source,
        "summary": summarize(items),
        "by_group": {name: summarize(group) for name, group in sorted(groups.items())},
        "per_query": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output-file", default=None)
    args = parser.parse_args()
    report = run(args.source)
    if args.output_file:
        write_json(args.output_file, report)
    print(json.dumps({"summary": report["summary"], "by_group": report["by_group"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
