"""Regression tests for change ``fix-loop-retrieval-gating-and-synthesis``.

Covers the five product defects registered by the 0918 quality run
(``docs/reports/quality_evaluation_20260918/report.md`` §9):

- QD-20260918-01 ``force_search`` is enforced by the critic
- QD-20260918-02 tool-free rounds do not read as retrieval stagnation and the
  candidate answer survives a stagnated close
- QD-20260918-03 forced synthesis with evidence produces a grounded answer,
  and a terminal stub is never accepted as success
- QD-20260918-04 the qualification prefix is added exactly once
- QD-20260918-05 relative pronouns / named entities do not trigger the
  comparison clarification

All tests use scripted fake chat models and fake tools; no real LLM or search.
"""

from typing import Any, Dict, List, Optional

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool as lc_tool

from orchestrators.react_agent_orchestrator import ReactAgentOrchestrator
from orchestrators.react_loop_graph import (
    ReactLoopGraphRunner,
    _STUB_ANSWERS,
    langgraph_available,
)
from utils.query_orchestration import (
    QueryAnalysis,
    TerminationAction,
    TerminationContext,
    analyze_query,
    evaluate_termination,
    normalize_termination_config,
)

pytestmark = pytest.mark.skipif(not langgraph_available(), reason="langgraph not installed")


class ScriptedChatModel(BaseChatModel):
    replies: List[Any]
    calls: int = 0
    seen: List[List[str]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append([str(getattr(message, "content", "")) for message in messages])
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        if isinstance(reply, AIMessage):
            message = reply.model_copy(deep=True)
            message.id = f"fake-{self.calls}-{id(message)}"
        else:
            message = AIMessage(content=str(reply))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools, **kwargs):
        return self


def _tool_call(name: str, args: Optional[Dict[str, Any]] = None, call_id: str = "c1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args or {"query": "q"}, "id": call_id, "type": "tool_call"}],
    )


def _fake_tool(name: str, outputs: List[str]):
    state = {"calls": 0}

    @lc_tool(name)
    def fake_tool(query: str) -> str:
        """Fake tool. Args: query."""
        idx = min(state["calls"], len(outputs) - 1)
        state["calls"] += 1
        return outputs[idx]

    fake_tool.name = name
    return fake_tool


class EvidenceTool:
    """Fake search tool that registers ledger-style records with ``eid``s.

    Plain ``lc_tool`` fakes carry no ``metadata.eid``, so an answer citing
    ``[E1]`` would fail the mechanical citation check as unresolved. This
    fake mirrors what the production search tools expose.
    """

    def __init__(self, name: str, content: str, *, tier: str = "official"):
        self.name = name
        self.description = "fake evidence tool"
        self._content = content
        self._tier = tier
        self.calls = 0

    def invoke(self, args):  # noqa: D401 - tool protocol
        self.calls += 1
        return self._content

    def get_last_evidence_records(self):
        return [
            {
                "content": self._content,
                "reference": f"https://example.org/{self.name}/{self.calls}",
                "title": "fake",
                "source_type": "web",
                "source_tier": self._tier,
                "metadata": {"eid": 1},
            }
        ]


def _runner(replies, tools, **kwargs) -> ReactLoopGraphRunner:
    model = ScriptedChatModel(replies=replies)
    model.seen = []
    kwargs.setdefault("max_iterations", 6)
    return ReactLoopGraphRunner(llm=model, tools=tools, **kwargs)


BOILING_QUERY = "水在标准大气压下的沸点是多少摄氏度？"
BOILING_DIRECT = "在标准大气压下，纯水的沸点是 100 摄氏度。"
BOILING_EVIDENCE = "标准大气压（101.325 kPa）下水的沸点为 100 °C，即 212 °F。" * 4
BOILING_CITED = "在标准大气压下，纯水的沸点是 100 摄氏度 [E1]。"
ZH_PREFIX = "现有证据或执行预算不足，以下仅为当前候选信息，可能不完整或不准确：\n"


