"""Structurally verifiable bidirectional relations for official-domain ruling.

Discovery (which may be agent-assisted) only ever *produces candidates and
fills this relation graph*; the ruling is a deterministic function of the
graph. Every edge is a checkable structural fact whose forgery cost is far
above SEO ranking:

- ``registry_homepage``: a package registry (PyPI/npm/GitHub) entry names the
  candidate as its homepage. Directional -- whoever controls the registry
  entry controls the link, so alone it is a weak A-signal.
- ``homepage_repo_backlink``: the candidate page links back to the *same*
  repository the registry entry names. A squatter cannot forge this half:
  the real repository never links out to the squat site. ``verified=False``
  means the check ran and the backlink was absent (a contradiction); a fetch
  failure records no edge at all (inconclusive).
- ``wikidata_p856``: Wikidata P856 (official website) names the candidate.
- ``redirect_into``: a domain already trusted for this entity (a pin, a
  configured alias, or a previously verified domain) redirects into the
  candidate. This also captures renames (moonshot.cn -> kimi.com).
- ``cert_san_cover``: the candidate's TLS certificate SAN set covers another
  domain already related to the entity.
- ``facade_consistency``: several public facades (docs/status/blog) resolve
  under the same registrable domain.
- ``ct_log_age``: certificate-transparency logs date the domain; squat
  domains are overwhelmingly young. ``verified`` means age >= threshold.
- ``search_vote``: one search provider surfaced the candidate. Demoted to a
  weak signal: cross-provider voting measures SEO rank, and with few
  providers ``min_signals`` degenerates into "everyone must agree".

The graph -- not the verdict -- is what gets cached. Ruling rules can be
revised and re-applied to a cached graph offline without re-crawling.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import requests


_LOGGER = logging.getLogger(__name__)

# Edge kinds.
EDGE_REGISTRY_HOMEPAGE = "registry_homepage"
EDGE_HOMEPAGE_REPO_BACKLINK = "homepage_repo_backlink"
EDGE_WIKIDATA_P856 = "wikidata_p856"
EDGE_REDIRECT_INTO = "redirect_into"
EDGE_CERT_SAN = "cert_san_cover"
EDGE_FACADE = "facade_consistency"
EDGE_CT_AGE = "ct_log_age"
EDGE_SEARCH_VOTE = "search_vote"

HTTP_TIMEOUT = 8
DEFAULT_MIN_DOMAIN_AGE_DAYS = 180
FACADE_PREFIXES = ("docs.", "status.", "blog.")
_CRTSH_API = "https://crt.sh/?q=%25.{domain}&output=json"


@dataclass
class RelationEdge:
    """One checkable structural fact between the entity and a candidate host.

    ``object`` is the candidate host pattern the fact speaks about; ``subject``
    is the other party (registry source, trusted domain, provider, ...).
    ``verified=False`` records an active contradiction (the check ran and the
    expected relation was absent); an inconclusive check records no edge.
    """

    kind: str
    subject: str
    object: str
    detail: str = ""
    verified: bool = True
    observed_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "object": self.object,
            "detail": self.detail[:300],
            "verified": self.verified,
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Optional["RelationEdge"]:
        try:
            kind = str(data.get("kind") or "")
            subject = str(data.get("subject") or "")
            obj = str(data.get("object") or "")
        except Exception:  # noqa: BLE001
            return None
        if not kind or not obj:
            return None
        return cls(
            kind=kind,
            subject=subject,
            object=obj,
            detail=str(data.get("detail") or ""),
            verified=bool(data.get("verified", True)),
            observed_at=float(data.get("observed_at") or 0.0),
        )


@dataclass
class RelationGraph:
    """The relation evidence for one entity stem. Cached, re-rulable."""

    stem: str
    edges: List[RelationEdge] = field(default_factory=list)
    generated_by: str = "deterministic"

    def add(self, edge: RelationEdge) -> None:
        key = (edge.kind, edge.subject, edge.object, edge.verified)
        for existing in self.edges:
            if (existing.kind, existing.subject, existing.object, existing.verified) == key:
                return
        self.edges.append(edge)

    def for_domain(self, domain: str) -> List[RelationEdge]:
        return [edge for edge in self.edges if edge.object == domain]

    def candidate_domains(self) -> List[str]:
        seen: Dict[str, None] = {}
        for edge in self.edges:
            if edge.object:
                seen.setdefault(edge.object, None)
        return list(seen)

    def merge(self, other: "RelationGraph") -> "RelationGraph":
        for edge in other.edges:
            self.add(edge)
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stem": self.stem,
            "generated_by": self.generated_by,
            "edges": [edge.to_dict() for edge in self.edges],
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["RelationGraph"]:
        if not isinstance(data, Mapping):
            return None
        stem = str(data.get("stem") or "")
        if not stem:
            return None
        graph = cls(stem=stem, generated_by=str(data.get("generated_by") or "deterministic"))
        for item in data.get("edges") or []:
            edge = RelationEdge.from_dict(item)
            if edge is not None:
                graph.edges.append(edge)
        return graph

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, payload: str) -> Optional["RelationGraph"]:
        try:
            return cls.from_dict(json.loads(payload or "{}"))
        except (ValueError, TypeError):
            return None


def _registrable(host: str) -> str:
    from evidence.source_tiering import registrable_domain

    return registrable_domain(host)


def _host_of(value: Any) -> str:
    from evidence.official_domain_resolver import HostPattern

    pattern = HostPattern.parse(value)
    return pattern.host if pattern is not None else ""


# -- Deterministic ruling ----------------------------------------------------


def decide_graph(
    graph: RelationGraph,
    *,
    min_signals: int = 2,
    suspected_aggregator: Optional[Callable[[str], bool]] = None,
) -> Tuple[str, str]:
    """Rule one entity's graph into ``(confidence, domain)`` deterministically.

    Returns ``("official"|"candidate"|"none", best_domain)``. Strong rules
    (any one sufficient for ``official``):

    1. bidirectional registry closure: registry homepage edge + verified
       backlink from the candidate page to the same repository;
    2. a trusted domain redirects into the candidate (catches renames);
    3. the candidate's certificate covers another domain of the same entity;
    4. Wikidata P856 plus one supporting edge (facade consistency or a
       verified CT-log age);
    5. a registry homepage edge with no recorded backlink contradiction
       (inconclusive checks never contradict);
    6. ``min_signals`` independent search votes AND a verified CT-log age --
       voting alone can never rule official.
    """
    suspected = suspected_aggregator or (lambda host: False)
    best_domain = ""
    best_rank = -1
    lead_domain = ""
    lead_rank = -1
    for domain in graph.candidate_domains():
        edges = graph.for_domain(domain)
        if not edges:
            continue
        rank = len(edges)
        if rank > lead_rank:
            lead_rank = rank
            lead_domain = domain
        kinds = {(edge.kind, edge.verified) for edge in edges}

        def has(kind: str) -> bool:
            return any(edge.kind == kind and edge.verified for edge in edges)

        closure = has(EDGE_REGISTRY_HOMEPAGE) and has(EDGE_HOMEPAGE_REPO_BACKLINK)
        redirect = has(EDGE_REDIRECT_INTO)
        cert = has(EDGE_CERT_SAN)
        wikidata = has(EDGE_WIKIDATA_P856) and (
            has(EDGE_FACADE) or has(EDGE_CT_AGE)
        )
        registry_uncontradicted = has(EDGE_REGISTRY_HOMEPAGE) and (
            EDGE_HOMEPAGE_REPO_BACKLINK,
            False,
        ) not in kinds
        votes = {edge.subject for edge in edges if edge.kind == EDGE_SEARCH_VOTE}
        required = min_signals + (1 if suspected(domain) else 0)
        votes_with_age = len(votes) >= required and has(EDGE_CT_AGE)

        strong = (
            closure or redirect or cert or wikidata
            or registry_uncontradicted or votes_with_age
        )
        if strong and rank > best_rank:
            best_rank = rank
            best_domain = domain
    if best_domain:
        return "official", best_domain
    if lead_domain:
        # A candidate keeps its leading host for traceability, but the domain
        # alone must never be treated as official.
        return "candidate", lead_domain
    return "none", ""


# -- Probers (real network implementations; injectable for tests) ------------


def probe_redirect_into(
    candidate_host: str,
    known_hosts: Iterable[str],
    *,
    opener: Optional[Callable[..., Any]] = None,
    timeout: int = HTTP_TIMEOUT,
    clock: Callable[[], float] = time.time,
) -> List[RelationEdge]:
    """Check whether already-trusted hosts redirect into the candidate."""
    from evidence.official_domain_resolver import host_matches

    edges: List[RelationEdge] = []
    candidate = _host_of(candidate_host)
    if not candidate:
        return edges
    for known in known_hosts or []:
        known_host = _host_of(known)
        if not known_host or host_matches(candidate, known_host):
            continue
        url = f"https://{known_host}/"
        try:
            if opener is not None:
                final_url = opener(url)
            else:
                resp = requests.head(
                    url, allow_redirects=True, timeout=timeout,
                    headers={"User-Agent": "ISE-official-domain-probe/1.0"},
                )
                final_url = resp.url
        except Exception:  # noqa: BLE001 - probe failures are inconclusive
            continue
        final_host = _host_of(final_url)
        if final_host and host_matches(candidate, final_host):
            edges.append(
                RelationEdge(
                    kind=EDGE_REDIRECT_INTO,
                    subject=known_host,
                    object=candidate,
                    detail=f"{known_host} -> {final_url}",
                    observed_at=clock(),
                )
            )
    return edges


def probe_cert_san(
    candidate_host: str,
    related_hosts: Iterable[str],
    *,
    san_fetcher: Optional[Callable[[str], Sequence[str]]] = None,
    timeout: int = HTTP_TIMEOUT,
    clock: Callable[[], float] = time.time,
) -> List[RelationEdge]:
    """Check the candidate's TLS SAN set for other domains of the same entity."""
    from evidence.official_domain_resolver import host_matches

    candidate = _host_of(candidate_host)
    if not candidate:
        return []

    def fetch_sans(host: str) -> Sequence[str]:
        if san_fetcher is not None:
            return san_fetcher(host)
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
        return [
            value
            for kind, value in cert.get("subjectAltName", ())
            if str(kind).lower() == "dns"
        ]

    try:
        sans = fetch_sans(candidate)
    except Exception:  # noqa: BLE001 - inconclusive
        return []
    edges: List[RelationEdge] = []
    candidate_registrable = _registrable(candidate)
    for related in related_hosts or []:
        related_host = _host_of(related)
        if not related_host or host_matches(candidate, related_host):
            continue
        for san in sans:
            san_host = san.lstrip("*.").casefold()
            if not san_host or _registrable(san_host) == candidate_registrable:
                continue
            if host_matches(related_host, san_host) or host_matches(san_host, related_host):
                edges.append(
                    RelationEdge(
                        kind=EDGE_CERT_SAN,
                        subject=related_host,
                        object=candidate,
                        detail=f"SAN {san_host} covers {related_host}",
                        observed_at=clock(),
                    )
                )
                break
    return edges


