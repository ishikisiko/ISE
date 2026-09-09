"""Q4-03: kappa hand-computed samples."""
from __future__ import annotations

import pytest

from tests.quality.agreement import agreement_report, cohen_kappa, load_labels, weighted_kappa

pytestmark = pytest.mark.quality_offline


def test_cohen_kappa_textbook_example():
    # 2x2: both yes 20, both no 15, A yes/B no 5, A no/B yes 10 -> po=0.70, pe=0.5*0.6+0.5*0.4=0.50 -> kappa=0.40
    left = ["y"] * 20 + ["n"] * 15 + ["y"] * 5 + ["n"] * 10
    right = ["y"] * 20 + ["n"] * 15 + ["n"] * 5 + ["y"] * 10
    assert cohen_kappa(left, right) == pytest.approx(0.4)
    assert cohen_kappa([1, 1], [1, 1]) == 1.0
    assert cohen_kappa([1], [1]) is None


def test_weighted_kappa_linear_and_quadratic():
    left = [0, 1, 2, 2, 1, 0]
    right = [0, 1, 2, 1, 1, 2]
    # perfect agreement on 4/6; one off-by-one, one off-by-two
    linear = weighted_kappa(left, right, weights="linear")
    quadratic = weighted_kappa(left, right, weights="quadratic")
    assert weighted_kappa([0, 1, 2], [0, 1, 2]) == 1.0
    assert 0 < linear < 1 and 0 < quadratic < 1
    # Off-by-two disagreements are punished harder under quadratic weights.
    far_left, far_right = [0, 2, 0, 2, 1, 1], [2, 0, 2, 0, 1, 1]
    assert weighted_kappa(far_left, far_right, weights="quadratic") < weighted_kappa(far_left, far_right, weights="linear")


def test_agreement_report_flags_low_kappa_dimensions_and_lists_disagreements(tmp_path):
    judge = {f"q{i}": {"core_correct": 2, "evidence_support": i % 3} for i in range(12)}
    human = {f"q{i}": {"core_correct": 2 if i != 3 else 0, "evidence_support": (i + 1) % 3} for i in range(12)}
    report = agreement_report(judge, human, ["core_correct", "evidence_support"], left_name="judge", right_name="human")
    assert report["paired_questions"] == 12
    core = report["dimensions"]["core_correct"]
    assert core["disagreements"] == [{"qid": "q3", "left": 2, "right": 0}]
    assert core["agreement_rate"] == pytest.approx(11 / 12)
    assert report["dimensions"]["evidence_support"]["reportable"] is False
    assert report["dimensions"]["evidence_support"]["verdict"].startswith("human-only")

    csv_path = tmp_path / "answer.csv"
    csv_path.write_text("qid,annotator,core_correct,evidence_support,notes\nq1,A,2,1,\nq1,B,1,1,\nq2,A,0,0,x\n", encoding="utf-8")
    labels_a = load_labels(str(csv_path), annotator="A")
    labels_b = load_labels(str(csv_path), annotator="B")
    assert labels_a == {"q1": {"core_correct": 2, "evidence_support": 1}, "q2": {"core_correct": 0, "evidence_support": 0}}
    assert labels_b == {"q1": {"core_correct": 1, "evidence_support": 1}}
