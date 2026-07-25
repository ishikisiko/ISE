## ADDED Requirements

### Requirement: System SHALL resolve an entity to an official domain via structural signals only
The system SHALL expose a single resolver entry point that maps an entity stem to a `Resolution` value object containing `domain`, `confidence`, an ordered `signals` list, and `verified_at`. The resolver SHALL assemble confidence exclusively from structural signals — structured registry fields (Wikidata `P856`, PyPI `home_page`, npm `homepage`, GitHub repo homepage), cross-provider search voting collapsed to the registrable domain, `DirectFetchClient` redirect-chain / `<title>` / `og:site_name` / TLS-host self-verification, an aggregator denylist, and well-known-subdomain acceptance. The resolver SHALL NOT use LLM output that asserts what an entity's official site is, because model memory and page-body claims are injectable sources of domain hallucination.

#### Scenario: Unknown entity resolves via two independent search providers
- **WHEN** the resolver is asked for an entity that has no pin and no cache entry
- **AND** two independent search providers return top hits that collapse to the same registrable domain
- **THEN** the resolver SHALL return that domain with `confidence="official"` and at least two `signals` whose `kind` records the independent providers

#### Scenario: Single weak signal does not satisfy official
- **WHEN** exactly one non-structured signal supports a candidate domain
- **THEN** the resolver SHALL return `confidence="candidate"` and SHALL NOT return `confidence="official"`

#### Scenario: Structured registry field alone is sufficient
- **WHEN** a structured A-tier source (e.g. PyPI `home_page`) returns a registrable domain for the entity
- **THEN** the resolver SHALL return that domain with `confidence="official"` and a `signals` entry whose `kind` identifies the structured source

#### Scenario: Aggregator domain is never official
- **WHEN** the only candidate domain for an entity is on the configured aggregator denylist
- **THEN** the resolver SHALL return `confidence="none"` regardless of how many sources pointed at it

#### Scenario: Page-body claim is rejected
- **WHEN** a fetched page body asserts "this is the official site of <entity>"
- **AND** no structural signal (registry field, redirect chain, certificate, or cross-provider vote) corroborates it
- **THEN** the resolver SHALL NOT return that domain as `official` or `candidate`

### Requirement: Pinned domains SHALL override discovery with highest priority
The system SHALL read configured `pins` (`{stem: [domains]}`) and return a pinned stem's domains as `confidence="official"` with a `signal` of `kind="pin"`, without performing any network discovery. A pin SHALL override any contradictory cached or discovered result for the same stem.

#### Scenario: Pinned stem skips discovery
- **WHEN** an entity stem matches a configured pin
- **THEN** the resolver SHALL return the pinned domain as `official` immediately
- **AND** the resolver SHALL NOT issue any search or fetch request for that stem

#### Scenario: Pin overrides a stale cached result
- **WHEN** a cache entry for a stem disagrees with a configured pin
- **THEN** the pin SHALL win and the returned `signals` SHALL include a `kind="pin"` entry

### Requirement: Resolver SHALL persist resolutions with split positive/negative TTLs
The system SHALL persist resolutions in a SQLite store keyed by entity stem, recording `domain`, `confidence`, serialized `signals`, and `resolved_at`. Positive resolutions (`official`/`candidate`) SHALL be served from cache until `cache_ttl_days` (default 30) elapses; negative resolutions (`none`) SHALL be served until `negative_ttl_hours` (default 24) elapses. Cache read/write failures SHALL degrade to live resolution and SHALL NOT abort the query.

#### Scenario: Hot entity is served from cache
- **WHEN** the resolver is asked for a stem whose cached positive entry is within TTL
- **THEN** the resolver SHALL return the cached resolution without network I/O

#### Scenario: Expired entry triggers rediscovery
- **WHEN** a cached entry is older than its TTL
- **THEN** the resolver SHALL re-run discovery and update the cache

#### Scenario: Cache failure does not block the query
- **WHEN** the SQLite store is unreadable or unwritable
- **THEN** the resolver SHALL fall back to live discovery for the current request
- **AND** the surrounding query SHALL NOT fail

### Requirement: Discovery SHALL be bounded by configurable budgets
The system SHALL bound first-sight cost by `max_discovery_searches` (default 2) and `max_verification_fetches` (default 2). The resolver SHALL short-circuit the moment enough independent signals exist to reach a decision.

#### Scenario: Budget caps discovery
- **WHEN** an entity has no pin and no cache
- **THEN** the resolver SHALL issue at most `max_discovery_searches` search requests and at most `max_verification_fetches` fetches before deciding

#### Scenario: Early termination on sufficient signals
- **WHEN** enough independent signals to decide are collected before budgets are exhausted
- **THEN** the resolver SHALL stop further network requests and return the resolution

### Requirement: Resolver SHALL be disableable to restore static-map behavior
The system SHALL honor an `official_domain_resolution.enabled` flag. When `false`, official-domain recognition SHALL fall back to the legacy static `pins`/`official_domains` map only, with no discovery, caching, or network I/O.

#### Scenario: Disabled resolver behaves like today
- **WHEN** `official_domain_resolution.enabled` is `false`
- **THEN** tier classification SHALL rely solely on the configured pin/alias map
- **AND** the resolver SHALL NOT create or read the SQLite cache

### Requirement: Resolver results SHALL be auditable through the execution trace
The system SHALL surface the `signals` list (kind, source, detail) for each resolved official domain into the query execution trace so a human can reconstruct why a domain was judged official.

#### Scenario: Trace records resolution rationale
- **WHEN** the resolver returns `confidence="official"` for an entity
- **THEN** the execution trace SHALL include the contributing signals and their sources
- **AND** the trace SHALL distinguish pinned resolutions from discovered ones
