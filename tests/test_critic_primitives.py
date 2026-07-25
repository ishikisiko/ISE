"""Unit tests for deterministic critic coverage and evidence progress helpers."""

from utils.query_orchestration import (
    check_constraint_coverage,
    evidence_increment_ratio,
)
from utils.time_parser import TimeConstraint


def _time_constraint(days: int = 7) -> TimeConstraint:
    return TimeConstraint(original_query="最近一周", cleaned_query="", days=days)


def test_empty_observation_has_no_progress() -> None:
    assert evidence_increment_ratio("anything", "") == 0.0


def test_new_and_repeated_observation_progress() -> None:
    assert evidence_increment_ratio("", "全新的证据内容") == 1.0
    pool = "苹果公司发布财报数据显示增长"
    assert evidence_increment_ratio(pool, pool) == 0.0
    assert 0.0 < evidence_increment_ratio("苹果 公司", "苹果公司发布全新财报") < 1.0
    assert evidence_increment_ratio("revenue growth", "net income") == 1.0


def test_no_constraint_query_has_no_gaps() -> None:
    met, missing = check_constraint_coverage(
        "苹果公司的CEO是谁",
        "",
        "蒂姆·库克",
        None,
    )
    assert met == []
    assert missing == []


def test_time_constraint_requires_a_time_signal() -> None:
    met, missing = check_constraint_coverage(
        "最近一周苹果股价",
        "没有时间的证据",
        "股价上涨了",
        _time_constraint(),
    )
    assert "time_constraint" in missing
    assert "time_constraint" not in met


def test_time_constraint_accepts_year_or_freshness_signal() -> None:
    for evidence, answer in (
        ("", "2025年苹果股价上涨"),
        ("2025年最新数据", "股价上涨"),
        ("", "最近苹果股价上涨"),
        ("", "苹果现在的股价上涨"),
    ):
        met, _ = check_constraint_coverage(
            "最近一周苹果股价",
            evidence,
            answer,
            _time_constraint(),
        )
        assert "time_constraint" in met


def test_comparison_requires_markers_and_substance() -> None:
    _, missing = check_constraint_coverage("对比苹果和微软", "", "苹果更大", None)
    assert "comparison" in missing

    answer = "苹果相比微软在硬件生态上更强，而微软同时在企业服务领域分别占据优势，" * 5
    met, _ = check_constraint_coverage("对比苹果和微软", "", answer, None)
    assert "comparison" in met


def test_multi_hop_requires_substance() -> None:
    _, missing = check_constraint_coverage(
        "为什么苹果股价上涨",
        "",
        "因为业绩好",
        None,
    )
    assert "multi_hop_reasoning" in missing

    met, _ = check_constraint_coverage(
        "为什么苹果股价上涨",
        "",
        "苹果股价上涨的原因包括多个方面。" * 20,
        None,
    )
    assert "multi_hop_reasoning" in met


def test_multiple_constraints_remain_independent() -> None:
    _, missing = check_constraint_coverage(
        "对比苹果和微软为什么股价不同",
        "",
        "短",
        _time_constraint(),
    )
    assert set(missing) == {
        "time_constraint",
        "comparison",
        "multi_hop_reasoning",
    }
