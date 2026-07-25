## 1. Host-pattern model and explicit subdomain matcher

- [x] 1.1 Add a `HostPattern` value object (host + optional `path_prefix`, parse/serialize from `host` or `host/path_prefix` strings) in `evidence/official_domain_resolver.py`
- [x] 1.2 Rework `is_subdomain_of` into an explicit `host_matches(pattern, url)` matcher (exact, well-known prefixes, single-label subdomain) shared by resolver and tiering; remove reliance on registrable collapsing on the judgement path
- [x] 1.3 Extend `Resolution.domains` to carry host-pattern entries (serialized strings); keep `domain` as primary entry and `resolved_domains` backward compatible

## 2. Three list tables with extend/replace mode

- [x] 2.1 Define built-in seed lists: `_DEFAULT_NEVER_OFFICIAL` (content farms), `_DEFAULT_HOSTING_PLATFORMS` (github.com/github.io, readthedocs.io, gitbook.io, vercel.app, pages.dev, notion.site, …), `_DEFAULT_NON_EVIDENCE` (search engines, AI answer sites, mirrors); split `_DEFAULT_AGGREGATOR_DENYLIST` entries across them
- [x] 2.2 Parse `{"mode": "extend"|"replace", "domains": [...]}` per table in `resolver_config_from_mapping`; map legacy bare-array `aggregator_denylist` to `never_official` + extend
- [x] 2.3 Update `config.example.json` with the three-table block and a `mode` example

## 3. Entity-aware suppression and hosting-platform ownership

- [x] 3.1 Reorder `resolve`/`_discover` so pins and A-tier probes complete before any list check; candidates vouched by pin/A-tier bypass list suppression
- [x] 3.2 Drop `never_official` and `non_evidence` hosts from B-tier vote collection before top-N tallying (no slot occupation)
- [x] 3.3 Implement hosting-platform ownership: A-tier declared-homepage pointing at a hosting-platform host yields an official host entry; GitHub probe treats matching repo owner/Pages host as ownership-backed (absorbing the current github.com/github.io special case)
- [x] 3.4 Record A-tier and B-tier signals at host granularity (stop collapsing to registrable on the judgement path)

## 4. Behavioral aggregator auto-detection

- [x] 4.1 Add `candidate_observation` table (`host`, `stem`, `observed_at`, unique on host+stem) to the SQLite cache with idempotent DDL
- [x] 4.2 Record one observation per B-tier candidate host per resolution (best-effort, dedup by host+stem)
- [x] 4.3 Compute suspected-aggregator flags (distinct-stem count ≥ `aggregator_min_stems`, default 5) and apply the higher bar: `min_signals + 1` independent sources, no E-tier subdomain acceptance; pins/A-tier unaffected
- [x] 4.4 Add `aggregator_min_stems` (and observation window) config knobs with validation

## 5. Tiering and consumers

- [x] 5.1 Update `evidence/source_tiering.py` ownership matching to host/path patterns via the shared matcher; remove registrable-collapse dependence in `official_entity_for_url` / `classify_web_source_tier`
- [x] 5.2 Exclude `non_evidence` URLs from ordinary web evidence at the tiering layer and wire the exclusion flag into the RAG evidence filter (`langchain/langchain_rag.py`)
- [x] 5.3 Audit other consumers (`utils/query_orchestration.py`, `pins_for`, `is_denied` call sites) for host-granularity semantics and update accordingly

## 6. Tests and validation

- [x] 6.1 Add/extend tests in `tests/test_official_domain_resolver.py`: host-level pins (Google family case), sibling hosts not merging votes, three-table semantics, extend/replace modes, entity-aware bypass, hosting-platform ownership, auto-detection threshold and higher bar
- [x] 6.2 Add tiering tests: host/path official matching, explicit subdomain acceptance/rejection, non_evidence exclusion, hosting-platform official-with-ownership
- [x] 6.3 Run `python -m pytest tests/test_official_domain_resolver.py` and related tiering tests; run a CLI sanity check (`python main.py "sanity check" --pretty`)
