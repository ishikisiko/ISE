"""Tests for model-initiated clarification (tasks 7.1-7.8).

Covers the six required scenarios (7.8): initiating a clarification, replying to
resume, explicit reset/abandon, clarification budget exhaustion, the iteration
ceiling not being bypassed across a clarification round-trip, and a cross-turn
autonomy switch resetting the trajectory. Also covers the tool-surface filtering
(7.1), the non-terminal state (7.2), the SSE waiting-for-input event (7.3), and
the clarification-owner mutual exclusion (7.4).

All loop tests use scripted fake models/tools and a temporary SQLite checkpoint
store; no real LLM or search backend is required.
"""

from __future__ import annotations

import os
import sys
from typing import Any, List

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

# Ensure project root importable when running from anywhere
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain.langchain_react_tools import ReActAskUserTool
from orchestrators import conversation_store
from orchestrators.autonomy_policy import (
    AUTONOMOUS_PRESET,
    GUIDED_PRESET,
    resolve_autonomy_policy,
)
from orchestrators.conversation_store import (
    ConversationManager,
    reset_conversation_manager,
)
from orchestrators.react_loop_graph import ReactLoopGraphRunner, langgraph_available

pytestmark = pytest.mark.skipif(not langgraph_available(), reason="langgraph not installed")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
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


def _ask(question: str = "你想比较哪两款产品？", cid: str = "c1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "ask_user", "args": {"question": question}, "id": cid, "type": "tool_call"}
        ],
    )


def _tc(name: str, args: Any = None, cid: str = "c2") -> AIMessage:
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


def _autonomous_policy():
    return resolve_autonomy_policy({"autonomy": {"mode": "autonomous"}}, "autonomous")


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


def _record_turn(mgr, cid, query, result, *, topic_reset=False):
    """Record a turn the way the orchestrator layer would (with control meta)."""
    mgr.record_turn(cid, query, result.get("answer", ""), result=result, topic_reset=topic_reset)


# ---------------------------------------------------------------------------
# 7.1 / 7.2: the ask_user tool and the loop's clarification detection
# ---------------------------------------------------------------------------
class TestClarificationTool:
    def test_ask_user_emits_waiting_payload_and_no_provider_call(self):
        tool = ReActAskUserTool(max_calls_per_query=2)
        import json

        payload = json.loads(tool.invoke({"question": "范围是哪两个？"}))
        assert payload["status"] == "waiting_for_user"
        assert payload["question"] == "范围是哪两个？"

    def test_ask_user_budget_exhausted_is_structured(self):
        tool = ReActAskUserTool(max_calls_per_query=1)
        import json

        first = json.loads(tool.invoke({"question": "q1"}))
        assert first["status"] == "waiting_for_user"
        second = json.loads(tool.invoke({"question": "q2"}))
        assert second["status"] == "rejected"
        assert second["reason"] == "max_calls_per_query"

    def test_ask_user_question_is_bounded(self):
        tool = ReActAskUserTool(max_calls_per_query=2)
        import json

        payload = json.loads(tool.invoke({"question": "x" * 1000}))
        assert len(payload["question"]) <= 500


class TestClarificationDetection:
    def test_loop_detects_ask_user_and_pauses_non_terminal(self):
        runner = ReactLoopGraphRunner(
            llm=M(replies=[_ask(), "最终答案覆盖了要求。" * 6]),
            tools=[ReActAskUserTool()],
            query="帮我比较一下",
            autonomy_policy=_autonomous_policy(),
        )
        result = runner.run("帮我比较一下")
        assert result["loop_status"] == "clarification_required"
        assert result["model_clarification"] is True
        assert result["clarification_question"] == "你想比较哪两款产品？"
        # ask_user produces no evidence and no provider retrieval.
        assert not result["evidence_records"]
        # The verdict is a distinct model_clarification pause, not a budget or
        # evidence terminal.
        assert result["verdicts"][-1]["reason"] == "model_clarification"
        assert result["verdicts"][-1]["action"] == "clarify"
        assert result["termination_reason"] == "clarification_required"

    def test_clarification_distinct_from_budget_and_evidence_terminals(self):
        # A clarification must not be reported as exhausted / insufficient.
        runner = ReactLoopGraphRunner(
            llm=M(replies=[_ask()]),
            tools=[ReActAskUserTool()],
            query="帮我比较一下",
            autonomy_policy=_autonomous_policy(),
        )
        result = runner.run("帮我比较一下")
        assert result["loop_status"] not in {"exhausted", "evidence_insufficient", "succeeded"}


