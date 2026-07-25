"""Persisted per-turn process audit (JSONL).

Records one self-contained audit line per answered turn under
``runtime/audit/<conversation_id>.jsonl``: workflow step events, control
metadata, search queries, timing payloads, warnings, and optionally the
answer text or the complete response payload. Writes are best-effort; callers
decide how to surface errors.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DEFAULT_AUDIT_DIR = "runtime/audit"
DEFAULT_MAX_FILES = 200
DEFAULT_MAX_BYTES_PER_RECORD = 65536
MIN_MAX_BYTES_PER_RECORD = 256

_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]")
_AUDIT_WRITE_LOCK = threading.Lock()
_SENSITIVE_FIELD_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "headers",
    "full_content",
    "prompt",
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|authorization|cookie|token|secret|password)\s*([:=])\s*[^\s,;]+"
)
_URL_WITH_QUERY = re.compile(r"https?://[^\s?#]+(?:\?[^\s#]*)?(?:#[^\s]*)?")


def _normalize_max_bytes(value: int) -> int:
    return 0 if value <= 0 else max(MIN_MAX_BYTES_PER_RECORD, value)


def resolve_audit_settings(
    config: Optional[Dict[str, Any]],
    cli_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve effective audit settings: CLI override > config block > defaults."""
    block: Dict[str, Any] = {}
    if isinstance(config, dict):
        raw = config.get("audit")
        if isinstance(raw, dict):
            block = raw

    def _bool(key: str, default: bool) -> bool:
        value = block.get(key)
        return bool(value) if value is not None else default

    def _int(key: str, default: int) -> int:
        try:
            return int(block.get(key, default))
        except (TypeError, ValueError):
            return default

    max_bytes_per_record = _normalize_max_bytes(
        _int("max_bytes_per_record", DEFAULT_MAX_BYTES_PER_RECORD)
    )
    settings: Dict[str, Any] = {
        "enabled": _bool("enabled", False),
        "dir": str(block.get("dir") or DEFAULT_AUDIT_DIR),
        "include_answer": _bool("include_answer", True),
        "include_full_result": _bool("include_full_result", False),
        # Zero explicitly means retain every conversation audit file.
        "max_files": max(0, _int("max_files", DEFAULT_MAX_FILES)),
        "max_bytes_per_record": max_bytes_per_record,
    }

    override = (cli_override or "").strip().lower()
    if override == "file":
        settings["enabled"] = True
    elif override == "off":
        settings["enabled"] = False
    return settings


def _sanitize_filename(conversation_id: Optional[str]) -> str:
    token = _SAFE_NAME_PATTERN.sub("_", str(conversation_id or ""))
    token = token.strip(".") or "unknown"
    return token[:120]


def _serialized_size(record: Dict[str, Any]) -> int:
    return len(json.dumps(record, ensure_ascii=False, default=str).encode("utf-8"))


def _redact_text(value: str) -> str:
    text = _SENSITIVE_ASSIGNMENT.sub(r"\1\2[redacted]", value)
    return _URL_WITH_QUERY.sub(lambda match: match.group(0).split("?", 1)[0].split("#", 1)[0], text)


def sanitize_audit_value(
    value: Any,
    *,
    max_depth: Optional[int] = 5,
    depth: int = 0,
) -> Any:
    """Return a JSON-safe value with credential-like fields removed.

    ``max_depth=None`` is intended for the explicit full-payload audit mode.
    It preserves every serializable response field while retaining the same
    credential and URL-query redaction used by the compact audit record.
    """
    if max_depth is not None and depth > max_depth:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        safe: Dict[str, Any] = {}
        for key, child in value.items():
            name = str(key)
            if any(marker in name.casefold() for marker in _SENSITIVE_FIELD_MARKERS):
                continue
            safe[name] = sanitize_audit_value(
                child,
                max_depth=max_depth,
                depth=depth + 1,
            )
        return safe
    if isinstance(value, list):
        return [
            sanitize_audit_value(child, max_depth=max_depth, depth=depth + 1)
            for child in value
        ]
    if isinstance(value, tuple):
        return [
            sanitize_audit_value(child, max_depth=max_depth, depth=depth + 1)
            for child in value
        ]
    return _redact_text(str(value))


