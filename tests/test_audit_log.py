"""Focused coverage for persisted process-audit records."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

from langchain.langchain_orchestrator import LangChainOrchestrator

import main as cli_main
import server
from utils import audit_log
from utils.audit_log import (
    AuditRecorder,
    build_audit_record,
    resolve_audit_settings,
)
from utils.workflow_trace import WorkflowTracer


class _NoDomainSelector:
    def select_sources(self, query: str, timing_recorder: Any = None):
        return "general", []

    def generate_domain_specific_query(self, query: str, domain: str):
        return query

    def fetch_domain_data(
        self,
        query: str,
        domain: str,
        timing_recorder: Any = None,
    ):
        return None


def _configure_direct_orchestrator(
    monkeypatch,
    config: Dict[str, Any],
) -> LangChainOrchestrator:
    monkeypatch.setattr(
        LangChainOrchestrator,
        "_build_decision_chain",
        lambda self: None,
    )
    monkeypatch.setattr(
        LangChainOrchestrator,
        "_build_keyword_chain",
        lambda self: None,
    )
    orchestrator = LangChainOrchestrator(
        llm=object(),
        source_selector=_NoDomainSelector(),
        config=config,
        show_timings=False,
    )
    monkeypatch.setattr(orchestrator, "_snapshot_local_docs", lambda: None)
    monkeypatch.setattr(
        orchestrator,
        "_make_routing_decision",
        lambda query, timing_recorder=None: {
            "needs_search": False,
            "direct_answer": "audited answer",
            "reason": "test route",
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "_record_conversation_turn",
        lambda answer: None,
    )
    return orchestrator


def _read_records(path: Path) -> list[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_build_record_tolerates_missing_result_keys() -> None:
    event = {
        "seq": 1,
        "id": "route",
        "title": "Route",
        "status": "done",
    }

    record = build_audit_record(
        conversation_id="c1",
        query="What happened?",
        allow_search=True,
        events=[event],
        result={},
    )

    assert record["conversation_id"] == "c1"
    assert record["query"] == "What happened?"
    assert record["allow_search"] is True
    assert record["steps"] == [event]
    assert "answer" not in record
    assert "control" not in record


def test_recorder_truncates_and_honors_include_answer(tmp_path: Path) -> None:
    recorder = AuditRecorder(
        str(tmp_path),
        include_answer=True,
        max_bytes_per_record=460,
    )
    path = Path(
        recorder.record_turn(
            conversation_id="long-answer",
            query="audit this answer",
            allow_search=False,
            result={"answer": "x" * 5000},
        )
    )

    raw_line = path.read_text(encoding="utf-8").strip()
    record = json.loads(raw_line)
    assert record["truncated"] is True
    assert record["answer"].endswith("...")
    assert len(raw_line.encode("utf-8")) <= 460

    no_answer_recorder = AuditRecorder(
        str(tmp_path),
        include_answer=False,
    )
    no_answer_path = Path(
        no_answer_recorder.record_turn(
            conversation_id="without-answer",
            query="metadata only",
            allow_search=True,
            result={"answer": "must not be stored"},
        )
    )
    assert "answer" not in _read_records(no_answer_path)[0]


def test_recorder_caps_large_structured_metadata(tmp_path: Path) -> None:
    recorder = AuditRecorder(
        str(tmp_path),
        max_bytes_per_record=512,
    )
    path = Path(
        recorder.record_turn(
            conversation_id="structured",
            query="q" * 1000,
            allow_search=True,
            result={
                "answer": "a" * 1000,
                "control": {"numeric_payload": list(range(1000))},
            },
        )
    )

    raw_line = path.read_text(encoding="utf-8").strip()
    record = json.loads(raw_line)
    assert record["truncated"] is True
    assert len(raw_line.encode("utf-8")) <= 512


def test_recorder_appends_and_evicts_oldest_conversation_file(tmp_path: Path) -> None:
    recorder = AuditRecorder(str(tmp_path), max_files=2)
    first_path = Path(
        recorder.record_turn(
            conversation_id="first",
            query="first turn",
            allow_search=True,
            result={"answer": "one"},
        )
    )
    recorder.record_turn(
        conversation_id="first",
        query="second turn",
        allow_search=True,
        result={"answer": "two"},
    )
    assert len(_read_records(first_path)) == 2

    os.utime(first_path, (1, 1))
    recorder.record_turn(
        conversation_id="second",
        query="another",
        allow_search=True,
        result={"answer": "three"},
    )
    recorder.record_turn(
        conversation_id="third",
        query="last",
        allow_search=True,
        result={"answer": "four"},
    )

    assert not first_path.exists()
    assert len(list(tmp_path.glob("*.jsonl"))) == 2


def test_resolve_settings_honors_cli_priority() -> None:
    config = {
        "audit": {
            "enabled": True,
            "dir": "custom-audit",
            "include_answer": False,
            "max_files": 7,
            "max_bytes_per_record": 1234,
        }
    }

    assert resolve_audit_settings({}, None)["enabled"] is False
    assert resolve_audit_settings(config, None)["enabled"] is True
    assert resolve_audit_settings(config, "off")["enabled"] is False
    assert resolve_audit_settings({}, "file")["enabled"] is True
    assert resolve_audit_settings(config, None)["dir"] == "custom-audit"


def test_external_cli_mode_skips_orchestrator_write_and_keeps_timings(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = {
        "audit": {
            "enabled": True,
            "dir": str(tmp_path),
            "include_answer": True,
            "max_files": 10,
            "max_bytes_per_record": 4096,
        }
    }
    orchestrator = _configure_direct_orchestrator(monkeypatch, config)
    tracer = WorkflowTracer()

    result = orchestrator.answer(
        "Explain audit records",
        conversation_id="cli-turn",
        tracer=tracer,
        audit_mode="external",
    )

    assert result["response_times"]["total_ms"] >= 0
    assert list(tmp_path.glob("*.jsonl")) == []

    AuditRecorder(str(tmp_path)).record_turn(
        conversation_id="cli-turn",
        query="Explain audit records",
        allow_search=True,
        events=list(tracer.events),
        result=result,
    )
    records = _read_records(tmp_path / "cli-turn.jsonl")
    assert len(records) == 1


def test_config_enabled_audit_records_steps_without_a_supplied_tracer(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = {
        "audit": {
            "enabled": True,
            "dir": str(tmp_path),
            "include_answer": True,
        }
    }
    orchestrator = _configure_direct_orchestrator(monkeypatch, config)

    result = orchestrator.answer(
        "Web-style request without an SSE tracer",
        conversation_id="web-turn",
    )

    records = _read_records(tmp_path / "web-turn.jsonl")
    assert len(records) == 1
    assert records[0]["steps"]
    assert records[0]["response_times"]["total_ms"] >= 0
    assert result["answer"] == "audited answer"


def test_default_off_does_not_create_an_audit_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audit_dir = tmp_path / "audit"
    monkeypatch.setattr(audit_log, "DEFAULT_AUDIT_DIR", str(audit_dir))
    orchestrator = _configure_direct_orchestrator(monkeypatch, {})

    result = orchestrator.answer("No audit by default", conversation_id="off")

    assert result["answer"] == "audited answer"
    assert not audit_dir.exists()


def test_orchestrator_audit_failure_does_not_break_answer(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    config = {
        "audit": {
            "enabled": True,
            "dir": str(tmp_path),
        }
    }
    orchestrator = _configure_direct_orchestrator(monkeypatch, config)

    def fail_record(*args, **kwargs):
        raise OSError("audit directory unavailable")

    monkeypatch.setattr(audit_log.AuditRecorder, "record_turn", fail_record)

    result = orchestrator.answer("Answer despite audit failure", conversation_id="fail")

    assert result["answer"] == "audited answer"
    assert "[audit] record failed: audit directory unavailable" in capsys.readouterr().out


def test_cli_audit_writes_once_without_printing_timings(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    config = {
        "LLM_PROVIDER": "test",
        "audit": {
            "enabled": True,
            "dir": str(tmp_path),
            "include_answer": True,
            "max_files": 10,
            "max_bytes_per_record": 4096,
        },
    }
    orchestrator_holder: Dict[str, LangChainOrchestrator] = {}

    def factory(**kwargs):
        orchestrator = _configure_direct_orchestrator(monkeypatch, kwargs["config"])
        orchestrator_holder["value"] = orchestrator
        return orchestrator

    langchain_llm = importlib.import_module("langchain.langchain_llm")
    langchain_orchestrator = importlib.import_module(
        "langchain.langchain_orchestrator"
    )
    monkeypatch.setattr(cli_main, "LANGCHAIN_AVAILABLE", True)
    monkeypatch.setattr(cli_main, "load_runtime_config", lambda: config)
    monkeypatch.setattr(cli_main, "build_search_client", lambda config: None)
    monkeypatch.setattr(cli_main, "build_reranker", lambda config: (None, {}))
    monkeypatch.setattr(langchain_llm, "create_chat_model", lambda config: object())
    monkeypatch.setattr(
        langchain_orchestrator,
        "create_langchain_orchestrator",
        factory,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "CLI audit test", "--audit", "file"],
    )

    cli_main.main()

    audit_paths = list(tmp_path.glob("*.jsonl"))
    assert len(audit_paths) == 1
    assert len(_read_records(audit_paths[0])) == 1
    assert orchestrator_holder["value"]._audit_external is True
    output = capsys.readouterr().out
    assert "audited answer" in output
    assert "\u005b\u54cd\u5e94\u65f6\u95f4\u005d" not in output


def test_server_pipeline_passes_audit_config_to_orchestrator(monkeypatch) -> None:
    config = {
        "providers": {
            "minimax": {
                "api_key": "valid-key",
                "model": "test-model",
            }
        },
        "braveSearch": {},
        "brightDataSearch": {},
        "firecrawlSearch": {},
        "tavilySearch": {},
        "parallelSearch": {},
        "googleSearch": {},
        "rerank": {},
        "audit": {
            "enabled": True,
            "dir": "runtime/audit-test",
        },
    }
    captured: Dict[str, Any] = {}
    sentinel = object()

    monkeypatch.setattr(server, "load_base_config", lambda: config)
    monkeypatch.setattr(server, "create_chat_model", lambda config=None: object())
    monkeypatch.setattr(
        server,
        "build_search_client",
        lambda config, sources=None: None,
    )
    monkeypatch.setattr(
        server,
        "build_reranker",
        lambda config: (None, config.get("rerank", {})),
    )

    def factory(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(server, "create_langchain_orchestrator", factory)

    assert server.build_pipeline() is sentinel
    assert captured["config"]["audit"] == config["audit"]


def test_config_example_documents_disabled_audit_defaults() -> None:
    config_path = Path(__file__).resolve().parents[1] / "config.example.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config["audit"]["enabled"] is False
    assert config["audit"]["dir"] == "runtime/audit"
    assert config["audit"]["include_answer"] is True
    assert config["audit"]["max_files"] == 200
    assert config["audit"]["max_bytes_per_record"] == 65536
