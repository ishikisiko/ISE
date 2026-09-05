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


def validate_autonomy_config(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate the ``autonomy`` config block.

    Returns a normalized copy. Raises ``ValueError`` for an unknown mode name
    or an unparseable rule-strength field, so a typo in ``config.json`` fails
    loudly at load time rather than silently coercing to a default preset.
    Budget numbers are not rejected here -- they are resolved with fallbacks
    inside ``resolve_autonomy_policy`` (invariant I4 wants a bound to always
    exist, so invalid numbers degrade rather than abort).
    """
    from orchestrators.autonomy_policy import (
        BINDING_VALUES,
        CHECKLIST_INJECTION_VALUES,
        CLARIFICATION_OWNER_VALUES,
        FORCED_SYNTHESIS_VALUES,
        NARRATION_GUARD_VALUES,
        VALID_MODES,
    )

    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("autonomy block must be a JSON object")

    normalized: Dict[str, Any] = {}
    mode = value.get("mode")
    if mode is not None:
        mode = str(mode).strip().lower()
        if mode not in VALID_MODES:
            raise ValueError(
                f"autonomy.mode {mode!r} is not one of {VALID_MODES}"
            )
        normalized["mode"] = mode

    profiles = value.get("profiles")
    if profiles is not None:
        if not isinstance(profiles, dict):
            raise ValueError("autonomy.profiles must be a JSON object")
        validated_profiles: Dict[str, Any] = {}
        enum_fields = {
            "checklist_injection": CHECKLIST_INJECTION_VALUES,
            "critic_verdict": BINDING_VALUES,
            "citation_check": BINDING_VALUES,
            "narration_guard": NARRATION_GUARD_VALUES,
            "forced_synthesis": FORCED_SYNTHESIS_VALUES,
            "clarification_owner": CLARIFICATION_OWNER_VALUES,
        }
        for profile_name, profile_body in profiles.items():
            if not isinstance(profile_body, dict):
                raise ValueError(
                    f"autonomy.profiles.{profile_name} must be a JSON object"
                )
            body: Dict[str, Any] = {}
            for key, allowed in enum_fields.items():
                if key in profile_body:
                    parsed = str(profile_body[key]).strip().lower()
                    if parsed not in allowed:
                        raise ValueError(
                            f"autonomy.profiles.{profile_name}.{key} "
                            f"{parsed!r} is not one of {allowed}"
                        )
                    body[key] = parsed
            if "judge_enabled" in profile_body:
                body["judge_enabled"] = bool(profile_body["judge_enabled"])
            if "budgets" in profile_body:
                body["budgets"] = profile_body["budgets"]
            validated_profiles[str(profile_name)] = body
        normalized["profiles"] = validated_profiles
    return normalized
