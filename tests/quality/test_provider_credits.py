"""Credits accounting for metered providers (Q0-03)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from search.reference_fetch import (
    FirecrawlScrapeClient,
    ReferenceExtractorRouter,
    TavilyExtractClient,
    build_reference_extractors,
)
from search.search import FirecrawlSearchClient, ParallelSearchClient, TavilySearchClient
from tests.quality.provider_usage import check_daily_limits, load_usage_rows, summarize_usage
from utils.provider_usage import ProviderUsageRecorder, extract_credits, resolve_credits
from utils.retrieval_trace import search_call_snapshot

pytestmark = pytest.mark.quality_offline


class FakeResponse:
    def __init__(self, payload, *, status_code: int = 200, headers=None) -> None:
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def test_extract_credits_resolution_order() -> None:
    assert extract_credits({"creditsUsed": 3}) == 3.0
    assert extract_credits({"data": {"metadata": {"creditsUsed": 2}}}) == 2.0
    assert extract_credits({"usage": {"credits": "1.5"}}) == 1.5
    assert extract_credits({}, {"X-Credits-Used": "4"}) == 4.0
    assert extract_credits({}, {}, fallback=0.5) == 0.5
    assert extract_credits({}, {}, fallback=None) is None
    assert extract_credits({"creditsUsed": -1}, {}, fallback=None) is None
    assert resolve_credits({"creditsUsed": 3}, {"x-credits": 9}, 1) == (3.0, "body")
    assert resolve_credits({}, {"x-credits": 9}, 1) == (9.0, "header")
    assert resolve_credits({}, {}, 1) == (1.0, "config")
    assert resolve_credits({}, {}, None) == (None, None)


def test_search_client_credits_from_body_header_and_config(monkeypatch, tmp_path) -> None:
    recorder = ProviderUsageRecorder(str(tmp_path / "usage"))
    body = {"success": True, "creditsUsed": 2, "data": {"web": [{"title": "t", "url": "https://example.com/a", "description": "d"}]}}
    monkeypatch.setattr("search.search.requests.post", lambda url, **kwargs: FakeResponse(body))
    firecrawl = FirecrawlSearchClient(api_key="k", credits_per_request=9, usage_recorder=recorder)
    firecrawl.search("query")
    record = firecrawl.get_last_call_records()[-1]
    assert record["credits"] == 2.0
    assert search_call_snapshot(record)["credits"] == 2.0

    monkeypatch.setattr(
        "search.search.requests.post",
        lambda url, **kwargs: FakeResponse({"results": []}, headers={"x-credits-used": "1"}),
    )
    tavily = TavilySearchClient(api_key="k", credits_per_request=9, usage_recorder=recorder)
    tavily.search("query")
    assert tavily.get_last_call_records()[-1]["credits"] == 1.0

    monkeypatch.setattr("search.search.requests.post", lambda url, **kwargs: FakeResponse({"results": []}))
    parallel = ParallelSearchClient(api_key="k", credits_per_request=0.25, usage_recorder=recorder)
    parallel.search("query")
    assert parallel.get_last_call_records()[-1]["credits"] == 0.25

    unknown = ParallelSearchClient(api_key="k")
    unknown.search("query")
    assert "credits" not in unknown.get_last_call_records()[-1]

    # Failed requests are recorded with unknown credits, never a fabricated value.
    monkeypatch.setattr("search.search.requests.post", lambda url, **kwargs: FakeResponse({}, status_code=500))
    with pytest.raises(RuntimeError):
        FirecrawlSearchClient(api_key="k", credits_per_request=1, usage_recorder=recorder).search("q")

    rows = load_usage_rows(str(tmp_path / "usage"), brave_log=None)
    by_provider = {}
    for row in rows:
        by_provider.setdefault(row["provider"], []).append(row)
    assert [row["credits"] for row in by_provider["firecrawl"]] == [2.0, None]
    assert by_provider["firecrawl"][0]["credits_source"] == "body"
    assert by_provider["firecrawl"][1]["success"] is False
    assert by_provider["tavily"][0]["credits_source"] == "header"
    assert by_provider["parallel"][0]["credits_source"] == "config"
    assert all(row["kind"] == "search" for row in rows)
    assert all("query_hash" in row for row in rows)


def test_extractors_record_credits_and_router_attaches_them(monkeypatch, tmp_path) -> None:
    recorder = ProviderUsageRecorder(str(tmp_path / "usage"))
    body = {
        "success": True,
        "data": {"markdown": "x" * 700, "metadata": {"sourceURL": "https://docs.example.com/a", "creditsUsed": 5}},
    }
    monkeypatch.setattr("search.reference_fetch.requests.post", lambda url, **kwargs: FakeResponse(body))
    firecrawl = FirecrawlScrapeClient(api_key="k", usage_recorder=recorder)
    router = ReferenceExtractorRouter([firecrawl], min_content_chars=100)
    extraction = router.extract(["https://docs.example.com/a"])
    assert extraction.attempts[0]["credits"] == 5.0
    assert firecrawl.get_last_timings()[-1]["credits"] == 5.0

    monkeypatch.setattr(
        "search.reference_fetch.requests.post",
        lambda url, **kwargs: FakeResponse({"results": [{"url": "https://e.com/p", "raw_content": "y" * 700}], "failed_results": []}),
    )
    tavily = TavilyExtractClient(api_key="k", credits_per_request=2, usage_recorder=recorder)
    tavily.extract(["https://e.com/p"])
    assert tavily.get_last_timings()[-1]["credits"] == 2.0

    rows = load_usage_rows(str(tmp_path / "usage"), brave_log=None)
    assert {row["kind"] for row in rows} == {"extract"}
    assert sorted(row["provider"] for row in rows) == ["firecrawl_scrape", "tavily_extract"]


def test_build_reference_extractors_passes_credit_config() -> None:
    extractors = build_reference_extractors(
        {
            "firecrawl2": {"api_key": "k", "credits_per_request": 1.5},
            "providerUsage": {"dir": "runtime/test_provider_usage"},
        }
    )
    firecrawl = next(item for item in extractors if item.source_id == "firecrawl_scrape")
    assert firecrawl.credits_per_request == 1.5
    assert firecrawl.usage_recorder.directory == "runtime/test_provider_usage"


def test_summarize_usage_by_day_and_limits() -> None:
    rows = [
        {"timestamp": "2026-09-08T10:00:00Z", "provider": "firecrawl", "kind": "search", "success": True, "credits": 2},
        {"timestamp": "2026-09-08T11:00:00Z", "provider": "firecrawl", "kind": "extract", "success": False, "credits": None, "error": "boom"},
        {"timestamp": "2026-09-09T09:00:00Z", "provider": "tavily", "kind": "search", "success": True, "credits": 1},
        {"timestamp": "2026-09-09T09:30:00Z", "provider": "brave", "slot": "primary", "success": True},
    ]
    summary = summarize_usage(rows)
    assert summary["days"]["2026-09-08"]["firecrawl"] == {
        "requests": 2,
        "errors": 1,
        "credits_known": 2.0,
        "credits_known_requests": 1,
        "credits_unknown_requests": 1,
        "by_kind": {"search": 1, "extract": 1},
    }
    assert summary["days"]["2026-09-09"]["brave"]["credits_unknown_requests"] == 1
    assert summary["totals"]["firecrawl"]["requests"] == 2

    only_today = summarize_usage(rows, days=1, now=datetime(2026, 9, 9, 12, tzinfo=timezone.utc))
    assert list(only_today["days"]) == ["2026-09-09"]
    single = summarize_usage(rows, date="2026-09-08")
    assert list(single["days"]) == ["2026-09-08"]
    assert check_daily_limits(summary, {"firecrawl": 1}) == [
        {"day": "2026-09-08", "provider": "firecrawl", "credits": 2.0, "limit": 1.0}
    ]
    assert check_daily_limits(summary, {"firecrawl": None, "tavily": 5}) == []
