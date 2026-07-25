## Why

The resolver collapses every URL to its registrable domain, which makes whole-product families inexpressible: `ai.google.dev`, `cloud.google.com`, and `deepmind.google` are official while `google.com/search` is not, but a registrable-domain-only model cannot say so — the only available action is to deny `google.com` outright and pin Gemini forever. The single `aggregator_denylist` also conflates three different semantics (content farms, hosting platforms, non-evidence sites), is entity-blind (so questions *about* GitHub, PyPI, Tencent Cloud, or Google themselves can never be answered authoritatively), and is force-unioned with the built-in default so users cannot remove an entry they know is wrong.

## What Changes

- **Host + path-prefix granularity**: resolutions, pins, and list entries are matched at host level (with optional path prefixes) instead of always collapsing to the registrable domain. `Resolution.domains` accepts host-level entries. The E-tier subdomain acceptance that is currently implicit in `registrable_domain()` collapsing is re-implemented explicitly.
- **Split the denylist into three tables with distinct semantics**:
  - `never_official`: content farms (csdn, zhihu, juejin, medium, jianshu, segmentfault, …) — can never be official for any entity and are excluded from B-tier voting entirely instead of merely occupying top-N slots.
  - `hosting_platforms`: `github.com`/`.github.io`, `readthedocs.io`, `gitbook.io`, `vercel.app`, `pages.dev`, `notion.site`, … — not brand-owned by default, but MAY be judged official when an ownership relation exists (repo owner matches the entity, or a package's declared homepage points at the URL).
  - `non_evidence`: search engines, AI answer sites, aggregator mirrors — never official AND excluded from ordinary web evidence, not just from official judgement.
- **Entity-aware denylist**: pins and A-tier structured signals run first; a hit bypasses list suppression. Lists only suppress a domain that is *not* the current entity's own domain, so questions about listed platforms themselves become answerable.
- **Self-growing aggregator detection**: the resolver cache already records which domains appear as candidates for how many distinct stems; a domain exceeding a configurable stem-count threshold is auto-flagged as a suspected aggregator and requires stronger signals. The curated list degrades to a cold-start seed.
- **BREAKING (config)**: `aggregator_denylist` gains `mode: extend | replace` (default `extend` preserves current behavior); the single list is superseded by the three-table block (`never_official` / `hosting_platforms` / `non_evidence`), with the legacy key read for compatibility.

## Capabilities

### New Capabilities
- `aggregator-auto-detection`: Behavioral detection of aggregator domains from cross-stem candidate statistics in the resolver cache, with configurable threshold and stronger-signal requirements for flagged domains.

### Modified Capabilities
- `official-domain-resolution`: Resolution granularity moves from registrable domain to host (+ optional path prefix); the single denylist becomes three semantically distinct tables; list checks become entity-aware (pins/A-tier bypass); `aggregator_denylist` gains extend/replace mode.
- `evidence-source-layer`: Tier classification matches official ownership at host/path granularity; subdomain acceptance becomes explicit; `non_evidence` hosts are excluded from ordinary web evidence; `hosting_platforms` URLs classify official when an ownership relation exists.

## Impact

Modified: `evidence/official_domain_resolver.py` (granularity, three lists, entity-aware checks, auto-detection stats, config parsing), `evidence/source_tiering.py` (host/path matching, explicit subdomain acceptance, non-evidence exclusion), `langchain/langchain_rag.py` and `utils/query_orchestration.py` (consumers of `is_denied` / `resolved_domains` / tiering). Config: `config.json` / `config.example.json` gain the three-table block, auto-detection knobs, and `mode`. Cache: SQLite schema gains candidate-observation recording for auto-detection. Tests: `tests/test_official_domain_resolver.py` plus tiering tests. No new third-party dependencies.
