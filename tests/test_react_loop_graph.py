"""Unit tests for the LangGraph ReAct loop (orchestrators/react_loop_graph.py).

All tests use scripted fake chat models and fake tools; no real LLM or search.
"""

from typing import Any, Dict, List, Optional

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool as lc_tool

from orchestrators.react_loop_graph import (
    LoopVerdict,
    ReactLoopGraphRunner,
    langgraph_available,
    normalize_termination_config,
)
from utils.query_orchestration import QueryAnalysis, analyze_query
from utils.timing_utils import TimingRecorder
from utils.workflow_trace import WorkflowTracer

pytestmark = pytest.mark.skipif(not langgraph_available(), reason="langgraph not installed")


class ScriptedChatModel(BaseChatModel):
    """A fake chat model returning scripted replies based on a queue.

    Without bind_tools support: exercises the JSON-shim path.
    """

    replies: List[Any]
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, AIMessage):
            message = reply.model_copy(deep=True)
            message.id = f"fake-{self.calls}-{id(message)}"
        else:
            message = AIMessage(content=str(reply))
        return ChatResult(generations=[ChatGeneration(message=message)])


class NativeScriptedChatModel(ScriptedChatModel):
    """Scripted model that pretends to support native bind_tools."""

    def bind_tools(self, tools, **kwargs):
        return self


class RecordingScriptedChatModel(NativeScriptedChatModel):
    """Scripted model that also records the message contents of every call."""

    seen: List[Any] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append([getattr(message, "content", "") for message in messages])
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _tool_call(name: str, args: Optional[Dict[str, Any]] = None, call_id: str = "c1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args or {"query": "q"}, "id": call_id, "type": "tool_call"}],
    )


class FakeTools:
    """Factory for scripted LangChain tools."""

    @staticmethod
    def make(name: str, outputs: List[str]):
        state = {"calls": 0}

        @lc_tool(name)
        def fake_tool(query: str) -> str:
            """Fake tool for testing. Args: query."""
            idx = min(state["calls"], len(outputs) - 1)
            state["calls"] += 1
            result = outputs[idx]
            if isinstance(result, Exception):
                raise result
            return result

        fake_tool.name = name
        return fake_tool


def make_runner(
    replies,
    tools,
    *,
    max_iterations: int = 5,
    termination_config: Optional[Dict[str, Any]] = None,
    judge_llm=None,
    query: str = "苹果和微软的区别",
    tracer: Optional[Any] = None,
    timing_recorder: Optional[TimingRecorder] = None,
) -> ReactLoopGraphRunner:
    return ReactLoopGraphRunner(
        llm=NativeScriptedChatModel(replies=replies),
        tools=tools,
        max_iterations=max_iterations,
        termination_config=termination_config,
        judge_llm=judge_llm,
        query=query,
        tracer=tracer,
        timing_recorder=timing_recorder,
    )


class TestReasoningPolicyResolution:
    """Per-scenario reasoning levels are derived from the query analysis."""

    POLICY = {
        "easy": "low",
        "hard": "high",
        "judge_easy": "disabled",
        "judge_hard": "low",
        "hard_claim_classes": ["comparison", "pricing", "numeric", "historical"],
    }

    @staticmethod
    def _runner(*, claim_classes, comparison_required=False):
        analysis = QueryAnalysis(
            query="q",
            claim_classes=list(claim_classes),
            constraints={"comparison_required": comparison_required},
        )
        return ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(replies=["unused"]),
            tools=[],
            query="q",
            analysis=analysis,
            termination_config={"reasoning_policy": TestReasoningPolicyResolution.POLICY},
        )

    def test_easy_scenario_uses_low_and_disabled_judge(self):
        runner = self._runner(claim_classes=["current"])
        assert runner._act_reasoning == "low"
        assert runner._judge_reasoning == "disabled"

    def test_comparison_scenario_uses_high_and_low_judge(self):
        runner = self._runner(claim_classes=["comparison"], comparison_required=True)
        assert runner._act_reasoning == "high"
        assert runner._judge_reasoning == "low"

    def test_pricing_scenario_is_hard(self):
        runner = self._runner(claim_classes=["numeric", "pricing"])
        assert runner._act_reasoning == "high"

    def test_no_policy_falls_back_to_model_default(self):
        analysis = QueryAnalysis(query="q", claim_classes=["comparison"])
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(replies=["unused"]),
            tools=[],
            query="q",
            analysis=analysis,
        )
        assert runner._act_reasoning is None
        assert runner._judge_reasoning is None


