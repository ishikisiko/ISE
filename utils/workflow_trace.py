"""Lightweight workflow tracing for live step streaming.

The tracer records ordered step events (begin/end/skip) as the orchestrator
moves through its pipeline stages. Subscribers receive each event as it
happens, which lets the web layer stream progress to the frontend over SSE.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit

StepEvent = Dict[str, Any]
EventCallback = Callable[[StepEvent], None]

_TRACE_RECORD_LIMIT = 20
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|authorization|token|secret|password)\s*([=:])\s*([^\s,;]+)"
)
_TRACE_URL = re.compile(r"https?://[^\s<>\"']+")


def safe_trace_url(value: Any, *, limit: int = 2048) -> str:
    """Keep a link usable while dropping query, fragment, and credentials."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return raw.split("?", 1)[0].split("#", 1)[0][:limit]
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))[:limit]


def safe_trace_text(value: Any, *, limit: int = 240) -> str:
    """Normalize text for streamed records without retaining obvious secrets."""
    text = " ".join(str(value or "").split())
    text = _SENSITIVE_ASSIGNMENT.sub(r"\1\2[redacted]", text)
    text = _TRACE_URL.sub(lambda match: safe_trace_url(match.group(0)), text)
    if len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def safe_trace_records(
    records: Optional[Sequence[Mapping[str, Any]]],
    *,
    limit: int = _TRACE_RECORD_LIMIT,
) -> List[Dict[str, Any]]:
    """Allowlist compact browser/audit records and never retain page bodies."""
    sanitized: List[Dict[str, Any]] = []
    for raw in list(records or [])[: max(0, limit)]:
        if not isinstance(raw, Mapping):
            continue
        record: Dict[str, Any] = {}
        for key, text_limit in (
            ("title", 220),
            ("snippet", 360),
            ("provider", 100),
            ("status", 40),
            ("detail", 180),
            ("reason", 180),
            ("request_id", 120),
        ):
            value = raw.get(key)
            if value is not None and str(value).strip():
                record[key] = safe_trace_text(value, limit=text_limit)
        url = safe_trace_url(raw.get("url"))
        if url:
            record["url"] = url
        content_chars = raw.get("content_chars")
        if isinstance(content_chars, (int, float)) and not isinstance(content_chars, bool):
            record["content_chars"] = max(0, int(content_chars))
        if record:
            sanitized.append(record)
    return sanitized


class WorkflowTracer:
    """Collects and broadcasts workflow step events."""

    def __init__(self) -> None:
        self._seq = 0
        self._starts: Dict[str, float] = {}
        self._titles: Dict[str, str] = {}
        self._listeners: List[EventCallback] = []
        self.events: List[StepEvent] = []
        self._lock = threading.Lock()

    def on_event(self, callback: EventCallback) -> None:
        """Subscribe to step events as they are emitted."""
        self._listeners.append(callback)

    def _emit(self, payload: StepEvent) -> None:
        with self._lock:
            self._seq += 1
            event: StepEvent = {"seq": self._seq, **payload}
            self.events.append(event)
        for callback in list(self._listeners):
            try:
                callback(event)
            except Exception:
                # Listeners must never break the pipeline.
                continue

    def begin(self, step_id: str, title: str, detail: Optional[str] = None) -> None:
        """Mark a step as actively running."""
        self._starts[step_id] = time.perf_counter()
        self._titles[step_id] = title
        payload: StepEvent = {"id": step_id, "title": title, "status": "active"}
        if detail:
            payload["detail"] = detail
        self._emit(payload)

    def end(
        self,
        step_id: str,
        *,
        title: Optional[str] = None,
        detail: Optional[str] = None,
        items: Optional[List[Dict[str, str]]] = None,
        status: str = "done",
        badge: Optional[Dict[str, str]] = None,
        records: Optional[Sequence[Mapping[str, Any]]] = None,
        record_kind: Optional[str] = None,
        record_label: Optional[str] = None,
    ) -> None:
        """Mark a step as finished (or errored/skipped after a begin)."""
        start = self._starts.pop(step_id, None)
        duration_ms: Optional[float] = None
        if start is not None:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
        payload: StepEvent = {
            "id": step_id,
            "title": title or self._titles.get(step_id, step_id),
            "status": status,
        }
        if detail:
            payload["detail"] = detail
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if items:
            payload["items"] = items
        if badge:
            payload["badge"] = badge
        if records is not None:
            payload["records"] = safe_trace_records(records)
        if record_kind:
            payload["record_kind"] = safe_trace_text(record_kind, limit=40)
        if record_label:
            payload["record_label"] = safe_trace_text(record_label, limit=120)
        self._emit(payload)

    def skip(self, step_id: str, title: str, detail: Optional[str] = None) -> None:
        """Record that a step was considered but skipped."""
        self._starts.pop(step_id, None)
        payload: StepEvent = {"id": step_id, "title": title, "status": "skipped"}
        if detail:
            payload["detail"] = detail
        self._emit(payload)

    def error(self, step_id: str, detail: Optional[str] = None) -> None:
        """Mark a running step as failed."""
        self.end(step_id, detail=detail, status="error")


class NullWorkflowTracer:
    """No-op stand-in used when tracing is not requested."""

    events: tuple = ()

    def on_event(self, callback: EventCallback) -> None:  # noqa: ARG002
        return None

    def begin(self, *args: Any, **kwargs: Any) -> None:
        return None

    def end(self, *args: Any, **kwargs: Any) -> None:
        return None

    def skip(self, *args: Any, **kwargs: Any) -> None:
        return None

    def error(self, *args: Any, **kwargs: Any) -> None:
        return None


def ensure_tracer(tracer: Optional[WorkflowTracer]) -> Any:
    """Return the given tracer or a no-op replacement."""
    return tracer if tracer is not None else NullWorkflowTracer()
