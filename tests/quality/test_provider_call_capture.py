"""Q0-02: concrete provider requests reach ``TimingRecorder.tool_calls``."""
from __future__ import annotations

import os
import tempfile
from typing import Any, List

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

from evidence.official_domain_resolver import OfficialDomainResolver, resolver_config_from_mapping
from langchain.langchain_rag import SearchRAGChain
from orchestrators.react_loop_graph import ReactLoopGraphRunner, langgraph_available
from search.reference_fetch import ReferenceContent, ReferenceExtraction
from search.search import SearchClient, SearchHit
from utils.provider_calls import (
    notify_provider_call,
    observe_provider_calls,
    record_provider_requests_from_snapshots,
)
from utils.timing_utils import TimingRecorder

pytestmark = pytest.mark.quality_offline


def test_snapshots_expand_to_one_request_per_provider_call() -> None:
    timing = TimingRecorder(enabled=True)
    count = record_provider_requests_from_snapshots(
        timing,
        [
            {"source": "brave", "status": "done", "duration_ms": 12.5, "result_count": 3, "credits": None},
            {"source": "anysearch", "status": "error", "error": "skipped: provider does not honour the site: operator"},
            {"source": "firecrawl", "status": "error", "error": "timeout", "fallback": True},
            {
                "source": "reference_extract",
                "kind": "extracted_pages",
                "attempts": [
                    {"provider": "direct_fetch", "status": "failed", "reason": "insufficient_content"},
                    {"provider": "firecrawl_scrape", "status": "success", "credits": 1.0},
                    {"provider": "reference_router", "status": "skipped", "reason": "url_exhausted"},
                ],
            },
            {"source": "official_domain_resolution", "kind": "resolved_entities", "records": []},
        ],
    )
    entries = timing.provider_requests()
    assert count == 4
    assert [(e["kind"], e["provider"], e["success"]) for e in entries] == [
        ("search", "brave", True),
        ("search", "firecrawl", False),
        ("extract", "direct_fetch", False),
        ("extract", "firecrawl_scrape", True),
    ]
    assert entries[1]["fallback"] is True
    assert entries[3]["credits"] == 1.0
    assert TimingRecorder(enabled=False).provider_requests() == []


class _RecordedSearchClient(SearchClient):
    def __init__(self, source: str, hits: List[SearchHit]) -> None:
        super().__init__()
        self.source_id = source
        self.display_name = source.title()
        self.hits = hits

    def search(self, query: str, num_results: int = 5, **kwargs: Any) -> List[SearchHit]:
        self._reset_timings()
        selected = list(self.hits[:num_results])
        self._append_timing({"source": self.source_id, "label": self.display_name, "duration_ms": 1.0})
        self._append_call_record(query=query, duration_ms=1.0, hits=selected, credits=0.5)
        return selected


def test_search_rag_chain_records_provider_requests_when_given_a_recorder() -> None:
    client = _RecordedSearchClient("brave", [SearchHit("Pricing", "https://example.com/pricing", "snippet")])
    chain = SearchRAGChain(llm=object(), search_client=client, data_path=None)
    timing = TimingRecorder(enabled=True)
    chain._retrieve_evidence(
        "pricing",
        search_query="pricing",
        num_search_results=5,
        per_source_limit=5,
        num_retrieved_docs=0,
        enable_search=True,
        enable_local_docs=False,
        freshness=None,
        date_restrict=None,
        timing_recorder=timing,
    )
    entries = timing.provider_requests()
    assert (entries[0]["kind"], entries[0]["provider"]) == ("search", "brave")
    assert entries[0]["credits"] == 0.5
    # Official-page extraction behind the search is a real request too.
    assert all(entry["kind"] in {"search", "extract"} for entry in entries)


class _Prov:
    def __init__(self, source_id: str, hits: List[SearchHit], *, fail: bool = False) -> None:
        self.source_id = source_id
        self.hits = hits
        self.fail = fail

    def search(self, query: str, num_results: int = 5, **kwargs: Any) -> List[SearchHit]:
        if self.fail:
            raise RuntimeError("provider down")
        return list(self.hits)


class _SelfProofFetch:
    source_id = "direct_fetch"

    def extract(self, urls: Any, **kwargs: Any) -> ReferenceExtraction:
        extraction = ReferenceExtraction(provider="self_proof")
        extraction.contents.append(
            ReferenceContent(
                provider="self_proof",
                requested_url=urls[0],
                url=urls[0],
                title="Acme Corp",
                content="Acme Corp official site",
            )
        )
        return extraction


