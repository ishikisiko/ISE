## MODIFIED Requirements

### Requirement: System SHALL resolve an entity to an official domain via structural signals only
The system SHALL expose a single resolver entry point that maps an entity stem to a `Resolution` value object containing `domain`, `domains` (host-level entries, optionally with path prefixes), `confidence`, an ordered `signals` list, and `verified_at`. The resolver SHALL assemble confidence exclusively from structural signals — structured registry fields (Wikidata `P856`, PyPI `home_page`, npm `homepage`, GitHub repo homepage) recorded at host granularity, cross-provider search voting collapsed to the **host** (not the registrable domain), `DirectFetchClient` redirect-chain / `<title>` / `og:site_name` / TLS-host self-verification, the three list tables (`never_official`, `hosting_platforms`, `non_evidence`), suspected-aggregator flags, and explicit well-known-subdomain acceptance. The resolver SHALL NOT use LLM output that asserts what an entity's official site is, because model memory and page-body claims are injectable sources of domain hallucination.

#### Scenario: Unknown entity resolves via two independent search providers
- **WHEN** the resolver is asked for an entity that has no pin and no cache entry
- **AND** two independent search providers return top hits that collapse to the same host
- **THEN** the resolver SHALL return that host with `confidence="official"` and at least two `signals` whose `kind` records the independent providers

#### Scenario: Sibling hosts do not merge votes
- **WHEN** one provider's top hit is `cloud.google.com` and another's is `ai.google.dev`
- **THEN** the resolver SHALL NOT merge these into a single `google.com` candidate
- **AND** each host SHALL be tallied as its own candidate

#### Scenario: Single weak signal does not satisfy official
- **WHEN** exactly one non-structured signal supports a candidate host
- **THEN** the resolver SHALL return `confidence="candidate"` and SHALL NOT return `confidence="official"`

#### Scenario: Structured registry field alone is sufficient
- **WHEN** a structured A-tier source (e.g. PyPI `home_page`) returns a URL for the entity
- **THEN** the resolver SHALL return that URL's host with `confidence="official"` and a `signals` entry whose `kind` identifies the structured source

#### Scenario: Never-official domain is excluded from voting
- **WHEN** search providers return hits on a `never_official` host (e.g. a content farm)
- **THEN** those hits SHALL be dropped before candidate tallying
- **AND** the host SHALL NOT occupy a top-N candidate slot
- **AND** if it is the only candidate the resolver SHALL return `confidence="none"`

#### Scenario: Page-body claim is rejected
- **WHEN** a fetched page body asserts "this is the official site of <entity>"
- **AND** no structural signal (registry field, redirect chain, certificate, or cross-provider vote) corroborates it
- **THEN** the resolver SHALL NOT return that host as `official` or `candidate`

### Requirement: Pinned domains SHALL override discovery with highest priority
The system SHALL read configured `pins` (`{stem: [host-or-host/path entries]}`) and return a pinned stem's entries as `confidence="official"` with a `signal` of `kind="pin"`, without performing any network discovery. Pin entries SHALL be matched at host granularity with optional path prefixes, so a pin may name a specific host of a product family (`deepmind.google`, `ai.google.dev`). A pin SHALL override any contradictory cached or discovered result for the same stem, and SHALL NOT be suppressed by any list table or suspected-aggregator flag.

#### Scenario: Pinned stem skips discovery
- **WHEN** an entity stem matches a configured pin
- **THEN** the resolver SHALL return the pinned host entries as `official` immediately
- **AND** the resolver SHALL NOT issue any search or fetch request for that stem

#### Scenario: Pin overrides a stale cached result
- **WHEN** a cache entry for a stem disagrees with a configured pin
- **THEN** the pin SHALL win and the returned `signals` SHALL include a `kind="pin"` entry

#### Scenario: Pin on a listed host still wins
- **WHEN** a pinned host also appears in a list table (e.g. `hosting_platforms`)
- **THEN** the pin SHALL take precedence and the host SHALL resolve `official`

## ADDED Requirements

