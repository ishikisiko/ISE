from __future__ import annotations

import pytest
import json

from main import build_search_client
from search.search import (
    AnySearchClient,
    BraveSearchClient,
    BrightDataSERPClient,
    CombinedSearchClient,
    PrioritySearchClient,
    SearchClient,
    SearchHit,
    FirecrawlSearchClient,
    ParallelSearchClient,
    TavilySearchClient,
    apply_search_depth_override,
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
    call_records = client.get_last_call_records()
    assert [(record["slot"], record["status"]) for record in call_records] == [
        ("primary", "error"),
        ("secondary", "done"),
    ]
    assert call_records[1]["result_count"] == 1
    assert call_records[1]["results"][0]["url"] == "https://brave.example/result"


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
        "GOOGLE_API_KEY": "google-key",
        "GOOGLE_CX": "google-cx",
    }

    client = build_search_client(config)

    assert client is not None
    expected = ["brave", "firecrawl", "tavily", "parallel", "brightdata", "google"]
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


def test_apply_search_depth_override_sets_nested_tavily_clients():
    tavily = TavilySearchClient(api_key="tavily-key")
    firecrawl = FirecrawlSearchClient(api_key="firecrawl-key")
    combined = CombinedSearchClient([tavily, firecrawl])

    applied = apply_search_depth_override(combined, "ADVANCED")

    assert applied == "advanced"
    assert tavily.search_depth == "advanced"
    assert firecrawl.search_depth == 3


def test_apply_search_depth_override_ignores_invalid_and_none():
    tavily = TavilySearchClient(api_key="tavily-key", search_depth="basic")

    assert apply_search_depth_override(tavily, "nonsense") is None
    assert apply_search_depth_override(tavily, None) is None
    assert apply_search_depth_override(tavily, "") is None
    # Existing value is untouched when the override is invalid.
    assert tavily.search_depth == "basic"


def test_apply_search_depth_override_noop_without_depth_provider():
    brave = BraveSearchClient(primary_api_key="brave-key")
    assert apply_search_depth_override(brave, "advanced") is None


