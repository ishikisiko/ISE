## Context

`evidence/official_domain_resolver.py` currently resolves an entity stem to one **registrable domain** (`google.com`), and `evidence/source_tiering.py` classifies URLs by the same collapse. This creates the Google deadlock: `ai.google.dev`, `cloud.google.com`, `deepmind.google` are official while `google.com/search` is not, but the model cannot express the difference, so `google.com` sits on the denylist and Gemini survives only via pins. A single `aggregator_denylist` mixes content farms, hosting platforms, and non-evidence sites; it is entity-blind (nothing about GitHub/PyPI/Tencent Cloud/Google themselves can ever be official); it is force-unioned with `_DEFAULT_AGGREGATOR_DENYLIST` so entries cannot be removed; and E-tier subdomain acceptance is an accidental side effect of `registrable_domain()` collapsing subdomains rather than an explicit rule.

## Goals / Non-Goals

**Goals:**
- Match resolutions, pins, and list entries at **host** granularity, with optional **path-prefix** entries where a host is only partly official (the `google.com/search` case).
- Split list semantics into `never_official`, `hosting_platforms`, `non_evidence`, each with distinct enforcement points.
- Make suppression entity-aware: pins and A-tier signals run before list checks; a list never suppresses the current entity's own domain.
- Auto-detect suspected aggregators from cross-stem candidate statistics already flowing through the cache; curated lists become cold-start seeds.
- `aggregator_denylist` honors `mode: extend | replace` (default `extend`, current behavior).

**Non-Goals:**
- No LLM involvement in any judgement (structural signals only, as today).
- No full public-suffix-list dependency; host matching stays string-based over the existing suffix table.
- No retroactive migration of cached registrable-domain resolutions; old entries age out under existing TTLs.
- Path-prefix support is for *exclusion/matching*, not a general per-path ACL language.

## Decisions

### D1: Host-keyed matching with optional path prefixes

Introduce a `HostPattern` value object: `host` (full, case-folded, no leading dot) plus optional `path_prefix`. A URL matches when its host equals the pattern host or is a subdomain of it (explicit rule, see D2), and its path starts with `path_prefix` when one is present.

- `Resolution.domains` becomes a list of host patterns (serialized as strings, `host` or `host/path_prefix`). The `domain` field remains as the primary entry for backward compatibility; `resolved_domains` returns pattern strings.
- Pins accept host entries (`"gemini": ["deepmind.google", "ai.google.dev"]`) — this is what unblocks the Google case without a special-case code path.
- A-tier signals already return full URLs from registries; they now record the **host** instead of collapsing to registrable, so Wikidata `P856 → https://ai.google.dev/...` yields `ai.google.dev`.
- B-tier voting collapses hits to **host** (not registrable). Sibling hosts of the same family (`cloud.google.com` vs `ai.google.dev`) no longer merge votes; that is the intended precision gain.
- Alternative considered: keep registrable keys plus an exceptions table — rejected because it re-introduces per-brand special cases, the exact "treat the symptom" pattern this change removes.

### D2: Explicit E-tier subdomain acceptance

Subdomain acceptance becomes a first-class function, not a side effect of collapsing: `host_matches(pattern_host, url_host)` accepts exact match, well-known prefixes (`docs.`, `platform.`, `developer.`, `open.`, `api.`, `www.`, `dev.`, `developers.`, `documentation.`), and — for host-pattern entries only — single-label subdomains. `is_subdomain_of` moves off the registrable-collapse assumption and is the single matcher used by both resolver and tiering. Registrable collapsing is retained only for B-tier vote independence counting where cross-provider comparison needs a stable key — no, it does not: votes compare hosts directly (D1); `registrable_domain()` stays available for legacy consumers but is no longer on the judgement path.

### D3: Three tables, three enforcement points

