"""Durable server-side logs for HTTP traffic and query execution.

The web server uses two complementary log streams:

* ``server.log``, ``stdout.log``, and ``stderr.log`` preserve process output;
* ``access.jsonl`` plus one JSONL file per answer request preserve structured
  request, workflow-step, response, and error events.

Query records deliberately redact credential-like fields and URL query strings.
They otherwise keep the complete request and response payloads when enabled.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TextIO

from utils.audit_log import sanitize_audit_value


DEFAULT_SERVER_LOG_DIR = "runtime/server"
_WRITE_LOCK = threading.Lock()
_TEE_STREAMS: list["_TeeStream"] = []


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def resolve_server_logging_settings(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Resolve the optional ``server_logging`` configuration block."""
    block: Dict[str, Any] = {}
    if isinstance(config, dict) and isinstance(config.get("server_logging"), dict):
        block = config["server_logging"]

    return {
        "enabled": _coerce_bool(block.get("enabled"), False),
        "dir": str(block.get("dir") or DEFAULT_SERVER_LOG_DIR),
        "capture_stdio": _coerce_bool(block.get("capture_stdio"), True),
        "include_request_payload": _coerce_bool(
            block.get("include_request_payload"), True
        ),
        "include_response_payload": _coerce_bool(
            block.get("include_response_payload"), True
        ),
    }


def _append_jsonl(path: str, record: Dict[str, Any]) -> None:
    """Append one durable JSONL record, serializing concurrent Flask threads."""
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    with _WRITE_LOCK:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())


class QueryAuditLog:
    """Append-only event log for one answer endpoint request."""

    def __init__(
        self,
        settings: Dict[str, Any],
        *,
        endpoint: str,
        request_id: Optional[str] = None,
    ) -> None:
        self.enabled = bool(settings.get("enabled"))
        self.endpoint = endpoint
        self.request_id = request_id or uuid.uuid4().hex
        self._started_at = time.perf_counter()
        directory = str(settings.get("dir") or DEFAULT_SERVER_LOG_DIR)
        self.path = os.path.join(directory, "requests", f"{self.request_id}.jsonl")
        self.include_request_payload = bool(settings.get("include_request_payload", True))
        self.include_response_payload = bool(settings.get("include_response_payload", True))

    def record(self, event: str, **payload: Any) -> None:
        """Durably append an event without ever failing the answer request."""
        if not self.enabled:
            return
        entry: Dict[str, Any] = {
            "ts": _utc_timestamp(),
            "event": event,
            "request_id": self.request_id,
            "endpoint": self.endpoint,
            "pid": os.getpid(),
            "thread": threading.current_thread().name,
            "elapsed_ms": round((time.perf_counter() - self._started_at) * 1000, 1),
        }
        for key, value in payload.items():
            if key == "request_payload" and not self.include_request_payload:
                continue
            if key == "response_payload" and not self.include_response_payload:
                continue
            entry[key] = sanitize_audit_value(value, max_depth=None)
        try:
            _append_jsonl(self.path, entry)
        except Exception:  # noqa: BLE001 - observability must not fail requests
            logging.getLogger(__name__).exception(
                "Unable to persist query audit event %s", event
            )


def write_access_event(settings: Dict[str, Any], **payload: Any) -> None:
    """Append one credential-safe HTTP access record when server logging is on."""
    if not settings.get("enabled"):
        return
    entry = {"ts": _utc_timestamp(), **payload}
    safe_entry = sanitize_audit_value(entry, max_depth=None)
    directory = str(settings.get("dir") or DEFAULT_SERVER_LOG_DIR)
    try:
        _append_jsonl(os.path.join(directory, "access.jsonl"), safe_entry)
    except Exception:  # noqa: BLE001 - access logging must not fail requests
        logging.getLogger(__name__).exception("Unable to persist HTTP access event")


class _TeeStream:
    """Forward process output to its original stream and a durable log file."""

    def __init__(self, original: TextIO, path: str) -> None:
        self._original = original
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._file = open(path, "a", encoding="utf-8", buffering=1)

    @property
    def encoding(self) -> str:
        return getattr(self._original, "encoding", "utf-8") or "utf-8"

    def write(self, value: str) -> int:
        written = self._original.write(value)
        with _WRITE_LOCK:
            self._file.write(value)
            self._file.flush()
            if "\n" in value:
                try:
                    os.fsync(self._file.fileno())
                except OSError:
                    pass
        return int(written) if isinstance(written, int) else len(value)

    def flush(self) -> None:
        self._original.flush()
        with _WRITE_LOCK:
            self._file.flush()
            try:
                os.fsync(self._file.fileno())
            except OSError:
                pass

    def isatty(self) -> bool:
        return bool(getattr(self._original, "isatty", lambda: False)())

    def fileno(self) -> int:
        return self._original.fileno()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


class _DurableFileHandler(logging.FileHandler):
    """A file handler that flushes each log record through the OS buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        if self.stream is not None:
            self.stream.flush()
            try:
                os.fsync(self.stream.fileno())
            except OSError:
                self.handleError(record)


def _has_file_handler(logger: logging.Logger, path: str) -> bool:
    target = os.path.abspath(path)
    return any(
        isinstance(handler, logging.FileHandler)
        and os.path.abspath(getattr(handler, "baseFilename", "")) == target
        for handler in logger.handlers
    )


def configure_process_logging(settings: Dict[str, Any]) -> None:
    """Capture Python, Flask, and Werkzeug output in durable server files.

    This is intentionally invoked only by ``server.py`` when run as the web
    process, not when the Flask app is imported by pytest or other modules.
    """
    if not settings.get("enabled"):
        return

    directory = str(settings.get("dir") or DEFAULT_SERVER_LOG_DIR)
    os.makedirs(directory, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(min(root.level or logging.INFO, logging.INFO))
    server_log_path = os.path.join(directory, "server.log")
    if not _has_file_handler(root, server_log_path):
        handler = _DurableFileHandler(server_log_path, encoding="utf-8")
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )
        root.addHandler(handler)

    logging.getLogger("werkzeug").setLevel(logging.INFO)
    if settings.get("capture_stdio"):
        if not isinstance(sys.stdout, _TeeStream):
            stdout = _TeeStream(sys.stdout, os.path.join(directory, "stdout.log"))
            _TEE_STREAMS.append(stdout)
            sys.stdout = stdout
        if not isinstance(sys.stderr, _TeeStream):
            stderr = _TeeStream(sys.stderr, os.path.join(directory, "stderr.log"))
            _TEE_STREAMS.append(stderr)
            sys.stderr = stderr

    logging.getLogger(__name__).info(
        "Persistent server logging enabled under %s", os.path.abspath(directory)
    )
