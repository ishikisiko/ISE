## Context

`IntelligentSourceSelector` currently accepts an LLM domain label ahead of deterministic keyword classification. Its finance symbol parser then uppercases arbitrary two-to-six-letter words, and its finance handler retains truthy provider-error dictionaries as successful results. Together these independent permissive decisions turn a weak classification guess into a terminal structured answer, preventing the default orchestrator from reaching general web search.

The existing orchestrator already continues when a domain handler returns `handled: false`, so the repair can remain inside the source-selector boundary. The implementation must preserve valid structured finance routes for explicit tickers, known companies, indices, cryptocurrencies, and numeric exchange codes.

## Goals / Non-Goals

**Goals:**

- Require positive finance evidence before an LLM-only finance label can override deterministic classification.
- Separate trusted ticker syntax from ambiguous uppercase candidates and reject ordinary lowercase product words.
- Use the optional symbol-extraction LLM to validate ambiguous uppercase candidates or resolve an otherwise unresolved company name.
- Return an explicit unhandled result when every structured finance provider fails so the normal search route can continue.
- Cover the combined regression and each independent guard with deterministic tests.

**Non-Goals:**

- Rebuild the general routing architecture or remove LLM domain classification.
- Perform a live exchange lookup for every candidate during routing.
- Expand the supported finance instruments or provider set.
- Change public configuration or response schemas beyond the existing unhandled/skipped contract.

## Decisions

### Cross-check only the high-risk finance override

Keyword classification is computed before accepting the LLM result. A valid non-finance LLM label retains current precedence, while an LLM `finance` result is accepted only when the keyword classifier also returns `finance` or deterministic symbol syntax/mapping yields evidence. Otherwise classification returns `general`.

This targets the observed asymmetric risk without changing all domain precedence rules. Replacing LLM precedence globally was considered, but would alter weather, sports, location, and temporal behavior outside this change.

### Split symbol evidence into trusted and ambiguous inputs

Known mappings, `$TICKER`, parenthesized uppercase tickers, six-digit numeric codes, and crypto/index mappings are accepted deterministically. Bare candidates must be uppercase in the original query and two to five letters long; ordinary lowercase English words are never promoted by uppercasing.

When an LLM is available, ambiguous uppercase candidates are passed to symbol extraction for confirmation rather than accepted first and allowed to suppress extraction. The same extraction can resolve a company name when no deterministic symbol exists. Without an LLM, original-uppercase candidates remain usable for backward-compatible offline finance queries. Google symbol search remains a final fallback only when no usable symbol exists.

### Provider errors are control flow, not finance data

The finance handler appends only non-error quote/history dictionaries. If all symbols fail, it returns `handled: false`, `skipped: true`, and `reason: data_fetch_failed`. This matches the existing cannot-parse contract and lets the orchestrator proceed through its general search decision rather than formatting provider errors into an answer.

### Keep the change localized

No orchestrator branch is added. The existing `handled` gate is the integration contract, and focused source-selector tests will assert the payload that drives it. This avoids duplicating fallback policy in both modules.

## Risks / Trade-offs

- [A lowercase ticker written without `$` or parentheses is no longer recognized directly] -> Known mappings and LLM company extraction cover common cases; users can use explicit ticker syntax for deterministic handling.
- [LLM confirmation may add latency for ambiguous uppercase candidates] -> It runs only when candidate validation is useful and replaces unsafe blind acceptance.
- [An LLM can still return a syntactically valid but incorrect symbol] -> Restrict the prompt to explicitly mentioned securities and preserve strict output-format validation; provider failure still falls back to general search.
- [A genuine finance query with neither finance keywords nor symbol evidence routes to general search] -> This is the intended conservative failure mode because general search can still answer it, while a false structured route can terminate incorrectly.
