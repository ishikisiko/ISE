"""Focused coverage for the discover -> verify -> cache official-domain resolver."""

from __future__ import annotations

import tempfile
import os
from typing import Any, Dict, List

import pytest

from evidence.official_domain_graph import (
    EDGE_CT_AGE,
    EDGE_FACADE,
    EDGE_HOMEPAGE_REPO_BACKLINK,
    EDGE_REDIRECT_INTO,
    EDGE_REGISTRY_HOMEPAGE,
    EDGE_SEARCH_VOTE,
    EDGE_WIKIDATA_P856,
    GraphProbers,
    RelationEdge,
    RelationGraph,
    decide_graph,
)
from evidence.official_domain_resolver import (
    HostPattern,
    OfficialDomainResolver,
    Resolution,
    Signal,
    host_matches,
    is_subdomain_of,
    resolver_config_from_mapping,
)
from evidence.source_tiering import (
    classify_web_source_tier,
    normalize_entity_stem,
    official_entity_for_url,
)
from evidence.source_layer import WebEvidenceSource
from search.reference_fetch import ReferenceExtraction
from search.search import SearchHit


# --- Test doubles ----------------------------------------------------------


class _Prov:
    """A minimal search provider double."""

    def __init__(self, source_id: str, hits: List[SearchHit]) -> None:
        self.source_id = source_id
        self.hits = hits
        self.calls = 0

    def search(self, query: str, num_results: int = 5, **kwargs: Any) -> List[SearchHit]:
        self.calls += 1
        return list(self.hits)


class _NoFetch:
    def extract(self, urls: Any, **kwargs: Any) -> ReferenceExtraction:
        return ReferenceExtraction(provider="nofetch")


class _SelfProofFetch:
    """Returns one successful extraction whose final domain matches a target."""

    def __init__(self, final_url: str, title: str, content: str = "") -> None:
        self.final_url = final_url
        self.title = title
        self.content = content

    def extract(self, urls: Any, **kwargs: Any) -> ReferenceExtraction:
        from search.reference_fetch import ReferenceContent

        extraction = ReferenceExtraction(provider="self_proof")
        extraction.contents.append(
            ReferenceContent(
                provider="self_proof",
                requested_url=self.final_url,
                url=self.final_url,
                title=self.title,
                content=self.content or self.title,
            )
        )
        return extraction


def _cfg(**overrides: Any) -> Any:
    base: Dict[str, Any] = {
        "enabled": True,
        "min_signals": 2,
        "structured_sources": (),
        # Hermetic by default: no real network probes, no background audits.
        "graph_probes_enabled": False,
        "pin_shadow_audit": False,
    }
    base.update(overrides)
    tmp = tempfile.mkdtemp()
    base.setdefault("cache_path", os.path.join(tmp, "cache.sqlite"))
    return resolver_config_from_mapping(base)


def _ct_probers(*, verified: bool = True) -> GraphProbers:
    """Prober bundle with an injectable CT-log age verdict, rest disabled."""
    probers = GraphProbers.disabled()

    def ct_age(host: str, **kwargs: Any) -> List[RelationEdge]:
        return [
            RelationEdge(
                kind=EDGE_CT_AGE,
                subject="crt.sh",
                object=host,
                detail="earliest CT entry 400d ago" if verified else "earliest CT entry 3d ago",
                verified=verified,
                observed_at=1000.0,
            )
        ]

    probers.ct_age = ct_age
    return probers


# --- Acceptance rule -------------------------------------------------------


def test_two_independent_providers_alone_are_candidate() -> None:
    """Cross-provider voting is demoted to a weak signal: it measures SEO
    rank, and with few providers ``min_signals`` degenerates into "everyone
    must agree". Votes alone can never rule official."""
    p1 = _Prov("brave", [SearchHit(title="Acme", url="https://acme.com/", snippet="")])
    p2 = _Prov("google", [SearchHit(title="Acme", url="https://acme.com/x", snippet="")])
    r = OfficialDomainResolver(_cfg(), search_clients=[p1, p2], fetch_client=_NoFetch())
    res = r.resolve("acme")
    assert res.confidence == "candidate" and res.domain == "acme.com"
    assert {s.source for s in res.signals} == {"brave", "google"}


def test_two_providers_plus_ct_age_yield_official() -> None:
    """Independent votes plus a CT-log-verified domain age cross the bar:
    squat domains are overwhelmingly young, so age is the costly-to-fake half."""
    p1 = _Prov("brave", [SearchHit(title="Acme", url="https://acme.com/", snippet="")])
    p2 = _Prov("google", [SearchHit(title="Acme", url="https://acme.com/x", snippet="")])
    r = OfficialDomainResolver(
        _cfg(),
        search_clients=[p1, p2],
        fetch_client=_NoFetch(),
        probers=_ct_probers(),
    )
    res = r.resolve("acme")
    assert res.is_official and res.domain == "acme.com"


