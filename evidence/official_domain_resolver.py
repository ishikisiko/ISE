"""Discover -> verify -> cache resolver for entity official domains.

The resolver maps an entity stem to a verified official registrable domain.
The ruling currency is not "how many providers agree" (that measures SEO
rank) but *structurally verifiable, mostly bidirectional relations* -- see
:mod:`evidence.official_domain_graph`:

- registry -> homepage plus the homepage -> same repository backlink (the
  closure a squatter cannot forge: the real repository never links out);
- Wikidata ``P856`` plus a supporting edge (facade consistency / CT age);
- a domain already trusted for the entity redirecting into the candidate
  (this also captures brand renames, e.g. moonshot.cn -> kimi.com);
- the candidate's certificate SAN set covering another domain of the entity;
- CT-log domain age (squat domains are overwhelmingly young);
- cross-provider search voting, demoted to one weak signal that can never
  rule ``official`` on its own.

What gets cached is the relation graph, not the verdict: ruling rules can be
revised and re-applied to a cached graph offline without re-crawling.
``C-tier`` page self-proof still corroborates a candidate for tracing but
carries no ruling weight -- page-body claims are injectable.

Configured ``pins`` (the former ``official_domains`` map) override everything:
a pinned stem returns ``official`` immediately. Pins skip all validation by
design, so an optional *shadow audit* (``pin_shadow_audit``) re-verifies them
in the background on a long TTL and records disagreements -- a wrong pin is
no longer permanently invisible.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote, urlsplit

import requests

from evidence.official_domain_graph import (
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
from evidence.source_tiering import normalize_entity_stem


_LOGGER = logging.getLogger(__name__)

# Confidence enum values.
OFFICIAL = "official"
CANDIDATE = "candidate"
NONE = "none"

# Signal tiers / kinds.
TIER_A = "structured"
TIER_B = "search_vote"
TIER_C = "self_proof"
TIER_PIN = "pin"

_WELL_KNOWN_SUBDOMAIN_PREFIXES = (
    "docs.", "platform.", "developer.", "open.", "api.", "www.", "dev.",
    "developers.", "documentation.",
)

# Small, stable seed lists. They have different enforcement points: content
# farms cannot become official, hosting platforms require ownership evidence,
# and non-evidence hosts are also removed from normal web evidence.
_DEFAULT_NEVER_OFFICIAL = frozenset(
    {
        "csdn.net", "csdn.com", "zhihu.com", "juejin.cn", "medium.com",
        "cnblogs.com", "reddit.com", "stackoverflow.com", "stackexchange.com",
        "dev.to", "jianshu.com", "cloud.tencent.com", "developer.aliyun.com",
        "huaweicloud.com", "segmentfault.com", "oschina.net", "infoq.cn",
        "infoq.com",
    }
)
_DEFAULT_HOSTING_PLATFORMS = frozenset(
    {
        "github.com", "github.io", "gitlab.com", "gitlab.io", "gitee.com",
        "gitee.io", "readthedocs.io", "gitbook.io", "vercel.app", "pages.dev",
        "notion.site",
    }
)
_DEFAULT_NON_EVIDENCE = frozenset(
    {
        "wikidata.org", "wikipedia.org", "pypi.org", "npmjs.com", "bing.com/search",
        "google.com/search", "baidu.com/s", "duckduckgo.com", "perplexity.ai", "you.com",
        "poe.com", "phind.com",
    }
)
# Compatibility export for callers that used the old single-table name.
_DEFAULT_AGGREGATOR_DENYLIST = (
    _DEFAULT_NEVER_OFFICIAL | _DEFAULT_HOSTING_PLATFORMS | _DEFAULT_NON_EVIDENCE
)

_HTTP_TIMEOUT = 8
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_PYPI_API = "https://pypi.org/pypi/{pkg}/json"
_NPM_API = "https://registry.npmjs.org/{pkg}"
_GITHUB_SEARCH = "https://api.github.com/search/repositories?q={q}&per_page=5"


@dataclass(frozen=True)
class HostPattern:
    """A normalized host with an optional path-prefix ownership boundary."""

    host: str
    path_prefix: str = ""

    @classmethod
    def parse(cls, value: Any) -> Optional["HostPattern"]:
        raw = str(value or "").strip()
        if not raw:
            return None
        candidate = raw if "://" in raw else f"https://{raw.lstrip('/')}"
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            return None
        host = (parsed.hostname or "").casefold().strip(".")
        if not host:
            return None
        path = parsed.path or ""
        if path and path != "/":
            path = "/" + path.lstrip("/")
            path = path.rstrip("/") or "/"
        else:
            path = ""
        return cls(host=host, path_prefix=path)

    @classmethod
    def from_url(cls, value: Any, *, include_path: bool = False) -> Optional["HostPattern"]:
        pattern = cls.parse(value)
        if pattern is None:
            return None
        return pattern if include_path else cls(host=pattern.host)

    def serialize(self) -> str:
        return f"{self.host}{self.path_prefix}"


def host_matches(
    pattern: Any,
    url: Any,
    *,
    allow_subdomains: bool = True,
) -> bool:
    """Match a URL/host against a host-pattern ownership entry.

    Exact hosts always match. A confirmed host may also cover its well-known
    or single-label subdomains, while a path prefix restricts the accepted URL
    to that prefix. This is deliberately string-based and avoids public-suffix
    collapsing on the official-ownership judgement path.
    """
    pattern_value = pattern if isinstance(pattern, HostPattern) else HostPattern.parse(pattern)
    target = HostPattern.parse(url)
    if pattern_value is None or target is None:
        return False
    if target.host == pattern_value.host:
        host_ok = True
    elif allow_subdomains and target.host.endswith("." + pattern_value.host):
        prefix = target.host[: -len(pattern_value.host) - 1]
        host_ok = "." not in prefix or prefix in {
            item.rstrip(".") for item in _WELL_KNOWN_SUBDOMAIN_PREFIXES
        }
    else:
        host_ok = False
    if not host_ok:
        return False
    if not pattern_value.path_prefix:
        return True
    return target.path_prefix == pattern_value.path_prefix or target.path_prefix.startswith(
        pattern_value.path_prefix.rstrip("/") + "/"
    )


def _canonical_ownership_pattern(value: Any) -> str:
    """Collapse conventional service hosts to their registrable owner domain.

    Path-scoped and non-conventional hosts stay exact. This preserves tenant
    boundaries such as ``github.com/org`` and product siblings such as
    ``ai.google.dev`` while preventing cached ``www.``/``docs.``/``api.``
    candidates from becoming stranded from another service subdomain.
    """
    pattern = HostPattern.parse(value)
    if pattern is None or pattern.path_prefix:
        return pattern.serialize() if pattern is not None else ""

    from evidence.source_tiering import registrable_domain

    domain = registrable_domain(pattern.host)
    if not domain or pattern.host == domain:
        return pattern.host
    suffix = "." + domain
    if not pattern.host.endswith(suffix):
        return pattern.host
    prefix = pattern.host[: -len(suffix)]
    known_prefixes = {
        item.rstrip(".") for item in _WELL_KNOWN_SUBDOMAIN_PREFIXES
    }
    if "." not in prefix and prefix in known_prefixes:
        return domain
    return pattern.host


def _host_from_value(value: Any, *, include_path: bool = False) -> str:
    pattern = HostPattern.from_url(value, include_path=include_path)
    return pattern.serialize() if pattern is not None else ""


@dataclass
class Signal:
    """One structural fact contributing to a resolution."""

    kind: str
    source: str
    detail: str = ""
    domain: str = ""
    tier: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "detail": self.detail[:200],
            "domain": self.domain,
            "tier": self.tier,
        }


@dataclass
class Resolution:
    """The resolver's verdict for one entity stem."""

    stem: str
    domain: str = ""
    confidence: str = NONE
    signals: List[Signal] = field(default_factory=list)
    verified_at: float = 0.0
    # Serialized host patterns covered by this resolution. A pin may
    # legitimately cover several hosts (or host/path prefixes).
    domains: List[str] = field(default_factory=list)
    # Suspected aggregators may still be official with a higher evidence bar,
    # but do not receive implicit subdomain acceptance.
    subdomain_allowed: bool = True
    # The relation graph this verdict was ruled from. Cached alongside the
    # verdict so future ruling-rule changes can re-adjudicate offline.
    graph: Optional[RelationGraph] = None

    @property
    def is_official(self) -> bool:
        return self.confidence == OFFICIAL

    @property
    def resolved_domains(self) -> List[str]:
        """All domains this resolution accepts (primary domain as fallback)."""
        return list(self.domains) or ([self.domain] if self.domain else [])

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "stem": self.stem,
            "domain": self.domain,
            "domains": list(self.resolved_domains),
            "confidence": self.confidence,
            "signals": [s.to_dict() for s in self.signals],
            "verified_at": self.verified_at,
            "subdomain_allowed": self.subdomain_allowed,
        }


