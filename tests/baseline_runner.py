"""Reproducible baseline runner for the agentic-loop roadmap.

Runs the sole agentic executor over the route-intent and final-answer datasets
and records the metrics (tool-selection accuracy, answer
fact-coverage, P50/P95 latency, LLM calls + tokens, external API calls) to
``runtime/baseline/<milestone>/``.

The baseline is re-runnable by design: each invocation re-executes the loop
end-to-end and overwrites the milestone directory. Historical run directories
can still be compared with ``--compare``.

Usage::

    python -m tests.baseline_runner                       # both datasets, m5
    python -m tests.baseline_runner --datasets route      # routing only
    python -m tests.baseline_runner --max-queries 5       # smoke check
    python -m tests.baseline_runner --milestone m1        # write m1 baseline
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

# Add project directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_ROUTE_DATASET = "dataset/route_intent_dataset.csv"
DEFAULT_ANSWER_DATASET = "dataset/final_answer_dataset.csv"
DEFAULT_OUTPUT_ROOT = "runtime/baseline"
DEFAULT_MILESTONE = "m5"

SKILL_TOOL_ROUTE_MAP = {
    "finance_market_data": "finance_api",
    "weather_conditions": "weather_api",
    "nearby_places": "location_api",
    "route_directions": "transportation_api",
    "sports_schedule": "sports_api",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the sole agentic-loop baseline and record metrics.")
    parser.add_argument(
        "--datasets",
        choices=["route", "answer", "both"],
        default="both",
        help="Which dataset to run. Default: both.",
    )
    parser.add_argument("--route-dataset", default=DEFAULT_ROUTE_DATASET)
    parser.add_argument("--answer-dataset", default=DEFAULT_ANSWER_DATASET)
    parser.add_argument(
        "--output-root",
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for baseline artefacts. Milestone subdir is appended.",
    )
    parser.add_argument("--milestone", default=DEFAULT_MILESTONE)
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("OLD_DIR", "NEW_DIR"),
        default=None,
        help="Skip running and instead diff two existing run directories "
        "(including historical plan/loop runs). Writes comparison.json into the second dir.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Optional path to config.json. Defaults to NLP_CONFIG_PATH env or ./config.json.",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=None,
        help="Optional cap on the number of rows processed per dataset (smoke runs).",
    )
    parser.add_argument(
        "--intent-label",
        default=None,
        help="Optional comma-separated route intent labels (for example: weather,sports).",
    )
    parser.add_argument(
        "--num-results",
        type=int,
        default=5,
        help="Number of search results / retrieved docs requested per query.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=4000,
        help="Generation token budget for the answering step.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help="Sampling temperature for the answering step.",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help="Override the configured LLM provider/model for this run.",
    )
    parser.add_argument(
        "--data-path",
        default="./uploads",
        help="Local-doc path forwarded to the orchestrator (kept empty by default).",
    )
    return parser.parse_args()


def load_config(path: Optional[str]) -> Dict[str, Any]:
    config_path = path or os.environ.get("NLP_CONFIG_PATH") or "config.json"
    with open(config_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def read_csv_rows(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        lines = [line for line in handle if line.strip()]
    return list(csv.DictReader(lines))


def coerce_float(value: Any) -> Optional[float]:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def percentile(values: Sequence[float], ratio: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * ratio
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def build_orchestrator(config: Dict[str, Any], *, data_path: str):
    """Build the production orchestrator exactly as main.py does."""

    from langchain.langchain_llm import create_chat_model
    from langchain.langchain_orchestrator import create_langchain_orchestrator
    from main import build_reranker, build_search_client

    reranker = None
    try:
        reranker, rerank_config = build_reranker(config)
    except Exception as exc:
        print(f"[baseline] reranker disabled: {exc}")
        reranker, rerank_config = None, config.get("rerank") or {}
    min_rerank_score = float(rerank_config.get("min_score", 0.0))
    max_per_domain = max(1, int(rerank_config.get("max_per_domain", 1)))

    search_client = build_search_client(config)
    llm = create_chat_model(config=config)

    return create_langchain_orchestrator(
        config=config,
        llm=llm,
        search_client=search_client,
        data_path=data_path,
        reranker=reranker,
        min_rerank_score=min_rerank_score,
        max_per_domain=max_per_domain,
        requested_search_sources=list(getattr(search_client, "requested_sources", [])) if search_client else [],
        active_search_sources=list(getattr(search_client, "active_sources", [])) if search_client else [],
        active_search_source_labels=list(getattr(search_client, "active_source_labels", [])) if search_client else [],
        missing_search_sources=list(getattr(search_client, "missing_requested_sources", [])) if search_client else [],
        configured_search_sources=list(getattr(search_client, "configured_sources", [])) if search_client else [],
        show_timings=True,
    )


def infer_route(control: Dict[str, Any]) -> str:
    """Map the orchestrator's control fields onto the dataset's route vocabulary.

    This is a deterministic best-effort projection of what the executor
    actually did. Direct-LLM and web-search outcomes both fall through to
    ``general_web`` because the current system has no calculator / time /
    code / translation tools — a genuine routing gap the baseline must expose
    rather than hide.
    """

    search_mode = str(control.get("search_mode") or "").strip().lower()
    decision = control.get("decision") or {}
    reason = str(decision.get("reason") or "").strip().lower()

    if search_mode == "small_talk" or "small_talk" in reason:
        return "chat"

    skill_tools = set(control.get("skill_tools_used") or [])
    explicit_tool = str(control.get("tool") or "").strip()
    # An accepted skill preflight is the routing decision even when the
    # provider returns no data and execution continues through web fallback.
    for tool_name in [explicit_tool, *sorted(skill_tools)]:
        if tool_name in SKILL_TOOL_ROUTE_MAP:
            return SKILL_TOOL_ROUTE_MAP[tool_name]

    # Everything else (web search, direct-LLM answer, search-unavailable
    # fallback) is recorded as general_web — the system's current catch-all.
    return "general_web"


def extract_latency_ms(result: Dict[str, Any]) -> Optional[float]:
    response_times = result.get("response_times")
    if isinstance(response_times, dict):
        latency = coerce_float(response_times.get("total_ms"))
        if latency is not None:
            return latency
    return coerce_float(result.get("latency_ms"))


def extract_llm_stats(result: Dict[str, Any]) -> Dict[str, Any]:
    """Summarise LLM call count and captured token usage for one query."""

    response_times = result.get("response_times") or {}
    calls = response_times.get("llm_calls") if isinstance(response_times, dict) else None
    if not isinstance(calls, list):
        calls = []
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    calls_with_tokens = 0
    peak_input_tokens = 0
    for entry in calls:
        if not isinstance(entry, dict):
            continue
        entry_input = coerce_float(entry.get("input_tokens"))
        entry_output = coerce_float(entry.get("output_tokens"))
        entry_total = coerce_float(entry.get("total_tokens"))
        if entry_input is not None or entry_output is not None or entry_total is not None:
            calls_with_tokens += 1
        input_tokens += int(entry_input or 0)
        output_tokens += int(entry_output or 0)
        total_tokens += int(entry_total or 0)
        peak_input_tokens = max(peak_input_tokens, int(entry_input or 0))
    if total_tokens == 0 and (input_tokens or output_tokens):
        total_tokens = input_tokens + output_tokens
    return {
        "llm_call_count": len(calls),
        "llm_calls_with_tokens": calls_with_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "peak_input_tokens": peak_input_tokens,
    }


def extract_external_api_calls(result: Dict[str, Any]) -> int:
    response_times = result.get("response_times") or {}
    tool_calls = response_times.get("tool_calls") if isinstance(response_times, dict) else None
    if isinstance(tool_calls, list):
        return sum(1 for entry in tool_calls if isinstance(entry, dict))
    return 0


def extract_loop_stats(result: Dict[str, Any]) -> Dict[str, Any]:
    """Pull agentic-loop telemetry from the result (zero when not a loop run).

    Captured so the M1 plan-vs-loop comparison can quantify the cost of
    multi-iteration retrieval alongside answer quality.
    """

    control = result.get("control") or {}
    return {
        "loop_iterations": control.get("loop_iterations"),
        "loop_status": control.get("loop_status"),
        "loop_evidence_records": control.get("loop_evidence_records"),
        "skill_tools_used": list(control.get("skill_tools_used") or []),
        "compactions": control.get("compactions"),
        "peak_context_ratio": control.get("peak_context_ratio"),
    }


def run_route_dataset(
    orchestrator: Any,
    rows: List[Dict[str, Any]],
    *,
    num_results: int,
    max_tokens: int,
    temperature: float,
) -> List[Dict[str, Any]]:
    details: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        query = str(row.get("query") or "").strip()
        expected = str(row.get("expected_route") or "").strip().lower()
        if not query:
            continue
        print(f"[baseline/route] {index}/{len(rows)} {query}")
        try:
            result = orchestrator.answer(
                query,
                num_search_results=num_results,
                per_source_search_results=num_results,
                num_retrieved_docs=num_results,
                max_tokens=max_tokens,
                temperature=temperature,
                allow_search=True,
            )
            control = result.get("control") or {}
            inferred = infer_route(control)
            error = result.get("llm_error")
        except Exception as exc:  # noqa: BLE001 - baseline records per-query failures as data
            result = {}
            control = {}
            inferred = "error"
            error = str(exc)
        latency = extract_latency_ms(result)
        llm_stats = extract_llm_stats(result)
        details.append(
            {
                "qid": row.get("qid"),
                "query": query,
                "intent_label": row.get("intent_label"),
                "expected_route": expected,
                "inferred_route": inferred,
                "route_correct": inferred == expected,
                "latency_ms": latency,
                "llm_error": error,
                **llm_stats,
                **extract_loop_stats(result),
                "external_api_calls": extract_external_api_calls(result),
            }
        )
    return details


def split_facts(must_include: str) -> List[str]:
    clauses = [chunk.strip() for chunk in str(must_include or "").split(";")]
    return [clause for clause in clauses if clause]


def significant_terms(text: str) -> List[str]:
    """Tokenise a fact clause into the terms worth matching.

    Drops punctuation and pure stopwords so a clause like "larger than
    Mercury and Pluto" matches on the proper nouns rather than the glue
    words. Keeps tokens with any letter or digit; numbers are retained
    because factual answers hinge on them (dates, magnitudes).
    """

    stopwords = {
        "the", "a", "an", "of", "and", "or", "to", "in", "on", "at", "by",
        "for", "is", "are", "was", "were", "be", "been", "with", "than",
        "from", "as", "into", "about",
    }
    cleaned = []
    current = []
    for char in str(text or "").lower():
        if char.isalnum():
            current.append(char)
        else:
            if current:
                cleaned.append("".join(current))
                current = []
    if current:
        cleaned.append("".join(current))
    return [token for token in cleaned if token and token not in stopwords]


def fact_clause_overlap(clause: str, answer_lower: str) -> float:
    """Fraction of the clause's significant terms present in the answer.

    Uses term overlap rather than all-or-nothing matching so a clause like
    "1,000 mph (1,600 km/h)" still scores when the answer carries most of
    its vocabulary. ``1.0`` means every significant term matched.
    """

    terms = significant_terms(clause)
    if not terms:
        return 0.0
    hits = sum(1 for term in terms if term in answer_lower)
    return hits / len(terms)


def fact_clause_covered(clause: str, answer_lower: str) -> bool:
    """Strict clause coverage kept for the discrete "fully covered" count."""

    return fact_clause_overlap(clause, answer_lower) >= 1.0


def score_answer_quality(query_result: Dict[str, Any], must_include: str) -> Dict[str, Any]:
    answer = str(query_result.get("answer") or "")
    answer_lower = answer.lower()
    clauses = split_facts(must_include)
    has_answer = bool(answer.strip()) and not query_result.get("llm_error")
    if not clauses:
        overlaps: List[float] = []
        coverage = None
        fully_covered = 0
    else:
        overlaps = [fact_clause_overlap(clause, answer_lower) for clause in clauses]
        coverage = sum(overlaps) / len(clauses)
        fully_covered = sum(1 for overlap in overlaps if overlap >= 1.0)
    return {
        "has_answer": has_answer,
        "fact_clauses_total": len(clauses),
        "fact_clauses_covered": fully_covered,
        "fact_coverage": coverage,
        "answer_chars": len(answer),
    }


def run_answer_dataset(
    orchestrator: Any,
    rows: List[Dict[str, Any]],
    *,
    num_results: int,
    max_tokens: int,
    temperature: float,
    on_progress: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
) -> List[Dict[str, Any]]:
    details: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        query = str(row.get("query") or "").strip()
        if not query:
            continue
        print(f"[baseline/answer] {index}/{len(rows)} {query}")
        try:
            result = orchestrator.answer(
                query,
                num_search_results=num_results,
                per_source_search_results=num_results,
                num_retrieved_docs=num_results,
                max_tokens=max_tokens,
                temperature=temperature,
                allow_search=True,
            )
        except Exception as exc:  # noqa: BLE001 - baseline records per-query failures as data
            result = {"answer": "", "llm_error": str(exc)}
        quality = score_answer_quality(result, str(row.get("must_include_facts") or ""))
        details.append(
            {
                "qid": row.get("qid"),
                "query": query,
                "allowed_sources": row.get("allowed_sources"),
                "time_sensitive": row.get("time_sensitive"),
                "latency_ms": extract_latency_ms(result),
                "llm_error": result.get("llm_error"),
                **quality,
                **extract_llm_stats(result),
                **extract_loop_stats(result),
                "external_api_calls": extract_external_api_calls(result),
            }
        )
        if on_progress is not None:
            on_progress(details)
    return details


def mean(values: Iterable[Optional[float]]) -> Optional[float]:
    usable = [float(v) for v in values if v is not None]
    if not usable:
        return None
    return sum(usable) / len(usable)


def summarise(values: Sequence[Optional[float]]) -> Dict[str, Any]:
    usable = [float(v) for v in values if v is not None]
    summary: Dict[str, Any] = {
        "denominator": len(usable),
        "mean": None,
        "p50": None,
        "p95": None,
        "min": None,
        "max": None,
    }
    if not usable:
        return summary
    summary["mean"] = sum(usable) / len(usable)
    summary["p50"] = percentile(usable, 0.50)
    summary["p95"] = percentile(usable, 0.95)
    summary["min"] = min(usable)
    summary["max"] = max(usable)
    return summary


def summarise_rate(values: Sequence[Optional[bool]]) -> Dict[str, Any]:
    usable = [bool(v) for v in values if v is not None]
    summary: Dict[str, Any] = {"denominator": len(usable), "rate": None, "positives": 0}
    if not usable:
        return summary
    positives = sum(1 for v in usable if v)
    summary["positives"] = positives
    summary["rate"] = positives / len(usable)
    return summary


def route_confusion(details: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    matrix: Dict[str, Dict[str, int]] = {}
    for row in details:
        expected = str(row.get("expected_route") or "unknown")
        inferred = str(row.get("inferred_route") or "unknown")
        matrix.setdefault(expected, {})
        matrix[expected][inferred] = matrix[expected].get(inferred, 0) + 1
    return matrix


def build_route_summary(details: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "rows": len(details),
        "route_accuracy": summarise_rate([row.get("route_correct") for row in details]),
        "latency_ms": summarise([row.get("latency_ms") for row in details]),
        "llm_calls_per_query": summarise([row.get("llm_call_count") for row in details]),
        "input_tokens_per_query": summarise([row.get("input_tokens") for row in details]),
        "output_tokens_per_query": summarise([row.get("output_tokens") for row in details]),
        "total_tokens_per_query": summarise([row.get("total_tokens") for row in details]),
        "peak_input_tokens_per_query": summarise(
            [row.get("peak_input_tokens") for row in details]
        ),
        "compactions_per_query": summarise([row.get("compactions") for row in details]),
        "peak_context_ratio_per_query": summarise(
            [row.get("peak_context_ratio") for row in details]
        ),
        "external_api_calls_per_query": summarise([row.get("external_api_calls") for row in details]),
        "token_capture_rate": summarise_rate(
            [
                (row.get("llm_calls_with_tokens", 0) or 0) > 0 and (row.get("llm_call_count", 0) or 0) > 0
                for row in details
            ]
        ),
        "confusion_matrix": route_confusion(details),
    }


def build_answer_summary(details: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "rows": len(details),
        "has_answer_rate": summarise_rate([row.get("has_answer") for row in details]),
        "fact_coverage": summarise([row.get("fact_coverage") for row in details]),
        "latency_ms": summarise([row.get("latency_ms") for row in details]),
        "llm_calls_per_query": summarise([row.get("llm_call_count") for row in details]),
        "input_tokens_per_query": summarise([row.get("input_tokens") for row in details]),
        "output_tokens_per_query": summarise([row.get("output_tokens") for row in details]),
        "total_tokens_per_query": summarise([row.get("total_tokens") for row in details]),
        "peak_input_tokens_per_query": summarise(
            [row.get("peak_input_tokens") for row in details]
        ),
        "compactions_per_query": summarise([row.get("compactions") for row in details]),
        "peak_context_ratio_per_query": summarise(
            [row.get("peak_context_ratio") for row in details]
        ),
        "external_api_calls_per_query": summarise([row.get("external_api_calls") for row in details]),
        "token_capture_rate": summarise_rate(
            [
                (row.get("llm_calls_with_tokens", 0) or 0) > 0 and (row.get("llm_call_count", 0) or 0) > 0
                for row in details
            ]
        ),
    }


def write_jsonl(path: str, rows: Sequence[Dict[str, Any]]) -> None:
    ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: str, payload: Any) -> None:
    ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _num(value: Any) -> Optional[float]:
    return coerce_float(value)


def _aggregate_metric(
    plan_rows: List[Dict[str, Any]],
    loop_rows: List[Dict[str, Any]],
    key: str,
) -> Dict[str, Any]:
    """Compute mean for one metric on both sides plus the delta.

    Latency-style metrics also report P50/P95 so the loop's multi-iteration
    cost is visible next to the headline mean.
    """

    plan_values = [v for v in (_num(r.get(key)) for r in plan_rows) if v is not None]
    loop_values = [v for v in (_num(r.get(key)) for r in loop_rows) if v is not None]

    def _block(values: List[float]) -> Dict[str, Any]:
        block = summarise(values)
        return block

    plan_block = _block(plan_values)
    loop_block = _block(loop_values)
    delta = None
    if plan_block["mean"] is not None and loop_block["mean"] is not None:
        delta = loop_block["mean"] - plan_block["mean"]
    return {"plan": plan_block, "loop": loop_block, "delta_mean": delta}


def compare_runs(plan_dir: str, loop_dir: str) -> Dict[str, Any]:
    """Diff plan vs loop runs on the same queries (roadmap M1 exit criteria).

    Reads the ``*_details.jsonl`` artefacts each mode writes, matches rows by
    ``qid``, and reports per-query plus aggregate deltas for routing accuracy,
    answer fact-coverage, latency, LLM calls, tokens, and loop iterations.
    The loop-only fields are absent on the plan side by construction.
    """

    comparison: Dict[str, Any] = {
        "plan_dir": plan_dir,
        "loop_dir": loop_dir,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "datasets": {},
    }

    for name, filename in (
        ("route_intent", "route_intent_details.jsonl"),
        ("final_answer", "final_answer_details.jsonl"),
    ):
        plan_rows = read_jsonl(os.path.join(plan_dir, filename))
        loop_rows = read_jsonl(os.path.join(loop_dir, filename))
        if not plan_rows and not loop_rows:
            continue

        plan_by_qid = {str(r.get("qid")): r for r in plan_rows}
        loop_by_qid = {str(r.get("qid")): r for r in loop_rows}
        shared_qids = sorted(set(plan_by_qid) & set(loop_by_qid))

        per_query: List[Dict[str, Any]] = []
        for qid in shared_qids:
            p = plan_by_qid[qid]
            l = loop_by_qid[qid]
            entry: Dict[str, Any] = {
                "qid": qid,
                "query": p.get("query") or l.get("query"),
            }
            if "expected_route" in p or "expected_route" in l:
                entry["route_correct_plan"] = p.get("route_correct")
                entry["route_correct_loop"] = l.get("route_correct")
            if "fact_coverage" in p or "fact_coverage" in l:
                entry["fact_coverage_plan"] = _num(p.get("fact_coverage"))
                entry["fact_coverage_loop"] = _num(l.get("fact_coverage"))
            entry["latency_ms_plan"] = _num(p.get("latency_ms"))
            entry["latency_ms_loop"] = _num(l.get("latency_ms"))
            entry["total_tokens_plan"] = _num(p.get("total_tokens"))
            entry["total_tokens_loop"] = _num(l.get("total_tokens"))
            entry["peak_input_tokens_plan"] = _num(p.get("peak_input_tokens"))
            entry["peak_input_tokens_loop"] = _num(l.get("peak_input_tokens"))
            entry["loop_iterations"] = l.get("loop_iterations")
            entry["loop_status"] = l.get("loop_status")
            entry["compactions"] = l.get("compactions")
            entry["peak_context_ratio"] = _num(l.get("peak_context_ratio"))
            per_query.append(entry)

        route_rate = lambda rows: summarise_rate([r.get("route_correct") for r in rows])["rate"]
        coverage_mean = lambda rows: summarise([_num(r.get("fact_coverage")) for r in rows])["mean"]

        dataset_report: Dict[str, Any] = {
            "matched_queries": len(shared_qids),
            "plan_rows": len(plan_rows),
            "loop_rows": len(loop_rows),
            "route_accuracy": {
                "plan": route_rate(plan_rows) if "route_correct" in (plan_rows[0] if plan_rows else {}) else None,
                "loop": route_rate(loop_rows) if "route_correct" in (loop_rows[0] if loop_rows else {}) else None,
            },
            "fact_coverage_mean": {
                "plan": coverage_mean(plan_rows),
                "loop": coverage_mean(loop_rows),
            },
            "latency_ms": _aggregate_metric(plan_rows, loop_rows, "latency_ms"),
            "total_tokens": _aggregate_metric(plan_rows, loop_rows, "total_tokens"),
            "peak_input_tokens": _aggregate_metric(
                plan_rows, loop_rows, "peak_input_tokens"
            ),
            "llm_call_count": _aggregate_metric(plan_rows, loop_rows, "llm_call_count"),
            "loop_iterations": summarise([_num(r.get("loop_iterations")) for r in loop_rows]),
            "per_query": per_query,
        }
        # Quantify the gap source the M1 exit criteria asks for: which queries
        # did loop resolve that plan missed, and vice versa.
        if shared_qids and "route_correct" in (plan_rows[0] if plan_rows else {}):
            loop_only_hits = [
                qid for qid in shared_qids
                if plan_by_qid[qid].get("route_correct") is False
                and loop_by_qid[qid].get("route_correct") is True
            ]
            plan_only_hits = [
                qid for qid in shared_qids
                if plan_by_qid[qid].get("route_correct") is True
                and loop_by_qid[qid].get("route_correct") is False
            ]
            dataset_report["route_gap"] = {
                "loop_resolved": loop_only_hits,
                "loop_regressed": plan_only_hits,
            }
        comparison["datasets"][name] = dataset_report

    out_path = os.path.join(loop_dir, "comparison.json")
    write_json(out_path, comparison)
    print(f"[compare] wrote {out_path}")
    _print_comparison(comparison)
    return comparison


def _print_comparison(comparison: Dict[str, Any]) -> None:
    for name, report in comparison.get("datasets", {}).items():
        print(f"\n== {name} ({report.get('matched_queries', 0)} matched queries) ==")
        ra = report.get("route_accuracy") or {}
        if ra.get("plan") is not None or ra.get("loop") is not None:
            print(f"  route_accuracy:   plan={ra.get('plan')}  loop={ra.get('loop')}")
        fc = report.get("fact_coverage_mean") or {}
        print(f"  fact_coverage:    plan={fc.get('plan')}  loop={fc.get('loop')}")
        lat = report.get("latency_ms") or {}
        print(
            "  latency_ms(mean): plan={p}  loop={l}  delta={d}".format(
                p=(lat.get("plan") or {}).get("mean"),
                l=(lat.get("loop") or {}).get("mean"),
                d=lat.get("delta_mean"),
            )
        )
        tok = report.get("total_tokens") or {}
        print(
            "  tokens(mean):     plan={p}  loop={l}  delta={d}".format(
                p=(tok.get("plan") or {}).get("mean"),
                l=(tok.get("loop") or {}).get("mean"),
                d=tok.get("delta_mean"),
            )
        )
        it = report.get("loop_iterations") or {}
        print(f"  loop_iterations:  mean={it.get('mean')}  p95={it.get('p95')}")
        gap = report.get("route_gap")
        if gap:
            print(
                f"  route_gap:        loop_resolved={len(gap.get('loop_resolved', []))}  "
                f"loop_regressed={len(gap.get('loop_regressed', []))}"
            )


def run_baseline(args: argparse.Namespace) -> Dict[str, Any]:
    config = load_config(args.config)
    if args.provider:
        config["LLM_PROVIDER"] = args.provider
    orchestrator = build_orchestrator(config, data_path=args.data_path)

    output_dir = os.path.join(args.output_root, args.milestone)
    os.makedirs(output_dir, exist_ok=True)

    cap = args.max_queries
    report: Dict[str, Any] = {
        "milestone": args.milestone,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": config.get("LLM_PROVIDER"),
        "intent_label_filter": args.intent_label,
        "datasets": {},
    }

    if args.datasets in {"route", "both"}:
        rows = read_csv_rows(args.route_dataset)
        if args.intent_label:
            expected_labels = {
                value.strip().lower()
                for value in args.intent_label.split(",")
                if value.strip()
            }
            rows = [
                row for row in rows
                if str(row.get("intent_label") or "").strip().lower() in expected_labels
            ]
        if cap is not None:
            rows = rows[:cap]
        details = run_route_dataset(
            orchestrator,
            rows,
            num_results=args.num_results,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
        )
        write_jsonl(os.path.join(output_dir, "route_intent_details.jsonl"), details)
        route_summary = build_route_summary(details)
        write_json(os.path.join(output_dir, "route_intent_summary.json"), route_summary)
        report["datasets"]["route_intent"] = {
            "rows": len(details),
            "summary_file": "route_intent_summary.json",
            "details_file": "route_intent_details.jsonl",
            "route_accuracy": route_summary["route_accuracy"]["rate"],
        }
        print(f"[baseline] route accuracy: {route_summary['route_accuracy']['rate']}")

    if args.datasets in {"answer", "both"}:
        rows = read_csv_rows(args.answer_dataset)
        if cap is not None:
            rows = rows[:cap]
        details_path = os.path.join(output_dir, "final_answer_details.jsonl")
        summary_path = os.path.join(output_dir, "final_answer_summary.json")

        def persist_answer_progress(progress: List[Dict[str, Any]]) -> None:
            """Keep completed live-provider rows inspectable after a stalled request."""
            write_jsonl(details_path, progress)
            write_json(summary_path, build_answer_summary(progress))

        details = run_answer_dataset(
            orchestrator,
            rows,
            num_results=args.num_results,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            on_progress=persist_answer_progress,
        )
        write_jsonl(details_path, details)
        answer_summary = build_answer_summary(details)
        write_json(summary_path, answer_summary)
        report["datasets"]["final_answer"] = {
            "rows": len(details),
            "summary_file": "final_answer_summary.json",
            "details_file": "final_answer_details.jsonl",
            "fact_coverage": answer_summary["fact_coverage"]["mean"],
        }
        print(f"[baseline] answer fact_coverage: {answer_summary['fact_coverage']['mean']}")

    write_json(os.path.join(output_dir, "run_meta.json"), report)
    write_json(os.path.join(output_dir, "summary.json"), report)
    print(f"[baseline] wrote artefacts to {output_dir}")
    return report


def main() -> None:
    args = parse_args()
    if args.compare:
        compare_runs(args.compare[0], args.compare[1])
        return
    run_baseline(args)


if __name__ == "__main__":
    main()
