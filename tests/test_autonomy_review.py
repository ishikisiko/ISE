import json

import pytest

from tests.autonomy_review import parse_review, review_input


def test_review_input_hides_mode_and_preserves_source_location():
    record = {"requested_mode": "autonomous", "result": {
        "answer": "candidate", "control": {"autonomy": {"mode": "autonomous"}},
        "evidence_items": [{"reference": "https://example.com/a", "content": "a fact"}],
    }}
    data = review_input(record, {"qid": "final003", "dataset": "final_answer", "query": "Capital?"})
    assert "autonomous" not in json.dumps(data)
    assert data["retrieved_evidence"][0]["reference"] == "https://example.com/a"


def test_review_validation_rejects_wrong_dimensions_and_boolean_scores():
    data = {"scores": {"core_correctness": True}, "reasons": {"core_correctness": "reason"},
            "grounding": 1, "confidence": 2, "answer_complete": True, "factual_concerns": []}
    with pytest.raises(ValueError):
        parse_review(json.dumps(data), ["core_correctness"])
    data["scores"]["core_correctness"] = 2
    assert parse_review(json.dumps(data), ["core_correctness"])["scores"]["core_correctness"] == 2
    with pytest.raises(ValueError):
        parse_review(json.dumps(data), ["other_dimension"])


def test_review_preserves_real_ledger_citation_mapping():
    record = {"result": {"answer": "A supported fact [E7].",
                         "evidence_items": [{"content": "UI form without citation id"}],
                         "evidence_records": [{"content": "A supported fact",
                                               "reference": "https://example.com/verified",
                                               "metadata": {"eid": 7}}]}}
    data = review_input(record, {"qid": "final003", "dataset": "final_answer", "query": "Capital?"})
    evidence = data["retrieved_evidence"]
    assert evidence == [{"content": "A supported fact", "reference": "https://example.com/verified",
                         "citation_id": "E7"}]