def test_single_provider_alone_is_candidate() -> None:
    p1 = _Prov("brave", [SearchHit(title="Acme", url="https://acme.com/", snippet="")])
    r = OfficialDomainResolver(_cfg(), search_clients=[p1], fetch_client=_NoFetch())
    res = r.resolve("acme")
    assert res.confidence == "candidate"


def test_cached_conventional_service_host_uses_registrable_domain() -> None:
    provider = _Prov(
        "brave",
        [SearchHit(title="Kimi", url="https://www.kimi.com/", snippet="Kimi")],
    )
    resolver = OfficialDomainResolver(
        _cfg(max_discovery_searches=1),
        search_clients=[provider],
        fetch_client=_NoFetch(),
    )

    result = resolver.resolve("kimik2.7code")

    assert result.confidence == "candidate"
    assert result.domain == "kimi.com"
    assert result.resolved_domains == ["kimi.com"]
    cached = resolver.resolve("kimik2.7code")
    assert cached.domain == "kimi.com"
    assert provider.calls == 1


def test_single_provider_plus_self_proof_is_candidate() -> None:
    """A single search provider plus a self-proof is only ONE independent
    source. Self-proof verifies the candidate the provider surfaced, so it is
    not independent of B-tier and must not help cross the threshold. The
    result stays at ``candidate`` rather than being promoted to ``official``
    (which would let a typosquat self-proof its way to ``official``)."""
    p1 = _Prov("brave", [SearchHit(title="Acme", url="https://acme.com/", snippet="")])
    fetch = _SelfProofFetch("https://acme.com/", "Acme - Home", "Acme official site")
    r = OfficialDomainResolver(_cfg(), search_clients=[p1], fetch_client=fetch)
    res = r.resolve("acme")
    assert res.confidence == "candidate"
    assert res.domain == "acme.com"
    tiers = {s.tier for s in res.signals}
    assert "search_vote" in tiers and "self_proof" in tiers


def test_one_provider_duplicate_votes_do_not_promote_squat() -> None:
    """With a single provider, the same domain can win every query variant and
    then self-proof. Duplicate votes from one provider must collapse to a
    single independent source, so the squat domain stays ``candidate`` and is
    never trusted as ``official``."""
    squat = _Prov(
        "brave",
        [SearchHit(title="Acme", url="https://acme-official.com/", snippet="")],
    )
    fetch = _SelfProofFetch(
        "https://acme-official.com/", "Acme - official site", "Acme"
    )
    r = OfficialDomainResolver(
        _cfg(max_discovery_searches=2),
        search_clients=[squat],
        fetch_client=fetch,
    )
    res = r.resolve("acme")
    assert res.domain == "acme-official.com"
    assert res.confidence == "candidate"
    # The duplicate brave votes collapse: only one search_provider signal.
    assert sum(1 for s in res.signals if s.tier == "search_vote") == 1


def test_denylist_suppresses_even_with_agreement() -> None:
    pa = _Prov("brave", [SearchHit(title="", url="https://medium.com/acme", snippet="")])
    pb = _Prov("google", [SearchHit(title="", url="https://medium.com/acme", snippet="")])
    r = OfficialDomainResolver(_cfg(), search_clients=[pa, pb], fetch_client=_NoFetch())
    res = r.resolve("acme")
    assert res.confidence == "none"


def test_structured_a_tier_alone_is_official() -> None:
    class _Struct:
        def __init__(self) -> None:
            self.calls: List[str] = []

    cfg = _cfg(structured_sources=("pypi",))
    r = OfficialDomainResolver(cfg, search_clients=[], fetch_client=_NoFetch())

    def fake_pypi(self: Any, stem: str, label: str) -> Any:
        return Signal(
            kind="pypi_home_page", source="pypi", domain="acme.com", tier="structured"
        ), ""

    # Monkeypatch the structured probe to avoid real network.
    r._probe_pypi = fake_pypi.__get__(r)  # type: ignore[method-assign]
    res = r.resolve("acme")
    assert res.is_official and res.domain == "acme.com"


# --- Pins + red line -------------------------------------------------------


def test_pins_override_with_zero_network() -> None:
    cfg = resolver_config_from_mapping({"pins": {"acme": ["acme.com"]}})
    r = OfficialDomainResolver(cfg, search_clients=[], fetch_client=_NoFetch())
    res = r.resolve("Acme")  # stem -> "acme" matches the pin key
    assert res.is_official and res.domain == "acme.com"
    assert all(s.tier == "pin" for s in res.signals)
    # No search/fetch client was ever consulted.
    assert r._search_clients == []


