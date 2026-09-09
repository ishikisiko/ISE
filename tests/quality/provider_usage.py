"""Summarise per-provider credit usage by day (Q0-03).

Reads ``runtime/provider_usage/<provider>.jsonl`` (written by the metered
search / extract clients) plus ``runtime/brave_search_usage.jsonl`` and prints
requests, known credits and error counts per day and provider. Credits are
``None`` when a provider reported nothing and no ``credits_per_request`` was
configured; the summary keeps "unknown" separate from zero.

Usage::

    python -m tests.quality.provider_usage                # all days
    python -m tests.quality.provider_usage --days 7       # last 7 days (UTC)
    python -m tests.quality.provider_usage --date 2026-09-09 --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from utils.provider_usage import DEFAULT_PROVIDER_USAGE_DIR  # noqa: E402

DEFAULT_BRAVE_LOG = "runtime/brave_search_usage.jsonl"


def _read_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def load_usage_rows(
    usage_dir: str = DEFAULT_PROVIDER_USAGE_DIR,
    brave_log: Optional[str] = DEFAULT_BRAVE_LOG,
) -> List[Dict[str, Any]]:
    """Load every provider ledger row; Brave rows get ``kind='search'``."""
    rows: List[Dict[str, Any]] = []
    if os.path.isdir(usage_dir):
        for name in sorted(os.listdir(usage_dir)):
            if name.endswith(".jsonl"):
                rows.extend(_read_jsonl(os.path.join(usage_dir, name)))
    if brave_log:
        for row in _read_jsonl(brave_log):
            row = dict(row)
            row.setdefault("kind", "search")
            row.setdefault("credits", None)
            rows.append(row)
    return rows


def _day(row: Dict[str, Any]) -> str:
    return str(row.get("timestamp") or "")[:10] or "unknown"


def summarize_usage(
    rows: Iterable[Dict[str, Any]],
    *,
    days: Optional[int] = None,
    date: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Aggregate rows into ``{day: {provider: {...}}}`` plus provider totals."""
    now = now or datetime.now(timezone.utc)
    cutoff = None
    if days is not None:
        cutoff = (now - timedelta(days=max(0, int(days)) - 1)).strftime("%Y-%m-%d")
    per_day: Dict[str, Dict[str, Dict[str, Any]]] = {}
    totals: Dict[str, Dict[str, Any]] = {}

    def bucket(store: Dict[str, Dict[str, Any]], provider: str) -> Dict[str, Any]:
        return store.setdefault(
            provider,
            {"requests": 0, "errors": 0, "credits_known": 0.0, "credits_known_requests": 0, "credits_unknown_requests": 0, "by_kind": {}},
        )

    for row in rows:
        day = _day(row)
        if date is not None and day != date:
            continue
        if cutoff is not None and day < cutoff:
            continue
        provider = str(row.get("provider") or "unknown")
        for store in (per_day.setdefault(day, {}), totals):
            entry = bucket(store, provider)
            entry["requests"] += 1
            if row.get("success") is False or row.get("error"):
                entry["errors"] += 1
            credits = row.get("credits")
            if isinstance(credits, (int, float)) and not isinstance(credits, bool):
                entry["credits_known"] += float(credits)
                entry["credits_known_requests"] += 1
            else:
                entry["credits_unknown_requests"] += 1
            kind = str(row.get("kind") or "search")
            entry["by_kind"][kind] = entry["by_kind"].get(kind, 0) + 1
    return {
        "days": {day: per_day[day] for day in sorted(per_day)},
        "totals": dict(sorted(totals.items())),
        "filter": {"days": days, "date": date},
    }


def check_daily_limits(summary: Dict[str, Any], limits: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return providers whose known credits on any day exceed their limit."""
    violations: List[Dict[str, Any]] = []
    for day, providers in summary.get("days", {}).items():
        for provider, entry in providers.items():
            limit = limits.get(provider)
            if limit is None:
                continue
            try:
                limit_value = float(limit)
            except (TypeError, ValueError):
                continue
            if entry["credits_known"] > limit_value:
                violations.append({"day": day, "provider": provider, "credits": entry["credits_known"], "limit": limit_value})
    return violations


def render_table(summary: Dict[str, Any]) -> str:
    lines = ["day\tprovider\trequests\terrors\tcredits_known\tunknown_credit_requests"]
    for day, providers in summary["days"].items():
        for provider, entry in sorted(providers.items()):
            lines.append(
                f"{day}\t{provider}\t{entry['requests']}\t{entry['errors']}\t"
                f"{entry['credits_known']:.2f}\t{entry['credits_unknown_requests']}"
            )
    lines.append("")
    lines.append("total\tprovider\trequests\terrors\tcredits_known\tunknown_credit_requests")
    for provider, entry in summary["totals"].items():
        lines.append(
            f"-\t{provider}\t{entry['requests']}\t{entry['errors']}\t"
            f"{entry['credits_known']:.2f}\t{entry['credits_unknown_requests']}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--usage-dir", default=DEFAULT_PROVIDER_USAGE_DIR)
    parser.add_argument("--brave-log", default=DEFAULT_BRAVE_LOG)
    parser.add_argument("--days", type=int, default=None, help="Only the last N UTC days.")
    parser.add_argument("--date", default=None, help="Only this UTC day (YYYY-MM-DD).")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table.")
    parser.add_argument("--output-file", default=None)
    args = parser.parse_args()
    rows = load_usage_rows(args.usage_dir, args.brave_log)
    summary = summarize_usage(rows, days=args.days, date=args.date)
    if args.output_file:
        os.makedirs(os.path.dirname(args.output_file) or ".", exist_ok=True)
        with open(args.output_file, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(render_table(summary))


if __name__ == "__main__":
    main()
