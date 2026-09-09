"""Q2-01 gate: D1 query-analysis regression on ``dataset/query_analysis_gold.csv``."""
from __future__ import annotations

import pytest

from tests.quality.analysis_eval import match_members, parse_aliases, run

pytestmark = pytest.mark.quality_offline

DEFECT_MEMBER_NOISE = (
    "QD-20260909-04 deterministic comparison-member extraction keeps instruction tails / "
    "limiting phrases as members (noise_member_rate > 0 without LLM reconcile)"
)


@pytest.fixture(scope="module")
def report():
    return run()


def test_member_matching_uses_stems_and_aliases():
    aliases = parse_aliases("Kimi K2.7=K2.7/Kimi|Opus 5=Opus")
    tp, pred, gold, noise = match_members(["GLM-5.2", "K2.7", "注意不要只看官方宣传"], ["GLM-5.2", "Kimi K2.7"], aliases)
    assert (tp, pred, gold) == (2, 3, 2)
    assert noise == ["注意不要只看官方宣传"]


def test_dataset_shape_meets_plan_minimums(report):
    per_query = report["per_query"]
    assert len(per_query) >= 60
    categories = {}
    for item in per_query:
        categories[item["category"]] = categories.get(item["category"], 0) + 1
    assert categories["comparison"] >= 20
    assert categories["temporal"] >= 15
    assert categories["ambiguous"] >= 10
    assert categories["chat_local"] >= 15


def test_no_false_temporal_fanout(report):
    summary = report["summary"]
    assert summary["false_temporal_fanout_rate"] == 0.0, summary["false_temporal_fanout_qids"]


def test_summary_fields_present(report):
    summary = report["summary"]
    for key in ("intent_shape_accuracy", "intent_shape_confusion", "comparison_members", "entities",
                "claim_classes_micro", "critical_ambiguity", "existence_query", "time_scope_accuracy"):
        assert key in summary
    assert summary["comparison_members"]["f1"] is not None


@pytest.mark.xfail(strict=True, reason=DEFECT_MEMBER_NOISE)
def test_comparison_noise_member_rate_is_zero(report):
    assert report["summary"]["comparison_members"]["noise_member_rate"] == 0.0
