"""Inter-rater agreement for judge-vs-human and human-vs-human labels (Q4-03).

Cohen's kappa for nominal labels, linear/quadratic weighted kappa for the
ordinal 0/1/2 scales, plus the per-question disagreement list::

    python -m tests.quality.agreement --left runtime/quality/<run>/reviews-v4 --right dataset/annotations/answer_20260909.csv \\
        --dimensions core_correct,request_completeness,evidence_support --output-file <...>/calibration.json

Label sources: a ``reviews-v4`` directory (judge), a ``calibration.json``-style
JSON of ``{qid: {dimension: label}}``, or an annotation CSV with ``qid`` plus
one column per dimension (an ``annotator`` column splits raters).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.quality.common import ROOT, read_csv_rows, read_json, utc_now, write_json  # noqa: E402

ORDINAL_DIMENSIONS = {"core_correct", "request_completeness", "evidence_support", "abstention", "grounding"}


def cohen_kappa(left: Sequence[Any], right: Sequence[Any]) -> Optional[float]:
    """Unweighted Cohen's kappa; None when fewer than 2 paired labels."""
    pairs = [(a, b) for a, b in zip(left, right) if a is not None and b is not None]
    n = len(pairs)
    if n < 2:
        return None
    observed = sum(1 for a, b in pairs if a == b) / n
    left_counts = Counter(a for a, _ in pairs)
    right_counts = Counter(b for _, b in pairs)
    expected = sum(left_counts[label] * right_counts[label] for label in set(left_counts) | set(right_counts)) / (n * n)
    if expected == 1.0:
        return 1.0
    return (observed - expected) / (1 - expected)


def weighted_kappa(left: Sequence[Any], right: Sequence[Any], *, categories: Sequence[int] = (0, 1, 2), weights: str = "linear") -> Optional[float]:
    """Weighted kappa for ordinal labels (Cohen 1968), linear or quadratic disagreement weights."""
    pairs = [(int(a), int(b)) for a, b in zip(left, right) if a is not None and b is not None]
    n = len(pairs)
    if n < 2:
        return None
    k = len(categories)
    index = {label: position for position, label in enumerate(categories)}
    observed = [[0.0] * k for _ in range(k)]
    for a, b in pairs:
        observed[index[a]][index[b]] += 1.0 / n
    left_margin = [sum(observed[i][j] for j in range(k)) for i in range(k)]
    right_margin = [sum(observed[i][j] for i in range(k)) for j in range(k)]
    denominator = (k - 1) if weights == "linear" else (k - 1) ** 2
    numerator_obs = 0.0
    numerator_exp = 0.0
    for i in range(k):
        for j in range(k):
            distance = abs(i - j) if weights == "linear" else (i - j) ** 2
            weight = distance / denominator
            numerator_obs += weight * observed[i][j]
            numerator_exp += weight * left_margin[i] * right_margin[j]
    if numerator_exp == 0:
        return 1.0
    return 1 - numerator_obs / numerator_exp


def disagreements(qids: Sequence[str], left: Sequence[Any], right: Sequence[Any]) -> List[Dict[str, Any]]:
    return [{"qid": qid, "left": a, "right": b} for qid, a, b in zip(qids, left, right) if a is not None and b is not None and a != b]


