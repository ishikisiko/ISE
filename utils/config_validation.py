"""Validation helpers for user-supplied configuration values."""

from __future__ import annotations

from typing import Any, Dict, Optional


def configured_value(value: Any) -> str:
    """Return a stripped value, or empty text for template placeholders."""

    get_secret_value = getattr(value, "get_secret_value", None)
    if callable(get_secret_value):
        value = get_secret_value()
    if not isinstance(value, str):
        return ""
    cleaned = value.strip()
    upper_value = cleaned.upper()
    if not cleaned or any(
        marker in upper_value
        for marker in ("YOUR_", "REPLACE", "TODO", "_HERE")
    ):
        return ""
    return cleaned


def has_configured_value(value: Any) -> bool:
    return bool(configured_value(value))


def validate_context_compaction_config(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize the bounded-context configuration without accepting bad values."""
    from orchestrators.context_compaction import normalize_context_compaction_config

    return normalize_context_compaction_config(value)
