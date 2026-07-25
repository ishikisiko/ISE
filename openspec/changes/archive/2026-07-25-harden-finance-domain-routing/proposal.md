## Why

General product questions that mention dollar amounts or English product names can be misclassified as finance queries, converted into fabricated ticker symbols, and terminated inside the structured finance path with provider-error text. The routing boundary must require positive finance evidence and must return control to general search whenever structured market data cannot produce a usable result.

## What Changes

- Define positive and negative finance intent examples for LLM domain classification, with `general` as the uncertainty fallback.
- Require deterministic finance evidence before accepting an LLM-only `finance` classification.
- Restrict ticker extraction to explicit ticker syntax, uppercase tokens, numeric exchange codes, known mappings, and confirmed company-to-symbol extraction instead of treating arbitrary English words as tickers.
- Treat failed finance-provider payloads as failures rather than answerable data, and fall back to the normal search route when every requested symbol fails.
- Add focused regression coverage for product-credit queries, valid ticker queries, and all-provider failure behavior.

## Capabilities

### New Capabilities
- `finance-domain-routing`: Defines guarded finance classification, evidence-based symbol extraction, and structured-data fallback behavior.

### Modified Capabilities

None.

## Impact

- Affected runtime code: `search/source_selector.py` and its interaction with the default LangChain orchestrator.
- Affected tests: new focused source-selector routing and finance fallback tests.
- No configuration, public API, or dependency changes are required.
