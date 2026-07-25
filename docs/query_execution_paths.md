# Query Execution Path Map

`utils/query_orchestration.py` is the shared contract boundary. It owns
`QueryAnalysis`, `EvidenceLedger`, the sole `evaluate_termination` critic,
and the bounded `QueryExecutionTrace`.

| Entry or exit path | Contract behavior |
| --- | --- |
| CLI | `main.py` builds `LangChainOrchestrator` and calls the same `answer` entrypoint as the API. |
| Flask | `server.py` builds the same orchestrator through `build_pipeline`; SSE events and durable audit use the shared tracer. |
| Small talk | The deterministic small-talk shortcut answers directly and does not open a tool loop. |
| Visual input | The bounded visual handler remains separate because the loop has no image tool. |
| Critical ambiguity | `QueryAnalysis` requests clarification before tools; accepted deterministic skill preflight may resolve a generic entity reference first. |
| All other queries | `LangChainOrchestrator -> ReactAgentOrchestrator -> ReactLoopGraphRunner` runs `act -> observe -> evaluate`. |
| Search disabled | The same loop runs with `web_search` and `search_recovery` removed from its tool surface. |
| Registered skills | The model selects registry-derived tools; each tool enforces deterministic preflight and its manifest call budget. |
| Web/local retrieval | Tool results carry source tier, canonical reference, and actual tool-call provenance into one `EvidenceLedger`. |
| Termination | The deterministic critic, optional semantic judge, global iteration ceiling, and per-tool budgets produce the sole terminal verdict. |

Responses keep `answer`, `search_hits`, `retrieved_docs`, `control`, and
timing fields. Additive control metadata exposes query analysis, ledger coverage,
actual execution trace, terminal verdicts, and per-tool budget use. There is no
runtime engine switch, static query plan, or post-generation fallback executor.
