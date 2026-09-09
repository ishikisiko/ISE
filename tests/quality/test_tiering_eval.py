"""Q2-02 gate: tiering + resolver replay stay offline and meet the hard thresholds."""
from __future__ import annotations

import pytest
import requests

from tests.quality.tiering_eval import run

pytestmark = pytest.mark.quality_offline


@pytest.fixture(scope="module")
def report(module_monkeypatch=None):
    return run()


def test_evaluation_makes_no_network_requests(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access during offline tiering evaluation")

    monkeypatch.setattr(requests.Session, "send", refuse)
    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests, "post", refuse)
    monkeypatch.setattr(requests, "head", refuse)
    result = run()
    assert result["tiering"]["urls"] >= 150
    assert result["resolver"]["entities"] >= 60
    assert result["resolver"]["unpinned_entities"] >= 45


def test_hard_thresholds(report):
    tiering = report["tiering"]
    assert tiering["official_precision"] is not None and tiering["official_precision"] >= 0.98
    assert tiering["denylist_compliance"] == 1.0
    assert tiering["non_evidence_exclusion"] == 1.0
    matrix = tiering["confusion_matrix"]
    assert set(matrix) == {"official", "first_party", "aggregator", "unknown", "excluded"}
    # Nothing on the denylist or the non-evidence table may be promoted to official.
    assert matrix["aggregator"]["official"] == 0
    assert matrix["excluded"]["official"] == 0


def test_resolver_replay_reports_accuracy_and_none_rate(report):
    resolver = report["resolver"]
    assert 0.0 <= resolver["resolver_accuracy"] <= 1.0
    assert 0.0 <= resolver["resolver_none_rate"] <= 1.0
    pinned = [item for item in report["per_entity"] if item["is_pinned"]]
    assert pinned and all(item["correct"] for item in pinned)
