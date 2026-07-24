# Query Execution Path Map

`utils/query_orchestration.py` is the shared contract boundary. It owns
`QueryAnalysis`, `QueryPlan`, `EvidenceLedger`, `VerificationOutcome`, and the
bounded `QueryExecutionTrace` projection.

| Entry or exit path | Adapter and contract behavior |
| --- | --- |
| CLI | `main.py` builds the LangChain orchestrator and passes the normal `answer` options. CLI audit owns the JSONL write in external mode. |
| Flask | `server.py` builds the same orchestrator through `build_pipeline`; `/api/answer` preserves additive `control` fields and stream tracing remains separate. |
| Direct and small-talk | `LangChainOrchestrator.answer` records analysis and adds a direct plan only at finalization. No evidence executor is started. |
| Local-only | `allow_search=false` creates a local/direct plan before `_handle_local_only`; the primary RAG layer receives the plan and only enables the local source. |
| Structured domain API | Only weather, transportation, finance, sports, and location can create a domain API step. The call is controller-traced and its normalized result enters the ledger. |
| Default search | The orchestrator binds a plan before keyword generation. `SearchRAGChain` executes only plan-authorized web/local steps, captures provider attempts before later calls reset client state, and sends retained ledger evidence to answer construction. |
| Temporal recovery | The granular historical search is callable only from a declared `temporal_recovery` step and consumes controller query/recovery budget. |
| ReAct fallback | Existing post-check fallback remains compatible, but a nonrecoverable plan verification outcome blocks it; the trace records the fallback decision. |

Existing `answer`, `search_hits`, `retrieved_docs`, `control`, and timing
fields remain available. Plan, coverage, verification, and trace facts are
additive under `control`.
