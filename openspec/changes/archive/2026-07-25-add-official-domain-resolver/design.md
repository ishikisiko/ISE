## Context

Today the only way a URL is recognized as "official" for a query entity is a hand-maintained map under `orchestration.official_domains` (~14 aliases). `classify_web_source_tier` and `official_entity_for_url` in `evidence/source_tiering.py` look the entity stem up in that map; a miss returns `unknown`, so the `authority` verification gate is unreachable for any new vendor/library/person. The map also (a) rots on brand renames (e.g. moonshot → kimi), (b) is half-redundant because `registrable_domain()` already collapses `platform.openai.com` → `openai.com`, and (c) is shadowed by a second, divergent copy of that helper in `utils/query_orchestration.py` (`_configured_domain`, 7-entry suffix table vs the 46-entry table in `source_tiering.py`).

A token-grep fallback in `langchain/langchain_rag.py::_enrich_official_pages` (splitting the query on whitespace and matching tokens against alias keys) currently papers over the fact that the deterministic analyzer does not always emit brand entities — i.e. the alias map is being used backwards as a poor man's entity extractor.

Existing building blocks we will reuse, not duplicate: `DirectFetchClient` (zero-config HTTP + main-text + redirect chain), the multi-provider `SearchClient` family (Brave, BrightData, Google, Tavily, Firecrawl, Parallel) for cross-voting, `normalize_entity_stem` for stable stem keys, and stdlib `sqlite3` for caching (mirrors the conversation checkpoint pattern).

## Goals / Non-Goals

**Goals:**
- Recognize official domains for entities that were never configured, with deterministic, auditable signals — no human config edit per brand.
- Make the resolver the single authority on "is this URL official for this entity", while keeping a small `pins` override for cases humans must lock down.
- Strictly avoid LLM hallucination as a signal source: only structural facts (registry fields, redirect chains, certificates, cross-source agreement) count. The LLM may normalize an entity label to a stem, nothing more.
- Bound cost: first sight of an entity spends a small fixed budget (≤ a few searches + ≤ a couple fetches); afterwards everything is served from a TTL cache.
- One canonical `registrable_domain`; the plan layer and the tier layer must agree on the same domain for the same URL.

**Non-Goals:**
- Building a general-purpose entity-linking / knowledge-graph service. The resolver answers one question: "what is the official registrable domain for this stem".
- Resolving people/blogs that genuinely have no canonical owned domain — those stay `unknown` and the answer path degrades as designed.
- Crawling or rendering JavaScript. Discovery fetches reuse `DirectFetchClient` semantics (static HTML only).
- Replacing search provider routing, evidence fusion, or the verification policy itself — the resolver only feeds `classify_web_source_tier` and the plan-layer official-target builder.

## Decisions

### D1. One entry point, value-object result
`resolve(entity) -> Resolution(domain, confidence, signals, verified_at)` is the only public surface. `confidence ∈ {"official","candidate","none"}`. `signals` is an ordered list of typed facts (`{kind, source, detail}`) so trace/audit can show *why* a domain was judged official. Tier consumers (`classify_web_source_tier`, `official_entity_for_url`) call the resolver and never touch config maps directly.

*Alternative*: expose per-signal probes. Rejected — it re-scatters the "what is official" decision across callers and makes the audit story incoherent.

### D2. Signal tiers and the acceptance rule (deterministic, no LLM)
Signals, strongest to weakest:

| Tier | Signal | Trust rationale |
|------|--------|-----------------|
| A | Structured registry field: Wikidata `P856` (official website), PyPI `home_page`, npm `homepage`, GitHub repo `homepage`/`html_url` | Keyless public structured fields, not page-content forgeable |
| B | Cross-provider search voting: query `<entity> 官网 / official site / docs`, take top-N hits, collapse to registrable domain, vote | Two independent providers agreeing beats any single source |
| C | `DirectFetchClient` self-proof on a candidate root: HTTP 200, store the **final post-redirect domain**, `<title>`/`og:site_name` contains the entity stem, TLS host matches | Eliminates typosquats and dead domains |
| D | Aggregator denylist (`csdn.net`, `zhihu.com`, `juejin.cn`, `medium.com`, `cnblogs.com`, `reddit.com`, API-directory sites, …) — never `official` | Small, stable; this is what static lists are good at |
| E | Well-known subdomain acceptance (`docs.`, `platform.`, `developer.`, `open.`, `api.`, `www.`) under a confirmed registrable domain | Replaces hand-listed subdomains |

