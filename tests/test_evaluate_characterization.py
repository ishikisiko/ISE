"""Characterization tests for ``ReactLoopGraphRunner._evaluate``.

These lock the current (pre-refactor) decision behaviour of the unified
evaluate node before it is split into four stages (task 1.2). Each test
drives ``_evaluate`` with a hand-built loop state and asserts the four
observable decision fields: ``reason`` and ``action`` (on the produced
``LoopVerdict``) plus ``next_action`` and ``termination_reason`` (on the
returned state update).

They cover the eight mutually exclusive decision paths exercised by the
current monolithic method:

1. ``pricing_recovery``  - switch to a configured official price page
2. ``force_synthesis``   - ``late_loop_no_answer`` near the iteration cap
3. ``degraded_synthesis_force`` - degrade a hard terminal to grounded synthesis
4. ``compact_next``      - context over budget, compact before continuing
5. ``invalid_tool_request`` - malformed / unsupported tool call
6. ``invalid_final_response`` - process narration without a tool call
7. ``final_proposed`` rejected - candidate answer bounced back to the loop
8. hard terminals - exhausted / stagnated / unrecoverable

No real LLM or search calls are made: the runner is constructed without a
checkpointer and ``_evaluate`` is invoked directly on a fabricated state.
"""

from typing import Any, Dict, List, Optional

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

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


class BudgetTool:
    """A fake tool that reports a non-exhausted per-query budget."""

    def __init__(self, name: str = "web_search", limit: int = 3, used: int = 0):
        self.name = name
        self.description = "fake tool"
        self._limit = limit
        self._used = used

    def invoke(self, args):  # noqa: D401
        return "ok"

    def get_budget_status(self):
        return {"limit": self._limit, "used": self._used}


class PricingFetchTool:
    """Fake fetch_url exposing configured pricing source candidates."""

    name = "fetch_url"
    description = "fake fetch"

    @staticmethod
    def get_pricing_source_candidates(requirements=None):
        return [{"url": "https://docs.acme.example/pricing"}]

    @staticmethod
    def get_budget_status():
        return {"limit": 3, "used": 0}


def _runner(
    *,
    query: str = "有哪些插件",
    max_iterations: int = 5,
    analysis: Optional[QueryAnalysis] = None,
    termination_config: Optional[Dict[str, Any]] = None,
    tools: Optional[List[Any]] = None,
    compaction: Optional[Dict[str, Any]] = None,
) -> ReactLoopGraphRunner:
    return ReactLoopGraphRunner(
        llm=NativeScriptedChatModel(replies=["unused"]),
        tools=tools or [],
        max_iterations=max_iterations,
        query=query,
        analysis=analysis,
        termination_config=termination_config,
        context_compaction_config=compaction,
    )


def _base_state(runner: ReactLoopGraphRunner, **overrides) -> Dict[str, Any]:
    state = runner._build_initial_state("q")
    state.update(overrides)
    return state


def _evaluate_fields(runner: ReactLoopGraphRunner, state: Dict[str, Any]) -> Dict[str, Any]:
    """Run ``_evaluate`` and return the four observed decision fields."""
    update = runner._evaluate(dict(state))
    verdict = update["verdicts"][-1]
    return {
        "reason": verdict["reason"],
        "action": verdict["action"],
        "next_action": update.get("next_action"),
        "termination_reason": update.get("termination_reason"),
    }


_EXISTENCE_ANALYSIS = QueryAnalysis(
    query="有哪些插件",
    existence_query=True,
    requires_evidence=True,
)


class TestEvaluatePricingRecovery:
    def test_pricing_recovery_routes_to_pricing_fetch(self):
        analysis = QueryAnalysis(
            query="price",
            claim_classes=["numeric", "pricing"],
            constraints={"authority_required": True},
            numeric_requirements={
                "operation": "pricing_total",
                "required_rates": ["input", "output"],
                "subject": "Acme",
            },
        )
        runner = _runner(
            query="price",
            max_iterations=5,
            analysis=analysis,
            tools=[PricingFetchTool()],
        )
        state = _base_state(
            runner,
            iteration=1,
            final_proposed=False,
            had_successful_observation=True,
            last_round_new_evidence=True,
            evidence_pool=["some"],
            evidence_records=[{"content": "some", "source_tier": "unknown", "metadata": {}}],
            messages=[HumanMessage(content="q"), AIMessage(content="", tool_calls=[])],
        )

        fields = _evaluate_fields(runner, state)

        assert fields["reason"] == "pricing_source_recovery"
        assert fields["action"] == "pricing_fetch"
        assert fields["next_action"] == "pricing_fetch"
        assert fields["termination_reason"] is None


