"""Q2-05: every skill has >= 30 eval cases with >= 10 hard negatives; the summary runs offline."""
from __future__ import annotations

import pytest

from tests.quality.preflight_eval import SKILLS, load_cases, run

pytestmark = pytest.mark.quality_offline


def test_case_files_meet_plan_minimums():
    for skill in SKILLS:
        cases = load_cases(skill)
        assert len(cases) >= 30, skill
        negatives = [case for case in cases if case["expect"] != skill]
        hard = [case for case in negatives if "hard negative" in str(case.get("why") or "").lower() or "困难负例" in str(case.get("why") or "")]
        assert len(hard) >= 10, (skill, len(hard))
        for case in cases:
            assert {"query", "expect", "preflight", "why"} <= set(case)


def test_summary_reports_precision_recall_and_gaps():
    report = run()
    for skill in SKILLS:
        entry = report["skills"][skill]
        assert entry["preflight_precision"] is not None and entry["preflight_recall"] is not None
        assert entry["known_gaps"] == len(entry["gaps"])
        assert isinstance(entry["reject_reason_distribution"], dict)
