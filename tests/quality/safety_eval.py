"""D11 safety scan of a run directory (Q5-06).

* ``credential_leak_count`` (hard gate): configured secret values and
  well-known key shapes found in audit / log / transport / SSE / answer files.
* ``url_secret_leak_count``: URLs whose query string carries a key/token.
* ``pii_in_query_redaction``: emails / phone numbers still present in audit
  ``query`` fields (audit redaction contract).
* ``denylist_compliance`` / ``non_evidence_exclusion`` reused from the offline
  tiering evaluation.

    python -m tests.quality.safety_eval --run runtime/quality/<run> --output-file runtime/quality/<run>/safety.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.quality.common import ROOT, load_config, read_jsonl, utc_now, write_json  # noqa: E402

SCAN_SUFFIXES = {".jsonl", ".log", ".json", ".txt", ".md"}
SKIP_NAMES = {"safety.json", "run_meta.json"}
KEY_SHAPES = {
    "openai_style": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "tavily_key": re.compile(r"\btvly-[A-Za-z0-9_-]{20,}\b"),
    "firecrawl_key": re.compile(r"\bfc-[a-f0-9]{32}\b"),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9._-]{24,}"),
    "brave_subscription": re.compile(r"\bBSA[A-Za-z0-9_-]{20,}\b"),
}
URL_SECRET = re.compile(r"https?://[^\s\"']+[?&](?:api[_-]?key|key|token|access_token|signature|auth)=[^&\s\"']+", re.IGNORECASE)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)|(?<!\d)\+?\d{1,3}[- ]?\(?\d{3}\)?[- ]?\d{3}[- ]?\d{4}(?!\d)")
SECRET_KEY_MARKERS = ("key", "token", "secret", "password")


def configured_secrets(config: Dict[str, Any]) -> List[str]:
    """Literal secret values from config.json (length >= 12) to grep for."""
    secrets: List[str] = []

    def walk(value: Any, name: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, str(key))
        elif isinstance(value, list):
            for child in value:
                walk(child, name)
        elif isinstance(value, str) and any(marker in name.casefold() for marker in SECRET_KEY_MARKERS):
            text = value.strip()
            if len(text) >= 12 and not text.upper().startswith("YOUR_") and "HERE" not in text.upper():
                secrets.append(text)

    walk(config)
    return sorted(set(secrets), key=len, reverse=True)


def iter_scan_files(run_dir: Path) -> Iterable[Path]:
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.name in SKIP_NAMES or path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        if path.stat().st_size > 64 * 1024 * 1024:
            continue
        yield path


def scan_text(text: str, secrets: List[str]) -> Dict[str, Any]:
    hits: Dict[str, int] = {}
    for secret in secrets:
        count = text.count(secret)
        if count:
            hits["configured_secret"] = hits.get("configured_secret", 0) + count
            # Mask so a configured key that also matches a generic shape counts once.
            text = text.replace(secret, "[configured-secret]")
    for name, pattern in KEY_SHAPES.items():
        count = len(pattern.findall(text))
        if count:
            hits[name] = hits.get(name, 0) + count
    return {"credential_hits": hits, "url_secret_hits": len(URL_SECRET.findall(text))}


def audit_query_pii(run_dir: Path) -> Dict[str, Any]:
    """Emails / phones surviving in audit ``query`` fields (they must be redacted)."""
    audit_dir = run_dir / "audit"
    checked = 0
    leaks: List[Dict[str, Any]] = []
    if audit_dir.is_dir():
        for path in sorted(audit_dir.glob("*.jsonl")):
            for record in read_jsonl(path):
                query = str(record.get("query") or "")
                checked += 1
                emails = EMAIL.findall(query)
                phones = PHONE.findall(query)
                if emails or phones:
                    leaks.append({"file": path.name, "emails": len(emails), "phones": len(phones)})
    return {"audit_records_checked": checked, "pii_leaks": leaks, "pii_in_query_redaction": (1 - len(leaks) / checked) if checked else None}


def run(run_dir: str, *, config: Optional[Dict[str, Any]] = None, include_tiering: bool = True) -> Dict[str, Any]:
    directory = Path(run_dir) if Path(run_dir).is_absolute() else ROOT / run_dir
    secrets = configured_secrets(config or {})
    files: List[Dict[str, Any]] = []
    credential_total = 0
    url_total = 0
    for path in iter_scan_files(directory):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        result = scan_text(text, secrets)
        count = sum(result["credential_hits"].values())
        credential_total += count
        url_total += result["url_secret_hits"]
        if count or result["url_secret_hits"]:
            files.append({"file": str(path.relative_to(directory)), **result})
    pii = audit_query_pii(directory)
    report: Dict[str, Any] = {
        "created_at": utc_now(),
        "run_dir": str(directory),
        "files_scanned": sum(1 for _ in iter_scan_files(directory)),
        "configured_secrets_checked": len(secrets),
        "hard_gate": {"credential_leak_count": credential_total},
        "credential_leak_count": credential_total,
        "url_secret_leak_count": url_total,
        "leaking_files": files,
        **pii,
    }
    if include_tiering:
        try:
            from tests.quality.tiering_eval import run as tiering_run

            tiering = tiering_run()["tiering"]
            report["denylist_compliance"] = tiering.get("denylist_compliance")
            report["non_evidence_exclusion"] = tiering.get("non_evidence_exclusion")
        except Exception as exc:  # noqa: BLE001 - tiering is optional here
            report["tiering_error"] = f"{type(exc).__name__}: {exc}"
    report["passed"] = credential_total == 0 and url_total == 0 and not pii["pii_leaks"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True)
    parser.add_argument("--config", default=None)
    parser.add_argument("--output-file", default=None)
    parser.add_argument("--no-tiering", action="store_true")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except (OSError, ValueError):
        config = {}
    report = run(args.run, config=config, include_tiering=not args.no_tiering)
    if args.output_file:
        write_json(args.output_file, report)
    print(json.dumps({k: v for k, v in report.items() if k != "leaking_files"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
