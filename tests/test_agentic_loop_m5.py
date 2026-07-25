"""M5 regressions: static planning removed, per-tool budgets and sole trace."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from langchain.langchain_orchestrator import LangChainOrchestrator
from langchain.langchain_rag import SearchRAGChain
from langchain.langchain_react_tools import (
    ReActFetchUrlTool,
    ReActSearchTool,
    create_react_tools_from_config,
)
from search.search import SearchClient, SearchHit
from utils.query_orchestration import QueryAnalysis, analyze_query


ROOT = Path(__file__).resolve().parents[1]


class _SearchClient(SearchClient):
    def search(self, query: str, num_results: int = 5, **kwargs):
        return [
            SearchHit(
                title="OpenAI pricing",
                url="https://openai.com/api/pricing/",
                snippet="Official API pricing.",
            )
        ]


def test_static_plan_contracts_are_deleted() -> None:
    import utils.query_orchestration as contract

    for name in (
        "build_query_plan",
        "QueryPlan",
        "QueryPlanStep",
        "PlanController",
        "PlanStepKind",
        "verify_evidence_plan",
        "VerificationOutcome",
    ):
        assert not hasattr(contract, name)


def test_default_orchestrator_has_no_routing_or_rollout_switch() -> None:
    source = inspect.getsource(LangChainOrchestrator)
    for symbol in (
        "_make_routing_decision",
        "_generate_keywords",
        "DECISION_SYSTEM_PROMPT",
        "KEYWORD_SYSTEM_PROMPT",
        "engine_mode",
        "_apply_postcheck",
    ):
        assert symbol not in source


def test_shipped_config_has_only_loop_and_per_tool_budgets() -> None:
    config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    assert "engine" not in config
    assert "reactAgent" not in config
    assert "postcheck" not in config
    assert config["termination"]["tool_budgets"] == {
        "web_search": 3,
        "fetch_url": 3,
        "search_recovery": 2,
        "local_docs": 2,
    }


def test_web_tool_enforces_its_own_budget() -> None:
    tool = ReActSearchTool(
        search_client=_SearchClient(),
        max_calls_per_query=1,
    )
    assert "OpenAI pricing" in tool._run("OpenAI pricing")
    exhausted = json.loads(tool._run("OpenAI pricing again"))
    assert exhausted == {
        "status": "budget_exhausted",
        "reason": "max_calls_per_query",
        "limit": 1,
    }
    assert tool.get_budget_status() == {"limit": 1, "used": 1}
    tool.reset_budget()
    assert tool.get_budget_status() == {"limit": 1, "used": 0}


def test_fetch_url_tool_enforces_its_own_budget() -> None:
    tool = ReActFetchUrlTool(config={}, max_calls_per_query=1)
    # Burn the single allowed call without touching the network.
    tool._calls_in_run = 1
    exhausted = json.loads(tool._run("https://example.com"))
    assert exhausted == {
        "status": "budget_exhausted",
        "reason": "max_calls_per_query",
        "limit": 1,
    }
    assert tool.get_last_evidence_records() == []
    tool.reset_budget()
    assert tool.get_budget_status() == {"limit": 1, "used": 0}


def test_fetch_url_rejects_non_http_input_without_burning_budget() -> None:
    tool = ReActFetchUrlTool(config={}, max_calls_per_query=2)
    for bad in ("", "not-a-url", "ftp://example.com/x"):
        result = tool._run(bad)
        assert result.startswith("Fetch failed:")
    # All inputs were rejected before extraction, so no budget consumed.
    assert tool.get_budget_status() == {"limit": 2, "used": 0}
    assert tool.get_last_evidence_records() == []


def test_fetch_url_is_registered_with_independent_budget() -> None:
    tools = create_react_tools_from_config(
        config={"termination": {"tool_budgets": {"web_search": 4, "fetch_url": 3}}},
        search_client=_SearchClient(),
    )
    fetch_tools = [t for t in tools if t.name == "fetch_url"]
    assert len(fetch_tools) == 1
    assert fetch_tools[0].get_budget_status() == {"limit": 3, "used": 0}
    # web_search keeps its own budget.
    web_tools = [t for t in tools if t.name == "web_search"]
    assert web_tools and web_tools[0].max_calls_per_query == 4


def test_web_tool_emits_target_bound_structured_evidence() -> None:
    tool = ReActSearchTool(
        search_client=_SearchClient(),
        config={
            "orchestration": {
                "official_domain_resolution": {
                    "enabled": True,
                    "pins": {"openai": ["openai.com"]},
                }
            }
        },
    )
    tool.set_analysis(
        QueryAnalysis(
            query="OpenAI current pricing",
            entities=["OpenAI"],
            constraints={"authority_required": True},
        )
    )

    tool._run("site:openai.com API pricing")

    records = tool.get_last_evidence_records()
    assert records[0]["reference"] == "https://openai.com/api/pricing/"
    assert records[0]["source_tier"] == "official"


def test_factory_applies_independent_tool_budget_configuration() -> None:
    tools = create_react_tools_from_config(
        {
            "termination": {
                "tool_budgets": {"web_search": 1, "search_recovery": 4}
            }
        },
        llm=object(),
        search_client=_SearchClient(),
    )
    statuses = {
        tool.name: tool.get_budget_status()
        for tool in tools
        if hasattr(tool, "get_budget_status")
    }
    assert statuses["web_search"]["limit"] == 1
    assert statuses["search_recovery"]["limit"] == 4


def test_current_recovery_does_not_fan_out_by_year(monkeypatch) -> None:
    chain = SearchRAGChain(
        llm=object(),
        search_client=_SearchClient(),
        data_path=None,
    )
    calls: list[str] = []

    def record_granular(query: str, *args, **kwargs):
        calls.append(query)
        return []

    monkeypatch.setattr(chain, "_perform_granular_search_fallback", record_granular)

    def retrieve(query: str, analysis: QueryAnalysis) -> None:
        chain._retrieve_evidence(
            query,
            search_query=query,
            num_search_results=1,
            per_source_limit=1,
            num_retrieved_docs=0,
            enable_search=True,
            enable_local_docs=False,
            freshness=analysis.freshness,
            date_restrict=None,
            timing_recorder=None,
            analysis=analysis,
        )

    retrieve(
        "北京现在天气如何？",
        analyze_query("北京现在天气如何？", allow_search=True),
    )
    assert calls == []

    retrieve(
        "过去五年的价格趋势",
        analyze_query("过去五年的价格趋势", allow_search=True),
    )
    assert calls == ["过去五年的价格趋势"]


def test_plan_specs_are_removed() -> None:
    assert not (ROOT / "openspec/specs/query-plan-orchestration/spec.md").exists()
    assert not (ROOT / "openspec/specs/query-postcheck-fallback/spec.md").exists()
    assert not (ROOT / "openspec/specs/search-routing-core/spec.md").exists()
    assert not (ROOT / "langchain/postcheck.py").exists()
