"""M2 skill-contract, finance migration, and eval regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evidence import EvidenceSource, RetrievalOptions
from langchain.langchain_react_tools import ReActSkillTool, create_react_tools_from_config
from orchestrators.react_loop_graph import ReactLoopGraphRunner
from skills import SkillRegistry
from skills.finance.handler import FinanceSkillHandler


ROOT = Path(__file__).resolve().parents[1]


def test_finance_manifest_loads_and_handler_mounts_evidence_source():
    registry = SkillRegistry.from_config({})
    handler = registry.get("finance")

    assert isinstance(handler, FinanceSkillHandler)
    assert isinstance(handler, EvidenceSource)
    assert handler.manifest.tool_name == "finance_market_data"
    assert handler.manifest.budget == {
        "max_calls_per_query": 2,
        "timeout_seconds": 15,
        "max_evidence_items": 5,
    }


def test_registry_availability_gate_removes_disabled_skill_from_tool_surface():
    registry = SkillRegistry.from_config({"skills": {"disabled": ["finance"]}})

    assert registry.get("finance") is None
    assert registry.availability()[0].reason == "disabled_by_config"


def test_react_tool_surface_has_one_finance_tool_and_no_legacy_domain_tool():
    tools = create_react_tools_from_config(config={})
    names = [tool.name for tool in tools]

    assert names.count("finance_market_data") == 1
    assert "domain_api" not in names


def test_preflight_rejection_reason_is_returned_to_loop_model():
    handler = SkillRegistry.from_config({}).get("finance")
    tool = ReActSkillTool(skill_handler=handler)

    payload = json.loads(tool._run("这家公司最新财报如何"))

    assert payload["status"] == "rejected"
    assert payload["reason"] == "symbol_required"
    assert "explicit user input" in payload["instruction"]
    assert ReactLoopGraphRunner._is_textual_tool_error(json.dumps(payload)) is True


def test_skill_tool_enforces_and_resets_per_query_call_budget(monkeypatch):
    handler = SkillRegistry.from_config({}).get("finance")
    monkeypatch.setattr(handler, "run", lambda args, options: [])
    tool = ReActSkillTool(skill_handler=handler)

    tool._run("请查看 $AAPL")
    tool._run("请查看 $MSFT")
    exhausted = json.loads(tool._run("请查看 $NVDA"))
    tool.reset_budget()

    assert exhausted == {
        "status": "budget_exhausted",
        "reason": "max_calls_per_query",
        "limit": 2,
    }
    assert json.loads(tool._run("这家公司最新财报如何"))["status"] == "rejected"


def test_fx_alias_is_normalized_by_deterministic_preflight():
    handler = SkillRegistry.from_config({}).get("finance")

    decision = handler.preflight({"query": "美元兑人民币汇率"})

    assert decision.accepted is True
    assert decision.normalized_args["symbols"] == ["CNY=X"]


@pytest.mark.parametrize(
    "case",
    [
        json.loads(line)
        for line in (ROOT / "skills/finance/evals/cases.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ],
)
def test_finance_skill_prose_examples_are_executable_evals(case):
    handler = SkillRegistry.from_config({}).get("finance")

    assert handler.handles_query(case["query"]) is (case["expect"] == "finance")


def test_finance_runtime_does_not_depend_on_source_selector():
    assert not (ROOT / "search/source_selector.py").exists()


def test_registry_execution_preserves_skill_provenance(monkeypatch):
    registry = SkillRegistry.from_config({})
    handler = registry.get("finance")
    monkeypatch.setattr(
        handler,
        "_query_quote",
        lambda symbol, timing_recorder=None, require_market_cap=False: {
            "c": 123.0,
            "pc": 120.0,
            "source_name": "stub",
            "endpoint": "stub://quote",
        },
    )

    result = registry.execute(
        "finance",
        {"query": "请查看 $AAPL"},
        options=RetrievalOptions(metadata={"originating_tool_call": "skill_1"}),
    )

    assert result.preflight.accepted is True
    assert len(result.evidence_items) == 1
    item = result.evidence_items[0]
    assert item.source_id == "skill:finance"
    assert item.metadata["tool_name"] == "finance_market_data"
    assert item.metadata["originating_tool_call"] == "skill_1"


def test_single_formatter_covers_quote_market_cap_and_history():
    quote = FinanceSkillHandler.format_answer(
        "TSLA",
        {
            "c": 250,
            "pc": 240,
            "marketCap": 800_000_000_000,
            "currency": "USD",
            "source_name": "stub",
        },
        mode="quote",
    )
    history = FinanceSkillHandler.format_answer(
        "AAPL",
        {
            "start_date": "2025-01-01",
            "end_date": "2026-01-01",
            "start_price": 100,
            "end_price": 125,
            "pct_change": 25,
            "high": 130,
            "low": 90,
            "source_name": "stub",
        },
        mode="history",
    )

    assert "市值: 800,000,000,000 USD" in quote
    assert "期间涨跌: +25.00%" in history