def test_product_stem_pin_upgrades_and_replaces_stale_candidate_cache() -> None:
    cache_path = os.path.join(tempfile.mkdtemp(), "cache.sqlite")
    cfg = resolver_config_from_mapping(
        {
            "enabled": True,
            "cache_path": cache_path,
            "structured_sources": [],
            "graph_probes_enabled": False,
            "pin_shadow_audit": False,
            "pins": {"kimi": ["www.kimi.com"]},
        }
    )
    resolver = OfficialDomainResolver(
        cfg,
        search_clients=[],
        fetch_client=_NoFetch(),
    )
    resolver._cache.put(
        Resolution(
            stem="kimik27code",
            domain="www.kimi.com",
            domains=["www.kimi.com"],
            confidence="candidate",
        )
    )

    result = resolver.resolve("Kimi K2.7 Code HighSpeed")

    assert result.is_official
    assert result.stem == "kimi"
    assert result.domain == "kimi.com"
    rows = resolver._cache._connect().execute(
        "SELECT stem, domain, confidence FROM entity_stem ORDER BY stem"
    ).fetchall()
    assert rows == [("kimi", "kimi.com", "official")]

    resolver.config.pins.clear()
    assert not resolver.resolve("kimi").is_official


def test_disabled_resolver_returns_none_for_unpinned() -> None:
    cfg = resolver_config_from_mapping({"enabled": False})
    r = OfficialDomainResolver(cfg, search_clients=[], fetch_client=_NoFetch())
    assert r.resolve("unknown-brand").confidence == "none"


def test_provider_exception_does_not_break_resolution() -> None:
    """A provider that raises must be skipped, not crash the whole resolution.

    Regression guard: the exception handler used to log an undefined ``label``
    name, so any provider error raised NameError and silently downgraded the
    entity to ``unknown`` (then cached that negative result for 24h)."""

    class _Boom:
        source_id = "boom"

        def search(self, query: str, num_results: int = 5, **kwargs: Any) -> List[SearchHit]:
            raise RuntimeError("provider down")

    good = _Prov("brave", [SearchHit(title="Acme", url="https://acme.com/", snippet="")])
    r = OfficialDomainResolver(
        _cfg(), search_clients=[_Boom(), good], fetch_client=_NoFetch()
    )
    res = r.resolve("SomeBrandXYZ")
    # The exploding provider is skipped; the lone good provider yields candidate.
    assert res.confidence == "candidate"
    assert res.domain == "acme.com"


def test_pins_cover_every_pinned_domain() -> None:
    """A multi-domain pin must classify *all* of its domains as official, not
    just the first. Regression guard for pins being collapsed to ``pinned[0]``
    while tiering checked equality against that single domain."""
    cfg = resolver_config_from_mapping({"pins": {"glm": ["bigmodel.cn", "zhipu.cn"]}})
    r = OfficialDomainResolver(cfg, search_clients=[], fetch_client=_NoFetch())
    res = r.resolve("glm")
    assert res.is_official
    assert set(res.resolved_domains) == {"bigmodel.cn", "zhipu.cn"}
    # A URL on the *second* pinned domain is official too.
    assert (
        classify_web_source_tier(
            "https://zhipu.cn/pricing",
            entities=["glm"],
            official_domains={},
            resolver=r,
        )
        == "official"
    )
    assert (
        official_entity_for_url(
            "https://open.bigmodel.cn/pricing",
            entities=["glm"],
            official_domains={},
            resolver=r,
        )
        == "glm"
    )


def test_cjk_entity_resolves_via_pin() -> None:
    """Pure-CJK labels used to normalize to an empty stem, so the resolver
    bailed before discovery and the pin never registered."""
    res_stem = normalize_entity_stem("小米")
    assert res_stem == "小米"  # not "" -- the bug collapsed all non-ASCII
    cfg = resolver_config_from_mapping({"pins": {"小米": ["mi.com"]}})
    r = OfficialDomainResolver(cfg, search_clients=[], fetch_client=_NoFetch())
    res = r.resolve("小米")
    assert res.is_official and res.domain == "mi.com"
    assert (
        classify_web_source_tier(
            "https://www.mi.com/store",
            entities=["小米"],
            official_domains={},
            resolver=r,
        )
        == "official"
    )


def test_page_body_claim_alone_is_not_official() -> None:
    """A page body asserting 'official site' without structural corroboration
    must not promote a domain. The self-proof requires a redirect/title match,
    and there is no A-tier or cross-provider vote here."""
    # A single provider points at the real domain, but the fetch self-proof
    # title does NOT mention the entity stem, so C-tier stays silent and only
    # one weak B-signal remains -> candidate, never official.
    p1 = _Prov("brave", [SearchHit(title="Acme", url="https://acme.com/", snippet="")])
    fetch = _SelfProofFetch("https://acme.com/", "Welcome to a Generic Site", "This is the official site")
    r = OfficialDomainResolver(_cfg(), search_clients=[p1], fetch_client=fetch)
    res = r.resolve("acme")
    # The body says "official site" but there is no structural corroboration:
    # the title lacks the stem, so the lone B-signal keeps it at candidate.
    assert res.confidence == "candidate"


