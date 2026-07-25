## ADDED Requirements

### Requirement: Plan layer SHALL use the single canonical registrable-domain helper
The system SHALL compute registrable domains in the planning layer using the canonical `evidence.source_tiering.registrable_domain` helper. The system SHALL NOT maintain a duplicate domain-normalization or public-suffix implementation in the planning module, so that a given URL resolves to the same domain in both the plan layer and the evidence-tier layer.

#### Scenario: Plan and tier layers agree on a domain
- **WHEN** the plan layer and the evidence-tier layer each normalize the same URL
- **THEN** both SHALL return the identical registrable domain
- **AND** no private/duplicate public-suffix table SHALL remain in the planning module

### Requirement: Official recovery targets SHALL be sourced through the domain resolver
The system SHALL derive authority-recovery official-domain targets for comparison members by querying the domain resolver (with `pins` precedence) rather than by reading a separate static alias map. Targets the resolver cannot confirm as official SHALL not be planned as official recovery targets.

#### Scenario: Comparison member resolves to an official domain
- **WHEN** an authority-required comparison member has no configured pin
- **AND** the resolver returns a confirmed official domain for that member
- **THEN** the plan SHALL include that domain as a target official site
- **AND** the planned recovery step SHALL record the resolver signals in the trace

#### Scenario: Comparison member resolves to nothing official
- **WHEN** the resolver returns `confidence="none"` for a comparison member
- **THEN** the plan SHALL NOT emit an official recovery target for that member
- **AND** the verification policy SHALL treat that member as lacking official coverage
