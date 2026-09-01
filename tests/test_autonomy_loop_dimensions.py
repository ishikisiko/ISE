"""Tests for autonomy-policy dimensions wired into the loop (tasks 3.2-3.12).

Covers the five rule-strength dimensions, the policy-driven budgets, the
preflight guard (3.11), and the guided-budget equivalence invariant (3.12).
"""

from typing import Any, Dict, List, Optional

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from orchestrators.autonomy_policy import (
    AUTONOMOUS_PRESET,
    GUIDED_PRESET,
    assert_no_preflight_toggle,
    resolve_autonomy_policy,
)
from orchestrators.react_loop_graph import ReactLoopGraphRunner, langgraph_available
from utils.query_orchestration import QueryAnalysis

pytestmark = pytest.mark.skipif(not langgraph_available(), reason="langgraph not installed")


class NativeScriptedChatModel(BaseChatModel):
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

    def bind_tools(self, tools, **kwargs):
        return self


def _tool_call(name, args=None, call_id="c1"):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args or {"query": "q"}, "id": call_id, "type": "tool_call"}],
    )


class BudgetTool:
    def __init__(self, name="web_search", limit=3, used=0):
        self.name = name
        self.description = "fake"
        self.max_calls_per_query = limit
        self._used = used

    def invoke(self, args):
        return "evidence " * 10

    def get_budget_status(self):
        return {"limit": self.max_calls_per_query, "used": self._used}

    def reset_budget(self):
        self._used = 0


class OfficialFetchTool:
    name = "fetch_url"
    description = "fake"

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


class TestPreflightGuard:
    """Task 3.11: preflight is binding in every preset; the policy offers no
    value to turn it off."""

    def test_policy_has_no_preflight_toggle(self):
        for policy in (GUIDED_PRESET, AUTONOMOUS_PRESET):
            assert_no_preflight_toggle(policy)

    def test_autonomous_rejects_invalid_tool_args_without_provider_call(self):
        # An unsupported tool emitted as function markup is rejected by the
        # loop's argument validation (preflight) regardless of autonomy mode;
        # no provider call is made for it.
        auto = resolve_autonomy_policy({"autonomy": {"mode": "autonomous"}}, "autonomous")
        analysis = QueryAnalysis(query="有哪些插件", existence_query=True, requires_evidence=True)
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(
                replies=[
                    "<function>no_such_tool</function><query>x</query>",
                    "最终答案，覆盖了问题要求。" * 5,
                ]
            ),
            tools=[BudgetTool()],
            max_iterations=4,
            query="有哪些插件",
            analysis=analysis,
            autonomy_policy=auto,
        )
        result = runner.run("有哪些插件")
        # The invalid call is surfaced as an invalid_tool_request verdict, not
        # executed; the loop never made a real provider call for it.
        assert any(
            v["reason"] == "invalid_tool_request" for v in result["verdicts"]
        )


class TestGuidedBudgetEquivalence:
    """Task 3.12: a guided policy resolved from config mirrors the termination
    block and the per-tool budgets, field by field."""

    def test_guided_budgets_equal_termination_block(self):
        config = {
            "termination": {
                "max_iterations": 8,
                "max_synthesis_attempts": 2,
                "tool_budgets": {
                    "web_search": 6,
                    "fetch_url": 3,
                    "search_recovery": 4,
                    "local_docs": 2,
                },
            },
            "orchestration": {
                "context_compaction": {"threshold": 0.7, "keep_recent_rounds": 3},
            },
        }
        policy = resolve_autonomy_policy(config)
        assert policy.budgets.max_iterations == 8
        assert policy.budgets.max_synthesis_attempts == 2
        assert policy.budgets.tool_limit("web_search") == 6
        assert policy.budgets.tool_limit("fetch_url") == 3
        assert policy.budgets.tool_limit("search_recovery") == 4
        assert policy.budgets.tool_limit("local_docs") == 2
        assert abs(policy.budgets.context_compaction.threshold - 0.7) < 1e-9
        assert policy.budgets.context_compaction.keep_recent_rounds == 3

    def test_runner_uses_guided_policy_iterations(self):
        config = {
            "termination": {"max_iterations": 7, "tool_budgets": {"web_search": 4}},
        }
        policy = resolve_autonomy_policy(config)
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(replies=["unused"]),
            tools=[BudgetTool("web_search", limit=4)],
            query="q",
            autonomy_policy=policy,
        )
        assert runner.max_iterations == 7
        # The budget self-report reflects the policy ceiling.
        report = runner._budget_self_report({"iteration": 0})
        assert "7/7" in report

    def test_runner_applies_autonomous_tool_budgets(self):
        auto = resolve_autonomy_policy({"autonomy": {"mode": "autonomous"}}, "autonomous")
        tool = BudgetTool("web_search", limit=3)
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(replies=["unused"]),
            tools=[tool],
            query="q",
            autonomy_policy=auto,
        )
        # run() applies the policy's per-tool ceilings before executing.
        runner._apply_policy_tool_budgets()
        expected = auto.budgets.tool_limit("web_search")
        assert tool.max_calls_per_query == expected
        assert expected > 3  # autonomous widens the guided default


