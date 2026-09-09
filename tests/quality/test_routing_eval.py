"""Q6-06: routing metrics on hand-built records; dataset carries the tool columns."""
from __future__ import annotations

import pytest

from tests.quality.routing_eval import evaluate_record, sequence_admissible, summarize, validate_datasets

pytestmark = pytest.mark.quality_offline


def _record(tools, *, skill=None, status="succeeded"):
    control = {
        "search_mode": "agentic_loop",
        "execution_trace": {"events": [{"kind": "tool_call", "tool": tool, "iteration": i + 1, "query": f"q{i if tool != 'web_search' else 0}"} for i, tool in enumerate(tools)]},
        "termination_policy": {"tool_budgets": {"web_search": {"limit": 3, "used": tools.count("web_search")}}},
        "loop_status": status,
    }
    if skill:
        control["skill_tools_used"] = [skill]
    return {"qid": "r", "query": "q", "control": control}


def test_dataset_has_tool_columns():
    assert validate_datasets()["ok"] is True


def test_sequence_patterns():
    assert sequence_admissible(["weather_conditions", "web_search"], "weather_conditions(,web_search)*")
    assert sequence_admissible([], "none")
    assert not sequence_admissible(["web_search"], "none")
    assert sequence_admissible(["web_search", "fetch_url", "fetch_url"], "web_search(,fetch_url|search_recovery)*")
    assert sequence_admissible([], "none|web_search") and sequence_admissible(["web_search"], "none|web_search")


def test_routing_metrics():
    weather_row = {"expected_route": "weather_api", "intent_label": "weather", "allowed_first_tools": "weather_conditions", "allowed_tool_sequences": "weather_conditions(,web_search)*", "route_coverage_gap": "0", "needs_web_evidence": "0"}
    chat_row = {"expected_route": "chat", "intent_label": "small_talk", "allowed_first_tools": "none", "allowed_tool_sequences": "none", "route_coverage_gap": "0", "needs_web_evidence": "0"}
    calc_row = {"expected_route": "calculator", "intent_label": "math", "allowed_first_tools": "none", "allowed_tool_sequences": "none", "route_coverage_gap": "1", "needs_web_evidence": "0"}
    web_row = {"expected_route": "general_web", "intent_label": "general_knowledge", "allowed_first_tools": "web_search|search_recovery|fetch_url", "allowed_tool_sequences": "web_search(,fetch_url|search_recovery)*", "route_coverage_gap": "0", "needs_web_evidence": "1"}
    good_weather = evaluate_record(_record(["weather_conditions"], skill="weather_conditions"), weather_row, None)
    bad_weather = evaluate_record(_record(["web_search", "web_search", "web_search"]), weather_row, None)
    chat = evaluate_record(_record(["web_search"]), chat_row, None)
    calc = evaluate_record(_record([]), calc_row, None)
    web_missing = evaluate_record(_record([]), web_row, {"need_fulltext": "True"})
    web_ok = evaluate_record(_record(["web_search", "fetch_url"]), web_row, {"need_fulltext": "True"})
    assert good_weather["route_correct"] and good_weather["first_tool_correct"] and good_weather["tool_sequence_admissible"]
    assert bad_weather["unnecessary_search"] is True and bad_weather["first_tool_correct"] is False
    assert bad_weather["redundant_call_rate"] == pytest.approx(2 / 3) and bad_weather["budget_hits"] == ["web_search"]
    assert chat["unnecessary_search"] is True and chat["used_web"] is True
    assert calc["route_coverage_gap"] is True
    assert web_missing["missing_search"] is True and web_missing["fulltext_decision_correct"] is False
    assert web_ok["fulltext_decision_correct"] is True and web_ok["missing_search"] is False
    summary = summarize([good_weather, bad_weather, chat, calc, web_missing, web_ok])
    assert summary["route_coverage_gap"]["count"] == 1
    assert summary["unnecessary_search_rate_small_talk"]["rate"] == 1.0
    assert summary["missing_search_rate"]["rate"] == 0.5
    assert summary["fulltext_decision_correct"]["rate"] == 0.5
    assert summary["budget_hit_rate"]["positives"] == 1
