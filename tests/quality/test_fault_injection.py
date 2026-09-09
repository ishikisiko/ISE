"""Q2-03: provider-outage, quota-exhaustion and LLM-error behaviour, fully offline (D10)."""
from __future__ import annotations

import json
import time
from typing import Any, List, Optional

import pytest
import requests

from search.search import (
    BraveSearchClient,
    CombinedSearchClient,
    PrioritySearchClient,
    SearchClient,
    SearchHit,
    build_priority_chain,
)
from utils.retrieval_trace import search_call_snapshots

pytestmark = pytest.mark.quality_offline

HIT = [SearchHit(title="t", url="https://example.com/", snippet="s")]


class _Stub(SearchClient):
    """Provider double: raise, return nothing, sleep-then-timeout, or return hits."""

    def __init__(self, source_id: str, *, hits: Optional[List[SearchHit]] = None, error: Optional[Exception] = None, site_operator: bool = True) -> None:
        super().__init__()
        self.source_id = source_id
        self.display_name = source_id.title()
        self.supports_site_operator = site_operator
        self.hits = list(hits or [])
        self.error = error
        self.calls = 0

    def search(self, query: str, num_results: int = 5, **kwargs: Any) -> List[SearchHit]:
        self._reset_timings()
        self.calls += 1
        if self.error is not None:
            self._append_timing({"source": self.source_id, "label": self.display_name, "duration_ms": 1.0, "error": str(self.error)})
            self._append_call_record(query=query, duration_ms=1.0, error=str(self.error))
            raise self.error
        self._append_timing({"source": self.source_id, "label": self.display_name, "duration_ms": 1.0})
        self._append_call_record(query=query, duration_ms=1.0, hits=self.hits)
        return list(self.hits)


def _members(client: Any) -> List[str]:
    return [getattr(member, "source_id", type(member).__name__) for member in getattr(client, "clients", [client])]


@pytest.mark.parametrize(
    "config,expected_tiers",
    [
        ({}, [["brave"], ["firecrawl", "tavily"], ["parallel", "brightdata", "google"]]),
        ({"searchFallback": {"batch_sizes": [1, 1], "order": ["tavily", "google"]}}, [["brave"], ["tavily"], ["google"], ["firecrawl", "parallel", "brightdata"]]),
    ],
)
def test_priority_chain_falls_through_tiers_until_a_result(config, expected_tiers):
    brave = _Stub("brave", error=requests.ConnectionError("brave down"))
    firecrawl = _Stub("firecrawl")  # empty
    tavily = _Stub("tavily", error=requests.Timeout("tavily timed out"))
    parallel = _Stub("parallel", hits=HIT)
    brightdata = _Stub("brightdata", error=RuntimeError("HTTP 503"))
    google = _Stub("google", hits=HIT)
    chain = build_priority_chain(config, [brave, firecrawl, tavily, parallel, brightdata, google])
    assert isinstance(chain, PrioritySearchClient)
    assert [_members(tier) for tier in chain.clients] == expected_tiers

    hits = chain.search("outage query", num_results=5)

    assert hits and hits[0].url == "https://example.com/"
    records = chain.get_last_call_records()
    snapshots = search_call_snapshots(records)
    by_provider = {snapshot["provider"]: snapshot for snapshot in snapshots}
    assert brave.calls == 1 and by_provider["brave"]["status"] == "error"
    assert "brave down" in by_provider["brave"]["reason"]
    assert by_provider["brave"].get("fallback") is None
    # Every provider consulted after the primary is a fallback request.
    assert all(snapshot.get("fallback") for snapshot in snapshots if snapshot["provider"] != "brave")
    if config:
        # tavily (tier 2) times out, google (tier 3) answers; the leftover tier is never queried.
        assert tavily.calls == 1 and google.calls == 1
        assert firecrawl.calls == 0 and parallel.calls == 0 and brightdata.calls == 0
        assert "timed out" in by_provider["tavily"]["reason"]
    else:
        assert firecrawl.calls == 1 and tavily.calls == 1
        assert parallel.calls == 1 and brightdata.calls == 1 and google.calls == 1
        assert by_provider["brightdata"]["status"] == "error"


def test_all_providers_failing_yields_no_hits_but_full_records():
    chain = build_priority_chain({}, [_Stub("brave", error=RuntimeError("x")), _Stub("firecrawl"), _Stub("tavily")])
    assert chain.search("q") == []
    statuses = [(record["source"], record["status"]) for record in chain.get_last_call_records()]
    assert ("brave", "error") in statuses and ("firecrawl", "done") in statuses and ("tavily", "done") in statuses
    assert [error["source"] for error in chain.get_last_errors()] == ["Brave"]


