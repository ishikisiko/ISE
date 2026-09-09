"""Observe live HTTP usage without changing production requests or responses.

Two kinds of records are kept:

* LLM requests (paths ending in ``/chat/completions``, ``/messages`` or
  ``/responses``): status plus the ``usage`` block of non-streaming responses,
  exactly as the autonomy study recorded them.
* Every other request: host, path prefix, status and duration, classified by
  provider host. These are the proxy-side counts the quality evaluation uses
  as the denominator of ``tool_call_capture_ratio`` (design D0).

The observer never reads or alters production request bodies of non-LLM calls
and never consumes streaming bodies.
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit


LLM_PATH_SUFFIXES = ("/chat/completions", "/messages", "/responses")

# Host suffix -> provider label. Longest suffix wins so ``maps.googleapis.com``
# is not swallowed by a generic ``googleapis.com`` rule.
PROVIDER_HOSTS: Dict[str, str] = {
    "api.search.brave.com": "brave",
    "api.firecrawl.dev": "firecrawl",
    "api.tavily.com": "tavily",
    "api.parallel.ai": "parallel",
    "api.anysearch.com": "anysearch",
    "api.brightdata.com": "brightdata",
    "brd.superproxy.io": "brightdata",
    "customsearch.googleapis.com": "google",
    "www.googleapis.com": "google",
    "maps.googleapis.com": "google_maps",
    "weather.googleapis.com": "google_weather",
    "airquality.googleapis.com": "google_air_quality",
    "routes.googleapis.com": "google_routes",
    "places.googleapis.com": "google_places",
    "finnhub.io": "finnhub",
    "thesportsdb.com": "sportsdb",
    "query1.finance.yahoo.com": "yahoo_finance",
    "query2.finance.yahoo.com": "yahoo_finance",
    "wikidata.org": "resolver_wikidata",
    "pypi.org": "resolver_pypi",
    "registry.npmjs.org": "resolver_npm",
    "api.github.com": "resolver_github",
    "crt.sh": "resolver_ct_log",
    "opencode.ai": "llm",
    "api.anthropic.com": "llm",
    "api.openai.com": "llm",
    "open.bigmodel.cn": "llm",
    "api.z.ai": "llm",
    "api.minimaxi.com": "llm",
    "dashscope.aliyuncs.com": "dashscope",
}


def classify_host(host: str) -> str:
    """Map a request host onto a provider label (``other:<host>`` if unknown)."""
    lowered = str(host or "").casefold().split(":", 1)[0]
    best: Optional[str] = None
    best_len = -1
    for suffix, label in PROVIDER_HOSTS.items():
        if (lowered == suffix or lowered.endswith("." + suffix)) and len(suffix) > best_len:
            best, best_len = label, len(suffix)
    if best is not None:
        return best
    return f"other:{lowered or 'unknown'}"


def path_prefix(path: str, *, segments: int = 2) -> str:
    """Keep the first path segments only; query strings never reach the record."""
    parts = [part for part in str(path or "").split("/") if part]
    return "/" + "/".join(parts[:segments])


def usage_counts(raw: dict) -> dict:
    incoming = raw.get("prompt_tokens", raw.get("input_tokens"))
    outgoing = raw.get("completion_tokens", raw.get("output_tokens"))
    total = raw.get("total_tokens")
    if total is None and isinstance(incoming, int) and isinstance(outgoing, int):
        total = incoming + outgoing
    cached = (raw.get("prompt_tokens_details") or {}).get("cached_tokens")
    if cached is None:
        cached = raw.get("cache_read_input_tokens")
    return {"input_tokens": incoming, "output_tokens": outgoing, "total_tokens": total,
            "cached_input_tokens": cached}


class TransportObserver:
    def __init__(self, path: Path):
        self.path = path
        self.records: List[dict] = []
        self.external_records: List[dict] = []
        self.lock = Lock()
        self._original: Any = None

    def _persist(self, record: dict) -> None:
        with self.lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def install(self) -> None:
        import requests

        if self._original is not None:
            return
        original = requests.Session.send
        self._original = original
        observer = self

        def send(session, request, **kwargs):
            parsed = urlsplit(request.url)
            monitored = parsed.path.endswith(LLM_PATH_SUFFIXES)
            if not monitored:
                return observer._send_external(original, session, request, parsed, **kwargs)
            started = time.monotonic()
            record = {"host": parsed.netloc, "path": parsed.path, "status": None, "usage": None}
            try:
                body = json.loads(request.body or "{}")
                record["model"] = body.get("model")
            except (ValueError, TypeError):
                pass
            try:
                response = original(session, request, **kwargs)
                record["status"] = response.status_code
                # Do not consume streaming bodies. These remain explicitly uncaptured.
                if not kwargs.get("stream"):
                    try:
                        data = response.json()
                        usage = data.get("usage")
                        if isinstance(usage, dict) and usage:
                            record["usage"] = usage_counts(usage)
                        if response.status_code >= 400:
                            error = data.get("error")
                            if isinstance(error, dict):
                                record["error_type"] = error.get("type") or error.get("code")
                    except (ValueError, AttributeError):
                        pass
                return response
            except Exception as exc:
                record["error_type"] = type(exc).__name__
                raise
            finally:
                record["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
                with observer.lock:
                    observer.records.append(record)
                observer._persist(record)
        requests.Session.send = send

    def uninstall(self) -> None:
        import requests

        if self._original is not None:
            requests.Session.send = self._original
            self._original = None

    def _send_external(self, original, session, request, parsed, **kwargs):
        started = time.monotonic()
        record = {
            "kind": "external",
            "host": parsed.netloc,
            "path_prefix": path_prefix(parsed.path),
            "method": str(getattr(request, "method", "") or "").upper(),
            "provider": classify_host(parsed.netloc),
            "status": None,
        }
        try:
            response = original(session, request, **kwargs)
            record["status"] = response.status_code
            return response
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            raise
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
            with self.lock:
                self.external_records.append(record)
            self._persist(record)

    @staticmethod
    def load_records(path: Path) -> tuple[List[dict], List[dict]]:
        """Split a persisted transport log back into LLM and external records."""
        llm: List[dict] = []
        external: List[dict] = []
        if not path.exists():
            return llm, external
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            (external if record.get("kind") == "external" else llm).append(record)
        return llm, external

    def summary(self) -> dict:
        captured = [r["usage"] for r in self.records if r.get("usage") is not None]
        success = [r for r in self.records if r.get("status") == 200]
        by_provider: Dict[str, int] = {}
        errors_by_provider: Dict[str, int] = {}
        for record in self.external_records:
            provider = str(record.get("provider") or "other:unknown")
            by_provider[provider] = by_provider.get(provider, 0) + 1
            status = record.get("status")
            if record.get("error_type") or not isinstance(status, int) or status >= 400:
                errors_by_provider[provider] = errors_by_provider.get(provider, 0) + 1
        return {
            "transport_requests": len(self.records),
            "transport_successes": len(success),
            "transport_usage_captured": len(captured),
            "transport_usage_complete": bool(success) and len(captured) == len(success),
            "transport_http_errors": sum(r.get("status") != 200 for r in self.records),
            **{f"transport_{key}": sum(r[key] for r in captured if isinstance(r.get(key), int))
               if any(isinstance(r.get(key), int) for r in captured) else None
               for key in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens")},
            "external_requests_total": len(self.external_records),
            "external_requests_by_provider": dict(sorted(by_provider.items())),
            "external_errors_by_provider": dict(sorted(errors_by_provider.items())),
        }
