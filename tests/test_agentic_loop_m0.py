"""M0 regression tests retained for token instrumentation and baseline metrics.

These cover the three pieces of M0 groundwork that must hold as the agentic
loop rolls out:

- the obsolete rollout switch is absent after M5;
- ``extract_token_usage`` reads the token fields providers actually emit;
- the baseline runner's deterministic metric helpers (routing inference,
  fact coverage, latency summaries) behave as the roadmap §5 table requires.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from tests.baseline_runner import (
    build_answer_summary,
    build_route_summary,
    extract_llm_stats,
    fact_clause_covered,
    fact_clause_overlap,
    infer_route,
    score_answer_quality,
    significant_terms,
    summarise,
    summarise_rate,
)
from utils.timing_utils import TimingRecorder, extract_token_usage


class _UsageResponse:
    """Minimal stand-in for a LangChain ``AIMessage``."""

    def __init__(
        self,
        *,
        usage_metadata: Optional[Dict[str, Any]] = None,
        response_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.usage_metadata = usage_metadata
        self.response_metadata = response_metadata or {}


# ---------------------------------------------------------------------------
# M5 removal of the rollout switch
# ---------------------------------------------------------------------------


def test_engine_mode_switch_is_absent_from_shipped_configs() -> None:
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for name in ("config.json", "config.example.json"):
        config = json.loads((root / name).read_text(encoding="utf-8"))
        assert "engine" not in config
        assert "reactAgent" not in config
        assert "postcheck" not in config

# ---------------------------------------------------------------------------
# token instrumentation
# ---------------------------------------------------------------------------


def test_extract_token_usage_from_usage_metadata() -> None:
    response = _UsageResponse(
        usage_metadata={"input_tokens": 120, "output_tokens": 45, "total_tokens": 165}
    )
    assert extract_token_usage(response) == {
        "input_tokens": 120,
        "output_tokens": 45,
        "total_tokens": 165,
    }


def test_extract_token_usage_from_response_metadata_aliases() -> None:
    response = _UsageResponse(
        response_metadata={
            "token_usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
        }
    )
    assert extract_token_usage(response) == {
        "input_tokens": 7,
        "output_tokens": 3,
        "total_tokens": 10,
    }


def test_extract_token_usage_handles_camel_case_and_totals() -> None:
    response = _UsageResponse(
        response_metadata={"usage": {"inputTokens": 10, "outputTokens": 5}}
    )
    assert extract_token_usage(response) == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    }


def test_extract_token_usage_returns_none_when_absent() -> None:
    assert extract_token_usage(None) is None
    assert extract_token_usage(_UsageResponse()) is None
    assert extract_token_usage(_UsageResponse(response_metadata={"unrelated": {}})) is None


def test_record_llm_call_threads_token_extra_into_payload() -> None:
    recorder = TimingRecorder(enabled=True)
    recorder.start()
    recorder.record_llm_call(
        label="search_rag_answer",
        duration_ms=12.0,
        extra={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    )
    payload = recorder.to_dict()
    assert payload["llm_calls"][0]["total_tokens"] == 120
    assert payload["llm_calls"][0]["input_tokens"] == 100


def test_universal_chat_model_extracts_usage_and_attaches_to_message() -> None:
    """The chat model must put provider usage onto the AIMessage so timing/

    audit can read cost. Without this attachment the token instrumentation
    at call sites captures nothing (the M0 baseline smoke run surfaced this).
    """

    from langchain.langchain_llm import UniversalChatModel

    model = UniversalChatModel.__new__(UniversalChatModel)
    # OpenAI-style payload
    openai_usage = model._extract_usage(
        {"usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}}
    )
    assert openai_usage == {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}
    # Anthropic-style payload (no total_tokens field → derived)
    anthropic_usage = model._extract_usage({"usage": {"input_tokens": 4, "output_tokens": 6}})
    assert anthropic_usage == {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10}
    # Nested under message
    nested = model._extract_usage({"message": {"usage": {"input_tokens": 1, "output_tokens": 2}}})
    assert nested == {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
    assert model._extract_usage({"choices": []}) is None
    assert model._extract_usage({}) is None


# ---------------------------------------------------------------------------
# baseline metric helpers
# ---------------------------------------------------------------------------


def test_significant_terms_drops_stopwords_and_punctuation() -> None:
    assert significant_terms("larger than Mercury and Pluto!") == [
        "larger",
        "mercury",
        "pluto",
    ]


def test_fact_clause_covered_requires_all_significant_terms() -> None:
    assert fact_clause_covered("larger than Mercury and Pluto", "mercury and pluto are smaller, larger") is True
    assert fact_clause_covered("larger than Mercury", "only mercury mentioned") is False


def test_fact_clause_overlap_gives_partial_credit() -> None:
    # Three significant terms (than/and are stopwords); two present → 0.667.
    # Partial credit keeps the scorer from collapsing to zero when a synonym
    # or unit is phrased differently.
    assert fact_clause_overlap("larger than Mercury and Pluto", "larger than mercury, neptune") == pytest.approx(2 / 3)
    assert fact_clause_overlap("larger than Mercury and Pluto", "larger than mercury and pluto here") == 1.0
    assert fact_clause_overlap("larger than Mercury and Pluto", "nothing relevant") == 0.0


def test_score_answer_quality_partial_coverage() -> None:
    result = {"answer": "Ganymede is a moon of Jupiter and the largest moon."}
    quality = score_answer_quality(
        result,
        "Ganymede is a moon of Jupiter; largest moon; larger than Mercury and Pluto",
    )
    assert quality["fact_clauses_total"] == 3
    # First two clauses fully match; the third (larger/mercury/pluto) is absent.
    assert quality["fact_clauses_covered"] == 2
    assert quality["fact_coverage"] == pytest.approx((1.0 + 1.0 + 0.0) / 3)
    assert quality["has_answer"] is True


def test_score_answer_quality_marks_empty_or_error_answers() -> None:
    empty = score_answer_quality({"answer": ""}, "fact one")
    assert empty["has_answer"] is False
    errored = score_answer_quality({"answer": "x", "llm_error": "boom"}, "fact one")
    assert errored["has_answer"] is False


@pytest.mark.parametrize(
    "control, expected",
    [
        ({"search_mode": "small_talk"}, "chat"),
        ({"decision": {"reason": "small_talk_heuristic"}}, "chat"),
        ({"search_mode": "skill", "tool": "weather_conditions"}, "weather_api"),
        ({"search_mode": "skill", "tool": "finance_market_data"}, "finance_api"),
        ({"search_mode": "skill", "tool": "nearby_places"}, "location_api"),
        ({"search_mode": "skill", "tool": "route_directions"}, "transportation_api"),
        ({"search_mode": "skill", "tool": "sports_schedule"}, "sports_api"),
        ({"search_mode": "search", "search_performed": True, "domain": "general"}, "general_web"),
        ({"search_mode": "direct_llm"}, "general_web"),
        ({}, "general_web"),
    ],
)
def test_infer_route_projects_control_onto_dataset_vocabulary(control: Dict[str, Any], expected: str) -> None:
    assert infer_route(control) == expected


def test_infer_route_web_search_with_domain_hint_is_not_a_skill_hit() -> None:
    # A finance hint that fell through to web search must count as web, not
    # finance_api — otherwise the baseline would hide the routing miss.
    assert (
        infer_route({"search_mode": "search", "search_performed": True, "domain": "finance"}) == "general_web"
    )


def test_extract_llm_stats_sums_tokens_and_counts_capture_rate() -> None:
    stats = extract_llm_stats(
        {
            "response_times": {
                "llm_calls": [
                    {"label": "a", "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                    {"label": "b"},  # no token data
                ]
            }
        }
    )
    assert stats == {
        "llm_call_count": 2,
        "llm_calls_with_tokens": 1,
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "peak_input_tokens": 10,
    }


def test_extract_llm_stats_handles_missing_response_times() -> None:
    stats = extract_llm_stats({})
    assert stats["llm_call_count"] == 0
    assert stats["total_tokens"] == 0


def test_summarise_computes_mean_and_percentiles() -> None:
    summary = summarise([1.0, 2.0, 3.0, 4.0, 5.0])
    assert summary["mean"] == pytest.approx(3.0)
    assert summary["p50"] == pytest.approx(3.0)
    assert summary["p95"] == pytest.approx(4.8)
    assert summary["min"] == 1.0
    assert summary["max"] == 5.0
    assert summary["denominator"] == 5


def test_summarise_rate_reports_positive_share() -> None:
    assert summarise_rate([True, True, False, None])["rate"] == pytest.approx(2 / 3)


def test_build_route_summary_aggregates_accuracy_and_tokens() -> None:
    details = [
        {
            "expected_route": "chat",
            "inferred_route": "chat",
            "route_correct": True,
            "latency_ms": 100.0,
            "llm_call_count": 1,
            "llm_calls_with_tokens": 1,
            "input_tokens": 5,
            "output_tokens": 3,
            "total_tokens": 8,
            "external_api_calls": 0,
        },
        {
            "expected_route": "weather_api",
            "inferred_route": "general_web",
            "route_correct": False,
            "latency_ms": 300.0,
            "llm_call_count": 2,
            "llm_calls_with_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "external_api_calls": 1,
        },
    ]
    summary = build_route_summary(details)
    assert summary["rows"] == 2
    assert summary["route_accuracy"]["rate"] == pytest.approx(0.5)
    assert summary["latency_ms"]["p50"] == pytest.approx(200.0)
    assert summary["total_tokens_per_query"]["mean"] == pytest.approx(4.0)
    assert summary["confusion_matrix"]["weather_api"]["general_web"] == 1


def test_build_answer_summary_aggregates_fact_coverage() -> None:
    details = [
        {"has_answer": True, "fact_coverage": 1.0, "latency_ms": 100.0, "llm_call_count": 1,
         "llm_calls_with_tokens": 1, "input_tokens": 5, "output_tokens": 3, "total_tokens": 8,
         "external_api_calls": 0},
        {"has_answer": False, "fact_coverage": 0.0, "latency_ms": 200.0, "llm_call_count": 1,
         "llm_calls_with_tokens": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
         "external_api_calls": 0},
    ]
    summary = build_answer_summary(details)
    assert summary["rows"] == 2
    assert summary["has_answer_rate"]["rate"] == pytest.approx(0.5)
    assert summary["fact_coverage"]["mean"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# dataset coverage (roadmap M0 risk mitigation: route_intent >= 50 rows)
# ---------------------------------------------------------------------------


def test_route_intent_dataset_meets_m0_floor() -> None:
    import csv
    import os

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "dataset",
        "route_intent_dataset.csv",
    )
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(line for line in handle if line.strip())]
    assert len(rows) >= 50, f"route_intent_dataset has {len(rows)} rows; M0 risk note requires >= 50"
    # every row must carry the columns the baseline runner reads
    for row in rows:
        assert row["qid"]
        assert row["query"]
        assert row["expected_route"]
