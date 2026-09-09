"""D4 fetch / extraction metrics (Q3-05).

From ``control.loop_fetch_outcomes`` (the loop's ``fetch_url`` tool) and
``extracted_pages`` records inside ``search_api_calls`` (official-page
extraction inside search_recovery)::

    fetch_success_rate, extractor_attempts_per_success, extractor_success_by_provider,
    content_sufficiency_rate, truncation_loss_rate, gold_span_containment

``gold_span_containment`` needs the fetched page text in the evidence records
(evaluation runs with ``audit.include_full_result=true`` keep it) and gold spans
from ``dataset/gold_chunk_dataset.csv`` matched by query::

    python -m tests.quality.fetch_eval --source <run dir | study round> --output-file <...>/fetch_eval.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.quality.common import ROOT, dataset_of, load_answer_records, mean, rate, read_csv_rows, utc_now, write_json  # noqa: E402
from tests.quality.local_gold_check import span_contained, split_spans  # noqa: E402
from tests.search_quality_pipeline import gold_doc_match, normalize_query_key  # noqa: E402

DEFAULT_GOLD_CHUNKS = "dataset/gold_chunk_dataset.csv"
DEFAULT_MIN_CONTENT_CHARS = 600


def load_gold_spans(path: str = DEFAULT_GOLD_CHUNKS) -> Dict[str, List[Dict[str, str]]]:
    gold: Dict[str, List[Dict[str, str]]] = {}
    for row in read_csv_rows(ROOT / path):
        query = normalize_query_key(str(row.get("query") or ""))
        if not query:
            continue
        gold.setdefault(query, []).append({"url": str(row.get("gold_doc_url") or ""), "span": str(row.get("gold_span") or "")})
    return gold


def fetch_outcomes(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    control = row.get("control") or {}
    outcomes = [outcome for outcome in (control.get("loop_fetch_outcomes") or []) if isinstance(outcome, dict)]
    for call in row.get("search_api_calls") or []:
        if not isinstance(call, dict) or call.get("kind") != "extracted_pages":
            continue
        attempts = [attempt for attempt in (call.get("attempts") or []) if isinstance(attempt, dict)]
        by_url: Dict[str, List[Dict[str, Any]]] = {}
        for attempt in attempts:
            by_url.setdefault(str(attempt.get("requested_url") or ""), []).append(attempt)
        for url, url_attempts in by_url.items():
            success = next((attempt for attempt in url_attempts if attempt.get("status") == "success"), None)
            outcomes.append(
                {
                    "url": url,
                    "status": "success" if success else "no_data",
                    "chars": int((success or {}).get("content_chars") or max((int(a.get("content_chars") or 0) for a in url_attempts), default=0)),
                    "provider": (success or {}).get("provider"),
                    "attempts": url_attempts,
                    "origin": "search_recovery_extraction",
                }
            )
    return outcomes


def evaluate_row(row: Dict[str, Any], gold: Dict[str, List[Dict[str, str]]], *, min_content_chars: int) -> Dict[str, Any]:
    outcomes = [outcome for outcome in fetch_outcomes(row) if outcome.get("error_type") != "duplicate_url"]
    successes = [outcome for outcome in outcomes if outcome.get("status") == "success"]
    attempts_per_success = [len(outcome.get("attempts") or []) or 1 for outcome in successes]
    provider_stats: Dict[str, Dict[str, int]] = {}
    for outcome in outcomes:
        for attempt in outcome.get("attempts") or []:
            provider = str(attempt.get("provider") or "unknown")
            entry = provider_stats.setdefault(provider, {"attempts": 0, "successes": 0})
            entry["attempts"] += 1
            if attempt.get("status") == "success":
                entry["successes"] += 1
    fetched_records = [
        record for record in (row.get("evidence_records") or [])
        if isinstance(record, dict) and str((record.get("metadata") or {}).get("retrieval_kind") or "") == "fetch_url"
    ]
    truncated = [record for record in fetched_records if (record.get("metadata") or {}).get("truncated")]
    gold_entries = gold.get(normalize_query_key(str(row.get("query") or ""))) or []
    containment: Optional[bool] = None
    truncation_loss: Optional[bool] = None
    if gold_entries:
        page_records = [record for record in (row.get("evidence_records") or []) if isinstance(record, dict)]
        found = False
        lost_to_truncation = False
        for entry in gold_entries:
            spans = split_spans(entry["span"]) or [entry["span"]]
            candidates = [record for record in page_records if gold_doc_match(record.get("reference"), entry["url"])] or page_records
            for record in candidates:
                content = str(record.get("content") or "")
                if all(span_contained(span, content)[0] for span in spans if span):
                    found = True
                    break
            if not found and any((record.get("metadata") or {}).get("truncated") for record in candidates):
                lost_to_truncation = True
        containment = found
        truncation_loss = (not found) and lost_to_truncation
    return {
        "qid": row.get("qid"),
        "mode": row.get("mode"),
        "dataset": dataset_of(row),
        "fetch_attempted": len(outcomes),
        "fetch_success": len(successes),
        "fetch_success_rate": (len(successes) / len(outcomes)) if outcomes else None,
        "extractor_attempts_per_success": mean(attempts_per_success),
        "provider_stats": provider_stats,
        "content_sufficiency": [(int(outcome.get("chars") or 0) >= min_content_chars) for outcome in successes],
        "fetched_records": len(fetched_records),
        "truncated_records": len(truncated),
        "gold_span_containment": containment,
        "truncation_loss": truncation_loss,
        "error_types": {str(outcome.get("error_type") or "none"): 1 for outcome in outcomes if outcome.get("status") != "success"},
    }


def summarize(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    attempted = sum(item["fetch_attempted"] for item in items)
    successes = sum(item["fetch_success"] for item in items)
    providers: Dict[str, Dict[str, int]] = {}
    for item in items:
        for provider, entry in item["provider_stats"].items():
            agg = providers.setdefault(provider, {"attempts": 0, "successes": 0})
            agg["attempts"] += entry["attempts"]
            agg["successes"] += entry["successes"]
    sufficiency = [flag for item in items for flag in item["content_sufficiency"]]
    with_gold = [item for item in items if item["gold_span_containment"] is not None]
    with_gold_fetched = [item for item in with_gold if item["fetched_records"] or item["fetch_success"]]
    fetched = sum(item["fetched_records"] for item in items)
    error_types: Dict[str, int] = {}
    for item in items:
        for name, count in item["error_types"].items():
            error_types[name] = error_types.get(name, 0) + count
    return {
        "questions": len(items),
        "questions_with_fetch": sum(1 for item in items if item["fetch_attempted"]),
        "fetch_attempted": attempted,
        "fetch_success_rate": (successes / attempted) if attempted else None,
        "extractor_attempts_per_success": mean(item["extractor_attempts_per_success"] for item in items),
        "extractor_success_by_provider": {provider: {**entry, "success_rate": (entry["successes"] / entry["attempts"]) if entry["attempts"] else None} for provider, entry in sorted(providers.items())},
        "content_sufficiency_rate": rate(sufficiency),
        "truncation_rate_of_fetched_records": (sum(item["truncated_records"] for item in items) / fetched) if fetched else None,
        "gold_span_containment": rate(item["gold_span_containment"] for item in with_gold),
        "gold_span_containment_when_fetched": rate(item["gold_span_containment"] for item in with_gold_fetched),
        "truncation_loss_rate": rate(item["truncation_loss"] for item in with_gold),
        "gold_questions": len(with_gold),
        "error_types": error_types,
    }


def run(source: str, *, gold_path: str = DEFAULT_GOLD_CHUNKS, min_content_chars: int = DEFAULT_MIN_CONTENT_CHARS) -> Dict[str, Any]:
    gold = load_gold_spans(gold_path)
    rows = load_answer_records(source)
    items = [evaluate_row(row, gold, min_content_chars=min_content_chars) for row in rows]
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(f"{item['dataset']}/{item['mode']}", []).append(item)
    return {
        "created_at": utc_now(),
        "source": source,
        "min_content_chars": min_content_chars,
        "summary": summarize(items),
        "by_group": {name: summarize(group) for name, group in sorted(groups.items())},
        "per_query": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True)
    parser.add_argument("--gold-chunks", default=DEFAULT_GOLD_CHUNKS)
    parser.add_argument("--min-content-chars", type=int, default=DEFAULT_MIN_CONTENT_CHARS)
    parser.add_argument("--output-file", default=None)
    args = parser.parse_args()
    report = run(args.source, gold_path=args.gold_chunks, min_content_chars=args.min_content_chars)
    if args.output_file:
        write_json(args.output_file, report)
    print(json.dumps({"summary": report["summary"], "by_group": report["by_group"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
