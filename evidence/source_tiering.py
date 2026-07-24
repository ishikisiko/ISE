"""Deterministic source-tier classification for web evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Dict, Optional
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
    """Normalize an entity token for deterministic domain matching.

    Version suffixes and separators are deliberately removed so product labels
    such as ``glm5.2`` and ``kimik3`` both map to their stable brand stems.
    """
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    text = re.sub(r"(?:[-_.]*[vk]?\d+(?:[-_.]*\d+)*)+$", "", text)
    text = re.sub(r"[^a-z0-9]", "", text)
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
        domains = {
            domain
            for value in configured_domains
            if (domain := registrable_domain(value))
        }
        if domains:
            normalized.setdefault(stem, set()).update(domains)
    return normalized


def classify_web_source_tier(
    url: Any,
    *,
    entities: Optional[Iterable[Any]] = None,
    official_domains: Optional[Mapping[str, Any]] = None,
) -> str:
    """Classify a web URL as official, first-party, or unknown.

    Configured aliases win over heuristic stem matching. The heuristic only
    examines the registrable-domain label, never arbitrary URL path text.
    """
    domain = registrable_domain(url)
    if not domain:
        return "unknown"

    stems = [normalize_entity_stem(entity) for entity in entities or []]
    stems = [stem for stem in stems if stem]
    configured = _normalized_official_domains(official_domains)
    if any(domain in configured.get(stem, set()) for stem in stems):
        return "official"

    label = _domain_label(domain)
    normalized_label = re.sub(r"[^a-z0-9]", "", label.casefold())
    if any(stem in normalized_label for stem in stems):
        return "first_party"
    return "unknown"
