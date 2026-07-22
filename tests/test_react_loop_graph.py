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
    normalize_evaluation_config,
)

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
    evaluation_config: Optional[Dict[str, Any]] = None,
    judge_llm=None,
    query: str = "苹果和微软的区别",
    fallback_context: Optional[Dict[str, Any]] = None,
) -> ReactLoopGraphRunner:
    return ReactLoopGraphRunner(
        llm=NativeScriptedChatModel(replies=replies),
        tools=tools,
        max_iterations=max_iterations,
        evaluation_config=evaluation_config,
        judge_llm=judge_llm,
        query=query,
        fallback_context=fallback_context,
    )


class TestTerminationSemantics:
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

    def test_exhausted_at_max_iterations(self):
        tools = [FakeTools.make("web_search", ["全新证据A", "全新证据B", "全新证据C", "全新证据D"])]
        replies = [_tool_call("web_search", {"query": f"q{i}"}, f"c{i}") for i in range(6)]
        runner = make_runner(replies, tools, max_iterations=3)
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "exhausted"
        assert result["iterations"] == 3

    def test_stagnated_on_repeated_fingerprint(self):
        tools = [FakeTools.make("web_search", ["相同结果"])]
        replies = [_tool_call("web_search", {"query": "same"}, "c1")] * 5
        runner = make_runner(
            replies,
            tools,
            max_iterations=6,
            evaluation_config={"repeat_threshold": 2, "no_progress_threshold": 99},
        )
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "stagnated"

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
            evaluation_config={"repeat_threshold": 99, "no_progress_threshold": 2},
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
            evaluation_config={"tool_error_threshold": 2},
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
            fallback_context={"missing_constraints": ["comparison"], "failure_types": []},
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
    def test_judge_exception_does_not_break_loop(self):
        tools = [FakeTools.make("web_search", ["全新证据A", "全新证据B", "全新证据C"])]
        replies = [
            _tool_call("web_search", {"query": "q1"}, "c1"),
            _tool_call("web_search", {"query": "q2"}, "c2"),
            _tool_call("web_search", {"query": "q3"}, "c3"),
        ]
        judge = ScriptedChatModel(replies=[RuntimeError("judge down")])
        runner = make_runner(
            replies,
            tools,
            max_iterations=3,
            evaluation_config={"judge_interval": 1},
            judge_llm=judge,
        )
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] in ("exhausted", "succeeded", "stagnated")
        assert result["judge_error"]

    def test_judge_unparseable_degrades(self):
        tools = [FakeTools.make("web_search", ["全新证据A", "全新证据B"])]
        replies = [
            _tool_call("web_search", {"query": "q1"}, "c1"),
            _tool_call("web_search", {"query": "q2"}, "c2"),
        ]
        judge = ScriptedChatModel(replies=["这不是JSON"])
        runner = make_runner(
            replies,
            tools,
            max_iterations=2,
            evaluation_config={"judge_interval": 1},
            judge_llm=judge,
        )
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "exhausted"
        assert result["judge_error"] == "judge_unparseable_response"

    def test_judge_pass_overrides_rule_missing(self):
        tools = [FakeTools.make("web_search", ["全新证据A"])]
        replies = [
            _tool_call("web_search", {"query": "q1"}, "c1"),
            "完整答案但没有对比标记也没有长度" ,
        ]
        judge = ScriptedChatModel(
            replies=['{"passes_postcheck": true, "missing_constraints": [], "reason": "ok"}']
        )
        runner = make_runner(
            replies,
            tools,
            max_iterations=4,
            evaluation_config={"judge_interval": 1},
            judge_llm=judge,
            fallback_context={"missing_constraints": ["comparison"], "failure_types": []},
        )
        result = runner.run("苹果和微软的区别")
        assert result["loop_status"] == "succeeded"


class TestShimMode:
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
        assert result["loop_status"] in ("exhausted", "succeeded")


class TestConfig:
    def test_normalize_defaults(self):
        cfg = normalize_evaluation_config(None)
        assert cfg["judge_interval"] == 2
        assert cfg["repeat_threshold"] == 2

    def test_normalize_overrides(self):
        cfg = normalize_evaluation_config({"judge_interval": 3, "new_evidence_min_ratio": 0.2})
        assert cfg["judge_interval"] == 3
        assert cfg["new_evidence_min_ratio"] == 0.2
        assert cfg["repeat_threshold"] == 2

    def test_normalize_bad_values_ignored(self):
        cfg = normalize_evaluation_config({"judge_interval": "abc"})
        assert cfg["judge_interval"] == 2
