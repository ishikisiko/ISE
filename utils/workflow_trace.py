"""Lightweight workflow tracing for live step streaming.

The tracer records ordered step events (begin/end/skip) as the orchestrator
moves through its pipeline stages. Subscribers receive each event as it
happens, which lets the web layer stream progress to the frontend over SSE.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

StepEvent = Dict[str, Any]
EventCallback = Callable[[StepEvent], None]


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