class TestEvaluateLateLoopForceSynthesis:
    def test_late_loop_no_answer_forces_synthesis(self):
        runner = _runner(
            query="有哪些插件",
            max_iterations=5,
            analysis=_EXISTENCE_ANALYSIS,
            tools=[BudgetTool()],
        )
        state = _base_state(
            runner,
            iteration=4,
            final_proposed=False,
            had_successful_observation=True,
            last_round_new_evidence=True,
            evidence_pool=["pluginA"],
            evidence_records=[{"content": "pluginA", "source_tier": "unknown", "metadata": {}}],
            messages=[HumanMessage(content="q"), AIMessage(content="", tool_calls=[])],
        )

        fields = _evaluate_fields(runner, state)

        assert fields["reason"] == "forced_synthesis"
        assert fields["action"] == "synthesize"
        assert fields["next_action"] == "synthesize"
        assert fields["termination_reason"] is None


class TestEvaluateDegradedSynthesis:
    def test_degraded_synthesis_force_when_terminal_with_evidence(self):
        runner = _runner(
            query="有哪些插件",
            max_iterations=2,
            analysis=_EXISTENCE_ANALYSIS,
            termination_config={"degraded_synthesis": True},
        )
        content = "插件A 可用 " * 20
        state = _base_state(
            runner,
            iteration=2,
            final_proposed=False,
            had_successful_observation=True,
            evidence_pool=[content],
            evidence_records=[{"content": content, "source_tier": "official", "metadata": {}}],
        )

        fields = _evaluate_fields(runner, state)

        assert fields["reason"] == "degraded_synthesis"
        assert fields["action"] == "synthesize"
        assert fields["next_action"] == "synthesize"
        assert fields["termination_reason"] is None


class TestEvaluateCompactNext:
    def test_compact_next_when_context_over_budget(self):
        runner = _runner(
            query="有哪些插件",
            max_iterations=8,
            analysis=_EXISTENCE_ANALYSIS,
            tools=[BudgetTool()],
            compaction={"enabled": True, "threshold": 0.1, "max_compactions_per_run": 5},
        )
        # The compaction infrastructure (token ratios, thresholds) is exercised
        # elsewhere; here we isolate the compact_next *decision branch* by
        # forcing the over-budget predicate and disabling debounce.
        runner._can_compact = lambda state: True  # type: ignore[method-assign]
        runner._compaction_debounced = lambda state, **kwargs: False  # type: ignore[method-assign]
        state = _base_state(
            runner,
            iteration=1,
            final_proposed=False,
            had_successful_observation=True,
            last_round_new_evidence=True,
            evidence_pool=["pluginA"],
            evidence_records=[{"content": "pluginA", "source_tier": "unknown", "metadata": {}}],
            messages=[HumanMessage(content="q"), AIMessage(content="", tool_calls=[])],
        )

        fields = _evaluate_fields(runner, state)

        assert fields["reason"] == "context_compaction"
        assert fields["action"] == "compact"
        assert fields["next_action"] == "compact"
        assert fields["termination_reason"] is None


class TestEvaluateInvalidToolRequest:
    def test_invalid_tool_request_injects_feedback(self):
        runner = _runner(max_iterations=5)
        state = _base_state(
            runner,
            iteration=1,
            invalid_tool_request="unsupported_tool: foo",
            messages=[HumanMessage(content="q"), AIMessage(content="")],
        )

        fields = _evaluate_fields(runner, state)

        assert fields["reason"] == "invalid_tool_request"
        assert fields["action"] == "continue"
        assert fields["next_action"] == "act"
        assert fields["termination_reason"] is None
        # The recovery feedback is injected into the message stream.
        update = runner._evaluate(dict(state))
        assert any(
            isinstance(message, HumanMessage)
            and "未被执行" in message.content
            for message in update.get("messages") or []
        )