# ---------------------------------------------------------------------------
# QD-20260918-04: qualification prefix is idempotent
# ---------------------------------------------------------------------------
class TestBestEffortPrefix:
    @pytest.mark.parametrize(
        "draft",
        ["当前候选答案。", "The provisional candidate answer."],
    )
    def test_prefix_is_added_exactly_once(self, draft: str) -> None:
        once = ReactLoopGraphRunner._best_effort_answer(draft, "exhausted")
        twice = ReactLoopGraphRunner._best_effort_answer(once, "evidence_insufficient")
        assert once == twice
        assert once.endswith(draft)
        assert once.count("不足") + once.count("insufficient") == 1

    def test_authority_prefix_replaces_budget_prefix(self) -> None:
        budget = ReactLoopGraphRunner._best_effort_answer("候选。", "exhausted")
        authority = ReactLoopGraphRunner._best_effort_answer(budget, "authority_unverified")
        assert authority.startswith("已获取与目标实体相关的页面")
        assert "现有证据或执行预算不足" not in authority
        assert authority.endswith("候选。")

    def test_stub_draft_is_returned_as_stub_without_prefix(self) -> None:
        stub = _STUB_ANSWERS["exhausted"]
        assert ReactLoopGraphRunner._best_effort_answer(stub, "stagnated") == _STUB_ANSWERS["stagnated"]
        assert ReactLoopGraphRunner._best_effort_answer(ZH_PREFIX + stub, "exhausted") == stub


