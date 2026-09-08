from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.autonomy_study import write_new
from tests.study_transport import TransportObserver, usage_counts


def test_study_results_cannot_be_overwritten(tmp_path: Path):
    path = tmp_path / "result.json"
    write_new(path, {"score": 0})
    with pytest.raises(FileExistsError):
        write_new(path, {"score": 100})
    assert json.loads(path.read_text()) == {"score": 0}


def test_transport_observer_preserves_payload_and_records_missing_usage(monkeypatch, tmp_path):
    import requests

    seen = []
    def fake_send(session, request, **kwargs):
        seen.append(request)
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"usage": {"prompt_tokens": 20, "completion_tokens": 5}}).encode()
        return response

    monkeypatch.setattr(requests.Session, "send", fake_send)
    observer = TransportObserver(tmp_path / "transport.jsonl")
    observer.install()
    request = requests.Request("POST", "https://example.com/v1/chat/completions",
                               json={"model": "test", "messages": ["secret prompt"]},
                               headers={"Authorization": "secret-key"}).prepare()
    response = requests.Session().send(request)
    assert seen == [request]
    assert response.json()["usage"]["prompt_tokens"] == 20
    assert observer.summary()["transport_total_tokens"] == 25
    assert "secret" not in (tmp_path / "transport.jsonl").read_text()
    assert usage_counts({})["total_tokens"] is None