@dataclass
class ResolverConfig:
    """Resolved (validated) configuration for the official-domain resolver."""

    enabled: bool = False
    min_signals: int = 2
    cache_path: str = "./runtime/official_domains.sqlite"
    cache_ttl_days: int = 30
    negative_ttl_hours: int = 24
    max_discovery_searches: int = 2
    max_verification_fetches: int = 2
    structured_sources: tuple = ("wikidata", "pypi", "npm", "github")
    never_official: frozenset = _DEFAULT_NEVER_OFFICIAL
    hosting_platforms: frozenset = _DEFAULT_HOSTING_PLATFORMS
    non_evidence: frozenset = _DEFAULT_NON_EVIDENCE
    aggregator_min_stems: int = 5
    aggregator_observation_window_days: int = 30
    pins: Dict[str, List[str]] = field(default_factory=dict)
    # A domain must be at least this old (by CT logs) for search votes or
    # Wikidata to count it as supporting evidence of legitimacy.
    min_domain_age_days: int = 180
    # Supplementary structural probes (redirect / cert SAN / facade / CT age /
    # backlink). Disable for hermetic tests or fully offline deployments.
    graph_probes_enabled: bool = True
    # Re-verify pins in the background on a long TTL; never overrides the pin,
    # only records and surfaces disagreements.
    pin_shadow_audit: bool = True
    pin_audit_ttl_days: int = 7

    @property
    def aggregator_denylist(self) -> frozenset:
        """Legacy view of all host tables for compatible diagnostics."""
        return self.never_official | self.hosting_platforms | self.non_evidence

    @property
    def positive_ttl_seconds(self) -> int:
        return max(1, int(self.cache_ttl_days)) * 86400

    @property
    def negative_ttl_seconds(self) -> int:
        return max(1, int(self.negative_ttl_hours)) * 3600

    @property
    def aggregator_observation_window_seconds(self) -> int:
        return max(1, int(self.aggregator_observation_window_days)) * 86400


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "1", "yes", "on"}:
            return True
        if v in {"false", "0", "no", "off"}:
            return False
    return default


