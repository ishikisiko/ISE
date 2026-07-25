# react-orchestrator Specification

> **Status:** active - roadmap M5 default orchestrator contract.

## Purpose
Define the compatible adapter that exposes the sole LangGraph agentic loop to CLI and API callers.

## Requirements
### Requirement: ReactAgentOrchestrator SHALL expose the production answer interface
The system SHALL provide `ReactAgentOrchestrator.answer()` with the query, search eligibility, conversation, analysis, trace, and generation controls used by the default orchestrator.

#### Scenario: A normal query is delegated
- **WHEN** the default `LangChainOrchestrator` receives a non-shortcut query
- **THEN** it SHALL invoke `ReactAgentOrchestrator.answer()`
- **AND** the result SHALL contain `answer`, `control`, `search_hits`, and unified evidence metadata

#### Scenario: A conversation id is supplied
- **WHEN** `conversation_id` names an existing LangGraph checkpoint
- **THEN** the loop SHALL resume that thread using the bounded conversation history policy
- **AND** `control.conversation_resumed` SHALL report the actual resume state

### Requirement: ReactAgentOrchestrator SHALL be the sole default executor
The adapter SHALL run the explicit LangGraph `act -> observe -> evaluate` state machine and SHALL NOT run a pre-answer pipeline, postcheck fallback, or alternate stopping implementation.

#### Scenario: Search is enabled
- **WHEN** `allow_search=true`
- **THEN** the loop SHALL receive every configured and available tool
- **AND** tool selection SHALL occur inside the loop

#### Scenario: Search is disabled
- **WHEN** `allow_search=false`
- **THEN** the same loop SHALL run without `web_search` and `search_recovery`
- **AND** the adapter SHALL NOT select a separate local-only executor

#### Scenario: LangGraph is unavailable
- **WHEN** the LangGraph dependency cannot be imported
- **THEN** construction SHALL fail explicitly
- **AND** the adapter SHALL NOT silently switch to a second executor

### Requirement: Tool availability SHALL be deterministic
The adapter SHALL expose caller-supplied tools when provided; otherwise it SHALL build web, local, recovery, and registry-skill tools only when their deterministic availability requirements are met.

#### Scenario: A registry skill lacks required configuration
- **WHEN** the registry marks a skill unavailable
- **THEN** that skill tool SHALL NOT be exposed to the model

#### Scenario: Custom tools are supplied
- **WHEN** a caller constructs the adapter with an explicit non-empty tool list
- **THEN** only those tools SHALL be eligible

### Requirement: Response control SHALL report actual loop execution
The adapter SHALL expose the terminal status, iterations, verdicts, actual evidence sources, and per-tool budgets without a fallback marker or engine-mode field.

#### Scenario: The loop completes normally
- **WHEN** the shared critic accepts a candidate answer
- **THEN** `control.loop_status` SHALL be `succeeded`
- **AND** `control.final_executor` SHALL be `agentic_loop`

#### Scenario: The loop reaches a hard terminal
- **WHEN** the loop exhausts, stagnates, becomes unrecoverable, needs clarification, or lacks evidence
- **THEN** the corresponding terminal status and final verdict SHALL be returned
- **AND** any provisional answer SHALL explicitly state that evidence or execution budget was insufficient

### Requirement: The adapter SHALL share the outer execution trace
The default orchestrator SHALL pass its current `QueryExecutionTrace` into the loop so actual attempts are recorded as they occur.

#### Scenario: A tool succeeds or fails
- **WHEN** observe completes one tool invocation
- **THEN** the shared trace SHALL contain exactly one call event for that iteration and position
- **AND** evidence items from the call SHALL retain the same tool-call provenance
