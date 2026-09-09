"""Per-provider credit accounting for search and extraction requests.

Brave already keeps ``runtime/brave_search_usage.jsonl``; this module gives the
credit-metered providers (Firecrawl, Tavily, Parallel) the same append-only
ledger under ``runtime/provider_usage/<provider>.jsonl`` and a single place to
read credits from a response body, a response header, or a configured fixed
value (``credits_per_request``). ``None`` means "unknown", never zero.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any, Dict, Iterable, Mapping, Optional

DEFAULT_PROVIDER_USAGE_DIR = "runtime/provider_usage"

# Body keys observed across providers. Nested paths are dotted.
_CREDIT_BODY_PATHS = (
    "creditsUsed",
    "credits_used",
    "credits",
    "usage.credits",
    "usage.credits_used",
    "usage.creditsUsed",
    "data.metadata.creditsUsed",
    "metadata.creditsUsed",
    "cost.credits",
)
_CREDIT_HEADERS = (
    "x-credits-used",
    "x-credits",
    "x-credit-usage",
    "x-usage-credits",
    "x-request-cost",
)


def _dig(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _coerce_credits(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def extract_credits(
    payload: Any = None,
    headers: Optional[Mapping[str, Any]] = None,
    *,
    fallback: Any = None,
) -> Optional[float]:
    """Return the credits consumed by one request.

    Resolution order: response body -> response header -> configured fallback.
    Returns ``None`` when none of the three yields a usable number.
    """
    if isinstance(payload, Mapping):
        for path in _CREDIT_BODY_PATHS:
            credits = _coerce_credits(_dig(payload, path))
            if credits is not None:
                return credits
    if headers:
        lowered = {str(key).lower(): value for key, value in dict(headers).items()}
        for name in _CREDIT_HEADERS:
            credits = _coerce_credits(lowered.get(name))
            if credits is not None:
                return credits
    return _coerce_credits(fallback)


def sum_credits(values: Iterable[Optional[float]]) -> Optional[float]:
    """Sum known credits; ``None`` when nothing was known."""
    known = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if not known:
        return None
    return float(sum(known))


class ProviderUsageRecorder:
    """Append-only per-provider usage ledger (one JSONL file per provider)."""

    def __init__(self, directory: Optional[str] = None) -> None:
        self.directory = str(directory or DEFAULT_PROVIDER_USAGE_DIR)
        self._lock = threading.Lock()

    def path_for(self, provider: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(provider or "unknown"))
        return os.path.join(self.directory, f"{safe or 'unknown'}.jsonl")

    def record(
        self,
        *,
        provider: str,
        kind: str,
        success: bool,
        credits: Optional[float],
        status_code: Optional[int] = None,
        result_count: int = 0,
        query: Optional[str] = None,
        error: Optional[str] = None,
        duration_ms: Optional[float] = None,
        credits_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        query_clean = (query or "").strip()
        payload: Dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "provider": str(provider),
            "kind": str(kind),
            "success": bool(success),
            "status_code": status_code,
            "result_count": int(result_count or 0),
            "credits": credits,
            "credits_source": credits_source,
            "query_preview": query_clean[:120] if query_clean else None,
            "query_hash": hashlib.sha256(query_clean.encode("utf-8")).hexdigest() if query_clean else None,
        }
        if duration_ms is not None:
            payload["duration_ms"] = round(float(duration_ms), 2)
        if error:
            payload["error"] = str(error)[:200]
        line = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            os.makedirs(self.directory, exist_ok=True)
            with open(self.path_for(provider), "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return payload


def resolve_credits(
    payload: Any,
    headers: Optional[Mapping[str, Any]],
    fallback: Any,
) -> tuple[Optional[float], Optional[str]]:
    """Like :func:`extract_credits` but also names the source of the value."""
    credits = extract_credits(payload, None)
    if credits is not None:
        return credits, "body"
    credits = extract_credits(None, headers)
    if credits is not None:
        return credits, "header"
    credits = _coerce_credits(fallback)
    if credits is not None:
        return credits, "config"
    return None, None