def _safe_audit_value(value: Any, *, depth: int = 0) -> Any:
    """Copy a bounded, credential-safe value for the compact audit record."""
    return sanitize_audit_value(value, depth=depth)


def _truncate_text(value: str, keep_bytes: int) -> str:
    if keep_bytes <= 0:
        return ""
    return value.encode("utf-8")[:keep_bytes].decode("utf-8", errors="ignore")


def _shorten_text(value: str, overflow: int) -> str:
    """Shorten a string enough to absorb the requested byte overflow."""
    marker = "..."
    original_bytes = len(value.encode("utf-8"))
    keep_bytes = max(0, original_bytes - overflow - len(marker))
    shortened = _truncate_text(value, keep_bytes)
    if keep_bytes < original_bytes:
        shortened += marker
    # Avoid a no-progress loop when a value is already just the marker.
    return "" if shortened == value else shortened


def _string_locations(value: Any) -> List[tuple[Any, Any, str]]:
    """Return mutable locations for string values, largest-first callers decide."""
    locations: List[tuple[Any, Any, str]] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            if isinstance(child, str):
                locations.append((value, child_key, child))
            else:
                locations.extend(_string_locations(child))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, str):
                locations.append((value, index, child))
            else:
                locations.extend(_string_locations(child))
    return locations


def _serialized_value_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _prune_largest_collection(record: Dict[str, Any]) -> bool:
    """Reduce optional structured payloads after string truncation is exhausted."""
    for field in ("steps", "control", "response_times", "search_warnings"):
        value = record.get(field)
        if isinstance(value, list) and value:
            keep = len(value) // 2
            del value[keep:]
            return True
        if isinstance(value, dict) and value:
            largest_key = max(
                value,
                key=lambda key: _serialized_value_size(value[key]),
            )
            del value[largest_key]
            return True
    return False


def _compact_control_projection(record: Dict[str, Any]) -> bool:
    """Preserve the final orchestration facts before generic audit pruning."""
    control = record.get("control")
    if not isinstance(control, dict) or control.get("_audit_compacted"):
        return False

    compact: Dict[str, Any] = {}
    for key in (
        "search_mode",
        "final_executor",
        "loop_status",
        "loop_iterations",
        "termination_policy",
        "providers",
        "evidence_coverage",
    ):
        if key in control:
            compact[key] = control[key]

    trace = control.get("execution_trace")
    if isinstance(trace, dict):
        trace_compact = {
            key: trace.get(key, [])
            for key in ("configured", "requested", "eligible", "executed")
        }
        events = trace.get("events")
        if isinstance(events, list):
            # Keep opening plan facts and terminal evidence/verification facts.
            trace_compact["events"] = (events[:2] + events[-3:])[:5]
            trace_compact["truncated"] = len(events) > len(trace_compact["events"])
        compact["execution_trace"] = trace_compact

    if not compact:
        return False
    compact["_audit_compacted"] = True
    record["control"] = compact
    return True