# ---------------------------------------------------------------------------
# QD-20260918-02: tool-free rounds and candidate preservation
# ---------------------------------------------------------------------------
class TestToolFreeRounds:
    def test_rejection_without_evidence_names_search_tool(self) -> None:
        tools = [EvidenceTool("web_search", BOILING_EVIDENCE)]
        runner = _runner(
            [BOILING_DIRECT, _tool_call("web_search", {"query": "水 沸点"}), BOILING_CITED],
            tools,
            query=BOILING_QUERY,
            analysis=analyze_query(BOILING_QUERY, allow_search=True),
        )
        result = runner.run(BOILING_QUERY)

        assert result["loop_status"] == "succeeded"
        assert result["answer"] == BOILING_CITED
        assert result["verdicts"][0]["reason"] == "final_answer_rejected"
        # The feedback the model saw on its second call names the tool and
        # no longer tells it to skip retrieval.
        feedback = runner.llm.seen[1][-1]
        assert "web_search" in feedback
        assert "不要再调用检索工具" not in feedback

    def test_direct_answer_then_narration_still_reaches_search(self) -> None:
        tools = [EvidenceTool("web_search", BOILING_EVIDENCE)]
        runner = _runner(
            [
                BOILING_DIRECT,
                "我需要先搜索一下相关资料，让我查询权威来源。",
                _tool_call("web_search", {"query": "水 沸点"}),
                BOILING_CITED,
            ],
            tools,
            query=BOILING_QUERY,
            analysis=analyze_query(BOILING_QUERY, allow_search=True),
        )
        result = runner.run(BOILING_QUERY)

        assert result["loop_status"] == "succeeded"
        reasons = [verdict["reason"] for verdict in result["verdicts"]]
        assert reasons[:2] == ["final_answer_rejected", "process_narration"]
        assert "stagnated" not in reasons

    def test_three_tool_free_rounds_stagnate_but_keep_candidate(self) -> None:
        tools = [_fake_tool("web_search", [BOILING_EVIDENCE])]
        runner = _runner(
            [BOILING_DIRECT],
            tools,
            query=BOILING_QUERY,
            analysis=analyze_query(BOILING_QUERY, allow_search=True),
        )
        result = runner.run(BOILING_QUERY)

        assert result["loop_status"] == "stagnated"
        assert result["iterations"] == normalize_termination_config(None)["tool_free_threshold"]
        assert result["answer"] == ZH_PREFIX + BOILING_DIRECT
        assert result["answer"].count(ZH_PREFIX) == 1

    def test_narration_after_rejection_keeps_first_candidate(self) -> None:
        tools = [_fake_tool("web_search", [BOILING_EVIDENCE])]
        runner = _runner(
            [BOILING_DIRECT, "我需要搜索一下，让我查询相关资料。"],
            tools,
            query=BOILING_QUERY,
            analysis=analyze_query(BOILING_QUERY, allow_search=True),
        )
        result = runner.run(BOILING_QUERY)

        assert result["loop_status"] == "stagnated"
        # The narration rounds blank the latest AI message; the first-round
        # candidate is recovered instead of the bare stagnation stub.
        assert result["answer"] == ZH_PREFIX + BOILING_DIRECT

    def test_tool_free_threshold_is_configurable(self) -> None:
        runner = _runner(
            [BOILING_DIRECT],
            [_fake_tool("web_search", [BOILING_EVIDENCE])],
            query=BOILING_QUERY,
            analysis=analyze_query(BOILING_QUERY, allow_search=True),
            termination_config={"tool_free_threshold": 2},
        )
        result = runner.run(BOILING_QUERY)
        assert result["loop_status"] == "stagnated"
        assert result["iterations"] == 2

    def test_retrieval_no_progress_still_stagnates_after_threshold(self) -> None:
        tools = [_fake_tool("web_search", ["重复的证据内容"])]
        runner = _runner(
            [
                _tool_call("web_search", {"query": "q1"}, "c1"),
                _tool_call("web_search", {"query": "q2"}, "c2"),
                _tool_call("web_search", {"query": "q3"}, "c3"),
                _tool_call("web_search", {"query": "q4"}, "c4"),
            ],
            tools,
            max_iterations=8,
            query="苹果和微软的区别",
            termination_config={"repeat_threshold": 99, "no_progress_threshold": 2},
        )
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "stagnated"

    def test_critic_counts_tool_free_streak_separately(self) -> None:
        base = dict(
            phase="loop",
            final_proposed=True,
            answer="draft",
            constraints_missing=["comparison"],
            no_progress_threshold=2,
            tool_free_threshold=3,
        )
        below = evaluate_termination(TerminationContext(**base, no_progress_streak=0, tool_free_streak=2))
        assert below.action == TerminationAction.CONTINUE
        at = evaluate_termination(TerminationContext(**base, no_progress_streak=0, tool_free_streak=3))
        assert at.action == TerminationAction.STAGNATED
        assert any("tool-free" in hit["detail"] for hit in at.rule_hits if hit["rule"] == "no_progress")


# ---------------------------------------------------------------------------
# QD-20260918-03: forced synthesis is grounded; stubs are never "succeeded"
# ---------------------------------------------------------------------------
class TestForcedSynthesis:
    def test_late_loop_force_synthesis_generates_grounded_answer(self) -> None:
        evidence = "已安装插件包括 pluginA 与 pluginB，两者均为官方维护。" * 4
        grounded = "目前有 pluginA 和 pluginB 两个插件 [E1]。"
        tools = [EvidenceTool("web_search", evidence)]
        runner = _runner(
            [
                _tool_call("web_search", {"query": "插件 1"}, "c1"),
                _tool_call("web_search", {"query": "插件 2"}, "c2"),
                _tool_call("web_search", {"query": "插件 3"}, "c3"),
                grounded,
            ],
            tools,
            max_iterations=5,
            query="有哪些插件",
            analysis=QueryAnalysis(query="有哪些插件", existence_query=True, requires_evidence=True),
        )
        result = runner.run("有哪些插件")

        reasons = [verdict["reason"] for verdict in result["verdicts"]]
        assert "forced_synthesis" in reasons
        assert result["loop_status"] == "succeeded"
        assert result["answer"] == grounded
        assert result["answer"] not in _STUB_ANSWERS.values()
        synth_events = [
            event for event in runner.tracer.events
            if event.get("id", "").startswith("react_iteration") and "综合" in str(event.get("detail") or "")
        ]
        assert synth_events, "synthesis should be traced as a grounded synthesis"

    def test_stub_draft_is_not_accepted_as_success(self) -> None:
        runner = _runner(["unused"], [_fake_tool("web_search", ["x"])], query="有哪些插件",
                         analysis=QueryAnalysis(query="有哪些插件", existence_query=True, requires_evidence=True))
        state = runner._build_initial_state("有哪些插件")
        state.update(
            {
                "iteration": 6,
                "phase": "synthesis",
                "synthesis_attempts": 2,
                "final_proposed": True,
                "forced_synthesis": True,
                "had_successful_observation": True,
                "last_round_new_evidence": True,
                "evidence_pool": ["x"],
                "evidence_records": [{"content": "x", "source_tier": "unknown", "metadata": {}}],
                "messages": [HumanMessage(content="q"), AIMessage(content=_STUB_ANSWERS["exhausted"])],
            }
        )
        update = runner._evaluate(state)
        assert update["termination_reason"] != "succeeded"
        assert "answer" in update["verdicts"][-1]["constraints_missing"]