# --- Cache + budgets -------------------------------------------------------


def test_positive_cache_prevents_rediscovery() -> None:
    p1 = _Prov("brave", [SearchHit(title="Acme", url="https://acme.com/", snippet="")])
    p2 = _Prov("google", [SearchHit(title="Acme", url="https://acme.com/x", snippet="")])
    clock = [1000.0]
    r = OfficialDomainResolver(
        _cfg(),
        search_clients=[p1, p2],
        fetch_client=_NoFetch(),
        probers=_ct_probers(),
        clock=lambda: clock[0],
    )
    assert r.resolve("acme").is_official
    calls_after_first = p1.calls + p2.calls
    assert r.resolve("acme").is_official
    assert p1.calls + p2.calls == calls_after_first  # served from cache


def test_expired_positive_entry_triggers_rediscovery() -> None:
    p1 = _Prov("brave", [SearchHit(title="Acme", url="https://acme.com/", snippet="")])
    p2 = _Prov("google", [SearchHit(title="Acme", url="https://acme.com/x", snippet="")])
    clock = [1000.0]
    r = OfficialDomainResolver(
        _cfg(cache_ttl_days=30),
        search_clients=[p1, p2],
        fetch_client=_NoFetch(),
        probers=_ct_probers(),
        clock=lambda: clock[0],
    )
    assert r.resolve("acme").is_official
    first = p1.calls + p2.calls
    clock[0] += 31 * 86400  # past positive TTL
    assert r.resolve("acme").is_official
    assert p1.calls + p2.calls > first


def test_negative_cache_respects_short_ttl() -> None:
    clock = [1000.0]
    r = OfficialDomainResolver(
        _cfg(negative_ttl_hours=1),
        search_clients=[],
        fetch_client=_NoFetch(),
        clock=lambda: clock[0],
    )
    assert r.resolve("ghost").confidence == "none"
    clock[0] += 2 * 3600  # past 1h negative TTL
    assert r.resolve("ghost").confidence == "none"  # re-evaluated, still none


def test_cache_failure_degrades_gracefully() -> None:
    cfg = _cfg(cache_path="/nonexistent-ise-dir/c.sqlite", structured_sources=())
    r = OfficialDomainResolver(cfg, search_clients=[], fetch_client=_NoFetch())
    # Must not raise; returns a safe none verdict.
    assert r.resolve("anything").confidence == "none"


def test_budget_bounds_discovery_and_early_exits() -> None:
    p1 = _Prov("brave", [SearchHit(title="Acme", url="https://acme.com/", snippet="")])
    p2 = _Prov("google", [SearchHit(title="Acme", url="https://acme.com/x", snippet="")])
    r = OfficialDomainResolver(
        _cfg(max_discovery_searches=2),
        search_clients=[p1, p2],
        fetch_client=_NoFetch(),
    )
    r.resolve("acme")
    # Two agreeing providers in round 1 -> early exit, exactly one round each.
    assert p1.calls == 1 and p2.calls == 1


# --- Subdomain helper ------------------------------------------------------


def test_subdomain_acceptance() -> None:
    assert is_subdomain_of("docs.openai.com", "openai.com")
    assert is_subdomain_of("platform.openai.com", "openai.com")
    assert is_subdomain_of("openai.com", "openai.com")
    assert not is_subdomain_of("evil-openai.com", "openai.com")
    assert not is_subdomain_of("openai.evil.com", "openai.com")


def test_host_pattern_preserves_path_and_rejects_sibling_hosts() -> None:
    pattern = HostPattern.parse("https://ai.google.dev/gemini")
    assert pattern is not None
    assert pattern.serialize() == "ai.google.dev/gemini"
    assert host_matches(pattern, "https://ai.google.dev/gemini/api")
    assert not host_matches(pattern, "https://ai.google.dev/docs")
    assert not host_matches(pattern, "https://google.com/gemini")


def test_host_level_pin_does_not_bless_google_search() -> None:
    cfg = resolver_config_from_mapping(
        {"pins": {"gemini": ["gemini.google.com", "ai.google.dev"]}}
    )
    r = OfficialDomainResolver(cfg, search_clients=[], fetch_client=_NoFetch())
    assert classify_web_source_tier(
        "https://ai.google.dev/gemini-api/docs",
        entities=["gemini"],
        official_domains={},
        resolver=r,
    ) == "official"
    assert classify_web_source_tier(
        "https://google.com/search?q=gemini",
        entities=["gemini"],
        official_domains={},
        resolver=r,
    ) == "excluded"


def test_sibling_hosts_do_not_merge_search_votes() -> None:
    p1 = _Prov("brave", [SearchHit(title="", url="https://cloud.google.com/", snippet="")])
    p2 = _Prov("google", [SearchHit(title="", url="https://ai.google.dev/", snippet="")])
    r = OfficialDomainResolver(
        _cfg(max_discovery_searches=1), search_clients=[p1, p2], fetch_client=_NoFetch()
    )
    res = r.resolve("gemini")
    assert res.confidence == "candidate"
    assert {signal.domain for signal in res.signals if signal.tier == "search_vote"} == {
        "cloud.google.com",
    }