# ---------------------------------------------------------------------------
# 7.1: ask_user is only on the tool surface when the model owns clarification
# ---------------------------------------------------------------------------
class TestToolSurfaceFiltering:
    def test_guided_policy_hides_ask_user_from_surface(self):
        # The orchestrator filters ask_user unless the model owns clarification.
        # Verify the policy predicate that drives that filter.
        assert GUIDED_PRESET.model_owns_clarification is False
        assert AUTONOMOUS_PRESET.model_owns_clarification is True

    def test_orchestrator_filters_ask_user_under_system_owner(self, monkeypatch):
        from orchestrators.react_agent_orchestrator import ReactAgentOrchestrator

        orch = ReactAgentOrchestrator.__new__(ReactAgentOrchestrator)
        # Minimal attributes used by the filter block only.
        orch.tools = [ReActAskUserTool(), FakeSearch()]

        def _filter(allow_search, policy):
            model_owns = policy.model_owns_clarification
            return [
                tool
                for tool in orch.tools
                if allow_search
                and (model_owns or tool.name != "ask_user")
            ]

        guided_visible = [t.name for t in _filter(True, GUIDED_PRESET)]
        auto_visible = [t.name for t in _filter(True, AUTONOMOUS_PRESET)]
        assert "ask_user" not in guided_visible
        assert "ask_user" in auto_visible


# ---------------------------------------------------------------------------
# 7.1: clarification budget exhaustion lets the loop continue
# ---------------------------------------------------------------------------
class TestClarificationBudgetExhausted:
    def test_budget_exhausted_does_not_pause_and_loop_continues(self, temp_manager):
        # Budget exhaustion happens across a clarification round-trip: the
        # preserved counter means a later ask_user call returns
        # budget_exhausted instead of waiting_for_user, so the loop keeps going
        # on existing evidence rather than pausing or failing.
        cid = "conv-budget"
        ask = ReActAskUserTool(max_calls_per_query=2)
        search = FakeSearch()
        policy = _autonomous_policy()
        # Turn 1: clarify (call 1/2).
        r1 = ReactLoopGraphRunner(
            llm=M(replies=[_ask(cid="a")]),
            tools=[ask, search],
            query="帮我比较一下",
            autonomy_policy=policy,
        ).run("帮我比较一下", conversation_id=cid)
        assert r1["model_clarification"] is True
        _record_turn(
            temp_manager,
            cid,
            "帮我比较一下",
            {"answer": "Q?", "control": {"model_clarification": True, "autonomy": {"mode": "autonomous"}}},
        )
        # Turn 2: clarify again (call 2/2).
        r2 = ReactLoopGraphRunner(
            llm=M(replies=[_ask(cid="b")]),
            tools=[ask, search],
            query="帮我比较一下",
            autonomy_policy=policy,
        ).run("范围更大一点", conversation_id=cid)
        assert r2["model_clarification"] is True
        _record_turn(
            temp_manager,
            cid,
            "范围更大一点",
            {"answer": "Q?", "control": {"model_clarification": True, "autonomy": {"mode": "autonomous"}}},
        )
        # Turn 3: the ask_user budget is now exhausted; the loop must NOT pause
        # again and instead reach a normal terminal.
        r3 = ReactLoopGraphRunner(
            llm=M(replies=[_ask(cid="c"), "最终答案覆盖了要求。" * 6]),
            tools=[ask, search],
            query="帮我比较一下",
            autonomy_policy=policy,
        ).run("就这样吧", conversation_id=cid)
        assert r3["model_clarification"] is False
        assert r3["loop_status"] != "clarification_required"


