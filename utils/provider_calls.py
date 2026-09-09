"""Context-scoped observer for external provider requests.

The evidence resolver (discovery searches, self-proof fetches, registry and
graph probes) runs several layers below the request's ``TimingRecorder`` and
has no handle on it. Instead of threading a recorder through every call, the
orchestrator installs an observer for the duration of one answer and the
resolver reports each external request through :func:`notify_provider_call`.

The observer is a ``ContextVar`` so concurrent requests never see each
other's callbacks; when nothing is installed the notification is a no-op.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator, Optional

PROVIDER_CALL_KINDS = (
    "search",
    "extract",
    "resolver_discovery",
    "resolver_verify",
    "resolver_probe",
    "skill_provider",
)

_OBSERVER: ContextVar[Optional[Callable[..., Any]]] = ContextVar(
    "ise_provider_call_observer", default=None
)


@contextmanager
def observe_provider_calls(callback: Optional[Callable[..., Any]]) -> Iterator[None]:
    """Route :func:`notify_provider_call` to ``callback`` inside the block."""
    token = _OBSERVER.set(callback)
    try:
        yield
    finally:
        _OBSERVER.reset(token)


def notify_provider_call(
    *,
    kind: str,
    provider: str,
    duration_ms: float,
    success: bool,
    **extra: Any,
) -> None:
    """Report one external request to the active observer (if any)."""
    callback = _OBSERVER.get()
    if callback is None:
        return
    try:
        callback(kind=kind, provider=provider, duration_ms=duration_ms, success=success, **extra)
    except Exception:  # noqa: BLE001 - observers must never break the resolver
        pass


def record_provider_requests_from_snapshots(recorder: Any, records: Any) -> int:
    """Register provider-call snapshots (search / extraction) on a recorder.

    ``records`` are the raw ``get_last_call_records()`` dicts or the
    normalized ``search_api_calls`` snapshots. Extraction records of kind
    ``extracted_pages`` expand to one request per extractor attempt;
    ``resolved_entities`` records are cache projections and make no request.
    Returns the number of requests registered.
    """
    method = getattr(recorder, "record_provider_request", None)
    if not callable(method) or not isinstance(records, (list, tuple)):
        return 0
    count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        kind = str(record.get("kind") or "")
        if kind == "resolved_entities":
            continue
        if kind == "extracted_pages":
            attempts = record.get("attempts")
            if not isinstance(attempts, list) or not attempts:
                attempts = [
                    {"provider": record.get("source") or record.get("provider"), "status": record.get("status")}
                ]
            for attempt in attempts:
                if not isinstance(attempt, dict):
                    continue
                status = str(attempt.get("status") or "")
                if status == "skipped":
                    continue
                method(
                    kind="extract",
                    provider=str(attempt.get("provider") or record.get("source") or "reference"),
                    duration_ms=0.0,
                    success=status in {"success", "done"},
                    credits=attempt.get("credits"),
                    reason=attempt.get("reason"),
                )
                count += 1
            continue
        error = record.get("error") or record.get("reason")
        status = str(record.get("status") or "done")
        if isinstance(error, str) and error.startswith("skipped:"):
            # ``site:`` incapable providers are skipped without a request.
            continue
        method(
            kind="search",
            provider=str(record.get("source") or record.get("provider") or "search"),
            duration_ms=record.get("duration_ms") or 0.0,
            success=status != "error" and not error,
            credits=record.get("credits"),
            fallback=bool(record.get("fallback")) or None,
            target=record.get("target"),
            result_count=record.get("result_count"),
        )
        count += 1
    return count
