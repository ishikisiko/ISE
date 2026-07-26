"""Single facade over the authority machinery.

Four call sites used to consume the resolver, the static ``official_domains``
map, and the tiering heuristics independently, each with its own tier
vocabulary (``official`` / ``first_party`` / ``authoritative`` / ``fetched`` /
``unknown``). This module funnels every web-source decision through one
function that returns a :class:`SourceVerdict` carrying a canonical tier, the
matched entity, and a short human-readable ``why`` suitable for both the model
context (the evidence ledger) and the audit log.

The implementation underneath (resolver, relation graph, tiering heuristics)
is unchanged; this is only a normalizing facade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from evidence.source_tiering import (
    classify_web_source_tier,
    is_non_evidence_url,
    official_domain_targets,
    official_entity_for_url,
)

# Canonical tier vocabulary for web evidence. Every consumer should reference
# these names rather than literal strings.
TIER_OFFICIAL = "official"
TIER_FIRST_PARTY = "first_party"
TIER_AGGREGATOR = "aggregator"
TIER_UNKNOWN = "unknown"
TIER_EXCLUDED = "excluded"

CANONICAL_TIERS = (
    TIER_OFFICIAL,
    TIER_FIRST_PARTY,
    TIER_AGGREGATOR,
    TIER_UNKNOWN,
    TIER_EXCLUDED,
)

# Tiers trusted to back an authority-required claim. ``official`` is pinned or
# resolver-confirmed; ``first_party`` is a domain-stem heuristic match and is
# accepted but with lower confidence.
AUTHORITATIVE_TIERS = frozenset({TIER_OFFICIAL, TIER_FIRST_PARTY})

# Legacy / alias tiers emitted by paths that predate the canonical vocabulary.
# ``authoritative`` is a domain-skill primary source that was never confirmed
# by the resolver, so it normalizes to the heuristic ``first_party`` level.
# ``fetched`` described a fetch outcome rather than a real tier; a page that
# was fetched but could not be classified is ``unknown``.
_TIER_ALIASES = {
    "authoritative": TIER_FIRST_PARTY,
    "fetched": TIER_UNKNOWN,
    "": TIER_UNKNOWN,
}


def normalize_source_tier(tier: Any) -> str:
    """Map any historical tier label onto the canonical vocabulary."""
    text = str(tier or "").strip().casefold()
    text = _TIER_ALIASES.get(text, text)
    return text if text in CANONICAL_TIERS else TIER_UNKNOWN


def is_authoritative_tier(tier: Any) -> bool:
    """Whether a (possibly legacy) tier label counts as authoritative."""
    return normalize_source_tier(tier) in AUTHORITATIVE_TIERS


@dataclass
class SourceVerdict:
    """The authority verdict for one URL, with provenance for model and audit."""

    tier: str
    target: Optional[str] = None
    why: str = ""
    disagreement: str = ""

    @property
    def authoritative(self) -> bool:
        return self.tier in AUTHORITATIVE_TIERS

    def to_metadata(self) -> Dict[str, Any]:
        """Project onto evidence-record metadata keys."""
        data: Dict[str, Any] = {
            "source_tier": self.tier,
            "source_verdict_why": self.why,
        }
        if self.target:
            data["source_target"] = self.target
        if self.disagreement:
            data["source_verdict_disagreement"] = self.disagreement
        return data


def _resolver(entity_resolver: Any) -> Any:
    return entity_resolver


def _official_why(
    url: Any,
    target: Optional[str],
    official_domains: Optional[Mapping[str, Any]],
    resolver: Any,
) -> "tuple[str, str]":
    """Explain why ``url`` is official for ``target``; surface pin disagreement.

    Returns ``(why, disagreement)``. Checks the same sources in the same order
    as :func:`official_entity_for_url`: the static config map first, then the
    resolver (pin, then discovery).
    """
    if target:
        for item in official_domain_targets([target], official_domains, limit=1):
            for pattern in item.get("domains", []):
                from evidence.source_tiering import _host_matches

                if _host_matches(pattern, url):
                    return f"config official_domains: {pattern}", ""
    if resolver is not None and target:
        try:
            resolution = resolver.resolve(target)
        except Exception:  # noqa: BLE001 - verdict must not fail on resolver error
            resolution = None
        if resolution is not None:
            disagreement = ""
            pinned = False
            for signal in getattr(resolution, "signals", None) or []:
                kind = str(getattr(signal, "kind", ""))
                if kind == "pin":
                    pinned = True
                elif kind == "pin_shadow_disagreement":
                    disagreement = str(getattr(signal, "detail", "") or "")
            if pinned:
                domain = str(getattr(resolution, "domain", "") or target)
                return f"config pin: {domain}", disagreement
            if getattr(resolution, "is_official", False):
                return "resolver 确认 (relation graph)", disagreement
    return ("official", "")


def classify_source(
    url: Any,
    *,
    entities: Optional[Iterable[Any]] = None,
    official_domains: Optional[Mapping[str, Any]] = None,
    resolver: Any = None,
) -> SourceVerdict:
    """Classify ``url`` into a canonical :class:`SourceVerdict`.

    This is the single entry point every web-evidence path should use instead
    of calling :func:`classify_web_source_tier` plus ad-hoc post-processing.
    """
    entity_list = [str(e).strip() for e in (entities or []) if str(e or "").strip()]

    if is_non_evidence_url(url, resolver=resolver):
        return SourceVerdict(
            tier=TIER_EXCLUDED,
            target=None,
            why="非证据类页面（搜索结果页/登录墙等）",
        )

    base = classify_web_source_tier(
        url,
        entities=entity_list,
        official_domains=official_domains,
        resolver=resolver,
    )

    if base == TIER_OFFICIAL:
        target = official_entity_for_url(
            url,
            entities=entity_list,
            official_domains=official_domains,
            resolver=resolver,
        )
        why, disagreement = _official_why(url, target, official_domains, resolver)
        return SourceVerdict(
            tier=TIER_OFFICIAL,
            target=target,
            why=why,
            disagreement=disagreement,
        )

    # Aggregators are known-low-quality; surface them distinctly rather than
    # letting them blend into ``unknown`` (which merely means "unclassified").
    # ``is_never_official`` is the explicit content-farm denylist (csdn, zhihu,
    # medium, ...); ``is_suspected_aggregator`` is the learned cross-entity
    # signal. Either marks the host as an aggregator.
    if resolver is not None:
        try:
            if resolver.is_never_official(url) or resolver.is_suspected_aggregator(url):
                return SourceVerdict(
                    tier=TIER_AGGREGATOR,
                    target=None,
                    why="已知聚合/内容农场站（denylist）",
                )
        except Exception:  # noqa: BLE001 - aggregator check must not fail verdict
            pass

    if base == TIER_FIRST_PARTY:
        target = official_entity_for_url(
            url,
            entities=entity_list,
            official_domains=official_domains,
            resolver=resolver,
        )
        return SourceVerdict(
            tier=TIER_FIRST_PARTY,
            target=target,
            why="域名词干启发式匹配，未经 pin/resolver 确认",
        )

    return SourceVerdict(
        tier=TIER_UNKNOWN,
        target=None,
        why="未匹配任何已知官方来源",
    )
