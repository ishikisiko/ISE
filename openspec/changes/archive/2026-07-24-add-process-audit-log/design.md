## Context

The LangChain orchestrator is already instrumented: ~30 `tracer.begin/end/skip` call sites in `langchain/langchain_orchestrator.py` emit structured step events (`{seq, id, title, status, detail?, duration_ms?, items?, badge?}`) through `utils/workflow_trace.py`. The web layer (`server.py:642`) creates a `WorkflowTracer` per request and streams events over SSE; the CLI (`main.py:872`) passes no tracer, so `ensure_tracer(None)` downgrades to a no-op and all process data is discarded. On success the CLI prints only `result["answer"]` (`main.py:899`); timings exist only when `--pretty` sets `show_timings` (`main.py:750`).

Key structural facts established during exploration:

- `_finalize_response` (`langchain_orchestrator.py:1154`) is the convergence point for 9 return paths (visual/small-talk/direct/domain/search-RAG), but the conversation-resume path bypasses it and calls `_record_conversation_turn` directly (`:1354`). `_record_conversation_turn` is therefore the true all-paths choke point.
- `TimingRecorder` is gated on `show_timings`; the orchestrator's flag comes from `create_langchain_orchestrator` (CLI: `args.pretty`; web: `config.displayResponseTimes`, `server.py:302`).
- Precedents to copy: `BraveUsageRecorder` (`search/search.py:555`, lock + makedirs + append), `runtime/` dir (gitignored, already hosts `brave_search_usage.jsonl`), LRU eviction in `conversation_store.evict_if_needed` (`:231`).
- Every CLI run already mints a `conversation_id` (`main.py:870`)—the natural audit key.
- Legacy `SmartSearchOrchestrator` has zero tracer instrumentation.

## Goals / Non-Goals

**Goals:**
- Persist one self-contained JSONL audit record per answered turn: step events, control metadata, search query, timings, warnings, optional answer text.
- Dual entry coverage: CLI (`main.py`) and Web (`server.py`) via an orchestrator-internal hook, including the resume path.
- Toggle hierarchy: CLI flag `--audit off|file` > `config.audit.enabled` > default off.
- Zero overhead and zero behavior change when off (NullWorkflowTracer, no file I/O).
- Best-effort writes: audit failure can never fail an answer.
- Retention: evict oldest files beyond `max_files`.

**Non-Goals:**
- New instrumentation depth (no prompt bodies, token counts, per-hit URL lists beyond existing event payloads).
- Replay/analysis tooling (JSONL + `jq` suffices for v1).
- Legacy orchestrator support.
- Changing `--pretty` semantics (the timings/audit coupling is handled by force-enabling timings, not by repurposing the flag).

## Decisions

### D1: Record granularity = one JSONL line per turn, events nested
Each line is a complete audit unit `{ts, conversation_id, query, allow_search, steps[], control, search_query, response_times, search_warnings, answer?}`, appendable by multi-turn follow-ups and directly analyzable with `jq`. Per-event lines were rejected: they scatter one turn across interleaved lines and force reassembly.

### D2: File layout = one file per conversation
`runtime/audit/<conversation_id>.jsonl`. Multi-turn turns append naturally; per-conversation replay is trivial; LRU eviction operates on whole files (same granularity as checkpoint eviction). A single global file was rejected: unbounded growth, mixed conversations, harder retention.

### D3: Dual hook, shared writer
- `utils/audit_log.py` owns record assembly, redaction/truncation (`max_bytes_per_record`), file writing, and eviction. Stdlib only.
- **CLI hook**: `main.py` builds the tracer, forces `show_timings=True` when audit is on, calls `answer(tracer=...)`, then writes via the shared writer after the result returns (max failure isolation).
- **Orchestrator hook**: `LangChainOrchestrator.answer()` stashes the tracer (`self._current_tracer = ensure_tracer(tracer)`); a `_record_audit_turn(result)` helper is invoked next to `_record_conversation_turn` in both `_finalize_response` and the resume path (`:1354`), covering all 10 return paths when `config.audit.enabled` and no CLI-side write already happened (see D4).

### D4: Dedup between the two hooks
When the CLI drives with audit on, the orchestrator would also see `audit.enabled` in config. To avoid double-writing the same turn, the CLI passes its audit intent down (e.g. `audit_mode="file"` kwarg on `answer()` or an orchestrator constructor flag): the orchestrator hook then skips its own write for that turn. Web requests never set this, so they always use the orchestrator hook. Alternative considered (write both, dedup by ts) rejected as racy.

### D5: Timings force-enable under audit
`TimingRecorder(enabled=self.show_timings or audit_active_for_this_run)` in `answer()`. The orchestrator learns "audit active" from config (web path) or the CLI override flag. This avoids the empty-`response_times` trap when `--pretty`/`displayResponseTimes` are off, without changing their display semantics.

### D6: `include_answer` default `true`
Audit files are self-contained (analyzable without the checkpoints sqlite). `false` slims records and relies on `conversation_id` join against `conversation_turns`. Precedent: turns table already stores full query+answer, so sensitivity class is unchanged; files live under gitignored `runtime/`.

### D7: Retention = file-count LRU
After each write, if file count in `audit.dir` exceeds `max_files` (default 200), delete oldest by mtime. Age-based and size-based caps were considered; file-count matches `max_threads` precedent and is trivially testable.

### D8: Config schema
```json
"audit": {
  "enabled": false,
  "dir": "runtime/audit",
  "include_answer": true,
  "max_files": 200,
  "max_bytes_per_record": 65536
}
```
Absent block ⇒ disabled. `max_bytes_per_record` truncates the largest string fields (answer first) with a `truncated: true` marker, bounding worst-case line size.

## Risks / Trade-offs

- [Audit write throws inside orchestrator] → wrap in try/except mirroring `_record_conversation_turn`; emit at most `print(f"[audit] ...")`.
- [Double audit records for CLI turns] → D4 explicit skip signal; verified by a CLI test asserting exactly one line per turn.
- [File volume under heavy web use when config-enabled] → `max_files` LRU + default-off; documented in config example.
- [Resume path divergence: resume result shape differs from fresh turns] → `_record_audit_turn` tolerates missing keys (`.get` everywhere); spec requires only the envelope + steps + whatever metadata exists.
- [Sensitive content in audit files] → gitignored `runtime/`; `include_answer: false` escape hatch; documented alongside `checkpoints/` handling in AGENTS.md wording (docs only if asked).
- [Orchestrator constructor signature grows] → prefer reading `self.config["audit"]` (already stored, `:136`) over new constructor params; only the per-run CLI override needs a new optional kwarg on `answer()`.

## Migration Plan

1. Ship writer + CLI hook + orchestrator hook behind default-off config.
2. No migration: absent `audit` block = today's behavior. Rollback = delete flag/config; no persistent format depends on it (JSONL is additive, readers optional).

## Open Questions

- Exact `answer()` kwarg name for the CLI override (`audit_mode` vs constructor flag on `create_langchain_orchestrator`)—decide at implementation time; spec treats it as internal.
- Whether a future `--audit live|summary` terminal rendering reuses the same tracer subscription (writer design keeps events in memory, so this stays possible).
