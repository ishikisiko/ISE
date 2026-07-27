from __future__ import annotations

import json

import main
from utils.config_validation import validate_context_compaction_config


def test_load_runtime_config_prefers_nlp_config_path(monkeypatch, tmp_path):
    config_path = tmp_path / "alt_config.json"
    config_path.write_text(json.dumps({"LLM_PROVIDER": "glm"}), encoding="utf-8")

    monkeypatch.setenv("NLP_CONFIG_PATH", str(config_path))

    loaded = main.load_runtime_config()

    assert loaded["LLM_PROVIDER"] == "glm"


def test_load_runtime_config_explicit_path_overrides_env(monkeypatch, tmp_path):
    env_path = tmp_path / "env_config.json"
    env_path.write_text(json.dumps({"LLM_PROVIDER": "openai"}), encoding="utf-8")
    explicit_path = tmp_path / "explicit_config.json"
    explicit_path.write_text(json.dumps({"LLM_PROVIDER": "zai"}), encoding="utf-8")

    monkeypatch.setenv("NLP_CONFIG_PATH", str(env_path))

    loaded = main.load_runtime_config(str(explicit_path))

    assert loaded["LLM_PROVIDER"] == "zai"


def test_context_compaction_config_has_safe_defaults_and_full_override():
    defaults = validate_context_compaction_config(None)
    assert defaults["enabled"] is True
    assert defaults["context_window"] == 128000
    assert defaults["threshold"] == 0.75

    configured = validate_context_compaction_config(
        {
            "enabled": False,
            "context_window": 64000,
            "per_model_window": {"model-a": 32000},
            "threshold": 0.8,
            "keep_recent_rounds": 3,
            "max_compactions_per_run": 4,
            "summary_max_tokens": 512,
            "use_judge_llm": False,
            "evidence_pool_max_entries": 12,
        }
    )
    assert configured == {
        "enabled": False,
        "context_window": 64000,
        "per_model_window": {"model-a": 32000},
        "threshold": 0.8,
        "keep_recent_rounds": 3,
        "max_compactions_per_run": 4,
        "summary_max_tokens": 512,
        "use_judge_llm": False,
        "evidence_pool_max_entries": 12,
    }