def agreement_report(labels_left: Dict[str, Dict[str, Any]], labels_right: Dict[str, Dict[str, Any]], dimensions: Iterable[str], *, left_name: str = "left", right_name: str = "right") -> Dict[str, Any]:
    shared = sorted(set(labels_left) & set(labels_right))
    report: Dict[str, Any] = {"left": left_name, "right": right_name, "paired_questions": len(shared), "dimensions": {}}
    for dimension in dimensions:
        left = [labels_left[qid].get(dimension) for qid in shared]
        right = [labels_right[qid].get(dimension) for qid in shared]
        paired = [(a, b) for a, b in zip(left, right) if a is not None and b is not None]
        entry: Dict[str, Any] = {
            "n": len(paired),
            "agreement_rate": (sum(1 for a, b in paired if a == b) / len(paired)) if paired else None,
            "cohen_kappa": cohen_kappa(left, right),
            "disagreements": disagreements(shared, left, right),
        }
        if dimension in ORDINAL_DIMENSIONS or all(isinstance(a, int) and isinstance(b, int) for a, b in paired):
            try:
                entry["weighted_kappa_linear"] = weighted_kappa(left, right, weights="linear")
                entry["weighted_kappa_quadratic"] = weighted_kappa(left, right, weights="quadratic")
            except (KeyError, ValueError, TypeError):
                entry["weighted_kappa_linear"] = None
                entry["weighted_kappa_quadratic"] = None
        kappa = entry.get("weighted_kappa_linear") if entry.get("weighted_kappa_linear") is not None else entry["cohen_kappa"]
        entry["reportable"] = kappa is not None and kappa >= 0.6
        entry["verdict"] = "model-assisted score reportable" if entry["reportable"] else "human-only (kappa < 0.6 or too few pairs)"
        report["dimensions"][dimension] = entry
    return report


def _coerce_label(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.lower() in {"true", "yes"}:
        return 1
    if text.lower() in {"false", "no"}:
        return 0
    try:
        return int(text)
    except ValueError:
        return text


def load_labels(source: str, *, annotator: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Load ``{qid: {dimension: label}}`` from a reviews dir, a JSON file or an annotation CSV."""
    path = Path(source) if Path(source).is_absolute() else ROOT / source
    labels: Dict[str, Dict[str, Any]] = {}
    if path.is_dir():
        for file in sorted(path.glob("*.json")):
            review = read_json(file)
            if not isinstance(review, dict) or not review.get("judgment"):
                continue
            judgment = review["judgment"]
            qid = str(review.get("qid") or "")
            if not qid:
                continue
            entry: Dict[str, Any] = {}
            for key in ("core_correct", "request_completeness", "evidence_support", "abstention", "answer_complete", "grounding"):
                if key in judgment:
                    entry[key] = _coerce_label(judgment[key])
            scores = judgment.get("scores") if isinstance(judgment.get("scores"), dict) else {}
            if "core_correctness" in scores and "core_correct" not in entry:
                entry["core_correct"] = _coerce_label(scores["core_correctness"])
            for name, value in scores.items():
                entry[name] = _coerce_label(value)
            entry["factual_concerns_count"] = len(judgment.get("factual_concerns") or [])
            labels[qid] = entry
        return labels
    if path.suffix.lower() == ".json":
        payload = read_json(path)
        if isinstance(payload, dict):
            for qid, entry in payload.items():
                if isinstance(entry, dict):
                    labels[str(qid)] = {key: _coerce_label(value) for key, value in entry.items()}
        return labels
    for row in read_csv_rows(path):
        if annotator and str(row.get("annotator") or "").strip() != annotator:
            continue
        qid = str(row.get("qid") or "").strip()
        if not qid:
            continue
        labels[qid] = {key: _coerce_label(value) for key, value in row.items() if key not in {"qid", "annotator", "annotated_at", "notes", "query"}}
    return labels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--left", required=True)
    parser.add_argument("--right", required=True)
    parser.add_argument("--left-annotator", default=None)
    parser.add_argument("--right-annotator", default=None)
    parser.add_argument("--dimensions", default="core_correct,request_completeness,evidence_support")
    parser.add_argument("--output-file", default=None)
    args = parser.parse_args()
    left = load_labels(args.left, annotator=args.left_annotator)
    right = load_labels(args.right, annotator=args.right_annotator)
    report = agreement_report(left, right, [part.strip() for part in args.dimensions.split(",") if part.strip()], left_name=args.left, right_name=args.right)
    report["created_at"] = utc_now()
    if args.output_file:
        write_json(args.output_file, report)
    print(json.dumps({dim: {k: v for k, v in entry.items() if k != "disagreements"} for dim, entry in report["dimensions"].items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