def _coerce_int(value: Any, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def _coerce_pins(value: Any) -> Dict[str, List[str]]:
    """Normalize legacy ``official_domains`` and new ``pins`` to host patterns."""
    if not isinstance(value, Mapping):
        return {}
    pins: Dict[str, List[str]] = {}
    for alias, configured in value.items():
        if str(alias).startswith("_"):
            continue
        stem = normalize_entity_stem(alias)
        if not stem:
            continue
        if isinstance(configured, str):
            configured = [configured]
        if not isinstance(configured, Iterable):
            continue
        domains = {
            pattern.serialize()
            for value in configured
            if (pattern := HostPattern.parse(value)) is not None
        }
        if domains:
            pins.setdefault(stem, []).extend(sorted(domains))
    return pins


def _coerce_host_table(value: Any, defaults: frozenset) -> frozenset:
    """Read one list table while preserving the explicit extend/replace mode."""
    mode = "extend"
    domains: Any = []
    if isinstance(value, Mapping):
        raw_mode = str(value.get("mode") or "extend").casefold()
        mode = raw_mode if raw_mode in {"extend", "replace"} else "extend"
        domains = value.get("domains") or []
    elif isinstance(value, Iterable) and not isinstance(value, str):
        domains = value
    if not isinstance(domains, Iterable) or isinstance(domains, str):
        domains = []
    parsed = {
        pattern.serialize()
        for item in domains
        if (pattern := HostPattern.parse(item)) is not None
    }
    return frozenset(parsed if mode == "replace" else set(defaults) | parsed)


def resolver_config_from_mapping(
    config: Any,
    *,
    legacy_official_domains: Any = None,
) -> ResolverConfig:
    """Build :class:`ResolverConfig` from the ``official_domain_resolution`` block.

    ``legacy_official_domains`` (the old ``orchestration.official_domains`` map)
    is folded into ``pins`` so existing aliases keep working unchanged.
    """
    block: Mapping[str, Any] = {}
    if isinstance(config, Mapping):
        block = config
    cfg = ResolverConfig(
        enabled=_coerce_bool(block.get("enabled"), False),
        min_signals=_coerce_int(block.get("min_signals"), 2),
        cache_path=str(block.get("cache_path") or "./runtime/official_domains.sqlite"),
        cache_ttl_days=_coerce_int(block.get("cache_ttl_days"), 30),
        negative_ttl_hours=_coerce_int(block.get("negative_ttl_hours"), 24),
        max_discovery_searches=_coerce_int(block.get("max_discovery_searches"), 2),
        max_verification_fetches=_coerce_int(block.get("max_verification_fetches"), 2),
        aggregator_min_stems=_coerce_int(block.get("aggregator_min_stems"), 5),
        aggregator_observation_window_days=_coerce_int(
            block.get("aggregator_observation_window_days"), 30
        ),
        min_domain_age_days=_coerce_int(block.get("min_domain_age_days"), 180),
        pin_audit_ttl_days=_coerce_int(block.get("pin_audit_ttl_days"), 7),
    )
    cfg.graph_probes_enabled = _coerce_bool(block.get("graph_probes_enabled"), True)
    cfg.pin_shadow_audit = _coerce_bool(block.get("pin_shadow_audit"), True)
    sources = block.get("structured_sources")
    if isinstance(sources, Iterable):
        cfg.structured_sources = tuple(str(s) for s in sources if str(s))
    cfg.never_official = _coerce_host_table(
        block.get("never_official"), _DEFAULT_NEVER_OFFICIAL
    )
    cfg.hosting_platforms = _coerce_host_table(
        block.get("hosting_platforms"), _DEFAULT_HOSTING_PLATFORMS
    )
    cfg.non_evidence = _coerce_host_table(
        block.get("non_evidence"), _DEFAULT_NON_EVIDENCE
    )
    # Legacy bare list continues to extend the never-official table. The new
    # table's replace mode remains authoritative, so it is not silently
    # undermined by an old compatibility key.
    legacy_denylist = block.get("aggregator_denylist")
    if (
        "never_official" not in block
        and isinstance(legacy_denylist, Iterable)
        and not isinstance(legacy_denylist, str)
    ):
        cfg.never_official = _coerce_host_table(
            {"mode": "extend", "domains": legacy_denylist},
            _DEFAULT_NEVER_OFFICIAL,
        )
    pins_block = block.get("pins")
    pins = _coerce_pins(pins_block if pins_block is not None else legacy_official_domains)
    # If both pins and legacy are present, pins take precedence and legacy fills gaps.
    if pins_block is not None and legacy_official_domains is not None:
        for stem, domains in _coerce_pins(legacy_official_domains).items():
            pins.setdefault(stem, []).extend(
                d for d in domains if d not in pins.get(stem, [])
            )
    cfg.pins = pins
    return cfg


class _Cache:
    """SQLite-backed resolution cache with split positive/negative TTLs."""

    def __init__(
        self,
        path: str,
        *,
        positive_ttl: int,
        negative_ttl: int,
        aggregator_min_stems: int,
        observation_window: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.positive_ttl = positive_ttl
        self.negative_ttl = negative_ttl
        self.aggregator_min_stems = aggregator_min_stems
        self.observation_window = observation_window
        self._clock = clock
        self._conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> Optional[sqlite3.Connection]:
        if self._conn is not None:
            return self._conn
        try:
            directory = Path(self.path).expanduser().parent
            directory.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS entity_stem ("
                "  stem TEXT PRIMARY KEY,"
                "  domain TEXT NOT NULL,"
                "  confidence TEXT NOT NULL,"
                "  signals_json TEXT NOT NULL,"
                "  resolved_at REAL NOT NULL"
                ")"
            )
            columns = {
                row[1] for row in self._conn.execute("PRAGMA table_info(entity_stem)")
            }
            if "domains_json" not in columns:
                self._conn.execute(
                    "ALTER TABLE entity_stem ADD COLUMN domains_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "graph_json" not in columns:
                self._conn.execute(
                    "ALTER TABLE entity_stem ADD COLUMN graph_json TEXT NOT NULL DEFAULT ''"
                )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS candidate_observation ("
                "  host TEXT NOT NULL,"
                "  stem TEXT NOT NULL,"
                "  observed_at REAL NOT NULL,"
                "  PRIMARY KEY (host, stem)"
                ")"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS candidate_observation_host_time "
                "ON candidate_observation (host, observed_at)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS pin_audit ("
                "  stem TEXT PRIMARY KEY,"
                "  pinned_json TEXT NOT NULL,"
                "  observed_confidence TEXT NOT NULL,"
                "  observed_domain TEXT NOT NULL,"
                "  agreement INTEGER NOT NULL,"
                "  detail TEXT NOT NULL,"
                "  audited_at REAL NOT NULL"
                ")"
            )
            self._conn.commit()
        except (sqlite3.Error, OSError):
            self._conn = None
            _LOGGER.debug("official-domain cache unavailable at %s", self.path, exc_info=True)
        return self._conn

    def get(self, stem: str, *, ignore_ttl: bool = False) -> Optional[Resolution]:
        conn = self._connect()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT domain, confidence, signals_json, resolved_at, domains_json, graph_json "
                "FROM entity_stem WHERE stem = ?",
                (stem,),
            ).fetchone()
        except sqlite3.Error:
            return None
        if not row:
            return None
        domain, confidence, signals_json, resolved_at, domains_json, graph_json = row
        ttl = self.positive_ttl if confidence != NONE else self.negative_ttl
        if not ignore_ttl and self._clock() - resolved_at > ttl:
            return None
        try:
            signals = [Signal(**s) for s in json.loads(signals_json)]
        except (ValueError, TypeError):
            signals = []
        try:
            domains = [
                pattern.serialize()
                for item in json.loads(domains_json or "[]")
                if (pattern := HostPattern.parse(item)) is not None
            ]
        except (ValueError, TypeError):
            domains = []
        graph = RelationGraph.from_json(graph_json) if graph_json else None
        domain = _canonical_ownership_pattern(domain)
        domains = [
            canonical
            for item in domains
            if (canonical := _canonical_ownership_pattern(item))
        ]
        domains = list(dict.fromkeys(domains))
        return Resolution(
            stem=stem,
            domain=domain,
            confidence=confidence,
            signals=signals,
            verified_at=resolved_at,
            domains=domains,
            subdomain_allowed=not self.is_suspected_aggregator(domain),
            graph=graph,
        )

    def put(self, resolution: Resolution) -> None:
        resolution.domain = _canonical_ownership_pattern(resolution.domain)
        resolution.domains = list(
            dict.fromkeys(
                canonical
                for item in resolution.resolved_domains
                if (canonical := _canonical_ownership_pattern(item))
            )
        )
        conn = self._connect()
        if conn is None:
            return
        try:
            conn.execute(
                "INSERT OR REPLACE INTO entity_stem "
                "(stem, domain, confidence, signals_json, resolved_at, domains_json, graph_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    resolution.stem,
                    resolution.domain,
                    resolution.confidence,
                    json.dumps([s.to_dict() for s in resolution.signals]),
                    resolution.verified_at or time.time(),
                    json.dumps(resolution.resolved_domains),
                    resolution.graph.to_json() if resolution.graph is not None else "",
                ),
            )
            conn.commit()
        except sqlite3.Error:
            _LOGGER.debug("official-domain cache write failed for %s", resolution.stem, exc_info=True)

    def delete(self, stem: str) -> None:
        conn = self._connect()
        if conn is None:
            return
        try:
            conn.execute("DELETE FROM entity_stem WHERE stem = ?", (stem,))
            conn.commit()
        except sqlite3.Error:
            _LOGGER.debug("official-domain cache delete failed for %s", stem, exc_info=True)

    def delete_normalized_aliases(self, canonical_stem: str) -> int:
        """Remove stale cache keys that now normalize to ``canonical_stem``."""
        conn = self._connect()
        if conn is None:
            return 0
        try:
            stale = [
                str(row[0])
                for row in conn.execute("SELECT stem FROM entity_stem").fetchall()
                if str(row[0]) != canonical_stem
                and normalize_entity_stem(row[0]) == canonical_stem
            ]
            if stale:
                conn.executemany(
                    "DELETE FROM entity_stem WHERE stem = ?",
                    [(stem,) for stem in stale],
                )
                conn.commit()
            return len(stale)
        except sqlite3.Error:
            _LOGGER.debug(
                "official-domain alias cleanup failed for %s",
                canonical_stem,
                exc_info=True,
            )
            return 0

    def record_candidate_observations(self, stem: str, hosts: Iterable[str]) -> None:
        """Best-effort upsert of one B-tier observation per host/stem pair."""
        conn = self._connect()
        if conn is None:
            return
        now = self._clock()
        rows = [
            (pattern.host, stem, now)
            for value in set(hosts)
            if (pattern := HostPattern.parse(value)) is not None
        ]
        if not rows:
            return
        try:
            conn.executemany(
                "INSERT INTO candidate_observation (host, stem, observed_at) VALUES (?, ?, ?) "
                "ON CONFLICT(host, stem) DO UPDATE SET observed_at = excluded.observed_at",
                rows,
            )
            conn.commit()
        except sqlite3.Error:
            _LOGGER.debug("candidate observation write failed for %s", stem, exc_info=True)

    def get_pin_audit(self, stem: str, *, ttl: int) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT pinned_json, observed_confidence, observed_domain, agreement, "
                "detail, audited_at FROM pin_audit WHERE stem = ?",
                (stem,),
            ).fetchone()
        except sqlite3.Error:
            return None
        if not row:
            return None
        pinned_json, confidence, domain, agreement, detail, audited_at = row
        if self._clock() - audited_at > ttl:
            return None
        try:
            pinned = list(json.loads(pinned_json or "[]"))
        except (ValueError, TypeError):
            pinned = []
        return {
            "stem": stem,
            "pinned": pinned,
            "observed_confidence": confidence,
            "observed_domain": domain,
            "agreement": int(agreement),
            "detail": detail,
            "audited_at": audited_at,
        }

    def put_pin_audit(
        self,
        stem: str,
        *,
        pinned: Sequence[str],
        observed_confidence: str,
        observed_domain: str,
        agreement: int,
        detail: str,
    ) -> None:
        conn = self._connect()
        if conn is None:
            return
        try:
            conn.execute(
                "INSERT OR REPLACE INTO pin_audit "
                "(stem, pinned_json, observed_confidence, observed_domain, agreement, "
                "detail, audited_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    stem,
                    json.dumps(list(pinned)),
                    observed_confidence,
                    observed_domain,
                    int(agreement),
                    (detail or "")[:500],
                    self._clock(),
                ),
            )
            conn.commit()
        except sqlite3.Error:
            _LOGGER.debug("pin audit write failed for %s", stem, exc_info=True)

    def is_suspected_aggregator(self, value: Any) -> bool:
        pattern = HostPattern.parse(value)
        conn = self._connect()
        if pattern is None or conn is None:
            return False
        try:
            row = conn.execute(
                "SELECT COUNT(DISTINCT stem) FROM candidate_observation "
                "WHERE host = ? AND observed_at >= ?",
                (pattern.host, self._clock() - self.observation_window),
            ).fetchone()
            return bool(row and int(row[0]) >= self.aggregator_min_stems)
        except sqlite3.Error:
            return False


