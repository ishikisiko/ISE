## ADDED Requirements

### Requirement: Evidence recall SHALL be a budgeted local tool
The system SHALL expose `recall_evidence` as an independently budgeted tool that resolves `[En]` citation identifiers against the run's `EvidenceLedger`. The tool SHALL NOT perform any network or provider call, and SHALL NOT register new evidence records.

#### Scenario: Recall returns the stored entry
- **WHEN** the loop calls `recall_evidence` with identifiers present in the ledger
- **THEN** the tool SHALL return the full rendered ledger entry for each identifier
- **AND** the returned content SHALL come only from records already registered by a prior tool call

#### Scenario: Recall of an unknown identifier
- **WHEN** a requested identifier is absent from the ledger
- **THEN** the tool SHALL return a structured not-found outcome for that identifier
- **AND** it SHALL NOT fabricate an evidence record

#### Scenario: Recall budget is exhausted
- **WHEN** `recall_evidence` has reached its configured call budget for the run
- **THEN** the wrapper SHALL return structured `rejected` data with a bounded reason
- **AND** the loop SHALL continue without error

#### Scenario: Recall performs no external call
- **WHEN** `recall_evidence` executes
- **THEN** no search provider, fetch, or skill provider SHALL be invoked
- **AND** the call SHALL NOT consume any other tool's budget
