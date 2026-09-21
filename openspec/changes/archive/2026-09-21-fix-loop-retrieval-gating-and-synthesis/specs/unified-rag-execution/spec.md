## MODIFIED Requirements

### Requirement: The loop SHALL preserve deterministic shortcuts
The runtime SHALL allow only small talk and visual input to bypass the loop through their bounded direct handlers. The deterministic clarification shortcut SHALL fire only for an unresolved reference: an English `this` / `that` used as a determiner or relative pronoun (immediately followed by a word) SHALL NOT count, a query that already names an entity SHALL NOT be treated as an unresolved reference unless it is a comparison lacking two members, and the clarification wording SHALL match the ambiguity type.

#### Scenario: Small talk is detected
- **WHEN** the deterministic small-talk check accepts the query
- **THEN** the system SHALL answer directly without opening the tool loop

#### Scenario: Critical ambiguity is detected
- **WHEN** `QueryAnalysis.critical_ambiguity=true` after deterministic skill preflight
- **THEN** the system SHALL request clarification before any tool call
- **AND** the clarification terminal state SHALL enter the execution trace

#### Scenario: Relative pronoun does not trigger clarification
- **WHEN** the query is an explanatory question such as "Explain how feature flags should be architected for a system that deploys 20+ times a day"
- **THEN** `QueryAnalysis.critical_ambiguity` SHALL be false
- **AND** the loop SHALL open normally

#### Scenario: Clarification wording matches the ambiguity
- **WHEN** the ambiguity is `unresolved_entity_reference`
- **THEN** the clarification SHALL ask which entity the reference points to
- **AND** SHALL NOT mention a comparison target
- **WHEN** the ambiguity is `comparison_members_unresolved`
- **THEN** the clarification SHALL ask for the objects to compare