### Requirement: List tables SHALL have three distinct semantics and enforcement points
The system SHALL replace the single aggregator denylist with three tables. `never_official` hosts SHALL be dropped from B-tier candidate collection and SHALL never be judged official for any entity. `hosting_platforms` hosts SHALL NOT be judged official by default, but SHALL be judgeable as official when an ownership relation exists: an A-tier registry field declares the URL as the entity's homepage, or a code-hosting repo whose owner/name matches the entity stem. `non_evidence` hosts SHALL be dropped from B-tier voting, SHALL never be official, and SHALL be marked for exclusion from ordinary web evidence.

#### Scenario: Content farm can never be official
- **WHEN** the only candidate host for an entity is on `never_official`
- **THEN** the resolver SHALL return `confidence="none"` regardless of how many providers pointed at it

#### Scenario: Hosting platform official via declared homepage
- **WHEN** an entity's PyPI `home_page` (or npm `homepage`) points at `https://<pkg>.readthedocs.io/`
- **AND** `readthedocs.io` is on `hosting_platforms`
- **THEN** the resolver SHALL return `<pkg>.readthedocs.io` with `confidence="official"`

#### Scenario: Hosting platform official via matching repo owner
- **WHEN** the top GitHub repo for an entity has an owner/login matching the entity stem and its homepage is a `*.github.io` Pages host
- **THEN** the resolver SHALL treat that Pages host as an ownership-backed candidate
- **AND** without such a match a `github.com` / `github.io` candidate SHALL NOT be judged official

### Requirement: List suppression SHALL be entity-aware
List tables SHALL be evaluated only against candidates that have no ownership relation to the current entity. Pins and A-tier structured signals SHALL be resolved before list checks, and any candidate they vouch for SHALL bypass list suppression. A question about a listed platform itself (e.g. GitHub, PyPI, a cloud vendor) SHALL therefore remain answerable with that platform's own official host.

#### Scenario: Entity that is itself a listed platform
- **WHEN** the resolver is asked for an entity whose own host appears in a list table
- **AND** a pin or A-tier structured signal confirms that host for the entity
- **THEN** the resolver SHALL return `confidence="official"` for that host

#### Scenario: Unrelated entity still suppressed
- **WHEN** an entity has no pin or A-tier signal pointing at a listed host
- **AND** B-tier voting surfaces that listed host as a candidate
- **THEN** the list table SHALL suppress it per its semantics

### Requirement: List configuration SHALL support extend and replace modes
Each list table SHALL accept `{"mode": "extend" | "replace", "domains": [...]}`. `extend` (default) SHALL union the configured entries with the built-in seed list; `replace` SHALL use the configured entries verbatim. A legacy bare-array `aggregator_denylist` SHALL be read as `never_official` with mode `extend` for compatibility.

#### Scenario: Replace mode removes a built-in entry
- **WHEN** `hosting_platforms` is configured with `mode: "replace"` and a list that omits `readthedocs.io`
- **THEN** `readthedocs.io` SHALL NOT be treated as a hosting platform

#### Scenario: Extend mode preserves built-ins
- **WHEN** `never_official` is configured with `mode: "extend"` and one extra host
- **THEN** the effective table SHALL contain the built-in seeds plus the extra host

#### Scenario: Legacy aggregator_denylist still honored
- **WHEN** config contains only a legacy bare-array `aggregator_denylist`
- **THEN** its entries SHALL extend `never_official` and resolution SHALL proceed without error

### Requirement: E-tier subdomain acceptance SHALL be explicit
The system SHALL accept a URL host as covered by a confirmed official host entry when the hosts are equal, or when the URL host is a well-known subdomain (`docs.`, `platform.`, `developer.`, `open.`, `api.`, `www.`, `dev.`, `developers.`, `documentation.`) or single-label subdomain of the confirmed host. Acceptance SHALL be implemented as an explicit matcher shared by the resolver and tiering, and SHALL NOT rely on registrable-domain collapsing. Suspected-aggregator hosts SHALL NOT receive subdomain acceptance.

#### Scenario: Well-known subdomain of confirmed host
- **WHEN** `example.com` is resolver-confirmed official for an entity
- **AND** a result host is `docs.example.com`
- **THEN** the result SHALL be accepted as official without a dedicated entry

#### Scenario: Sibling product host is not implicitly accepted
- **WHEN** `google.com` is confirmed official for an entity
- **AND** a result host is `ai.google.dev`
- **THEN** the result SHALL NOT be accepted on that basis, because the hosts are unrelated