# ---------------------------------------------------------------------------
# QD-20260918-01: force_search is enforced by the critic
# ---------------------------------------------------------------------------
INTRO_QUERY = "介绍一下 LangGraph 是什么"
INTRO_DIRECT = "LangGraph 是一个用于构建有状态多智能体应用的框架。"
INTRO_EVIDENCE = "LangGraph 是 LangChain 团队推出的用于构建有状态智能体工作流的库。" * 4
INTRO_CITED = "LangGraph 是 LangChain 团队推出的有状态智能体工作流库 [E1]。"


class TestForceSearch:
    def test_direct_answer_is_accepted_without_force_search(self) -> None:
        runner = _runner([INTRO_DIRECT], [_fake_tool("web_search", [INTRO_EVIDENCE])], query=INTRO_QUERY)
        result = runner.run(INTRO_QUERY)
        assert result["loop_status"] == "succeeded"
        assert result["iterations"] == 1
        assert result["search_calls"] == 0
        assert result["force_search"] is False

    def test_force_search_rejects_direct_answer_until_a_search_is_made(self) -> None:
        runner = _runner(
            [INTRO_DIRECT, _tool_call("web_search", {"query": "LangGraph"}), INTRO_CITED],
            [EvidenceTool("web_search", INTRO_EVIDENCE)],
            query=INTRO_QUERY,
            force_search=True,
        )
        result = runner.run(INTRO_QUERY)

        assert result["loop_status"] == "succeeded"
        assert result["answer"] == INTRO_CITED
        assert result["search_calls"] == 1
        first = result["verdicts"][0]
        assert first["reason"] == "final_answer_rejected"
        assert "search_required" in first["failure_types"]
        assert any(hit["rule"] == "search_required" for hit in first["rule_hits"])
        assert "search_required" not in result["verdicts"][-1]["failure_types"]
        assert "web_search" in runner.llm.seen[1][-1]
        assert "先检索再作答" in runner.system_prompt

    def test_force_search_is_bounded_by_tool_free_threshold(self) -> None:
        runner = _runner(
            [INTRO_DIRECT],
            [_fake_tool("web_search", [INTRO_EVIDENCE])],
            query=INTRO_QUERY,
            force_search=True,
        )
        result = runner.run(INTRO_QUERY)
        assert result["loop_status"] == "stagnated"
        assert result["iterations"] == 3
        assert result["answer"].endswith(INTRO_DIRECT)

    def test_force_search_ignored_without_search_tool(self) -> None:
        runner = _runner([INTRO_DIRECT], [_fake_tool("local_docs", ["x"])], query=INTRO_QUERY, force_search=True)
        assert runner.force_search is False
        assert "先检索再作答" not in runner.system_prompt
        result = runner.run(INTRO_QUERY)
        assert result["loop_status"] == "succeeded"

    def test_adapter_forwards_and_echoes_force_search(self) -> None:
        llm = ScriptedChatModel(
            replies=[INTRO_DIRECT, _tool_call("web_search", {"query": "LangGraph"}), INTRO_CITED]
        )
        llm.seen = []
        orchestrator = ReactAgentOrchestrator(
            llm=llm,
            tools=[EvidenceTool("web_search", INTRO_EVIDENCE)],
            max_iterations=4,
            config={},
        )
        result = orchestrator.answer(INTRO_QUERY, force_search=True)
        control = result["control"]
        assert control["request_parameters"]["force_search"] is True
        assert control["loop_search_calls"] == 1
        assert control["loop_status"] == "succeeded"

    def test_adapter_drops_force_search_when_search_disallowed(self) -> None:
        llm = ScriptedChatModel(replies=[INTRO_DIRECT])
        llm.seen = []
        orchestrator = ReactAgentOrchestrator(
            llm=llm,
            tools=[_fake_tool("web_search", [INTRO_EVIDENCE])],
            max_iterations=4,
            config={},
        )
        result = orchestrator.answer(INTRO_QUERY, force_search=True, allow_search=False)
        control = result["control"]
        assert control["request_parameters"]["force_search"] is False
        assert control["loop_search_calls"] == 0
        assert control["loop_status"] == "succeeded"


