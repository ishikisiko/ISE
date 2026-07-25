## Context

The default evidence plan correctly identifies pricing comparisons as authority-required,
but it currently gives every comparison a single mixed web query and a single mixed
reformulation. `PrioritySearchClient` stops after any provider has results, while page
extraction can only fetch those already-selected URLs. The configured alias map knows
the official domains for GLM, Kimi, and Fable, but it is not used to discover a missing
target URL. A separate union-of-all-configured-domains check also lets an unrelated
configured provider domain be displayed as official for the current query.

## Goals / Non-Goals

**Goals:**
- Recover official evidence per named comparison member when an authority-required
  comparison has configured official domains.
- Bound the work, keep the normal first-hit provider route unchanged, and expose every
  targeted provider attempt and matched entity in the existing trace structures.
- Never treat an unrelated configured domain as official evidence for a query target.
- Return an explicit evidence-insufficient response instead of a normal pricing
  comparison when target official coverage remains incomplete.

**Non-Goals:**
- Guarantee that a provider indexes every official pricing page.
- Add browser rendering, alter provider credentials, or turn ordinary searches into
  all-provider fan-out.
- Change non-pricing, non-authority comparison behaviour.

## Decisions

### Model target-domain recovery as one bounded recovery step with per-target searches

`build_query_plan` will derive up to four target records from comparison members and
`orchestration.official_domains`. The recovery step includes each normalized entity,
its permitted registrable domains, and a deterministic query. It is one logical
recovery-budget unit, while its trace records the individual per-target searches.
This keeps the existing recovery budget contract while ensuring that one mixed query
does not drop later comparison members.

We will not rely only on a combined query containing several `site:` clauses: search
providers do not reliably return every requested domain from such a query.

### Add domain-coverage fallback only to priority search recovery

`PrioritySearchClient` will expose a targeted method that continues through its
configured clients until a result URL is in the target's allowed domains or the list is
exhausted. The ordinary `search` method retains its current first-nonempty-result
semantics, avoiding a cost and latency regression for generic search. Non-priority
clients continue to receive one bounded attempt.

### Make official selection entity-aware

The page-enrichment path will classify a URL against the current plan entities, not a
union of every configured official domain. Selected result metadata will include the
matching target entity when available. Non-target pages can remain optional context,
but must not be counted or labelled as official documentation.

### Gate normal answers for incomplete authority-required pricing comparisons

After recovery, a pricing comparison with configured target domains must have retained
official evidence for every mapped comparison member before it reaches normal answer
generation. Otherwise the response returns an explicit insufficient-evidence notice
and the audit/control payload identifies the missing targets. This is deliberately
narrower than a global ban on limited-evidence answers.

### Keep LLM keyword generation auxiliary

The JSON example in `KEYWORD_SYSTEM_PROMPT` will escape template braces. Target-domain
recovery remains deterministic and does not depend on this LLM-generated query.

## Risks / Trade-offs

- [A comparison with many aliases can add requests] -> Limit to four targets, one
  logical recovery pass, configured provider order, and existing time/result limits.
- [Some search APIs may ignore `site:`] -> Filter returned URLs against the configured
  registrable-domain allowlist before accepting coverage and continue fallback.
- [An official page may be absent from every provider index] -> Return the explicit
  incomplete-evidence response; do not fabricate pricing coverage.
- [A strict gate can reduce answer availability] -> Apply it only to authority-required
  pricing comparisons that have configured official-domain targets.

## Migration Plan

The change is additive to response metadata and uses existing `official_domains`
configuration. Deploy with focused tests, then run the original ISE query through the
live pipeline and inspect its persisted audit record. Rollback consists of disabling
the new targeted recovery configuration block; existing generic recovery remains
available for queries without target mappings.

## Open Questions

None. The first implementation will use the existing configured provider order and
will not introduce a new external provider.
