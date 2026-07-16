from __future__ import annotations

import json

from main import build_search_client
from search.search import (
    BraveSearchClient,
    BrightDataSERPClient,
    FirecrawlSearchClient,
    ParallelSearchClient,
    TavilySearchClient,
)
from server import app


class FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data=None, text: str = "", headers=None) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._json_data is None:
            raise ValueError("No JSON payload")
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_brightdata_search_normalizes_results(monkeypatch):
    html = """
    <html>
      <body>
        <a href="/url?q=https://example.com/article&sa=U">
          <h3>Example Result</h3>
          <div>Example snippet text</div>
        </a>
      </body>
    </html>
    """

    def fake_post(*args, **kwargs):
        return FakeResponse(status_code=200, text=html, headers={"Content-Type": "text/html"})

    monkeypatch.setattr("search.search.requests.post", fake_post)

    client = BrightDataSERPClient(api_token="token", zone="serp_api1")
    hits = client.search("pizza", num_results=5)

    assert len(hits) == 1
    assert hits[0].title == "Example Result"
    assert hits[0].url == "https://example.com/article"
    assert "Example snippet text" in hits[0].snippet


def test_brave_search_falls_back_to_secondary_and_records_usage(monkeypatch, tmp_path):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(headers["X-Subscription-Token"])
        if len(calls) == 1:
            return FakeResponse(status_code=429, json_data={"error": "rate_limited"}, headers={"Content-Type": "application/json"})
        return FakeResponse(
            status_code=200,
            json_data={
                "web": {
                    "results": [
                        {
                            "title": "Brave Result",
                            "url": "https://brave.example/result",
                            "description": "Returned by secondary key",
                        }
                    ]
                }
            },
            headers={"Content-Type": "application/json"},
        )

    monkeypatch.setattr("search.search.requests.get", fake_get)

    log_path = tmp_path / "brave_usage.jsonl"
    client = BraveSearchClient(
        primary_api_key="primary-key",
        secondary_api_key="secondary-key",
        usage_log_path=str(log_path),
    )

    hits = client.search("latest ai news", num_results=5)

    assert [hit.title for hit in hits] == ["Brave Result"]
    assert calls == ["primary-key", "secondary-key"]

    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [entry["slot"] for entry in lines] == ["primary", "secondary"]
    assert lines[0]["success"] is False
    assert lines[1]["success"] is True
    assert lines[1]["fallback_used"] is True


def test_firecrawl_search_uses_v2_web_results(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(
            json_data={
                "success": True,
                "data": {
                    "web": [
                        {
                            "title": "Firecrawl Result",
                            "url": "https://example.com/firecrawl",
                            "description": "Firecrawl snippet",
                        }
                    ]
                },
            }
        )

    monkeypatch.setattr("search.search.requests.post", fake_post)
    hits = FirecrawlSearchClient(api_key="firecrawl-key").search("query")

    assert captured["url"] == "https://api.firecrawl.dev/v2/search"
    assert captured["headers"]["Authorization"] == "Bearer firecrawl-key"
    assert captured["json"]["sources"] == ["web"]
    assert [hit.title for hit in hits] == ["Firecrawl Result"]


def test_tavily_search_maps_freshness_and_results(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(
            json_data={
                "results": [
                    {
                        "title": "Tavily Result",
                        "url": "https://example.com/tavily",
                        "content": "Tavily snippet",
                    }
                ]
            }
        )

    monkeypatch.setattr("search.search.requests.post", fake_post)
    hits = TavilySearchClient(api_key="tavily-key").search("query", freshness="w1")

    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["headers"]["Authorization"] == "Bearer tavily-key"
    assert captured["json"]["time_range"] == "week"
    assert [hit.snippet for hit in hits] == ["Tavily snippet"]


def test_parallel_search_uses_beta_endpoint_and_excerpts(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(
            json_data={
                "results": [
                    {
                        "title": "Parallel Result",
                        "url": "https://example.com/parallel",
                        "excerpts": ["First excerpt.", "Second excerpt."],
                    }
                ]
            }
        )

    monkeypatch.setattr("search.search.requests.post", fake_post)
    hits = ParallelSearchClient(api_key="parallel-key").search("query")

    assert captured["url"] == "https://api.parallel.ai/v1beta/search"
    assert captured["headers"]["x-api-key"] == "parallel-key"
    assert captured["json"]["mode"] == "fast"
    assert hits[0].snippet == "First excerpt. Second excerpt."


def test_build_search_client_prefers_brave_metadata():
    config = {
        "braveSearch": {
            "primary_api_key": "primary-key",
            "secondary_api_key": "secondary-key",
            "usage_log_path": "runtime/test_brave_usage.jsonl",
            "rps": 1,
            "monthly_limit": 2000,
        },
        "brightDataSearch": {
            "api_token": "bright-token",
            "zone": "serp_api1",
        },
        "firecrawlSearch": {"api_key": "firecrawl-key"},
        "tavilySearch": {"api_key": "tavily-key"},
        "parallelSearch": {"api_key": "parallel-key"},
        "YOU_API_KEY": "you-key",
        "GOOGLE_API_KEY": "google-key",
        "GOOGLE_CX": "google-cx",
    }

    client = build_search_client(config)

    assert client is not None
    expected = ["brave", "firecrawl", "tavily", "parallel", "brightdata", "you", "google"]
    assert getattr(client, "requested_sources") == expected
    assert getattr(client, "active_sources") == expected
    assert getattr(client, "configured_sources") == expected


def test_api_rejects_legacy_serp_source():
    app.config["TESTING"] = True
    with app.test_client() as client:
        response = client.post(
            "/api/answer",
            json={"query": "hello", "search": "on", "search_sources": ["serp"]},
        )

    assert response.status_code == 400
    payload = response.get_json()
    assert "Unsupported search source" in payload["error"]
