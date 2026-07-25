# evidence-fusion-pipeline Specification

> **Status:** active - roadmap M5 loop/ledger contract.

## Purpose
Define how tool observations enter the single evidence ledger and become answer context.

## Requirements
### Requirement: Tool evidence SHALL enter one unified ledger
Every successful loop tool observation SHALL be normalized as an `EvidenceItem` and ingested into the turn's `EvidenceLedger`.

#### Scenario: Web, local, and skill tools return evidence
- **WHEN** any enabled tool returns evidence
- **THEN** each item SHALL retain its source type, source tier, reference, and originating tool call
- **AND** all items SHALL enter the same ledger

#### Scenario: Search is disabled
- **WHEN** `allow_search=false`
- **THEN** web tools SHALL be absent from the loop tool surface
- **AND** available local and skill tools SHALL continue to use the same ledger

### Requirement: Ledger policy SHALL control retention
The ledger SHALL apply authority, comparison, temporal, deduplication, and result-limit policies derived from `QueryAnalysis`.

#### Scenario: Duplicate references arrive from multiple calls
- **WHEN** multiple tool calls return the same canonical reference
- **THEN** the ledger SHALL merge them into one entry
- **AND** provenance SHALL preserve the actual calls that contributed evidence

#### Scenario: Evidence does not satisfy authority
- **WHEN** a result is relevant but its tier is below an authority requirement
- **THEN** the ledger SHALL mark it limited or rejected
- **AND** the termination critic SHALL NOT count it as authoritative evidence

### Requirement: Answer assembly SHALL use ledger evidence
Answer evidence metadata SHALL be assembled from retained evidence, or explicitly bounded limited evidence when no retained evidence exists.

#### Scenario: Retained evidence exists
- **WHEN** the critic permits a final answer
- **THEN** response `evidence_items` SHALL contain ledger-selected evidence
- **AND** each item SHALL be traceable to a tool call

#### Scenario: No acceptable evidence exists
- **WHEN** the query requires evidence and the ledger cannot satisfy the required policy before budgets end
- **THEN** the loop SHALL return an evidence-insufficient terminal state
- **AND** it SHALL NOT present an unsupported factual answer
