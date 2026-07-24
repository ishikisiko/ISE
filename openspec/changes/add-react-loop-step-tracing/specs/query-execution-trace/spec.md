## ADDED Requirements

### Requirement: ReAct fallback actions SHALL be available to backend trace consumers
The backend SHALL make LangGraph ReAct action events available through the
existing `WorkflowTracer` consumers: SSE step frames, the persisted process
audit, and a bounded additive final-response control projection.

#### Scenario: A streamed request enters the ReAct loop
- **WHEN** `/api/answer/stream` runs a LangGraph ReAct fallback or conversation
  continuation
- **THEN** each emitted ReAct iteration and tool event SHALL be sent as an SSE
  `step` frame in execution order
- **AND** the server SHALL not need a new SSE event type or frontend protocol

#### Scenario: A ReAct response finishes
- **WHEN** the ReAct runner completes
- **THEN** the response control metadata SHALL include a bounded `react_trace`
  projection of its safe action events
- **AND** existing response and execution-trace fields SHALL remain compatible

#### Scenario: Process audit is enabled
- **WHEN** a traced ReAct turn is persisted through the process audit writer
- **THEN** its safe action events SHALL be recorded with the other workflow
  steps subject to the existing size and redaction rules
- **AND** an audit write failure SHALL NOT prevent the answer response

### Requirement: Individual retrieval API calls SHALL expose bounded audit records
Every actual search-provider request SHALL produce its own additive workflow
step containing the provider outcome and the result list returned by that
specific call. The system SHALL capture those records before cross-provider
merge, reranking, or final-reference limiting.

#### Scenario: A provider search succeeds
- **WHEN** a configured provider returns one or more web search results
- **THEN** the trace SHALL contain one completed API-call step for that provider
- **AND** the step SHALL include a bounded, browser-safe list of that call's
  title, URL, and snippet records
- **AND** the displayed list SHALL NOT be reconstructed from final merged or
  reranked evidence

#### Scenario: A composite or fallback search makes multiple requests
- **WHEN** a combined client fans out or a priority client tries a fallback
  provider
- **THEN** every underlying provider HTTP attempt SHALL be represented
  independently, including empty and failed outcomes
- **AND** a failed primary attempt SHALL NOT hide a later successful fallback
  result list

#### Scenario: Selected-page extraction runs
- **WHEN** an existing selected-page extraction adapter is invoked
- **THEN** each extracted or failed URL SHALL be available as a bounded
  extraction audit record with provider, status, and content-size facts
- **AND** complete page content, opaque provider payloads, credentials, and
  URL query values SHALL NOT enter the trace

### Requirement: The workflow UI SHALL collapse audit result lists by API call
The frontend SHALL render each API call's audit records in a collapsed,
expandable group associated with that call's workflow step.

#### Scenario: A search provider returns five results
- **WHEN** a search API-call step contains five result records
- **THEN** the workflow SHALL show one collapsed result group for that call
- **AND** expanding it SHALL show all five returned links individually with
  their bounded titles and snippets

#### Scenario: A page-extraction call is recorded
- **WHEN** an extraction API-call step contains page records
- **THEN** expanding its group SHALL identify the extracted page URL and
  provider/outcome facts without rendering the page body
