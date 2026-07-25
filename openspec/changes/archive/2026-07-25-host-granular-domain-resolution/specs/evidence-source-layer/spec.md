## MODIFIED Requirements

### Requirement: Official source tier SHALL be determined through the domain resolver
The system SHALL determine whether a web URL is `official`, `first_party`, or `unknown` for a query entity by routing ownership lookups through the official-domain resolver, with configured `pins` taking precedence over discovered results. Ownership matching SHALL be performed at host granularity with optional path prefixes: a URL is owned when its host matches a resolved official host entry (per the explicit subdomain rule) and, when the entry carries a path prefix, the URL path starts with that prefix. `classify_web_source_tier` and `official_entity_for_url` SHALL NOT consult the raw alias map except via the resolver's pin layer, and SHALL NOT collapse URLs to registrable domains for ownership decisions.

#### Scenario: Unconfigured entity resolves to official
- **WHEN** a web result's host matches a resolver-confirmed official host entry for a current query entity that has no configured pin
- **THEN** the result SHALL be classified as `official`
- **AND** the matched entity SHALL be preserved in the evidence metadata

#### Scenario: Host-level official entry does not bless the whole family
- **WHEN** `ai.google.dev` is resolver-confirmed official for an entity
- **AND** a result URL is on `google.com/search`
- **THEN** the result SHALL NOT be classified as `official` for that entity

#### Scenario: Resolver returns no official domain
- **WHEN** the resolver returns `confidence="none"` for every query entity
- **THEN** no result SHALL be classified as `official` for those entities
- **AND** results whose host label merely overlaps the entity stem MAY be classified as `first_party`

### Requirement: Confirmed official domains SHALL auto-accept well-known subdomains
The system SHALL treat a URL as `official` when its host equals a confirmed official host entry OR when its host is a well-known subdomain (`docs.`, `platform.`, `developer.`, `open.`, `api.`, `www.`, `dev.`, `developers.`, `documentation.`) or single-label subdomain of a confirmed official host entry, without requiring each subdomain to be individually configured. Acceptance SHALL use the explicit shared matcher and SHALL NOT rely on registrable-domain collapsing; hosts flagged as suspected aggregators SHALL NOT receive subdomain acceptance.

#### Scenario: Subdomain of confirmed official domain
- **WHEN** a result host is `docs.<confirmed-official-host>`
- **AND** `<confirmed-official-host>` is resolver-confirmed official for a query entity
- **THEN** the result SHALL be classified as `official` without a dedicated subdomain entry in `pins`

#### Scenario: Unrelated sibling host is not accepted
- **WHEN** `google.com` is confirmed official for a query entity
- **AND** a result host is `deepmind.google`
- **THEN** the result SHALL NOT be classified as `official` on that basis

## ADDED Requirements

### Requirement: Non-evidence hosts SHALL be excluded from ordinary web evidence
The system SHALL exclude URLs on `non_evidence` hosts (search engines, AI answer sites, aggregator mirrors) from ordinary web evidence in addition to never judging them official. `classify_web_source_tier` SHALL classify such URLs so the evidence pipeline can filter them before fusion, independent of any entity resolution.

#### Scenario: Search-engine URL is filtered
- **WHEN** a web result URL is on a `non_evidence` host (e.g. a search results page)
- **THEN** the result SHALL be marked for exclusion from the evidence set
- **AND** this SHALL hold even when no query entity resolves to any official host

### Requirement: Hosting-platform URLs SHALL classify official when ownership holds
The system SHALL classify a URL on a `hosting_platforms` host as `official` for a query entity when the resolver confirms an ownership relation (declared package homepage or matching repo owner), and SHALL otherwise classify it no higher than `unknown` for official-judgement purposes.

#### Scenario: Docs host with declared-homepage ownership
- **WHEN** the resolver confirms `<pkg>.readthedocs.io` as official for an entity via a declared package homepage
- **THEN** results on `<pkg>.readthedocs.io` SHALL be classified as `official`

#### Scenario: Hosting-platform URL without ownership
- **WHEN** a result is on a `hosting_platforms` host
- **AND** the resolver has no ownership relation between that host and any query entity
- **THEN** the result SHALL NOT be classified as `official`
