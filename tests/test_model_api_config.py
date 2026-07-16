from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

import server
from langchain.langchain_llm import create_chat_model, create_role_chat_model
from langchain.langchain_orchestrator import create_langchain_orchestrator
from llm.api import LLMClient
from llm.google_api import build_google_endpoint, build_google_payload
from main import build_llm_client, build_reranker, build_search_client
from search.source_selector import IntelligentSourceSelector


def _provider_config() -> dict:
    return {
        "LLM_PROVIDER": "zai",
        "providers": {
            "zai": {
                "api_key": "test-zai-key",
                "model": "glm-4.6",
                "base_url": "https://example.com/anthropic",
                "available_models": ["glm-4.6", "glm-4.5-air"],
            },
            "google": {
                "api_key": "test-google-key",
                "model": "gemini-2.5-flash",
                "base_url": "https://generativelanguage.googleapis.com/v1beta",
            },
        },
    }


def _opencode_go_config() -> dict:
    return {
        "LLM_PROVIDER": "opencode-go",
        "providers": {
            "opencode-go": {
                "api_key": "test-opencode-go-key",
                "model": "glm-5.2",
                "base_url": "https://opencode.ai/zen/go/v1",
                "api_style": "openai",
                "available_models": ["glm-5.2", "minimax-m3"],
                "model_api_styles": {"minimax-m3": "anthropic"},
            }
        },
    }


def test_role_model_override_is_preserved():
    model = create_role_chat_model(
        _provider_config(),
        {"provider": "zai", "model": "glm-4.5-air"},
    )

    assert model.provider == "zai"
    assert model.model_name == "glm-4.5-air"


def test_model_id_can_resolve_its_provider():
    model = create_chat_model(provider="glm-4.5-air", config=_provider_config())

    assert model.provider == "zai"
    assert model.model_name == "glm-4.5-air"


def test_orchestrator_uses_role_models_and_skips_disabled_postcheck():
    config = _provider_config()
    config.update(
        {
            "domainClassifier": {"provider": "zai", "model": "glm-4.5-air"},
            "routingAndKeywords": {"provider": "zai", "model": "glm-4.5-air"},
            "postcheck": {
                "enabled": False,
                "judge": {"enabled": True, "provider": "missing-provider"},
            },
        }
    )
    primary = create_chat_model(config=config)

    orchestrator = create_langchain_orchestrator(config=config, llm=primary)

    assert orchestrator.classifier_llm.model_name == "glm-4.5-air"
    assert orchestrator.routing_llm.model_name == "glm-4.5-air"
    assert orchestrator.postcheck_llm is primary


def test_opencode_go_glm_uses_openai_compatible_endpoint(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "Go GLM response"}}]}

    class FakeSession:
        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

    model = create_chat_model(config=_opencode_go_config())
    monkeypatch.setattr(model, "_session", FakeSession())

    response = model.invoke([HumanMessage(content="Hello")])

    assert captured["url"] == "https://opencode.ai/zen/go/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-opencode-go-key"
    assert "x-api-key" not in captured["headers"]
    assert captured["json"]["model"] == "glm-5.2"
    assert response.content == "Go GLM response"


def test_opencode_go_anthropic_model_uses_messages_endpoint(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"content": [{"type": "text", "text": "Go MiniMax response"}]}

    class FakeSession:
        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

    model = create_chat_model(
        provider="opencode-go",
        model="minimax-m3",
        config=_opencode_go_config(),
    )
    monkeypatch.setattr(model, "_session", FakeSession())

    response = model.invoke(
        [SystemMessage(content="Be concise."), HumanMessage(content="Hello")]
    )

    assert captured["url"] == "https://opencode.ai/zen/go/v1/messages"
    assert captured["headers"]["x-api-key"] == "test-opencode-go-key"
    assert "Authorization" not in captured["headers"]
    assert captured["json"]["system"] == "Be concise."
    assert captured["json"]["model"] == "minimax-m3"
    assert response.content == "Go MiniMax response"


def test_legacy_client_resolves_opencode_go_model_api_style():
    client = build_llm_client(
        _opencode_go_config(),
        provider_or_model="opencode-go",
        model_override="minimax-m3",
    )

    assert client.provider == "opencode-go"
    assert client.model_id == "minimax-m3"
    assert client.anthropic_compatible is True