class TestEvaluateInvalidFinalResponse:
    def test_invalid_final_response_injects_feedback(self):
        runner = _runner(max_iterations=5)
        state = _base_state(
            runner,
            iteration=1,
            invalid_final_response="search_plan_text",
            final_proposed=True,
            messages=[HumanMessage(content="q"), AIMessage(content="")],
        )

        fields = _evaluate_fields(runner, state)

        assert fields["reason"] == "process_narration"
        assert fields["action"] == "continue"
        assert fields["next_action"] == "act"
        assert fields["termination_reason"] is None


class TestEvaluateFinalProposedRejected:
    def test_final_proposal_rejected_when_constraints_missing(self):
        analysis = QueryAnalysis(
            query="苹果和微软的区别",
            constraints={"comparison_required": True},
        )
        runner = _runner(
            query="苹果和微软的区别",
            max_iterations=5,
            analysis=analysis,
            tools=[BudgetTool()],
        )
        state = _base_state(
            runner,
            iteration=1,
            final_proposed=True,
            had_successful_observation=True,
            evidence_pool=["苹果强"],
            evidence_records=[{"content": "苹果强", "source_tier": "unknown", "metadata": {}}],
            messages=[HumanMessage(content="q"), AIMessage(content="短答案")],
        )

        fields = _evaluate_fields(runner, state)

        assert fields["reason"] == "final_answer_rejected"
        assert fields["action"] == "continue"
        assert fields["next_action"] == "act"
        assert fields["termination_reason"] is None


class TestEvaluateHardTerminals:
    def test_exhausted_terminal(self):
        # A comparison query leaves ``comparison`` in the checklist, so the
        # late-loop force-synthesis branch (which requires satisfied
        # constraints) does not pre-empt the budget-exhaustion terminal.
        analysis = QueryAnalysis(
            query="苹果和微软的区别",
            constraints={"comparison_required": True},
        )
        runner = _runner(
            query="苹果和微软的区别",
            max_iterations=3,
            analysis=analysis,
            termination_config={"degraded_synthesis": False},
        )
        state = _base_state(
            runner,
            iteration=3,
            had_successful_observation=True,
            evidence_pool=["x"],
            evidence_records=[{"content": "x", "source_tier": "unknown", "metadata": {}}],
        )

        fields = _evaluate_fields(runner, state)

        assert fields["reason"] == "exhausted"
        assert fields["action"] == "exhausted"
        assert fields["next_action"] == "end"
        assert fields["termination_reason"] == "exhausted"

    def test_stagnated_terminal(self):
        runner = _runner(
            query="有哪些插件",
            max_iterations=8,
            analysis=_EXISTENCE_ANALYSIS,
            termination_config={"degraded_synthesis": False, "no_progress_threshold": 2},
        )
        state = _base_state(
            runner,
            iteration=3,
            final_proposed=False,
            no_progress_streak=2,
            had_successful_observation=True,
            evidence_pool=["x"],
            evidence_records=[{"content": "x", "source_tier": "unknown", "metadata": {}}],
            messages=[HumanMessage(content="q"), AIMessage(content="", tool_calls=[])],
        )

        fields = _evaluate_fields(runner, state)

        assert fields["reason"] == "stagnated"
        assert fields["action"] == "stagnated"
        assert fields["next_action"] == "end"
        assert fields["termination_reason"] == "stagnated"

    def test_unrecoverable_terminal(self):
        runner = _runner(
            query="有哪些插件",
            max_iterations=8,
            analysis=_EXISTENCE_ANALYSIS,
            termination_config={"tool_error_threshold": 2, "degraded_synthesis": False},
        )
        state = _base_state(
            runner,
            iteration=2,
            final_proposed=False,
            tool_error_streak=2,
            had_successful_observation=False,
        )

        fields = _evaluate_fields(runner, state)

        assert fields["reason"] == "unrecoverable"
        assert fields["action"] == "unrecoverable"
        assert fields["next_action"] == "end"
        assert fields["termination_reason"] == "unrecoverable"
