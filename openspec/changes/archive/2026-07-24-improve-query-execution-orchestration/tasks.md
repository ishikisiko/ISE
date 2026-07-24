## 1. Orchestration Contracts And Migration Baseline

- [x] 1.1 Resolve the lifecycle of `add-process-audit-log` and freeze its current writer/retention contract before extending its payload.
- [x] 1.2 Map CLI, Flask, direct, local-only, domain-API, default search, and ReAct fallback entry/exit paths that must adapt to the new orchestration contract.
- [x] 1.3 Define typed `QueryAnalysis`, `QueryPlan`, `EvidenceLedger`, `VerificationOutcome`, and `QueryExecutionTrace` models with bounded serializable summaries.
- [x] 1.4 Define compatibility adapters so existing `answer`, `search_hits`, `retrieved_docs`, `control`, and timing fields remain usable during staged migration.

## 2. Query Analysis And Evidence Policy

- [x] 2.1 Consolidate deterministic parsing of entities, comparison membership, ambiguity, time scope, freshness, claim classes, and search allowance into shared query analysis.
- [x] 2.2 Integrate optional LLM analysis behind deterministic validation and conservative fallback behavior.
- [x] 2.3 Implement a composable evidence-policy registry for authority, comparison coverage, temporal coverage, freshness, and ambiguity requirements.
- [x] 2.4 Replace duplicated bare-keyword temporal checks with policy-derived temporal eligibility while preserving valid historical and ranking behavior.
- [x] 2.5 Generate bounded `QueryPlan` steps from analysis and policies, including explicit domain API, local, web, clarification, and recovery decisions.
- [x] 2.6 Repair keyword-template formatting and make keyword generation an implementation detail of plan construction rather than an untracked routing side effect.

## 2A. Direct Reference Provider Foundation

- [x] 2A.1 Support the existing `parellel2` and `firecrawl2` configuration fields as either bare API-key strings or structured provider settings, while documenting the current official Extract and Scrape endpoints.
- [x] 2A.2 Implement provider-neutral selected-URL extraction contracts plus Parallel Extract and Firecrawl Scrape adapters with bounded, secret-safe normalized results and failures.
- [x] 2A.3 Add deterministic provider request/response, fallback, configuration, and no-secret serialization tests.
- [x] 2A.4 Perform a controlled live extraction probe for both configured providers and record only provider/status/content-size evidence.

## 3. Plan-Driven Execution

- [x] 3.1 Add a deterministic plan controller to the unified RAG path that executes ordered plan steps without changing non-search fast paths.
- [x] 3.2 Adapt web, local, and domain evidence retrieval into plan-step executors with a common result shape.
- [x] 3.3 Enforce query, result, time, and recovery budgets at the controller boundary.
- [x] 3.4 Permit replanning only from declared recoverable evidence gaps; return clarification-required or evidence-insufficient states when appropriate.
- [x] 3.5 Preserve explicit `search_sources` constraints and make provider selection a plan-authorized step.

## 4. Evidence Ledger And Fusion

- [x] 4.1 Extend normalized `EvidenceItem` metadata with canonical reference, originating plan step, source tier, and policy/constraint coverage markers.
- [x] 4.2 Build an evidence ledger that records retained, merged, limited, and rejected evidence with compact reasons.
- [x] 4.3 Apply canonical URL deduplication and final evidence/reference caps after all retrieval paths, independently of reranker availability.
- [x] 4.4 Update answer-context construction to consume only retained ledger evidence and its coverage summary.

## 5. Verification, Recovery, And Response Control

- [x] 5.1 Implement plan-aware rule verification that compares draft answers and evidence against entity, comparison, time, freshness, and authority constraints.
- [x] 5.2 Produce typed outcomes: complete, recoverable gap, clarification required, and evidence insufficient.
- [x] 5.3 Route only recoverable, in-budget gaps to a bounded recovery step or existing ReAct fallback; never use open-ended repeat search for ambiguity.
- [x] 5.4 Add compatible control metadata for plan summary, executed steps/providers, evidence coverage, verification outcome, and final executor.
- [x] 5.5 Update frontend/API metadata handling only where required to display or safely ignore the additive control fields.

## 6. Trace And Process Audit

- [x] 6.1 Capture an append-only execution trace at each plan step before later client calls can reset timing or error state.
- [x] 6.2 Separate configured, requested, eligible, and executed providers in response metadata and trace records.
- [x] 6.3 Persist a bounded, secret-safe projection of the trace and retained-evidence decisions through the existing process audit writer.
- [x] 6.4 Verify audit truncation, retention, failure isolation, and compatibility with existing JSONL readers.

## 7. Tests, Evaluation, And Rollout

- [x] 7.1 Add unit tests for query analysis and policy composition across generic, comparison, temporal, freshness, domain-data, and ambiguous-entity cases.
- [x] 7.2 Add plan-controller tests for budgets, skipped steps, recoverable replans, clarification gates, provider constraints, and failure paths.
- [x] 7.3 Add evidence-ledger tests for URL deduplication, source-tier policy, constraint coverage, reranker-disabled final caps, and retained-reference metadata.
- [x] 7.4 Add verifier tests for complete, recoverable, clarification-required, and evidence-insufficient outcomes.
- [x] 7.5 Add audit/trace and CLI/Flask response-contract tests, including multi-provider/fallback timing and no-secret serialization.
- [x] 7.6 Add the reported model API pricing comparison as a regression scenario, alongside ordinary comparisons and valid historical queries, rather than a dedicated production workflow.
- [x] 7.7 Run `conda run -n env1 python -m pytest -q`, exercise CLI and `/api/answer` with deterministic stubs, then perform controlled live checks and inspect trace/audit artifacts without exposing credentials.
