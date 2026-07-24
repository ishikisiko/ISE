## Context

The current default pipeline is structurally a sequence, not an orchestrated
workflow. It independently performs domain classification, optional domain
API work, search/no-search routing, keyword generation, search retrieval,
special-case fallback, evidence fusion, answer generation, and optional
post-check. Each stage carries only fragments of intent forward. Several
stages therefore re-detect time, comparison, source, or evidence semantics
from raw text, and a later stage can override the practical meaning of an
earlier stage without an explicit decision record.

This creates three system-wide failures:

- Query constraints are not a shared contract. Entity ambiguity, comparison
  coverage, freshness, authority, and time range are interpreted differently
  by classifiers, search helpers, and post-checks.
- Retrieval is not a plan. Special branches can issue hidden fan-out queries,
  and reranker availability changes final retention semantics.
- Verification and observability are afterthoughts. Post-check is disconnected
  from the retrieval objective, while audit stores final state rather than the
  plan, action sequence, and evidence decisions that produced it.

The model API pricing query is useful because it crosses all of these seams:
it has multiple entities, an ambiguity risk, a comparison constraint, numeric
claims requiring authority, and no temporal requirement. The correction is
not to encode a model-pricing micro-workflow; it is to make those constraints
first-class for every query class.

Constraints:

- Default CLI and Flask paths must continue to share the same core behavior.
- Existing top-level response fields remain compatible during migration.
- Existing direct, local-only, and domain-API paths stay fast; the design must
  not route every question through an expensive multi-step agent.
- No new external dependency is required. The first implementation uses a
  deterministic orchestration state machine and existing LLM/search clients.
- API keys, request headers, full prompts, and page bodies MUST NOT be placed
  in response control data or persistent audit records.
- The pending process-audit change remains the writer foundation and must be
  resolved before concurrent changes to its record contract.

## Goals / Non-Goals

**Goals:**

- Establish one shared `QueryAnalysis -> QueryPlan -> EvidenceLedger ->
  VerificationOutcome` contract for default search handling.
- Make evidence policy explicit: source authority, freshness, comparison
  coverage, temporal coverage, and ambiguity are plan constraints, not
  scattered keyword side effects.
- Execute only bounded plan steps, capture each attempt, and permit re-plan
  only for declared recoverable evidence gaps.
- Make answer acceptance depend on plan coverage and evidence sufficiency.
- Give users and developers a compact, truthful execution trace.
- Use the pricing incident and other existing query families as regression
  cases for the common orchestration model.

**Non-Goals:**

- Building a universal autonomous agent that replaces all direct paths.
- Creating per-query-type pipelines for pricing, sports, weather, rankings,
  and every future topic.
- Maintaining an exhaustive entity catalog or scraping sources that require
  authentication.
- Treating an LLM judge as the sole authority for factual correctness.
- Exposing internal chain-of-thought, credentials, full prompts, or page text.

## Decisions

### D1: Introduce a small shared orchestration state model

The default path will carry four explicit structures:

```text
QueryAnalysis
  intent_shape, entities, ambiguity, constraints, claim_classes,
  freshness, time_scope, search_allowed

QueryPlan
  analysis, evidence_policy, ordered_steps, query_budget, result_budget,
  clarification_gate

EvidenceLedger
  evidence items, canonical URL, source tier, originating step,
  claim/constraint coverage, retain/reject reason

VerificationOutcome
  complete | recoverable_gap | clarification_required |
  evidence_insufficient, missing_constraints, next_action
```

This is deliberately smaller than a general agent state graph. It gives every
stage a stable contract while preserving existing response structures as
adapters. The current `domain` remains an execution hint for domain APIs, not
the complete semantic representation of a query.

Alternative considered: extend the current `domain` enum with more labels.
Rejected because domains mix source implementation with user intent, cannot
represent multiple concurrent constraints, and force every new failure into a
new label.

### D2: Derive a plan from constraints, not from a single classifier label

