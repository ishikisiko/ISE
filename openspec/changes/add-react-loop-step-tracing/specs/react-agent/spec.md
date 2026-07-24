## ADDED Requirements

### Requirement: LangGraph ReAct SHALL emit auditable action events
When the LangGraph ReAct engine runs with a workflow tracer, it SHALL emit
ordered, additive events for each iteration start, each enabled tool request,
each tool outcome, and the iteration's evaluation. Events SHALL identify the
iteration and tool, and SHALL include only bounded public arguments and
outcome facts; they SHALL NOT contain hidden reasoning, prompts, credentials,
or complete tool responses.

#### Scenario: A tool call succeeds during an iteration
- **WHEN** an act node requests one or more enabled tools
- **THEN** the tracer SHALL emit an active and completed event for every tool
- **AND** the completed event SHALL include the tool name, safe query summary,
  elapsed duration, and bounded result summary

#### Scenario: A tool invocation fails
- **WHEN** an enabled tool raises or returns a recognized error result
- **THEN** the tracer SHALL record an error outcome with a bounded safe reason
- **AND** the failed observation SHALL participate in normal loop termination
  accounting

#### Scenario: A compatible XML function request is returned by a model
- **WHEN** a model response has no native tool call but contains
  `<function>enabled_tool</function><query>...</query>`
- **THEN** the runner SHALL normalize it into an enabled tool call and execute
  it through the ordinary observe path
- **AND** the trace SHALL identify it as a tool action rather than answer text

#### Scenario: Tool-like markup is unsupported or malformed
- **WHEN** a model response contains unrecognized function markup
- **THEN** the runner SHALL emit a traceable invalid-tool-request outcome
- **AND** the markup SHALL NOT become the final answer text

### Requirement: Process narration SHALL NOT become the final answer
The runner SHALL reject clearly first-person search planning or process
narration without an enabled structured tool call as a final-answer candidate.
It SHALL emit a bounded response-format event and either retry with corrective
feedback or use the existing neutral terminal message; it SHALL NOT return the
process narration in `answer`.

#### Scenario: A model describes a planned search instead of calling a tool
- **WHEN** a model response says it needs or will perform a search but contains
  no enabled tool call
- **THEN** the response SHALL be excluded from the final answer
- **AND** the trace SHALL identify the format outcome without including the
  raw model prose
