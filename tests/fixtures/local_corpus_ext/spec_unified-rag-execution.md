# unified-rag-execution Specification

> **Status:** active - roadmap M5 sole-executor contract.

## Purpose
Define the single act/observe/evaluate execution path used by default CLI and API requests.

## Requirements
### Requirement: Default queries SHALL use one LangGraph executor
Every non-visual, non-small-talk query SHALL run through the same LangGraph act/observe/evaluate loop.

#### Scenario: A query needs web evidence
- **WHEN** web search is allowed and the model selects a web tool
- **THEN** the loop SHALL execute the tool, observe its result, and run the shared critic
- **AND** no static preplanning executor SHALL run before or after the loop

#### Scenario: Search is disabled
- **WHEN** `allow_search=false`
- **THEN** the same loop SHALL run without web search and search-recovery tools
- **AND** the system SHALL NOT switch to a separate local-only pipeline

#### Scenario: No retrieval tool is available
- **WHEN** the loop has no eligible retrieval tool
- **THEN** it SHALL still produce a critic-governed direct or insufficient answer
- **AND** it SHALL NOT fall back to another executor

### Requirement: Tool calls SHALL have independent budgets
Every retrieval tool SHALL enforce its own `max_calls_per_query` budget, while `termination.max_iterations` remains the global loop ceiling.

#### Scenario: A tool reaches its call limit
- **WHEN** the model calls a tool after its per-query limit is spent
- **THEN** the tool SHALL return a structured `budget_exhausted` result
- **AND** the shared critic SHALL decide whether to continue with another eligible tool or terminate

#### Scenario: One tool is exhausted
- **WHEN** one tool has no remaining calls but another tool remains eligible
- **THEN** the exhausted tool SHALL NOT consume the other tool's budget
- **AND** actual usage and limits SHALL be exposed in control metadata

### Requirement: The loop SHALL preserve deterministic shortcuts
The runtime SHALL allow only small talk and visual input to bypass the loop through their bounded direct handlers.

#### Scenario: Small talk is detected
- **WHEN** the deterministic small-talk check accepts the query
- **THEN** the system SHALL answer directly without opening the tool loop

#### Scenario: Critical ambiguity is detected
- **WHEN** `QueryAnalysis.critical_ambiguity=true` after deterministic skill preflight
- **THEN** the system SHALL request clarification before any tool call
- **AND** the clarification terminal state SHALL enter the execution trace
