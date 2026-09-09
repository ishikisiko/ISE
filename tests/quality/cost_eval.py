"""D9 cost and latency roll-up (Q3-06).

Stage latency (search / fetch / local / skill / llm by label), tokens by label,
external calls per query (proxy-side when the run recorded a transport log,
application-side otherwise), provider credits, ``budget_exhaustion_cost`` and
``cost_per_correct_answer`` (tokens per ``core_correct == 2``; USD stays
``null`` without a price binding)::

    python -m tests.quality.cost_eval --source <run dir | study round> --output-file <...>/cost.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests import baseline_runner as baseline  # noqa: E402
from tests.quality.common import core_correct_of, dataset_of, load_answer_records, mean, percentile, summarise, utc_now, write_json  # noqa: E402

LABEL_STAGE = {
    "loop_act": "act",
    "termination_judge": "judge",
    "degraded_synthesis": "synthesize",
    "search_rag_answer": "recovery_synthesize",
    "search_rag_answer_stream": "recovery_synthesize",
    "analysis_reconcile": "reconcile",
    "reconcile": "reconcile",
    "compaction_summary": "compaction",
    "context_compaction": "compaction",
    "direct_answer": "direct",
    "local_rag_answer": "local",
}
PROVIDER_KINDS = {"search", "extract", "resolver_discovery", "resolver_verify", "resolver_probe", "skill_provider"}
EXHAUSTED = {"exhausted", "stagnated", "evidence_insufficient", "unrecoverable"}


def stage_of(label: Any) -> str:
    text = str(label or "")
    if text in LABEL_STAGE:
        return LABEL_STAGE[text]
    if "judge" in text:
        return "judge"
    if "compact" in text:
        return "compaction"
    if "reconcile" in text:
        return "reconcile"
    if "synth" in text:
        return "synthesize"
    return "other"


def evaluate_row(row: Dict[str, Any]) -> Dict[str, Any]:
    response_times = row.get("response_times") or {}
    metrics = row.get("metrics") or {}
    control = row.get("control") or {}
    llm_calls = [call for call in (response_times.get("llm_calls") or []) if isinstance(call, dict)]
    tool_calls = [call for call in (response_times.get("tool_calls") or []) if isinstance(call, dict)]
    search_sources = [entry for entry in (response_times.get("search_sources") or []) if isinstance(entry, dict)]
    llm_by_stage: Dict[str, float] = {}
    tokens_by_stage: Dict[str, int] = {}
    for call in llm_calls:
        stage = stage_of(call.get("label"))
        llm_by_stage[stage] = llm_by_stage.get(stage, 0.0) + float(call.get("duration_ms") or 0.0)
        total = call.get("total_tokens")
        if isinstance(total, (int, float)) and not isinstance(total, bool):
            tokens_by_stage[stage] = tokens_by_stage.get(stage, 0) + int(total)
    stage_ms: Dict[str, float] = {"llm": sum(llm_by_stage.values()), "search": sum(float(e.get("duration_ms") or 0.0) for e in search_sources)}
    for call in tool_calls:
        tool = str(call.get("tool") or "")
        kind = str(call.get("kind") or "")
        duration = float(call.get("duration_ms") or 0.0)
        if tool in {"web_search", "search_recovery"} and kind == "loop_search_tool":
            stage_ms["search"] = stage_ms.get("search", 0.0) + duration
        elif tool == "fetch_url" or kind == "extract":
            stage_ms["fetch"] = stage_ms.get("fetch", 0.0) + duration
        elif tool == "local_docs":
            stage_ms["local"] = stage_ms.get("local", 0.0) + duration
        elif kind == "skill_provider":
            stage_ms["skill"] = stage_ms.get("skill", 0.0) + duration
    llm_stats = baseline.extract_llm_stats({"response_times": response_times})
    app_provider_requests = sum(1 for call in tool_calls if call.get("kind") in PROVIDER_KINDS)
    credits = sum(float(call.get("credits")) for call in tool_calls if isinstance(call.get("credits"), (int, float)) and not isinstance(call.get("credits"), bool))
    credits_known = any(isinstance(call.get("credits"), (int, float)) for call in tool_calls)
    latency = baseline.extract_latency_ms({"response_times": response_times, "latency_ms": metrics.get("latency_ms")})
    total_tokens = llm_stats["total_tokens"] or 0
    if not llm_stats["llm_calls_with_tokens"] and isinstance(metrics.get("transport_total_tokens"), (int, float)):
        total_tokens = int(metrics["transport_total_tokens"])
        token_source = "transport"
    elif not llm_stats["llm_calls_with_tokens"] and isinstance(metrics.get("total_tokens"), (int, float)):
        total_tokens = int(metrics["total_tokens"])
        token_source = "metrics"
    else:
        token_source = "response_times"
    return {
        "qid": row.get("qid"),
        "mode": row.get("mode"),
        "dataset": dataset_of(row),
        "loop_status": control.get("loop_status"),
        "latency_ms": latency,
        "wall_ms": metrics.get("wall_ms"),
        "total_tokens": total_tokens,
        "token_source": token_source,
        "transport_total_tokens": metrics.get("transport_total_tokens"),
        "llm_call_count": llm_stats["llm_call_count"],
        "llm_calls_with_tokens": llm_stats["llm_calls_with_tokens"],
        "peak_input_tokens": llm_stats["peak_input_tokens"],
        "tokens_by_stage": tokens_by_stage,
        "stage_ms": {stage: round(value, 2) for stage, value in stage_ms.items()},
        "llm_stage_ms": {stage: round(value, 2) for stage, value in llm_by_stage.items()},
        "external_calls_app": app_provider_requests,
        "external_calls_proxy": metrics.get("external_requests_total") or metrics.get("external_requests"),
        "tool_calls_logical": sum(1 for call in tool_calls if call.get("kind") == "loop_search_tool"),
        "provider_credits": credits if credits_known else None,
        "core_correct": core_correct_of(row),
        "compactions": control.get("compactions"),
        "peak_context_ratio": control.get("peak_context_ratio"),
    }


def summarize(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    stages = sorted({stage for item in items for stage in item["stage_ms"]} | {stage for item in items for stage in item["llm_stage_ms"]})
    total_tokens = sum(item["total_tokens"] for item in items)
    exhausted = [item for item in items if item["loop_status"] in EXHAUSTED]
    correct = [item for item in items if item["core_correct"] == 2]
    with_judgment = [item for item in items if item["core_correct"] is not None]
    credits = [item["provider_credits"] for item in items if item["provider_credits"] is not None]
    return {
        "questions": len(items),
        "latency_ms": summarise(item["latency_ms"] for item in items),
        "total_tokens": summarise(item["total_tokens"] for item in items),
        "token_sources": {source: sum(1 for item in items if item["token_source"] == source) for source in sorted({item["token_source"] for item in items})},
        "llm_calls_per_query": summarise(item["llm_call_count"] for item in items),
        "peak_input_tokens": summarise(item["peak_input_tokens"] for item in items),
        "stage_latency_ms": {
            stage: {"p50": percentile([item["stage_ms"].get(stage, item["llm_stage_ms"].get(stage, 0.0)) for item in items], 0.5),
                    "p95": percentile([item["stage_ms"].get(stage, item["llm_stage_ms"].get(stage, 0.0)) for item in items], 0.95),
                    "mean": mean(item["stage_ms"].get(stage, item["llm_stage_ms"].get(stage, 0.0)) for item in items)}
            for stage in stages
        },
        "tokens_by_stage": {stage: sum(item["tokens_by_stage"].get(stage, 0) for item in items) for stage in sorted({s for item in items for s in item["tokens_by_stage"]})},
        "external_calls_per_query": {
            "app": mean(item["external_calls_app"] for item in items),
            "proxy": mean(item["external_calls_proxy"] for item in items),
            "logical_tool_calls": mean(item["tool_calls_logical"] for item in items),
        },
        "provider_credits_per_query": mean(credits) if credits else None,
        "provider_credits_known_questions": len(credits),
        "budget_exhaustion_cost": {
            "questions": len(exhausted),
            "tokens_mean": mean(item["total_tokens"] for item in exhausted),
            "latency_ms_mean": mean(item["latency_ms"] for item in exhausted),
            "token_share": (sum(item["total_tokens"] for item in exhausted) / total_tokens) if total_tokens else None,
        },
        "cost_per_correct_answer": {
            "judged_questions": len(with_judgment),
            "correct_answers": len(correct),
            "tokens_per_correct_answer": (sum(item["total_tokens"] for item in with_judgment) / len(correct)) if correct else None,
            "credits_per_correct_answer": (sum(item["provider_credits"] or 0 for item in with_judgment) / len(correct)) if (correct and credits) else None,
            "usd": None,
        },
        "compactions_total": sum(int(item["compactions"] or 0) for item in items),
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
