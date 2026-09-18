"""Q0-01: do entry parameters reach the loop's model object and tools?

Builds the production ``LangChainOrchestrator`` with the real factory, the
real ``UniversalChatModel`` and a stub search client, with every network path
disabled. The fake HTTP session captures the exact request payload the loop
sends, which is the only trustworthy witness of ``max_tokens`` /
``temperature`` (design D0 ``param_forwarding_pass``).

QD-20260909-01 (``max_tokens`` / ``temperature``) and QD-20260909-02
(``num_search_results``) were fixed by ``forward-entry-generation-params``
(2026-09-18); the three assertions below are now plain tests.

Set ``ISE_QUALITY_PROBE_OUT=/path/probe.json`` to persist the observed values
for ``tests/quality/validity.py``.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import pytest
import requests

from search.search import SearchClient, SearchHit

pytestmark = pytest.mark.quality_offline

REQUESTED = {"max_tokens": 4000, "temperature": 0.2, "num_search_results": 3, "autonomy": "guided"}


class _RecordingSearchClient(SearchClient):
    source_id = "stub"
    display_name = "Stub Search"

    def __init__(self) -> None:
        super().__init__()
        self.requests: List[Dict[str, Any]] = []

    def search(self, query: str, num_results: int = 5, *, per_source_limit=None, freshness=None, date_restrict=None):
        self.requests.append({"query": query, "num_results": num_results, "per_source_limit": per_source_limit})
        self._reset_timings()
        hits = [SearchHit("France", "https://www.britannica.com/place/France", "Paris is the capital of France.")]
        self._append_call_record(query=query, duration_ms=1.0, hits=hits)
        return hits


class _Response:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        pass

    def json(self) -> Dict[str, Any]:
        return {
            "choices": [{"message": {"content": self._content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }


class _FakeSession:
    """Captures the loop's request payloads; answers with the JSON shim protocol."""

    def __init__(self) -> None:
        self.payloads: List[Dict[str, Any]] = []

    def post(self, endpoint: str, **kwargs: Any) -> _Response:
        payload = kwargs["json"]
        self.payloads.append(payload)
        if len(self.payloads) == 1:
            return _Response(json.dumps({"action": "tool", "tool": "web_search", "args": {"query": "capital of France"}}))
        return _Response(json.dumps({"action": "final", "answer": "Paris is the capital of France [E1]."}))


def _config() -> Dict[str, Any]:
    return {
        "LLM_PROVIDER": "opencode-go",
        "providers": {
            "opencode-go": {
                "api_key": "test-key",
                "model": "deepseek-v4-flash",
                "base_url": "https://opencode.ai/zen/go/v1",
                "max_retries": 0,
                "request_timeout": 5,
            }
        },
        "skills": {"enabled": False},
        "conversation": {"enabled": False},
        "audit": {"enabled": False},
        "termination": {"max_iterations": 4, "judge": {"enabled": False}},
        "orchestration": {
            "official_domain_resolution": {"enabled": False, "graph_probes_enabled": False, "pin_shadow_audit": False},
            "official_page_extraction": {"enabled": False},
            "directFetch": {"enabled": False},
            "reconcile_analysis": False,
        },
    }


@pytest.fixture()
def probe(monkeypatch):
    """Run one guided query through the real builder with all networking disabled."""

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network access is disabled in the param-forwarding probe")

    monkeypatch.setattr(requests.Session, "send", refuse)
    monkeypatch.setattr(requests, "post", refuse)
    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests, "head", refuse)

    from langchain.langchain_llm import UniversalChatModel
    from langchain.langchain_orchestrator import create_langchain_orchestrator

    session = _FakeSession()

    def install_fake_session(self: Any) -> None:
        self._session = session

    monkeypatch.setattr(UniversalChatModel, "_setup_session", install_fake_session)
    search_client = _RecordingSearchClient()
    orchestrator = create_langchain_orchestrator(config=_config(), search_client=search_client, show_timings=True)
    result = orchestrator.answer(
        "What is the capital of France?",
        num_search_results=REQUESTED["num_search_results"],
        per_source_search_results=REQUESTED["num_search_results"],
        max_tokens=REQUESTED["max_tokens"],
        temperature=REQUESTED["temperature"],
        allow_search=True,
        autonomy_mode=REQUESTED["autonomy"],
    )
    loop_model = orchestrator._get_loop_orchestrator().llm
    observed = {
        "requested": dict(REQUESTED),
        "model_object": {"max_tokens": getattr(loop_model, "max_tokens", None), "temperature": getattr(loop_model, "temperature", None)},
        "wire_requests": [
            {"max_tokens": payload.get("max_tokens"), "temperature": payload.get("temperature"), "model": payload.get("model")}
            for payload in session.payloads
        ],
        "search_requests": list(search_client.requests),
        "control_autonomy": (result.get("control") or {}).get("autonomy"),
        "loop_status": (result.get("control") or {}).get("loop_status"),
        "control_request_parameters": (result.get("control") or {}).get("request_parameters"),
    }
    out = os.environ.get("ISE_QUALITY_PROBE_OUT")
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(observed, handle, ensure_ascii=False, indent=2)
    return observed


def test_probe_ran_the_loop_without_network(probe: Dict[str, Any]) -> None:
    assert len(probe["wire_requests"]) >= 2
    assert probe["search_requests"], "web_search was not invoked"
    assert probe["loop_status"] is not None


def test_autonomy_mode_reaches_the_loop(probe: Dict[str, Any]) -> None:
    assert probe["control_autonomy"] == {"mode": REQUESTED["autonomy"], "source": "request"}


def test_max_tokens_reaches_loop_model_calls(probe: Dict[str, Any]) -> None:
    assert all(request["max_tokens"] == REQUESTED["max_tokens"] for request in probe["wire_requests"])


def test_temperature_reaches_loop_model_calls(probe: Dict[str, Any]) -> None:
    assert all(request["temperature"] == REQUESTED["temperature"] for request in probe["wire_requests"])


def test_num_search_results_reaches_web_search(probe: Dict[str, Any]) -> None:
    assert all(request["num_results"] == REQUESTED["num_search_results"] for request in probe["search_requests"])


def test_control_echoes_effective_request_parameters(probe: Dict[str, Any]) -> None:
    echoed = probe["control_request_parameters"] or {}
    assert echoed.get("max_tokens") == REQUESTED["max_tokens"]
    assert echoed.get("temperature") == REQUESTED["temperature"]
    assert echoed.get("num_search_results") == REQUESTED["num_search_results"]