def test_three_table_modes_and_legacy_denylist() -> None:
    cfg = resolver_config_from_mapping(
        {
            "never_official": {"mode": "replace", "domains": ["farm.example"]},
            "hosting_platforms": {"mode": "replace", "domains": ["host.example"]},
            "non_evidence": {"mode": "replace", "domains": ["search.example"]},
        }
    )
    assert cfg.never_official == frozenset({"farm.example"})
    assert cfg.hosting_platforms == frozenset({"host.example"})
    assert cfg.non_evidence == frozenset({"search.example"})

    legacy = resolver_config_from_mapping({"aggregator_denylist": ["legacy.example"]})
    assert "legacy.example" in legacy.never_official
    assert "medium.com" in legacy.never_official


def test_structured_signal_bypasses_list_and_hosting_ownership() -> None:
    cfg = _cfg(structured_sources=("pypi",))
    r = OfficialDomainResolver(cfg, search_clients=[], fetch_client=_NoFetch())

    def fake_pypi(self: Any, stem: str, label: str) -> Any:
        return Signal(
            kind="pypi_home_page",
            source="pypi",
            domain="acme.readthedocs.io",
            tier="structured",
        ), ""

    r._probe_pypi = fake_pypi.__get__(r)  # type: ignore[method-assign]
    res = r.resolve("acme")
    assert res.is_official and res.domain == "acme.readthedocs.io"
    assert classify_web_source_tier(
        "https://acme.readthedocs.io/en/latest/",
        entities=["acme"],
        official_domains={},
        resolver=r,
    ) == "official"


def test_matching_github_owner_establishes_path_scoped_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> Dict[str, Any]:
            return {
                "items": [
                    {
                        "full_name": "acme/widgets",
                        "name": "widgets",
                        "owner": {"login": "acme"},
                        "homepage": "",
                        "html_url": "https://github.com/acme/widgets",
                    }
                ]
            }

    monkeypatch.setattr("evidence.official_domain_resolver.requests.get", lambda *args, **kwargs: _Response())
    r = OfficialDomainResolver(_cfg(), search_clients=[], fetch_client=_NoFetch())
    probe = r._probe_github("acme", "Acme")
    assert probe is not None
    signal, repo_url = probe
    assert signal.domain == "github.com/acme/widgets"
    assert repo_url == ""  # the repo itself is the signal; nothing to close
    resolution = Resolution(
        stem="acme",
        domain=signal.domain,
        domains=[signal.domain],
        confidence="official",
    )
    assert host_matches(resolution.domain, "https://github.com/acme/widgets/issues")
    assert not host_matches(resolution.domain, "https://github.com/other/widgets")


def test_suspected_aggregator_needs_extra_vote_and_loses_subdomains() -> None:
    cfg = _cfg(
        max_discovery_searches=1,
        aggregator_min_stems=3,
        never_official={"mode": "replace", "domains": []},
        hosting_platforms={"mode": "replace", "domains": []},
        non_evidence={"mode": "replace", "domains": []},
    )
    p1 = _Prov("brave", [SearchHit(title="", url="https://shared.example/", snippet="")])
    p2 = _Prov("google", [SearchHit(title="", url="https://shared.example/", snippet="")])
    r = OfficialDomainResolver(cfg, search_clients=[p1, p2], fetch_client=_NoFetch())
    r._cache.record_candidate_observations("one", ["shared.example"])
    r._cache.record_candidate_observations("two", ["shared.example"])
    r._cache.record_candidate_observations("three", ["shared.example"])

    res = r.resolve("acme")
    assert res.confidence == "candidate"
    assert not res.subdomain_allowed
    assert classify_web_source_tier(
        "https://docs.shared.example/guide",
        entities=["acme"],
        official_domains={},
        resolver=r,
    ) == "unknown"

    p3 = _Prov("bing", [SearchHit(title="", url="https://shared.example/", snippet="")])
    r_three = OfficialDomainResolver(
        cfg,
        search_clients=[p1, p2, p3],
        fetch_client=_NoFetch(),
        probers=_ct_probers(),
    )
    assert r_three.resolve("other").is_official


def test_non_evidence_host_is_excluded_from_tiering() -> None:
    cfg = _cfg(non_evidence={"mode": "replace", "domains": ["search.example"]})
    r = OfficialDomainResolver(cfg, search_clients=[], fetch_client=_NoFetch())
    assert classify_web_source_tier(
        "https://search.example/?q=acme",
        entities=["acme"],
        official_domains={},
        resolver=r,
    ) == "excluded"


