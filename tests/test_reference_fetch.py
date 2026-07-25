from __future__ import annotations

import json

import pytest

from search.reference_fetch import (
    FIRECRAWL_SCRAPE_URL,
    PARALLEL_EXTRACT_URL,
    TAVILY_EXTRACT_URL,
    DirectFetchClient,
    FirecrawlScrapeClient,
    ParallelExtractClient,
    ReferenceContent,
    ReferenceExtraction,
    ReferenceExtractorRouter,
    ReferenceFailure,
    TavilyExtractClient,
    build_reference_extractors,
)


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class DirectFetchResponse:
    def __init__(
        self,
        content: bytes,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}


def test_direct_fetch_extracts_static_html_before_provider_fallback(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return DirectFetchResponse(
            b"""
            <html><head><title>API docs</title></head>
            <body><nav>Navigation</nav><main><h1>API docs</h1><p>Useful content</p></main></body>
            </html>
            """,
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

    monkeypatch.setattr("search.reference_fetch.requests.get", fake_get)
    result = DirectFetchClient(max_chars=100).extract("https://example.com/docs")

    assert captured["url"] == "https://example.com/docs"
    assert captured["allow_redirects"] is False
    assert "Mozilla/5.0" in captured["headers"]["User-Agent"]
    assert result.contents[0].title == "API docs"
    assert result.contents[0].content == "API docs\nUseful content"
    assert result.failures == []


def test_direct_fetch_rejects_unsafe_redirect_before_following_it(monkeypatch):
    requested_urls = []

    def fake_get(url, **kwargs):
        requested_urls.append(url)
        return DirectFetchResponse(
            b"",
            status_code=302,
            headers={"Location": "http://127.0.0.1/internal"},
        )

    monkeypatch.setattr("search.reference_fetch.requests.get", fake_get)
    result = DirectFetchClient().extract("https://example.com/redirect")

    assert requested_urls == ["https://example.com/redirect"]
    assert result.contents == []
    assert result.failures == [
        ReferenceFailure(
            provider="direct_fetch",
            requested_url="https://example.com/redirect",
            error_type="unsafe_redirect",
        )
    ]


def test_parallel_extract_uses_current_endpoint_and_normalizes_content(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(
            {
                "extract_id": "extract-123",
                "session_id": "session-123",
                "results": [
                    {
                        "url": "https://example.com/pricing",
                        "title": "Pricing",
                        "publish_date": "2026-07-01",
                        "excerpts": ["Relevant excerpt"],
                        "full_content": "# Pricing\n\n$1 per unit",
                    }
                ],
                "errors": [],
            }
        )

    monkeypatch.setattr("search.reference_fetch.requests.post", fake_post)
    client = ParallelExtractClient(api_key="parallel-key", full_content=True)
    result = client.extract(
        "https://example.com/pricing",
        objective="Find the API price.",
    )

    assert captured["url"] == PARALLEL_EXTRACT_URL
    assert captured["headers"]["x-api-key"] == "parallel-key"
    assert captured["json"]["urls"] == ["https://example.com/pricing"]
    assert captured["json"]["objective"] == "Find the API price."
    assert captured["json"]["advanced_settings"]["full_content"] is True
    assert result.request_id == "extract-123"
    assert result.contents[0].content == "# Pricing\n\n$1 per unit"
    assert result.contents[0].published_at == "2026-07-01"
    assert result.failures == []


def test_firecrawl_scrape_uses_safe_markdown_request(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(
            {
                "success": True,
                "data": {
                    "markdown": "# API docs\n\nUseful content",
                    "metadata": {
                        "sourceURL": "https://docs.example.com/api",
                        "title": "API docs",
                        "statusCode": 200,
                        "scrapeId": "scrape-123",
                        "opaque": "must-not-be-kept",
                    },
                },
            }
        )

    monkeypatch.setattr("search.reference_fetch.requests.post", fake_post)
    client = FirecrawlScrapeClient(api_key="firecrawl-key", max_age_ms=0)
    result = client.extract("https://docs.example.com/api")

    assert captured["url"] == FIRECRAWL_SCRAPE_URL
    assert captured["headers"]["Authorization"] == "Bearer firecrawl-key"
    assert captured["json"]["formats"] == ["markdown"]
    assert captured["json"]["onlyMainContent"] is True
    assert captured["json"]["onlyCleanContent"] is False
    assert captured["json"]["skipTlsVerification"] is False
    assert captured["json"]["maxAge"] == 0
    assert "headers" not in captured["json"]
    assert "actions" not in captured["json"]
    assert result.contents[0].url == "https://docs.example.com/api"
    assert result.request_id == "scrape-123"
    assert result.contents[0].metadata == {
        "sourceURL": "https://docs.example.com/api",
        "title": "API docs",
        "statusCode": 200,
        "scrapeId": "scrape-123",
    }


def test_tavily_extract_uses_bearer_auth_and_normalizes_results(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(
            {
                "request_id": "req-123",
                "results": [
                    {
                        "url": "https://example.com/pricing?token=secret",
                        "raw_content": "# Pricing\n\n$1 per unit",
                    }
                ],
                "failed_results": [
                    {
                        "url": "https://example.com/blocked",
                        "error": "Extraction failed",
                    }
                ],
                "response_time": 0.5,
            }
        )

    monkeypatch.setattr("search.reference_fetch.requests.post", fake_post)
    client = TavilyExtractClient(
        api_key="tavily-key",
        extract_depth="advanced",
        chunks_per_source=9,
    )
    result = client.extract(
        ["https://example.com/pricing?token=secret", "https://example.com/blocked"],
        objective="Find the API price.",
    )

    assert captured["url"] == TAVILY_EXTRACT_URL
    assert captured["headers"]["Authorization"] == "Bearer tavily-key"
    assert captured["json"]["urls"] == [
        "https://example.com/pricing?token=secret",
        "https://example.com/blocked",
    ]
    assert captured["json"]["extract_depth"] == "advanced"
    assert captured["json"]["format"] == "markdown"
    assert captured["json"]["query"] == "Find the API price."
    assert captured["json"]["chunks_per_source"] == 5
    assert captured["json"]["timeout"] == 45
    assert result.request_id == "req-123"
    assert result.contents[0].content == "# Pricing\n\n$1 per unit"
    assert result.contents[0].requested_url == "https://example.com/pricing"
    assert result.contents[0].metadata == {"request_id": "req-123"}
    assert result.failures == [
        ReferenceFailure(
            provider="tavily_extract",
            requested_url="https://example.com/blocked",
            error_type="Extraction failed",
        )
    ]
    serialized = json.dumps(result, default=lambda value: value.__dict__)
    assert "token=secret" not in serialized
    assert "tavily-key" not in serialized


def test_tavily_extract_marks_unreturned_urls_as_empty_content(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse({"results": [], "failed_results": []})

    monkeypatch.setattr("search.reference_fetch.requests.post", fake_post)
    client = TavilyExtractClient(api_key="tavily-key", timeout=120)
    result = client.extract("https://example.com/missing")

    assert captured["json"]["timeout"] == 60
    assert captured["json"]["extract_depth"] == "basic"
    assert "query" not in captured["json"]
    assert result.contents == []
    assert result.failures == [
        ReferenceFailure(
            provider="tavily_extract",
            requested_url="https://example.com/missing",
            error_type="empty_content",
        )
    ]


def test_build_reference_extractors_accepts_existing_bare_key_fields():
    extractors = build_reference_extractors(
        {
            "parellel2": "parallel-key",
            "firecrawl2": "firecrawl-key",
            "tavily2": "tavily-key",
        }
    )

    assert [extractor.source_id for extractor in extractors] == [
        "direct_fetch",
        "parallel_extract",
        "firecrawl_scrape",
        "tavily_extract",
    ]
    assert extractors[0].source_id == "direct_fetch"
    assert extractors[1].base_url == PARALLEL_EXTRACT_URL
    assert extractors[2].base_url == FIRECRAWL_SCRAPE_URL
    assert extractors[3].base_url == TAVILY_EXTRACT_URL


def test_build_reference_extractors_accepts_structured_provider_settings():
    extractors = build_reference_extractors(
        {
            "parellel2": {
                "api_key": "parallel-key",
                "base_url": "https://parallel.example/extract",
                "timeout": 61,
                "full_content": True,
                "max_chars_total": 1234,
            },
            "firecrawl2": {
                "api_key": "firecrawl-key",
                "base_url": "https://firecrawl.example/scrape",
                "timeout": 62,
                "only_main_content": False,
                "only_clean_content": True,
                "max_age_ms": 0,
            },
            "tavily2": {
                "api_key": "tavily-key",
                "base_url": "https://tavily.example/extract",
                "timeout": 63,
                "extract_depth": "advanced",
                "format": "text",
                "chunks_per_source": 4,
            },
            "orchestration": {
                "directFetch": {
                    "enabled": True,
                    "timeout": 31,
                    "max_chars_per_page": 4321,
                }
            },
        }
    )

    direct, parallel, firecrawl, tavily = extractors
    assert direct.timeout == 31
    assert direct.max_chars == 4321
    assert parallel.base_url == "https://parallel.example/extract"
    assert parallel.timeout == 61
    assert parallel.full_content is True
    assert parallel.max_chars_total == 1234
    assert firecrawl.base_url == "https://firecrawl.example/scrape"
    assert firecrawl.timeout == 62
    assert firecrawl.only_main_content is False
    assert firecrawl.only_clean_content is True
    assert firecrawl.max_age_ms == 0
    assert tavily.base_url == "https://tavily.example/extract"
    assert tavily.timeout == 63
    assert tavily.extract_depth == "advanced"
    assert tavily.format == "text"
    assert tavily.chunks_per_source == 4


def test_build_reference_extractors_respects_nested_direct_fetch_disable():
    extractors = build_reference_extractors(
        {
            "parellel2": "parallel-key",
            "orchestration": {"directFetch": {"enabled": False}},
        }
    )

    assert [extractor.source_id for extractor in extractors] == ["parallel_extract"]


def test_reference_router_uses_direct_fetch_without_calling_extract_api_on_success():
    calls = []

    class DirectFetch:
        source_id = "direct_fetch"

        def extract(self, urls, *, objective=None):
            calls.append(self.source_id)
            return ReferenceExtraction(
                provider=self.source_id,
                contents=[
                    ReferenceContent(
                        provider=self.source_id,
                        requested_url=urls[0],
                        url=urls[0],
                        content="Direct page content",
                    )
                ],
            )

    class ExtractApi:
        source_id = "parallel_extract"

        def extract(self, urls, *, objective=None):
            calls.append(self.source_id)
            raise AssertionError("Extract API must not run after direct-fetch success")

    result = ReferenceExtractorRouter([DirectFetch(), ExtractApi()]).extract(
        "https://example.com",
        objective="Read the page.",
    )

    assert calls == ["direct_fetch"]
    assert [item.provider for item in result.contents] == ["direct_fetch"]


def test_reference_router_uses_extract_api_after_direct_fetch_has_no_content():
    calls = []

    class DirectFetchFailure:
        source_id = "direct_fetch"

        def extract(self, urls, *, objective=None):
            calls.append((self.source_id, list(urls), objective))
            return ReferenceExtraction(
                provider=self.source_id,
                failures=[
                    ReferenceFailure(
                        provider=self.source_id,
                        requested_url=urls[0],
                        error_type="empty_content",
                    )
                ],
            )

    class ExtractApiContent:
        source_id = "parallel_extract"

        def extract(self, urls, *, objective=None):
            calls.append((self.source_id, list(urls), objective))
            return ReferenceExtraction(
                provider=self.source_id,
                contents=[
                    ReferenceContent(
                        provider=self.source_id,
                        requested_url=urls[0],
                        url=urls[0],
                        content="Fetched content",
                    )
                ],
            )

    result = ReferenceExtractorRouter([DirectFetchFailure(), ExtractApiContent()]).extract(
        "https://example.com",
        objective="Read the page.",
    )

    assert [call[0] for call in calls] == ["direct_fetch", "parallel_extract"]
    assert [item.provider for item in result.contents] == ["parallel_extract"]
    assert result.attempts == [
        {
            "provider": "direct_fetch",
            "requested_url": "https://example.com",
            "status": "failed",
            "content_chars": 0,
        },
        {
            "provider": "parallel_extract",
            "requested_url": "https://example.com",
            "status": "success",
            "content_chars": 15,
        },
    ]


def test_reference_router_falls_back_when_direct_fetch_body_is_too_small():
    calls = []

    class DirectFetchShell:
        source_id = "direct_fetch"

        def extract(self, urls, *, objective=None):
            calls.append(self.source_id)
            return ReferenceExtraction(
                provider=self.source_id,
                contents=[
                    ReferenceContent(
                        provider=self.source_id,
                        requested_url=urls[0],
                        url=urls[0],
                        content="x" * 383,
                    )
                ],
            )

    class ExtractApiContent:
        source_id = "tavily_extract"

        def extract(self, urls, *, objective=None):
            calls.append(self.source_id)
            return ReferenceExtraction(
                provider=self.source_id,
                contents=[
                    ReferenceContent(
                        provider=self.source_id,
                        requested_url=urls[0],
                        url=urls[0],
                        content="Extracted pricing table " * 40,
                    )
                ],
            )

    result = ReferenceExtractorRouter(
        [DirectFetchShell(), ExtractApiContent()],
        min_content_chars=600,
    ).extract("https://example.com/pricing")

    assert calls == ["direct_fetch", "tavily_extract"]
    assert result.contents[0].provider == "tavily_extract"
    assert result.attempts[0] == {
        "provider": "direct_fetch",
        "requested_url": "https://example.com/pricing",
        "status": "failed",
        "content_chars": 383,
        "reason": "insufficient_content",
    }


def test_reference_router_uses_extract_api_when_direct_fetch_raises():
    calls = []

    class DirectFetchFailure:
        source_id = "direct_fetch"

        def extract(self, urls, *, objective=None):
            calls.append(self.source_id)
            raise RuntimeError("unexpected fetch failure")

    class ExtractApiContent:
        source_id = "parallel_extract"

        def extract(self, urls, *, objective=None):
            calls.append(self.source_id)
            return ReferenceExtraction(
                provider=self.source_id,
                contents=[
                    ReferenceContent(
                        provider=self.source_id,
                        requested_url=urls[0],
                        url=urls[0],
                        content="Recovered content",
                    )
                ],
            )

    result = ReferenceExtractorRouter([DirectFetchFailure(), ExtractApiContent()]).extract(
        "https://example.com",
    )

    assert calls == ["direct_fetch", "parallel_extract"]
    assert [item.provider for item in result.contents] == ["parallel_extract"]
    assert result.failures == [
        ReferenceFailure(
            provider="direct_fetch",
            requested_url="https://example.com",
            error_type="extractor_error",
        )
    ]


def test_reference_results_never_serialize_api_key():
    result = ReferenceExtraction(
        provider="parallel_extract",
        contents=[
            ReferenceContent(
                provider="parallel_extract",
                requested_url="https://example.com",
                url="https://example.com",
                content="Safe content",
                metadata={"extract_id": "extract-123"},
            )
        ],
    )

    serialized = json.dumps(result, default=lambda value: value.__dict__)
    assert "parallel-key" not in serialized
    assert "Authorization" not in serialized


def test_reference_results_strip_query_values_and_opaque_session_metadata(monkeypatch):
    def fake_post(url, **kwargs):
        return FakeResponse(
            {
                "extract_id": "extract-123",
                "session_id": "opaque-session-id",
                "results": [
                    {
                        "url": "https://example.com/pricing?token=secret",
                        "full_content": "Safe content",
                    }
                ],
            }
        )

    monkeypatch.setattr("search.reference_fetch.requests.post", fake_post)
    result = ParallelExtractClient(api_key="parallel-key").extract(
        "https://example.com/pricing?token=secret"
    )

    serialized = json.dumps(result, default=lambda value: value.__dict__)
    assert "token=secret" not in serialized
    assert "opaque-session-id" not in serialized
    assert result.contents[0].requested_url == "https://example.com/pricing"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:pass@example.com",
        "example.com",
        "http://localhost:8000/admin",
        "http://127.0.0.1:8000/admin",
        "http://[::1]/admin",
        "http://169.254.169.254/latest/meta-data",
    ],
)
def test_reference_extractors_reject_unsafe_or_non_absolute_urls(url):
    client = ParallelExtractClient(api_key="parallel-key")

    with pytest.raises(ValueError):
        client.extract(url)
