"""Deterministic source-tier classification for web evidence."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit


_MULTI_LABEL_PUBLIC_SUFFIXES = {
    "ac.uk",
    "co.uk",
    "gov.uk",
    "ltd.uk",
    "me.uk",
    "net.uk",
    "org.uk",
    "plc.uk",
    "com.au",
    "edu.au",
    "gov.au",
    "net.au",
    "org.au",
    "com.br",
    "com.cn",
    "edu.cn",
    "gov.cn",
    "net.cn",
    "org.cn",
    "com.hk",
    "edu.hk",
    "gov.hk",
    "net.hk",
    "org.hk",
    "co.jp",
    "ne.jp",
    "or.jp",
    "co.kr",
    "or.kr",
    "com.mx",
    "com.sg",
    "com.tw",
    "org.tw",
    "co.nz",
}


def normalize_entity_stem(value: Any) -> str:
    """Normalize an entity token for deterministic matching and cache keys.

    Version suffixes are stripped (``glm5.2`` -> ``glm``, ``kimik3`` ->
    ``kimi``) and separators collapsed, but non-ASCII characters are preserved
    so CJK and accented brand names keep a usable stem. An ASCII-only filter is
    intentionally avoided: it turned ``小米`` into ``""`` (making the resolver
    bail immediately) and ``Hermès`` into ``"herms"`` (a garbage stem that
    polluted the cache and crossed entity boundaries).
    """
    text = str(value or "").strip()
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).casefold()
    # Strip product-version tails such as ``K2.7 Code HighSpeed`` before the
    # generic numeric suffix rule. This keeps a branded model label bound to
    # the explicit brand pin instead of creating a second candidate cache key.
    text = re.sub(
        r"(?:[\s_.-]*[vk]?\d+(?:[\s_.-]*\d+)*)"
        r"(?:[\s_.-]*(?:code|chat|instruct|highspeed))+$",
        "",
        text,
    )
    # Strip trailing version suffixes (v3, 5.2, k3, ...) but keep everything
    # else, including CJK and accented letters.
    text = re.sub(r"(?:[-_.]*[vk]?\d+(?:[-_.]*\d+)*)+$", "", text)
    text = re.sub(r"[\s_.-]+", "", text)
    return text


def registrable_domain(value: Any) -> str:
    """Return a compact registrable-domain approximation without network I/O."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        host = (urlsplit(raw).hostname or "").casefold().strip(".")
    except ValueError:
        return ""
    if not host:
        return ""
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host) or ":" in host:
        return host
    labels = [label for label in host.split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    suffix = ".".join(labels[-2:])
    if suffix in _MULTI_LABEL_PUBLIC_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return suffix


def _domain_label(domain: str) -> str:
    labels = [label for label in domain.split(".") if label]
    if len(labels) <= 2:
        return labels[0] if labels else ""
    suffix = ".".join(labels[-2:])
    return labels[-3] if suffix in _MULTI_LABEL_PUBLIC_SUFFIXES else labels[-2]


def _host_pattern(value: Any) -> str:
    """Normalize a host/path entry without importing the resolver at module load."""
    from evidence.official_domain_resolver import HostPattern

    pattern = HostPattern.parse(value)
    return pattern.serialize() if pattern is not None else ""


def _host_matches(pattern: Any, url: Any, *, allow_subdomains: bool = True) -> bool:
    """Match ownership patterns, including safe sibling service subdomains.

    The resolver keeps host-level boundaries for multi-tenant and path-scoped
    ownership. Source tiering additionally treats conventional service hosts
    such as ``www.example.com`` and ``platform.example.com`` as the same
    first-party site when they share a registrable domain.
    """
    from evidence.official_domain_resolver import (
        HostPattern,
        _WELL_KNOWN_SUBDOMAIN_PREFIXES,
        host_matches,
    )

    if host_matches(pattern, url, allow_subdomains=allow_subdomains):
        return True
    if not allow_subdomains:
        return False

    pattern_value = HostPattern.parse(pattern)
    target = HostPattern.parse(url)
    if (
        pattern_value is None
        or target is None
        or pattern_value.path_prefix
    ):
        return False

    pattern_domain = registrable_domain(pattern_value.host)
    target_domain = registrable_domain(target.host)
    if not pattern_domain or pattern_domain != target_domain:
        return False

    known_prefixes = {
        value.rstrip(".") for value in _WELL_KNOWN_SUBDOMAIN_PREFIXES
    }

    def is_owned_service_host(host: str, domain: str) -> bool:
        if host == domain:
            return True
        suffix = "." + domain
        if not host.endswith(suffix):
            return False
        prefix = host[: -len(suffix)]
        return "." not in prefix and prefix in known_prefixes

    return is_owned_service_host(
        pattern_value.host, pattern_domain
    ) and is_owned_service_host(target.host, target_domain)


def _url_host(value: Any) -> str:
    from evidence.official_domain_resolver import HostPattern

    pattern = HostPattern.parse(value)
    return pattern.host if pattern is not None else ""


def _normalized_official_domains(official_domains: Optional[Mapping[str, Any]]) -> Dict[str, set[str]]:
    normalized: Dict[str, set[str]] = {}
    if not isinstance(official_domains, Mapping):
        return normalized
    for alias, configured_domains in official_domains.items():
        if str(alias).startswith("_"):
            continue
        stem = normalize_entity_stem(alias)
        if not stem:
            continue
        if isinstance(configured_domains, str):
            configured_domains = [configured_domains]
        if not isinstance(configured_domains, Iterable):
            continue
        domains = {pattern for value in configured_domains if (pattern := _host_pattern(value))}
        if domains:
            normalized.setdefault(stem, set()).update(domains)
    return normalized


def official_domain_targets(
    entities: Optional[Iterable[Any]],
    official_domains: Optional[Mapping[str, Any]],
    *,
    limit: int = 4,
) -> List[Dict[str, Any]]:
    """Project configured official domains onto the current query entities.

    The original entity label is retained for trace and coverage while matching
    uses the stable version-stripped stem. Unrelated configured aliases are
    intentionally omitted.
    """
    configured = _normalized_official_domains(official_domains)
    targets: List[Dict[str, Any]] = []
    seen = set()
    for entity in entities or []:
        label = str(entity or "").strip()
        stem = normalize_entity_stem(label)
        if not label or not stem or stem in seen:
            continue
        domains = configured.get(stem, set())
        if not domains:
            continue
        seen.add(stem)
        targets.append(
            {
                "entity": label,
                "stem": stem,
                "domains": sorted(domains),
            }
        )
        if len(targets) >= max(1, int(limit)):
            break
    return targets


def official_entity_for_url(
    url: Any,
    *,
    entities: Optional[Iterable[Any]] = None,
    official_domains: Optional[Mapping[str, Any]] = None,
    resolver: Any = None,
) -> Optional[str]:
    """Return the current-query entity whose host pattern owns ``url``."""
    if not _url_host(url):
        return None
    for target in official_domain_targets(entities, official_domains, limit=32):
        if any(_host_matches(pattern, url) for pattern in target["domains"]):
            return str(target["entity"])
    if resolver is not None:
        for entity in entities or []:
            label = str(entity or "").strip()
            if not label:
                continue
            try:
                resolution = resolver.resolve(label)
            except Exception:  # noqa: BLE001 - resolver failures must not poison tiering
                continue
            if not getattr(resolution, "is_official", False):
                continue
            # A resolution may cover several host/path patterns (e.g. a
            # multi-host pin); accept only an explicit host-pattern match.
            resolved_domains = getattr(resolution, "resolved_domains", None)
            if resolved_domains is None:
                resolved_domains = [getattr(resolution, "domain", "")]
            allow_subdomains = bool(getattr(resolution, "subdomain_allowed", True))
            if any(
                _host_matches(pattern, url, allow_subdomains=allow_subdomains)
                for pattern in resolved_domains
            ):
                return label
    return None


def provisional_entity_for_url(
    url: Any,
    *,
    entities: Optional[Iterable[Any]] = None,
    resolver: Any = None,
) -> Optional[str]:
    """Return an entity whose resolver candidate matches ``url``.

    Candidate ownership is deliberately not promoted to ``official`` or
    ``first_party``. The marker only enables a bounded, explicitly qualified
    fallback when the loop cannot verify authority after repeated attempts.
    """
    if resolver is None or not _url_host(url):
        return None
    for entity in entities or []:
        label = str(entity or "").strip()
        if not label:
            continue
        try:
            resolution = resolver.resolve(label)
        except Exception:  # noqa: BLE001 - resolver failures must not poison tiering
            continue
        if str(getattr(resolution, "confidence", "")).casefold() != "candidate":
            continue
        resolved_domains = getattr(resolution, "resolved_domains", None)
        if resolved_domains is None:
            resolved_domains = [getattr(resolution, "domain", "")]
        allow_subdomains = bool(getattr(resolution, "subdomain_allowed", True))
        if any(
            _host_matches(pattern, url, allow_subdomains=allow_subdomains)
            for pattern in resolved_domains
        ):
            return label
    return None


def classify_web_source_tier(
    url: Any,
    *,
    entities: Optional[Iterable[Any]] = None,
    official_domains: Optional[Mapping[str, Any]] = None,
    resolver: Any = None,
) -> str:
    """Classify a web URL as official, first-party, excluded, or unknown.

    Configured aliases win over heuristic stem matching. When a ``resolver`` is
    supplied, entities it confirms as official also classify the URL as
    ``official``. The heuristic only examines the registrable-domain label,
    never arbitrary URL path text.
    """
    host = _url_host(url)
    if not host:
        return "unknown"

    if is_non_evidence_url(url, resolver=resolver):
        return "excluded"

    stems = [normalize_entity_stem(entity) for entity in entities or []]
    stems = [stem for stem in stems if stem]
    if official_entity_for_url(
        url,
        entities=entities,
        official_domains=official_domains,
        resolver=resolver,
    ):
        return "official"

    label = _domain_label(registrable_domain(host))
    normalized_label = re.sub(r"[^a-z0-9]", "", label.casefold())
    if normalized_label and any(
        min(len(stem), len(normalized_label)) >= 4
        and (stem == normalized_label or normalized_label in stem)
        for stem in stems
    ):
        return "first_party"
    return "unknown"


def is_non_evidence_url(url: Any, *, resolver: Any = None) -> bool:
    """Whether a URL must be excluded from ordinary web evidence."""
    if resolver is not None:
        try:
            return bool(resolver.is_non_evidence(url))
        except Exception:  # noqa: BLE001 - a resolver must not break retrieval
            return False
    # The default remains useful to callers that only use the static tiering
    # helpers and have not constructed a resolver.
    from evidence.official_domain_resolver import _DEFAULT_NON_EVIDENCE

    return any(_host_matches(pattern, url) for pattern in _DEFAULT_NON_EVIDENCE)