def test_non_evidence_host_is_removed_before_web_evidence_fusion() -> None:
    cfg = _cfg(non_evidence={"mode": "replace", "domains": ["search.example"]})
    resolver = OfficialDomainResolver(cfg, search_clients=[], fetch_client=_NoFetch())
    source = WebEvidenceSource(_Prov("search", []), official_resolver=resolver)
    items = source.hits_to_items(
        [SearchHit(title="Search", url="https://search.example/?q=acme", snippet="")],
        tier_entities=["acme"],
    )
    assert items == []


# --- Tier integration ------------------------------------------------------


def test_classify_uses_resolver_for_unconfigured_entity() -> None:
    p1 = _Prov("brave", [SearchHit(title="Acme", url="https://acme.com/", snippet="")])
    p2 = _Prov("google", [SearchHit(title="Acme", url="https://acme.com/x", snippet="")])
    r = OfficialDomainResolver(
        _cfg(),
        search_clients=[p1, p2],
        fetch_client=_NoFetch(),
        probers=_ct_probers(),
    )
    tier = classify_web_source_tier(
        "https://docs.acme.com/pricing",
        entities=["acme"],
        official_domains={},
        resolver=r,
    )
    assert tier == "official"
    assert (
        official_entity_for_url(
            "https://docs.acme.com/pricing",
            entities=["acme"],
            official_domains={},
            resolver=r,
        )
        == "acme"
    )


def test_classify_falls_back_to_unknown_when_resolver_has_nothing() -> None:
    r = OfficialDomainResolver(_cfg(), search_clients=[], fetch_client=_NoFetch())
    tier = classify_web_source_tier(
        "https://random-blog.example/x",
        entities=["ghost"],
        official_domains={},
        resolver=r,
    )
    assert tier == "unknown"


def test_configured_pin_still_classifies_official_without_resolver() -> None:
    # Backward compatibility: no resolver passed -> pin map still works.
    tier = classify_web_source_tier(
        "https://open.bigmodel.cn/pricing",
        entities=["glm5.2"],
        official_domains={"glm": ["bigmodel.cn", "open.bigmodel.cn"]},
    )
    assert tier == "official"


# --- Relationship-graph ruling ----------------------------------------------


def _registry_resolver(repo_url: str = "", backlink: Any = None) -> OfficialDomainResolver:
    """Resolver whose PyPI probe yields one registry edge (plus a repo target)."""
    cfg = _cfg(structured_sources=("pypi",))
    probers = GraphProbers.disabled()
    if backlink is not None:
        probers.backlink = backlink
    r = OfficialDomainResolver(
        cfg, search_clients=[], fetch_client=_NoFetch(), probers=probers
    )

    def fake_pypi(self: Any, stem: str, label: str) -> Any:
        return Signal(
            kind="pypi_home_page", source="pypi", domain="acme.com", tier="structured"
        ), repo_url

    r._probe_pypi = fake_pypi.__get__(r)  # type: ignore[method-assign]
    return r


def test_bidirectional_registry_closure_is_official() -> None:
    """registry -> homepage AND homepage -> same repo: the half a squatter
    cannot forge (the real repository never links out to the squat site)."""
    r = _registry_resolver(
        repo_url="https://github.com/acme/widgets",
        backlink=lambda page, target, **kw: (True, "https://github.com/acme/widgets"),
    )
    res = r.resolve("acme")
    assert res.is_official and res.domain == "acme.com"
    kinds = {e.kind for e in res.graph.edges}
    assert EDGE_REGISTRY_HOMEPAGE in kinds and EDGE_HOMEPAGE_REPO_BACKLINK in kinds


def test_registry_alone_uncontradicted_stays_official() -> None:
    """A registry edge with an inconclusive backlink check (no edge recorded)
    keeps the legacy A-tier verdict; only an active contradiction downgrades."""
    r = _registry_resolver(
        repo_url="https://github.com/acme/widgets",
        backlink=lambda page, target, **kw: (None, ""),  # fetch failed
    )
    assert r.resolve("acme").is_official


def test_backlink_contradiction_downgrades_registry_to_candidate() -> None:
    """The fetch ran and the page does not link back to the registry's repo:
    the one-directional registry link is forgeable, so it cannot rule alone."""
    r = _registry_resolver(
        repo_url="https://github.com/acme/widgets",
        backlink=lambda page, target, **kw: (False, ""),
    )
    res = r.resolve("acme")
    assert res.confidence == "candidate"
    contradiction = [
        e
        for e in res.graph.edges
        if e.kind == EDGE_HOMEPAGE_REPO_BACKLINK and not e.verified
    ]
    assert contradiction


