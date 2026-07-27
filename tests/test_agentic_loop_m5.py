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
from search.reference_fetch import ReferenceContent, ReferenceExtraction
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
        "recall_evidence": 3,
    }
    assert config["termination"]["max_synthesis_attempts"] == 2


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
    tool = ReActFetchUrlTool(
        config={},
        max_calls_per_query=1,
        min_content_chars=20,
    )
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


def test_fetch_url_classifies_page_with_query_entities() -> None:
    class Router:
        @staticmethod
        def extract(urls, objective=None):
            return ReferenceExtraction(
                provider="direct_fetch",
                contents=[
                    ReferenceContent(
                        provider="direct_fetch",
                        requested_url=urls[0],
                        url="https://platform.kimi.com/docs/pricing",
                        title="Kimi API Pricing",
                        content="HighSpeed input token price: 1.9 USD per million.",
                    )
                ],
            )

    tool = ReActFetchUrlTool(
        config={},
        max_calls_per_query=1,
        min_content_chars=20,
    )
    tool._router = Router()
    tool.set_analysis(analyze_query("kimik2.7code highspeed价格", allow_search=True))

    tool._run("https://platform.kimi.com/docs/pricing")

    records = tool.get_last_evidence_records()
    assert records[0]["source_tier"] == "first_party"
    assert records[0]["metadata"]["source_tier_entities"] == [
        "kimik2.7code",
        "highspeed",
    ]


def test_fetch_url_rejects_duplicate_url_without_refetching() -> None:
    class Router:
        calls = 0

        @classmethod
        def extract(cls, urls, objective=None):
            cls.calls += 1
            return ReferenceExtraction(
                provider="direct_fetch",
                contents=[
                    ReferenceContent(
                        provider="direct_fetch",
                        requested_url=urls[0],
                        url=urls[0],
                        content="Official pricing content " * 40,
                    )
                ],
            )

    tool = ReActFetchUrlTool(config={}, max_calls_per_query=3)
    tool._router = Router()

    first = tool._run("https://example.com/pricing?source=one")
    duplicate = json.loads(
        tool._run("https://example.com/pricing?source=two")
    )

    assert "Official pricing content" in first
    assert duplicate == {
        "status": "rejected",
        "reason": "duplicate_url",
        "url": "https://example.com/pricing",
    }
    assert Router.calls == 1
    assert tool.get_budget_status() == {"limit": 3, "used": 1}


def test_fetch_url_does_not_retain_short_shell_as_authority() -> None:
    class Router:
        @staticmethod
        def extract(urls, objective=None):
            return ReferenceExtraction(
                provider="direct_fetch",
                contents=[
                    ReferenceContent(
                        provider="direct_fetch",
                        requested_url=urls[0],
                        url="https://platform.kimi.com/docs/pricing",
                        content="x" * 383,
                    )
                ],
            )

    tool = ReActFetchUrlTool(config={}, min_content_chars=600)
    tool._router = Router()
    tool.set_analysis(analyze_query("kimik2.7code价格", allow_search=True))

    result = tool._run("https://platform.kimi.com/docs/pricing")

    payload = json.loads(result)
    assert payload["status"] == "no_data"
    assert payload["exhausted"] is False
    assert tool.get_last_evidence_records() == []
    outcomes = tool.get_last_fetch_outcomes()
    assert len(outcomes) == 1
    assert outcomes[0]["status"] == "no_data"
    assert outcomes[0]["chars"] == 0