def test_site_operator_query_skips_incapable_provider_without_a_request():
    anysearch = _Stub("anysearch", hits=HIT, site_operator=False)
    brave = _Stub("brave", hits=HIT)
    chain = build_priority_chain({"searchFallback": {"primary": "anysearch"}}, [anysearch, brave])
    chain.search("pricing site:openai.com")
    assert anysearch.calls == 0 and brave.calls == 1
    records = chain.get_last_call_records()
    assert records[0]["source"] == "anysearch" and records[0]["error"].startswith("skipped:")


def _brave_usage(tmp_path, count: int, slot: str = "primary") -> str:
    path = tmp_path / "brave_usage.jsonl"
    month = time.strftime("%Y-%m", time.gmtime())
    with path.open("w", encoding="utf-8") as handle:
        for _ in range(count):
            handle.write(json.dumps({"timestamp": f"{month}-01T00:00:00Z", "provider": "brave", "slot": slot, "success": True}) + "\n")
    return str(path)


def test_brave_monthly_limit_switches_key_then_next_tier(monkeypatch, tmp_path):
    calls: List[str] = []

    class _Resp:
        def __init__(self, status: int, payload=None) -> None:
            self.status_code = status
            self._payload = payload or {}
            self.headers = {}

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise requests.HTTPError(f"HTTP {self.status_code}")

        def json(self):
            return self._payload

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(headers["X-Subscription-Token"])
        if headers["X-Subscription-Token"] == "primary-key":
            return _Resp(429, {"error": "quota"})
        return _Resp(200, {"web": {"results": [{"title": "Brave", "url": "https://brave.example/", "description": "d"}]}})

    monkeypatch.setattr("search.search.requests.get", fake_get)
    monkeypatch.setattr("search.search.time.sleep", lambda *_: None)
    usage = _brave_usage(tmp_path, count=1500)
    brave = BraveSearchClient(primary_api_key="primary-key", secondary_api_key="secondary-key", usage_log_path=usage, primary_switch_limit=1500, rps=1000)
    hits = brave.search("q")
    # Primary already at its switch limit: the secondary key is tried first and succeeds.
    assert calls == ["secondary-key"]
    assert hits and brave.get_last_call_records()[-1]["slot"] == "secondary"

    # Both keys rejected (quota): the chain moves on to the next tier.
    monkeypatch.setattr("search.search.requests.get", lambda *a, **k: _Resp(429, {"error": "quota"}))
    fallback = _Stub("tavily", hits=HIT)
    chain = build_priority_chain({}, [brave, fallback])
    result = chain.search("q")
    assert result == HIT and fallback.calls == 1
    records = chain.get_last_call_records()
    assert [record["source"] for record in records][-1] == "tavily"
    assert any(record["source"] == "brave" and record["status"] == "error" for record in records)


def test_combined_tier_survives_member_exception():
    combined = CombinedSearchClient([_Stub("firecrawl", error=RuntimeError("boom")), _Stub("tavily", hits=HIT)])
    assert combined.search("q") == HIT
    assert {record["source"]: record["status"] for record in combined.get_last_call_records()} == {"firecrawl": "error", "tavily": "done"}


@pytest.mark.parametrize("status", [401, 500])
def test_llm_http_error_is_surfaced_and_model_is_not_swapped(monkeypatch, status):
    from langchain.langchain_llm import UniversalChatModel
    from orchestrators.react_agent_orchestrator import ReactAgentOrchestrator

    class _Session:
        def post(self, endpoint, **kwargs):
            class _Resp:
                status_code = status
                headers: dict = {}

                def raise_for_status(self):
                    raise requests.HTTPError(f"{status} Server Error")

                def json(self):
                    return {"error": {"type": "server_error"}}

            return _Resp()

    model = UniversalChatModel(api_key="test-key", provider="opencode-go", max_retries=0)
    monkeypatch.setattr(model, "_session", _Session())
    orchestrator = ReactAgentOrchestrator(llm=model, tools=[], max_iterations=2, config={"termination": {"judge": {"enabled": False}}})
    result = orchestrator.answer("What is the capital of France?", allow_search=False)

    assert result["llm_error"], "the HTTP error must be exposed, not masked as an answer"
    assert str(status) in result["llm_error"] or "Error" in result["llm_error"]
    assert result["answer"].startswith("Agent execution failed")
    assert result["control"]["search_mode"] == "react_agent_error"
    assert orchestrator.llm is model and model.model_name == orchestrator.llm.model_name