The analyzer combines deterministic parsing and optional LLM assistance, then
validates the result. It extracts comparison membership, explicit time range,
freshness, numeric/authoritative claim requirements, and unresolved entity
references. A bare comparison word carries a comparison constraint only; it
does not create historical retrieval by itself.

The plan resolves one of a small set of execution modes:

- fast direct/local/domain paths when analysis shows no evidence plan is needed;
- one or more bounded web/domain/local evidence steps when a plan is needed;
- clarification before retrieval when a required entity or constraint is too
  ambiguous to safely plan.

The earlier model-pricing incident becomes: `comparison + numeric tariff claim
+ authority requirement + one ambiguous entity`. The plan selects a generic
authoritative-source policy and, when ambiguity blocks it, a clarification
outcome. It does not invoke a separate pricing-only orchestration engine.

### D3: Add reusable evidence policies

Evidence policy is a composable rule set selected from the plan, not a list of
hard-coded provider keywords. Initial policies include:

- **authority policy** for current/numeric/compliance-sensitive claims:
  require first-party, official, or explicitly trusted evidence before a claim
  is accepted;
- **comparison coverage policy**: every requested entity/alternative must be
  represented, absent, or explicitly unresolved;
- **temporal coverage policy**: historical expansion requires an explicit time
  constraint and specifies which period coverage is missing;
- **freshness policy**: current facts receive date-aware search constraints;
- **ambiguity policy**: unresolved critical entities require clarification
  rather than speculative source selection.

Policies can be combined. A price comparison uses authority plus comparison
coverage; a historical ranking comparison uses temporal plus comparison
coverage. This yields the correct distinction without creating bespoke flows.

Alternative considered: a global first-party-only search mode. Rejected
because it would unnecessarily weaken broad research and discovery queries.

### D3a: Treat selected-page retrieval as a separate provider contract

An explicitly selected page is not a web-search provider request. The plan
will represent it as a `direct_reference` step with one or more target URLs,
an evidence purpose, source-tier expectation, and a bounded fallback order.
The shared selector can choose a registry URL, a user-supplied URL, or a URL
found by a separately bounded discovery step; the fetch adapter only retrieves
the URL it receives.

The first provider foundation supports the existing `parellel2` configuration
field for Parallel Extract and `firecrawl2` for Firecrawl Scrape. Both fields
accept the user's existing bare API-key string and an object form with
`api_key`, endpoint, timeout, and provider-specific safe options. Their
spellings are retained as compatibility keys; internal code uses descriptive
provider identifiers.

- **Parallel Extract** calls `POST /v1/extract` with `x-api-key`, selected
  `urls`, and an optional objective. It normalizes full content or focused
  excerpts, per-URL errors, title, publication date, and provider request IDs.
- **Firecrawl Scrape** calls `POST /v2/scrape` with bearer authentication,
  `formats: ["markdown"]`, `onlyMainContent: true`, ad/base64-image removal,
  and TLS verification enabled. It does not forward caller cookies, arbitrary
  headers, browser actions, or prompt-like cleanup settings.

The adapter contract returns normalized page content and bounded error records
without exposing API keys or raw provider payloads. It remains independent of
the existing search clients, whose priority semantics are not valid for page
fetching. The later plan controller can use the common adapter router for
provider fallback after URL selection.

### D4: Execute plan steps through a budgeted controller

The primary RAG executor becomes a controller that executes `QueryPlan.steps`
in order. Each step has a purpose, allowed source types/providers, query ID,
and max results. The controller owns per-turn query, result, time, and recovery
budgets. It may add a recovery step only when `EvidenceLedger` and
`VerificationOutcome` identify a declared recoverable gap.

The existing temporal granular fallback becomes one implementation of the
temporal coverage policy. It cannot run from raw-keyword matching. Likewise,
domain APIs become explicit plan steps rather than a parallel side channel.

Alternative considered: move all queries directly to the existing ReAct agent.
Rejected because it adds cost and variability to simple paths and lacks the
deterministic coverage contract needed for reliable default behavior.

### D5: Make evidence fusion plan-aware

