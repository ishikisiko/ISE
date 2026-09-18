## MODIFIED Requirements

### Requirement: Every tool SHALL enforce its own call budget
Each wrapper SHALL reset and enforce `max_calls_per_query` independently and SHALL expose `limit` and `used` after the run. The limit value SHALL come from the effective autonomy policy's budget group; a mode that relaxes budgets SHALL raise the numbers without removing the per-tool ceiling.

#### Scenario: A tool budget is exhausted
- **WHEN** the loop calls a tool after its limit is spent
- **THEN** the wrapper SHALL return structured `budget_exhausted`
- **AND** no provider call SHALL occur

#### Scenario: Another tool remains available
- **WHEN** one tool is exhausted and another has remaining calls
- **THEN** the latter SHALL retain its full independent budget

#### Scenario: Budgets follow the effective policy
- **WHEN** a run uses a policy whose budget group differs from the default
- **THEN** each wrapper SHALL enforce the limit from that group
- **AND** every tool SHALL still have a finite limit

## ADDED Requirements

### Requirement: Clarification SHALL be a budgeted local tool
The `ask_user` wrapper SHALL follow the same wrapper contract as other tools for budget accounting and audit safety, while performing no retrieval: it SHALL NOT call any provider, SHALL NOT emit evidence records, and SHALL NOT contribute to evidence-increment accounting.

#### Scenario: The model asks a clarifying question
- **WHEN** the loop calls `ask_user` with a bounded question
- **THEN** the wrapper SHALL consume one unit of its own budget
- **AND** it SHALL NOT produce any `EvidenceItem` or search hit

#### Scenario: The clarification budget is exhausted
- **WHEN** `ask_user` is called beyond its limit
- **THEN** the wrapper SHALL return structured `budget_exhausted`
- **AND** no waiting state SHALL be entered

#### Scenario: Wrapper records stay audit safe
- **WHEN** an `ask_user` call is recorded
- **THEN** the record SHALL contain only the bounded question text and budget facts
- **AND** it SHALL NOT contain prompts, hidden reasoning, or credentials
