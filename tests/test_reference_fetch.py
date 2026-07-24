from __future__ import annotations

import json

import pytest

from search.reference_fetch import (
    FIRECRAWL_SCRAPE_URL,
    PARALLEL_EXTRACT_URL,
    FirecrawlScrapeClient,
    ParallelExtractClient,
    ReferenceContent,
    ReferenceExtraction,
    ReferenceExtractorRouter,
    ReferenceFailure,
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


def test_build_reference_extractors_accepts_existing_bare_key_fields():
    extractors = build_reference_extractors(
        {
            "parellel2": "parallel-key",
            "firecrawl2": "firecrawl-key",
        }
    )

    assert [extractor.source_id for extractor in extractors] == [
        "parallel_extract",
        "firecrawl_scrape",
    ]
    assert extractors[0].base_url == PARALLEL_EXTRACT_URL
    assert extractors[1].base_url == FIRECRAWL_SCRAPE_URL


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
        }
    )

    parallel, firecrawl = extractors
    assert parallel.base_url == "https://parallel.example/extract"
    assert parallel.timeout == 61
    assert parallel.full_content is True
    assert parallel.max_chars_total == 1234
    assert firecrawl.base_url == "https://firecrawl.example/scrape"
    assert firecrawl.timeout == 62
    assert firecrawl.only_main_content is False
    assert firecrawl.only_clean_content is True
    assert firecrawl.max_age_ms == 0


def test_reference_router_only_uses_fallback_after_primary_has_no_content():
    calls = []

    class EmptyExtractor:
        source_id = "primary"

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

    class ContentExtractor:
        source_id = "fallback"

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

    result = ReferenceExtractorRouter([EmptyExtractor(), ContentExtractor()]).extract(
        "https://example.com",
        objective="Read the page.",
    )

    assert [call[0] for call in calls] == ["primary", "fallback"]
    assert [item.provider for item in result.contents] == ["fallback"]
    assert result.attempts == [
        {
            "provider": "primary",
            "requested_url": "https://example.com",
            "status": "failed",
            "content_chars": 0,
        },
        {
            "provider": "fallback",
            "requested_url": "https://example.com",
            "status": "success",
            "content_chars": 15,
        },
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