def test_tavily_search_honors_overridden_depth(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse(json_data={"results": []})

    monkeypatch.setattr("search.search.requests.post", fake_post)
    client = TavilySearchClient(api_key="tavily-key")
    apply_search_depth_override(client, "ultra-fast")
    client.search("query")

    assert captured["json"]["search_depth"] == "ultra-fast"


def test_firecrawl_search_depth_disabled_by_default(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse(json_data={"success": True, "data": {"web": []}})

    monkeypatch.setattr("search.search.requests.post", fake_post)
    FirecrawlSearchClient(api_key="firecrawl-key").search("query")

    assert "scrapeOptions" not in captured["json"]


def test_firecrawl_search_sends_scrape_options_when_depth_configured(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse(json_data={"success": True, "data": {"web": []}})

    monkeypatch.setattr("search.search.requests.post", fake_post)
    client = FirecrawlSearchClient(api_key="firecrawl-key", search_depth="advanced")
    client.search("query")

    assert captured["json"]["scrapeOptions"] == {"search_depth": 3}


def test_apply_search_depth_override_overrides_firecrawl(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse(json_data={"success": True, "data": {"web": []}})

    monkeypatch.setattr("search.search.requests.post", fake_post)
    client = FirecrawlSearchClient(api_key="firecrawl-key")
    assert apply_search_depth_override(client, "fast") == "fast"
    client.search("query")

    assert captured["json"]["scrapeOptions"] == {"search_depth": 1}


# --- Fallback tiers (2 + 3 behind Brave) ------------------------------------


class _StubProvider(SearchClient):
    def __init__(self, source_id: str, hits=None, error: Exception | None = None, *, site_operator: bool = True) -> None:
        super().__init__()
        self.source_id = source_id
        self.display_name = source_id.title()
        self.supports_site_operator = site_operator
        self.hits = list(hits or [])
        self.error = error
        self.calls = 0

    def search(self, query, num_results=5, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.hits)


def _stubs(*ids):
    return [_StubProvider(i) for i in ids]


def _members(client):
    return [getattr(c, "source_id", type(c).__name__) for c in getattr(client, "clients", [client])]


def test_build_fallback_tiers_defaults_to_two_then_three():
    from search.search import build_fallback_tiers

    tiers = build_fallback_tiers(_stubs("firecrawl", "tavily", "parallel", "brightdata", "google"))
    assert len(tiers) == 2
    assert isinstance(tiers[0], CombinedSearchClient) and _members(tiers[0]) == ["firecrawl", "tavily"]
    assert isinstance(tiers[1], CombinedSearchClient) and _members(tiers[1]) == ["parallel", "brightdata", "google"]


def test_build_fallback_tiers_honors_order_sizes_and_leftovers():
    from search.search import build_fallback_tiers

    tiers = build_fallback_tiers(
        _stubs("firecrawl", "tavily", "parallel", "brightdata", "google"),
        batch_sizes=[1, 2],
        order=["tavily", "parallel"],
    )
    # tavily alone (single member is used as-is), then parallel+firecrawl,
    # then the unlisted leftovers keep their relative order in a final tier.
    assert [_members(t) for t in tiers] == [["tavily"], ["parallel", "firecrawl"], ["brightdata", "google"]]
    assert not isinstance(tiers[0], CombinedSearchClient)


def test_fallback_tiers_from_config_reads_search_fallback_block():
    from search.search import fallback_tiers_from_config

    config = {"searchFallback": {"batch_sizes": [3], "order": ["google"]}}
    tiers = fallback_tiers_from_config(config, _stubs("firecrawl", "tavily", "parallel", "google"))
    assert [_members(t) for t in tiers] == [["google", "firecrawl", "tavily"], ["parallel"]]
    # Malformed block falls back to the 2 + 3 default.
    tiers = fallback_tiers_from_config({"searchFallback": "junk"}, _stubs("a", "b", "c", "d", "e"))
    assert [_members(t) for t in tiers] == [["a", "b"], ["c", "d", "e"]]


def test_second_tier_is_not_queried_when_first_tier_has_hits():
    from search.search import build_fallback_tiers

    hit = [SearchHit(title="t", url="https://example.com/", snippet="s")]
    brave = _StubProvider("brave", error=RuntimeError("quota"))
    firecrawl, tavily = _StubProvider("firecrawl", hits=hit), _StubProvider("tavily", hits=hit)
    parallel, brightdata, google = _stubs("parallel", "brightdata", "google")
    tiers = build_fallback_tiers([firecrawl, tavily, parallel, brightdata, google])
    priority = PrioritySearchClient([brave] + tiers)

    hits = priority.search("query", num_results=5)

    assert hits and brave.calls == 1
    assert firecrawl.calls == 1 and tavily.calls == 1
    assert parallel.calls == 0 and brightdata.calls == 0 and google.calls == 0


def test_second_tier_runs_only_after_first_tier_misses():
    from search.search import build_fallback_tiers

    hit = [SearchHit(title="t", url="https://example.com/", snippet="s")]
    brave = _StubProvider("brave")
    firecrawl, tavily = _stubs("firecrawl", "tavily")
    parallel, brightdata, google = _StubProvider("parallel", hits=hit), _StubProvider("brightdata"), _StubProvider("google")
    tiers = build_fallback_tiers([firecrawl, tavily, parallel, brightdata, google])
    priority = PrioritySearchClient([brave] + tiers)

    hits = priority.search("query", num_results=5)

    assert hits
    assert firecrawl.calls == 1 and tavily.calls == 1
    assert parallel.calls == 1 and brightdata.calls == 1 and google.calls == 1


def test_build_search_client_forms_two_fallback_tiers_behind_brave():
    config = {
        "braveSearch": {"primary_api_key": "primary-key", "usage_log_path": "runtime/test_brave_usage.jsonl"},
        "brightDataSearch": {"api_token": "bright-token", "zone": "serp_api1"},
        "firecrawlSearch": {"api_key": "firecrawl-key"},
        "tavilySearch": {"api_key": "tavily-key"},
        "parallelSearch": {"api_key": "parallel-key"},
        "GOOGLE_API_KEY": "google-key",
        "GOOGLE_CX": "google-cx",
    }
    client = build_search_client(config)
    assert isinstance(client, PrioritySearchClient)
    assert [_members(c) for c in client.clients] == [
        ["brave"],
        ["firecrawl", "tavily"],
        ["parallel", "brightdata", "google"],
    ]
    # Metadata still lists every leaf provider.
    assert client.active_sources == ["brave", "firecrawl", "tavily", "parallel", "brightdata", "google"]


def test_create_search_tool_forms_the_same_tiers():
    from langchain.langchain_tools import create_search_tool_from_config

    config = {
        "braveSearch": {"primary_api_key": "primary-key", "usage_log_path": "runtime/test_brave_usage.jsonl"},
        "firecrawlSearch": {"api_key": "firecrawl-key"},
        "tavilySearch": {"api_key": "tavily-key"},
        "parallelSearch": {"api_key": "parallel-key"},
        "searchFallback": {"batch_sizes": [1]},
    }
    tool = create_search_tool_from_config(config)
    client = tool.search_client
    assert isinstance(client, PrioritySearchClient)
    assert [_members(c) for c in client.clients] == [["brave"], ["firecrawl"], ["tavily", "parallel"]]


# --- AnySearch --------------------------------------------------------------


def test_anysearch_posts_query_and_parses_envelope(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(
            json_data={
                "code": 0,
                "message": "ok",
                "request_id": "r1",
                "data": {
                    "results": [
                        {
                            "title": "Paris - Wikipedia",
                            "url": "https://en.wikipedia.org/wiki/Paris",
                            "snippet": "Paris is the capital of France.",
                            "content": "Paris is the capital of France. Longer body.",
                        },
                        {"title": "No snippet", "url": "https://example.com/x", "snippet": "", "content": "Body only"},
                    ],
                    "metadata": {"total_results": 2, "search_time_ms": 100},
                },
            }
        )

    monkeypatch.setattr("search.search.requests.post", fake_post)
    hits = AnySearchClient(api_key="anysearch-key").search("capital of France", num_results=3)

    assert captured["url"] == "https://api.anysearch.com/v1/search"
    assert captured["headers"]["Authorization"] == "Bearer anysearch-key"
    assert captured["json"] == {"query": "capital of France", "max_results": 3}
    assert [hit.url for hit in hits] == ["https://en.wikipedia.org/wiki/Paris", "https://example.com/x"]
    assert hits[0].snippet == "Paris is the capital of France."
    assert hits[1].snippet == "Body only"  # falls back to content


def test_anysearch_nonzero_code_is_an_error(monkeypatch):
    monkeypatch.setattr(
        "search.search.requests.post",
        lambda url, **kwargs: FakeResponse(json_data={"code": 40101, "message": "invalid api key", "data": None}),
    )
    client = AnySearchClient(api_key="bad")
    with pytest.raises(RuntimeError, match="invalid api key"):
        client.search("query")
    records = client.get_last_call_records()
    assert records and records[-1].get("error")


def test_build_search_client_places_anysearch_by_configured_order():
    config = {
        "braveSearch": {"primary_api_key": "primary-key", "usage_log_path": "runtime/test_brave_usage.jsonl"},
        "firecrawlSearch": {"api_key": "firecrawl-key"},
        "tavilySearch": {"api_key": "tavily-key"},
        "anySearch": {"api_key": "anysearch-key"},
        "parallelSearch": {"api_key": "parallel-key"},
        "searchFallback": {"batch_sizes": [2, 3], "order": ["anysearch", "tavily", "firecrawl", "parallel"]},
    }
    client = build_search_client(config)
    assert isinstance(client, PrioritySearchClient)
    assert [_members(c) for c in client.clients] == [["brave"], ["anysearch", "tavily"], ["firecrawl", "parallel"]]
    assert "anysearch" in client.configured_sources
    # Explicit source selection also knows the provider.
    only = build_search_client(config, sources=["anysearch"])
    assert _members(only) == ["anysearch"]
    assert getattr(only, "missing_requested_sources") == []


# --- Configurable primary + site: routing -----------------------------------


def _all_providers_config(**extra):
    config = {
        "braveSearch": {"primary_api_key": "primary-key", "usage_log_path": "runtime/test_brave_usage.jsonl"},
        "firecrawlSearch": {"api_key": "firecrawl-key"},
        "tavilySearch": {"api_key": "tavily-key"},
        "anySearch": {"api_key": "anysearch-key"},
        "parallelSearch": {"api_key": "parallel-key"},
    }
    config.update(extra)
    return config


def test_configured_primary_leads_and_brave_becomes_first_fallback():
    config = _all_providers_config(
        searchFallback={"primary": "anysearch", "batch_sizes": [1, 2], "order": ["brave", "tavily", "firecrawl", "parallel"]}
    )
    client = build_search_client(config)
    assert isinstance(client, PrioritySearchClient)
    assert [_members(c) for c in client.clients] == [["anysearch"], ["brave"], ["tavily", "firecrawl"], ["parallel"]]

    from langchain.langchain_tools import create_search_tool_from_config

    tool_client = create_search_tool_from_config(config).search_client
    assert [_members(c) for c in tool_client.clients] == [["anysearch"], ["brave"], ["tavily", "firecrawl"], ["parallel"]]


def test_unavailable_primary_falls_back_to_brave():
    config = _all_providers_config(searchFallback={"primary": "anysearch"})
    config.pop("anySearch")
    client = build_search_client(config)
    assert isinstance(client, PrioritySearchClient)
    assert _members(client.clients[0]) == ["brave"]


def test_explicit_subset_without_primary_keeps_combined_fanout():
    config = _all_providers_config(searchFallback={"primary": "anysearch"})
    client = build_search_client(config, sources=["tavily", "firecrawl"])
    assert isinstance(client, CombinedSearchClient)
    assert sorted(_members(client)) == ["firecrawl", "tavily"]


def test_site_operator_query_skips_incapable_primary():
    hit = [SearchHit(title="UN", url="https://www.un.org/en/observances/mens-day", snippet="s")]
    anysearch = _StubProvider("anysearch", hits=hit, site_operator=False)
    brave = _StubProvider("brave", hits=hit)
    priority = PrioritySearchClient([anysearch, brave])

    hits = priority.search("site:un.org International Men's Day", num_results=5)

    assert hits and anysearch.calls == 0 and brave.calls == 1
    records = priority.get_last_call_records()
    assert records[0]["source"] == "anysearch" and "site:" in records[0]["error"]

    # Plain queries still go to the primary first.
    priority.search("International Men's Day", num_results=5)
    assert anysearch.calls == 1 and brave.calls == 1


def test_combined_tier_skips_site_incapable_members():
    hit = [SearchHit(title="t", url="https://example.com/", snippet="s")]
    anysearch = _StubProvider("anysearch", hits=hit, site_operator=False)
    tavily = _StubProvider("tavily", hits=hit)
    combined = CombinedSearchClient([anysearch, tavily])

    assert combined.search("site:example.com docs", num_results=5)
    assert anysearch.calls == 0 and tavily.calls == 1

    only = CombinedSearchClient([anysearch])
    assert only.search("site:example.com docs", num_results=5) == []
    assert anysearch.calls == 0
