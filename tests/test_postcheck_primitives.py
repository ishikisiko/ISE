"""Unit tests for postcheck constraint-coverage and evidence-increment primitives."""

from langchain.postcheck import (
    check_constraint_coverage,
    evidence_increment_ratio,
)
from utils.time_parser import TimeConstraint


def _time_constraint(days=7):
    return TimeConstraint(original_query="最近一周", cleaned_query="", days=days)


class TestEvidenceIncrementRatio:
    def test_empty_observation_returns_zero(self):
        assert evidence_increment_ratio("anything", "") == 0.0

    def test_fully_new_observation_returns_one(self):
        assert evidence_increment_ratio("", "全新的证据内容") == 1.0

    def test_identical_observation_returns_zero(self):
        pool = "苹果公司发布财报数据显示增长"
        assert evidence_increment_ratio(pool, pool) == 0.0

    def test_partial_overlap(self):
        ratio = evidence_increment_ratio("苹果 公司", "苹果公司发布全新财报")
        assert 0.0 < ratio < 1.0

    def test_latin_tokens(self):
        assert evidence_increment_ratio("revenue growth", "revenue growth") == 0.0
        assert evidence_increment_ratio("revenue growth", "net income") == 1.0


class TestCheckConstraintCoverage:
    def test_no_constraints(self):
        met, missing = check_constraint_coverage("苹果公司的CEO是谁", "", "蒂姆·库克", None)
        assert met == []
        assert missing == []

    def test_time_constraint_missing(self):
        met, missing = check_constraint_coverage(
            "最近一周苹果股价", "没有时间的证据", "股价上涨了", _time_constraint()
        )
        assert "time_constraint" in missing
        assert "time_constraint" not in met

    def test_time_constraint_met_by_year_in_answer(self):
        met, missing = check_constraint_coverage(
            "最近一周苹果股价", "", "2025年苹果股价上涨", _time_constraint()
        )
        assert "time_constraint" in met

    def test_time_constraint_met_by_evidence(self):
        met, missing = check_constraint_coverage(
            "最近一周苹果股价", "2025年最新数据", "股价上涨", _time_constraint()
        )
        assert "time_constraint" in met

    def test_time_constraint_met_by_keyword(self):
        met, missing = check_constraint_coverage(
            "最近一周苹果股价", "", "最近苹果股价上涨", _time_constraint()
        )
        assert "time_constraint" in met

    def test_comparison_missing_when_short(self):
        met, missing = check_constraint_coverage("对比苹果和微软", "", "苹果更大", None)
        assert "comparison" in missing

    def test_comparison_met_with_markers_and_length(self):
        answer = "苹果相比微软在硬件生态上更强，而微软同时在企业服务领域分别占据优势，" * 5
        met, missing = check_constraint_coverage("对比苹果和微软", "", answer, None)
        assert "comparison" in met

    def test_multi_hop_missing_when_short(self):
        met, missing = check_constraint_coverage("为什么苹果股价上涨", "", "因为业绩好", None)
        assert "multi_hop_reasoning" in missing

    def test_multi_hop_met_when_long(self):
        answer = "苹果股价上涨的原因包括多个方面。" * 20
        met, missing = check_constraint_coverage("为什么苹果股价上涨", "", answer, None)
        assert "multi_hop_reasoning" in met

    def test_multiple_constraints(self):
        met, missing = check_constraint_coverage("对比苹果和微软为什么股价不同", "", "短", _time_constraint())
        assert set(missing) == {"time_constraint", "comparison", "multi_hop_reasoning"}