def probe_facades(
    candidate_host: str,
    *,
    dns_resolver: Optional[Callable[[str], bool]] = None,
    clock: Callable[[], float] = time.time,
) -> List[RelationEdge]:
    """Check docs/status/blog facades resolving under the candidate domain."""

    def resolves(host: str) -> bool:
        if dns_resolver is not None:
            return dns_resolver(host)
        try:
            socket.getaddrinfo(host, 443)
            return True
        except (socket.gaierror, OSError):
            return False

    candidate = _host_of(candidate_host)
    if not candidate:
        return []
    registrable = _registrable(candidate)
    resolved = [
        f"{prefix}{registrable}"
        for prefix in FACADE_PREFIXES
        if resolves(f"{prefix}{registrable}")
    ]
    if not resolved:
        return []
    return [
        RelationEdge(
            kind=EDGE_FACADE,
            subject=",".join(resolved),
            object=candidate,
            detail=f"facades live: {', '.join(resolved)}",
            observed_at=clock(),
        )
    ]


def probe_ct_age(
    candidate_host: str,
    *,
    min_age_days: int = DEFAULT_MIN_DOMAIN_AGE_DAYS,
    fetcher: Optional[Callable[[str], Any]] = None,
    timeout: int = HTTP_TIMEOUT,
    clock: Callable[[], float] = time.time,
) -> List[RelationEdge]:
    """Date the domain via certificate-transparency logs (no whois needed).

    ``verified`` means the earliest logged certificate is at least
    ``min_age_days`` old; a younger domain yields a verified=False edge so the
    youth is visible without ever promoting.
    """
    candidate = _host_of(candidate_host)
    registrable = _registrable(candidate)
    if not registrable:
        return []
    try:
        if fetcher is not None:
            entries = fetcher(registrable)
        else:
            resp = requests.get(
                _CRTSH_API.format(domain=registrable), timeout=timeout,
                headers={"User-Agent": "ISE-official-domain-probe/1.0"},
            )
            resp.raise_for_status()
            entries = resp.json()
    except Exception:  # noqa: BLE001 - inconclusive
        return []
    earliest: Optional[float] = None
    for entry in entries or []:
        raw = str((entry or {}).get("not_before") or "").strip()
        if not raw:
            continue
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            try:
                stamp = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S").replace(
                    tzinfo=timezone.utc
                ).timestamp()
            except ValueError:
                continue
        earliest = stamp if earliest is None else min(earliest, stamp)
    if earliest is None:
        return []
    age_days = max(0, int((clock() - earliest) / 86400))
    return [
        RelationEdge(
            kind=EDGE_CT_AGE,
            subject="crt.sh",
            object=candidate,
            detail=f"earliest CT entry {age_days}d ago",
            verified=age_days >= max(1, int(min_age_days)),
            observed_at=clock(),
        )
    ]


