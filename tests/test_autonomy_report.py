"""Integrity and heterogeneous-rubric regression tests for study aggregation."""
import json

import pytest

from tests.autonomy_report import aggregate, load_round, paired_comparison, render_cases
from tests.autonomy_study import digest


def test_open_tasks_have_different_dimension_names():
    rows = []
    for name, value in (("accuracy", 2), ("coverage", 1)):
        rows.append({"dataset": "open_task", "requested_mode": "guided", "outcome": "returned",
                     "metrics": {}, "review": {"judgment": {
                         "scores": {name: value}, "answer_complete": True,
                         "grounding": 1, "confidence": 2,
                     }}})
    group = aggregate(rows)["open_task/guided"]
    assert group["auxiliary_quality_percent"]["mean"] == 75
    assert group["dimension_scores"]["accuracy"]["mean"] == 2
    assert group["dimension_scores"]["coverage"]["mean"] == 1


def test_report_refuses_unbound_review(tmp_path):
    root = tmp_path / "r1"
    reviews = root / "reviews-v3-evidence"
    reviews.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({"runs": []}))
    manifest = {"revision": "v3-evidence"}
    (tmp_path / "review-manifest-v3-evidence.json").write_text(json.dumps(manifest))
    review = {"result_digest": "a", "review_manifest_digest": "wrong"}
    (reviews / "one.json").write_text(json.dumps(review))
    with pytest.raises(ValueError, match="protocol binding"):
        load_round(tmp_path, "r1")
    review["review_manifest_digest"] = digest(json.dumps(manifest, sort_keys=True).encode())
    (reviews / "one.json").write_text(json.dumps(review))
    assert load_round(tmp_path, "r1") == ([], [])


def test_paired_scores_use_common_questions_and_fixed_rubric():
    def row(qid, score):
        return {"qid": qid, "query": qid, "dataset": "open_task", "metrics": {"wall_ms": 10},
                "review": {"judgment": {"scores": {"accuracy": score}}}}

    before = [row("a", 1), row("b", 2)]
    after = [row("a", 2)]
    compared = paired_comparison(before, after)["open_task"]
    assert compared["pairs"] == 1
    assert compared["right_better"] == 1
    assert compared["quality_delta_points"]["mean"] == 50
    with pytest.raises(ValueError, match="one record"):
        paired_comparison(before + before, after)
    after[0]["review"]["judgment"]["scores"] = {"other": 2}
    with pytest.raises(ValueError, match="rubric mismatch"):
        paired_comparison(before, after)


def test_support_score_cannot_hide_core_fact_regression():
    def row(scores):
        return {"qid": "fact", "query": "When?", "dataset": "final_answer", "metrics": {},
                "review": {"judgment": {"scores": scores}}}

    before = row({"core_correctness": 2, "request_completeness": 2, "evidence_support": 0})
    after = row({"core_correctness": 0, "request_completeness": 2, "evidence_support": 2})
    compared = paired_comparison([before], [after])["final_answer"]
    assert compared["equal"] == 1
    assert compared["core_correctness_regressions"] == [{"qid": "fact", "left": 2, "right": 0, "delta": -2}]


def test_case_appendix_preserves_missing_and_incomplete_accounting():
    report = {"cases": {"final001": {"query": "Which?", "r1/guided": {
        "outcome": "returned", "metrics": {"loop_status": "succeeded", "wall_ms": 1234,
                                                "transport_total_tokens": 1200,
                                                "transport_usage_complete": False},
        "judgment": {"scores": {"core_correctness": 0, "request_completeness": 2,
                                   "evidence_support": 2}, "answer_complete": True},
    }}}}
    rendered = render_cases(report)
    assert "66.67；是；0/2；succeeded" in rendered
    assert "≥1,200；1.2" in rendered
    assert "未完成" in rendered
    assert "`final001`：Which?" in rendered
