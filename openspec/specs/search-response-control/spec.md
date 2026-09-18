# search-response-control Specification

> **Status:** active - roadmap M5 loop control contract.

## Purpose
Define stable response metadata for the sole agentic executor and bounded shortcuts.

## Requirements
### Requirement: Control metadata SHALL identify the actual executor
Responses SHALL expose `control.final_executor`, `control.search_mode`, `control.autonomy`, and loop terminal metadata without an engine-mode switch or fallback marker. `control.autonomy` SHALL carry the effective mode name and its resolution source, and SHALL NOT be interpreted as selecting a different executor.

#### Scenario: Agentic loop returns
- **WHEN** a non-shortcut query completes
- **THEN** `final_executor` SHALL equal `agentic_loop`
- **AND** control SHALL include loop status, iteration count, termination reason, and verdicts

#### Scenario: Small talk or visual handling returns
- **WHEN** a bounded shortcut handles the request
- **THEN** control SHALL identify that shortcut
- **AND** it SHALL NOT claim a loop tool was executed

#### Scenario: Autonomy is reported alongside a single executor
- **WHEN** a query completes under any autonomy mode
- **THEN** `control.autonomy` SHALL report the effective mode and source
- **AND** `final_executor` SHALL remain `agentic_loop`

### Requirement: Analysis, ledger, and trace metadata SHALL be additive and bounded
Control SHALL expose serializable `query_analysis`, `evidence_coverage`, and `execution_trace` when available.

#### Scenario: Existing callers parse a response
- **WHEN** callers read `answer`, `search_hits`, `retrieved_docs`, or `control`
- **THEN** those primary fields SHALL retain compatible types
- **AND** removed rollout metadata fields SHALL NOT be required

#### Scenario: Metadata exceeds its limit
- **WHEN** trace or evidence summaries exceed configured bounds
- **THEN** the system SHALL apply deterministic truncation
- **AND** truncation SHALL NOT change the answer or primary field types

### Requirement: Tool budgets SHALL be observable
Control SHALL expose each mounted tool's per-query limit and actual calls used.

#### Scenario: A query calls web search twice
- **WHEN** the response is finalized
- **THEN** `termination_policy.tool_budgets.web_search.used` SHALL equal two
- **AND** its configured limit SHALL be present

### Requirement: Advisory and cancellation facts SHALL be bounded control metadata
Control metadata SHALL expose the advisory gap count produced by non-binding rules and, when the run was cancelled, a cancellation flag with the iteration at which it took effect. Both SHALL be bounded scalars and SHALL NOT carry rule text, prompts, or model reasoning.

#### Scenario: Advisory gaps are reported
- **WHEN** the run used advisory critic or citation checking
- **THEN** control SHALL include the count of gaps recorded during the run
- **AND** the count SHALL NOT change the reported terminal status

#### Scenario: A cancelled run is reported
- **WHEN** the run ended because the client cancelled it
- **THEN** control SHALL expose the cancellation flag and the iteration reached
- **AND** it SHALL NOT report a budget or evidence based terminal status