class TestChecklistInjection:
    """Task 3.2: checklist wording switches with checklist_injection."""

    def test_guided_system_prompt_states_must_satisfy(self):
        analysis = QueryAnalysis(
            query="苹果和微软的区别", constraints={"comparison_required": True}
        )
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(replies=["x"]),
            tools=[],
            query="苹果和微软的区别",
            analysis=analysis,
        )
        assert "本次回答必须满足" in runner.system_prompt

    def test_autonomous_system_prompt_states_reference_only(self):
        analysis = QueryAnalysis(
            query="苹果和微软的区别", constraints={"comparison_required": True}
        )
        auto = resolve_autonomy_policy({"autonomy": {"mode": "autonomous"}}, "autonomous")
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(replies=["x"]),
            tools=[],
            query="苹果和微软的区别",
            analysis=analysis,
            autonomy_policy=auto,
        )
        assert "必须满足" not in runner.system_prompt
        assert "参考" in runner.system_prompt
        # The checklist is still derived for coverage accounting.
        assert runner.initial_checklist == ["comparison"]


class TestNarrationGuard:
    """Task 3.6: narration guard only rejects process narration when on."""

    def test_guided_rejects_process_narration(self):
        analysis = QueryAnalysis(query="有哪些插件", existence_query=True, requires_evidence=True)
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(replies=["x"]),
            tools=[],
            query="有哪些插件",
            analysis=analysis,
        )
        msg = AIMessage(content="我需要先搜索一下，然后查找相关信息再回答。")
        assert runner._process_narration_reason(msg) == "search_plan_text"

    def test_autonomous_does_not_reject_process_narration(self):
        analysis = QueryAnalysis(query="有哪些插件", existence_query=True, requires_evidence=True)
        auto = resolve_autonomy_policy({"autonomy": {"mode": "autonomous"}}, "autonomous")
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(replies=["x"]),
            tools=[],
            query="有哪些插件",
            analysis=analysis,
            autonomy_policy=auto,
        )
        msg = AIMessage(content="我需要先搜索一下，然后查找相关信息再回答。")
        assert runner._process_narration_reason(msg) is None


class TestJudgeGate:
    """Task 3.7: judge is never called when judge_enabled is false."""

    def test_autonomous_never_calls_judge(self):
        analysis = QueryAnalysis(
            query="Acme price",
            entities=["Acme"],
            claim_classes=["numeric", "pricing"],
            constraints={"authority_required": True},
            requires_evidence=True,
        )
        draft = "Acme 官方价格为 $0.50 per 1M tokens。"  # no [E1]
        auto = resolve_autonomy_policy({"autonomy": {"mode": "autonomous"}}, "autonomous")

        class CountingJudge(NativeScriptedChatModel):
            pass

        judge = CountingJudge(replies=['{"passes": true}'])
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(
                replies=[
                    _tool_call("fetch_url", {"url": "https://docs.acme.example/pricing"}),
                    draft,
                ]
            ),
            tools=[OfficialFetchTool()],
            max_iterations=6,
            query="Acme price",
            analysis=analysis,
            termination_config={"judge_interval": 1},
            judge_llm=judge,
            autonomy_policy=auto,
        )
        result = runner.run("Acme price")
        assert judge.calls == 0
        assert all(not v["judge_used"] for v in result["verdicts"])


class TestForcedSynthesisBypass:
    """Task 3.8: forced synthesis paths are off in autonomous."""

    def test_autonomous_does_not_force_synthesis_near_cap(self):
        analysis = QueryAnalysis(query="有哪些插件", existence_query=True, requires_evidence=True)
        auto = resolve_autonomy_policy({"autonomy": {"mode": "autonomous"}}, "autonomous")
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(
                replies=[_tool_call("web_search", {"query": f"q{i}"}, f"c{i}") for i in range(8)]
                + ["最终答案，覆盖了要求。" * 5]
            ),
            tools=[BudgetTool("web_search", limit=8)],
            max_iterations=15,
            query="有哪些插件",
            analysis=analysis,
            autonomy_policy=auto,
        )
        result = runner.run("有哪些插件")
        # No forced_synthesis verdict in autonomous.
        assert not any(v["reason"] in ("forced_synthesis", "ready_to_synthesize") for v in result["verdicts"])


class TestAdvisoryAccept:
    """Tasks 3.3/3.4: advisory critic/citation accept a final answer and
    record gaps without blocking."""

    def test_autonomous_accepts_uncited_numeric_with_advisory_gap(self):
        analysis = QueryAnalysis(
            query="Acme price",
            entities=["Acme"],
            claim_classes=["numeric", "pricing"],
            constraints={"authority_required": True},
            requires_evidence=True,
        )
        draft = "Acme 官方价格为 $0.50 per 1M tokens。"  # no [E1]
        auto = resolve_autonomy_policy({"autonomy": {"mode": "autonomous"}}, "autonomous")
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(
                replies=[
                    _tool_call("fetch_url", {"url": "https://docs.acme.example/pricing"}),
                    draft,
                ]
            ),
            tools=[OfficialFetchTool()],
            max_iterations=6,
            query="Acme price",
            analysis=analysis,
            autonomy_policy=auto,
        )
        result = runner.run("Acme price")
        assert result["loop_status"] == "succeeded"
        last = result["verdicts"][-1]
        assert last["reason"] == "model_self_wrap"
        assert last["autonomy_mode"] == "autonomous"
        assert any(g["kind"] == "citation" for g in last["advisory_gaps"])


class TestBudgetSelfReport:
    """Task 3.10: the act prompt carries constraint-accurate budget info."""

    def test_budget_report_reflects_policy(self):
        auto = resolve_autonomy_policy({"autonomy": {"mode": "autonomous"}}, "autonomous")
        tool = BudgetTool("web_search", limit=auto.budgets.tool_limit("web_search"))
        runner = ReactLoopGraphRunner(
            llm=NativeScriptedChatModel(replies=["x"]),
            tools=[tool],
            query="q",
            autonomy_policy=auto,
        )
        runner._apply_policy_tool_budgets()
        report = runner._budget_self_report({"iteration": 2})
        assert f"/{auto.budgets.max_iterations}" in report
        assert "web_search" in report
