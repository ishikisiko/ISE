"""``TransportObserver`` classifies non-LLM requests without touching them."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import requests

from tests.study_transport import TransportObserver, classify_host, path_prefix

pytestmark = pytest.mark.quality_offline


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def test_classify_host_prefers_longest_suffix() -> None:
    assert classify_host("api.search.brave.com") == "brave"
    assert classify_host("maps.googleapis.com") == "google_maps"
    assert classify_host("www.googleapis.com") == "google"
    assert classify_host("www.wikidata.org") == "resolver_wikidata"
    assert classify_host("example.org:8443") == "other:example.org"
    assert path_prefix("/v1/search") == "/v1/search"
    assert path_prefix("/res/v1/web/search") == "/res/v1"


def test_observer_records_external_requests_and_llm_usage(tmp_path, monkeypatch) -> None:
    sent: list[tuple[str, str]] = []

    def fake_send(session, request, **kwargs):
        sent.append((request.method, request.url))
        if request.url.endswith("/chat/completions"):
            return _FakeResponse(200, {"usage": {"prompt_tokens": 7, "completion_tokens": 3}})
        if "tavily" in request.url:
            return _FakeResponse(500)
        return _FakeResponse(200, {"web": {"results": []}})

    monkeypatch.setattr(requests.Session, "send", fake_send)
    observer = TransportObserver(tmp_path / "transport.jsonl")
    observer.install()
    try:
        session = requests.Session()
        for method, url in (
            ("GET", "https://api.search.brave.com/res/v1/web/search?q=x&key=hidden"),
            ("POST", "https://api.tavily.com/search"),
            ("POST", "https://opencode.ai/zen/go/v1/chat/completions"),
            ("GET", "https://www.wikidata.org/w/api.php?search=x"),
        ):
            request = SimpleNamespace(method=method, url=url, body=b"{}")
            requests.Session.send(session, request)
    finally:
        observer.uninstall()

    assert requests.Session.send is fake_send
    assert len(sent) == 4
    summary = observer.summary()
    assert summary["transport_requests"] == 1
    assert summary["transport_total_tokens"] == 10
    assert summary["external_requests_total"] == 3
    assert summary["external_requests_by_provider"] == {
        "brave": 1,
        "resolver_wikidata": 1,
        "tavily": 1,
    }
    assert summary["external_errors_by_provider"] == {"tavily": 1}

    persisted = [json.loads(line) for line in (tmp_path / "transport.jsonl").read_text().splitlines()]
    external = [record for record in persisted if record.get("kind") == "external"]
    assert all("?" not in record["path_prefix"] for record in external)
    llm, external_loaded = TransportObserver.load_records(tmp_path / "transport.jsonl")
    assert len(llm) == 1 and len(external_loaded) == 3
