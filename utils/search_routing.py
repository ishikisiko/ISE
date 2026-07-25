from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


# Shared helpers for the default loop entrypoint: small-talk short-circuit,
# source normalization, and tolerant JSON/bool coercion.
SMALL_TALK_PATTERNS = {
    "hi",
    "hello",
    "hey",
    "thanks",
    "thank you",
    "good morning",
    "good night",
    "bye",
    "goodbye",
    "see you",
    "你好",
    "您好",
    "嗨",
    "谢谢",
    "感谢",
    "早上好",
    "晚上好",
    "晚安",
    "再见",
    "拜拜",
    "哈囉",
    "謝謝",
    "感謝",
    "早安",
    "再見",
    "掰掰",
}

SMALL_TALK_SUBSTRING_TRIGGERS = (
    "你好",
    "您好",
    "嗨",
    "哈喽",
    "拜拜",
    "谢谢",
    "感谢",
    "哈囉",
    "掰掰",
    "謝謝",
    "感謝",
)


def normalize_sources(sources: Optional[List[str]]) -> List[str]:
    normalized: List[str] = []
    if not sources:
        return normalized
    for item in sources:
        if item is None:
            continue
        token = str(item).strip().lower()
        if token and token not in normalized:
            normalized.append(token)
    return normalized


def is_small_talk_query(query: str) -> bool:
    stripped = (query or "").strip()
    if not stripped:
        return True

    lowered = stripped.lower()
    if lowered in SMALL_TALK_PATTERNS or stripped in SMALL_TALK_PATTERNS:
        return True

    return any(token in stripped for token in SMALL_TALK_SUBSTRING_TRIGGERS)


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    content = (text or "").strip()
    if not content:
        return None

    candidates = [content]
    smart_quote_normalized = content.translate(
        str.maketrans({"\u201c": '"', "\u201d": '"'})
    )
    if smart_quote_normalized != content:
        candidates.append(smart_quote_normalized)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start == -1 or end == -1 or end <= start:
                continue
            try:
                parsed = json.loads(candidate[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                continue
    return None


def coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on", "需要"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", "不需要"}:
            return False
    return default