class OfficialDomainResolver:
    """Discover -> verify -> cache official domains for entity stems.

    The resolver is constructed once from validated config and a set of optional
    search/fetch clients. :meth:`resolve` is the only public entry point.
    """

    def __init__(
        self,
        config: ResolverConfig,
        *,
        search_clients: Optional[Sequence[Any]] = None,
        fetch_client: Optional[Any] = None,
        probers: Optional[GraphProbers] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self._search_clients = [c for c in (search_clients or []) if c is not None]
        # Build a zero-config DirectFetchClient only if a fetch client is needed
        # and none was supplied. Imported lazily to avoid an import cycle.
        self._fetch_client = fetch_client
        if probers is not None:
            self._probers = probers
        elif config.graph_probes_enabled:
            self._probers = GraphProbers().with_defaults()
        else:
            self._probers = GraphProbers.disabled()
        self._cache = _Cache(
            config.cache_path,
            positive_ttl=config.positive_ttl_seconds,
            negative_ttl=config.negative_ttl_seconds,
            aggregator_min_stems=config.aggregator_min_stems,
            observation_window=config.aggregator_observation_window_seconds,
            clock=clock,
        )
        self._clock = clock

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def pins_for(self, stem: str) -> List[str]:
        """Return pinned host patterns for a stem (empty if not pinned)."""
        return list(self.config.pins.get(normalize_entity_stem(stem), []))

    @staticmethod
    def _matches_table(value: Any, table: Iterable[str]) -> bool:
        return any(host_matches(pattern, value) for pattern in table)

    def is_never_official(self, value: Any) -> bool:
        return self._matches_table(value, self.config.never_official)

    def is_hosting_platform(self, value: Any) -> bool:
        return self._matches_table(value, self.config.hosting_platforms)

    def is_non_evidence(self, value: Any) -> bool:
        return self._matches_table(value, self.config.non_evidence)

    def is_denied(self, value: Any) -> bool:
        """Compatibility predicate for hosts that cannot win ordinary B votes."""
        return (
            self.is_never_official(value)
            or self.is_hosting_platform(value)
            or self.is_non_evidence(value)
        )

    def is_suspected_aggregator(self, value: Any) -> bool:
        return self._cache.is_suspected_aggregator(value)

    def resolve(self, entity: Any) -> Resolution:
        """Resolve one entity label to a :class:`Resolution`."""
        stem = normalize_entity_stem(entity)
        now = self._clock()
        if not stem:
            return Resolution(stem="", confidence=NONE, verified_at=now)

        # Pinned hosts win outright with zero network I/O, including any that
        # happen to appear in one of the three reputation tables. The pin is
        # never overridden -- but the shadow audit re-verifies it in the
        # background so a wrong or expired pin is visible instead of silent.
        pinned = self.config.pins.get(stem)
        if pinned:
            signals = [
                Signal(
                    kind="pin",
                    source="config",
                    detail=f"pinned: {', '.join(pinned)}",
                    domain=pinned[0],
                    tier=TIER_PIN,
                )
            ]
            audit = self._maybe_shadow_audit(stem, pinned, now)
            if audit and audit.get("agreement") == 0:
                signals.append(
                    Signal(
                        kind="pin_shadow_disagreement",
                        source="shadow_audit",
                        detail=str(audit.get("detail") or ""),
                        domain=pinned[0],
                        tier=TIER_PIN,
                    )
                )
            resolution = Resolution(
                stem=stem,
                domain=pinned[0],
                domains=list(pinned),
                confidence=OFFICIAL,
                signals=signals,
                verified_at=now,
            )
            cached_pin_target = self._cache.get(stem, ignore_ttl=True)
            aliases_deleted = self._cache.delete_normalized_aliases(stem)
            if cached_pin_target is not None or aliases_deleted:
                self._cache.put(resolution)
            return resolution

        if not self.config.enabled:
            return Resolution(stem=stem, confidence=NONE, verified_at=now)

        cached = self._cache.get(stem)
        if cached is not None and any(
            signal.kind == "pin" for signal in cached.signals
        ):
            self._cache.delete(stem)
            cached = None
        if cached is not None:
            return self._readjudicate(cached)

        # A previously verified (now expired) domain becomes a trusted input:
        # if it now redirects elsewhere, that rename chain is strong evidence.
        stale = self._cache.get(stem, ignore_ttl=True)
        known_hosts = (
            list(stale.resolved_domains)
            if stale is not None and stale.confidence == OFFICIAL
            else []
        )
        resolution = self._discover(stem, str(entity).strip(), now, known_hosts=known_hosts)
        self._cache.put(resolution)
        return resolution

    def _readjudicate(self, cached: Resolution) -> Resolution:
        """Re-rule a cached resolution from its cached relation graph.

        The cache stores the graph, not the verdict: ruling rules may change
        (thresholds, new edge kinds) and every cached graph must be
        re-decidable offline without re-crawling.
        """
        if cached.graph is None:
            return cached
        confidence, domain = self._rule(cached.graph)
        domain = _canonical_ownership_pattern(domain)
        if confidence == cached.confidence and domain == cached.domain:
            return cached
        domains = [domain] if domain else []
        suspected = bool(domain) and self.is_suspected_aggregator(domain)
        return Resolution(
            stem=cached.stem,
            domain=domain,
            domains=domains,
            confidence=confidence,
            signals=cached.signals,
            verified_at=cached.verified_at,
            subdomain_allowed=not suspected,
            graph=cached.graph,
        )

    def _rule(self, graph: RelationGraph) -> Tuple[str, str]:
        return decide_graph(
            graph,
            min_signals=self.config.min_signals,
            suspected_aggregator=self.is_suspected_aggregator,
        )

    def adjudicate(self, entity: Any, graph: RelationGraph) -> Resolution:
        """Merge an externally produced graph (e.g. agent discovery) and rule it.

        The agent only discovers candidates and fills the graph; this method is
        the deterministic ruling boundary. The merged graph is cached so the
        verdict is re-decidable later.
        """
        stem = normalize_entity_stem(entity)
        now = self._clock()
        if not stem or graph is None:
            return Resolution(stem=stem, confidence=NONE, verified_at=now)
        cached = self._cache.get(stem, ignore_ttl=True)
        if cached is not None and cached.graph is not None:
            graph = cached.graph.merge(graph)
        confidence, domain = self._rule(graph)
        suspected = bool(domain) and self.is_suspected_aggregator(domain)
        resolution = Resolution(
            stem=stem,
            domain=domain,
            domains=[domain] if domain else [],
            confidence=confidence,
            signals=(cached.signals if cached is not None else []),
            verified_at=now,
            subdomain_allowed=not suspected,
            graph=graph,
        )
        self._cache.put(resolution)
        return resolution

    # -- pin shadow audit ---------------------------------------------------

    def _maybe_shadow_audit(
        self,
        stem: str,
        pinned: Sequence[str],
        now: float,
    ) -> Optional[Dict[str, Any]]:
        """Re-verify a pin without ever overriding it; record disagreements.

        Runs at most once per ``pin_audit_ttl_days`` per stem, only when the
        resolver is enabled (the audit reuses the discovery machinery). A
        rename is captured when a pinned host redirects into the domain the
        discovery independently rules official -- that still counts as
        agreement, with the new domain noted in the detail.
        """
        if not self.config.pin_shadow_audit or not self.config.enabled:
            return None
        ttl = self.config.pin_audit_ttl_days * 86400
        fresh = self._cache.get_pin_audit(stem, ttl=ttl)
        if fresh is not None:
            return fresh
        try:
            observed = self._discover(stem, stem, now, known_hosts=list(pinned))
        except Exception:  # noqa: BLE001 - the audit must never break the pin
            _LOGGER.debug("pin shadow audit failed for %s", stem, exc_info=True)
            return None
        observed_domain = observed.domain if observed.is_official else ""
        pinned_hit = bool(observed_domain) and any(
            host_matches(pattern, observed_domain) for pattern in pinned
        )
        redirect_hit = bool(observed_domain) and any(
            edge.kind == EDGE_REDIRECT_INTO and edge.object == observed_domain
            for edge in (observed.graph.edges if observed.graph is not None else [])
        )
        if observed_domain and (pinned_hit or redirect_hit):
            agreement = 1
        elif observed_domain:
            agreement = 0
        else:
            agreement = -1
        if redirect_hit and not pinned_hit:
            detail = f"pin redirects to {observed_domain} (possible rename)"
        elif agreement == 0:
            detail = (
                f"pinned {', '.join(pinned)} but discovery ruled {observed_domain} official"
            )
        elif agreement == 1:
            detail = "pin confirmed by shadow discovery"
        else:
            detail = "shadow discovery inconclusive"
        self._cache.put_pin_audit(
            stem,
            pinned=pinned,
            observed_confidence=observed.confidence,
            observed_domain=observed_domain,
            agreement=agreement,
            detail=detail,
        )
        if agreement == 0:
            _LOGGER.warning(
                "official-domain pin for %r failed shadow audit: %s", stem, detail
            )
        return self._cache.get_pin_audit(stem, ttl=ttl)

    def pin_audit_for(self, entity: Any) -> Optional[Dict[str, Any]]:
        """Return the latest shadow-audit record for a pinned stem, if any."""
        stem = normalize_entity_stem(entity)
        if not stem:
            return None
        return self._cache.get_pin_audit(
            stem, ttl=self.config.pin_audit_ttl_days * 86400
        )

    # -- discovery ---------------------------------------------------------

    def _discover(
        self,
        stem: str,
        label: str,
        now: float,
        *,
        known_hosts: Optional[Sequence[str]] = None,
    ) -> Resolution:
        signals: List[Signal] = []
        candidate_hosts: Dict[str, List[Signal]] = {}
        seen_keys: set = set()
        graph = RelationGraph(stem=stem)

        def add(signal: Signal) -> None:
            # Independence is measured per (tier, source, domain). A single
            # provider voting across multiple query variants would otherwise
            # append duplicate signals and inflate both ranking and the
            # independence count, so collapse repeats here.
            host_pattern = HostPattern.parse(signal.domain)
            host = host_pattern.serialize() if host_pattern is not None else ""
            key = (signal.tier, signal.source, host)
            if key in seen_keys:
                return
            seen_keys.add(key)
            signals.append(signal)
            if host:
                candidate_hosts.setdefault(host, []).append(signal)
            if host and signal.tier == TIER_A:
                kind = (
                    EDGE_WIKIDATA_P856
                    if signal.kind == "wikidata_p856"
                    else EDGE_REGISTRY_HOMEPAGE
                )
                graph.add(
                    RelationEdge(
                        kind=kind,
                        subject=signal.source,
                        object=host,
                        detail=signal.detail,
                        observed_at=now,
                    )
                )
            elif host and signal.tier == TIER_B:
                graph.add(
                    RelationEdge(
                        kind=EDGE_SEARCH_VOTE,
                        subject=signal.source,
                        object=host,
                        detail=signal.detail,
                        observed_at=now,
                    )
                )

        # A-tier: structured registry fields, plus the repository each
        # registry entry names, so the homepage -> repo backlink can close
        # the loop bidirectionally.
        repo_targets: Dict[str, str] = {}
        for source in self.config.structured_sources:
            probe = self._probe_structured(source, stem, label)
            if probe is None:
                continue
            signal, repo_url = probe
            add(signal)
            host = _host_from_value(signal.domain)
            if repo_url and host:
                repo_targets.setdefault(host, repo_url)
        for host, repo_url in repo_targets.items():
            self._close_backlink(graph, host, repo_url, now)

        confidence, domain = self._rule(graph)
        if confidence == OFFICIAL:
            suspected = self.is_suspected_aggregator(domain)
            return Resolution(
                stem=stem,
                domain=domain,
                domains=[domain],
                confidence=OFFICIAL,
                signals=signals,
                verified_at=now,
                subdomain_allowed=not suspected,
                graph=graph,
            )

        # B-tier: cross-provider search voting. A weak signal on the graph;
        # it can corroborate but never rule official on its own. Each query
        # variant queries all available providers once; the budget bounds the
        # number of variants.
        query_variants = self._discovery_queries(label)
        searches_left = self.config.max_discovery_searches
        for query in query_variants:
            if searches_left <= 0 or not self._search_clients:
                break
            searches_left -= 1
            voted = self._search_vote(query, limit=5)
            for host, providers in voted.items():
                # Each independent provider that surfaced this domain is a
                # distinct B-tier signal.
                for provider in providers:
                    add(
                        Signal(
                            kind="search_provider",
                            source=provider,
                            detail=f"top hit host: {host}",
                            domain=host,
                            tier=TIER_B,
                        )
                    )
            # Stop once we have enough independent signals to decide.
            if self._has_independent_signals(candidate_hosts):
                break

        self._cache.record_candidate_observations(
            stem,
            (
                host
                for host, sigs in candidate_hosts.items()
                if any(signal.tier == TIER_B for signal in sigs)
            ),
        )

        # Supplementary structural probes on the leading candidates: CT-log
        # age, facade consistency, certificate SAN coverage, and redirects
        # from already-trusted domains (rename capture).
        self._augment_graph(graph, candidate_hosts, known_hosts or [], now)

        # C-tier: self-proof via direct fetch of the leading candidate hosts.
        # Trace-only corroboration; page-body claims are injectable and carry
        # no ruling weight.
        fetches_left = self.config.max_verification_fetches
        for host in list(candidate_hosts.keys()):
            if fetches_left <= 0:
                break
            fetches_left -= 1
            signal = self._self_proof(stem, label, host)
            if signal is not None:
                add(signal)

        return self._verdict(stem, graph, candidate_hosts, signals, now)

    def _verdict(
        self,
        stem: str,
        graph: RelationGraph,
        candidate_hosts: Dict[str, List[Signal]],
        signals: List[Signal],
        now: float,
    ) -> Resolution:
        confidence, domain = self._rule(graph)
        if not graph.edges:
            return Resolution(
                stem=stem, confidence=NONE, signals=signals, verified_at=now, graph=graph
            )
        suspected = bool(domain) and self.is_suspected_aggregator(domain)
        return Resolution(
            stem=stem,
            domain=domain,
            domains=[domain] if domain else [],
            confidence=confidence,
            signals=list(candidate_hosts.get(domain) or signals),
            verified_at=now,
            subdomain_allowed=not suspected,
            graph=graph,
        )

    def _close_backlink(
        self,
        graph: RelationGraph,
        host: str,
        repo_url: str,
        now: float,
    ) -> None:
        """Verify the candidate page links back to the registry's repository.

        A verified backlink closes the bidirectional loop; an absent backlink
        (the fetch ran) is an active contradiction; a failed fetch records
        nothing (inconclusive never contradicts).
        """
        pattern = HostPattern.parse(host)
        if pattern is None:
            return
        try:
            found, matched = self._probers.backlink(
                f"https://{pattern.host}/", repo_url
            )
        except Exception:  # noqa: BLE001
            return
        if found is None:
            return
        repo_target = _host_from_value(repo_url, include_path=True) or repo_url
        graph.add(
            RelationEdge(
                kind=EDGE_HOMEPAGE_REPO_BACKLINK,
                subject=repo_target,
                object=host,
                detail=(
                    f"backlink -> {matched}" if found else "no backlink to repo found"
                ),
                verified=bool(found),
                observed_at=now,
            )
        )

    def _augment_graph(
        self,
        graph: RelationGraph,
        candidate_hosts: Dict[str, List[Signal]],
        known_hosts: Sequence[str],
        now: float,
    ) -> None:
        ordered = sorted(
            candidate_hosts.items(),
            key=lambda kv: self._independent_source_count(kv[1]),
            reverse=True,
        )
        budget = max(1, int(self.config.max_verification_fetches))
        related = list(candidate_hosts) + [h for h in known_hosts if h]
        for host, _sigs in ordered[:budget]:
            for produce in (
                lambda: self._probers.ct_age(
                    host, min_age_days=self.config.min_domain_age_days
                ),
                lambda: self._probers.facade(host),
                lambda: self._probers.cert_san(host, related),
                lambda: self._probers.redirect(host, known_hosts),
            ):
                try:
                    for edge in produce() or []:
                        graph.add(edge)
                except Exception:  # noqa: BLE001 - probes are best-effort
                    _LOGGER.debug("graph probe failed for %s", host, exc_info=True)

    @staticmethod
    def _discovery_queries(label: str) -> List[str]:
        """Bounded set of discovery query phrasings for cross-provider voting."""
        label = (label or "").strip()
        if not label:
            return []
        return [
            f"{label} official site",
            f"{label} 官网",
            f"{label} docs",
        ][:2]

    @staticmethod
    def _independent_source_count(sigs: List[Signal]) -> int:
        """Count mutually independent sources backing a candidate.

        A-tier and B-tier each contribute one independent source per distinct
        ``source`` (registry / search provider). C-tier (direct-fetch
        self-proof) is intentionally excluded: it only verifies a domain that
        B-tier surfaced, so it is not independent of B and must never help
        a single provider cross the threshold.
        """
        a_sources = {s.source for s in sigs if s.tier == TIER_A and s.source}
        b_sources = {s.source for s in sigs if s.tier == TIER_B and s.source}
        return len(a_sources) + len(b_sources)

    def _has_independent_signals(
        self,
        candidates: Dict[str, List[Signal]],
    ) -> bool:
        """True when a candidate has its required independent-source count.

        See :meth:`_independent_source_count` for the independence model;
        C-tier self-proof corroborates but does not count. A behaviorally
        suspected aggregator needs one extra B-tier source.
        """
        return any(
            self._independent_source_count(sigs)
            >= self.config.min_signals + (1 if self.is_suspected_aggregator(host) else 0)
            for host, sigs in candidates.items()
        )

    # -- A-tier structured probes -----------------------------------------
    # Each probe returns ``(signal, repo_url)``: the structured homepage fact
    # plus the repository the registry entry names (when available), so the
    # homepage -> repo backlink can close the bidirectional loop.

    def _probe_structured(
        self, source: str, stem: str, label: str
    ) -> Optional[Tuple[Signal, str]]:
        try:
            if source == "wikidata":
                return self._probe_wikidata(stem, label)
            if source == "pypi":
                return self._probe_pypi(stem, label)
            if source == "npm":
                return self._probe_npm(stem, label)
            if source == "github":
                return self._probe_github(stem, label)
        except Exception:  # noqa: BLE001 - structured probes are best-effort
            _LOGGER.debug("structured probe %s failed for %s", source, label, exc_info=True)
        return None

    def _probe_wikidata(self, stem: str, label: str) -> Optional[Tuple[Signal, str]]:
        params = {
            "action": "wbsearchentities",
            "search": label,
            "language": "en",
            "format": "json",
            "limit": "1",
        }
        resp = requests.get(_WIKIDATA_API, params=params, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        search = (data.get("search") or [{}])[0]
        qid = search.get("concepturi") or search.get("id")
        if not qid:
            return None
        qid_only = qid.rsplit("/", 1)[-1]
        ent = requests.get(
            _WIKIDATA_API,
            params={"action": "wbgetentities", "ids": qid_only, "props": "claims", "format": "json"},
            timeout=_HTTP_TIMEOUT,
        )
        ent.raise_for_status()
        claims = (ent.json().get("entities") or {}).get(qid_only, {}).get("claims") or {}
        for claim in claims.get("P856") or []:
            value = (claim.get("mainsnak") or {}).get("datavalue", {}).get("value")
            # P856 datavalue is a string URL across API versions; tolerate the
            # rare object form {"id": url} too.
            if isinstance(value, str):
                url = value
            elif isinstance(value, Mapping):
                url = value.get("id") or value.get("value") or ""
            else:
                url = ""
            if url:
                host = _host_from_value(url)
                if host:
                    return Signal(
                        kind="wikidata_p856",
                        source="wikidata",
                        detail=f"{qid_only} P856 -> {url}",
                        domain=host,
                        tier=TIER_A,
                    ), ""
        return None

    def _probe_pypi(self, stem: str, label: str) -> Optional[Tuple[Signal, str]]:
        pkg = self._package_guess(label)
        if not pkg:
            return None
        resp = requests.get(_PYPI_API.format(pkg=pkg), timeout=_HTTP_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        info = (resp.json().get("info") or {})
        repo_url = ""
        project_urls = info.get("project_urls") or {}
        if isinstance(project_urls, Mapping):
            for value in project_urls.values():
                text = str(value or "")
                if any(marker in text for marker in ("github.com", "gitlab.com", "gitee.com")):
                    repo_url = text
                    break
        for key in ("home_page",):
            url = info.get(key)
            if url:
                host = _host_from_value(url)
                if host:
                    return Signal(
                        kind="pypi_home_page",
                        source="pypi",
                        detail=f"{pkg} home_page -> {url}",
                        domain=host,
                        tier=TIER_A,
                    ), repo_url
        return None

    def _probe_npm(self, stem: str, label: str) -> Optional[Tuple[Signal, str]]:
        pkg = self._package_guess(label)
        if not pkg:
            return None
        resp = requests.get(_NPM_API.format(pkg=pkg), timeout=_HTTP_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        body = resp.json()
        repo_url = ""
        if isinstance(body, Mapping):
            repository = body.get("repository")
            if isinstance(repository, Mapping):
                repository = repository.get("url")
            if isinstance(repository, str):
                repo_url = re.sub(r"^git\+", "", repository).removesuffix(".git")
        url = body.get("homepage") if isinstance(body, Mapping) else None
        if url and isinstance(url, str):
            host = _host_from_value(url)
            if host:
                return Signal(
                    kind="npm_homepage",
                    source="npm",
                    detail=f"{pkg} homepage -> {url}",
                    domain=host,
                    tier=TIER_A,
                ), repo_url
        return None

    def _probe_github(self, stem: str, label: str) -> Optional[Tuple[Signal, str]]:
        q = quote(label)
        resp = requests.get(
            _GITHUB_SEARCH.format(q=q),
            headers={"Accept": "application/vnd.github+json"},
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        # Search can rank an unrelated popular repository first. Inspect the
        # bounded result set for an owner or repository name matching the
        # requested entity before treating a hosting-platform URL as owned.
        items = (resp.json().get("items") or [])[:5]
        for item in items:
            full_name = str(item.get("full_name") or "")
            owner = str((item.get("owner") or {}).get("login") or full_name.split("/", 1)[0])
            repo_name = str(item.get("name") or full_name.rsplit("/", 1)[-1])
            repo_matches_entity = stem in {
                normalize_entity_stem(owner),
                normalize_entity_stem(repo_name),
            }
            if not repo_matches_entity:
                continue
            homepage = item.get("homepage")
            if homepage and isinstance(homepage, str):
                host = _host_from_value(homepage)
                if host:
                    # The registry names both a homepage and the repository;
                    # the homepage -> repo backlink can close the loop.
                    return Signal(
                        kind="github_repo",
                        source="github",
                        detail=f"{full_name} homepage -> {homepage}",
                        domain=host,
                        tier=TIER_A,
                    ), str(item.get("html_url") or "")
            url = item.get("html_url")
            if url and isinstance(url, str):
                # A matching repository establishes ownership of its own
                # GitHub path. Preserve that path so github.com does not
                # bless unrelated repos.
                host = _host_from_value(url, include_path=True)
                if host:
                    return Signal(
                        kind="github_repo",
                        source="github",
                        detail=f"{full_name} html_url -> {url}",
                        domain=host,
                        tier=TIER_A,
                    ), ""
        return None

    @staticmethod
    def _package_guess(label: str) -> str:
        text = re.sub(r"[^a-z0-9._-]+", "", label.casefold())
        return text[:80]

    # -- B-tier search voting ---------------------------------------------

    def _search_vote(self, query: str, *, limit: int) -> Dict[str, List[str]]:
        """Run one discovery query across available providers and tally hosts.

        Returns ``host -> [provider source_id, ...]``. Each
        provider is consulted once; providers that error are skipped.
        """
        voted: Dict[str, List[str]] = {}
        for client in self._search_clients:
            try:
                # Ask for headroom then keep only the first ``limit`` valid
                # candidates. List-suppressed results must not consume the
                # B-tier top-N budget.
                hits = client.search(query, num_results=max(limit * 3, limit))
            except Exception:  # noqa: BLE001 - a failed provider must not block voting
                _LOGGER.debug("search vote failed for %s", query, exc_info=True)
                continue
            seen_for_provider: set = set()
            source_id = getattr(client, "source_id", "search")
            accepted = 0
            for hit in hits or []:
                url = getattr(hit, "url", "") or (hit.get("url") if isinstance(hit, Mapping) else "")
                host = _host_from_value(url)
                if not host or self.is_denied(host) or host in seen_for_provider:
                    continue
                seen_for_provider.add(host)
                voted.setdefault(host, []).append(source_id)
                accepted += 1
                if accepted >= limit:
                    break
        return voted

    # -- C-tier self-proof ------------------------------------------------

    def _self_proof(self, stem: str, label: str, host: str) -> Optional[Signal]:
        client = self._fetch_client or self._build_fetch_client()
        if client is None:
            return None
        url = f"https://{host.lstrip('/')}"
        try:
            extraction = client.extract([url])
        except Exception:  # noqa: BLE001
            return None
        content = next(iter(extraction.contents or []), None)
        if content is None:
            return None
        final_host = _host_from_value(content.url or url)
        if not final_host:
            return None
        title = (content.title or "").casefold()
        body_head = (content.content or "")[:2000].casefold()
        # Match the page against the raw (NFKC + casefolded) stem/label so CJK
        # and accented brand names are recognized; the stem is already
        # separator-free, and stripping non-ASCII here would erase CJK entirely.
        stem_token = unicodedata.normalize("NFKC", str(stem)).casefold()
        label_token = unicodedata.normalize("NFKC", str(label)).casefold()
        mentions = bool(stem_token) and (stem_token in title or stem_token in body_head)
        mentions = mentions or (
            bool(label_token) and (label_token in title or label_token in body_head)
        )
        # Self-proof requires the post-redirect host to be the candidate (or a
        # subdomain of it) AND the page to reference the entity stem. This
        # rejects typosquats and dead domains.
        if mentions and host_matches(host, content.url or url):
            return Signal(
                kind="fetch_self_proof",
                source="direct_fetch",
                detail=f"redirect -> {content.url or url}; title={content.title or ''}",
                domain=host,
                tier=TIER_C,
            )
        return None

    def _build_fetch_client(self) -> Any:
        if self._fetch_client is not None:
            return self._fetch_client
        try:
            from search.reference_fetch import DirectFetchClient

            self._fetch_client = DirectFetchClient(timeout=_HTTP_TIMEOUT)
            return self._fetch_client
        except Exception:  # noqa: BLE001
            return None


def is_subdomain_of(host: str, registrable: str) -> bool:
    """Backward-compatible wrapper for the explicit host matcher."""
    return host_matches(registrable, host)


def build_official_domain_resolver(
    config: Any,
    *,
    search_clients: Optional[Sequence[Any]] = None,
    fetch_client: Optional[Any] = None,
    probers: Optional[GraphProbers] = None,
) -> Optional[OfficialDomainResolver]:
    """Construct a resolver from the raw config mapping, or ``None`` if absent.

    ``config`` is the ``orchestration`` block. Both the new
    ``official_domain_resolution`` sub-block and the legacy ``official_domains``
    map are honored.
    """
    if not isinstance(config, Mapping):
        return None
    resolution_block = config.get("official_domain_resolution")
    legacy = config.get("official_domains")
    if resolution_block is None and legacy is None:
        return None
    resolved = resolver_config_from_mapping(
        resolution_block if isinstance(resolution_block, Mapping) else {},
        legacy_official_domains=legacy,
    )
    return OfficialDomainResolver(
        resolved,
        search_clients=search_clients,
        fetch_client=fetch_client,
        probers=probers,
    )
