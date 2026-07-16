from __future__ import annotations

from utils.embedding_config import (
    DEFAULT_HUGGINGFACE_EMBEDDING_MODEL,
    DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
    DEFAULT_OPENAI_COMPATIBLE_EMBEDDING_MODEL,
    resolve_embedding_settings,
)


CONFIGURED_QWEN_BASE_URL = (
    "https://workspace123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)


def test_resolve_embedding_settings_defaults_to_huggingface_without_config():
    settings = resolve_embedding_settings()

    assert settings["provider"] == "huggingface"
    assert settings["model"] == DEFAULT_HUGGINGFACE_EMBEDDING_MODEL
    assert settings["base_url"] is None
    assert settings["api_key"] is None


def test_resolve_embedding_settings_uses_openai_compatible_config():
    settings = resolve_embedding_settings(
        {
            "embeddings": {
                "provider": "openai_compatible",
                "model": DEFAULT_OPENAI_COMPATIBLE_EMBEDDING_MODEL,
                "base_url": CONFIGURED_QWEN_BASE_URL,
                "api_key": "secret",
            }
        }
    )

    assert settings["provider"] == "openai_compatible"
    assert settings["model"] == DEFAULT_OPENAI_COMPATIBLE_EMBEDDING_MODEL
    assert settings["base_url"] == CONFIGURED_QWEN_BASE_URL
    assert settings["api_key"] == "secret"


def test_resolve_embedding_settings_respects_model_override():
    settings = resolve_embedding_settings(
        {
            "embeddings": {
                "provider": "openai_compatible",
                "model": DEFAULT_OPENAI_COMPATIBLE_EMBEDDING_MODEL,
                "base_url": CONFIGURED_QWEN_BASE_URL,
                "api_key": "secret",
            }
        },
        model_name="custom-embedding-model",
    )

    assert settings["provider"] == "openai_compatible"
    assert settings["model"] == "custom-embedding-model"


def test_placeholder_values_allow_environment_fallback(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BASE_URL", CONFIGURED_QWEN_BASE_URL)
    monkeypatch.setenv("EMBEDDING_API_KEY", "environment-secret")

    settings = resolve_embedding_settings(
        {
            "embeddings": {
                "provider": "openai_compatible",
                "model": DEFAULT_OPENAI_COMPATIBLE_EMBEDDING_MODEL,
                "base_url": DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
                "api_key": "YOUR_DASHSCOPE_API_KEY_HERE",
            }
        }
    )

    assert settings["base_url"] == CONFIGURED_QWEN_BASE_URL
    assert settings["api_key"] == "environment-secret"