class TestTerminationSemantics:
    def test_authority_rejection_points_directly_to_unfetched_official_page(self):
        analysis = QueryAnalysis(
            query="Acme price 1",
            constraints={"authority_required": True},
            requires_evidence=True,
        )
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(replies=["unused"]),
            tools=[FakeTools.make("fetch_url", ["page"])],
            query=analysis.query,
            analysis=analysis,
        )
        instruction = runner._official_fetch_instruction(
            {
                "evidence_records": [
                    {
                        "tool_name": "web_search",
                        "source_tier": "official",
                        "reference": "https://docs.acme.example/pricing",
                    }
                ]
            }
        )

        assert "直接调用 fetch_url" in instruction
        assert "https://docs.acme.example/pricing" in instruction
        assert "必须调用 fetch_url" in runner.system_prompt

    def test_authority_rejection_skips_url_that_already_failed_fetch(self):
        """When the authoritative URL has already been attempted (in
        fetch_outcomes), the instruction must NOT point the model at it again.
        Instead it should steer toward a different source."""
        analysis = QueryAnalysis(
            query="Acme price 2",
            constraints={"authority_required": True},
            requires_evidence=True,
        )
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(replies=["unused"]),
            tools=[FakeTools.make("fetch_url", ["page"])],
            query=analysis.query,
            analysis=analysis,
        )
        instruction = runner._official_fetch_instruction(
            {
                "evidence_records": [
                    {
                        "tool_name": "web_search",
                        "source_tier": "official",
                        "reference": "https://docs.acme.example/pricing",
                    }
                ],
                "fetch_outcomes": [
                    {
                        "url": "https://docs.acme.example/pricing",
                        "status": "no_data",
                        "exhausted": True,
                    }
                ],
            }
        )

        assert "直接调用 fetch_url" not in instruction
        assert "https://docs.acme.example/pricing" not in instruction
        assert "不要对已失败的同一 URL 重复调用 fetch_url" in instruction

    def test_provisional_authority_stops_with_qualified_answer(self):
        class ProvisionalFetchTool:
            name = "fetch_url"
            description = "test fetch"

            @staticmethod
            def invoke(args):
                return "目标产品页面包含价格说明，但域名归属仍待验证。"

            @staticmethod
            def get_last_evidence_records():
                return [
                    {
                        "source_type": "web",
                        "source_tier": "fetched",
                        "reference": "https://docs.newbrand.example/pricing",
                        "content": "目标产品页面包含价格说明，但域名归属仍待验证。",
                        "metadata": {"authority_provisional": True},
                    }
                ]

        analysis = QueryAnalysis(
            query="Acme price",
            entities=["Acme"],
            claim_classes=["numeric", "pricing"],
            constraints={"authority_required": True},
            requires_evidence=True,
        )
        draft = "当前页面显示该产品按使用量计费，但官方归属尚未完全核验。"
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(
                replies=[
                    _tool_call(
                        "fetch_url",
                        {"url": "https://docs.newbrand.example/pricing"},
                    ),
                    draft,
                    draft,
                ]
            ),
            tools=[ProvisionalFetchTool()],
            max_iterations=8,
            termination_config={"no_progress_threshold": 2},
            query=analysis.query,
            analysis=analysis,
        )

        result = runner.run(analysis.query)

        assert result["loop_status"] == "evidence_insufficient"
        assert result["iterations"] == 3
        assert result["verdicts"][-1]["reason"] == "authority_unverified"
        assert "官方归属未通过权威策略验证" in result["answer"]
        assert result["answer"].endswith(draft)

    def test_aggregator_cited_number_is_rejected_by_citation_check(self):
        """A numeric claim cited to an aggregator record must be rejected even
        when the same figure appears verbatim in the evidence pool (the old
        string-inclusion rule would have passed it)."""

        class AggregatorFetchTool:
            name = "fetch_url"
            description = "test fetch"

            @staticmethod
            def invoke(args):
                return "第三方聚合页显示 Acme 价格为 $0.50 per 1M tokens。"

            @staticmethod
            def get_last_evidence_records():
                return [
                    {
                        "source_type": "web",
                        "source_tier": "aggregator",
                        "reference": "https://aggregator.example/acme-pricing",
                        "content": "第三方聚合页显示 Acme 价格为 $0.50 per 1M tokens。",
                        "metadata": {
                            "eid": 1,
                            "retrieval_kind": "fetch_url",
                            "content_chars": 500,
                        },
                    }
                ]

        analysis = QueryAnalysis(
            query="Acme price",
            entities=["Acme"],
            claim_classes=["numeric", "pricing"],
            constraints={"authority_required": True},
            requires_evidence=True,
        )
        draft = "Acme 的价格为 $0.50 per 1M tokens [E1]。"
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(
                replies=[
                    _tool_call(
                        "fetch_url",
                        {"url": "https://aggregator.example/acme-pricing"},
                    ),
                    draft,
                    draft,
                    draft,
                    draft,
                ]
            ),
            tools=[AggregatorFetchTool()],
            max_iterations=6,
            termination_config={"no_progress_threshold": 2},
            query=analysis.query,
            analysis=analysis,
        )

        result = runner.run(analysis.query)

        assert result["loop_status"] != "succeeded"
        rejected = [
            verdict
            for verdict in result["verdicts"]
            if verdict["reason"] == "final_answer_rejected"
        ]
        assert rejected
        assert any(
            "citation_not_authoritative" in verdict["failure_types"]
            for verdict in rejected
        )
        assert any(
            any(
                str(constraint).startswith("citation_not_authoritative")
                for constraint in verdict["constraints_missing"]
            )
            for verdict in rejected
        )

    def test_official_fetched_citation_passes_citation_check(self):
        """A numeric claim cited to an official, full-fetched record with a
        matching source_target must clear both the citation check and the
        official-target coverage gate."""

        class OfficialFetchTool:
            name = "fetch_url"
            description = "test fetch"

            @staticmethod
            def invoke(args):
                return "Acme 官方价格页：API 调用 $0.50 per 1M tokens。"

            @staticmethod
            def get_last_evidence_records():
                return [
                    {
                        "source_type": "web",
                        "source_tier": "official",
                        "reference": "https://docs.acme.example/pricing",
                        "content": "Acme 官方价格页：API 调用 $0.50 per 1M tokens。",
                        "metadata": {
                            "eid": 1,
                            "retrieval_kind": "fetch_url",
                            "content_chars": 800,
                            "source_target": "Acme",
                        },
                    }
                ]

        analysis = QueryAnalysis(
            query="Acme price",
            entities=["Acme"],
            claim_classes=["numeric", "pricing"],
            constraints={"authority_required": True},
            requires_evidence=True,
        )
        draft = "Acme 官方价格为 $0.50 per 1M tokens [E1]。"
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(
                replies=[
                    _tool_call(
                        "fetch_url",
                        {"url": "https://docs.acme.example/pricing"},
                    ),
                    draft,
                ]
            ),
            tools=[OfficialFetchTool()],
            max_iterations=6,
            query=analysis.query,
            analysis=analysis,
        )

        result = runner.run(analysis.query)

        assert result["loop_status"] == "succeeded"
        assert result["verdicts"][-1]["reason"] == "constraints_satisfied"
        assert all(
            not any(
                str(failure).startswith("citation_")
                for failure in verdict["failure_types"]
            )
            for verdict in result["verdicts"]
        )

    def test_complete_pricing_tuple_forces_tool_free_decimal_synthesis(self):
        class CompletePricingFetchTool:
            name = "fetch_url"
            description = "test fetch"

            @staticmethod
            def get_pricing_source_candidates(requirements=None):
                return [
                    {
                        "url": "https://bigmodel.example/pricing",
                        "channel": "domestic",
                        "currency": "CNY",
                    }
                ]

            @staticmethod
            def invoke(args):
                return "Fetched complete official pricing table [E1]."

            @staticmethod
            def get_last_evidence_records():
                return [
                    {
                        "source_type": "web",
                        "source_tier": "official",
                        "reference": "https://bigmodel.example/pricing",
                        "content": """# 产品价格
|模型名称 |上下文 (千tokens) |输入单价 (百万tokens) |输出单价 (百万tokens) |缓存存储 (百万tokens/小时) |缓存命中 (百万tokens) |
| --- | --- | --- | --- | --- | --- |
|GLM-5.2 |200 |8元 |28元 |1元 |2元 |""",
                        "metadata": {
                            "eid": 1,
                            "retrieval_kind": "fetch_url",
                            "content_chars": 900,
                            "source_target": "GLM5.2",
                        },
                    }
                ]

            @staticmethod
            def get_last_fetch_outcomes():
                return [
                    {
                        "url": "https://bigmodel.example/pricing",
                        "status": "success",
                        "chars": 900,
                    }
                ]

        query = "对于GLM5.2, 3M输入，300K输出，30M输入缓存命中的价格"
        analysis = analyze_query(query, allow_search=True)
        model = NativeScriptedChatModel(
            replies=[
                _tool_call(
                    "fetch_url",
                    {"url": "https://bigmodel.example/pricing"},
                )
            ]
        )
        judge = ScriptedChatModel(
            replies=['{"passes": false, "missing_constraints": ["unused"]}']
        )
        runner = ReactLoopGraphRunner(
            llm=model,
            tools=[CompletePricingFetchTool()],
            max_iterations=5,
            judge_llm=judge,
            query=query,
            analysis=analysis,
        )

        result = runner.run(query)

        assert result["loop_status"] == "succeeded"
        assert result["forced_synthesis"] is True
        assert result["synthesis_attempts"] == 1
        assert result["fetch_outcomes"][0]["status"] == "success"
        assert result["iterations"] == 2
        assert model.calls == 0
        assert judge.calls == 0
        assert "3×8=24 + 0.3×28=8.4 + 30×2=60" in result["answer"]
        assert "**¥92.4**" in result["answer"]
        assert result["verdicts"][0]["reason"] == "ready_to_synthesize"
        assert result["verdicts"][-1]["reason"] == "constraints_satisfied"

    def test_incomplete_pricing_row_returns_specific_evidence_gap(self):
        class IncompletePricingFetchTool:
            name = "fetch_url"
            description = "test fetch"

            @staticmethod
            def invoke(args):
                return "Fetched an incomplete official pricing row [E1]."

            @staticmethod
            def get_last_evidence_records():
                return [
                    {
                        "source_type": "web",
                        "source_tier": "official",
                        "reference": "https://bigmodel.example/pricing",
                        "content": (
                            "# 产品价格\n输入单价 (百万tokens) 输出单价 "
                            "(百万tokens) 缓存命中 (百万tokens)\n|GLM-5.2"
                        ),
                        "metadata": {
                            "eid": 1,
                            "retrieval_kind": "fetch_url",
                            "content_chars": 800,
                            "source_target": "GLM5.2",
                        },
                    }
                ]

            @staticmethod
            def get_last_fetch_outcomes():
                return [
                    {
                        "url": "https://bigmodel.example/pricing",
                        "status": "no_data",
                        "chars": 800,
                        "error_type": "objective_incomplete",
                        "exhausted": True,
                    }
                ]

        query = "对于GLM5.2, 3M输入，300K输出，30M输入缓存命中的价格"
        analysis = analyze_query(query, allow_search=True)
        model = NativeScriptedChatModel(
            replies=[
                _tool_call(
                    "fetch_url",
                    {"url": "https://bigmodel.example/pricing"},
                )
            ]
        )
        runner = ReactLoopGraphRunner(
            llm=model,
            tools=[IncompletePricingFetchTool()],
            max_iterations=1,
            query=query,
            analysis=analysis,
        )

        result = runner.run(query)

        assert result["loop_status"] == "evidence_insufficient"
        assert result["forced_synthesis"] is True
        assert result["synthesis_attempts"] == 1
        assert result["fetch_outcomes"][0]["error_type"] == "objective_incomplete"
        assert "输入单价" in result["answer"]
        assert "缓存命中输入单价" in result["answer"]
        assert "迭代次数用尽" not in result["answer"]
        assert any(
            "pricing_rate:input" in verdict["constraints_missing"]
            for verdict in result["verdicts"]
        )

    def test_configured_pricing_sources_fall_back_without_another_llm_turn(self):
        class PricingFetchTool:
            name = "fetch_url"
            description = "test fetch"

            def __init__(self):
                self.calls = []
                self.last_records = []
                self.last_outcomes = []

            @staticmethod
            def get_pricing_source_candidates(requirements=None):
                return [
                    {"url": "https://bigmodel.example/pricing"},
                    {"url": "https://docs.z.example/pricing"},
                ]

            def invoke(self, args):
                url = args["url"]
                self.calls.append(url)
                if "bigmodel" in url:
                    self.last_records = []
                    self.last_outcomes = [
                        {
                            "url": url,
                            "status": "no_data",
                            "error_type": "objective_incomplete",
                            "exhausted": True,
                        }
                    ]
                    return '{"status":"no_data","error_type":"objective_incomplete"}'
                self.last_records = [
                    {
                        "source_type": "web",
                        "source_tier": "official",
                        "reference": url,
                        "content": """Prices per 1M tokens.
| Model | Input | Cached Input | Cached Input Storage | Output |
| GLM-5.2 | $1.4 | $0.26 | Limited-time Free | $4.4 |""",
                        "metadata": {
                            "eid": 1,
                            "retrieval_kind": "fetch_url",
                            "content_chars": 800,
                            "source_target": "GLM5.2",
                        },
                    }
                ]
                self.last_outcomes = [
                    {"url": url, "status": "success", "chars": 800}
                ]
                return "Fetched complete pricing [E1]."

            def get_last_evidence_records(self):
                return list(self.last_records)

            def get_last_fetch_outcomes(self):
                return list(self.last_outcomes)

            def get_budget_status(self):
                return {"limit": 2, "used": len(self.calls)}

        query = "对于GLM5.2, 3M输入，300K输出，30M输入缓存命中的价格"
        analysis = analyze_query(query, allow_search=True)
        model = NativeScriptedChatModel(replies=["unused"])
        tool = PricingFetchTool()
        runner = ReactLoopGraphRunner(
            llm=model,
            tools=[tool],
            max_iterations=5,
            query=query,
            analysis=analysis,
        )

        result = runner.run(query)

        assert result["loop_status"] == "succeeded"
        assert tool.calls == [
            "https://bigmodel.example/pricing",
            "https://docs.z.example/pricing",
        ]
        assert model.calls == 0
        assert result["iterations"] == 3
        assert [verdict["reason"] for verdict in result["verdicts"]] == [
            "pricing_source_recovery",
            "ready_to_synthesize",
            "constraints_satisfied",
        ]
        assert "**$13.32**" in result["answer"]

    def test_pricing_process_narration_at_budget_still_gets_synthesis(self):
        query = "对于GLM5.2, 3M输入，300K输出，30M输入缓存命中的价格"
        analysis = analyze_query(query, allow_search=True)
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(
                replies=["我需要先搜索价格信息，然后我会计算并回答用户。"]
            ),
            tools=[],
            max_iterations=1,
            query=query,
            analysis=analysis,
        )

        result = runner.run(query)

        assert result["loop_status"] == "evidence_insufficient"
        assert result["forced_synthesis"] is True
        assert result["synthesis_attempts"] == 1
        assert "可核验官方完整价目元组" in result["answer"]
        assert "迭代次数用尽" not in result["answer"]

    def test_multi_token_entity_does_not_deadlock_official_coverage(self):
        """A single-entity query whose name tokenizes into several entities
        must not demand official coverage per token.

        ``analysis.entities`` for "Kimi K2.7 Code HighSpeed" is a token bag,
        while ``source_target`` can only name the one entity that owns the
        domain. Requiring coverage for every token made the gate unsatisfiable
        and starved the loop into stagnation with no answer at all.
        """

        class OfficialFetchTool:
            name = "fetch_url"
            description = "test fetch"

            @staticmethod
            def invoke(args):
                return "Kimi 官方定价页：输出价格 ¥54.00 / 1M tokens。"

            @staticmethod
            def get_last_evidence_records():
                return [
                    {
                        "source_type": "web",
                        "source_tier": "official",
                        "reference": "https://platform.kimi.example/docs/pricing",
                        "content": "Kimi 官方定价页：输出价格 ¥54.00 / 1M tokens。",
                        "metadata": {
                            "eid": 1,
                            "retrieval_kind": "fetch_url",
                            "content_chars": 900,
                            "source_target": "Kimi",
                        },
                    }
                ]

        analysis = QueryAnalysis(
            query="Kimi K2.7 Code HighSpeed 的价格是多少？",
            entities=["K2.7", "Kimi", "Code", "HighSpeed"],
            claim_classes=["numeric", "pricing"],
            constraints={"authority_required": True},
            requires_evidence=True,
        )
        draft = "Kimi K2.7 Code HighSpeed 输出价格为 ¥54.00 / 1M tokens [E1]。"
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(
                replies=[
                    _tool_call(
                        "fetch_url",
                        {"url": "https://platform.kimi.example/docs/pricing"},
                    ),
                    draft,
                ]
            ),
            tools=[OfficialFetchTool()],
            max_iterations=6,
            query=analysis.query,
            analysis=analysis,
        )

        result = runner.run(analysis.query)

        assert result["loop_status"] == "succeeded"
        assert result["answer"] == draft
        assert all(
            not any(
                str(constraint).startswith("official:")
                for constraint in verdict["constraints_missing"]
            )
            for verdict in result["verdicts"]
        )

    def test_comparison_member_without_official_evidence_is_gated(self):
        """Per-target official coverage still applies to explicit comparison
        members, which is the case the gate was written for."""

        class OfficialFetchTool:
            name = "fetch_url"
            description = "test fetch"

            @staticmethod
            def invoke(args):
                return "Acme 官方价格页：API 调用 $0.50 per 1M tokens。"

            @staticmethod
            def get_last_evidence_records():
                return [
                    {
                        "source_type": "web",
                        "source_tier": "official",
                        "reference": "https://docs.acme.example/pricing",
                        "content": "Acme 官方价格页：API 调用 $0.50 per 1M tokens。",
                        "metadata": {
                            "eid": 1,
                            "retrieval_kind": "fetch_url",
                            "content_chars": 800,
                            "source_target": "Acme",
                        },
                    }
                ]

        analysis = QueryAnalysis(
            query="对比 Acme 和 Zeta 的价格",
            entities=["Acme", "Zeta"],
            comparison_members=["Acme", "Zeta"],
            claim_classes=["numeric", "pricing"],
            constraints={"authority_required": True},
            requires_evidence=True,
        )
        draft = "Acme 的价格为 $0.50 per 1M tokens [E1]，Zeta 暂无官方数据。"
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(
                replies=[
                    _tool_call(
                        "fetch_url",
                        {"url": "https://docs.acme.example/pricing"},
                    ),
                    draft,
                    draft,
                    draft,
                    draft,
                ]
            ),
            tools=[OfficialFetchTool()],
            max_iterations=4,
            termination_config={"no_progress_threshold": 2},
            query=analysis.query,
            analysis=analysis,
        )

        result = runner.run(analysis.query)

        assert result["loop_status"] != "succeeded"
        assert any(
            "official:Zeta" in verdict["constraints_missing"]
            for verdict in result["verdicts"]
        )
        assert all(
            "official:Acme" not in verdict["constraints_missing"]
            for verdict in result["verdicts"]
        )

    def test_numeric_claim_without_citation_is_rejected(self):
        """A numeric claim with no [En] marker at all must surface a
        citation_missing gap rather than passing via pool string inclusion."""

        class OfficialFetchTool:
            name = "fetch_url"
            description = "test fetch"

            @staticmethod
            def invoke(args):
                return "Acme 官方价格页：API 调用 $0.50 per 1M tokens。"

            @staticmethod
            def get_last_evidence_records():
                return [
                    {
                        "source_type": "web",
                        "source_tier": "official",
                        "reference": "https://docs.acme.example/pricing",
                        "content": "Acme 官方价格页：API 调用 $0.50 per 1M tokens。",
                        "metadata": {
                            "eid": 1,
                            "retrieval_kind": "fetch_url",
                            "content_chars": 800,
                            "source_target": "Acme",
                        },
                    }
                ]

        analysis = QueryAnalysis(
            query="Acme price",
            entities=["Acme"],
            claim_classes=["numeric", "pricing"],
            constraints={"authority_required": True},
            requires_evidence=True,
        )
        draft = "Acme 官方价格为 $0.50 per 1M tokens。"
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(
                replies=[
                    _tool_call(
                        "fetch_url",
                        {"url": "https://docs.acme.example/pricing"},
                    ),
                    draft,
                    draft,
                    draft,
                ]
            ),
            tools=[OfficialFetchTool()],
            max_iterations=6,
            termination_config={"no_progress_threshold": 2},
            query=analysis.query,
            analysis=analysis,
        )

        result = runner.run(analysis.query)

        assert result["loop_status"] != "succeeded"
        assert any(
            "citation_missing" in verdict["failure_types"]
            for verdict in result["verdicts"]
        )

    def test_critical_ambiguity_clarifies_without_spending_judge_call(self):
        judge = ScriptedChatModel(
            replies=['{"passes": true, "missing_constraints": [], "reason": "ok"}']
        )
        runner = make_runner(
            ["A polished but unsafe guess."],
            [],
            judge_llm=judge,
            query="Compare it",
        )
        runner.analysis = analyze_query("Compare it", allow_search=True)

        result = runner.run("Compare it")

        assert result["loop_status"] == "clarification_required"
        assert result["verdicts"][-1]["action"] == "clarify"
        assert result["verdicts"][-1]["hard_stop"] is True
        assert judge.calls == 0

    def test_succeeded_when_checklist_empty(self):
        tools = [FakeTools.make("web_search", ["2025年苹果和微软分别公布了财报，苹果相比微软增长更快，而微软同时在云上领先，" * 5])]
        runner = make_runner(
            [_tool_call("web_search"), "2025年苹果相比微软增长更快，而微软同时在云上领先，" * 5],
            tools,
        )
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "succeeded"
        assert result["answer"]
        assert result["iterations"] >= 2
        assert result["trace_events"]

    def test_exhausted_at_max_iterations(self):
        tools = [FakeTools.make("web_search", ["全新证据A", "全新证据B", "全新证据C", "全新证据D"])]
        replies = [_tool_call("web_search", {"query": f"q{i}"}, f"c{i}") for i in range(6)]
        runner = make_runner(replies, tools, max_iterations=3)
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "exhausted"
        assert result["iterations"] == 3

    def test_degraded_synthesis_answers_when_exhausted_with_evidence(self):
        """Phase 1 (M-RC2): exhaust-with-evidence degrades to a grounded answer,
        not an empty 'exhausted' stub. Contrast with the default-off path."""
        tools = [FakeTools.make("web_search", ["苹果相比微软更强 [E1]", "微软同时领先 [E2]"])]
        final_answer = (
            "苹果相比微软更强，而微软同时领先，两者分别发展，"
            "数据对比明显，细节各有侧重。" * 4
        )
        replies = [
            _tool_call("web_search", {"query": "q1"}, "c1"),
            _tool_call("web_search", {"query": "q2"}, "c2"),
            _tool_call("web_search", {"query": "q3"}, "c3"),
            final_answer,
        ]

        # Default flags (off): classic exhaust-and-empty behavior.
        off = make_runner(list(replies), tools, max_iterations=3).run("苹果和微软的区别")
        assert off["loop_status"] == "exhausted"
        assert off["answer"] == "迭代次数用尽，未能获得完整答案。"

        # Phase 1 flags on: degrade to a grounded synthesis instead.
        on = make_runner(
            list(replies),
            tools,
            max_iterations=3,
            termination_config={"coverage_mode": "advisory", "degraded_synthesis": True},
        ).run("苹果和微软的区别")
        degraded_verdicts = [v for v in on["verdicts"] if v.get("reason") == "degraded_synthesis"]
        assert degraded_verdicts, "expected a degraded-synthesis verdict"
        assert degraded_verdicts[-1]["action"] == "synthesize"
        assert "迭代次数用尽" not in on["answer"]
        assert final_answer in on["answer"]

    def test_degraded_synthesis_skipped_without_evidence(self):
        """Phase 1: no degraded synthesis when there is no retained evidence."""
        tools = [FakeTools.make("web_search", [RuntimeError("boom")])]
        replies = [
            _tool_call("web_search", {"query": "q1"}, "c1"),
            _tool_call("web_search", {"query": "q2"}, "c2"),
            "最终答案",
        ]
        result = make_runner(
            replies,
            tools,
            max_iterations=3,
            termination_config={"coverage_mode": "advisory", "degraded_synthesis": True},
        ).run("苹果和微软的区别")
        assert not any(v.get("reason") == "degraded_synthesis" for v in result["verdicts"])
        assert result["loop_status"] in ("unrecoverable", "exhausted", "stagnated")

    def test_stagnated_on_repeated_fingerprint(self):
        tools = [FakeTools.make("web_search", ["相同结果"])]
        replies = [_tool_call("web_search", {"query": "same"}, "c1")] * 5
        runner = make_runner(
            replies,
            tools,
            max_iterations=6,
            termination_config={"repeat_threshold": 2, "no_progress_threshold": 99},
        )
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "stagnated"

    def test_stagnated_on_interleaved_repeated_fingerprint(self):
        tools = [
            FakeTools.make(
                "web_search",
                [f"全新证据 {index} " * 20 for index in range(6)],
            )
        ]
        replies = [
            _tool_call("web_search", {"query": "q1"}, "c1"),
            _tool_call("web_search", {"query": "repeated"}, "c2"),
            _tool_call("web_search", {"query": "q2"}, "c3"),
            _tool_call("web_search", {"query": "repeated"}, "c4"),
            _tool_call("web_search", {"query": "q3"}, "c5"),
            _tool_call("web_search", {"query": "repeated"}, "c6"),
        ]
        runner = make_runner(
            replies,
            tools,
            max_iterations=8,
            termination_config={
                "repeat_threshold": 2,
                "no_progress_threshold": 99,
            },
        )

        result = runner.run("苹果和微软的区别")

        assert result["loop_status"] == "stagnated"
        assert result["iterations"] == 6
        assert result["verdicts"][-1]["rule_hits"][-1]["rule"] == "no_progress"

    def test_stagnated_on_no_new_evidence(self):
        tools = [FakeTools.make("web_search", ["重复的证据内容"])]
        replies = [
            _tool_call("web_search", {"query": "q1"}, "c1"),
            _tool_call("web_search", {"query": "q2"}, "c2"),
            _tool_call("web_search", {"query": "q3"}, "c3"),
            _tool_call("web_search", {"query": "q4"}, "c4"),
        ]
        runner = make_runner(
            replies,
            tools,
            max_iterations=8,
            termination_config={"repeat_threshold": 99, "no_progress_threshold": 2},
        )
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "stagnated"

    def test_unrecoverable_on_tool_errors(self):
        tools = [FakeTools.make("web_search", [RuntimeError("boom"), RuntimeError("boom2")])]
        replies = [
            _tool_call("web_search", {"query": "q1"}, "c1"),
            _tool_call("web_search", {"query": "q2"}, "c2"),
        ]
        runner = make_runner(
            replies,
            tools,
            max_iterations=6,
            termination_config={"tool_error_threshold": 2},
        )
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "unrecoverable"

    def test_final_answer_rejected_then_continue(self):
        """Final proposal with unmet checklist is rejected; loop continues."""
        tools = [FakeTools.make("web_search", ["2025年苹果和微软分别公布了财报，苹果相比微软更强，而微软同时领先，" * 4])]
        replies = [
            "短答案",  # final proposed but checklist unmet -> rejected
            _tool_call("web_search", {"query": "q1"}, "c1"),
            "2025年苹果相比微软更强，而微软同时领先，两者分别发展，" * 5,
        ]
        runner = make_runner(
            replies,
            tools,
            max_iterations=5,
        )
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "succeeded"
        reasons = [v["reason"] for v in result["verdicts"]]
        assert "final_answer_rejected" in reasons

    def test_verdicts_recorded_each_iteration(self):
        tools = [FakeTools.make("web_search", ["2025年苹果和微软分别公布财报，苹果相比微软更强，而微软同时领先，" * 6])]
        runner = make_runner(
            [_tool_call("web_search"), "2025年苹果相比微软更强，而微软同时领先，" * 6],
            tools,
        )
        result = runner.run("苹果和微软的区别")
        assert len(result["verdicts"]) >= 1
        for verdict in result["verdicts"]:
            assert "new_evidence" in verdict
            assert "constraints_met" in verdict
            assert "constraints_missing" in verdict
            assert "should_continue" in verdict
            assert "reason" in verdict


