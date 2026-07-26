from __future__ import annotations

from evidence.source_verdict import (
    AUTHORITATIVE_TIERS,
    SourceVerdict,
    classify_source,
    is_authoritative_tier,
    normalize_source_tier,
)


class _Signal:
    def __init__(self, kind, domain="", detail=""):
        self.kind = kind
        self.domain = domain
        self.detail = detail


class _Resolution:
    def __init__(self, *, official=False, domain="", signals=()):
        self.is_official = official
        self.domain = domain
        self.resolved_domains = [domain] if domain else []
        self.subdomain_allowed = True
        self.confidence = "official" if official else "none"
        self.signals = list(signals)


class _Resolver:
    """Minimal resolver stub: pins + optional disagreement + denylist."""

    def __init__(self, *, resolutions=None, never_official=(), non_evidence=()):
        self._resolutions = resolutions or {}
        self._never_official = set(never_official)
        self._non_evidence = set(non_evidence)

    def resolve(self, entity):
        return self._resolutions.get(
            str(entity).strip(), _Resolution(official=False)
        )

    def is_never_official(self, url):
        return any(host in str(url) for host in self._never_official)

    def is_suspected_aggregator(self, url):
        return False

    def is_non_evidence(self, url):
        return any(host in str(url) for host in self._non_evidence)


def test_normalize_source_tier_maps_legacy_aliases():
    assert normalize_source_tier("authoritative") == "first_party"
    assert normalize_source_tier("fetched") == "unknown"
    assert normalize_source_tier("official") == "official"
    assert normalize_source_tier("") == "unknown"
    assert normalize_source_tier("garbage") == "unknown"


def test_is_authoritative_tier():
    assert is_authoritative_tier("official")
    assert is_authoritative_tier("first_party")
    assert is_authoritative_tier("authoritative")  # via alias -> first_party
    assert not is_authoritative_tier("unknown")
    assert not is_authoritative_tier("aggregator")
    assert not is_authoritative_tier("fetched")


def test_classify_official_via_config_map():
    verdict = classify_source(
        "https://platform.kimi.com/docs/pricing",
        entities=["Kimi"],
        official_domains={"kimi": ["kimi.com"]},
        resolver=None,
    )
    assert verdict.tier == "official"
    assert verdict.target == "Kimi"
    assert "kimi.com" in verdict.why
    assert verdict.authoritative


def test_classify_official_via_resolver_pin_shows_why():
    resolver = _Resolver(
        resolutions={
            "Kimi": _Resolution(
                official=True,
                domain="kimi.com",
                signals=[_Signal("pin", domain="kimi.com")],
            )
        }
    )
    verdict = classify_source(
        "https://kimi.com/pricing",
        entities=["Kimi"],
        official_domains={},
        resolver=resolver,
    )
    assert verdict.tier == "official"
    assert verdict.why == "config pin: kimi.com"


def test_classify_surfaces_pin_discovery_disagreement():
    resolver = _Resolver(
        resolutions={
            "Kimi": _Resolution(
                official=True,
                domain="kimi.com",
                signals=[
                    _Signal("pin", domain="kimi.com"),
                    _Signal(
                        "pin_shadow_disagreement",
                        detail="pinned kimi.com but discovery ruled github.com/MoonshotAI/Kimi-K2 official",
                    ),
                ],
            )
        }
    )
    verdict = classify_source(
        "https://kimi.com/pricing",
        entities=["Kimi"],
        official_domains={},
        resolver=resolver,
    )
    assert verdict.tier == "official"
    assert "discovery ruled github.com" in verdict.disagreement
    metadata = verdict.to_metadata()
    assert metadata["source_verdict_disagreement"]


def test_classify_aggregator_via_denylist():
    resolver = _Resolver(never_official={"csdn.net"})
    verdict = classify_source(
        "https://blog.csdn.net/x/article",
        entities=["Kimi"],
        official_domains={},
        resolver=resolver,
    )
    assert verdict.tier == "aggregator"
    assert not verdict.authoritative


def test_classify_excluded_non_evidence():
    resolver = _Resolver(non_evidence={"google.com"})
    verdict = classify_source(
        "https://www.google.com/search?q=kimi",
        entities=["Kimi"],
        official_domains={},
        resolver=resolver,
    )
    assert verdict.tier == "excluded"


def test_classify_first_party_heuristic():
    verdict = classify_source(
        "https://acme-widgets.com/pricing",
        entities=["acme-widgets"],
        official_domains={},
        resolver=None,
    )
    assert verdict.tier == "first_party"
    assert "启发式" in verdict.why
    assert verdict.authoritative


def test_classify_unknown_when_nothing_matches():
    verdict = classify_source(
        "https://empiriolabs.ai/models/kimi",
        entities=["Kimi"],
        official_domains={},
        resolver=None,
    )
    assert verdict.tier == "unknown"
    assert not verdict.authoritative


def test_verdict_to_metadata_keys():
    verdict = SourceVerdict(
        tier="official", target="Kimi", why="config pin: kimi.com"
    )
    metadata = verdict.to_metadata()
    assert metadata["source_tier"] == "official"
    assert metadata["source_target"] == "Kimi"
    assert metadata["source_verdict_why"] == "config pin: kimi.com"
    assert "source_verdict_disagreement" not in metadata
