## Why

Official-source recognition is hard-wired to a hand-maintained alias table (`orchestration.official_domains`, ~14 entries). Any entity not listed there collapses to `unknown` in `classify_web_source_tier`, so the `authority` verification gate can never be satisfied for exactly the new entities (new vendors, libraries, people) that most need authoritative evidence. The table also rots silently (brand renames like moonshot → kimi land on `unknown` until a human edits config), half of its rows are dead weight because `registrable_domain()` already collapses subdomains, and the matching logic now exists as two drifting copies (`query_orchestration._configured_domain` with a 7-entry suffix table vs `source_tiering.registrable_domain` with a 46-entry table) that can compute different domains for the same URL in the plan layer and the tier layer.

## What Changes

- Add a discover → verify → cache official-domain resolver (`evidence/official_domain_resolver.py`) exposing one deterministic entry point: `resolve(entity) -> Resolution{domain, confidence, signals, verified_at}`. Confidence is assembled only from structural signals (Wikidata `P856` / PyPI `home_page` / npm `homepage` / GitHub repo homepage; cross-provider search voting on the registrable domain; `DirectFetchClient` redirect-chain + `<title>`/`og:site_name` + certificate-host self-verification). Page body claims of "this is the official site" are explicitly rejected as injectable.
- Demote `orchestration.official_domains` to an override layer renamed `pins`: pinned stems win with top priority, everything else is delegated to the resolver. A curated `aggregator_denylist` (csdn, zhihu, juejin, medium, cnblogs, reddit, API directories, ...) replaces the impossible allowlist; subdomains of a confirmed domain (`docs.`, `platform.`, `developer.`, `open.`, `api.`) are accepted automatically instead of hand-listed.
- **BREAKING (config)**: introduce an `orchestration.official_domain_resolution` block (`enabled`, `min_signals`, `cache_path`, TTLs, signal budgets, `structured_sources`, `aggregator_denylist`, `pins`). The old `official_domains` key is migrated to `pins` with a compatibility read.
- Merge the two `registrable_domain` implementations into the single canonical `evidence.source_tiering.registrable_domain` and delete `query_orchestration._configured_domain` / its private suffix copy.
- Make the query analyzer independently emit candidate brand entities so the resolver has real input, and remove the token-grep-against-alias fallback in `_enrich_official_pages` that currently masks the upstream gap.

## Capabilities

### New Capabilities
- `official-domain-resolution`: A discover→verify→cache resolver that maps an entity stem to a verified official registrable domain using only structural signals (structured registry fields, cross-provider search voting, redirect/certificate self-verification), an aggregator denylist, SQLite caching with positive/negative TTLs, and a config-driven `pins` override layer.

### Modified Capabilities
- `evidence-source-layer`: `classify_web_source_tier` and `official_entity_for_url` resolve official ownership through the resolver (with `pins` precedence) instead of only the static alias map; confirmed official domains auto-accept their well-known subdomains.
- `query-plan-orchestration`: Official-domain recovery targets and plan-layer domain math use the single canonical `registrable_domain` and the resolver, removing the duplicated suffix table.

## Impact

New module `evidence/official_domain_resolver.py` plus a SQLite cache under `runtime/`. Modified: `evidence/source_tiering.py` (resolver-backed tiering), `evidence/__init__.py` (exports), `utils/query_orchestration.py` (drop `_configured_domain`, call canonical helper; emit candidate entities), `langchain/langchain_rag.py` (drop token-alias fallback, route through resolver), and the analyzer that produces `QueryAnalysis.entities`. Config: `config.json` / `config.example.json` add `orchestration.official_domain_resolution` and migrate `official_domains` → `pins`. New focused tests under `tests/`. No new third-party runtime dependencies are required (HTTP + stdlib `sqlite3` + existing `requests`/`BeautifulSoup`); Wikidata/PyPI/npm/GitHub are keyless public endpoints.
