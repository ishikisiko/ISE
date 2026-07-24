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
    tracer: Optional[Any] = None,
) -> ReactLoopGraphRunner:
    return ReactLoopGraphRunner(
        llm=NativeScriptedChatModel(replies=replies),
        tools=tools,
        max_iterations=max_iterations,
        evaluation_config=evaluation_config,
        judge_llm=judge_llm,
        query=query,
        fallback_context=fallback_context,
        tracer=tracer,
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
        assert result["trace_events"]

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
            fallback_context={"missing_constraints": ["comparison"], "failure_types": []},
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
            evaluation_config={"tool_error_threshold": 2},
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
