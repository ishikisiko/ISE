"""D1 query-analysis field-level evaluation against ``dataset/query_analysis_gold.csv`` (Q2-01).

Zero cost: runs ``analyze_query`` + ``prepare_analysis(llm_invoke=None)`` (sanitize
only, no LLM reconcile) and compares each field with the human gold::

    python -m tests.quality.analysis_eval --output-file runtime/quality/<run>/analysis_eval.json

Member / entity matching uses ``normalize_entity_stem`` plus the per-row alias table
(``gold_member_aliases``: ``canonical=alias1/alias2|...``).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evidence.source_tiering import normalize_entity_stem  # noqa: E402
from tests.quality.common import macro_average, read_csv_rows, utc_now, write_json  # noqa: E402
from utils.query_orchestration import analyze_query, prepare_analysis  # noqa: E402

DEFAULT_GOLD = "dataset/query_analysis_gold.csv"
TIME_SCOPES = ("none", "recent", "window", "historical")


def split_list(raw: Any) -> List[str]:
    return [part.strip() for part in str(raw or "").split("|") if part.strip()]


def parse_aliases(raw: Any) -> Dict[str, Set[str]]:
    """``canonical=alias1/alias2|canonical2=alias`` -> {key(canonical): {keys of aliases}}."""
    table: Dict[str, Set[str]] = {}
    for part in split_list(raw):
        if "=" not in part:
            continue
        canonical, aliases = part.split("=", 1)
        keys: Set[str] = set()
        for alias in aliases.split("/"):
            keys |= _keys(alias)
        for key in _keys(canonical):
            table[key] = keys | _keys(canonical)
    return table


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _compact(text: Any) -> str:
    """Casefolded alphanumeric/CJK form that keeps version digits (``k2.7`` -> ``k27``)."""
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(text or "").casefold())


def _keys(text: Any) -> Set[str]:
    keys = {normalize_entity_stem(text), _compact(text)}
    return {key for key in keys if key}


def match_members(predicted: Iterable[str], gold: Iterable[str], aliases: Dict[str, Set[str]]) -> Tuple[int, int, int, List[str]]:
    """Return (true_positives, predicted_count, gold_count, unmatched_predictions).

    A prediction matches a gold member when any of its keys (entity stem or
    compact form) equals a key of the member or of one of its aliases, or when
    stems of >= 4 characters contain one another (``kimik27`` vs ``kimi``).
    """
    predicted = list(predicted)
    gold_list = [item for item in gold if str(item).strip()]
    gold_keys: List[Tuple[str, Set[str]]] = []
    for item in gold_list:
        keys = _keys(item)
        for canonical, alias_stems in aliases.items():
            if canonical in keys:
                keys |= alias_stems
        gold_keys.append((str(item), keys))
    matched_gold: Set[str] = set()
    unmatched: List[str] = []
    for item in predicted:
        keys = _keys(item)
        hit: Optional[str] = None
        for gold_item, candidates in gold_keys:
            if keys & candidates:
                hit = gold_item
                break
            if any(len(a) >= 4 and len(b) >= 4 and (a in b or b in a) for a in keys for b in candidates):
                hit = gold_item
                break
        if hit is None:
            unmatched.append(str(item))
        else:
            matched_gold.add(hit)
    return len(matched_gold), len(predicted), len(gold_list), unmatched


def prf(tp: int, predicted: int, gold: int) -> Dict[str, Optional[float]]:
    precision = tp / predicted if predicted else None
    recall = tp / gold if gold else None
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else (0.0 if (predicted or gold) else None)
    return {"precision": precision, "recall": recall, "f1": f1}


CURRENT_STATE_WORDS = {"现在", "目前", "如今", "当前", "今天", "今日", "today", "now", "latest", "current"}


def predicted_time_scope(analysis: Dict[str, Any]) -> str:
    """none / recent / window / historical from the analyzer's time fields.

    ``parse_time_constraint`` maps current-state words such as 现在 onto a 30-day
    window; the gold treats them as ``recent`` (current state), so the
    expression decides between ``recent`` and ``window``.
    """
    constraints = analysis.get("constraints") or {}
    if constraints.get("historical_coverage_required"):
        return "historical"
    scope = analysis.get("time_scope") or {}
    expression = str(scope.get("expression") or "").strip().casefold()
    if scope.get("days"):
        return "recent" if expression in CURRENT_STATE_WORDS else "window"
    if "current" in (analysis.get("claim_classes") or []):
        return "recent"
    return "none"


def evaluate_row(row: Dict[str, Any]) -> Dict[str, Any]:
    query = str(row.get("query") or "").strip()
    analysis_obj = prepare_analysis(analyze_query(query, allow_search=True), query=query, reconcile_enabled=True, llm_invoke=None)
    analysis = analysis_obj.to_dict()
    gold_members = split_list(row.get("gold_members"))
    aliases = parse_aliases(row.get("gold_member_aliases"))
    member_tp, member_pred, member_gold, member_noise = match_members(analysis.get("comparison_members") or [], gold_members, aliases)
    gold_entities = split_list(row.get("gold_entities"))
    entity_tp, entity_pred, entity_gold, _ = match_members(analysis.get("entities") or [], gold_entities, aliases)
    gold_claims = set(split_list(row.get("gold_claim_classes")))
    pred_claims = set(analysis.get("claim_classes") or [])
    gold_scope = str(row.get("gold_time_scope") or "none").strip() or "none"
    pred_scope = predicted_time_scope(analysis)
    gold_fanout = _truthy(row.get("gold_temporal_fanout"))
    pred_fanout = bool((analysis.get("constraints") or {}).get("historical_coverage_required"))
    return {
        "qid": row.get("qid"),
        "query": query,
        "category": row.get("category"),
        "language": row.get("language"),
        "intent_shape": {"gold": row.get("gold_intent_shape"), "pred": analysis.get("intent_shape"), "correct": analysis.get("intent_shape") == row.get("gold_intent_shape")},
        "members": {"gold": gold_members, "pred": analysis.get("comparison_members") or [], "tp": member_tp, "pred_count": member_pred, "gold_count": member_gold, "noise": member_noise},
        "entities": {"gold": gold_entities, "pred": analysis.get("entities") or [], "tp": entity_tp, "pred_count": entity_pred, "gold_count": entity_gold},
        "claim_classes": {"gold": sorted(gold_claims), "pred": sorted(pred_claims), "tp": len(gold_claims & pred_claims), "pred_count": len(pred_claims), "gold_count": len(gold_claims)},
        "critical_ambiguity": {"gold": _truthy(row.get("gold_critical_ambiguity")), "pred": bool(analysis.get("critical_ambiguity"))},
        "existence_query": {"gold": _truthy(row.get("gold_existence_query")), "pred": bool(analysis.get("existence_query"))},
        "time_scope": {"gold": gold_scope, "pred": pred_scope, "correct": gold_scope == pred_scope},
        "temporal_fanout": {"gold": gold_fanout, "pred": pred_fanout, "false_fanout": pred_fanout and not gold_fanout},
        "requires_evidence": {"gold": _truthy(row.get("gold_requires_evidence")), "pred": bool(analysis.get("requires_evidence"))},
        "analysis": analysis,
    }


def _binary_prf(items: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    tp = sum(1 for item in items if item[key]["gold"] and item[key]["pred"])
    fp = sum(1 for item in items if not item[key]["gold"] and item[key]["pred"])
    fn = sum(1 for item in items if item[key]["gold"] and not item[key]["pred"])
    tn = sum(1 for item in items if not item[key]["gold"] and not item[key]["pred"])
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, **prf(tp, tp + fp, tp + fn), "accuracy": ((tp + tn) / len(items)) if items else None,
            "false_positive_qids": [item["qid"] for item in items if not item[key]["gold"] and item[key]["pred"]],
            "false_negative_qids": [item["qid"] for item in items if item[key]["gold"] and not item[key]["pred"]]}


def summarize(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    confusion: Dict[str, Dict[str, int]] = {}
    for item in items:
        gold = str(item["intent_shape"]["gold"])
        pred = str(item["intent_shape"]["pred"])
        confusion.setdefault(gold, {})[pred] = confusion.setdefault(gold, {}).get(pred, 0) + 1
    comparison_items = [item for item in items if item["category"] == "comparison"]
    member_tp = sum(item["members"]["tp"] for item in comparison_items)
    member_pred = sum(item["members"]["pred_count"] for item in comparison_items)
    member_gold = sum(item["members"]["gold_count"] for item in comparison_items)
    noise = sum(len(item["members"]["noise"]) for item in comparison_items)
    all_member_pred = sum(item["members"]["pred_count"] for item in items)
    all_noise = sum(len(item["members"]["noise"]) for item in items)
    entity_tp = sum(item["entities"]["tp"] for item in items)
    entity_pred = sum(item["entities"]["pred_count"] for item in items)
    entity_gold = sum(item["entities"]["gold_count"] for item in items)
    claim_tp = sum(item["claim_classes"]["tp"] for item in items)
    claim_pred = sum(item["claim_classes"]["pred_count"] for item in items)
    claim_gold = sum(item["claim_classes"]["gold_count"] for item in items)
    scope_confusion: Dict[str, Dict[str, int]] = {}
    for item in items:
        gold = item["time_scope"]["gold"]
        pred = item["time_scope"]["pred"]
        scope_confusion.setdefault(gold, {})[pred] = scope_confusion.setdefault(gold, {}).get(pred, 0) + 1
    fanout_eligible = [item for item in items if not item["temporal_fanout"]["gold"]]
    false_fanout = [item["qid"] for item in fanout_eligible if item["temporal_fanout"]["false_fanout"]]
    per_category_f1: Dict[str, List[float]] = {}
    for item in items:
        stats = prf(item["members"]["tp"], item["members"]["pred_count"], item["members"]["gold_count"])
        if stats["f1"] is not None:
            per_category_f1.setdefault(str(item["category"]), []).append(stats["f1"])
    existence = _binary_prf(items, "existence_query")
    return {
        "questions": len(items),
        "intent_shape_accuracy": (sum(1 for item in items if item["intent_shape"]["correct"]) / len(items)) if items else None,
        "intent_shape_confusion": confusion,
        "comparison_members": {"comparison_questions": len(comparison_items), **prf(member_tp, member_pred, member_gold),
                               "noise_member_rate": (noise / member_pred) if member_pred else None, "noise_members": noise, "predicted_members": member_pred},
        "noise_member_rate_all_categories": (all_noise / all_member_pred) if all_member_pred else None,
        "members_f1_by_category": macro_average(per_category_f1, min_n=5),
        "entities": prf(entity_tp, entity_pred, entity_gold),
        "claim_classes_micro": prf(claim_tp, claim_pred, claim_gold),
        "critical_ambiguity": _binary_prf(items, "critical_ambiguity"),
        "existence_query": {"precision": existence["precision"], "recall": existence["recall"], "false_positive_qids": existence["false_positive_qids"], "false_negative_qids": existence["false_negative_qids"]},
        "time_scope_accuracy": (sum(1 for item in items if item["time_scope"]["correct"]) / len(items)) if items else None,
        "time_scope_confusion": scope_confusion,
        "false_temporal_fanout_rate": (len(false_fanout) / len(fanout_eligible)) if fanout_eligible else None,
        "false_temporal_fanout_qids": false_fanout,
        "requires_evidence_accuracy": (sum(1 for item in items if item["requires_evidence"]["gold"] == item["requires_evidence"]["pred"]) / len(items)) if items else None,
        "requires_evidence_false_positive_qids": [item["qid"] for item in items if item["requires_evidence"]["pred"] and not item["requires_evidence"]["gold"]],
    }


def run(gold_path: str = DEFAULT_GOLD) -> Dict[str, Any]:
    rows = read_csv_rows(gold_path)
    items = [evaluate_row(row) for row in rows]
    return {
        "created_at": utc_now(),
        "gold_path": gold_path,
        "mode": "deterministic_sanitize_only (llm_invoke=None)",
        "summary": summarize(items),
        "per_query": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", default=DEFAULT_GOLD)
    parser.add_argument("--output-file", default=None)
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args()
    report = run(args.gold)
    if args.output_file:
        write_json(args.output_file, report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if args.details:
        for item in report["per_query"]:
            print(json.dumps({k: v for k, v in item.items() if k != "analysis"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
