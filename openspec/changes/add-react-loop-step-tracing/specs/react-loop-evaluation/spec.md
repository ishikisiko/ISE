## ADDED Requirements

### Requirement: Loop verdicts SHALL be streamed as safe iteration details
Every LangGraph ReAct evaluation SHALL complete the corresponding iteration
workflow event with its `LoopVerdict` facts. The event SHALL expose the
continue/terminate reason, evidence increment flag, and bounded met/missing
constraint summaries without exposing model reasoning or answer drafts.

#### Scenario: An iteration continues
- **WHEN** evaluation decides another iteration is required
- **THEN** the completed iteration event SHALL state the continue reason and
  missing constraints
- **AND** the next iteration SHALL begin as a separate ordered event

#### Scenario: An iteration terminates
- **WHEN** evaluation reaches succeeded, exhausted, stagnated, or
  unrecoverable termination
- **THEN** the completed iteration event SHALL identify the terminal reason
- **AND** the final result SHALL retain the existing `loop_status` and
  `loop_verdicts` metadata

### Requirement: Each verdict SHALL have one detailed presentation layer
The complete verdict items SHALL belong to the per-iteration evaluation event.
The enclosing iteration and outer-loop completion events MAY summarize status,
but SHALL NOT repeat those items.

#### Scenario: A traced iteration completes
- **WHEN** `react_evaluate_N` completes with a verdict
- **THEN** its event SHALL contain the detailed verdict facts
- **AND** `react_iteration_N` and `react_loop` SHALL not duplicate those
  verdict rows

### Requirement: Resumed responses SHALL expose current-turn verdicts only
The result and additive control metadata for a resumed request SHALL contain
only verdicts generated during that request. The checkpoint MAY retain earlier
verdicts for continuation.

#### Scenario: A conversation continuation starts after prior ReAct turns
- **WHEN** the graph resumes from a checkpoint that contains verdict history
- **THEN** current-turn `loop_verdicts` SHALL exclude the earlier turn's rows
- **AND** retained evidence and historical state SHALL remain available to the
  graph
