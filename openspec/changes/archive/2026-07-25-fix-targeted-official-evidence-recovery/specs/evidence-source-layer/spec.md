## ADDED Requirements

### Requirement: Official web evidence labels SHALL be bound to current query targets
The system SHALL label a web result as official only when its domain matches a configured
official domain for an entity in the current query plan, and SHALL preserve the matched
entity in metadata when known.

#### Scenario: Unrelated configured domain appears in results
- **WHEN** a result belongs to a configured official domain for an entity not named by the query
- **THEN** the result SHALL not be counted or displayed as official evidence for the query targets

#### Scenario: Target official domain appears in results
- **WHEN** a result domain matches a configured official domain for a current comparison member
- **THEN** the evidence metadata SHALL identify the matched member and classify the result as official
