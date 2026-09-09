"""Q4-01: rubric v4 input building and strict output parsing (no judge calls)."""
from __future__ import annotations

import json

import pytest

from tests.quality_review import REVIEW_VERSION, deterministic_empty, load_errata, parse_review, review_input

pytestmark = pytest.mark.quality_offline

DIMS = ["breadth_of_tradeoffs", "citations"]


def _valid(**overrides):
    payload = {
        "core_correct": 2, "request_completeness": 1, "evidence_support": 2, "abstention": None, "scores": {},
        "semantic_fact_match": [{"fact": "capital city is Paris", "matched": True, "where": "Paris is the capital"}],
        "citation_checks": [{"id": "E1", "verdict": "supports"}], "factual_concerns": [], "answer_complete": True, "confidence": 2,
    }
    payload.update(overrides)
    return payload


def test_parse_review_accepts_valid_output_and_code_fences():
    text = "```json\n" + json.dumps(_valid()) + "\n```"
    parsed = parse_review(text, dimensions=[], must_include=1, should_abstain=False)
    assert parsed["core_correct"] == 2 and parsed["citation_checks"][0]["verdict"] == "supports"


@pytest.mark.parametrize(
    "overrides,dimensions,must_include,should_abstain",
    [
        ({"core_correct": 3}, [], 1, False),
        ({"core_correct": True}, [], 1, False),
        ({"abstention": 2}, [], 1, False),
        ({"abstention": None}, [], 1, True),
        ({"scores": {"other": 1}}, DIMS, 1, False),
        ({"scores": {"breadth_of_tradeoffs": 1, "citations": 5}}, DIMS, 1, False),
        ({"semantic_fact_match": []}, [], 1, False),
        ({"semantic_fact_match": [{"fact": "x", "matched": "yes"}]}, [], 1, False),
        ({"citation_checks": [{"id": "E1", "verdict": "maybe"}]}, [], 1, False),
        ({"citation_checks": [{"id": "1", "verdict": "supports"}]}, [], 1, False),
        ({"factual_concerns": "none"}, [], 1, False),
        ({"answer_complete": "true"}, [], 1, False),
        ({"confidence": 7}, [], 1, False),
    ],
)
def test_parse_review_rejects_malformed_output(overrides, dimensions, must_include, should_abstain):
    payload = _valid(**overrides)
    if dimensions and "scores" not in overrides:
        payload["scores"] = {name: 1 for name in dimensions}
    with pytest.raises(ValueError):
        parse_review(json.dumps(payload), dimensions=dimensions, must_include=must_include, should_abstain=should_abstain)
    with pytest.raises((ValueError, json.JSONDecodeError)):
        parse_review("not json at all", dimensions=[], must_include=0, should_abstain=False)


def test_review_input_keeps_all_retained_evidence_and_applies_errata():
    records = [
        {"reference": f"https://s{i}.example/", "title": f"t{i}", "content": "x" * 50, "source_tier": "unknown", "metadata": {"eid": i}}
        for i in range(1, 16)
    ]
    records.append({"reference": "https://long.example/", "title": "long", "content": "y" * 9000, "source_tier": "official", "metadata": {"eid": 16}})
    record = {
        "qid": "final016", "dataset": "final_answer", "query": "When was Google Inc. founded?", "answer": "September 1998 [E16]",
        "control": {"evidence_coverage": {"decisions": [{"reference": "https://long.example/", "decision": "retained"}, {"reference": "https://s1.example/", "decision": "rejected"}]}},
        "evidence_records": records,
    }
    dataset_row = {"reference_answer": "August 1998 ...", "must_include_facts": "official founding date August 1998; founders Larry Page and Sergey Brin"}
    errata = load_errata("dataset/annotations/gold_errata.json")
    item = review_input(record, dataset_row, errata, max_record_chars=6000)
    assert len(item["retrieved_evidence"]) == 16, "no 12-record cap"
    assert item["retrieved_evidence"][-1]["truncated"] is True and len(item["retrieved_evidence"][-1]["text"]) == 6000
    assert item["retrieved_evidence"][-1]["retained"] is True and item["retrieved_evidence"][0]["retained"] is False
    assert item["retrieved_evidence"][0]["citation_id"] == "E1"
    assert "September 4, 1998" in item["core_fact"]
    assert item["_meta"] == {"evidence_records": 16, "truncated_records": 1, "errata_applied": True, "max_record_chars": 6000}
    assert item["must_include_facts"] == ["official founding date August 1998", "founders Larry Page and Sergey Brin"]
    assert item["dimensions"] == [] and item["should_abstain"] is False
    assert "mode" not in json.dumps(item) and "guided" not in json.dumps(item)
    empty = deterministic_empty(item)
    assert parse_review(json.dumps(empty), dimensions=[], must_include=2, should_abstain=False)["answer_complete"] is False
    assert REVIEW_VERSION == "v4"
