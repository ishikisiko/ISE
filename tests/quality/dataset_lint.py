"""Dataset conventions lint (Q6-07).

Every dataset must be readable as UTF-8 CSV, have no blank first line, unique
non-empty ``qid`` values and, for datasets created under the quality plan,
the shared columns ``qid, language, difficulty, created_at, gold_verified_by``.
Legacy datasets (created before 2026-09-09) are only checked for encoding,
blank first line and qid uniqueness; the report lists the missing shared
columns as ``legacy_missing`` without failing.

    python -m tests.quality.dataset_lint
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.quality.common import ROOT, utc_now, write_json  # noqa: E402

SHARED_COLUMNS = ("qid", "language", "difficulty", "created_at", "gold_verified_by")
LEGACY = {
    "dataset/final_answer_dataset.csv", "dataset/full_text_trigger_dataset.csv", "dataset/gold_chunk_dataset.csv",
    "dataset/gold_doc_dataset.csv", "dataset/open_task_dataset.csv", "dataset/route_intent_dataset.csv",
}
REQUIRED_EXTRA = {
    "dataset/query_analysis_gold.csv": ("query", "category", "gold_intent_shape", "gold_members", "gold_claim_classes", "gold_critical_ambiguity", "gold_time_scope"),
    "dataset/source_tier_gold.csv": ("url", "entity", "gold_tier"),
    "dataset/official_domain_gold.csv": ("entity", "gold_domains", "aliases", "is_pinned"),
    "dataset/local_chunk_gold.csv": ("query", "gold_doc_id", "gold_span", "is_absent"),
    "dataset/web_gold_zh.csv": ("query", "gold_doc_url", "gold_span", "authority_required", "valid_from", "relevance_notes"),
    "dataset/abstention_set.csv": ("query", "should_abstain", "reason"),
    "dataset/hard_loop_set.csv": ("query", "expected_loop_behavior"),
    "dataset/multi_turn_set.csv": ("group_id", "turn_index", "query", "expected_reference_resolution"),
    "dataset/adversarial_pages/index.csv": ("query", "page", "injection_type", "expected_behavior", "canary"),
    "dataset/final_answer_dataset.csv": ("query", "reference_answer", "must_include_facts"),
    "dataset/route_intent_dataset.csv": ("query", "intent_label", "expected_route"),
}


def lint_file(path: Path) -> Dict[str, Any]:
    relative = str(path.relative_to(ROOT))
    result: Dict[str, Any] = {"file": relative, "errors": [], "warnings": [], "rows": 0}
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        result["errors"].append(f"not UTF-8: {exc}")
        return result
    if raw.startswith(b"\xef\xbb\xbf"):
        result["warnings"].append("UTF-8 BOM present")
        text = text.lstrip("﻿")
    first_line = text.split("\n", 1)[0]
    if not first_line.strip():
        message = "blank first line"
        (result["warnings"] if relative in LEGACY else result["errors"]).append(message)
    lines = [line for line in text.splitlines() if line.strip()]
    rows = list(csv.DictReader(lines))
    result["rows"] = len(rows)
    fieldnames = list(csv.DictReader(lines).fieldnames or [])
    result["columns"] = fieldnames
    if "qid" not in fieldnames:
        result["errors"].append("missing qid column")
    else:
        seen: Dict[str, int] = {}
        for row in rows:
            qid = str(row.get("qid") or "").strip()
            if not qid:
                result["errors"].append("empty qid")
                continue
            seen[qid] = seen.get(qid, 0) + 1
        duplicates = sorted(qid for qid, count in seen.items() if count > 1)
        if duplicates:
            result["errors"].append(f"duplicate qid: {', '.join(duplicates)}")
    missing_shared = [column for column in SHARED_COLUMNS if column not in fieldnames]
    if missing_shared:
        if relative in LEGACY:
            result["legacy_missing"] = missing_shared
        else:
            result["errors"].append(f"missing shared columns: {', '.join(missing_shared)}")
    for column in REQUIRED_EXTRA.get(relative, ()):
        if column not in fieldnames:
            result["errors"].append(f"missing column: {column}")
    for row in rows:
        if any(None in row for row in [row]):
            result["errors"].append("row with more fields than header")
            break
    result["ok"] = not result["errors"]
    return result


def run(paths: List[str] | None = None) -> Dict[str, Any]:
    files = [ROOT / path for path in paths] if paths else sorted(ROOT.glob("dataset/*.csv")) + [ROOT / "dataset/adversarial_pages/index.csv"]
    results = [lint_file(path) for path in files if path.is_file()]
    return {"created_at": utc_now(), "files": len(results), "all_ok": all(item["ok"] for item in results), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--output-file", default=None)
    args = parser.parse_args()
    report = run(args.paths or None)
    if args.output_file:
        write_json(args.output_file, report)
    for item in report["results"]:
        status = "OK " if item["ok"] else "ERR"
        extra = f" legacy_missing={item['legacy_missing']}" if item.get("legacy_missing") else ""
        print(f"{status} {item['file']} rows={item['rows']} {'; '.join(item['errors'] + item['warnings'])}{extra}")
    raise SystemExit(0 if report["all_ok"] else 1)


if __name__ == "__main__":
    main()