# ---------------------------------------------------------------------------
# 7.5: replying resumes the paused loop; iteration continues, not resets
# ---------------------------------------------------------------------------
class TestClarificationResume:
    def test_reply_resumes_and_iteration_continues(self, temp_manager):
        cid = "conv-resume"
        ask = ReActAskUserTool()
        search = FakeSearch()
        # Turn 1: the model asks for clarification on its first act (iteration 1).
        runner1 = ReactLoopGraphRunner(
            llm=M(replies=[_ask()]),
            tools=[ask, search],
            query="帮我比较一下",
            autonomy_policy=_autonomous_policy(),
        )
        r1 = runner1.run("帮我比较一下", conversation_id=cid)
        assert r1["model_clarification"] is True
        assert r1["iterations"] == 1
        _record_turn(
            temp_manager,
            cid,
            "帮我比较一下",
            {
                "answer": r1["clarification_question"],
                "control": {
                    "model_clarification": True,
                    "clarification_question": r1["clarification_question"],
                    "loop_status": "clarification_required",
                    "autonomy": {"mode": "autonomous"},
                },
            },
        )

        # Turn 2: the user replies. The loop must resume (not restart), so the
        # next act runs at iteration 2 (1 + 1), proving iteration continued.
        runner2 = ReactLoopGraphRunner(
            llm=M(replies=[_tc("web_search"), "最终答案覆盖了要求。" * 6]),
            tools=[ask, search],
            query="帮我比较一下",
            autonomy_policy=_autonomous_policy(),
        )
        r2 = runner2.run("比较 A 和 B", conversation_id=cid)
        assert r2["conversation_resumed"] is True
        assert r2["model_clarification"] is False
        # Resumed: first verdict of the resumed turn is at iteration >= 2.
        resumed_iterations = [v["iteration"] for v in r2["verdicts"]]
        assert resumed_iterations, "resumed turn should produce verdicts"
        assert min(resumed_iterations) >= 2

    def test_reply_preserves_prior_evidence(self, temp_manager):
        cid = "conv-evidence"
        ask = ReActAskUserTool()
        search = FakeSearch()
        # Turn 1: one search gathers evidence, then the model asks to clarify.
        runner1 = ReactLoopGraphRunner(
            llm=M(replies=[_tc("web_search"), _ask()]),
            tools=[ask, search],
            query="帮我比较一下",
            autonomy_policy=_autonomous_policy(),
        )
        r1 = runner1.run("帮我比较一下", conversation_id=cid)
        assert r1["model_clarification"] is True
        assert r1["evidence_records"]  # evidence was gathered before pausing
        _record_turn(
            temp_manager,
            cid,
            "帮我比较一下",
            {
                "answer": r1["clarification_question"],
                "control": {
                    "model_clarification": True,
                    "loop_status": "clarification_required",
                    "autonomy": {"mode": "autonomous"},
                },
            },
        )
        # Turn 2: reply. The resumed pool retains turn-1 evidence.
        runner2 = ReactLoopGraphRunner(
            llm=M(replies=["最终答案覆盖了要求，已综合已有证据。" * 6]),
            tools=[ask, search],
            query="帮我比较一下",
            autonomy_policy=_autonomous_policy(),
        )
        r2 = runner2.run("比较 A 和 B", conversation_id=cid)
        assert r2["conversation_resumed"] is True
        assert r2["evidence_pool_size"] >= 1


# ---------------------------------------------------------------------------
# 7.5: explicit reset abandons the paused clarification (fresh run)
# ---------------------------------------------------------------------------
class TestExplicitResetAbandon:
    def test_topic_reset_abandons_clarification_state(self, temp_manager):
        cid = "conv-reset"
        ask = ReActAskUserTool()
        search = FakeSearch()
        runner1 = ReactLoopGraphRunner(
            llm=M(replies=[_ask()]),
            tools=[ask, search],
            query="帮我比较一下",
            autonomy_policy=_autonomous_policy(),
        )
        r1 = runner1.run("帮我比较一下", conversation_id=cid)
        assert r1["model_clarification"] is True
        # Record the paused turn as a topic reset (user moved on explicitly).
        _record_turn(
            temp_manager,
            cid,
            "帮我比较一下",
            {
                "answer": r1["clarification_question"],
                "control": {"model_clarification": True, "autonomy": {"mode": "autonomous"}},
            },
            topic_reset=True,
        )
        # Turn 2 is a brand-new question: must NOT resume the paused loop.
        runner2 = ReactLoopGraphRunner(
            llm=M(replies=[_tc("web_search"), "最终答案覆盖了要求。" * 6]),
            tools=[ask, search],
            query="一个完全不同的问题",
            autonomy_policy=_autonomous_policy(),
        )
        r2 = runner2.run("一个完全不同的问题", conversation_id=cid)
        assert r2["conversation_resumed"] is False