def test_trusted_domain_redirect_into_candidate_is_official() -> None:
    """A previously verified domain redirecting into the candidate is strong
    bidirectional evidence -- and it is what captures brand renames."""
    p1 = _Prov("brave", [SearchHit(title="Kimi", url="https://kimi.com/", snippet="")])
    probers = GraphProbers.disabled()
    probers.redirect = lambda host, known, **kw: (
        [
            RelationEdge(
                kind=EDGE_REDIRECT_INTO,
                subject=known[0],
                object=host,
                detail="moonshot.cn -> https://kimi.com/",
                observed_at=1000.0,
            )
        ]
        if known
        else []
    )
    r = OfficialDomainResolver(
        _cfg(max_discovery_searches=1),
        search_clients=[p1],
        fetch_client=_NoFetch(),
        probers=probers,
    )
    # Seed a stale (expired) official entry for the old domain.
    r._cache.put(
        Resolution(
            stem="kimi",
            domain="moonshot.cn",
            domains=["moonshot.cn"],
            confidence="official",
            verified_at=1.0,
        )
    )
    r._cache.positive_ttl = 0  # force the seeded entry stale
    res = r.resolve("kimi")
    assert res.is_official and res.domain == "kimi.com"
    assert any(e.kind == EDGE_REDIRECT_INTO for e in res.graph.edges)


def test_wikidata_needs_a_supporting_edge() -> None:
    cfg = _cfg(structured_sources=("wikidata",))
    r = OfficialDomainResolver(cfg, search_clients=[], fetch_client=_NoFetch())

    def fake_wikidata(self: Any, stem: str, label: str) -> Any:
        return Signal(
            kind="wikidata_p856", source="wikidata", domain="acme.com", tier="structured"
        ), ""

    r._probe_wikidata = fake_wikidata.__get__(r)  # type: ignore[method-assign]
    assert r.resolve("acme").confidence == "candidate"  # P856 alone is not enough

    probers = GraphProbers.disabled()
    probers.facade = lambda host, **kw: [
        RelationEdge(
            kind=EDGE_FACADE, subject="docs.acme.com", object=host, observed_at=1000.0
        )
    ]
    r2 = OfficialDomainResolver(
        _cfg(structured_sources=("wikidata",)),
        search_clients=[],
        fetch_client=_NoFetch(),
        probers=probers,
    )
    r2._probe_wikidata = fake_wikidata.__get__(r2)  # type: ignore[method-assign]
    assert r2.resolve("acme").is_official


def test_cached_graph_is_readjudicated_under_new_rules() -> None:
    """The cache stores the graph, not the verdict: tighten min_signals and
    the cached graph re-decides offline, with zero provider calls."""
    p1 = _Prov("brave", [SearchHit(title="Acme", url="https://acme.com/", snippet="")])
    p2 = _Prov("google", [SearchHit(title="Acme", url="https://acme.com/x", snippet="")])
    tmp = tempfile.mkdtemp()
    cache_path = os.path.join(tmp, "cache.sqlite")
    cfg = _cfg(cache_path=cache_path, min_signals=2)
    r = OfficialDomainResolver(
        cfg, search_clients=[p1, p2], fetch_client=_NoFetch(), probers=_ct_probers()
    )
    assert r.resolve("acme").is_official
    calls = p1.calls + p2.calls

    # Same cache, stricter rule: 2 votes + CT age no longer suffice.
    strict = OfficialDomainResolver(
        _cfg(cache_path=cache_path, min_signals=3),
        search_clients=[p1, p2],
        fetch_client=_NoFetch(),
        probers=_ct_probers(),
    )
    res = strict.resolve("acme")
    assert res.confidence == "candidate" and res.domain == "acme.com"
    assert p1.calls + p2.calls == calls  # re-ruled from the cached graph, no network


def test_decide_graph_rules_table() -> None:
    def graph_with(*edges: RelationEdge) -> RelationGraph:
        graph = RelationGraph(stem="acme")
        for edge in edges:
            graph.add(edge)
        return graph

    vote = lambda provider: RelationEdge(
        kind=EDGE_SEARCH_VOTE, subject=provider, object="acme.com"
    )
    assert decide_graph(graph_with(vote("brave"), vote("google"))) == (
        "candidate",
        "acme.com",
    )
    assert decide_graph(RelationGraph(stem="acme")) == ("none", "")
    suspected = decide_graph(
        graph_with(
            vote("brave"),
            vote("google"),
            RelationEdge(kind=EDGE_CT_AGE, subject="crt.sh", object="acme.com"),
        ),
        suspected_aggregator=lambda host: True,
    )
    assert suspected == ("candidate", "acme.com")  # needs one extra vote


# --- adjudicate (agent boundary) -------------------------------------------


