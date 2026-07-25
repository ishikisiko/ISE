## ADDED Requirements

### Requirement: Official source tier SHALL be determined through the domain resolver
The system SHALL determine whether a web URL is `official`, `first_party`, or `unknown` for a query entity by routing ownership lookups through the official-domain resolver, with configured `pins` taking precedence over discovered results. `classify_web_source_tier` and `official_entity_for_url` SHALL NOT consult the raw alias map except via the resolver's pin layer.

#### Scenario: Unconfigured entity resolves to official
- **WHEN** a web result's registrable domain matches a resolver-confirmed official domain for a current query entity that has no configured pin
- **THEN** the result SHALL be classified as `official`
- **AND** the matched entity SHALL be preserved in the evidence metadata

#### Scenario: Resolver returns no official domain
- **WHEN** the resolver returns `confidence="none"` for every query entity
- **THEN** no result SHALL be classified as `official` for those entities
- **AND** results whose registrable-domain label merely overlaps the entity stem MAY be classified as `first_party`

### Requirement: Confirmed official domains SHALL auto-accept well-known subdomains
The system SHALL treat a URL as `official` when its registrable domain is a confirmed official domain OR when its host is a well-known subdomain (`docs.`, `platform.`, `developer.`, `open.`, `api.`, `www.`) of a confirmed official registrable domain, without requiring each subdomain to be individually configured.

#### Scenario: Subdomain of confirmed official domain
- **WHEN** a result host is `docs.<confirmed-official-domain>`
- **AND** the registrable domain `<confirmed-official-domain>` is resolver-confirmed official for a query entity
- **THEN** the result SHALL be classified as `official` without a dedicated subdomain entry in `pins`

### Requirement: Official-page enrichment SHALL rely on analyzer entities, not alias grepping
The system SHALL select official pages for extraction using entity candidates produced by the query analyzer and/or the resolver, and SHALL NOT recover entities by splitting the raw query text and matching tokens against configured alias keys.

#### Scenario: Analyzer emits no entity and resolver has no result
- **WHEN** the analyzer produces no candidate entity for the query
- **AND** the resolver returns no confirmed official domain
- **THEN** official-page extraction SHALL select no official pages
- **AND** the system SHALL NOT manufacture an entity by grepping alias keys out of the query string