def test_fetch_url_passes_structured_pricing_acceptance_to_router() -> None:
    class Router:
        acceptance_results = []

        @classmethod
        def extract(cls, urls, objective=None, accept_content=None):
            assert callable(accept_content)
            incomplete = "GLM-5.2 pricing row " + ("x" * 700)
            complete = """# 产品价格
|模型名称 |上下文 (千tokens) |输入单价 (百万tokens) |输出单价 (百万tokens) |缓存存储 (百万tokens/小时) |缓存命中 (百万tokens) |
| --- | --- | --- | --- | --- | --- |
|GLM-5.2 |200 |8元 |28元 |1元 |2元 |"""
            cls.acceptance_results = [
                accept_content(incomplete),
                accept_content(complete),
            ]
            return ReferenceExtraction(
                provider="parallel_extract",
                contents=[
                    ReferenceContent(
                        provider="parallel_extract",
                        requested_url=urls[0],
                        url=urls[0],
                        content=complete,
                    )
                ],
            )

    query = "对于GLM5.2, 3M输入，300K输出，30M输入缓存命中的价格"
    tool = ReActFetchUrlTool(config={}, min_content_chars=20)
    tool._router = Router()
    tool.set_analysis(analyze_query(query, allow_search=True))

    result = tool._run("https://example.com/pricing", objective=query)

    assert Router.acceptance_results[0][0] is False
    assert Router.acceptance_results[1] == (True, "complete_pricing_tuple")
    assert "GLM-5.2" in result


def test_fetch_url_exposes_channel_filtered_configured_pricing_sources() -> None:
    config = {
        "orchestration": {
            "pricing_sources": {
                "glm": [
                    {
                        "url": "https://bigmodel.cn/pricing",
                        "channel": "domestic",
                        "currency": "CNY",
                    },
                    {
                        "url": "https://docs.z.ai/guides/overview/pricing",
                        "channel": "global",
                        "currency": "USD",
                    },
                ]
            }
        }
    }
    tool = ReActFetchUrlTool(config=config)

    unspecified = analyze_query(
        "GLM5.2 的 1M 输入价格",
        allow_search=True,
    )
    global_only = analyze_query(
        "按 Z.ai 美元价格算 GLM5.2 的 1M 输入成本",
        allow_search=True,
    )

    assert [
        candidate["url"]
        for candidate in tool.get_pricing_source_candidates(
            unspecified.numeric_requirements
        )
    ] == [
        "https://bigmodel.cn/pricing",
        "https://docs.z.ai/guides/overview/pricing",
    ]
    assert tool.get_pricing_source_candidates(global_only.numeric_requirements) == [
        {
            "url": "https://docs.z.ai/guides/overview/pricing",
            "channel": "global",
            "currency": "USD",
        }
    ]


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


def test_search_and_fetch_share_ledger_ids_across_tools() -> None:
    """A URL found via search then fetched keeps the same citation ID, and the
    fetch upgrades it to fetched full text."""
    from evidence.ledger import EvidenceLedger

    class FetchRouter:
        @staticmethod
        def extract(urls, objective=None):
            return ReferenceExtraction(
                provider="direct_fetch",
                contents=[
                    ReferenceContent(
                        provider="direct_fetch",
                        requested_url=urls[0],
                        url="https://openai.com/api/pricing/",
                        title="OpenAI pricing",
                        content="Full official pricing page body " * 40,
                    )
                ],
            )

    ledger = EvidenceLedger()

    search_tool = ReActSearchTool(search_client=_SearchClient(), config={})
    search_tool.set_ledger(ledger)
    search_output = search_tool._run("openai pricing")
    assert "[E1]" in search_output
    assert "仅摘要" in search_output

    fetch_tool = ReActFetchUrlTool(config={}, min_content_chars=20)
    fetch_tool._router = FetchRouter()
    fetch_tool.set_ledger(ledger)
    fetch_tool.set_analysis(
        analyze_query("openai pricing", allow_search=True)
    )
    fetch_output = fetch_tool._run("https://openai.com/api/pricing/")

    # The same URL fetched reuses [E1] and is marked as fetched full text.
    assert "[E1]" in fetch_output
    assert "[E2]" not in fetch_output
    assert "已抓全文" in fetch_output

    # Citation ID is stamped into the records so the loop state can resolve it.
    assert fetch_tool.get_last_evidence_records()[0]["metadata"]["eid"] == 1
    assert search_tool.get_last_evidence_records()[0]["metadata"]["eid"] == 1


def test_search_tool_renders_ledger_entries_with_tier_and_url() -> None:
    search_tool = ReActSearchTool(search_client=_SearchClient(), config={})
    output = search_tool._run("openai pricing")
    assert output.startswith("[E1]")
    assert "unknown" in output
    assert "https://openai.com/api/pricing/" in output
