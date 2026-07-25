"""Finance preflight regressions retained under the M2 skill package."""

from pathlib import Path

from evidence import RetrievalOptions
from skills import SkillRegistry
from skills.contracts import SkillManifest
from skills.finance.handler import FinanceSkillHandler


def build_handler(config=None):
    manifest = SkillManifest(
        name="finance",
        version=1,
        handler="skills.finance.handler:FinanceSkillHandler",
        tool_name="finance_market_data",
        description="finance",
        args_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        budget={"max_calls_per_query": 2, "timeout_seconds": 15, "max_evidence_items": 5},
        availability={},
        package_dir=str(Path("skills/finance")),
    )
    return FinanceSkillHandler(config=config or {}, manifest=manifest)


def test_lowercase_product_words_are_not_ticker_symbols():
    handler = build_handler()

    assert handler.extract_symbols("claude pro fable") == []
    assert handler.preflight({"query": "claude pro fable"}).reason == "not_finance_query"


def test_explicit_and_original_uppercase_symbols_are_preserved():
    handler = build_handler()

    symbols = handler.extract_symbols("比较 $AAPL、(NVDA)、600519 和 MSFT 股票")

    assert set(symbols) == {"AAPL", "NVDA", "600519", "MSFT"}


def test_ambiguous_uppercase_product_candidates_are_rejected_deterministically():
    handler = build_handler()

    decision = handler.preflight({"query": "AI PRO 赠送额度"})

    assert decision.accepted is False
    assert decision.reason == "not_finance_query"


def test_unmapped_company_requires_explicit_symbol_instead_of_llm_guessing():
    handler = build_handler()

    decision = handler.preflight({"query": "查询 Palantir 的股价"})

    assert decision.accepted is False
    assert decision.reason == "symbol_required"


def test_all_finance_provider_errors_fall_back_to_general_handling(monkeypatch):
    handler = build_handler()
    monkeypatch.setattr(
        handler,
        "_query_quote",
        lambda symbol, timing_recorder=None, require_market_cap=False: {"error": f"no data for {symbol}"},
    )

    registry = SkillRegistry.from_config({})
    registry._skills = {"finance": handler}
    registry._by_tool_name = {"finance_market_data": handler}
    result = registry.execute("finance", {"query": "AAPL 和 MSFT 股价"})

    assert result.preflight.accepted is True
    assert result.evidence_items == []


def test_finance_result_contains_only_successful_symbols(monkeypatch):
    handler = build_handler()
    monkeypatch.setattr(
        handler,
        "_query_quote",
        lambda symbol, timing_recorder=None, require_market_cap=False: (
            {"error": "unavailable"}
            if symbol == "AAPL"
            else {"c": 420.0, "source_name": "stub", "endpoint": "stub://quote"}
        ),
    )

    items = handler.retrieve("AAPL 和 MSFT 股价", options=RetrievalOptions())

    assert len(items) == 1
    assert items[0].metadata["data"]["symbol"] == "MSFT"
    assert "MSFT 当前价: 420" in items[0].content
