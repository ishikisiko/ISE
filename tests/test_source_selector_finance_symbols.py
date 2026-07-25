"""Deterministic finance symbol extraction and structured-lookup fallback.

These cover the preflight half of the archived `harden-finance-domain-routing`
change. The classification half was reverted under roadmap M0 (see
`docs/agentic_loop_roadmap.md`); its cases live in
`skills/finance/evals/cases.jsonl` until M2 builds the finance skill.
"""

import json

from search.source_selector import IntelligentSourceSelector


class StubFinanceLLM:
    def __init__(self, *, symbols=None):
        self.symbols = list(symbols or [])
        self.calls = []
        self.provider = "stub"
        self.model_id = "stub-model"

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"content": json.dumps({"symbols": self.symbols})}


def test_lowercase_product_words_are_not_ticker_symbols():
    selector = IntelligentSourceSelector(use_llm=False)

    assert selector._extract_finance_symbols("claude pro fable") == []


def test_explicit_and_original_uppercase_symbols_are_preserved_without_llm():
    selector = IntelligentSourceSelector(use_llm=False)

    symbols = selector._extract_finance_symbols(
        "比较 $AAPL、(NVDA)、600519 和 MSFT"
    )

    assert set(symbols) == {"AAPL", "NVDA", "600519", "MSFT"}


def test_llm_rejects_ambiguous_uppercase_candidates():
    llm = StubFinanceLLM(symbols=[])
    selector = IntelligentSourceSelector(llm_client=llm, use_llm=True)

    symbols = selector._extract_finance_symbols("AI PRO 赠送额度")

    assert symbols == []
    assert "AI, PRO" in llm.calls[0]["user_prompt"]


def test_llm_can_resolve_unmapped_company_when_no_symbol_exists():
    selector = IntelligentSourceSelector(
        llm_client=StubFinanceLLM(symbols=["PLTR"]),
        use_llm=True,
    )

    assert selector._extract_finance_symbols("查询 Palantir 的股价") == ["PLTR"]


def test_all_finance_provider_errors_fall_back_to_general_handling(monkeypatch):
    selector = IntelligentSourceSelector(use_llm=False)
    monkeypatch.setattr(
        selector,
        "_extract_finance_symbols",
        lambda query: ["AAPL", "MSFT"],
    )
    monkeypatch.setattr(
        selector,
        "_query_stock_price",
        lambda symbol, timing_recorder=None: {"error": f"no data for {symbol}"},
    )

    result = selector._handle_finance("AAPL 和 MSFT 股价", timing_recorder=None)

    assert result == {
        "handled": False,
        "reason": "data_fetch_failed",
        "skipped": True,
        "symbols": ["AAPL", "MSFT"],
    }
    assert "answer" not in result


def test_finance_result_contains_only_successful_symbols(monkeypatch):
    selector = IntelligentSourceSelector(use_llm=False)
    monkeypatch.setattr(
        selector,
        "_extract_finance_symbols",
        lambda query: ["AAPL", "MSFT"],
    )
    monkeypatch.setattr(
        selector,
        "_query_stock_price",
        lambda symbol, timing_recorder=None: (
            {"error": "unavailable"}
            if symbol == "AAPL"
            else {"c": 420.0, "source_name": "stub"}
        ),
    )

    result = selector._handle_finance("AAPL 和 MSFT 股价", timing_recorder=None)

    assert result["handled"] is True
    assert result["symbols"] == ["AAPL", "MSFT"]
    assert result["data"] == [
        {"c": 420.0, "source_name": "stub", "symbol": "MSFT"}
    ]
