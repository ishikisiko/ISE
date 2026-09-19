# evidence-policy-routing Specification

> **Status:** active - roadmap M5 analysis-to-ledger policy contract.

## Purpose
Define evidence policies derived from query constraints and enforced by the ledger and critic.

## Requirements
### Requirement: Query analysis SHALL derive composable evidence policies
The system SHALL derive authority, comparison, temporal, and freshness policies from `QueryAnalysis`, not from a static execution plan or a single domain label.

#### Scenario: Current numeric claim requires authority
- **WHEN** analysis marks `authority_required=true`
- **THEN** the ledger SHALL accept only official, first-party, or authoritative evidence for that requirement
- **AND** the critic SHALL report an authority gap until such evidence exists

#### Scenario: Comparison requires member coverage
- **WHEN** analysis identifies two or more comparison members
- **THEN** the ledger and critic SHALL track evidence coverage independently for each member
- **AND** a missing member SHALL remain an explicit constraint gap

#### Scenario: Historical coverage is requested
- **WHEN** analysis marks `historical_coverage_required=true`
- **THEN** the critic SHALL require evidence matching the requested time scope
- **AND** the model MAY choose a bounded tool call to repair the gap

### Requirement: Source acceptance SHALL be independent of provider availability
A callable provider SHALL NOT automatically make its results acceptable evidence.

#### Scenario: Available source is below the required tier
- **WHEN** a web tool returns relevant but unknown-tier evidence for an authority-required claim
- **THEN** the ledger SHALL mark it limited or rejected
- **AND** the critic SHALL NOT count it as authoritative

#### Scenario: No acceptable evidence exists
- **WHEN** per-tool and loop budgets end without retained evidence for a required claim
- **THEN** the critic SHALL return an evidence-insufficient terminal action
- **AND** the answer SHALL state the missing evidence rather than fabricate support

### Requirement: Web evidence SHALL receive a target-bound source tier
Each web item SHALL be classified as `official`, `first_party`, `unknown`, or excluded before ledger retention.

#### Scenario: Official result matches a query entity
- **WHEN** the resolver confirms the result host for a current query entity
- **THEN** the evidence SHALL be tiered `official`
- **AND** matched-entity metadata SHALL be preserved

#### Scenario: Result cannot be attributed
- **WHEN** no resolver relation or first-party signal matches a query entity
- **THEN** the evidence SHALL remain `unknown`
- **AND** the system SHALL NOT promote it by guesswork

### Requirement: Official comparison coverage SHALL be per target
An authority-required comparison SHALL require acceptable evidence for every configured target.

#### Scenario: One target lacks official evidence
- **WHEN** another target has acceptable evidence but one member does not
- **THEN** the critic SHALL retain the missing target as an authority and comparison gap
- **AND** the response SHALL NOT present the comparison as verified

#### Scenario: All targets are covered
- **WHEN** every comparison member has acceptable evidence for the requested claim
- **THEN** the critic MAY allow a normal final answer subject to all other constraints
