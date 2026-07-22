"""Tests for conversation resume: checkpointer state retention, message
trimming, conversation store CRUD, and follow-up intent classification.

All tests use scripted fake models/tools and a temporary SQLite database; no
real LLM or search backend is required.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool as lc_tool

# Ensure project root importable when running from anywhere
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrators import conversation_store
from orchestrators.conversation_store import ConversationManager, reset_conversation_manager
from orchestrators.react_loop_graph import ReactLoopGraphRunner, langgraph_available

pytestmark = pytest.mark.skipif(not langgraph_available(), reason="langgraph not installed")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class ScriptedChatModel(BaseChatModel):
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
        """Fake tool for testing. Args: query."""
        idx = min(state["calls"], len(outputs) - 1)
        state["calls"] += 1
        result = outputs[idx]
        if isinstance(result, Exception):
            raise result
        return result

    fake_tool.name = name
    return fake_tool


def _runner(replies, tools, *, query: str = "苹果和微软的区别", fallback_context=None):
    return ReactLoopGraphRunner(
        llm=ScriptedChatModel(replies=replies),
        tools=tools,
        max_iterations=5,
        query=query,
        fallback_context=fallback_context,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def temp_manager(tmp_path, monkeypatch):
    reset_conversation_manager()
    mgr = ConversationManager(str(tmp_path / "conv.db"))
    monkeypatch.setattr(conversation_store, "_singleton", mgr)
    yield mgr
    reset_conversation_manager()


# ---------------------------------------------------------------------------
# Conversation store
# ---------------------------------------------------------------------------
class TestConversationStore:
    def test_record_and_retrieve_turns(self, temp_manager):
        temp_manager.record_turn("c1", "问1", "答1")
        temp_manager.record_turn("c1", "问2", "答2")
        turns = temp_manager.get_recent_turns("c1")
        assert [t["turn_index"] for t in turns] == [1, 2]
        assert temp_manager.get_last_turn("c1")["query"] == "问2"

    def test_no_checkpoint_initially(self, temp_manager):
        assert temp_manager.has_checkpoint("c1") is False

    def test_inherited_time_constraint(self, temp_manager):
        tc = {"days": 7, "freshness": "week", "time_expression": "最近一周"}
        temp_manager.record_turn("c1", "上周的新闻", "答", time_constraint=tc)
        temp_manager.record_turn("c1", "那后来呢", "答2")  # no time expr
        inherited = temp_manager.get_inherited_time_constraint("c1")
        assert inherited is not None
        assert inherited["days"] == 7

    def test_time_constraint_skipped_when_no_days(self, temp_manager):
        temp_manager.record_turn("c1", "q", "a", time_constraint={"days": None})
        assert temp_manager.get_inherited_time_constraint("c1") is None

    def test_lru_eviction(self, tmp_path, monkeypatch):
        reset_conversation_manager()
        mgr = ConversationManager(str(tmp_path / "conv.db"), max_threads=2)
        monkeypatch.setattr(conversation_store, "_singleton", mgr)
        for cid in ["a", "b", "c"]:
            mgr.record_turn(cid, "q", "a")
        # 'a' is the oldest; it should have been evicted
        assert mgr.get_recent_turns("a") == []
        assert mgr.get_recent_turns("b") != []
        assert mgr.get_recent_turns("c") != []
        reset_conversation_manager()

    def test_topic_reset_marker(self, temp_manager):
        temp_manager.record_turn("c1", "q", "a")
        assert temp_manager.last_turn_is_topic_reset("c1") is False
        temp_manager.record_turn("c1", "新话题", "a2", topic_reset=True)
        assert temp_manager.last_turn_is_topic_reset("c1") is True


# ---------------------------------------------------------------------------
# Follow-up input construction (field-level checks, no real invoke)
# ---------------------------------------------------------------------------
class TestFollowupStateInput:
    def _fake_graph_with_messages(self, messages):
        snapshot = SimpleNamespace(values={"messages": messages})
        graph = SimpleNamespace(get_state=lambda config: snapshot)
        return graph

    def test_evidence_pool_and_verdicts_absent(self):
        runner = _runner(["x"], [])
        graph = self._fake_graph_with_messages([HumanMessage(content="hi", id="h1")])
        inp = runner._build_followup_state_input(graph, {"configurable": {"thread_id": "t"}}, "反馈")
        assert "evidence_pool" not in inp
        assert "verdicts" not in inp

    def test_control_fields_reset(self):
        runner = _runner(["x"], [])
        graph = self._fake_graph_with_messages([HumanMessage(content="hi", id="h1")])
        inp = runner._build_followup_state_input(graph, {}, "反馈")
        assert inp["iteration"] == 0
        assert inp["fingerprint_streak"] == 0
        assert inp["no_progress_streak"] == 0
        assert inp["final_proposed"] is False
        assert inp["termination_reason"] is None
        assert inp["final_answer"] is None

    def test_feedback_message_appended(self):
        runner = _runner(["x"], [])
        graph = self._fake_graph_with_messages([HumanMessage(content="hi", id="h1")])
        inp = runner._build_followup_state_input(graph, {}, "精简一点")
        last = inp["messages"][-1]
        assert isinstance(last, HumanMessage)
        assert last.content == "精简一点"

    def test_trimming_only_targets_tool_messages(self):
        runner = ReactLoopGraphRunner(
            llm=ScriptedChatModel(replies=["x"]),
            tools=[],
            history_window=1,
        )
        msgs = [
            HumanMessage(content="user1", id="u1"),
            ToolMessage(content="obs1", tool_call_id="t1", id="m1"),
            ToolMessage(content="obs2", tool_call_id="t2", id="m2"),
            ToolMessage(content="obs3", tool_call_id="t3", id="m3"),
            ToolMessage(content="obs4", tool_call_id="t4", id="m4"),
            AIMessage(content="final", id="a1"),
        ]
        graph = self._fake_graph_with_messages(msgs)
        removals = runner._compute_message_removals(graph, {})
        # Only ToolMessages are trimmable; the AIMessage and HumanMessage survive.
        removed_ids = [r.id for r in removals]
        assert "a1" not in removed_ids
        assert "u1" not in removed_ids
        assert len(removed_ids) >= 1
        assert all(rid.startswith("m") for rid in removed_ids)

    def test_no_trimming_within_window(self):
        runner = ReactLoopGraphRunner(
            llm=ScriptedChatModel(replies=["x"]),
            tools=[],
            history_window=5,
        )
        msgs = [ToolMessage(content="o", tool_call_id="t", id=f"m{i}") for i in range(3)]
        graph = self._fake_graph_with_messages(msgs)
        assert runner._compute_message_removals(graph, {}) == []


# ---------------------------------------------------------------------------
# End-to-end resume across two turns (real graph + temp checkpointer)
# ---------------------------------------------------------------------------
class TestEndToEndResume:
    def test_evidence_pool_retained_across_turns(self, temp_manager):
        cid = "conv-e2e"
        evidence = "2025年苹果和微软分别公布财报，苹果相比微软增长更快，而微软同时在云上领先，" * 6

        # Turn 1: search tool produces evidence, then model answers.
        tools1 = [_fake_tool("web_search", [evidence])]
        runner1 = _runner([_tool_call("web_search"), evidence], tools1)
        result1 = runner1.run("苹果和微软的区别", conversation_id=cid)
        assert result1["loop_status"] == "succeeded"
        assert result1["conversation_resumed"] is False

        # Turn 2: feedback only; model rewrites without tools.
        tools2 = [_fake_tool("web_search", ["should-not-be-used"])]
        runner2 = _runner(["精简版答案，苹果对比微软。"], tools2, query="精简一点")
        result2 = runner2.run("精简一点", conversation_id=cid)
        assert result2["conversation_resumed"] is True
        # Evidence from turn 1 is retained from the checkpoint.
        assert result2.get("evidence_pool_size", 0) >= 1

    def test_new_thread_is_not_resume(self, temp_manager):
        tools = [_fake_tool("web_search", ["证据内容" * 20])]
        runner = _runner([_tool_call("web_search"), "答案" * 30], tools)
        result = runner.run("苹果和微软的区别", conversation_id="fresh-conv")
        assert result["conversation_resumed"] is False


# ---------------------------------------------------------------------------
# Follow-up intent classification (LangChainOrchestrator)
# ---------------------------------------------------------------------------
class TestFollowupIntent:
    def _orchestrator_for_intent(self, reply_content: Any):
        from langchain.langchain_orchestrator import LangChainOrchestrator

        # _classify_followup_intent only needs routing_llm; bypass heavy __init__.
        orch = LangChainOrchestrator.__new__(LangChainOrchestrator)
        orch.routing_llm = ScriptedChatModel(replies=[reply_content])
        orch.show_timings = False
        return orch

    def test_continuation_default_on_parse_garbage(self, temp_manager):
        orch = self._orchestrator_for_intent("乱七八糟不可解析")
        turns = [{"query": "苹果和微软", "answer": "苹果更强"}]
        assert orch._classify_followup_intent("再多说点", turns) == "continuation"

    def test_new_topic_detected(self, temp_manager):
        orch = self._orchestrator_for_intent("new_topic")
        turns = [{"query": "苹果和微软", "answer": "苹果更强"}]
        assert orch._classify_followup_intent("北京天气怎么样", turns) == "new_topic"

    def test_continuation_detected(self, temp_manager):
        orch = self._orchestrator_for_intent("continuation")
        turns = [{"query": "苹果和微软", "answer": "苹果更强"}]
        assert orch._classify_followup_intent("精简一点", turns) == "continuation"

    def test_no_history_is_new_topic(self, temp_manager):
        orch = self._orchestrator_for_intent("continuation")
        assert orch._classify_followup_intent("anything", []) == "new_topic"

    def test_classifier_exception_defaults_to_continuation(self, temp_manager):
        orch = self._orchestrator_for_intent(RuntimeError("routing down"))
        turns = [{"query": "q", "answer": "a"}]
        assert orch._classify_followup_intent("反馈", turns) == "continuation"
