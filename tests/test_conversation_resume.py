"""Tests for conversation resume: checkpoint state retention, context
compaction boundaries, conversation store CRUD, and follow-up intent classification.
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
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool as lc_tool

# Ensure project root importable when running from anywhere
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrators import conversation_store
from orchestrators.conversation_store import ConversationManager, reset_conversation_manager
from orchestrators.react_loop_graph import ReactLoopGraphRunner, langgraph_available
from orchestrators.context_compaction import assert_tool_call_pairing

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


def _runner(replies, tools, *, query: str = "苹果和微软的区别"):
    return ReactLoopGraphRunner(
        llm=ScriptedChatModel(replies=replies),
        tools=tools,
        max_iterations=5,
        query=query,
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
        assert inp["compactions"] == 0
        assert inp["tokens_at_last_compaction"] == 0
        assert inp["compaction_blocked"] is False
        assert inp["peak_context_ratio"] == 0.0

    def test_feedback_message_appended(self):
        runner = _runner(["x"], [])
        graph = self._fake_graph_with_messages([HumanMessage(content="hi", id="h1")])
        inp = runner._build_followup_state_input(graph, {}, "精简一点")
        last = inp["messages"][-1]
        assert isinstance(last, HumanMessage)
        assert last.content == "精简一点"

    def test_followup_keeps_native_tool_turns_paired(self):
        runner = ReactLoopGraphRunner(
            llm=ScriptedChatModel(replies=["x"]),
            tools=[],
        )
        msgs = [
            HumanMessage(content="user1", id="u1"),
            _tool_call("web_search", call_id="t1"),
            ToolMessage(content="obs1", tool_call_id="t1", id="m1"),
            AIMessage(content="final", id="a1"),
        ]
        graph = self._fake_graph_with_messages(msgs)
        inp = runner._build_followup_state_input(graph, {}, "继续")
        assert len(inp["messages"]) == 1
        assert_tool_call_pairing(msgs)

    def test_history_window_is_not_a_message_trimming_control(self):
        runner = ReactLoopGraphRunner(
            llm=ScriptedChatModel(replies=["x"]),
            tools=[],
            history_window=1,
        )
        assert not hasattr(runner, "history_window")
        assert not hasattr(runner, "_compute_message_removals")

    def test_resume_compacts_over_threshold_checkpoint_and_skips_under_threshold(self):
        # Task 7.2 exit criterion: a checkpoint whose estimated context ratio
        # exceeds the threshold is compacted before the follow-up is appended,
        # while a sub-threshold checkpoint is left untouched. Compaction must
        # keep the first user message and the most-recent final answer, and
        # must not orphan any tool_call.
        runner = ReactLoopGraphRunner(
            llm=ScriptedChatModel(replies=["compact summary"]),
            tools=[],
            context_compaction_config={
                "enabled": True,
                "context_window": 1500,
                "threshold": 0.1,
                "keep_recent_rounds": 1,
            },
        )
        big_body = "证据正文段落" * 200
        messages: List[Any] = [HumanMessage(content="首轮问题", id="u1")]
        for index in range(1, 4):
            call_id = f"c{index}"
            messages.append(
                AIMessage(
                    content="",
                    id=f"a{index}",
                    tool_calls=[
                        {"name": "web_search", "args": {"query": "q"}, "id": call_id, "type": "tool_call"}
                    ],
                )
            )
            messages.append(ToolMessage(content=big_body, tool_call_id=call_id, id=f"t{index}"))
        messages.append(AIMessage(content="最终答案草稿", id="final"))

        captured: Dict[str, Any] = {}

        def update_state(config, update, as_node=None):
            captured["update"] = update
            captured["as_node"] = as_node

        over_graph = SimpleNamespace(
            get_state=lambda config: SimpleNamespace(
                values={"messages": messages, "evidence_records": [], "compactions": 0}
            ),
            update_state=update_state,
        )
        update = runner._compact_checkpoint_if_needed(
            over_graph, {"configurable": {"thread_id": "t"}}
        )
        assert update is not None
        assert captured["as_node"] == "compact"
        kept = [m for m in update["messages"] if not isinstance(m, RemoveMessage)]
        assert kept[0].content == "首轮问题"
        assert any(
            isinstance(m, AIMessage) and m.content == "最终答案草稿" for m in kept
        )
        assert_tool_call_pairing(kept)

        # Sub-threshold checkpoint is left untouched (no compaction, no write).
        under_graph = SimpleNamespace(
            get_state=lambda config: SimpleNamespace(
                values={"messages": [HumanMessage(content="hi", id="h")], "evidence_records": []}
            ),
            update_state=update_state,
        )
        assert runner._compact_checkpoint_if_needed(under_graph, {}) is None


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
        assert len(result2["verdicts"]) == result2["iterations"]
        # Evidence from turn 1 is retained from the checkpoint.
        assert result2.get("evidence_pool_size", 0) >= 1

    def test_new_thread_is_not_resume(self, temp_manager):
        tools = [_fake_tool("web_search", ["证据内容" * 20])]
        runner = _runner([_tool_call("web_search"), "答案" * 30], tools)
        result = runner.run("苹果和微软的区别", conversation_id="fresh-conv")
        assert result["conversation_resumed"] is False
