# react-tool-wrapper Specification

> **Status:** active - roadmap M5 per-tool wrapper contract.

## Purpose
Define the independently budgeted tools available to the sole agentic loop.
## Requirements
### Requirement: Web search SHALL expose normalized structured evidence
The system SHALL wrap the configured `SearchClient` as `web_search` and SHALL normalize returned hits through `WebEvidenceSource` before observation.

#### Scenario: Web search succeeds
- **WHEN** the loop calls `web_search` with a query
- **THEN** the tool SHALL return a bounded readable result to the model
- **AND** it SHALL expose structured records containing reference, content, source tier, and provider-safe metadata

#### Scenario: Web search returns no usable results
- **WHEN** no evidence item remains after deterministic filtering
- **THEN** the tool SHALL return a no-results outcome
- **AND** it SHALL NOT fabricate a structured evidence record

### Requirement: Registry skills SHALL remain independent tools
Each available registry skill SHALL be exposed as its own LangChain tool; the runtime SHALL NOT provide a unified domain router.

#### Scenario: Skill preflight accepts the query
- **WHEN** the loop calls a skill with complete valid parameters
- **THEN** the wrapper SHALL run deterministic preflight before the provider
- **AND** successful `EvidenceItem` records SHALL retain skill, tool, provider, and reference provenance

#### Scenario: Skill preflight rejects the query
- **WHEN** a required explicit parameter is missing or invalid
- **THEN** the wrapper SHALL return structured `rejected` data and a bounded reason
- **AND** the provider SHALL NOT be called

### Requirement: Local documents SHALL use the unified evidence layer
The `local_docs` tool SHALL retrieve through the local `EvidenceSource` and return structured local evidence rather than invoke legacy `LocalRAG` directly.

#### Scenario: Local documents are available
- **WHEN** the loop calls `local_docs`
- **THEN** the tool SHALL return bounded content and document references
- **AND** structured records SHALL use `source_type=local`

#### Scenario: No local documents are available
- **WHEN** the configured data path contains no indexable document
- **THEN** the tool SHALL return a deterministic unavailable/no-results outcome

### Requirement: Search recovery SHALL be a normal high-level tool
The loop SHALL be able to select `search_recovery` to reuse unified retrieval, filtering, optional local retrieval, and synthesis; it SHALL NOT be invoked by a post-generation fallback controller.

#### Scenario: The model selects recovery
- **WHEN** current observations leave an evidence or synthesis gap
- **THEN** `search_recovery` SHALL execute through the same evidence normalization layer
- **AND** its structured evidence SHALL enter the same ledger as other tools

#### Scenario: A current query is recovered
- **WHEN** analysis requires freshness but not historical coverage
- **THEN** recovery SHALL NOT fan out into per-year historical searches

#### Scenario: Explicit multi-year history is requested
- **WHEN** analysis marks `historical_coverage_required=true`
- **THEN** recovery MAY perform its bounded granular temporal search
- **AND** every provider request SHALL remain observable

### Requirement: Every tool SHALL enforce its own call budget
Each wrapper SHALL reset and enforce `max_calls_per_query` independently and SHALL expose `limit` and `used` after the run.

#### Scenario: A tool budget is exhausted
- **WHEN** the loop calls a tool after its limit is spent
- **THEN** the wrapper SHALL return structured `budget_exhausted`
- **AND** no provider call SHALL occur

#### Scenario: Another tool remains available
- **WHEN** one tool is exhausted and another has remaining calls
- **THEN** the latter SHALL retain its full independent budget

### Requirement: Wrapper records SHALL be audit safe
Tool wrappers SHALL expose bounded structured evidence and provider-call snapshots without credentials, URL query strings, complete prompts, or hidden reasoning.

#### Scenario: The execution trace consumes wrapper output
- **WHEN** one invocation yields several evidence records
- **THEN** `QueryExecutionTrace` SHALL record one tool-call event with the aggregate item count
- **AND** `EvidenceLedger` SHALL retain per-item references to that call

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

