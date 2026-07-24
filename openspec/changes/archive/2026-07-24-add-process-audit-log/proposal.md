## Why

`main.py` currently prints only the final answer on success (`main.py:899`): the orchestrator emits a rich stream of workflow step events (`utils/workflow_trace.py`), but the CLI never passes a tracer, so every event is swallowed by `NullWorkflowTracer`. Process data that already exists—routing decisions, generated keywords, search skips, postcheck verdicts, ReAct recovery iterations, per-call timings—is invisible to CLI users and not persisted anywhere, making debugging, post-hoc review, and regression analysis impossible.

## What Changes

- Add an append-only JSONL audit writer (`utils/audit_log.py`, modeled on `BraveUsageRecorder` in `search/search.py:555`) that persists one record per answered turn under `runtime/audit/<conversation_id>.jsonl` (`runtime/` is already gitignored).
- Wire the CLI: new `--audit off|file` flag (default from config, fallback `off`). When active, `main.py` creates a real `WorkflowTracer`, passes it to `orchestrator.answer()`, force-enables timing collection, and writes the audit record after the result returns. Default-off keeps current behavior and `NullWorkflowTracer` zero-cost.
- Wire the orchestrator (`langchain/langchain_orchestrator.py`): when the `audit` config block is enabled, the orchestrator itself persists audit records so Web requests (`server.py`) are covered too, including the conversation-resume path that bypasses `_finalize_response` (`:1354`). CLI flag overrides config for CLI runs; CLI-written and orchestrator-written records are deduplicated per turn.
- Add `audit` config block: `enabled`, `dir`, `include_answer`, `max_files`, `max_bytes_per_record`, with LRU-style eviction of oldest audit files (mirroring `conversation_store.evict_if_needed`).
- Audit writes are strictly best-effort: failures never break the answer pipeline, at most a `[audit]` warning.

## Capabilities

### New Capabilities
- `process-audit-log`: Toggleable, persisted per-turn process audit—workflow step events, control metadata (routing/postcheck/sources), search queries, timing payloads, warnings, and optional answer text—written as JSONL keyed by `conversation_id`, with CLI/config dual control and retention.

### Modified Capabilities
<!-- No existing spec requirements change: default behavior (answer-only stdout, NullWorkflowTracer, web SSE steps) is preserved when audit is off. -->

## Impact

- **Code**: new `utils/audit_log.py`; edits to `main.py` (flag, tracer wiring, force timings, post-run write), `langchain/langchain_orchestrator.py` (stash tracer, audit hook on finalize + resume paths, force TimingRecorder when audit on), `config.json`/`config.example.json` (`audit` block), `server.py` (pass audit override through orchestrator factory if needed).
- **Config**: new optional `audit` object; absent block ⇒ disabled (fully backward compatible).
- **Data**: new files under `runtime/audit/` (gitignored); contain query text and optionally answer text—same sensitivity class as `checkpoints/`.
- **Dependencies**: none (stdlib only).
- **Out of scope (v1)**: new call-level instrumentation (prompts/token counts), replay/analysis tooling, legacy `SmartSearchOrchestrator` support (it has no tracer instrumentation).