_HREF_RE = re.compile(r"""href\s*=\s*["']([^"'#]+)["']""", re.IGNORECASE)


def extract_href_targets(html: str, *, limit: int = 500) -> List[str]:
    """Pull outbound link targets from raw HTML (deterministic, no parsing lib)."""
    targets: List[str] = []
    for match in _HREF_RE.finditer(html or ""):
        target = match.group(1).strip()
        if target and target not in targets:
            targets.append(target)
        if len(targets) >= limit:
            break
    return targets


def check_backlink(
    page_url: str,
    target: str,
    *,
    html_fetcher: Optional[Callable[[str], str]] = None,
    timeout: int = HTTP_TIMEOUT,
) -> Tuple[Optional[bool], str]:
    """Whether ``page_url`` links out to ``target`` (host or host/path).

    Tri-state: ``(True, url)`` backlink found; ``(False, "")`` the fetch ran
    and no backlink exists (an active contradiction); ``(None, "")`` the fetch
    failed -- inconclusive, which must never contradict.
    """
    from evidence.official_domain_resolver import host_matches

    if not _host_of(page_url) or not _host_of(target):
        return None, ""
    try:
        if html_fetcher is not None:
            html = html_fetcher(page_url)
        else:
            resp = requests.get(
                page_url,
                timeout=timeout,
                headers={"User-Agent": "ISE-official-domain-probe/1.0"},
            )
            resp.raise_for_status()
            html = resp.text or ""
    except Exception:  # noqa: BLE001 - inconclusive
        return None, ""
    if not html:
        return None, ""
    for link in extract_href_targets(html):
        if host_matches(target, link):
            return True, link
    return False, ""


@dataclass
class GraphProbers:
    """Injectable probe bundle so ruling tests never touch the network."""

    redirect: Optional[Callable[..., List[RelationEdge]]] = None
    cert_san: Optional[Callable[..., List[RelationEdge]]] = None
    facade: Optional[Callable[..., List[RelationEdge]]] = None
    ct_age: Optional[Callable[..., List[RelationEdge]]] = None
    backlink: Optional[Callable[..., Tuple[Optional[bool], str]]] = None

    def with_defaults(self) -> "GraphProbers":
        return GraphProbers(
            redirect=self.redirect or probe_redirect_into,
            cert_san=self.cert_san or probe_cert_san,
            facade=self.facade or probe_facades,
            ct_age=self.ct_age or probe_ct_age,
            backlink=self.backlink or check_backlink,
        )

    @classmethod
    def disabled(cls) -> "GraphProbers":
        return cls(
            redirect=lambda *a, **k: [],
            cert_san=lambda *a, **k: [],
            facade=lambda *a, **k: [],
            ct_age=lambda *a, **k: [],
            backlink=lambda *a, **k: (None, ""),
        )