class TestLoopVerdict:
    def test_to_dict(self):
        verdict = LoopVerdict(iteration=1, new_evidence=True, reason="continue")
        data = verdict.to_dict()
        assert data["iteration"] == 1
        assert data["new_evidence"] is True
        assert data["judge_used"] is False


class TestJudgeDegradation:
    def test_empty_tool_rounds_skip_semantic_judge(self):
        tools = [FakeTools.make("web_search", ["证据 A", "证据 B"])]
        judge = ScriptedChatModel(
            replies=['{"passes": false, "missing_constraints": ["answer"]}']
        )
        runner = make_runner(
            [
                _tool_call("web_search", {"query": "q1"}, "c1"),
                _tool_call("web_search", {"query": "q2"}, "c2"),
            ],
            tools,
            max_iterations=2,
            termination_config={"judge_interval": 1},
            judge_llm=judge,
        )

        result = runner.run("苹果和微软的区别")

        assert result["loop_status"] == "exhausted"
        assert judge.calls == 0
        assert all(not verdict["judge_used"] for verdict in result["verdicts"])

    def test_judge_exception_does_not_break_loop(self):
        evidence = "2025年苹果和微软分别公布财报，苹果相比微软更强，而微软同时领先。" * 6
        answer = "2025年苹果相比微软更强，而微软同时领先，两者分别发展。" * 8
        tools = [FakeTools.make("web_search", [evidence])]
        replies = [
            _tool_call("web_search", {"query": "q1"}, "c1"),
            answer,
        ]
        judge = ScriptedChatModel(replies=[RuntimeError("judge down")])
        runner = make_runner(
            replies,
            tools,
            max_iterations=2,
            termination_config={"judge_interval": 1},
            judge_llm=judge,
        )
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "succeeded"
        assert result["judge_error"] == "judge down"
        assert judge.calls == 1

    def test_judge_unparseable_degrades(self):
        evidence = "2025年苹果和微软分别公布财报，苹果相比微软更强，而微软同时领先。" * 6
        answer = "2025年苹果相比微软更强，而微软同时领先，两者分别发展。" * 8
        tools = [FakeTools.make("web_search", [evidence])]
        replies = [
            _tool_call("web_search", {"query": "q1"}, "c1"),
            answer,
        ]
        judge = ScriptedChatModel(replies=["这不是JSON"])
        runner = make_runner(
            replies,
            tools,
            max_iterations=2,
            termination_config={"judge_interval": 1},
            judge_llm=judge,
        )
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "succeeded"
        assert result["judge_error"] == "judge_unparseable_response"
        assert judge.calls == 1

    def test_judge_pass_cannot_override_deterministic_missing_constraint(self):
        tools = [FakeTools.make("web_search", ["全新证据A"])]
        replies = [
            _tool_call("web_search", {"query": "q1"}, "c1"),
            "完整答案但没有对比标记也没有长度" ,
        ]
        judge = ScriptedChatModel(
            replies=['{"passes": true, "missing_constraints": [], "reason": "ok"}']
        )
        runner = make_runner(
            replies,
            tools,
            max_iterations=4,
            termination_config={"judge_interval": 1},
            judge_llm=judge,
        )
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "stagnated"
        assert "comparison" in result["verdicts"][-1]["constraints_missing"]
        assert result["verdicts"][-1]["deterministic_pass"] is False


