"""D6 automatic evidence metrics from ``control.evidence_coverage`` (Q3-02).

Per answer: retained / limited / rejected / merged distribution, share of
authoritative retained entries, ``aggregator_leak_rate`` (share of the
answer's cited ``[En]`` records whose tier is ``aggregator``; also reported for
``authority_required`` questions alone) and ``member_coverage`` (F1 of
``comparison_members_covered`` against gold members from
``dataset/query_analysis_gold.csv`` when the query is in that gold)::

    python -m tests.quality.evidence_eval --source <run dir | study round> --output-file <...>/evidence_eval.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evidence.source_verdict import normalize_source_tier  # noqa: E402
from tests.quality.analysis_eval import DEFAULT_GOLD as ANALYSIS_GOLD, match_members, parse_aliases, split_list  # noqa: E402
from tests.quality.common import ROOT, dataset_of, load_answer_records, mean, rate, read_csv_rows, utc_now, write_json  # noqa: E402
from tests.search_quality_pipeline import normalize_query_key  # noqa: E402

_CITATION_RE = re.compile(r"\[E(\d{1,4})\]")


def load_member_gold(path: str = ANALYSIS_GOLD) -> Dict[str, Dict[str, Any]]:
    gold: Dict[str, Dict[str, Any]] = {}
    for row in read_csv_rows(ROOT / path):
        members = split_list(row.get("gold_members"))
        if members:
            gold[normalize_query_key(str(row.get("query") or ""))] = {"members": members, "aliases": parse_aliases(row.get("gold_member_aliases"))}
    return gold


def cited_records(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    eids = {int(match) for match in _CITATION_RE.findall(str(row.get("answer") or ""))}
    by_eid: Dict[int, Dict[str, Any]] = {}
    for record in row.get("evidence_records") or []:
        if not isinstance(record, dict):
            continue
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        eid = metadata.get("eid")
        if isinstance(eid, int) and not isinstance(eid, bool):
            by_eid.setdefault(eid, record)
    return [by_eid[eid] for eid in sorted(eids) if eid in by_eid]


def evaluate_row(row: Dict[str, Any], member_gold: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    control = row.get("control") or {}
    coverage = control.get("evidence_coverage") or {}
    analysis = control.get("query_analysis") or {}
    constraints = analysis.get("constraints") or {}
    entries = int(coverage.get("entries") or 0)
    retained = int(coverage.get("retained") or 0)
    cited = cited_records(row)
    cited_tiers = [normalize_source_tier(record.get("source_tier")) for record in cited]
    aggregator_leak = (sum(1 for tier in cited_tiers if tier == "aggregator") / len(cited_tiers)) if cited_tiers else None
    covered = list(coverage.get("comparison_members_covered") or [])
    gold = member_gold.get(normalize_query_key(str(row.get("query") or "")))
    member_f1: Optional[float] = None
    if gold:
        tp, predicted, gold_count, _ = match_members(covered, gold["members"], gold["aliases"])
        precision = tp / predicted if predicted else 0.0
        recall = tp / gold_count if gold_count else 0.0
        member_f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "qid": row.get("qid"),
        "mode": row.get("mode"),
        "dataset": dataset_of(row),
        "entries": entries,
        "retained": retained,
        "limited": int(coverage.get("limited") or 0),
        "rejected": int(coverage.get("rejected") or 0),
        "merged": int(coverage.get("merged") or 0),
        "authoritative_entries": int(coverage.get("authoritative_entries") or 0),
        "authoritative_share": (int(coverage.get("authoritative_entries") or 0) / retained) if retained else None,
        "authority_required": bool(constraints.get("authority_required")),
        "cited_records": len(cited),
        "cited_tiers": cited_tiers,
        "aggregator_leak_rate": aggregator_leak,
        "comparison_required": bool(constraints.get("comparison_required")),
        "members_covered": covered,
        "member_coverage_f1": member_f1,
        "decisions_sample": [
            {"reference": item.get("reference"), "decision": item.get("decision"), "reason": item.get("reason"), "tier": item.get("source_tier")}
            for item in (coverage.get("decisions") or [])[:6]
            if isinstance(item, dict)
        ],
    }


def summarize(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    totals = {key: sum(item[key] for item in items) for key in ("entries", "retained", "limited", "rejected", "merged")}
    entries = totals["entries"] or 0
    authority_items = [item for item in items if item["authority_required"]]
    return {
        "questions": len(items),
        "ledger_totals": totals,
        "decision_distribution": {key: (totals[key] / entries) if entries else None for key in ("retained", "limited", "rejected")},
        "merged_per_question": mean(item["merged"] for item in items),
        "retained_per_question": mean(item["retained"] for item in items),
        "authoritative_share_of_retained": mean(item["authoritative_share"] for item in items),
        "questions_without_evidence": sum(1 for item in items if item["entries"] == 0),
        "aggregator_leak_rate": mean(item["aggregator_leak_rate"] for item in items),
        "aggregator_leak_rate_authority_required": mean(item["aggregator_leak_rate"] for item in authority_items),
        "authority_required_questions_with_aggregator_citation": [item["qid"] for item in authority_items if item["aggregator_leak_rate"]],
        "hard_gate": {"aggregator_leak_authority_required_count": sum(1 for item in authority_items if item["aggregator_leak_rate"])},
        "cited_records_per_answer": mean(item["cited_records"] for item in items),
        "member_coverage_f1": mean(item["member_coverage_f1"] for item in items),
        "member_coverage_questions": sum(1 for item in items if item["member_coverage_f1"] is not None),
    }


def run(source: str) -> Dict[str, Any]:
    member_gold = load_member_gold()
    rows = load_answer_records(source)
    items = [evaluate_row(row, member_gold) for row in rows]
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
