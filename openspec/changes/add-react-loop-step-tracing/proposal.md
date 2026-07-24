## Why

The default LangGraph ReAct path reports only an outer "ReAct loop" start/end
event and a final `LoopVerdict` summary. The SSE API therefore has no
auditable record of what each iteration attempted, which tool actually ran,
or whether a displayed tool-like response was ever executed. This made a
pricing-comparison run look active while raw `<function>...</function>` text
was returned as an answer instead of a structured search call.

## What Changes

- Emit safe, ordered workflow events for every LangGraph ReAct iteration:
  iteration start, tool request, tool outcome, and evaluation verdict.
- Include the tool name, normalized public query arguments, success/failure,
  duration, bounded result summary, and missing constraints where available;
  do not emit prompts, hidden reasoning, credentials, or full tool payloads.
- Pass those events through the existing SSE and process-audit paths without
  changing their transport contract or existing response fields.
- Normalize the observed XML-style function request form into a supported
  tool call when it names an enabled tool; unrecognized malformed tool markup
  becomes an explicit traceable failure rather than silently becoming the
  final answer.
- Keep resumed-turn verdict history in the checkpoint while returning and
  rendering only the verdicts produced by the current request, so the workflow
  view does not replay earlier turns.
- Keep a single detailed verdict presentation layer and reject model
  search-plan/process narration as a final answer when no tool call was made.
- Preserve the actual result list for each individual search-provider API call
  before merge/deduplication, and expose it as a separately expandable audit
  record rather than attributing final merged references back to a provider.
- Expose selected-page extraction records only when extraction really ran,
  identifying the page URL, provider, outcome, and extracted size without
  adding automatic full-page fetching to ordinary search requests.

## Capabilities

### New Capabilities

<!-- None. -->

### Modified Capabilities

- `react-agent`: the LangGraph ReAct executor exposes safe, per-step action
  events and handles compatible structured tool-call representations.
- `react-loop-evaluation`: every iteration's verdict is emitted as an
  auditable event in addition to the existing final metadata summary.
- `query-execution-trace`: streamed and persisted execution traces include
  the ReAct fallback's actual tool actions and outcomes.

## Impact

- `orchestrators/react_loop_graph.py` and
  `orchestrators/react_agent_orchestrator.py` gain event propagation and
  tool-call normalization.
- Existing `WorkflowTracer`, `/api/answer/stream`, and process-audit output
  receive additive step events and bounded audit-record payloads.
- Search providers preserve their individual returned hits through combined
  and priority/fallback clients; the frontend renders every provider call as
  a collapsed result group with direct links and snippets.
- The selected-page extraction adapter exposes a similarly safe page-level
  record when it is called. Ordinary search remains snippet-only and does not
  start page extraction merely to populate the audit view.
- ReAct response metadata now scopes `loop_verdicts` to the current request;
  checkpointed evidence and historical verdicts remain available to the
  graph for continuation.
- ReAct unit, orchestration, SSE, and audit regression tests gain coverage
  for event ordering, safe payloads, failures, and XML-style tool calls.