class TestShimMode:
    def test_exhausted_draft_is_explicitly_qualified(self):
        answer = ReactLoopGraphRunner._best_effort_answer(
            "当前候选答案。",
            "exhausted",
        )
        assert "证据或执行预算不足" in answer
        assert answer.endswith("当前候选答案。")

    def test_json_shim_tool_call_and_final(self):
        """Models without bind_tools use the JSON shim protocol."""
        tools = [FakeTools.make("web_search", ["2025年苹果和微软分别公布财报，苹果相比微软更强，而微软同时领先，" * 6])]
        replies = [
            '{"action": "tool", "tool": "web_search", "args": {"query": "苹果 微软 区别"}}',
            '{"action": "final", "answer": "2025年苹果相比微软更强，而微软同时领先，两者分别发展，'
            + "数据对比明显，" * 16
            + '"}',
        ]
        runner = ReactLoopGraphRunner(
            llm=ScriptedChatModel(replies=replies),
            tools=tools,
            max_iterations=5,
            query="苹果和微软的区别",
        )
        assert not runner._use_native_tools
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "succeeded"
        assert "苹果" in result["answer"]

    def test_shim_unknown_tool_treated_as_text(self):
        replies = ['{"action": "tool", "tool": "no_such_tool", "args": {}}']
        runner = ReactLoopGraphRunner(
            llm=ScriptedChatModel(replies=replies),
            tools=[FakeTools.make("web_search", ["x"])],
            max_iterations=2,
            query="苹果和微软的区别",
        )
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "stagnated"

    def test_native_fallback_accepts_direct_json_tool_action(self):
        tools = [
            FakeTools.make(
                "web_search",
                ["苹果和微软分别提供了可核验信息，苹果相比微软更强，而微软同时领先。" * 6],
            )
        ]
        answer = "苹果和微软分别有公开信息，苹果相比微软更强，而微软同时领先。" * 8
        runner = make_runner(
            [
                '{"action": "web_search", "query": "Apple Microsoft pricing"}',
                answer,
            ],
            tools,
        )

        result = runner.run("苹果和微软的区别")

        assert result["loop_status"] == "succeeded"
        assert result["answer"] == answer

    def test_native_fallback_accepts_smart_quote_json_tool_action(self):
        tools = [
            FakeTools.make(
                "web_search",
                ["苹果和微软分别提供了可核验信息，苹果相比微软更强，而微软同时领先。" * 6],
            )
        ]
        answer = "苹果和微软分别有公开信息，苹果相比微软更强，而微软同时领先。" * 8
        runner = make_runner(
            [
                "{“action”: “web_search”, “query”: “Apple Microsoft pricing”}",
                answer,
            ],
            tools,
        )

        result = runner.run("苹果和微软的区别")

        assert result["loop_status"] == "succeeded"
        assert result["answer"] == answer

    def test_invalid_json_tool_arguments_stagnate_without_leaking_markup(self):
        raw = '{"action": "web_search", "query": {}}'
        runner = make_runner(
            [raw],
            [FakeTools.make("web_search", ["unused"])],
            max_iterations=8,
        )

        result = runner.run("苹果和微软的区别")

        assert result["loop_status"] == "stagnated"
        assert result["iterations"] == 2
        assert raw not in result["answer"]

    def test_structured_action_draft_is_never_returned_by_best_effort(self):
        raw = '{"action": "web_search", "query": "pricing"}'

        answer = ReactLoopGraphRunner._best_effort_answer(raw, "exhausted")

        assert raw not in answer
        assert answer == "迭代次数用尽，未能获得完整答案。"

    def test_final_answer_wrapper_is_unwrapped(self):
        from orchestrators.react_loop_graph import _unwrap_final_answer

        leaked = '{"action": "final", "answer": "要看清实际区别 [E1]"}'
        assert _unwrap_final_answer(leaked) == "要看清实际区别 [E1]"
        # Broken inner quotes (strict JSON fails) still recover the answer.
        broken = '{"action": "final", "answer": "a"b"c [E2]"}'
        assert _unwrap_final_answer(broken) == 'a"b"c [E2]'
        # Plain text without a wrapper is left untouched (returns None).
        assert _unwrap_final_answer("苹果相比微软更强 [E1]") is None

    def test_xml_function_markup_is_normalized_into_tool_call(self):
        tools = [
            FakeTools.make(
                "web_search",
                ["2025年苹果和微软分别公布财报，苹果相比微软更强，而微软同时领先，" * 6],
            )
        ]
        tracer = WorkflowTracer()
        final_answer = "2025年苹果相比微软更强，而微软同时领先，两者分别发展，" + "数据对比明显，" * 16
        runner = ReactLoopGraphRunner(
            llm=ScriptedChatModel(
                replies=[
                    "<function>web_search</function><query>Apple Microsoft pricing</query>",
                    '{"action": "final", "answer": "' + final_answer + '"}',
                ]
            ),
            tools=tools,
            max_iterations=4,
            query="苹果和微软的区别",
            tracer=tracer,
        )

        result = runner.run("苹果和微软的区别")

        assert result["loop_status"] == "succeeded"
        assert "<function>" not in result["answer"]
        tool_event = next(
            event
            for event in tracer.events
            if event["id"] == "react_tool_1_1" and event["status"] == "done"
        )
        assert {item["label"]: item["value"] for item in tool_event["items"]}["查询"] == "Apple Microsoft pricing"

    def test_malformed_function_markup_is_not_returned_as_answer(self):
        tracer = WorkflowTracer()
        runner = ReactLoopGraphRunner(
            llm=ScriptedChatModel(replies=["<function>web_search</function>"]),
            tools=[FakeTools.make("web_search", ["unused"])],
            max_iterations=1,
            query="苹果和微软的区别",
            tracer=tracer,
        )

        result = runner.run("苹果和微软的区别")

        assert result["loop_status"] == "exhausted"
        assert "<function>" not in result["answer"]
        assert result["verdicts"][-1]["reason"] == "invalid_tool_request"
        invalid_event = next(
            event
            for event in tracer.events
            if event["id"] == "react_invalid_tool_1" and event["status"] == "error"
        )
        assert invalid_event["status"] == "error"

    def test_process_narration_is_not_returned_as_answer(self):
        tracer = WorkflowTracer()
        narration = "让我直接查看智谱官方定价文档，然后获取具体价格数据。"
        runner = make_runner(
            [narration],
            [],
            max_iterations=1,
            tracer=tracer,
        )

        result = runner.run("苹果和微软的区别")

        assert result["loop_status"] == "exhausted"
        assert result["answer"] == "迭代次数用尽，未能获得完整答案。"
        assert narration not in result["answer"]
        assert result["verdicts"][-1]["reason"] == "process_narration"
        invalid_event = next(
            event
            for event in tracer.events
            if event["id"] == "react_invalid_final_1" and event["status"] == "error"
        )
        assert "智谱官方" not in str(invalid_event)

    def test_process_narration_retries_for_a_direct_answer(self):
        narration = "好的，我明白。我会继续检索并搜索官方定价资料。"
        answer = "这是面向用户的最终答案，包含已验证的价格信息。"
        runner = make_runner([narration, answer], [], max_iterations=2, query="简单问题")

        result = runner.run("简单问题")

        assert result["loop_status"] == "succeeded"
        assert result["answer"] == answer
        assert [verdict["reason"] for verdict in result["verdicts"]] == [
            "process_narration",
            "constraints_satisfied",
        ]

    def test_invalid_tool_nudge_names_reason_and_available_tools(self):
        model = RecordingScriptedChatModel(
            replies=[
                '{"action": "tool", "tool": "brave_search", "args": {"query": "q"}}',
                "这是面向用户的最终答案，包含已验证的信息。",
            ]
        )
        runner = ReactLoopGraphRunner(
            llm=model,
            tools=[FakeTools.make("web_search", ["unused"])],
            max_iterations=2,
            query="简单问题",
        )

        result = runner.run("简单问题")

        assert result["loop_status"] == "succeeded"
        second_call_text = "\n".join(str(content) for content in model.seen[1])
        assert "unsupported_tool: brave_search" in second_call_text
        assert "web_search" in second_call_text
        assert "结构化调用" not in second_call_text

    def test_process_narration_nudge_demands_tool_call_or_answer(self):
        model = RecordingScriptedChatModel(
            replies=[
                "让我直接查看智谱官方定价文档，然后获取具体价格数据。",
                "这是面向用户的最终答案，包含已验证的信息。",
            ]
        )
        runner = ReactLoopGraphRunner(
            llm=model,
            tools=[FakeTools.make("web_search", ["unused"])],
            max_iterations=2,
            query="简单问题",
        )

        result = runner.run("简单问题")

        assert result["loop_status"] == "succeeded"
        second_call_text = "\n".join(str(content) for content in model.seen[1])
        assert "不要描述你准备做什么" in second_call_text
        assert "结构化调用" not in second_call_text