def test_resolver_discovery_and_verification_are_observed() -> None:
    cfg = resolver_config_from_mapping(
        {
            "enabled": True,
            "min_signals": 2,
            "structured_sources": (),
            "graph_probes_enabled": False,
            "pin_shadow_audit": False,
            "max_discovery_providers": 8,
            "max_discovery_searches": 1,
            "cache_path": os.path.join(tempfile.mkdtemp(), "cache.sqlite"),
        }
    )
    hits = [SearchHit("Acme", "https://acme.example/", "Acme Corp")]
    resolver = OfficialDomainResolver(
        cfg,
        search_clients=[_Prov("brave", hits), _Prov("tavily", hits, fail=True)],
        fetch_client=_SelfProofFetch(),
    )
    timing = TimingRecorder(enabled=True)
    with observe_provider_calls(timing.record_provider_request):
        resolver.resolve("Acme Corp")
    entries = timing.provider_requests()
    kinds = [(e["kind"], e["provider"], e["success"]) for e in entries]
    assert ("resolver_discovery", "brave", True) in kinds
    assert ("resolver_discovery", "tavily", False) in kinds
    assert any(kind == "resolver_verify" and provider == "direct_fetch" for kind, provider, _ in kinds)

    # Outside the observer nothing is recorded and nothing breaks.
    quiet = TimingRecorder(enabled=True)
    resolver.resolve("Acme Corp")
    notify_provider_call(kind="search", provider="x", duration_ms=1.0, success=True)
    assert quiet.provider_requests() == []


class _FakeSearchTool(BaseTool):
    name: str = "web_search"
    description: str = "fake search"

    def _run(self, query: str, **kwargs: Any) -> str:
        return "[E1] official · https://example.com/a\nsnippet"

    def get_last_search_api_calls(self) -> List[dict]:
        return [
            {"source": "brave", "status": "error", "error": "HTTP 429", "duration_ms": 3.0},
            {"source": "firecrawl", "status": "done", "duration_ms": 30.0, "fallback": True, "result_count": 2},
        ]

    def get_last_evidence_records(self) -> List[dict]:
        return [{"source_type": "web", "source_tier": "unknown", "reference": "https://example.com/a", "title": "A", "content": "snippet", "metadata": {"eid": 1}}]


class _FakeFetchTool(BaseTool):
    name: str = "fetch_url"
    description: str = "fake fetch"

    def _run(self, url: str, objective: str = "", **kwargs: Any) -> str:
        return "[E2] official · https://example.com/a · 已抓全文 900 字"

    def get_last_fetch_outcomes(self) -> List[dict]:
        return [
            {
                "url": "https://example.com/a",
                "status": "success",
                "chars": 900,
                "attempts": [
                    {"provider": "direct_fetch", "status": "failed", "reason": "insufficient_content"},
                    {"provider": "tavily_extract", "status": "success", "credits": 2.0},
                ],
            }
        ]


@pytest.mark.skipif(not langgraph_available(), reason="langgraph not installed")
def test_loop_records_provider_requests_behind_search_and_fetch_tools() -> None:
    from tests.test_react_loop_graph import NativeScriptedChatModel, _tool_call

    replies = [
        _tool_call("web_search", {"query": "acme"}, call_id="c1"),
        _tool_call("fetch_url", {"url": "https://example.com/a", "objective": "read"}, call_id="c2"),
        AIMessage(content="Acme is a company [E1][E2]."),
        AIMessage(content="Acme is a company [E1][E2]."),
    ]
    timing = TimingRecorder(enabled=True)
    runner = ReactLoopGraphRunner(
        llm=NativeScriptedChatModel(replies=replies),
        tools=[_FakeSearchTool(), _FakeFetchTool()],
        max_iterations=4,
        query="What is Acme?",
        timing_recorder=timing,
    )
    runner.run("What is Acme?")
    logical = [(e["tool"], e["kind"]) for e in timing.tool_calls if e.get("kind") == "loop_search_tool"]
    assert ("web_search", "loop_search_tool") in logical
    assert ("fetch_url", "loop_search_tool") in logical
    provider = [(e["kind"], e["provider"], e["success"]) for e in timing.provider_requests()]
    assert provider == [
        ("search", "brave", False),
        ("search", "firecrawl", True),
        ("extract", "direct_fetch", False),
        ("extract", "tavily_extract", True),
    ]
