## ADDED Requirements

### Requirement: Authority-required comparisons SHALL plan bounded target-domain recovery
The system SHALL create a bounded recovery step with one deterministic target record
per mapped member when an authority-required comparison contains members that map to
configured official domains.

#### Scenario: Pricing comparison has three mapped members
- **WHEN** a pricing comparison names three entities that map to configured official domains
- **THEN** the plan SHALL retain the three target entities and their allowed registrable domains
- **AND** the recovery step SHALL be bounded to the configured target limit and recovery budget

#### Scenario: Comparison member has no official-domain mapping
- **WHEN** a comparison member has no configured official-domain alias
- **THEN** the plan SHALL not invent an official domain for that member
- **AND** existing generic recovery semantics SHALL remain available
