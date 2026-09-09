"""D7.2 citation metrics recomputed offline from answer records (Q3-01).

Re-runs ``evidence.citation_check.check_citations`` on every final answer with
the ledger records and the query-analysis flags the loop used, and derives::

    hallucinated_citation_count, citation_recall, authority_compliance,
    pricing_source_compliance, recency_compliance, citations_per_answer,
    unverified_hedge_rate

Hard-gate fields (``questions_with_hallucinated_citations``) are listed
separately from the soft averages. Input: a run directory holding
``answer_details.jsonl`` or an autonomy-study round (``runs/*/result.json``)::

    python -m tests.quality.citation_eval --source runtime/baseline/autonomy-20260908-measured/r2 \\
        --output-file runtime/quality/<run>/citation_eval.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evidence.citation_check import (  # noqa: E402
    _CITATION_RE,
    _SENTENCE_SPLIT_RE,
    _UNVERIFIED_HEDGE_RE,
    _PRICING_CUES_RE,
    _significant_numbers,
    check_citations,
)
from tests.quality.common import dataset_of, load_answer_records, mean, rate, utc_now, write_json  # noqa: E402

FAILURE_TYPES = (
    "citation_unresolved",
    "citation_missing",
    "citation_not_authoritative",
    "citation_needs_official_source",
    "citation_recency_missing",
)


def citation_flags(analysis: Dict[str, Any]) -> Dict[str, bool]:
    """Mirror ``ReactLoopGraphRunner._check_draft_citations`` flag derivation."""
    claim_classes = set(analysis.get("claim_classes") or [])
    constraints = analysis.get("constraints") or {}
    temporal_required = bool(
        constraints.get("historical_coverage_required")
        or constraints.get("freshness_required")
        or "temporal" in claim_classes
        or "current" in claim_classes
    )
    if analysis.get("existence_query"):
        temporal_required = False
    return {"requires_official_pricing": "pricing" in claim_classes, "temporal_required": temporal_required}


def numeric_sentences(answer: str) -> List[str]:
    return [
        segment.strip()
        for segment in _SENTENCE_SPLIT_RE.split(str(answer or ""))
        if segment and segment.strip() and _significant_numbers(segment)
    ]


def loop_citation_failure_types(control: Dict[str, Any]) -> List[str]:
    """Citation failure types the loop itself recorded on its last verdict."""
    verdicts = [v for v in (control.get("loop_verdicts") or []) if isinstance(v, dict)]
    if not verdicts:
        return []
    last = verdicts[-1]
    types = [str(item) for item in (last.get("failure_types") or []) if str(item).startswith("citation")]
    for hit in last.get("rule_hits") or []:
        if isinstance(hit, dict) and str(hit.get("rule") or "") == "citation_verification":
            types.append("citation_verification")
    return sorted(set(types))


def evaluate_row(row: Dict[str, Any]) -> Dict[str, Any]:
    answer = str(row.get("answer") or "")
    control = row.get("control") or {}
    analysis = control.get("query_analysis") or {}
    records = [record for record in (row.get("evidence_records") or []) if isinstance(record, dict)]
    flags = citation_flags(analysis)
    failures = check_citations(answer, records, **flags)
    counts = {name: sum(1 for failure in failures if failure.get("type") == name) for name in FAILURE_TYPES}
    sentences = numeric_sentences(answer)
    cited_sentences = [sentence for sentence in sentences if _CITATION_RE.search(sentence)]
    pricing_sentences = [sentence for sentence in cited_sentences if _PRICING_CUES_RE.search(sentence)]
    hedged = [sentence for sentence in sentences if _UNVERIFIED_HEDGE_RE.search(sentence)]
    citations = sorted({int(match) for match in _CITATION_RE.findall(answer)})
    numeric_count = len(sentences)
    return {
        "qid": row.get("qid"),
        "mode": row.get("mode"),
        "dataset": dataset_of(row),
        "answer_chars": len(answer),
        "flags": flags,
        "failure_counts": counts,
        "hallucinated_citation_count": counts["citation_unresolved"],
        "citations_per_answer": len(citations),
        "numeric_sentences": numeric_count,
        "citation_recall": (1 - counts["citation_missing"] / numeric_count) if numeric_count else None,
        "authority_compliance": (1 - counts["citation_not_authoritative"] / len(cited_sentences)) if cited_sentences else None,
        "pricing_source_compliance": (
            (1 - counts["citation_needs_official_source"] / len(pricing_sentences)) if pricing_sentences else None
        ) if flags["requires_official_pricing"] else None,
        "recency_compliance": (counts["citation_recency_missing"] == 0) if flags["temporal_required"] and citations else None,
        "unverified_hedge_rate": (len(hedged) / numeric_count) if numeric_count else None,
        "loop_citation_failure_types": loop_citation_failure_types(control),
        "offline_citation_failure": bool(failures),
        "failures": failures[:12],
    }


def summarize(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    with_loop = [item for item in items if (item["mode"] == "guided" or item["mode"] is None)]
    agreement_sample = with_loop[:10]
    agreement = [
        (bool(item["loop_citation_failure_types"]) == item["offline_citation_failure"]) for item in agreement_sample
    ]
    return {
        "questions": len(items),
        "hard_gate": {
            "hallucinated_citation_count_total": sum(item["hallucinated_citation_count"] for item in items),
            "questions_with_hallucinated_citations": [item["qid"] for item in items if item["hallucinated_citation_count"]],
        },
        "citation_recall": mean(item["citation_recall"] for item in items),
        "authority_compliance": mean(item["authority_compliance"] for item in items),
        "pricing_source_compliance": mean(item["pricing_source_compliance"] for item in items),
        "recency_compliance": rate(item["recency_compliance"] for item in items),
        "citations_per_answer": {
            "mean": mean(item["citations_per_answer"] for item in items),
            "zero": sum(1 for item in items if item["citations_per_answer"] == 0),
            "over_20": sum(1 for item in items if item["citations_per_answer"] > 20),
        },
        "unverified_hedge_rate": mean(item["unverified_hedge_rate"] for item in items),
        "failure_type_totals": {name: sum(item["failure_counts"][name] for item in items) for name in FAILURE_TYPES},
        "loop_agreement_sample": {
            "n": len(agreement_sample),
            "agree": sum(1 for value in agreement if value),
            "note": "Compares 'loop recorded any citation failure on its last verdict' with 'offline check fails on the delivered answer'; the loop judged the draft, not necessarily the delivered text.",
        },
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
