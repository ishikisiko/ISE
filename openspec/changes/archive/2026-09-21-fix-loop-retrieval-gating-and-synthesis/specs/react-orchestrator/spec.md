## MODIFIED Requirements

### Requirement: ReactAgentOrchestrator SHALL expose the production answer interface
The system SHALL provide `ReactAgentOrchestrator.answer()` with the query, search eligibility, conversation, analysis, trace, and generation controls used by the default orchestrator. The `force_search` argument SHALL be forwarded to the loop runner and SHALL be echoed in `control.request_parameters.force_search`; the number of search tool calls the loop actually made SHALL be exposed as `control.loop_search_calls`.

#### Scenario: A normal query is delegated
- **WHEN** the default `LangChainOrchestrator` receives a non-shortcut query
- **THEN** it SHALL invoke `ReactAgentOrchestrator.answer()`
- **AND** the result SHALL contain `answer`, `control`, `search_hits`, and unified evidence metadata

#### Scenario: A conversation id is supplied
- **WHEN** `conversation_id` names an existing LangGraph checkpoint
- **THEN** the loop SHALL resume that thread using the bounded conversation history policy
- **AND** `control.conversation_resumed` SHALL report the actual resume state

#### Scenario: force_search is requested
- **WHEN** `answer()` is called with `force_search=True` and `allow_search=True`
- **THEN** the loop runner SHALL receive `force_search=True`
- **AND** `control.request_parameters.force_search` SHALL be `true`
- **AND** `control.loop_search_calls` SHALL count the `web_search` / `search_recovery` calls made in this turn
