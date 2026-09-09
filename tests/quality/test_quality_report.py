"""Q5-02 / Q5-03: scorecard with missing inputs, report rendering, paired regression gate."""
from __future__ import annotations

import json

import pytest

from tests.quality_report import NOT_RUN, build_scorecard, compare_rows, load_inputs, per_question_rows, render_report

pytestmark = pytest.mark.quality_offline


def _record(qid, mode, core, *, answer="A fine answer [E1].", status="succeeded", tokens=1000, latency=2000.0, dataset="final_answer"):
    return {
        "qid": qid, "dataset": dataset, "mode": mode, "query": f"q {qid}", "answer": answer, "outcome": "returned",
        "review": {"review_version": "v4", "judgment": {"core_correct": core, "request_completeness": 2, "evidence_support": 1, "scores": {}, "citation_checks": [{"id": "E1", "verdict": "supports"}], "factual_concerns": [], "answer_complete": True}},
        "control": {"loop_status": status, "loop_termination_reason": status, "loop_iterations": 2, "query_analysis": {"claim_classes": [], "constraints": {}}, "evidence_coverage": {"retained": 1, "entries": 1}, "loop_verdicts": [], "termination_policy": {"tool_budgets": {}}, "execution_trace": {"events": []}},
        "evidence_records": [{"source_type": "web", "source_tier": "official", "reference": "https://x.example/", "content": "x", "metadata": {"eid": 1}}],
        "response_times": {"total_ms": latency, "llm_calls": [{"label": "loop_act", "duration_ms": 100, "total_tokens": tokens}], "tool_calls": []},
        "metrics": {},
    }


def test_scorecard_marks_missing_dimensions_as_not_run(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "loop_eval.json").write_text(json.dumps({"summary": {"premature_success_rate": 0.1, "hard_gate": {"false_exhaustion_count": 0}}}), encoding="utf-8")
    inputs = load_inputs(run_dir)
    rows = per_question_rows([_record("q1", "guided", 2), _record("q2", "guided", 0)])
    scorecard = build_scorecard(run_dir, inputs, rows)
    assert scorecard["validity"]["status"] == NOT_RUN
    assert scorecard["indices"]["retrieval"]["status"] == NOT_RUN and scorecard["indices"]["retrieval"]["value"] is None
    assert scorecard["indices"]["process"]["components"]["not_premature"]["value"] == pytest.approx(0.9)
    assert scorecard["indices"]["process"]["components"]["route_accuracy"]["status"] == NOT_RUN
    assert scorecard["indices"]["answer"]["components"]["core_correct"]["value"] == 0.5
    assert scorecard["indices"]["evidence"]["components"]["citation_precision"]["value"] == 1.0
    hard = scorecard["hard_gates"]
    assert hard["core_correct_zero_count"] == 1 and hard["core_correct_zero_qids"] == ["q2"]
    assert hard["credential_leak_count"] is None and "credential_leak_count" in hard["unavailable"]
    assert hard["passed"] is False
    assert scorecard["confidence_intervals"]["core_correct"] is None  # n < 20
    report = render_report(scorecard, inputs)
    assert NOT_RUN in report and "| q2 |" in report and "## 7. 复现命令" in report


def test_compare_rows_applies_regression_gate():
    left = per_question_rows([_record("q1", "guided", 2, tokens=1000), _record("q2", "guided", 2, tokens=1000), _record("q3", "guided", 1, tokens=1000)])
    better = per_question_rows([_record("q1", "guided", 2, tokens=1100), _record("q2", "guided", 2, tokens=1100), _record("q3", "guided", 2, tokens=1100)])
    result = compare_rows(left, better, pair_key=lambda row: row["qid"])
    assert result["pairs"] == 3
    assert result["overall"]["core_correct"] == {"wins": 1, "ties": 2, "losses": 0, "n": 3, "mean_delta": pytest.approx(1 / 3)}
    assert result["gate"]["accepted"] is True
    assert result["gate"]["cost_ratio_tokens"] == pytest.approx(1.1)

    worse = per_question_rows([_record("q1", "guided", 0, tokens=2000), _record("q2", "guided", 2, tokens=2000), _record("q3", "guided", 1, tokens=2000)])
    result = compare_rows(left, worse, pair_key=lambda row: row["qid"])
    gate = result["gate"]
    assert gate["hard_gates_not_increased"] is False  # a new core_correct=0
    assert gate["no_index_with_more_losses_than_wins"] is False
    assert gate["cost_ratio_ok"] is False and gate["accepted"] is False
    accepted_cost = compare_rows(left, worse, pair_key=lambda row: row["qid"], accept_cost="user accepted 2x tokens on 2026-09-09")
    assert accepted_cost["gate"]["cost_ratio_ok"] is True and accepted_cost["gate"]["accepted"] is False
