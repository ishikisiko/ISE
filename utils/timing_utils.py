from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


def extract_token_usage(response: Any) -> Optional[Dict[str, int]]:
    """Best-effort extraction of token usage from a chat-model response.

    LangChain populates ``usage_metadata`` (``input_tokens`` /
    ``output_tokens`` / ``total_tokens``) on ``AIMessage``; some providers
    expose the same fields under ``response_metadata['token_usage']`` or
    ``['usage']``. Returns ``None`` when no usage is present so callers can
    pass the result straight through as the ``extra`` of ``record_llm_call``
    without polluting timing entries with empty placeholders.
    """
    if response is None:
        return None

    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict) and usage:
        return _normalize_usage_dict(usage)

    metadata = getattr(response, "response_metadata", None)
    if isinstance(metadata, dict):
        for key in ("token_usage", "usage"):
            candidate = metadata.get(key)
            if isinstance(candidate, dict) and candidate:
                normalized = _normalize_usage_dict(candidate)
                if normalized:
                    return normalized
    return None


def _normalize_usage_dict(raw: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Map provider-specific usage keys onto input/output/total tokens."""

    field_aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens", "inputTokens"),
        "output_tokens": ("output_tokens", "completion_tokens", "outputTokens"),
        "total_tokens": ("total_tokens", "total_tokens_billable", "totalTokens"),
    }
    normalized: Dict[str, int] = {}
    for canonical, aliases in field_aliases.items():
        for alias in aliases:
            value = raw.get(alias)
            if value is None:
                continue
            try:
                normalized[canonical] = int(value)
            except (TypeError, ValueError):
                continue
            break
    if not normalized:
        return None
    if "total_tokens" not in normalized and "input_tokens" in normalized and "output_tokens" in normalized:
        normalized["total_tokens"] = normalized["input_tokens"] + normalized["output_tokens"]
    return normalized


class TimingRecorder:
    """Collects timing details for LLM calls and search sources."""

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = bool(enabled)
        self._overall_start: Optional[float] = None
        self._total_recorded = False
        self._total_ms: Optional[float] = None
        self.llm_calls: List[Dict[str, Any]] = []
        self.search_sources: List[Dict[str, Any]] = []
        self.tool_calls: List[Dict[str, Any]] = []

    def start(self) -> None:
        if not self.enabled:
            return
        self._overall_start = time.perf_counter()
        self._total_recorded = False

    def stop(self) -> None:
        if not self.enabled or self._total_recorded:
            return
        if self._overall_start is None:
            return
        duration_ms = (time.perf_counter() - self._overall_start) * 1000
        self._total_ms = round(duration_ms, 2)
        self._overall_start = None
        self._total_recorded = True

    def record_llm_call(
        self,
        *,
        label: str,
        duration_ms: float,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.enabled:
            return
        entry: Dict[str, Any] = {
            "label": label,
            "duration_ms": round(duration_ms, 2),
        }
        if provider:
            entry["provider"] = provider
        if model:
            entry["model"] = model
        if extra:
            entry.update(extra)
        self.llm_calls.append(entry)

    def record_tool_call(
        self,
        *,
        tool: str,
        duration_ms: float,
        success: bool = True,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.enabled:
            return
        entry: Dict[str, Any] = {
            "tool": tool,
            "duration_ms": round(duration_ms, 2),
            "success": success,
        }
        if extra:
            entry.update(extra)
        self.tool_calls.append(entry)

    def record_search_timing(
        self,
        *,
        source: Optional[str],
        label: Optional[str],
        duration_ms: float,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.enabled:
            return
        entry: Dict[str, Any] = {
            "source": source,
            "label": label,
            "duration_ms": round(duration_ms, 2),
        }
        if extra:
            entry.update(extra)
        self.search_sources.append(entry)

    def extend_search_timings(self, timings: Optional[List[Dict[str, Any]]]) -> None:
        if not self.enabled or not timings:
            return
        for item in timings:
            if not isinstance(item, dict):
                continue
            raw_duration = item.get("duration_ms", 0.0)
            try:
                duration_value = float(raw_duration)
            except (TypeError, ValueError):
                duration_value = 0.0
            entry = {
                "source": item.get("source"),
                "label": item.get("label"),
                "duration_ms": round(duration_value, 2),
            }
            if item.get("error"):
                entry["error"] = item["error"]
            for key, value in item.items():
                if key in {"source", "label", "duration_ms", "error"}:
                    continue
                entry[key] = value
            self.search_sources.append(entry)

    def merge_payload(self, payload: Optional[Dict[str, Any]]) -> None:
        """Merge nested executor facts without adding a second total timer."""
        if not self.enabled or not isinstance(payload, dict):
            return
        self.extend_search_timings(payload.get("search_sources"))
        for item in payload.get("llm_calls") or []:
            if not isinstance(item, dict):
                continue
            extra = {
                key: value
                for key, value in item.items()
                if key not in {"label", "duration_ms", "provider", "model"}
            }
            self.record_llm_call(
                label=str(item.get("label") or "nested_llm"),
                duration_ms=float(item.get("duration_ms") or 0.0),
                provider=item.get("provider"),
                model=item.get("model"),
                extra=extra or None,
            )
        for item in payload.get("tool_calls") or []:
            if not isinstance(item, dict):
                continue
            extra = {
                key: value
                for key, value in item.items()
                if key not in {"tool", "duration_ms", "success"}
            }
            self.record_tool_call(
                tool=str(item.get("tool") or "nested_tool"),
                duration_ms=float(item.get("duration_ms") or 0.0),
                success=bool(item.get("success", True)),
                extra=extra or None,
            )

    def to_dict(self) -> Dict[str, Any]:
        if not self.enabled:
            return {}
        payload: Dict[str, Any] = {}
        if self._overall_start is not None and not self._total_recorded:
            self.stop()
        if self._total_ms is not None:
            payload["total_ms"] = self._total_ms
        if self.search_sources:
            payload["search_sources"] = self.search_sources
        if self.llm_calls:
            payload["llm_calls"] = self.llm_calls
        if self.tool_calls:
            payload["tool_calls"] = self.tool_calls
        return payload