class TestWorkflowTrace:
    def test_search_api_records_are_emitted_under_the_react_tool_call(self):
        class SearchTool:
            name = "web_search"
            description = "test search"

            def invoke(self, args):
                return "1. Pricing\n   URL: https://example.com/pricing\n   Pricing evidence"

            def get_last_search_api_calls(self):
                return [
                    {
                        "source": "brave",
                        "label": "Brave Search",
                        "query": "pricing token=hidden",
                        "duration_ms": 8.0,
                        "status": "done",
                        "result_count": 1,
                        "results": [
                            {
                                "title": "Pricing",
                                "url": "https://example.com/pricing?token=hidden",
                                "snippet": "Pricing evidence",
                            }
                        ],
                    }
                ]

        tracer = WorkflowTracer()
        runner = make_runner(
            [
                _tool_call("web_search", {"query": "pricing"}),
                "苹果和微软的公开价格信息已经分别覆盖，并完成了直接对比。" * 6,
            ],
            [SearchTool()],
            tracer=tracer,
        )

        runner.run("苹果和微软的区别")

        event = next(
            event
            for event in tracer.events
            if event["id"] == "react_search_api_1_1_1" and event["status"] == "done"
        )
        assert event["record_kind"] == "search_results"
        assert event["records"][0]["url"] == "https://example.com/pricing"
        assert "hidden" not in str(event)

    def test_trace_projection_is_bounded_and_keeps_terminal_events(self):
        tracer = WorkflowTracer()
        runner = make_runner(["unused"], [], tracer=tracer)
        for index in range(25):
            step_id = f"react_tool_1_{index}"
            tracer.begin(step_id, "工具调用")
            tracer.end(step_id, detail=f"完成 {index}")

        events, truncated = runner._trace_events()

        assert truncated is True
        assert len(events) == 40
        assert events[0]["id"] == "react_tool_1_0"
        assert events[-1]["detail"] == "完成 24"

    def test_iteration_tool_and_verdict_events_are_safe_and_ordered(self):
        tracer = WorkflowTracer()
        evidence = (
            "1. 2025年苹果和微软分别公布财报，苹果相比微软更强，而微软同时领先。\n\n"
            "2. 两家公司的公开数据对比完整。"
        )
        runner = make_runner(
            [
                _tool_call("web_search", {"query": "Apple Microsoft pricing"}),
                "2025年苹果相比微软更强，而微软同时领先，" * 6,
            ],
            [FakeTools.make("web_search", [evidence])],
            tracer=tracer,
        )

        result = runner.run("苹果和微软的区别")

        assert result["loop_status"] == "succeeded"
        ids = [event["id"] for event in tracer.events]
        assert ids.index("react_iteration_1") < ids.index("react_tool_1_1")
        assert ids.index("react_tool_1_1") < ids.index("react_evaluate_1")

        tool_event = next(
            event
            for event in tracer.events
            if event["id"] == "react_tool_1_1" and event["status"] == "done"
        )
        assert tool_event["detail"] == "完成 · 返回 2 条结果"
        tool_items = {item["label"]: item["value"] for item in tool_event["items"]}
        assert tool_items == {"查询": "Apple Microsoft pricing", "结果": "2 条"}

        verdict_event = next(
            event
            for event in tracer.events
            if event["id"] == "react_evaluate_1" and event["status"] == "done"
        )
        verdict_items = {item["label"]: item["value"] for item in verdict_event["items"]}
        assert verdict_items["判定"] == "继续检索"
        assert verdict_items["新证据"] == "是"
        iteration_event = next(
            event
            for event in tracer.events
            if event["id"] == "react_iteration_1" and event["status"] == "done"
        )
        assert iteration_event["detail"] == "本轮完成"
        assert "items" not in iteration_event
        final_verdict_event = next(
            event
            for event in tracer.events
            if event["id"] == "react_evaluate_2" and event["status"] == "done"
        )
        final_verdict_items = {item["label"]: item["value"] for item in final_verdict_event["items"]}
        assert final_verdict_items["新证据"] == "否"
        assert final_verdict_items["动作"] == "return"
        assert final_verdict_items["确定性 critic"] == "通过"
        assert final_verdict_items["规则命中"] == "constraints_satisfied"
        assert result["trace_events"]
        assert result["trace_truncated"] is False

    def test_textual_tool_error_is_traced_and_redacted(self):
        tracer = WorkflowTracer()
        runner = make_runner(
            [
                _tool_call("web_search", {"query": "first"}, "c1"),
                _tool_call("web_search", {"query": "second"}, "c2"),
            ],
            [
                FakeTools.make(
                    "web_search",
                    [
                        "Search failed: token=top-secret https://example.com/search?token=top-secret",
                        "Search failed: token=top-secret https://example.com/search?token=top-secret",
                    ],
                )
            ],
            max_iterations=3,
            termination_config={"tool_error_threshold": 2},
            tracer=tracer,
        )

        result = runner.run("苹果和微软的区别")

        assert result["loop_status"] == "unrecoverable"
        failed_event = next(
            event
            for event in tracer.events
            if event["id"] == "react_tool_1_1" and event["status"] == "error"
        )
        serialized = str(failed_event)
        assert "top-secret" not in serialized
        assert "?token" not in serialized
        assert {item["label"]: item["value"] for item in failed_event["items"]}["结果"] == "调用失败"