- `never_official` (content farms): excluded from B-tier candidate collection **entirely** (votes are dropped before top-N tallying, so they no longer crowd out real candidates), and can never be official. They remain usable as ordinary web evidence unless also in `non_evidence`.
- `hosting_platforms`: dropped from B-tier *self-votes* and never official by default, BUT official when an ownership relation holds: (a) an A-tier registry field declares the URL as the package homepage (PyPI `home_page`, npm `homepage` pointing at `*.readthedocs.io` etc.), or (b) a GitHub repo whose owner/name matches the entity stem has its `html_url`/Pages host as candidate. This recovers the lost recall of docs-on-readthedocs libraries and GitHub-Pages-hosted projects.
- `non_evidence` (search engines, AI answer sites, mirrors): excluded from B-tier voting, never official, AND filtered from ordinary web evidence at the tiering layer (`classify_web_source_tier` → `unknown` plus an exclusion flag consumed by the RAG pipeline's evidence filter).

The current `_probe_github` special case (skip `github.com`/`github.io`) is absorbed into `hosting_platforms` ownership logic.

### D4: Entity-aware suppression order

Resolution order becomes: pins → cache → A-tier probes → list checks on remaining candidates. A domain that a pin or A-tier signal vouches for is never suppressed by any list (ownership beats reputation). List suppression applies only to B-tier candidates that have no ownership relation to the current entity. Concretely: asking about "GitHub" lets the GitHub A-tier/Wikidata path confirm `github.com`; asking about "some library" keeps `github.com` out of official judgement.

### D5: Behavioral aggregator auto-detection

The cache gains a `candidate_observation` table: `(domain_or_host, stem, observed_at)`, one row per B-tier candidate per resolution. A host appearing as a candidate for ≥ `aggregator_min_stems` (default 5) **distinct stems** within the observation window is flagged `suspected_aggregator`; flagged hosts require `min_signals + 1` independent sources and lose E-tier subdomain acceptance. Flags are recomputed on write and cached in the same SQLite store. Detection measures behavior (voted for by many unrelated entities), not names, so new content farms are caught without config edits. Curated `never_official` remains as the cold-start seed.

### D6: List config shape and extend/replace mode

```jsonc
"official_domain_resolution": {
  "never_official":     {"mode": "extend", "domains": [...]},
  "hosting_platforms":  {"mode": "extend", "domains": [...]},
  "non_evidence":       {"mode": "extend", "domains": [...]},
  "aggregator_denylist": [...]              // legacy: folds into never_official (extend)
}
```

`mode: replace` uses the user list verbatim (defaults dropped); `mode: "extend"` (default) unions with built-ins — preserving today's behavior for `aggregator_denylist` while making removal possible. Legacy bare-array `aggregator_denylist` reads as `never_official` + extend for compatibility.

## Risks / Trade-offs

- [Host-level B-tier voting splits family votes (`cloud.google.com` vs `ai.google.dev`), so some entities that previously crossed `min_signals` via sibling subdomains now land at `candidate`] → Acceptable precision-for-recall trade; pins and A-tier cover the important families, and `candidate` still surfaces in trace.
- [Auto-detection could flag a legitimate docs host that many entities genuinely vote for] → Flag only raises the evidence bar (`min_signals + 1`) and never suppresses pins/A-tier; threshold and window are configurable; flags are observable via cache inspection.
- [Cache schema change on an existing SQLite file] → New table is `CREATE TABLE IF NOT EXISTS`; old rows simply lack observations and rebuild naturally; no migration script needed.
- [`non_evidence` filtering at tiering changes which pages reach the RAG pipeline] → Search engines/AI-answer sites carry no citable content today anyway; the filter removes noise, and `mode: replace` gives operators an escape hatch.
- [Old cached registrable-domain entries are coarser than new host-level ones] → Left to expire under existing TTLs; positive TTL is 30 days, acceptable drift window; operators can delete the cache file to force re-resolution.

## Migration Plan

1. Land resolver + tiering changes behind the existing `official_domain_resolution.enabled` flag (default off in shipped `config.example.json` stays off → zero behavior change for disabled deployments).
2. Parse new list block with legacy `aggregator_denylist` compatibility read; no config edits required on upgrade.
3. Add `candidate_observation` table on cache open (idempotent DDL).
4. Rollback: set `enabled=false` or restore previous config; new cache table is inert under old code.

## Open Questions

- Default `aggregator_min_stems` threshold (5 proposed) — tune against real cache data after a soak period.
- Whether `non_evidence` exclusion should also apply to uploaded/local docs (currently scoped to web evidence only).
- Whether hosting-platform ownership should extend to GitLab/Gitee Pages mirrors or stay GitHub-centric initially.
