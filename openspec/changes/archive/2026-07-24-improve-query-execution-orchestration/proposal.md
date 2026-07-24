## Why

The default search path is a fixed sequence of independently implemented
heuristics: domain classification, source selection, keyword generation,
special-case retrieval, fusion, answer generation, and optional post-check.
Those stages do not share a durable statement of the user's entities,
constraints, claim types, evidence standard, or execution budget. As a
result, a local heuristic can reinterpret a query later in the pipeline and
trigger unplanned searches or unsupported answers without a coherent way to
stop, re-plan, or explain the outcome.

The pricing comparison incident is a regression example, not a new product
workflow. It exposed a general orchestration gap: the system needs to plan
evidence work before it executes it, validate the resulting answer against the
plan, and make the entire decision path observable.

## What Changes

- Introduce a structured `QueryPlan` that captures user constraints, entities,
  ambiguity, freshness, claim classes, required evidence standards, and a
  bounded sequence of retrieval or domain-data steps.
- Add reusable evidence-policy routing that selects an appropriate evidence
  standard from the claim and constraints, such as authority for numeric/current
  claims, coverage for comparisons, and time coverage for historical claims.
- Add direct-reference retrieval for already selected official pages or user
  supplied URLs. It uses configured extract/scrape providers as plan-step
  executors, rather than treating a target URL as a search query.
- Replace scattered special-case fan-out with a plan-driven executor that
  applies explicit query/result/cost budgets, records each action, and only
  replans when the evidence ledger identifies a recoverable gap.
- Make evidence fusion preserve each item’s URL, source tier, plan step, and
  constraint coverage; apply final deduplication and limits independently of
  reranker availability.
- Turn post-check into plan-aware verification that can return complete,
  recoverable, clarification-required, or evidence-insufficient outcomes.
- Expose an execution trace and persisted audit built from the same plan and
  ledger, separating configured capability from work actually performed.
- Keep existing direct, local-only, and domain-API fast paths, but route their
  decisions through the common analysis/verification contract where relevant.

## Capabilities

### New Capabilities
- `query-plan-orchestration`: Build and validate a reusable query plan before
  retrieval, including constraints, entities, evidence requirements, budgets,
  and clarification gates.
- `evidence-policy-routing`: Derive source-authority, comparison-coverage,
  temporal-coverage, freshness, and ambiguity policies from the query plan.
- `query-execution-trace`: Record bounded plan steps, provider attempts,
  evidence decisions, and verifier outcomes for responses and process audit.
- `direct-reference-retrieval`: Retrieve and normalize content from an
  explicitly selected public URL through configured Parallel Extract and
  Firecrawl Scrape adapters, with provider-level fallback and safe provenance.

### Modified Capabilities
- `search-routing-core`: Produce a shared, validated analysis and query plan
  instead of disconnected classification and keyword outputs.
- `unified-rag-execution`: Execute the plan with bounded replanning rather
  than independent temporal or specialized retrieval branches.
- `evidence-fusion-pipeline`: Fuse and rank evidence against the plan’s
  coverage and authority requirements.
- `evidence-source-layer`: Preserve evidence identity, source tier, and
  originating plan-step provenance.
- `web-search-provider-routing`: Distinguish provider inventory from actual
  execution and expose fallback decisions.
- `query-postcheck-fallback`: Verify the answer against plan constraints and
  direct recovery or clarification deterministically.
- `search-response-control`: Expose additive plan, execution, evidence, and
  verification metadata without breaking existing clients.

## Impact

- Affected modules: `search/source_selector.py`, `utils/query_config.py`,
  `langchain/langchain_orchestrator.py`, `langchain/langchain_rag.py`,
  `search/search.py`, `search/reference_fetch.py`, `evidence/source_layer.py`,
  `langchain/postcheck.py`, `utils/audit_log.py`, `main.py`, `server.py`,
  `config.example.json`, and control-metadata consumers in the frontend.
- Affected behavior: CLI and API retain current response fields, but search
  work becomes explicit, bounded, and verifiable. A query can correctly result
  in a clarification or evidence-insufficient answer instead of an apparently
  complete but weakly grounded answer.
- Validation: deterministic plan/execution/verifier tests across generic,
  temporal, comparison, domain-data, and ambiguous-entity scenarios; CLI and
  Flask contract tests; controlled live verification with secret-safe audit
  inspection. The reported API-pricing query is one regression case among them.
