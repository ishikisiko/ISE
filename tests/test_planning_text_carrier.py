"""Tests for the planning-text carrier (tasks 6.1-6.4).

When ``narration_guard=off`` (autonomous), a response carrying both prose and
structured tool calls keeps the prose in the message sequence, executes the
tool calls, is not judged a candidate final answer, and is recorded as a
bounded trace note. When ``narration_guard=on`` (guided) the behaviour is
unchanged from before this capability.
"""

from typing import Any, List

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from orchestrators.autonomy_policy import resolve_autonomy_policy
from orchestrators.react_loop_graph import ReactLoopGraphRunner, langgraph_available

pytestmark = pytest.mark.skipif(not langgraph_available(), reason="langgraph not installed")


class M(BaseChatModel):
    replies: List[Any]
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, m, **k):
        r = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        msg = r if isinstance(r, AIMessage) else AIMessage(content=str(r))
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, t, **k):
        return self


def _tc(name, args=None, cid="c1"):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args or {"query": "q"}, "id": cid, "type": "tool_call"}],
    )


class FakeSearch:
    name = "web_search"
    description = "fake"
    max_calls_per_query = 3

    def invoke(self, a):
        return "苹果强微软领先 " * 8

    def get_budget_status(self):
        return {"limit": 3, "used": 0}

    def reset_budget(self):
        pass


class TestPlanningTextCarrier:
    def test_autonomous_preserves_planning_text_and_runs_tool(self):
        auto = resolve_autonomy_policy({"autonomy": {"mode": "autonomous"}}, "autonomous")
        plan_and_call = AIMessage(
            content="我先搜索苹果和微软的最新信息，然后做对比。",
            tool_calls=[{"name": "web_search", "args": {"query": "苹果 微软"}, "id": "c1", "type": "tool_call"}],
        )
        runner = ReactLoopGraphRunner(
            llm=M(replies=[plan_and_call, "苹果相比微软更强，而微软同时领先。" * 6]),
            tools=[FakeSearch()],
            query="苹果和微软的区别",
            autonomy_policy=auto,
        )
        result = runner.run("苹果和微软的区别")
        # The tool-call round was not judged a final proposal.
        assert result["verdicts"][0]["reason"] == "continue"
        # The tool actually ran (evidence retained).
        assert result["evidence_records"]
        # A bounded planning-text trace event was emitted.
        planning_events = [
            e for e in result["trace_events"]
            if str(e.get("id") or "").startswith("react_planning")
        ]
        assert planning_events

    def test_autonomous_planning_text_trace_is_bounded(self):
        auto = resolve_autonomy_policy({"autonomy": {"mode": "autonomous"}}, "autonomous")
        long_plan = "计划" * 500
        plan_and_call = AIMessage(
            content=long_plan,
            tool_calls=[{"name": "web_search", "args": {"query": "q"}, "id": "c1", "type": "tool_call"}],
        )
        runner = ReactLoopGraphRunner(
            llm=M(replies=[plan_and_call, "答案覆盖了要求。" * 6]),
            tools=[FakeSearch()],
            query="苹果和微软的区别",
            autonomy_policy=auto,
        )
        result = runner.run("苹果和微软的区别")
        planning_events = [
            e for e in result["trace_events"]
            if str(e.get("id") or "").startswith("react_planning")
        ]
        assert planning_events
        blob = str(planning_events[0])
        # Bounded: the full 1000-char plan must not leak verbatim.
        assert len(blob) < 500

    def test_guided_does_not_emit_planning_trace(self):
        # narration_guard=on: the carrier trace is not added (behaviour unchanged).
        plan_and_call = AIMessage(
            content="我先搜索一下。",
            tool_calls=[{"name": "web_search", "args": {"query": "q"}, "id": "c1", "type": "tool_call"}],
        )
        runner = ReactLoopGraphRunner(
            llm=M(replies=[plan_and_call, "苹果相比微软更强，而微软同时领先。" * 6]),
            tools=[FakeSearch()],
            query="苹果和微软的区别",
        )
        result = runner.run("苹果和微软的区别")
        assert not any(
            str(e.get("id") or "").startswith("react_planning")
            for e in result["trace_events"]
        )
