## MODIFIED Requirements

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

## ADDED Requirements

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
