"""Focused coverage for the bounded ReAct context implementation."""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool as lc_tool

from evidence.ledger import EvidenceLedger
from langchain.langchain_react_tools import ReActRecallEvidenceTool
from orchestrators.context_compaction import (
    TokenBudget,
    approximate_tokens,
    assert_tool_call_pairing,
    fold_evidence_messages,
    normalize_context_compaction_config,
    partition,
    render_decision_trace,
    resolve_context_window,
    summarize,
    unknown_summary_evidence_ids,
)
from orchestrators.react_loop_graph import ReactLoopGraphRunner


class ScriptedModel(BaseChatModel):
    replies: List[Any]
    calls: int = 0
    seen_kwargs: List[Dict[str, Any]] = []

    @property
    def _llm_type(self) -> str:
        return "context-compaction-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen_kwargs.append(dict(kwargs))
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        if isinstance(reply, Exception):
            raise reply
        message = reply if isinstance(reply, AIMessage) else AIMessage(content=str(reply))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools, **kwargs):
        return self


def _call(call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        id=f"ai-{call_id}",
        tool_calls=[
            {
                "name": "web_search",
                "args": {"query": call_id},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def _record(ledger: EvidenceLedger, index: int, body: str) -> Dict[str, Any]:
    record = {
        "source_type": "web",
        "source_tier": "official",
        "reference": f"https://example.test/{index}",
        "title": f"Evidence {index}",
        "content": body,
        "metadata": {"retrieval_kind": "fetch_url", "content_chars": len(body)},
    }
    record["metadata"]["eid"] = ledger.register(record)
    return record


def _state(messages, records, **extra):
    base = {
        "messages": messages,
        "evidence_records": records,
        "evidence_pool": [],
        "fetch_outcomes": [],
        "verdicts": [],
        "constraints_missing": [],
        "compactions": 0,
        "iteration": 3,
        "peak_context_ratio": 0.0,
        "token_budget_state": {},
        "forced_synthesis": False,
    }
    base.update(extra)
    return base


def test_safe_cut_moves_before_a_split_tool_turn() -> None:
    messages = [
        HumanMessage(content="question"),
        _call("c1"),
        ToolMessage(content="one", tool_call_id="c1", id="tool-c1"),
        _call("c2"),
        ToolMessage(content="two", tool_call_id="c2", id="tool-c2"),
    ]

    assert ReactLoopGraphRunner._safe_cut_index(messages, 2) == 1
    assert_tool_call_pairing(messages)
    with pytest.raises(AssertionError, match="orphaned tool calls"):
        assert_tool_call_pairing(messages[:-1])


def test_token_budget_uses_measurement_then_calibrated_increment() -> None:
    messages = [HumanMessage(content="中文内容" * 20)]
    budget = TokenBudget(system_prompt="system", max_tokens=64, context_window=1000)
    assert budget.estimate(messages) > 0

    assert budget.record_usage(messages, {"input_tokens": 200})
    assert budget.estimate(messages) == 200 + budget.reserve

    expanded = messages + [AIMessage(content="more text" * 40)]
    expected = round(
        200
        + budget.calibration
        * (approximate_tokens(expanded) - approximate_tokens(messages))
        + budget.reserve
    )
    assert budget.estimate(expanded) == expected

    calibrated = TokenBudget(system_prompt="system")
    for multiplier in (2.4, 2.6):
        approx = approximate_tokens(expanded) + approximate_tokens([SystemMessage(content="system")])
        calibrated.record_usage(expanded, {"input_tokens": int(approx * multiplier)})
    assert abs(calibrated.calibration - 2.5) / 2.5 < 0.15


def test_context_window_resolution_and_missing_usage_fallback() -> None:
    config = normalize_context_compaction_config(
        {"context_window": 4096, "per_model_window": {"model-a": 8192}}
    )
    assert resolve_context_window(config, "model-a") == 8192
    assert resolve_context_window(config, "other") == 4096
    assert resolve_context_window({}, "other") == 128000
    assert TokenBudget().estimate([HumanMessage(content="no usage")]) > 0


def test_ledger_headers_and_pointerization_keep_pairing_and_content() -> None:
    ledger = EvidenceLedger()
    record = _record(ledger, 1, "confidential full evidence body")
    header = ledger.render_header(1)
    assert "confidential full evidence body" not in header
    assert "https://example.test/1" in header

    messages = [_call("c1"), ToolMessage(
        content=ledger.render_entry(1), tool_call_id="c1", id="tool-c1"
    )]
    folded = fold_evidence_messages(messages, ledger, [record])
    assert folded[1].id == "tool-c1"
    assert "confidential full evidence body" not in folded[1].content
    assert "recall_evidence" in folded[1].content
    assert_tool_call_pairing(folded)


def test_partition_and_trace_preserve_recent_rounds_and_reasons() -> None:
    messages = [HumanMessage(content="question")]
    for index in range(1, 4):
        messages.extend([
            _call(f"c{index}"),
            ToolMessage(content=f"result {index}", tool_call_id=f"c{index}", id=f"tool-{index}"),
        ])
    messages.append(AIMessage(content="final answer"))
    parts = partition(messages, keep_recent_rounds=1)
    assert parts.pinned[0].content == "question"
    assert parts.compressible
    assert_tool_call_pairing(parts.pinned + parts.compressible + parts.recent)

    trace = render_decision_trace(
        {
            "messages": messages,
            "fetch_outcomes": [
                {"url": "https://failed.example", "status": "no_data", "error_type": "timeout"}
            ],
            "verdicts": [{"iteration": 2, "reason": "continue", "rule_hits": [{"detail": "need official rate"}]}],
            "constraints_missing": ["official_price"],
            "context_budget": 99,
            "context_ratio": 0.5,
        }
    )
    assert "https://failed.example" in trace
    assert "timeout" in trace
    assert "need official rate" in trace

    blocked = partition([HumanMessage(content="question")], keep_recent_rounds=2)
    assert blocked.blocked is True
    assert blocked.compressible == []


def test_recall_evidence_is_local_and_budgeted() -> None:
    ledger = EvidenceLedger()
    _record(ledger, 1, "stored full text")
    tool = ReActRecallEvidenceTool(max_calls_per_query=1)
    tool.set_ledger(ledger)

    payload = json.loads(tool.invoke({"evidence_ids": ["[E1]", "E99"]}))
    assert payload["status"] == "ok"
    assert payload["entries"][0]["entry"].endswith("stored full text")
    assert payload["entries"][1]["status"] == "not_found"
    rejected = json.loads(tool.invoke({"evidence_ids": ["E1"]}))
    assert rejected["status"] == "rejected"

    single = ReActRecallEvidenceTool()
    single.set_ledger(ledger)
    assert json.loads(single.invoke({"evidence_ids": "E1"}))["status"] == "ok"


def test_summary_uses_judge_first_and_never_includes_raw_tool_body() -> None:
    primary = ScriptedModel(replies=["primary"])
    judge = ScriptedModel(replies=["judge summary"])
    ledger = EvidenceLedger()
    record = _record(ledger, 1, "raw tool body must not enter prompt")
    span = [ToolMessage(content="raw tool body must not enter prompt", tool_call_id="c", id="t")]
    result = summarize(
        {"messages": [], "fetch_outcomes": [], "verdicts": [], "constraints_missing": []},
        span,
        llm=primary,
        judge_llm=judge,
        use_judge_llm=True,
        summary_max_tokens=77,
        ledger=ledger,
        evidence_records=[record],
    )
    assert result == "judge summary"
    assert primary.calls == 0
    assert judge.seen_kwargs[-1]["temperature"] == 0
    assert judge.seen_kwargs[-1]["max_tokens"] == 77


def test_summary_rejects_citations_that_are_not_in_the_ledger() -> None:
    ledger = EvidenceLedger()
    record = _record(ledger, 1, "known body")
    assert unknown_summary_evidence_ids("Known [E1], invented [E99]", ledger, [record]) == [99]
    with pytest.raises(RuntimeError, match="unknown evidence: E99"):
        summarize(
            {"messages": [], "fetch_outcomes": [], "verdicts": [], "constraints_missing": []},
            [],
            llm=ScriptedModel(replies=["only [E99]"]),
            judge_llm=None,
            use_judge_llm=False,
            summary_max_tokens=77,
            ledger=ledger,
            evidence_records=[record],
        )


def test_compact_uses_tier_one_without_summary_call_when_it_is_enough() -> None:
    ledger = EvidenceLedger()
    records = [_record(ledger, index, "body " * 220) for index in range(1, 4)]
    messages = [HumanMessage(content="question", id="u")]
    for index, record in enumerate(records, start=1):
        call_id = f"c{index}"
        messages.extend([
            _call(call_id),
            ToolMessage(content=ledger.render_entry(index), tool_call_id=call_id, id=f"t{index}"),
        ])
    messages.append(AIMessage(content="draft [E1]", id="final"))
    model = ScriptedModel(replies=["should not be called"])
    runner = ReactLoopGraphRunner(
        llm=model,
        tools=[],
        ledger=ledger,
        context_compaction_config={
            "enabled": True,
            "context_window": 3000,
            "threshold": 0.5,
            "keep_recent_rounds": 1,
        },
    )
    update = runner._compact(_state(messages, records), force=True)
    assert update["summary_source"] == "deterministic"
    assert model.calls == 0
    compacted = [message for message in update["messages"] if not isinstance(message, RemoveMessage)]
    assert_tool_call_pairing(compacted)


def test_compact_falls_back_to_truncate_when_both_summary_paths_fail(monkeypatch) -> None:
    ledger = EvidenceLedger()
    records = [_record(ledger, index, "body " * 320) for index in range(1, 4)]
    messages = [HumanMessage(content="question")]
    for index in range(1, 4):
        messages.extend([
            _call(f"c{index}"),
            ToolMessage(content=ledger.render_entry(index), tool_call_id=f"c{index}", id=f"t{index}"),
        ])
        if index == 1:
            messages.append(AIMessage(content="earlier final answer", id="earlier-final"))
    runner = ReactLoopGraphRunner(
        llm=ScriptedModel(replies=[RuntimeError("summary failed")]),
        tools=[],
        ledger=ledger,
        context_compaction_config={
            "enabled": True,
            "context_window": 100,
            "threshold": 0.1,
            "keep_recent_rounds": 1,
        },
    )
    monkeypatch.setattr(
        "orchestrators.react_loop_graph.deterministic_summary",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("trace failed")),
    )
    update = runner._compact(_state(messages, records), force=True)
    assert update["summary_source"] == "truncate"
    compacted = [message for message in update["messages"] if not isinstance(message, RemoveMessage)]
    assert compacted[0].content == "question"
    assert any(
        isinstance(message, AIMessage) and message.content == "earlier final answer"
        for message in compacted
    )
    assert_tool_call_pairing(compacted)


def test_summary_failure_uses_deterministic_human_message_without_incrementing_iteration() -> None:
    ledger = EvidenceLedger()
    records = [_record(ledger, index, "body " * 320) for index in range(1, 4)]
    messages = [HumanMessage(content="question")]
    for index in range(1, 4):
        messages.extend([
            _call(f"c{index}"),
            ToolMessage(content=ledger.render_entry(index), tool_call_id=f"c{index}", id=f"t{index}"),
        ])
    runner = ReactLoopGraphRunner(
        llm=ScriptedModel(replies=[RuntimeError("summary failed")]),
        tools=[],
        ledger=ledger,
        context_compaction_config={
            "enabled": True,
            "context_window": 100,
            "threshold": 0.1,
            "keep_recent_rounds": 1,
        },
    )
    update = runner._compact(_state(messages, records, iteration=7), force=True)
    assert update["summary_source"] == "deterministic"
    assert "iteration" not in update
    summary = next(
        message
        for message in update["messages"]
        if isinstance(message, HumanMessage)
        and str(message.content).startswith("[上下文摘要]")
    )
    assert isinstance(summary, HumanMessage)


def test_evidence_pool_is_bounded_without_dropping_evidence_records() -> None:
    @lc_tool("web_search")
    def search(query: str) -> str:
        """Return one deterministic observation."""
        return "new observation"

    runner = ReactLoopGraphRunner(
        llm=ScriptedModel(replies=["unused"]),
        tools=[search],
        context_compaction_config={"evidence_pool_max_entries": 2},
    )
    state = _state(
        [_call("c1")],
        [],
        evidence_pool=["old one", "old two"],
        tool_error_streak=0,
        had_successful_observation=False,
        seen_fingerprints=[],
        fingerprint_streak=0,
        last_fingerprint=None,
    )
    update = runner._observe(state)
    assert update["evidence_pool"] == ["old two", "new observation"]
    assert len(update["evidence_records"]) == 1


def test_compact_route_is_disabled_but_terminal_still_wins() -> None:
    disabled = ReactLoopGraphRunner(
        llm=ScriptedModel(replies=["unused"]),
        tools=[],
        context_compaction_config={"enabled": False, "context_window": 1},
    )
    state = _state([HumanMessage(content="long context" * 100)], [])
    assert disabled._can_compact(state) is False
    assert disabled._route_after_evaluate(
        {"termination_reason": "succeeded", "next_action": "compact"}
    ) == "end"
    assert disabled._route_after_evaluate(
        {"termination_reason": None, "next_action": "compact"}
    ) == "compact"


def test_blocked_compaction_returns_through_act_before_synthesis() -> None:
    runner = ReactLoopGraphRunner(
        llm=ScriptedModel(replies=["unused"]),
        tools=[],
        context_compaction_config={"enabled": True, "context_window": 1},
    )
    blocked = runner._compact(_state([HumanMessage(content="question")], []), force=True)
    assert blocked["force_synthesis"] is True
    assert runner._route_after_act(blocked) == "synthesize"
    assert runner._act(blocked) == {"next_action": "synthesize"}

    graph_state = runner._build_initial_state("question")
    graph_state["force_synthesis"] = True
    completed = runner.build_graph().invoke(graph_state, config={"recursion_limit": 20})
    assert completed["forced_synthesis"] is True
    assert completed["termination_reason"] == "succeeded"

    state = _state(
        [HumanMessage(content="question")],
        [],
        compactions=1,
        iteration=3,
        last_compaction_iteration=2,
        tokens_at_last_compaction=100,
        context_budget=1,
    )
    assert runner._compaction_debounced(state, current_budget=101) is True
    assert runner._compaction_debounced(state, current_budget=99) is False


def test_compaction_trace_exposes_only_sanitized_metrics() -> None:
    runner = ReactLoopGraphRunner(llm=ScriptedModel(replies=["unused"]), tools=[])
    runner._trace_compaction(
        sequence=1,
        before_messages=9,
        after_messages=4,
        before_budget=9_001,
        after_budget=1_234,
        summary_source="token=should-not-leak",
    )
    event = next(
        event
        for event in runner.tracer.events
        if event["id"] == "react_compact_1" and event["status"] == "done"
    )
    values = {item["label"]: item["value"] for item in event["items"]}
    assert set(values) == {"消息", "预算", "summary_source"}
    assert values["消息"] == "9 -> 4"
    assert values["预算"] == "9001/128000 -> 1234/128000"
    assert "should-not-leak" not in str(event)
    assert "token=[redacted]" in values["summary_source"]
