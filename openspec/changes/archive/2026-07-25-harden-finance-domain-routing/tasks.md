## 1. Guard Finance Classification

- [x] 1.1 Add finance positive/negative examples and an uncertainty fallback to the LLM domain prompt.
- [x] 1.2 Cross-check an LLM-only finance label against deterministic finance keywords or symbol evidence before accepting it.

## 2. Harden Structured Finance Handling

- [x] 2.1 Restrict regex symbol extraction to explicit syntax, exchange codes, and original-uppercase candidates while preserving known mappings.
- [x] 2.2 Use LLM symbol extraction to validate ambiguous uppercase candidates and retain search as the empty-result fallback.
- [x] 2.3 Filter provider error payloads and return an unhandled result when all structured finance lookups fail.

## 3. Verify Routing Behavior

- [x] 3.1 Add focused tests for product-credit classification, valid finance evidence, ticker extraction, LLM candidate validation, and all-provider failure fallback.
- [x] 3.2 Run focused tests, the full pytest suite in `env1`, strict OpenSpec validation, and `git diff --check`.
