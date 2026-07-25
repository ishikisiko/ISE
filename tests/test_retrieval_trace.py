"""Regression coverage for provider-level workflow audit records."""

from __future__ import annotations

from typing import Any

from langchain.langchain_rag import SearchRAGChain
from search.reference_fetch import (
    ReferenceContent,
    ReferenceExtraction,
    ReferenceExtractorRouter,
)
from search.search import CombinedSearchClient, PrioritySearchClient, SearchClient, SearchHit
from utils.workflow_trace import WorkflowTracer


class _RecordedSearchClient(SearchClient):
    def __init__(self, source: str, hits: list[SearchHit], *, fail: bool = False) -> None:
        super().__init__()
        self.source_id = source
        self.display_name = source.title()
        self.hits = hits
        self.fail = fail

    def search(self, query: str, num_results: int = 5, **kwargs: Any) -> list[SearchHit]:
        self._reset_timings()
        if self.fail:
            self._append_timing(
                {"source": self.source_id, "label": self.display_name, "duration_ms": 2.0, "error": "timeout"}
            )
            self._append_call_record(query=query, duration_ms=2.0, error="token=should-not-leak")
            raise RuntimeError("timeout")
        selected = list(self.hits[:num_results])
        self._append_timing({"source": self.source_id, "label": self.display_name, "duration_ms": 1.0})
        self._append_call_record(query=query, duration_ms=1.0, hits=selected)
        return selected


def test_combined_and_priority_clients_keep_concrete_provider_snapshots() -> None:
    first = _RecordedSearchClient(
        "brave",
        [SearchHit("Brave", "https://example.com/brave", "Brave snippet")],
    )
    second = _RecordedSearchClient(
        "tavily",
        [SearchHit("Tavily", "https://example.com/tavily", "Tavily snippet")],
    )
    combined = CombinedSearchClient([first, second])

    combined.search("pricing", num_results=2)
    records = combined.get_last_call_records()
    assert {record["source"] for record in records} == {"brave", "tavily"}
    assert {record["results"][0]["url"] for record in records} == {
        "https://example.com/brave",
        "https://example.com/tavily",
    }

    failing = _RecordedSearchClient("brave", [], fail=True)
    fallback = _RecordedSearchClient(
        "firecrawl",
        [SearchHit("Fallback", "https://example.com/fallback", "Fallback snippet")],
    )
    priority = PrioritySearchClient([failing, fallback])

    assert priority.search("pricing", num_results=2)[0].title == "Fallback"
    records = priority.get_last_call_records()
    assert [(record["source"], record["status"]) for record in records] == [
        ("brave", "error"),
        ("firecrawl", "done"),
    ]
    assert records[1]["fallback"] is True


def test_priority_domain_search_continues_past_non_target_results() -> None:
    first = _RecordedSearchClient(
        "brave",
        [SearchHit("Review", "https://review.example/glm", "third-party pricing")],
    )
    second = _RecordedSearchClient(
        "firecrawl",
        [SearchHit("GLM pricing", "https://open.bigmodel.cn/pricing", "official pricing")],
    )
    priority = PrioritySearchClient([first, second])

    hits = priority.search_for_domains(
        "glm5.2 official pricing site:bigmodel.cn",
        {"bigmodel.cn"},
        num_results=1,
    )

    assert [hit.url for hit in hits] == ["https://open.bigmodel.cn/pricing"]
    records = priority.get_last_call_records()
    assert [record["source"] for record in records] == ["brave", "firecrawl"]
    assert records[1]["fallback"] is True


def test_search_rag_emits_one_safe_trace_step_per_provider_call() -> None:
    client = _RecordedSearchClient(
        "brave",
        [
            SearchHit(
                "Pricing",
                "https://example.com/pricing?token=hidden",
                "Official API pricing details.",
            )
        ],
    )
    tracer = WorkflowTracer()
    chain = SearchRAGChain(llm=object(), search_client=client, data_path=None)

    retrieval = chain._retrieve_evidence(
        "pricing",
        search_query="pricing token=hidden",
        num_search_results=5,
        per_source_limit=5,
        num_retrieved_docs=0,
        enable_search=True,
        enable_local_docs=False,
        freshness=None,
        date_restrict=None,
        timing_recorder=None,
        tracer=tracer,
    )

    event = next(
        event
        for event in tracer.events
        if event["id"] == "search_api_web_search_1" and event["status"] == "done"
    )
    assert event["record_kind"] == "search_results"
    assert event["record_label"] == "搜索结果 · 1"
    assert event["records"] == [
        {
            "title": "Pricing",
            "snippet": "Official API pricing details.",
            "provider": "brave",
            "status": "done",
            "url": "https://example.com/pricing",
        }
    ]
    assert "hidden" not in str(event)
    assert retrieval["search_api_calls"][0]["records"] == event["records"]


def test_reference_router_emits_page_identity_without_page_body() -> None:
    class Extractor:
        source_id = "parallel_extract"

        def extract(self, urls, *, objective=None):
            return ReferenceExtraction(
                provider=self.source_id,
                request_id="extract-123",
                contents=[
                    ReferenceContent(
                        provider=self.source_id,
                        requested_url=urls[0],
                        url=urls[0],
                        title="Pricing page",
                        content="Sensitive page body must not reach the workflow trace.",
                    )
                ],
            )

    tracer = WorkflowTracer()
    result = ReferenceExtractorRouter([Extractor()]).extract(
        "https://example.com/pricing?token=hidden",
        tracer=tracer,
    )

    assert result.trace_records()[0]["url"] == "https://example.com/pricing"
    event = next(event for event in tracer.events if event["id"] == "extract_api_1" and event["status"] == "done")
    assert event["record_kind"] == "extracted_pages"
    assert event["records"][0]["url"] == "https://example.com/pricing"
    assert event["records"][0]["content_chars"] > 0
    assert "Sensitive page body" not in str(event)
    assert "hidden" not in str(event)
