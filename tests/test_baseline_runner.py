"""Unit coverage for baseline telemetry added by context compaction."""

from __future__ import annotations

from tests.baseline_runner import (
    build_answer_summary,
    extract_llm_stats,
    extract_loop_stats,
    run_answer_dataset,
)


def test_baseline_records_per_query_token_peak_and_compaction_metrics() -> None:
    result = {
        "response_times": {
            "llm_calls": [
                {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                {"input_tokens": 360, "output_tokens": 10, "total_tokens": 370},
            ]
        },
        "control": {"compactions": 2, "peak_context_ratio": 0.74},
    }

    stats = extract_llm_stats(result)
    loop = extract_loop_stats(result)
    assert stats["peak_input_tokens"] == 360
    assert loop == {
        "loop_iterations": None,
        "loop_status": None,
        "loop_evidence_records": None,
        "skill_tools_used": [],
        "compactions": 2,
        "peak_context_ratio": 0.74,
    }

    summary = build_answer_summary([{**stats, **loop}])
    assert summary["peak_input_tokens_per_query"]["max"] == 360
    assert summary["compactions_per_query"]["mean"] == 2
    assert summary["peak_context_ratio_per_query"]["p95"] == 0.74


def test_answer_baseline_persists_progress_after_each_completed_live_row() -> None:
    class FakeOrchestrator:
        def answer(self, query: str, **kwargs):
            return {"answer": f"answer for {query}", "response_times": {"llm_calls": []}}

    snapshots = []
    details = run_answer_dataset(
        FakeOrchestrator(),
        [
            {"qid": "a", "query": "first", "must_include_facts": "first"},
            {"qid": "b", "query": "second", "must_include_facts": "second"},
        ],
        num_results=1,
        max_tokens=32,
        temperature=0,
        on_progress=lambda rows: snapshots.append([row["qid"] for row in rows]),
    )

    assert [row["qid"] for row in details] == ["a", "b"]
    assert snapshots == [["a"], ["a", "b"]]
