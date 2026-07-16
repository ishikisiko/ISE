"""Helpers for the native Gemini generateContent REST API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote


def build_google_endpoint(base_url: str, model: str, *, stream: bool = False) -> str:
    """Build a native Gemini content-generation endpoint."""

    normalized_model = str(model or "").strip()
    if normalized_model.startswith("models/"):
        normalized_model = normalized_model[len("models/") :]
    if not normalized_model:
        raise ValueError("A Gemini model name is required.")

    method = "streamGenerateContent?alt=sse" if stream else "generateContent"
    encoded_model = quote(normalized_model, safe="-._")
    return f"{base_url.rstrip('/')}/models/{encoded_model}:{method}"


def _content_parts(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}]
    if not isinstance(content, list):
        return [{"text": str(content)}]

    parts: List[Dict[str, Any]] = []
    for item in content:
        if isinstance(item, str):
            parts.append({"text": item})
            continue
        if not isinstance(item, dict):
            parts.append({"text": str(item)})
            continue

        item_type = item.get("type")
        if item_type == "text":
            parts.append({"text": str(item.get("text") or "")})
            continue
        if item_type != "image_url":
            text = item.get("text") or item.get("content")
            if text is not None:
                parts.append({"text": str(text)})
            continue

        image = item.get("image_url") or {}
        url = image.get("url", "") if isinstance(image, dict) else str(image)
        if not isinstance(url, str) or not url.startswith("data:") or ";base64," not in url:
            continue
        metadata, encoded_data = url.split(";base64,", 1)
        mime_type = metadata.removeprefix("data:") or "image/jpeg"
        parts.append(
            {
                "inlineData": {
                    "mimeType": mime_type,
                    "data": encoded_data,
                }
            }
        )

    return parts or [{"text": ""}]


def build_google_payload(
    messages: List[Dict[str, Any]],
    *,
    max_tokens: int,
    temperature: float,
    stop: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Convert OpenAI-style messages into a Gemini GenerateContent request."""

    system_text: List[str] = []
    contents: List[Dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content", "")
        if role == "system":
            if isinstance(content, str) and content.strip():
                system_text.append(content)
            continue

        google_role = "model" if role == "assistant" else "user"
        contents.append({"role": google_role, "parts": _content_parts(content)})

    payload: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": int(max_tokens),
            "temperature": float(temperature),
        },
    }
    if system_text:
        payload["systemInstruction"] = {
            "parts": [{"text": "\n\n".join(system_text)}]
        }
    if stop:
        payload["generationConfig"]["stopSequences"] = list(stop)
    return payload


def extract_google_content(payload: Dict[str, Any], *, strip: bool = True) -> str:
    """Extract concatenated text from a Gemini response or stream event."""

    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list) or not candidates:
        return ""
    candidate = candidates[0] if isinstance(candidates[0], dict) else {}
    content = candidate.get("content") if isinstance(candidate, dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return ""

    text_parts = [
        part.get("text")
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]
    content_text = "".join(text_parts)
    return content_text.strip() if strip else content_text
