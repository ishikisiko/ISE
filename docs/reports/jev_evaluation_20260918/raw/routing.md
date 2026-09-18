# routing  (197 unique queries, errors 0)
latency ms p50 302 p95 389 max 841 (concurrency 4)

## per-skill cases.jsonl  (baseline = handles_query word lists)
| skill | n | base P | base R | jev P | jev R | jev F1 | jev P@conf>=0.5 | jev R@conf>=0.5 |
|---|---|---|---|---|---|---|---|---|
| weather | 34 | 0.655 | 0.95 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| finance | 34 | 0.72 | 1.0 | 1.0 | 0.889 | 0.941 | 1.0 | 0.889 |
| sports | 30 | 0.5 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| location | 32 | 0.68 | 1.0 | 1.0 | 0.706 | 0.828 | 1.0 | 0.706 |
| transportation | 31 | 0.536 | 1.0 | 1.0 | 0.933 | 0.966 | 1.0 | 0.933 |

jev disagreements with cases.jsonl:
- [finance file] expect=finance pred=none conf=0.68: 这家公司最新财报如何  (财报)
- [finance file] expect=finance pred=none conf=0.78: 腾讯财报  (财报)
- [location file] expect=location pred=none conf=0.6: Find a gas station near my current location.  (domain yes, reference vague -> explicit_location_required)
- [location file] expect=location pred=none conf=0.79: Find coffee shops near me.  (缺显式地点)
- [location file] expect=location pred=none conf=0.7: Where is the nearest hospital?  (缺显式地点)
- [location file] expect=location pred=none conf=0.77: 附近有什么餐厅  (缺显式地点)
- [location file] expect=location pred=none conf=0.63: nearest ATM to my current location  (缺显式地点)
- [transportation file] expect=transportation pred=none conf=0.91: What time does the next train to Osaka leave?  (no origin -> route_endpoints_required)

## route_intent_dataset  6-way accuracy 55/57 = 0.965
- route024 gold=none pred=finance conf=0.99: What is 50 USD in EUR?
- route040 gold=none pred=transportation conf=0.83: How do I get from the airport to downtown Tokyo?