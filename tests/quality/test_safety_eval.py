"""Q5-06: credential / URL-secret / PII scanning of run artefacts."""
from __future__ import annotations

import json

import pytest

from tests.quality.safety_eval import configured_secrets, run, scan_text

pytestmark = pytest.mark.quality_offline


def test_configured_secrets_and_shapes_are_detected(tmp_path):
    config = {"providers": {"x": {"api_key": "sk-live-1234567890abcdefghijklmnop"}}, "tavilySearch": {"api_key": "YOUR_TAVILY_API_KEY_HERE"}, "braveSearch": {"primary_api_key": "short"}}
    secrets = configured_secrets(config)
    assert secrets == ["sk-live-1234567890abcdefghijklmnop"]
    text = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123 and https://api.example.com/v1?q=x&api_key=zzz and AKIAABCDEFGHIJKLMNOP"
    result = scan_text(text, secrets)
    assert result["credential_hits"] == {"aws_access_key": 1, "bearer_token": 1}
    assert result["url_secret_hits"] == 1

    run_dir = tmp_path / "run"
    (run_dir / "audit").mkdir(parents=True)
    (run_dir / "audit" / "c1.jsonl").write_text(
        json.dumps({"query": "contact me at someone@example.com", "answer": "ok"}) + "\n" + json.dumps({"query": "safe query", "answer": secrets[0]}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "execution.log").write_text("token=redacted\n", encoding="utf-8")
    report = run(str(run_dir), config=config, include_tiering=False)
    assert report["credential_leak_count"] == 1
    assert report["hard_gate"]["credential_leak_count"] == 1
    assert report["audit_records_checked"] == 2 and len(report["pii_leaks"]) == 1
    assert report["passed"] is False

    clean = tmp_path / "clean"
    (clean / "audit").mkdir(parents=True)
    (clean / "audit" / "c1.jsonl").write_text(json.dumps({"query": "hello", "answer": "world"}) + "\n", encoding="utf-8")
    assert run(str(clean), config=config, include_tiering=False)["passed"] is True