class TestConfig:
    def test_loop_llm_and_search_calls_enter_shared_timing_recorder(self):
        recorder = TimingRecorder(enabled=True)
        recorder.start()
        runner = make_runner(
            [_tool_call("web_search"), "最终答案"],
            [FakeTools.make("web_search", ["有效证据"])],
            query="简单问题",
            timing_recorder=recorder,
        )

        result = runner.run("简单问题")
        payload = recorder.to_dict()

        assert result["loop_status"] == "succeeded"
        assert [call["label"] for call in payload["llm_calls"]] == [
            "loop_act",
            "loop_act",
        ]
        assert payload["tool_calls"][0]["tool"] == "web_search"

    def test_normalize_defaults(self):
        cfg = normalize_termination_config(None)
        assert cfg["judge_interval"] == 2
        assert cfg["repeat_threshold"] == 2

    def test_normalize_overrides(self):
        cfg = normalize_termination_config({"judge_interval": 3, "new_evidence_min_ratio": 0.2})
        assert cfg["judge_interval"] == 3
        assert cfg["new_evidence_min_ratio"] == 0.2
        assert cfg["repeat_threshold"] == 2

    def test_normalize_bad_values_ignored(self):
        cfg = normalize_termination_config({"judge_interval": "abc"})
        assert cfg["judge_interval"] == 2


