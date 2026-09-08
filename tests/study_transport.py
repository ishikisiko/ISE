"""Observe live HTTP usage without changing production requests or responses."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
import time
from urllib.parse import urlsplit


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
        self.records = []
        self.lock = Lock()

    def install(self) -> None:
        import requests

        original = requests.Session.send
        observer = self

        def send(session, request, **kwargs):
            parsed = urlsplit(request.url)
            monitored = parsed.path.endswith(("/chat/completions", "/messages", "/responses"))
            if not monitored:
                return original(session, request, **kwargs)
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
                    with observer.path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        requests.Session.send = send

    def summary(self) -> dict:
        captured = [r["usage"] for r in self.records if r.get("usage") is not None]
        success = [r for r in self.records if r.get("status") == 200]
        return {
            "transport_requests": len(self.records),
            "transport_successes": len(success),
            "transport_usage_captured": len(captured),
            "transport_usage_complete": bool(success) and len(captured) == len(success),
            "transport_http_errors": sum(r.get("status") != 200 for r in self.records),
            **{f"transport_{key}": sum(r[key] for r in captured if isinstance(r.get(key), int))
               if any(isinstance(r.get(key), int) for r in captured) else None
               for key in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens")},
        }