Every retrieved item is normalized into an evidence ledger entry with a
canonical URL/reference, source tier, originating plan step, and coverage
labels. Deduplication occurs by canonical identity before and after fusion.
The final retention limit applies regardless of reranker configuration; rerank
is a ranking signal, not a correctness or safety boundary.

The answer builder receives only retained ledger entries and a coverage
summary. It cannot present a numeric statement as verified when the authority
policy has not been met. Secondary sources remain usable for discovery or
context, but their policy limitations travel with the evidence.

### D6: Replace optional post-check semantics with plan-aware verification

Verification compares the draft answer and ledger to the original plan. It
produces a typed outcome instead of a loose pass/fail only:

- `complete`: all material constraints and evidence policy are satisfied;
- `recoverable_gap`: one bounded follow-up step can plausibly close a gap;
- `clarification_required`: required entity/constraint ambiguity blocks safe
  execution;
- `evidence_insufficient`: plan exhausted without acceptable evidence.

Rule-based verification always enforces structural constraints for planned
search paths. An optional LLM judge can review nuanced cases, and ReAct remains
a fallback only when the verifier says recovery is warranted.

### D7: Produce one execution trace for response metadata and audit

The controller emits an append-only, bounded trace: analysis summary, selected
policy, planned steps, actual provider attempts, evidence retain/reject
decisions, verification outcome, and fallback/clarification decision. The same
trace feeds `control` metadata and the persisted audit writer.

Provider inventory (`configured` / `requested` / `eligible`) is kept separate
from actual execution. The trace records each invocation immediately, so a
later client timing reset cannot hide earlier work. Audit stores compact IDs,
URLs, counts, and reasons, never secrets or opaque model prompt contents.

### D8: Migrate in layers rather than replacing the pipeline at once

Phase 1 creates analysis and plan structures alongside current output, records
them in test/shadow traces, and centralizes temporal eligibility. Phase 2
routes web retrieval through the plan controller with strict budgets and ledger
deduplication. Phase 3 turns plan-aware verification on for planned search
paths and wires deterministic recovery/clarification. Existing fast paths stay
as adapters throughout.

## Risks / Trade-offs

- [The planner becomes a second monolith] -> Keep contracts small, policies
  composable, and execution steps owned by existing source abstractions.
- [LLM analysis is inconsistent] -> Validate plan invariants deterministically
  and default to conservative clarification/evidence-insufficient outcomes.
- [More metadata increases latency or response size] -> Use bounded compact
  trace entries and make detailed persistence audit-only.
- [Policies are too strict for exploratory research] -> Apply authority and
  coverage requirements only when claim classes/constraints request them.
- [Migration causes subtle output drift] -> Preserve existing top-level fields,
  run both paths against a regression corpus, and enable plan execution in
  stages.
- [Audit foundation remains unarchived] -> Finish its OpenSpec lifecycle before
  changing shared writer semantics; use the in-memory trace meanwhile.

## Migration Plan

1. Archive/sync the existing process-audit change and add contract tests for
   its current JSONL behavior.
2. Add shared analysis, plan, policy, ledger, and outcome types with adapters
   that preserve existing `control`, `search_hits`, and `retrieved_docs`.
3. Replace duplicated temporal/query heuristics with validated plan policies;
   test against normal comparison, historical, domain, local, and ambiguous
   queries before changing default execution.
4. Route web evidence through the budgeted controller and enable trace/audit
   fields additively.
5. Enable plan-aware verification and recover/clarify behavior for eligible
   planned searches, retaining a rollback switch for the new controller.
6. Run a controlled live regression suite and inspect traces for budget,
   provider, and evidence-policy correctness before broad rollout.

## Open Questions

- Which current domain API paths should enter the plan controller in the first
  phase, and which retain a direct adapter longer?
- What per-turn budgets are acceptable for normal web search, temporal
  coverage, and recovery without degrading interactive latency?
- Which source-tier registry should be configuration-managed versus maintained
  in code, and how will changes be reviewed?
- Should partial comparison answers be returned with explicit gaps by default,
  or should some ambiguity classes always stop for clarification first?
