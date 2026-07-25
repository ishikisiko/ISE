## Context

The Flask server already creates a `WorkflowTracer` and forwards each emitted
event through SSE and the process audit. The frontend already renders a step's
`detail` and `items`. The gap is inside `ReactLoopGraphRunner`: it has no
tracer and only its caller emits a single outer `react_loop` begin/end pair.

The same runner treats tool calls as either native LangChain `tool_calls` or a
JSON-only shim. A provider response in the observed XML-style function form is
therefore stored as ordinary answer text, so it is neither executed nor
traceable.

## Goals / Non-Goals

**Goals:**
- Stream a bounded, human-readable account of each LangGraph ReAct iteration
  and tool outcome through the existing backend paths.
- Preserve `LoopVerdict` and existing `control` fields while adding a bounded
  `react_trace` projection to the final response.
- Execute compatible XML-style function requests as normal tool calls and
  make malformed or unsupported requests visible without leaking them as the
  final answer.
- Keep trace payloads safe for the browser and persisted audit log.

**Non-Goals:**
- Do not expose chain-of-thought, full prompts, full web page/tool payloads,
  API credentials, or authorization headers.
- Do not replace the generic frontend workflow renderer or change SSE framing.
- Do not add detailed callback instrumentation to the deprecated legacy
  `AgentExecutor` engine in this change.

## Decisions

### D1: Instrument the LangGraph nodes through the existing WorkflowTracer

`ReactLoopGraphRunner` accepts an optional tracer. `act` begins a stable
per-iteration step; `observe` creates a child-like event for every tool call;
`evaluate` emits the verdict and completes the iteration step. The outer
orchestrator retains its existing `react_loop` event.

This streams immediately because `WorkflowTracer` listeners are already
connected to the server queue. It also preserves the existing audit writer,
which records the same events. A new event transport or a LangGraph callback
handler would duplicate lifecycle and ordering behavior for no benefit.

### D2: Define a safe trace projection, not a transcript

Events include only iteration number, tool name, bounded public query
arguments, outcome status, elapsed time, result count where detectable, and
the rule/judge verdict. Tool output body, model content, prompts, and raw
exception payloads are never placed in trace details. Error summaries are
bounded and redact credential-like assignments and URL query strings.

The runner derives `control.react_trace` from its own emitted events with a
fixed cap. This makes the synchronous response inspectable as well as the SSE
stream, while the audit writer continues to enforce its global byte cap.

### D3: Normalize the observed XML function-call form at the graph boundary

After a model act response, the runner first uses native `tool_calls`; if none
exist, it recognizes the bounded `<function>name</function><query>...</query>`
form only when `name` is in the enabled tool set. It converts that into the
same normalized tool-call dictionary used by native calls.

Unknown or malformed XML function markup records a traceable invalid-request
event, supplies corrective feedback for another iteration, and is never used
as the final answer draft. Supporting arbitrary XML schemas or executing
unknown function names is intentionally rejected.

### D4: Keep existing completion metadata compatible

`control.loop_verdicts`, `loop_status`, and all existing SSE step IDs remain
available. The new IDs are additive and contain the iteration number, so the
generic frontend can render them without a protocol migration. The UI adds a
label only for the new invalid-tool-request verdict reason.

### D5: Separate checkpoint history from the current request presentation

Conversation checkpoints retain verdict history for loop continuity, but the
runner records the pre-invocation verdict count and returns only the suffix
created by the current request. The outer `react_loop` event remains a compact
status summary once per-step trace events exist; it does not repeat their
verdict rows. Similarly, the iteration wrapper closes with a concise
completion detail, while `react_evaluate_N` owns the complete verdict items.

When a model returns first-person search planning or process narration without
a structured tool call, the runner records a bounded invalid-final-response
event, supplies a corrective retry message, and never selects that text as the
answer. This is a response-quality guard, not exposure or retention of hidden
reasoning.

### D6: Preserve per-API call evidence before result fusion

Search providers retain a bounded snapshot for every actual provider request:
provider identity, public query summary, duration, success/failure, and the
returned title/URL/snippet list. Composite and priority clients forward the
child snapshots in the order their underlying calls completed or were tried;
they do not fabricate a provider-level result list from the final merged,
reranked evidence set. A Brave key fallback similarly records its primary and
secondary HTTP attempts separately.

`WorkflowTracer` gains an additive, strongly bounded record-list payload. The
search RAG path emits one workflow step per provider request, and the ReAct
runner projects the same snapshots from direct search and recovery tools. The
existing summary step remains a concise aggregate only.

The selected-page extraction adapters use the same record shape when invoked:
one record per requested URL with provider, outcome, request ID when available,
and content length. They never expose extracted body text. This change does
not select or automatically fetch search-result URLs; a record only appears
after a real selected-page extraction call.

### D7: Render audit records as collapsed, linkable lists

The generic workflow renderer recognizes the additive record-list field. Each
API step gets one closed native `details` group whose summary names the result
kind and count. Search rows link to their returned URLs and show the title,
domain, and bounded snippet. Extraction rows link to the extracted page and
show the provider and content-size/status facts. This preserves scanability for
normal runs while allowing a reviewer to inspect every actual call.

## Risks / Trade-offs

- [More SSE events increase audit size] -> Per-event fields are bounded, tool
  output is omitted, `react_trace` has a fixed event cap, and existing audit
  truncation remains authoritative.
- [Provider XML differs from the observed form] -> Accept only the documented
  minimal form; unsupported formatting visibly fails and prompts a retry
  instead of being silently mistaken for an answer.
- [A tool returns an error as text rather than raising] -> Recognize standard
  error prefixes as failed observations for both the trace and loop error
  accounting.
- [A query contains sensitive values] -> Preserve only the bounded query
  argument already supplied by the user, apply redaction to trace text, and
  omit all raw outputs.
- [A provider emits prose about its next action instead of a tool call] ->
  Reject clearly process-like text, request a structured call or a direct
  answer, and fall back to the existing neutral exhaustion message rather than
  displaying that prose as an answer.
- [Audit records disclose a page body or URL secrets] -> Allowlist only
  title, URL without query/fragment, bounded snippet, provider, outcome,
  request ID, and content size; omit raw response bodies, headers, credentials,
  and opaque metadata.
- [Merged results are misleadingly shown as one provider call] -> Capture
  snapshots at each concrete client boundary, then flatten child snapshots in
  composite/priority clients before fusion.

## Migration Plan

1. Add the runner tracer and safe event helpers with no API removals.
2. Pass the tracer from `ReactAgentOrchestrator` and expose the bounded result
   projection under a new control field.
3. Add tests for native calls, XML normalization, invalid call handling, and
   streamed/audited event safety.
4. Rollback is code-only: removing the new optional events restores the prior
   outer-loop-only behavior without checkpoint migration.

## Open Questions

- The legacy `AgentExecutor` path remains intentionally coarse-grained. If it
  is promoted from rollback-only status, it will need a separate callback
  tracing change.