Acceptance rule (pure function of signals): **one A-signal, or ≥ `min_signals` (default 2) mutually independent B/C signals → `official`**. A single weak (B or C alone) signal → `candidate` (usable as first-party context, does NOT satisfy the `authority` gate). Denylist hit or zero signals → `none`. The LLM is never asked "what is X's official site"; model memory is the source of domain hallucination and page body text is injectable. The LLM may only map a raw entity label to a stem.

*Alternative*: weight signals with a learned model. Rejected — destroys determinism and auditability.

### D3. `pins` override, demoted config
`orchestration.official_domain_resolution.pins` keeps the exact `{stem: [domains]}` shape of today's `official_domains`. Pinned stems are returned with `confidence="official"`, `signal kind="pin"`, and **skip discovery entirely** — highest priority, zero cost, zero network. The old `official_domains` key is read for one release as a compatibility alias and migrated to `pins` on first load (with a logged deprecation). This keeps existing behavior for the 14 known brands while letting everything else be discovered.

### D4. SQLite cache with split TTL
`runtime/official_domains.sqlite`, table `entity_stem → (domain, confidence, signals_json, resolved_at)`. Positive (`official`/`candidate`) TTL = `cache_ttl_days` (default 30); negative (`none`) TTL = `negative_ttl_hours` (default 24). Hot entities cost ~zero; the first miss is bounded by `max_discovery_searches` (default 2) + `max_verification_fetches` (default 2). Cache failures degrade to "resolve live every time" and never crash the query. Path/TTLs/budgets all configurable; `enabled=false` falls back to current static-map-only behavior (rollback lever).

*Alternative*: in-process LRU only. Rejected — a fresh process would re-pay discovery on every entity on every restart; persistence matters because entities recur across sessions.

### D5. Merge `registrable_domain` first (prerequisite)
Delete `query_orchestration._configured_domain` and its 7-entry suffix table; the plan layer imports and calls `evidence.source_tiering.registrable_domain` (the 46-entry table). This must land before the resolver is wired, otherwise the resolver's output gets recomputed to a different domain in the two layers.

### D6. Analyzer must emit candidate entities (prerequisite)
The deterministic analyzer is extended to surface brand/product tokens as `QueryAnalysis.entities` candidates independently of the alias map. Once that exists, the token-grep fallback in `_enrich_official_pages` is deleted — it only existed because the analyzer under-produced entities and the alias map was being abused as the extractor.

## Risks / Trade-offs

- [First-sight latency] A brand-new entity pays discovery cost on the first query. → Bounded budgets (`max_discovery_searches`, `max_verification_fetches`), aggressive caching, and the resolver runs only when `classify_web_source_tier` would otherwise return `unknown` (configured/pinned/cached stems never trigger discovery).
- [Structured-source outage] Wikidata/PyPI/npm/GitHub may rate-limit or be unreachable. → Each A-source is best-effort and non-fatal; missing one falls through to B/C voting. A total A-failure still yields official via ≥2 B/C signals.
- [Wrong domain cached for TTL window] A misvote could pin a wrong domain for ≤30 days. → Negative TTL is short (24h); positive cache entries carry full `signals` so a human can audit and a `pins` entry can override any bad cached result instantly.
- [Entity ambiguity] "Apple" the company vs. the fruit. → The resolver is entity-scoped to the current query; ambiguous/weak resolutions return `candidate` and do not satisfy the authority gate rather than guessing.
- [Config migration confusion] Users with custom `official_domains`. → Backward-compatible read + deprecation log; no behavior change for pinned stems.
- [Network in tiering path] Tier classification was previously pure-function (no I/O). → The resolver is only invoked on the `unknown` branch; cached reads are local; live discovery is bounded and wrapped so any failure returns `none` and never blocks the query.

## Migration Plan

1. Land the two prerequisites (D5 domain-helper merge, D6 analyzer entity candidates) — pure refactors, no behavior change.
2. Add `official_domain_resolver.py` + SQLite cache behind `official_domain_resolution.enabled` (default `false` during integration).
3. Wire `classify_web_source_tier` / `official_entity_for_url` / the plan-layer official-target builder through the resolver; keep `pins` precedence.
4. Remove the token-grep fallback in `_enrich_official_pages`.
5. Add `official_domain_resolution` to `config.example.json` and migrate `official_domains` → `pins` in `config.json`; flip `enabled=true` once tests pass.
6. Rollback: set `official_domain_resolution.enabled=false` to restore static-map-only behavior.

## Open Questions

- Default value of `min_signals` (2 vs 1+structured-only). Proposal defaults to 2 for safety; tunable in config.
- Whether to seed the cache from the current 14 aliases on first run (warm-start) or resolve them lazily. Lean: warm-start `pins` only, resolve the rest lazily.
