## ADDED Requirements

### Requirement: Finance classification SHALL require positive finance evidence
The system SHALL accept an LLM `finance` classification only when deterministic finance keywords or supported finance-symbol evidence corroborates the label. The classifier prompt SHALL define finance intent, exclude product pricing, credits, subsidies, and quota policies, and direct uncertain classifications to `general`.

#### Scenario: Product credit is not a finance-market query
- **WHEN** a query discusses a product or service granting a dollar-denominated credit without market-data keywords or ticker evidence
- **THEN** an LLM-only `finance` label SHALL be downgraded to `general`

#### Scenario: Market query retains finance classification
- **WHEN** a query contains finance-market keywords or supported explicit ticker evidence and the LLM returns `finance`
- **THEN** the system SHALL retain the `finance` classification

### Requirement: Finance symbol extraction SHALL reject arbitrary English words
The system SHALL extract deterministic symbols only from known mappings, explicit dollar-prefixed tickers, parenthesized uppercase tickers, six-digit exchange codes, or tokens that are uppercase in the original query. Bare lowercase product or company words SHALL NOT be converted to ticker symbols solely by uppercasing them.

#### Scenario: Lowercase product names produce no regex ticker candidates
- **WHEN** a query contains lowercase English product names without explicit ticker syntax or known mappings
- **THEN** the regex extraction stage SHALL NOT return those words as finance symbols

#### Scenario: Explicit ticker syntax remains supported
- **WHEN** a query contains `$AAPL`, `(NVDA)`, a supported six-digit code, or an original-uppercase ticker token
- **THEN** symbol extraction SHALL preserve the corresponding candidate

### Requirement: Optional intelligent extraction SHALL validate ambiguous candidates
When an LLM symbol extractor is enabled, the system SHALL use it to confirm ambiguous original-uppercase candidates instead of allowing unvalidated regex candidates to suppress intelligent extraction. The extractor SHALL be allowed to resolve an explicitly mentioned company when no deterministic symbol is available.

#### Scenario: LLM rejects a non-ticker uppercase token
- **WHEN** regex finds an original-uppercase candidate and the LLM symbol extractor returns no confirmed symbol
- **THEN** the candidate SHALL NOT be included in the final symbol set

#### Scenario: LLM resolves an unmapped company
- **WHEN** no deterministic symbol exists and the query explicitly asks for market data about a company the LLM can map confidently
- **THEN** the validated LLM symbol SHALL be included in the final symbol set

### Requirement: Structured finance failure SHALL fall back to general handling
The finance handler SHALL treat provider payloads containing errors as failed retrievals. When every extracted symbol fails, it SHALL return an unhandled and skipped result without a formatted finance answer so the caller can continue through general handling.

#### Scenario: Every finance provider fails
- **WHEN** all quote or history lookups return empty or error payloads
- **THEN** the finance handler SHALL return `handled=false`, `skipped=true`, and `reason=data_fetch_failed`
- **AND** the result SHALL NOT contain a terminal formatted finance answer

#### Scenario: At least one finance provider succeeds
- **WHEN** at least one symbol returns a non-error market-data payload
- **THEN** the finance handler SHALL return a handled finance result containing only successful symbol data
