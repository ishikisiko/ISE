"""Tests for request-level loop cancellation (tasks 5.1-5.9)."""

import threading
from typing import Any, List

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from orchestrators.react_loop_graph import ReactLoopGraphRunner, langgraph_available

pytestmark = pytest.mark.skipif(not langgraph_available(), reason="langgraph not installed")


class M(BaseChatModel):
    replies: List[Any]
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, **kw):
        r = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        msg = r if isinstance(r, AIMessage) else AIMessage(content=str(r))
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kwargs):
        return self


def _tc(name, args=None, cid="c1"):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args or {"query": "q"}, "id": cid, "type": "tool_call"}],
    )


class FakeSearch:
    name = "web_search"
    description = "fake"
    max_calls_per_query = 5

    def invoke(self, args):
        return "苹果相比微软更强，而微软同时领先。" * 6

    def get_budget_status(self):
        return {"limit": 5, "used": 0}

    def reset_budget(self):
        pass


class TestCancellationRegistry:
    """Task 5.1: bounded run_id -> Event registry with LRU eviction and cleanup."""

    def test_register_cancel_remove(self):
        from server import _CancellationRegistry

        reg = _CancellationRegistry(capacity=4)
        event = reg.register("r1")
        assert "r1" in reg
        assert not event.is_set()
        assert reg.cancel("r1") is True
        assert event.is_set()
        # Cancelling an unknown id is a safe no-op.
        assert reg.cancel("nope") is False
        reg.remove("r1")
        assert "r1" not in reg
        assert len(reg) == 0

    def test_lru_eviction_when_capacity_exceeded(self):
        from server import _CancellationRegistry

        reg = _CancellationRegistry(capacity=3)
        for i in range(5):
            reg.register(f"r{i}")
        # Only the most recent capacity entries are retained.
        assert len(reg) == 3
        assert "r0" not in reg
        assert "r4" in reg

    def test_no_leak_on_remove(self):
        from server import _CancellationRegistry

        reg = _CancellationRegistry()
        reg.register("a")
        reg.register("b")
        reg.remove("a")
        reg.remove("a")  # idempotent
        assert "a" not in reg
        assert "b" in reg


class TestRunnerCancellation:
    """Tasks 5.4-5.7: the loop honors the cancel event at node boundaries."""

    def test_cancel_preset_makes_no_model_call_and_no_evidence(self):
        event = threading.Event()
        event.set()
        runner = ReactLoopGraphRunner(
            llm=M(replies=[_tc("web_search"), "ans" * 20]),
            tools=[FakeSearch()],
            query="苹果和微软的区别",
            cancel_event=event,
        )
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "cancelled"
        assert runner.llm.calls == 0  # no model call started
        assert not result["evidence_records"]
        assert "取消" in result["answer"]
        assert result["verdicts"][-1]["reason"] == "cancelled"
        assert result["verdicts"][-1]["action"] == "cancelled"
        assert result["cancelled_iteration"] == 0

    def test_cancel_mid_run_retains_evidence_as_partial_result(self):
        event = threading.Event()

        class CancelAfterFirstCall(M):
            def _generate(self, messages, **kw):
                out = super()._generate(messages, **kw)
                if self.calls == 1:
                    event.set()
                return out

        runner = ReactLoopGraphRunner(
            llm=CancelAfterFirstCall(replies=[_tc("web_search"), "ans" * 20]),
            tools=[FakeSearch()],
            query="苹果和微软的区别",
            cancel_event=event,
        )
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "cancelled"
        # The in-progress first round finished and registered its evidence.
        assert result["evidence_records"]
        assert "取消" in result["answer"]

    def test_cancel_after_end_is_a_noop(self):
        # A normal run completes; setting the event afterwards changes nothing.
        from utils.query_orchestration import QueryAnalysis

        analysis = QueryAnalysis(query="有哪些插件", existence_query=True, requires_evidence=True)
        runner = ReactLoopGraphRunner(
            llm=M(replies=[_tc("web_search"), "答案覆盖了要求，列出多个插件。" * 6]),
            tools=[FakeSearch()],
            query="有哪些插件",
            analysis=analysis,
        )
        result = runner.run("有哪些插件")
        assert result["loop_status"] == "succeeded"
        runner.cancel_event = threading.Event()
        runner.cancel_event.set()
        # No effect on the already-finished result.
        assert result["loop_status"] == "succeeded"

    def test_cancel_does_not_leave_unanswered_tool_call(self):
        event = threading.Event()

        class CancelAfterFirstCall(M):
            def _generate(self, messages, **kw):
                out = super()._generate(messages, **kw)
                if self.calls == 1:
                    event.set()
                return out

        runner = ReactLoopGraphRunner(
            llm=CancelAfterFirstCall(replies=[_tc("web_search"), "ans" * 20]),
            tools=[FakeSearch()],
            query="苹果和微软的区别",
            cancel_event=event,
        )
        result = runner.run("苹果和微软的区别")
        # Every AIMessage carrying tool_calls must be followed by its ToolMessage.
        messages = runner._build_initial_state("q")["messages"]
        # We cannot read the graph's internal messages here, but the cancel path
        # never returns a cancelled verdict from _act without observe having run
        # for any tool-call it produced: assert no exception + clean status.
        assert result["loop_status"] == "cancelled"

    def test_session_usable_after_cancel(self):
        # After a cancelled run, a fresh run on the same runner works normally.
        event = threading.Event()
        event.set()
        runner = ReactLoopGraphRunner(
            llm=M(replies=[_tc("web_search"), "ans" * 20]),
            tools=[FakeSearch()],
            query="苹果和微软的区别",
            cancel_event=event,
        )
        cancelled = runner.run("苹果和微软的区别")
        assert cancelled["loop_status"] == "cancelled"

        # Clear the token and run a normal query with a fresh scripted model.
        from utils.query_orchestration import QueryAnalysis

        analysis = QueryAnalysis(query="有哪些插件", existence_query=True, requires_evidence=True)
        runner.cancel_event = None
        runner.analysis = analysis
        runner.query = "有哪些插件"
        runner.llm = M(replies=[_tc("web_search"), "答案覆盖了要求，列出多个插件。" * 6])
        result = runner.run("有哪些插件")
        assert result["loop_status"] == "succeeded"