def test_native_google_request_shape(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [
                    {"content": {"parts": [{"text": "Gemini response"}]}}
                ]
            }

    class FakeSession:
        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

    model = create_chat_model(provider="google", config=_provider_config())
    monkeypatch.setattr(model, "_session", FakeSession())

    response = model.invoke(
        [SystemMessage(content="Be concise."), HumanMessage(content="Hello")]
    )

    assert captured["url"].endswith(
        "/models/gemini-2.5-flash:generateContent"
    )
    assert captured["headers"]["x-goog-api-key"] == "test-google-key"
    assert "Authorization" not in captured["headers"]
    assert captured["json"]["systemInstruction"]["parts"][0]["text"] == "Be concise."
    assert captured["json"]["contents"][0]["role"] == "user"
    assert response.content == "Gemini response"


def test_legacy_client_uses_native_google_api():
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [
                    {"content": {"parts": [{"text": "Legacy response"}]}}
                ]
            }

    class FakeSession:
        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

    client = LLMClient(
        api_key="test-google-key",
        model_id="gemini-2.5-flash",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        provider="google",
    )
    client.session = FakeSession()

    response = client.chat("Be concise.", "Hello")

    assert captured["url"].endswith(
        "/models/gemini-2.5-flash:generateContent"
    )
    assert captured["headers"]["x-goog-api-key"] == "test-google-key"
    assert response["content"] == "Legacy response"


def test_google_multimodal_payload_and_stream_endpoint():
    payload = build_google_payload(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,abc123"},
                    },
                ],
            }
        ],
        max_tokens=200,
        temperature=0.2,
        stop=["STOP"],
    )

    assert payload["contents"][0]["parts"][1] == {
        "inlineData": {"mimeType": "image/png", "data": "abc123"}
    }
    assert payload["generationConfig"]["stopSequences"] == ["STOP"]
    assert build_google_endpoint(
        "https://generativelanguage.googleapis.com/v1beta",
        "models/gemini-2.5-flash",
        stream=True,
    ).endswith("/models/gemini-2.5-flash:streamGenerateContent?alt=sse")


def test_rerank_enabled_flag_is_authoritative():
    disabled, _ = build_reranker(
        {
            "rerank": {
                "enabled": False,
                "provider": "qwen",
                "providers": {"qwen": {"api_key": "test-key"}},
            }
        }
    )
    enabled, _ = build_reranker(
        {
            "rerank": {
                "enabled": True,
                "provider": "qwen",
                "providers": {"qwen": {"api_key": "test-key"}},
            }
        }
    )

    assert disabled is None
    assert enabled is not None


def test_source_selector_reads_google_cx_from_config():
    selector = IntelligentSourceSelector(
        use_llm=False,
        config={
            "GOOGLE_API_KEY": "google-key",
            "GOOGLE_CX": "google-cx",
            "FINNHUB_API_KEY": "finnhub-key",
        },
    )

    assert selector.google_api_key == "google-key"
    assert selector.google_cx == "google-cx"
    assert selector.finnhub_api_key == "finnhub-key"


def test_example_placeholders_are_not_treated_as_credentials():
    config_path = Path(__file__).resolve().parents[1] / "config.example.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config["LLM_PROVIDER"] == "opencode-go"
    assert config["providers"]["opencode-go"]["model"] == "deepseek-v4-flash"
    assert config["domainClassifier"] == {
        "provider": "opencode-go",
        "model": "deepseek-v4-flash",
    }
    assert config["routingAndKeywords"] == {
        "provider": "opencode-go",
        "model": "deepseek-v4-flash",
    }
    assert config["postcheck"]["judge"] == {
        "enabled": True,
        "provider": "opencode-go",
        "model": "deepseek-v4-flash",
    }
    assert config["embeddings"] == {
        "provider": "openai_compatible",
        "model": "qwen3.7-text-embedding",
        "base_url": (
            "https://YOUR_ALIBABA_CLOUD_WORKSPACE_ID.cn-beijing.maas.aliyuncs.com/"
            "compatible-mode/v1"
        ),
        "api_key": "YOUR_DASHSCOPE_API_KEY_HERE",
    }
    assert build_search_client(config) is None
    assert build_reranker(config)[0] is None
    with pytest.raises(ValueError, match="configured API key"):
        create_chat_model(config=config)


def test_models_api_only_advertises_configured_providers(monkeypatch):
    config = _provider_config()
    config["providers"]["google"]["api_key"] = "YOUR_GOOGLE_API_KEY_HERE"
    monkeypatch.setattr(server, "load_base_config", lambda: config)

    with server.app.test_client() as client:
        response = client.get("/api/models")

    assert response.status_code == 200
    models = response.get_json()["models"]
    assert {model["provider"] for model in models} == {"zai"}
    assert [model["id"] for model in models] == ["glm-4.6", "glm-4.5-air"]
