## 1. Targeted Recovery Planning

- [x] 1.1 Derive bounded configured official-domain targets from authority-required comparison members and add a planned recovery step.
- [x] 1.2 Extend verification so mapped pricing-comparison members require retained official coverage.

## 2. Retrieval And Provenance

- [x] 2.1 Add target-domain-aware priority-provider fallback that preserves concrete call records and timing metadata.
- [x] 2.2 Execute target-domain recovery in the unified RAG path and enrich only target-official pages as official evidence.
- [x] 2.3 Correct official metadata/counting so unrelated configured domains are not labelled official for the active query.

## 3. Answer Safety And Keyword Reliability

- [x] 3.1 Escape the keyword prompt JSON example so keyword generation no longer fails template validation.
- [x] 3.2 Return an explicit insufficient-evidence response rather than a normal answer for incomplete mapped pricing comparisons.

## 4. Verification

- [x] 4.1 Add focused unit and integration regression tests for planning, provider escalation, entity-aware official labels, and answer gating.
- [x] 4.2 Run focused tests, the full pytest suite, OpenSpec strict validation, and diff hygiene checks.
- [x] 4.3 Run the original query through the actual ISE pipeline and inspect its persisted audit trace for target official coverage.
