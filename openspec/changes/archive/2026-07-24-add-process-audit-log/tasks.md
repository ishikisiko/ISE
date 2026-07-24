## 1. Audit writer module

- [x] 1.1 Create `utils/audit_log.py` with an `AuditRecorder` (modeled on `BraveUsageRecorder`, `search/search.py:555`): thread-safe append, `os.makedirs(dir, exist_ok=True)`, stdlib only
- [x] 1.2 Implement record assembly: envelope (`ts`, `conversation_id`, `query`, `allow_search`) + `steps` from `tracer.events` + `control`/`search_query`/`response_times`/`search_warnings` from result, all via `.get` with missing-key tolerance
- [x] 1.3 Implement `include_answer` handling and `max_bytes_per_record` truncation (answer first, `truncated: true` marker)
- [x] 1.4 Implement LRU eviction: after write, if file count in audit dir > `max_files`, delete oldest by mtime
- [x] 1.5 Add `resolve_audit_settings(config, cli_override)` helper: returns effective `{enabled, dir, include_answer, max_files, max_bytes_per_record}` honoring CLI > config > default-off

## 2. Orchestrator hook

- [x] 2.1 In `LangChainOrchestrator.answer()` (`langchain/langchain_orchestrator.py:515`), stash `self._current_tracer = ensure_tracer(tracer)` and read resolved audit settings from `self.config`
- [x] 2.2 Force-enable timings under audit: `TimingRecorder(enabled=self.show_timings or audit_active)` (`:516`)
- [x] 2.3 Add `_record_audit_turn(result)` next to `_record_conversation_turn` (`:1207`): best-effort try/except, at most one `[audit]` warning print
- [x] 2.4 Invoke `_record_audit_turn` from `_finalize_response` (`:1201`) and from the resume path (`:1354`) so all 10 return paths are covered
- [x] 2.5 Add per-run CLI override plumbing: optional `audit_mode` kwarg on `answer()`; when the CLI already wrote/skips audit for the turn, the orchestrator hook must not double-write (D4 dedup)

## 3. CLI wiring

- [x] 3.1 Add `--audit` argparse flag with choices `off|file`, default `None` (meaning: defer to config)
- [x] 3.2 In `main()`: resolve audit settings; when active, create `WorkflowTracer()`, pass `tracer=` into `orchestrator.answer()` along with the dedup override, and set `show_timings = args.pretty or audit_active` (`main.py:750`)
- [x] 3.3 After `answer()` returns, write the audit record via `AuditRecorder` (both success and error/warning paths), exactly once per run
- [x] 3.4 Keep stdout contract unchanged: answer-only on success, `[conversation_id]` line, timings block only under `--pretty`

## 4. Config & docs

- [x] 4.1 Add `audit` block to `config.example.json` with defaults (`enabled: false`, `dir: "runtime/audit"`, `include_answer: true`, `max_files: 200`, `max_bytes_per_record: 65536`) and comments on sensitivity/retention
- [x] 4.2 Verify `server.py` needs no change beyond config (orchestrator reads `self.config`); adjust `create_langchain_orchestrator` only if config propagation is missing

## 5. Tests & validation

- [x] 5.1 Add `tests/test_audit_log.py`: record assembly (missing keys), truncation marker, include_answer toggle, LRU eviction, one-line-per-turn append
- [x] 5.2 Test dedup: CLI-driven turn with config enabled produces exactly one record
- [x] 5.3 Test failure safety: unwritable audit dir does not break `answer()` (monkeypatch/bad path)
- [x] 5.4 Manual: `python main.py "sanity check" --audit file` → inspect `runtime/audit/<cid>.jsonl`; follow-up with `--conversation-id` → second line appended with `conversation_resume` step
- [x] 5.5 Manual: `python server.py` with `audit.enabled=true` → POST `/api/answer` → record appears; default-off config → `runtime/audit/` untouched
- [x] 5.6 Run `python -m pytest` for regression