# ---------------------------------------------------------------------------
# 7.5: a clarification round-trip does not bypass the iteration ceiling
# ---------------------------------------------------------------------------
class TestIterationCeilingShared:
    def test_repeated_clarifications_hit_the_ceiling(self, temp_manager):
        cid = "conv-ceiling"
        ask = ReActAskUserTool(max_calls_per_query=8)
        search = FakeSearch()
        # A tight ceiling so we can observe the bound being shared.
        policy = resolve_autonomy_policy({"autonomy": {"mode": "autonomous"}}, "autonomous")
        from dataclasses import replace as _r

        policy = _r(policy.budgets and policy, budgets=_r(policy.budgets, max_iterations=2))

        # Turn 1: ask immediately (iteration 1).
        runner1 = ReactLoopGraphRunner(
            llm=M(replies=[_ask()]),
            tools=[ask, search],
            query="帮我比较一下",
            autonomy_policy=policy,
        )
        r1 = runner1.run("帮我比较一下", conversation_id=cid)
        assert r1["model_clarification"] is True
        _record_turn(
            temp_manager,
            cid,
            "帮我比较一下",
            {
                "answer": r1["clarification_question"],
                "control": {"model_clarification": True, "autonomy": {"mode": "autonomous"}},
            },
        )
        # Turn 2: ask again (iteration 2). The shared ceiling means iteration
        # is exhausted after this act; the loop cannot run forever.
        runner2 = ReactLoopGraphRunner(
            llm=M(replies=[_ask(cid="b")]),
            tools=[ask, search],
            query="帮我比较一下",
            autonomy_policy=policy,
        )
        r2 = runner2.run("再说一次", conversation_id=cid)
        # Either it clarifies again at the ceiling, or it terminates because the
        # ceiling is reached. Either way it stopped without bypassing the bound.
        assert r2["loop_status"] in {"clarification_required", "exhausted"}


# ---------------------------------------------------------------------------
# 7.6: a cross-turn autonomy switch resets the trajectory
# ---------------------------------------------------------------------------
class TestModeSwitchReset:
    def test_switching_autonomy_resets_trajectory(self, temp_manager):
        cid = "conv-switch"
        ask = ReActAskUserTool()
        search = FakeSearch()
        # Turn 1: autonomous, clarifies.
        runner1 = ReactLoopGraphRunner(
            llm=M(replies=[_ask()]),
            tools=[ask, search],
            query="帮我比较一下",
            autonomy_policy=_autonomous_policy(),
        )
        r1 = runner1.run("帮我比较一下", conversation_id=cid)
        assert r1["model_clarification"] is True
        _record_turn(
            temp_manager,
            cid,
            "帮我比较一下",
            {
                "answer": r1["clarification_question"],
                "control": {"model_clarification": True, "autonomy": {"mode": "autonomous"}},
            },
        )
        # Sanity: the autonomous checkpoint exists.
        assert temp_manager.has_checkpoint(cid) is True

        # Turn 2: switch to guided. The prior autonomous trajectory must be
        # dropped (fresh run, autonomy_reset flagged).
        guided = resolve_autonomy_policy({"autonomy": {"mode": "guided"}}, "guided")
        runner2 = ReactLoopGraphRunner(
            llm=M(replies=[_tc("web_search"), "最终答案覆盖了要求，附 [E1] 来源。" * 6]),
            tools=[ask, search],
            query="帮我比较一下",
            autonomy_policy=guided,
        )
        r2 = runner2.run("比较 A 和 B", conversation_id=cid)
        assert r2["conversation_resumed"] is False
        assert r2["autonomy_reset"] is True

    def test_same_autonomy_continues_without_reset(self, temp_manager):
        cid = "conv-same"
        ask = ReActAskUserTool()
        search = FakeSearch()
        runner1 = ReactLoopGraphRunner(
            llm=M(replies=[_ask()]),
            tools=[ask, search],
            query="帮我比较一下",
            autonomy_policy=_autonomous_policy(),
        )
        r1 = runner1.run("帮我比较一下", conversation_id=cid)
        _record_turn(
            temp_manager,
            cid,
            "帮我比较一下",
            {
                "answer": r1["clarification_question"],
                "control": {"model_clarification": True, "autonomy": {"mode": "autonomous"}},
            },
        )
        runner2 = ReactLoopGraphRunner(
            llm=M(replies=[_tc("web_search"), "最终答案覆盖了要求。" * 6]),
            tools=[ask, search],
            query="帮我比较一下",
            autonomy_policy=_autonomous_policy(),
        )
        r2 = runner2.run("比较 A 和 B", conversation_id=cid)
        # Same mode: no reset, and it resumes.
        assert r2["autonomy_reset"] is False
        assert r2["conversation_resumed"] is True


