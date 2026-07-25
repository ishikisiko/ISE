## Why

Authority-required multi-model pricing queries can return ordinary web pages even when
the configured official domains are known. The current recovery path issues one mixed
query, stops provider fallback after any result, and can label an unrelated configured
domain as official, so the response may look grounded when no target official evidence
was retrieved.

## What Changes

- Add a bounded, per-entity official-domain recovery plan for authority-required
  comparisons. It will search configured official domains separately and record target
  coverage in the execution trace.
- Continue provider fallback during this recovery only when a provider returns no
  result in the target official domains; retain the existing inexpensive first-hit
  behavior for ordinary web search.
- Make official-page selection and metadata entity-aware so unrelated configured
  domains cannot be counted or displayed as official evidence for the query.
- Repair the keyword-generation prompt template and prevent authority-required pricing
  comparisons from producing a normal answer when target official coverage remains
  incomplete.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `query-plan-orchestration`: Plans authority recovery as bounded target-domain work
  for the comparison members that have configured official domains.
- `web-search-provider-routing`: Allows a planned authority-recovery step to continue
  deterministic provider fallback until it finds target-domain evidence or exhausts a
  bounded provider list.
- `evidence-policy-routing`: Requires target-by-target official coverage before a
  pricing comparison is considered verified or normally answered.
- `evidence-source-layer`: Preserves the query entity that a selected official URL
  supports and prevents unrelated configured domains from being labelled official.

## Impact

Affected runtime paths are `utils/query_orchestration.py`, `search/search.py`,
`langchain/langchain_rag.py`, and `langchain/langchain_orchestrator.py`, together with
their focused tests and the additive response/audit metadata. No provider credentials
or public request schema changes are required.
