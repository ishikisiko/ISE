## 1. Prerequisites (pure refactors, no behavior change)

- [x] 1.1 Merge the duplicated domain helper: make `utils/query_orchestration.py` import and call `evidence.source_tiering.registrable_domain`; delete `_configured_domain` and its private 7-entry suffix table; keep call-site behavior identical.
- [x] 1.2 Extend the deterministic query analyzer so `QueryAnalysis.entities` independently surfaces brand/product candidate tokens (not derived from the alias map).
- [x] 1.3 Remove the token-grep-against-alias fallback in `langchain/langchain_rag.py::_enrich_official_pages` (query splitting + `alias_stems` matching); rely on analyzer entities and (later) the resolver.
- [x] 1.4 Add regression tests asserting plan-layer and tier-layer return identical registrable domains for a shared URL fixture set.

## 2. Resolver core (`evidence/official_domain_resolver.py`)

- [x] 2.1 Define the `Resolution` value object (`domain`, `confidence`, `signals`, `verified_at`) and the public `resolve(entity) -> Resolution` entry point with `pins`-precedence short-circuit.
- [x] 2.2 Implement A-tier structured-source probes (Wikidata `P856`, PyPI `home_page`, npm `homepage`, GitHub repo `homepage`/`html_url`) as best-effort, non-fatal, keyless HTTP fetches.
- [x] 2.3 Implement B-tier cross-provider search voting: query `<entity> 官网 / official site / docs` via existing `SearchClient` providers, collapse hits to registrable domain, tally independent providers.
- [x] 2.4 Implement C-tier self-proof via `DirectFetchClient`: follow redirect chain, store final domain, check `<title>`/`og:site_name` contains stem and TLS host matches.
- [x] 2.5 Implement the D-tier aggregator denylist (csdn, zhihu, juejin, medium, cnblogs, reddit, API-directory sites) and the deterministic acceptance rule (1 A-signal OR ≥ `min_signals` independent B/C → official; lone weak signal → candidate; denylist/none → none).
- [x] 2.6 Implement E-tier well-known-subdomain acceptance (`docs.`, `platform.`, `developer.`, `open.`, `api.`, `www.`) under a confirmed registrable domain.
- [x] 2.7 Enforce the no-LLM-for-domains red line in code and tests: page-body "official site" claims are rejected unless a structural signal corroborates.

## 3. Cache layer

- [x] 3.1 Add the SQLite store (`runtime/official_domains.sqlite`, table `entity_stem → domain, confidence, signals_json, resolved_at`) using stdlib `sqlite3`.
- [x] 3.2 Implement split TTL: positive (`official`/`candidate`) `cache_ttl_days` (default 30), negative (`none`) `negative_ttl_hours` (default 24); make read/write failures degrade to live resolution.
- [x] 3.3 Enforce discovery budgets (`max_discovery_searches` default 2, `max_verification_fetches` default 2) with early termination on sufficient signals.

## 4. Wiring and config

- [x] 4.1 Route `evidence/source_tiering.py::classify_web_source_tier` and `official_entity_for_url` through the resolver (pin precedence, subdomain acceptance) while keeping `unknown` as the safe fallback.
- [x] 4.2 Route the plan-layer official-target builder (`_official_recovery_targets`) through the resolver; record resolver signals in the recovery-step trace.
- [x] 4.3 Add `orchestration.official_domain_resolution` to `config.example.json` and migrate `config.json` (`official_domains` → `pins`, with backward-compatible read + deprecation log).
- [x] 4.4 Implement the `enabled=false` rollback switch that restores static-map-only behavior.
- [x] 4.5 Surface resolver signals into the execution trace / audit for each resolved official domain.

## 5. Tests and verification

- [x] 5.1 Unit tests for `registrable_domain` merge parity, resolver acceptance rule (A-sufficient, ≥2 B/C, lone-weak→candidate, denylist→none, pin override, subdomain acceptance, red-line rejection).
- [x] 5.2 Unit tests for cache TTL semantics (hot hit, expiry triggers rediscovery, sqlite failure degrades gracefully) and budget caps / early termination.
- [x] 5.3 Integration test: an unconfigured entity resolves to official and the URL is classified `official`; an entity with only weak signals is classified `first_party`/`unknown` and does not satisfy the authority gate.
- [x] 5.4 Run focused tests, the full `pytest` suite, and `openspec` strict validation.
- [x] 5.5 Run the actual pipeline (`python main.py "..."`) for an unconfigured brand and a pinned brand, inspecting the persisted audit trace for resolver signals.
