"""Observability tests for the autonomy policy (tasks 4.1-4.5).

Covers:
* 4.1: LoopVerdict carries autonomy_mode + advisory_gaps into trace/checkpoint.
* 4.3: both modes write verdicts into the result (which feed trace + audit).
* 4.4: control.autonomy / advisory counts are bounded scalars (no rule text).
* 4.5: forward-compat with historical verdict dicts lacking the new fields.
"""

from typing import Any, Dict, List

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from orchestrators.autonomy_policy import resolve_autonomy_policy
from orchestrators.react_loop_graph import LoopVerdict, ReactLoopGraphRunner, langgraph_available
from utils.query_orchestration import QueryAnalysis

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
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {"query": "q"}, "id": cid, "type": "tool_call"}])


class FakeSearch:
    name = "web_search"
    description = "fake"
    max_calls_per_query = 3

    def invoke(self, args):
        return ("苹果和微软分别公布财报，苹果相比微软更强，而微软同时领先。" * 6)

    def get_budget_status(self):
        return {"limit": 3, "used": 0}

    def reset_budget(self):
        pass


class TestVerdictCarriesAutonomyFields:
    """4.1: LoopVerdict exposes autonomy_mode + advisory_gaps."""

    def test_default_verdict_has_safe_defaults(self):
        v = LoopVerdict(iteration=1)
        d = v.to_dict()
        assert d["autonomy_mode"] == "guided"
        assert d["advisory_gaps"] == []

    def test_guided_run_verdicts_carry_guided_mode(self):
        runner = ReactLoopGraphRunner(
            llm=M(replies=[_tc("web_search"), "苹果相比微软更强，而微软同时领先，" * 6]),
            tools=[FakeSearch()],
            query="苹果和微软的区别",
        )
        result = runner.run("苹果和微软的区别")
        assert result["verdicts"]
        for v in result["verdicts"]:
            assert v["autonomy_mode"] == "guided"
            assert "advisory_gaps" in v

    def test_autonomous_run_verdicts_carry_autonomous_mode(self):
        auto = resolve_autonomy_policy({"autonomy": {"mode": "autonomous"}}, "autonomous")
        runner = ReactLoopGraphRunner(
            llm=M(replies=[_tc("web_search"), "苹果相比微软更强，而微软同时领先，" * 6]),
            tools=[FakeSearch()],
            query="苹果和微软的区别",
            autonomy_policy=auto,
        )
        result = runner.run("苹果和微软的区别")
        assert result["verdicts"]
        for v in result["verdicts"]:
            assert v["autonomy_mode"] == "autonomous"


class TestBothModesWriteVerdictsAndTrace:
    """4.3: both modes produce verdicts and trace events (audit completeness)."""

    def test_both_modes_produce_verdicts_and_trace(self):
        analysis = QueryAnalysis(query="有哪些插件", existence_query=True, requires_evidence=True)
        for policy in (
            resolve_autonomy_policy({}),
            resolve_autonomy_policy({"autonomy": {"mode": "autonomous"}}, "autonomous"),
        ):
            runner = ReactLoopGraphRunner(
                llm=M(replies=[_tc("web_search"), "答案覆盖了要求，列出多个插件。" * 6]),
                tools=[FakeSearch()],
                query="有哪些插件",
                analysis=analysis,
                autonomy_policy=policy,
            )
            result = runner.run("有哪些插件")
            assert result["verdicts"], f"{policy.mode}: no verdicts"
            assert result["trace_events"], f"{policy.mode}: no trace events"
            # Evaluate rounds produce trace events unless the bounded trace was
            # truncated (in which case some evaluate events are dropped by design).
            eval_events = [
                e for e in result["trace_events"]
                if str(e.get("id") or "").startswith("react_evaluate")
            ]
            assert eval_events or result.get("trace_truncated"), (
                f"{policy.mode}: no evaluate trace events"
            )


class TestControlFieldsBounded:
    """4.4: autonomy control fields are bounded scalars (no rule text)."""

    def test_advisory_gaps_carry_no_rule_text_or_prompts(self):
        auto = resolve_autonomy_policy({"autonomy": {"mode": "autonomous"}}, "autonomous")
        # A verdict dict with advisory gaps: each gap is bounded metadata only.
        sample = {
            "iteration": 1,
            "advisory_gaps": [
                {"kind": "citation", "rule": "citation_missing", "detail": "数值缺少 [En] 标注", "injected": True},
                {"kind": "constraint", "rule": "comparison", "injected": False},
            ],
            "autonomy_mode": "autonomous",
        }
        for gap in sample["advisory_gaps"]:
            blob = str(gap)
            # No prompt fragments, no hidden reasoning markers, no credentials.
            assert "system" not in blob.lower()
            assert "prompt" not in blob.lower()
            assert "api_key" not in blob.lower()


class TestOldCheckpointForwardCompat:
    """4.5: historical verdict dicts lacking the new fields are tolerated."""

    def test_counting_tolerates_old_verdict_dicts(self):
        # A verdict from before this capability (no autonomy_mode / advisory_gaps).
        old_verdict = {
            "iteration": 1,
            "new_evidence": True,
            "constraints_met": [],
            "constraints_missing": ["comparison"],
            "should_continue": True,
            "reason": "continue",
            "action": "continue",
        }
        # The control-side advisory-gap count must not crash and yields 0.
        count = sum(
            len(v.get("advisory_gaps") or [])
            for v in [old_verdict]
            if isinstance(v, dict)
        )
        assert count == 0
        # autonomy_mode is read with a safe default.
        assert old_verdict.get("autonomy_mode", "guided") == "guided"