# ---------------------------------------------------------------------------
# QD-20260918-05: reference ambiguity
# ---------------------------------------------------------------------------
class TestReferenceAmbiguity:
    @pytest.mark.parametrize(
        "query",
        [
            "Explain how feature flags should be architected for a system that deploys 20+ times a day, including the failure modes to guard against.",
            "GLM-5.2 的价格和它的上下文长度",
            "Kubernetes 这个名字是什么意思？",
            "How should this model be deployed in production?",
        ],
    )
    def test_named_or_relative_references_are_not_ambiguous(self, query: str) -> None:
        analysis = analyze_query(query, allow_search=True)
        assert analysis.critical_ambiguity is False
        assert "unresolved_entity_reference" not in analysis.ambiguities

    @pytest.mark.parametrize(
        ("query", "ambiguity"),
        [
            ("Compare it with the other one", "comparison_members_unresolved"),
            ("它的价格是多少", "unresolved_entity_reference"),
            ("这个模型支持多模态吗", "unresolved_entity_reference"),
            ("What is that?", "unresolved_entity_reference"),
            ("帮我对比一下", "comparison_members_unresolved"),
            ("它和 Redis 的区别是什么？", "comparison_members_unresolved"),
        ],
    )
    def test_genuine_references_stay_ambiguous(self, query: str, ambiguity: str) -> None:
        analysis = analyze_query(query, allow_search=True)
        assert analysis.critical_ambiguity is True
        assert ambiguity in analysis.ambiguities

    def test_clarification_wording_matches_ambiguity_type(self) -> None:
        from langchain.langchain_orchestrator import LangChainOrchestrator

        class LLMStub:
            provider = "stub"
            model_name = "stub"

            def invoke(self, *args: Any, **kwargs: Any) -> Any:
                return type("Response", (), {"content": "unused"})()

        orchestrator = LangChainOrchestrator(llm=LLMStub(), config={"GOOGLE_API_KEY": "test-key"})

        pronoun = orchestrator.answer("Where is it displayed?")
        assert pronoun["control"]["decision"]["reason"] == "clarification_required"
        assert "compar" not in pronoun["answer"].casefold()
        assert "entity" in pronoun["answer"].casefold()

        comparison = orchestrator.answer("Compare these two")
        assert comparison["control"]["decision"]["reason"] == "clarification_required"
        assert "compare" in comparison["answer"].casefold()

        zh = orchestrator.answer("它的价格是多少")
        assert zh["control"]["decision"]["reason"] == "clarification_required"
        assert "指代" in zh["answer"] and "比较" not in zh["answer"]
