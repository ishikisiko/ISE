# query-execution-trace Specification

> **Status:** active - roadmap M5 actual-execution trace.

## Purpose
Define the bounded record of analysis, actual tool calls, ledger decisions, and termination.

## Requirements
### Requirement: Trace SHALL record actual execution only
`QueryExecutionTrace` SHALL record query analysis, each attempted tool call, ledger decisions, and the terminal critic verdict in order.

#### Scenario: A tool succeeds
- **WHEN** a loop tool returns one or more evidence items
- **THEN** trace SHALL record tool name, iteration, position, status, query, source type, source tier, and item count
- **AND** the tool SHALL appear in `executed`

#### Scenario: A tool fails or exhausts budget
- **WHEN** a tool call returns an error or `budget_exhausted`
- **THEN** trace SHALL record the actual status and bounded reason
- **AND** it SHALL NOT invent an execution step for an uncalled tool

#### Scenario: Clarification happens before tools
- **WHEN** critical ambiguity blocks execution
- **THEN** trace SHALL contain analysis and a clarification terminal event
- **AND** `executed` SHALL remain empty

### Requirement: Provider inventory SHALL remain distinct from execution
Trace SHALL expose configured, requested, eligible, and executed provider/tool identities as separate bounded lists.

#### Scenario: A configured provider is not called
- **WHEN** the model completes without selecting that provider's tool
- **THEN** the provider MAY appear as configured or eligible
- **AND** it SHALL NOT appear as executed

#### Scenario: A provider fallback occurs inside a search tool
- **WHEN** the search client attempts multiple providers
- **THEN** provider API-call audit events SHALL preserve their real order and outcomes
- **AND** the outer trace SHALL still identify the originating loop tool call

### Requirement: Trace output SHALL be bounded and safe
The response and durable audit projection SHALL cap event counts and sanitize sensitive values.

#### Scenario: A long loop emits many events
- **WHEN** event count exceeds the response limit
- **THEN** trace SHALL return a deterministic prefix and `truncated=true`
- **AND** the final ledger and terminal summary SHALL remain available in control metadata

#### Scenario: Tool input contains secrets or URL queries
- **WHEN** trace data is serialized
- **THEN** credentials and URL query or fragment values SHALL be redacted
- **AND** opaque provider payloads SHALL NOT be persisted
