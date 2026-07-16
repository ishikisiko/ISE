from __future__ import annotations

import os
from typing import Any, Dict, Optional

from utils.config_validation import configured_value


DEFAULT_HUGGINGFACE_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
DEFAULT_OPENAI_COMPATIBLE_EMBEDDING_MODEL = "qwen3.7-text-embedding"
DEFAULT_OPENAI_COMPATIBLE_BASE_URL = (
    "https://YOUR_ALIBABA_CLOUD_WORKSPACE_ID.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)


def resolve_embedding_settings(
    config: Optional[Dict[str, Any]] = None,
    *,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve embedding provider settings from config plus an optional model override."""

    embeddings_cfg = {}
    if isinstance(config, dict):
        embeddings_cfg = config.get("embeddings") or {}
        if not isinstance(embeddings_cfg, dict):
            embeddings_cfg = {}

    provider = str(embeddings_cfg.get("provider") or "").strip().lower()
    if not provider:
        provider = "openai_compatible" if embeddings_cfg else "huggingface"

    override_model = str(model_name or "").strip()
    resolved_model = str(embeddings_cfg.get("model") or "").strip()
    if override_model:
        resolved_model = override_model
    if not resolved_model:
        resolved_model = (
            DEFAULT_OPENAI_COMPATIBLE_EMBEDDING_MODEL
            if provider == "openai_compatible"
            else DEFAULT_HUGGINGFACE_EMBEDDING_MODEL
        )

    base_url = configured_value(embeddings_cfg.get("base_url")) or configured_value(
        os.environ.get("EMBEDDING_BASE_URL")
    )

    api_key = configured_value(
        embeddings_cfg.get("api_key")
    ) or configured_value(
        os.environ.get("EMBEDDING_API_KEY")
    ) or configured_value(
        os.environ.get("OPENAI_API_KEY")
    )

    timeout = embeddings_cfg.get("timeout")
    try:
        resolved_timeout = float(timeout) if timeout is not None else None
    except (TypeError, ValueError):
        resolved_timeout = None

    return {
        "provider": provider,
        "model": resolved_model,
        "base_url": base_url or None,
        "api_key": api_key or None,
        "timeout": resolved_timeout,
    }
