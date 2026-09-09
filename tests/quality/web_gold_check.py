"""Verify ``dataset/web_gold_zh.csv`` (Q6-01): structure offline, page text online.

    python -m tests.quality.web_gold_check            # structure + span syntax only (offline)
    python -m tests.quality.web_gold_check --fetch    # plain HTTP GET via DirectFetchClient; no provider credits

``--fetch`` records ``fetched_at``, HTTP status, extracted chars and whether the
gold span is contained in the extracted main text (whitespace-normalised
substring, else token overlap >= 0.8). Results go to ``--output-file``; failing
rows are printed so the dataset can be corrected.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.quality.common import ROOT, read_csv_rows, utc_now, write_json  # noqa: E402
from tests.quality.local_gold_check import span_contained, split_spans  # noqa: E402

DEFAULT_GOLD = "dataset/web_gold_zh.csv"
REQUIRED = ("qid", "query", "gold_doc_url", "gold_span", "authority_required", "valid_from", "relevance_notes", "created_at", "gold_verified_by")


def check_structure(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    errors: List[str] = []
    seen: set = set()
    for row in rows:
        qid = str(row.get("qid") or "")
        if not qid or qid in seen:
            errors.append(f"bad/duplicate qid: {qid!r}")
        seen.add(qid)
        url = str(row.get("gold_doc_url") or "")
        if not url.startswith("http"):
            errors.append(f"{qid}: gold_doc_url must be absolute")
        elif url.rstrip("/").count("/") <= 2:
            errors.append(f"{qid}: gold_doc_url must point to a specific page, not a site root")
        if not split_spans(row.get("gold_span")):
            errors.append(f"{qid}: empty gold_span")
        if str(row.get("authority_required") or "") not in {"0", "1"}:
            errors.append(f"{qid}: authority_required must be 0/1")
    categories: Dict[str, int] = {}
    for row in rows:
        key = str(row.get("category") or "uncategorized")
        categories[key] = categories.get(key, 0) + 1
    return {"rows": len(rows), "errors": errors, "categories": categories, "ok": not errors and len(rows) >= 40}


def fetch_text(url: str, *, timeout: int = 30) -> Dict[str, Any]:
    from search.reference_fetch import DirectFetchClient

    client = DirectFetchClient(timeout=timeout, max_chars=200000)
    started = time.perf_counter()
    try:
        extraction = client.extract([url])
    except Exception as exc:  # noqa: BLE001 - recorded as a failed fetch
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "duration_ms": round((time.perf_counter() - started) * 1000, 1)}
    content = next((item for item in extraction.contents if (item.content or "").strip()), None)
    if content is None:
        failure = extraction.failures[0] if extraction.failures else None
        return {"ok": False, "error": getattr(failure, "error_type", "no_content") if failure else "no_content", "duration_ms": round((time.perf_counter() - started) * 1000, 1)}
    return {"ok": True, "text": content.content, "title": content.title, "final_url": content.url, "chars": len(content.content), "duration_ms": round((time.perf_counter() - started) * 1000, 1)}


def check_rows(rows: List[Dict[str, Any]], *, fetch: bool, timeout: int = 30) -> Dict[str, Any]:
    structure = check_structure(rows)
    results: List[Dict[str, Any]] = []
    if fetch:
        for row in rows:
            fetched = fetch_text(str(row.get("gold_doc_url") or ""), timeout=timeout)
            entry: Dict[str, Any] = {"qid": row.get("qid"), "url": row.get("gold_doc_url"), "fetch_ok": fetched.get("ok"), "error": fetched.get("error"), "chars": fetched.get("chars"), "duration_ms": fetched.get("duration_ms"), "spans": []}
            if fetched.get("ok"):
                for span in split_spans(row.get("gold_span")):
                    found, how = span_contained(span, fetched["text"])
                    entry["spans"].append({"span": span, "found": found, "how": how})
                entry["all_spans_found"] = all(item["found"] for item in entry["spans"]) if entry["spans"] else False
            else:
                entry["all_spans_found"] = False
            results.append(entry)
            print(f"[web_gold_check] {row.get('qid')} fetch={'ok' if fetched.get('ok') else fetched.get('error')} spans={'ok' if entry['all_spans_found'] else 'MISSING'}", flush=True)
    fetched_rows = [item for item in results if item.get("fetch_ok")]
    return {
        "created_at": utc_now(),
        "structure": structure,
        "fetched": bool(fetch),
        "fetch_ok": len(fetched_rows),
        "spans_ok": sum(1 for item in results if item.get("all_spans_found")),
        "failures": [item for item in results if not item.get("all_spans_found")],
        "results": results,
        "all_ok": structure["ok"] and (not fetch or all(item.get("all_spans_found") for item in results)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", default=DEFAULT_GOLD)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--output-file", default=None)
    parser.add_argument("--qid", action="append", default=[], help="Only check these qids.")
    args = parser.parse_args()
    rows = read_csv_rows(ROOT / args.gold)
    if args.qid:
        rows = [row for row in rows if row.get("qid") in set(args.qid)]
    report = check_rows(rows, fetch=args.fetch, timeout=args.timeout)
    if args.output_file:
        write_json(args.output_file, report)
    print(json.dumps({k: v for k, v in report.items() if k not in {"results", "failures"}}, ensure_ascii=False, indent=2))
    for item in report["failures"]:
        print("FAIL", item["qid"], item.get("error") or [span for span in item["spans"] if not span["found"]])
    raise SystemExit(0 if report["all_ok"] else 1)


if __name__ == "__main__":
    main()
