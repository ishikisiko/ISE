"""Contract tests for the discovery agent: it discovers, it never rules."""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List

from evidence.official_domain_graph import (
    EDGE_HOMEPAGE_REPO_BACKLINK,
    EDGE_REGISTRY_HOMEPAGE,
    EDGE_SEARCH_VOTE,
    RelationGraph,
)
from evidence.official_domain_resolver import (
    OfficialDomainResolver,
    Signal,
    resolver_config_from_mapping,
)
from orchestrators.official_domain_discovery_agent import (
    DiscoverySession,
    run_discovery_agent,
)
from search.search import SearchHit


class _Prov:
    def __init__(self, source_id: str, hits: List[SearchHit]) -> None:
        self.source_id = source_id
        self.hits = hits

    def search(self, query: str, num_results: int = 5, **kwargs: Any) -> List[SearchHit]:
        return list(self.hits)


class _ScriptedLLM:
    """Plays back a queue of model turns."""

    def __init__(self, turns: List[str]) -> None:
        self.turns = list(turns)
        self.calls = 0

    def invoke(self, messages: Any) -> Any:
        self.calls += 1
        content = self.turns.pop(0) if self.turns else '{"action": "final", "candidates": []}'

        class _Response:
            pass

        response = _Response()
        response.content = content
        return response


def _resolver() -> OfficialDomainResolver:
    tmp = tempfile.mkdtemp()
    cfg = resolver_config_from_mapping(
        {
            "enabled": True,
            "structured_sources": ("pypi",),
            "graph_probes_enabled": False,
            "pin_shadow_audit": False,
            "cache_path": os.path.join(tmp, "cache.sqlite"),
        }
    )
    resolver = OfficialDomainResolver(cfg, search_clients=[], fetch_client=None)

    def fake_pypi(self: Any, stem: str, label: str) -> Any:
        return Signal(
            kind="pypi_home_page", source="pypi", domain="acme.com", tier="structured"
        ), "https://github.com/acme/widgets"

    resolver._probe_pypi = fake_pypi.__get__(resolver)  # type: ignore[method-assign]
    return resolver


def test_edges_come_from_tool_outputs_not_prose() -> None:
    """The model may *say* anything; only deterministic tool outputs may add
    edges to the graph. A final answer full of claims adds zero edges."""
    llm = _ScriptedLLM(
        [
            '{"action": "tool", "tool": "search", "args": {"query": "acme 官网"}}',
            '{"action": "final", "candidates": ["acme.com"], "official": "acme.com", "confidence": "high"}',
        ]
    )
    graph = run_discovery_agent(
        "acme",
        llm=llm,
        resolver=_resolver(),
        search_clients=[_Prov("brave", [SearchHit(title="Acme", url="https://acme.com/", snippet="")])],
        backlink_checker=lambda page, target, **kw: (None, ""),
    )
    # Only the search tool's deterministic vote edge exists; the prose fields
    # "official"/"confidence" in the final action are ignored entirely.
    assert graph.edges
    assert {edge.kind for edge in graph.edges} == {EDGE_SEARCH_VOTE}
    assert "official" not in graph.to_dict()
    assert "confidence" not in graph.to_dict()


def test_registry_lookup_and_backlink_close_the_loop() -> None:
    llm = _ScriptedLLM(
        [
            '{"action": "tool", "tool": "registry_lookup", "args": {"name": "acme"}}',
            '{"action": "tool", "tool": "check_backlink", "args": {"domain": "acme.com", "target": "github.com/acme/widgets"}}',
            '{"action": "final", "candidates": ["acme.com"]}',
        ]
    )
    graph = run_discovery_agent(
        "acme",
        llm=llm,
        resolver=_resolver(),
        backlink_checker=lambda page, target, **kw: (True, "https://github.com/acme/widgets"),
    )
    kinds = {edge.kind for edge in graph.edges}
    assert EDGE_REGISTRY_HOMEPAGE in kinds
    assert EDGE_HOMEPAGE_REPO_BACKLINK in kinds

    # The graph is rulable by the deterministic boundary: closure -> official.
    resolver = _resolver()
    resolution = resolver.adjudicate("acme", graph)
    assert resolution.is_official and resolution.domain == "acme.com"


def test_backlink_absent_records_contradiction_not_inconclusive() -> None:
    session = DiscoverySession(
        "acme",
        resolver=_resolver(),
        backlink_checker=lambda page, target, **kw: (False, ""),
    )
    result = session.call("check_backlink", {"domain": "acme.com", "target": "github.com/acme/widgets"})
    assert result["result"] == "backlink_absent"
    edge = session.graph.edges[0]
    assert edge.kind == EDGE_HOMEPAGE_REPO_BACKLINK and not edge.verified

    inconclusive = DiscoverySession(
        "acme",
        resolver=_resolver(),
        backlink_checker=lambda page, target, **kw: (None, ""),
    )
    result = inconclusive.call("check_backlink", {"domain": "acme.com", "target": "github.com/acme/widgets"})
    assert result["result"] == "inconclusive"
    assert inconclusive.graph.edges == []  # inconclusive records nothing


def test_unknown_tools_and_unparseable_turns_are_contained() -> None:
    llm = _ScriptedLLM(
        [
            "I think the official site is definitely acme.com!",  # prose, no JSON
            '{"action": "tool", "tool": "judge_official", "args": {"domain": "acme.com"}}',
            '{"action": "final", "candidates": ["acme.com"]}',
        ]
    )
    graph = run_discovery_agent(
        "acme",
        llm=llm,
        resolver=_resolver(),
        backlink_checker=lambda page, target, **kw: (None, ""),
    )
    # No tool ran -> no edges; the session survived prose and a forbidden tool.
    assert graph.edges == []
    assert llm.calls == 3


def test_submit_candidates_validates_hosts() -> None:
    session = DiscoverySession("acme", resolver=_resolver())
    result = session.call(
        "submit_candidates",
        {"candidates": ["acme.com", "not a host", "https://docs.acme.com/pricing", "acme.com"]},
    )
    assert result["accepted"] == ["acme.com", "docs.acme.com"]
    assert session.finished


def test_no_llm_returns_empty_graph() -> None:
    graph = run_discovery_agent("acme", llm=None, resolver=_resolver())
    assert graph.stem == "acme" and graph.edges == []
