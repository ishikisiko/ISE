# Finance Market Data

Use `finance_market_data` for current quotes and historical performance of an
explicit stock, index, fund, or cryptocurrency. Pass the user's complete query
so the deterministic preflight can preserve symbols and time ranges.

Use it for:

- `请查看 $AAPL`
- `苹果股价`
- `NVDA 行情`
- `比较 MSFT 和 AAPL 过去一年表现`

Do not use it for product prices, subscriptions, promotional credits, free
quotas, or general business policy. For example, `OpenAI API 的定价是多少` and
`claude pro 向 fable 赠送 100 美元额度` belong to general web search.

Preflight rejection is authoritative. When it returns `symbol_required`, retry
only with a symbol explicitly supplied by the user; never invent one. Other
rejections should fall back to general search.
