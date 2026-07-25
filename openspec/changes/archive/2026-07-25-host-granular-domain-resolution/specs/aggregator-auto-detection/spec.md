## ADDED Requirements

### Requirement: System SHALL record cross-stem candidate observations in the resolver cache
The resolver cache SHALL persist one observation row per B-tier candidate host per resolution, recording the host, the entity stem it was a candidate for, and the observation time. Observation writes SHALL be best-effort and SHALL NOT abort resolution on failure.

#### Scenario: Discovery records candidate observations
- **WHEN** a resolution completes with B-tier candidate hosts
- **THEN** the cache SHALL contain one observation row per (host, stem) pair
- **AND** duplicate observations for the same (host, stem) pair SHALL NOT accumulate

#### Scenario: Observation write failure does not block resolution
- **WHEN** the observation table is unavailable or unwritable
- **THEN** the resolver SHALL still return the resolution
- **AND** the surrounding query SHALL NOT fail

### Requirement: System SHALL auto-flag suspected aggregators from cross-stem statistics
The system SHALL flag a host as a `suspected_aggregator` when it has been observed as a B-tier candidate for at least `aggregator_min_stems` (default 5) distinct entity stems within the observation window. Flag state SHALL be recomputed from observations and SHALL NOT require curated-list edits.

#### Scenario: Host voted for by many unrelated stems is flagged
- **WHEN** a host has appeared as a candidate for 5 or more distinct stems
- **AND** `aggregator_min_stems` is 5
- **THEN** the resolver SHALL treat that host as a suspected aggregator for subsequent resolutions

#### Scenario: Host below threshold is not flagged
- **WHEN** a host has appeared as a candidate for fewer than `aggregator_min_stems` distinct stems
- **THEN** the resolver SHALL NOT treat it as a suspected aggregator on behavioral grounds

### Requirement: Suspected aggregators SHALL face a higher evidence bar
A suspected-aggregator host SHALL require at least `min_signals + 1` mutually independent sources to reach `confidence="official"` via B-tier voting, and SHALL NOT receive E-tier well-known-subdomain acceptance. Pins and A-tier structured signals SHALL be unaffected by the flag.

#### Scenario: Flagged host needs an extra independent source
- **WHEN** a suspected-aggregator host has exactly `min_signals` independent B-tier sources for an entity
- **AND** no pin or A-tier signal supports it
- **THEN** the resolver SHALL return at most `confidence="candidate"` for that host

#### Scenario: Flag does not override ownership
- **WHEN** a suspected-aggregator host is vouched for by a pin or an A-tier structured signal for the current entity
- **THEN** the resolver SHALL return `confidence="official"` for that host regardless of the flag