class TestDsmlMarkupNormalization:
    """DeepSeek emits tool calls as ``<｜｜DSML｜｜...>`` text markup; the loop
    must normalize it back into structured tool calls or execution is skipped."""

    @staticmethod
    def _runner() -> "ReactLoopGraphRunner":
        return ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(replies=["unused"]),
            tools=[FakeTools.make("web_search", ["result"])],
            query="对比 A 和 B",
        )

    def test_single_invoke_becomes_tool_call(self) -> None:
        runner = self._runner()
        dsml = (
            "<｜｜DSML｜｜tool_calls>"
            '<｜｜DSML｜｜invoke name="web_search">'
            '<｜｜DSML｜｜parameter name="query" string="true">GLM-5.2 price</｜｜DSML｜｜parameter>'
            "</｜｜DSML｜｜invoke>"
            "</｜｜DSML｜｜tool_calls>"
        )
        response, error = runner._normalize_function_markup(AIMessage(content=dsml))
        assert error is None
        calls = getattr(response, "tool_calls", None)
        assert calls and len(calls) == 1
        assert calls[0]["name"] == "web_search"
        assert calls[0]["args"] == {"query": "GLM-5.2 price"}

    def test_multiple_invokes_all_become_calls(self) -> None:
        runner = self._runner()
        dsml = (
            "<｜｜DSML｜｜tool_calls>"
            '<｜｜DSML｜｜invoke name="web_search">'
            '<｜｜DSML｜｜parameter name="query" string="true">GLM-5.2 price</｜｜DSML｜｜parameter>'
            "</｜｜DSML｜｜invoke>"
            '<｜｜DSML｜｜invoke name="web_search">'
            '<｜｜DSML｜｜parameter name="query" string="true">K2.7 price</｜｜DSML｜｜parameter>'
            "</｜｜DSML｜｜invoke>"
            "</｜｜DSML｜｜tool_calls>"
        )
        response, error = runner._normalize_function_markup(AIMessage(content=dsml))
        assert error is None
        assert [c["args"]["query"] for c in response.tool_calls] == [
            "GLM-5.2 price",
            "K2.7 price",
        ]

    def test_prefix_disclaimer_does_not_block_parsing(self) -> None:
        # The harness prefixes a disclaimer when surfacing candidate answers;
        # DSML may appear after such prose and must still be parsed.
        runner = self._runner()
        prefix = "现有证据或执行预算不足，以下仅为当前候选信息：\n"
        dsml = (
            prefix
            + '<｜｜DSML｜｜invoke name="web_search">'
            '<｜｜DSML｜｜parameter name="query" string="true">q</｜｜DSML｜｜parameter>'
            "</｜｜DSML｜｜invoke>"
        )
        response, error = runner._normalize_function_markup(AIMessage(content=dsml))
        assert error is None
        assert response.tool_calls[0]["name"] == "web_search"

    def test_unsupported_tool_is_flagged(self) -> None:
        runner = self._runner()
        dsml = (
            '<｜｜DSML｜｜invoke name="nonexistent_tool">'
            '<｜｜DSML｜｜parameter name="query" string="true">q</｜｜DSML｜｜parameter>'
            "</｜｜DSML｜｜invoke>"
        )
        response, error = runner._normalize_function_markup(AIMessage(content=dsml))
        assert getattr(response, "tool_calls", None) in (None, [])
        assert error and "unsupported_tool" in error