def _apply_size_cap(record: Dict[str, Any], max_bytes: int) -> Dict[str, Any]:
    max_bytes = _normalize_max_bytes(max_bytes)
    if max_bytes <= 0:
        return record
    if _serialized_size(record) <= max_bytes:
        return record

    # Round-trip through JSON so nested event/result objects are not mutated.
    record = json.loads(json.dumps(record, ensure_ascii=False, default=str))
    record["truncated"] = True

    # Answer carries the largest expected payload, so reduce it first as promised
    # by the spec, then shrink other string fields until the configured cap fits.
    while _serialized_size(record) > max_bytes:
        answer = record.get("answer")
        if isinstance(answer, str) and answer:
            overflow = _serialized_size(record) - max_bytes
            record["answer"] = _shorten_text(answer, overflow)
            continue

        # Large plan/trace metadata should become its compact projection before
        # generic string trimming can erase its configured/executed summary.
        if _compact_control_projection(record):
            continue

        locations = [location for location in _string_locations(record) if location[2]]
        if locations:
            parent, key, value = max(
                locations,
                key=lambda item: len(item[2].encode("utf-8")),
            )
            overflow = _serialized_size(record) - max_bytes
            parent[key] = _shorten_text(value, overflow)
            continue

        if not _prune_largest_collection(record):
            break

    return record


def build_audit_record(
    *,
    conversation_id: Optional[str],
    query: str,
    allow_search: bool,
    events: Optional[List[Dict[str, Any]]] = None,
    result: Optional[Dict[str, Any]] = None,
    include_answer: bool = True,
    include_full_result: bool = False,
    max_bytes_per_record: int = DEFAULT_MAX_BYTES_PER_RECORD,
) -> Dict[str, Any]:
    """Assemble one audit record from tracer events and the result payload."""
    result = result if isinstance(result, dict) else {}
    record: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "conversation_id": conversation_id,
        "query": _redact_text(query or ""),
        "allow_search": bool(allow_search),
        "steps": _safe_audit_value(list(events or [])),
    }
    for key in ("control", "search_query", "response_times", "search_warnings"):
        if key in result and result.get(key) is not None:
            record[key] = _safe_audit_value(result.get(key))
    if include_answer:
        answer = result.get("answer")
        if answer is not None:
            record["answer"] = _safe_audit_value(answer)
    if include_full_result:
        record["result"] = sanitize_audit_value(result, max_depth=None)
    return _apply_size_cap(record, max_bytes_per_record)


class AuditRecorder:
    """Append-only per-conversation audit log with LRU eviction."""

    def __init__(
        self,
        directory: str,
        *,
        include_answer: bool = True,
        include_full_result: bool = False,
        max_files: int = DEFAULT_MAX_FILES,
        max_bytes_per_record: int = DEFAULT_MAX_BYTES_PER_RECORD,
    ) -> None:
        self.directory = directory or DEFAULT_AUDIT_DIR
        self.include_answer = include_answer
        self.include_full_result = include_full_result
        self.max_files = max(0, int(max_files))
        self.max_bytes_per_record = _normalize_max_bytes(int(max_bytes_per_record))

    def record_turn(
        self,
        *,
        conversation_id: Optional[str],
        query: str,
        allow_search: bool,
        events: Optional[List[Dict[str, Any]]] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Append one turn record and return the file path written."""
        record = build_audit_record(
            conversation_id=conversation_id,
            query=query,
            allow_search=allow_search,
            events=events,
            result=result,
            include_answer=self.include_answer,
            include_full_result=self.include_full_result,
            max_bytes_per_record=self.max_bytes_per_record,
        )
        line = json.dumps(record, ensure_ascii=False, default=str)
        # A recorder is constructed for each web request, so the lock needs to
        # cover instances as well as consecutive calls on one instance.
        with _AUDIT_WRITE_LOCK:
            os.makedirs(self.directory, exist_ok=True)
            path = os.path.join(
                self.directory, f"{_sanitize_filename(conversation_id)}.jsonl"
            )
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._evict_if_needed()
        return path

    def _evict_if_needed(self) -> None:
        if self.max_files <= 0:
            return
        try:
            names = [
                name
                for name in os.listdir(self.directory)
                if name.endswith(".jsonl")
            ]
            if len(names) <= self.max_files:
                return
            names.sort(
                key=lambda name: os.path.getmtime(os.path.join(self.directory, name))
            )
            for name in names[: len(names) - self.max_files]:
                try:
                    os.remove(os.path.join(self.directory, name))
                except OSError:
                    continue
        except OSError:
            return