def test_adjudicate_merges_and_rules_external_graph() -> None:
    p1 = _Prov("brave", [SearchHit(title="Acme", url="https://acme.com/", snippet="")])
    r = OfficialDomainResolver(_cfg(), search_clients=[p1], fetch_client=_NoFetch())
    assert r.resolve("acme").confidence == "candidate"

    # An external discovery (e.g. the agent) supplies a registry closure the
    # deterministic path missed; adjudication merges and re-rules it.
    agent_graph = RelationGraph(stem="acme", generated_by="agent")
    agent_graph.add(
        RelationEdge(kind=EDGE_REGISTRY_HOMEPAGE, subject="pypi", object="acme.com")
    )
    agent_graph.add(
        RelationEdge(
            kind=EDGE_HOMEPAGE_REPO_BACKLINK,
            subject="github.com/acme/widgets",
            object="acme.com",
        )
    )
    res = r.adjudicate("acme", agent_graph)
    assert res.is_official and res.domain == "acme.com"
    # The merged graph is cached: a fresh resolver re-rules it without network.
    cached = r._cache.get("acme")
    assert cached is not None and cached.graph is not None
    assert {e.kind for e in cached.graph.edges} >= {
        EDGE_REGISTRY_HOMEPAGE,
        EDGE_HOMEPAGE_REPO_BACKLINK,
        EDGE_SEARCH_VOTE,
    }


# --- Pin shadow audit -------------------------------------------------------


def test_pin_shadow_audit_records_disagreement() -> None:
    """The pin still wins outright, but the disagreement is recorded and
    surfaced instead of staying invisible forever."""
    cfg = _cfg(structured_sources=("pypi",), pin_shadow_audit=True)
    r = OfficialDomainResolver(cfg, search_clients=[], fetch_client=_NoFetch())
    r.config.pins["acme"] = ["acme-squat.example"]

    def fake_pypi(self: Any, stem: str, label: str) -> Any:
        return Signal(
            kind="pypi_home_page", source="pypi", domain="acme.com", tier="structured"
        ), ""

    r._probe_pypi = fake_pypi.__get__(r)  # type: ignore[method-assign]
    res = r.resolve("acme")
    assert res.is_official and res.domain == "acme-squat.example"  # pin unchanged
    assert any(s.kind == "pin_shadow_disagreement" for s in res.signals)
    audit = r.pin_audit_for("acme")
    assert audit is not None and audit["agreement"] == 0
    assert audit["observed_domain"] == "acme.com"


def test_pin_shadow_audit_confirms_good_pin_once_per_ttl() -> None:
    cfg = _cfg(structured_sources=("pypi",), pin_shadow_audit=True)
    r = OfficialDomainResolver(cfg, search_clients=[], fetch_client=_NoFetch())
    r.config.pins["acme"] = ["acme.com"]
    calls = [0]

    def fake_pypi(self: Any, stem: str, label: str) -> Any:
        calls[0] += 1
        return Signal(
            kind="pypi_home_page", source="pypi", domain="acme.com", tier="structured"
        ), ""

    r._probe_pypi = fake_pypi.__get__(r)  # type: ignore[method-assign]
    res = r.resolve("acme")
    assert res.is_official and not any(
        s.kind == "pin_shadow_disagreement" for s in res.signals
    )
    audit = r.pin_audit_for("acme")
    assert audit is not None and audit["agreement"] == 1
    first_calls = calls[0]
    r.resolve("acme")  # fresh audit row -> no re-discovery within the TTL
    assert calls[0] == first_calls


def test_pin_shadow_audit_captures_rename_via_redirect() -> None:
    """moonshot.cn -> kimi.com: the pin is stale but consistent with the
    redirect chain, so the audit agrees AND notes the new domain."""
    cfg = _cfg(structured_sources=(), pin_shadow_audit=True)
    p1 = _Prov("brave", [SearchHit(title="Kimi", url="https://kimi.com/", snippet="")])
    probers = GraphProbers.disabled()
    probers.redirect = lambda host, known, **kw: (
        [
            RelationEdge(
                kind=EDGE_REDIRECT_INTO,
                subject=known[0],
                object=host,
                detail="moonshot.cn -> https://kimi.com/",
                observed_at=1000.0,
            )
        ]
        if known
        else []
    )
    r = OfficialDomainResolver(
        cfg, search_clients=[p1], fetch_client=_NoFetch(), probers=probers
    )
    r.config.pins["kimi"] = ["moonshot.cn"]
    res = r.resolve("kimi")
    assert res.is_official and res.domain == "moonshot.cn"  # pin still honored
    audit = r.pin_audit_for("kimi")
    assert audit is not None and audit["agreement"] == 1
    assert "kimi.com" in audit["detail"]


def test_pin_shadow_audit_disabled_by_config() -> None:
    cfg = resolver_config_from_mapping(
        {
            "enabled": True,
            "pins": {"acme": ["acme.com"]},
            "pin_shadow_audit": False,
            "graph_probes_enabled": False,
            "cache_path": os.path.join(tempfile.mkdtemp(), "cache.sqlite"),
        }
    )
    r = OfficialDomainResolver(cfg, search_clients=[], fetch_client=_NoFetch())
    assert r.resolve("acme").is_official
    assert r.pin_audit_for("acme") is None