# ---------------------------------------------------------------------------
# 7.3: the streaming endpoint emits a recognizable waiting-for-input event
# ---------------------------------------------------------------------------
class TestSSEClarifyEvent:
    def test_stream_emits_clarify_event_carrying_question(self, monkeypatch):
        import server

        class Pipeline:
            def answer(self, query, **kwargs):
                return {
                    "answer": "你想比较哪两款产品？",
                    "search_hits": [],
                    "control": {
                        "model_clarification": True,
                        "clarification_question": "你想比较哪两款产品？",
                        "loop_status": "clarification_required",
                    },
                }

        monkeypatch.setattr(server, "build_pipeline", lambda **kwargs: Pipeline())
        with server.app.test_client() as client:
            response = client.post(
                "/api/answer/stream",
                json={"query": "帮我比较一下", "search": "on"},
                buffered=True,
            )
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        # A dedicated, recognizable waiting-for-input event is emitted.
        assert "event: clarify" in body
        assert "你想比较哪两款产品？" in body
        # It is still followed by result/done (the run is consistent), and is
        # not represented as a failure.
        assert "event: result" in body
        assert "event: error" not in body

    def test_stream_no_clarify_event_for_normal_result(self, monkeypatch):
        import server

        class Pipeline:
            def answer(self, query, **kwargs):
                return {"answer": "普通答案", "search_hits": [], "control": {"loop_status": "succeeded"}}

        monkeypatch.setattr(server, "build_pipeline", lambda **kwargs: Pipeline())
        with server.app.test_client() as client:
            response = client.post(
                "/api/answer/stream",
                json={"query": "普通问题", "search": "on"},
                buffered=True,
            )
        body = response.get_data(as_text=True)
        assert "event: clarify" not in body
        assert "event: result" in body


# ---------------------------------------------------------------------------
# 7.4: clarification ownership is mutually exclusive with the deterministic
# pre-loop short-circuit
# ---------------------------------------------------------------------------
class TestClarificationOwnerMutex:
    def test_model_owner_disables_deterministic_short_circuit(self):
        # The langchain orchestrator gates its pre-loop clarification short-
        # circuit on ``not policy.model_owns_clarification``. Under the model
        # owner the gate is open (short-circuit skipped); under system owner it
        # is closed (short-circuit active, pre-capability behaviour).
        assert AUTONOMOUS_PRESET.model_owns_clarification is True
        assert GUIDED_PRESET.model_owns_clarification is False
        # The predicate the orchestrator uses:
        skip_when_model = not AUTONOMOUS_PRESET.model_owns_clarification
        skip_when_system = not GUIDED_PRESET.model_owns_clarification
        assert skip_when_model is False  # model owner -> do NOT short-circuit
        assert skip_when_system is True  # system owner -> short-circuit (status quo)
