"""Regression coverage for durable web-server query records."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import server
from utils import server_logging


def _read_events(directory: Path) -> list[dict[str, Any]]:
    paths = list((directory / "requests").glob("*.jsonl"))
    assert len(paths) == 1
    return [
        json.loads(line)
        for line in paths[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _enable_test_logging(monkeypatch, directory: Path) -> None:
    monkeypatch.setitem(server.app.config, "TESTING", True)
    monkeypatch.setitem(
        server.app.config,
        "SERVER_LOGGING_SETTINGS",
        {
            "enabled": True,
            "dir": str(directory),
            "capture_stdio": False,
            "include_request_payload": True,
            "include_response_payload": True,
        },
    )
    monkeypatch.setattr(server, "load_base_config", lambda: {"LLM_PROVIDER": "stub"})


def test_manual_compact_returns_not_found_without_creating_a_conversation(monkeypatch) -> None:
    class Manager:
        def has_checkpoint(self, conversation_id: str) -> bool:
            return False

    monkeypatch.setattr(server, "_conversation_manager", lambda: Manager())
    with server.app.test_client() as client:
        response = client.post("/api/conversation/missing/compact")

    assert response.status_code == 404
    assert response.get_json()["error"] == "Conversation checkpoint not found"


def test_manual_compact_returns_checkpoint_metrics(monkeypatch) -> None:
    class Manager:
        def has_checkpoint(self, conversation_id: str) -> bool:
            return conversation_id == "existing"

    class Loop:
        def compact_conversation(self, conversation_id: str) -> dict[str, Any]:
            assert conversation_id == "existing"
            return {
                "before_messages": 12,
                "after_messages": 5,
                "summary_source": "deterministic",
                "compactions": 1,
            }

    class Pipeline:
        def _get_loop_orchestrator(self) -> Loop:
            return Loop()

    monkeypatch.setattr(server, "_conversation_manager", lambda: Manager())
    monkeypatch.setattr(server, "build_pipeline", lambda **kwargs: Pipeline())
    with server.app.test_client() as client:
        response = client.post("/api/conversation/existing/compact")

    assert response.status_code == 200
    assert response.get_json() == {
        "conversation_id": "existing",
        "before_messages": 12,
        "after_messages": 5,
        "summary_source": "deterministic",
        "compactions": 1,
    }


def test_process_logging_keeps_terminal_output_and_avoids_duplicates(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    root = logging.Logger("test-server-root", level=logging.WARNING)
    named_logger = logging.Logger("test-server-named", level=logging.INFO)

    def get_logger(name: str | None = None) -> logging.Logger:
        return named_logger if name else root

    monkeypatch.setattr(server_logging.logging, "getLogger", get_logger)
    settings = {
        "enabled": True,
        "dir": str(tmp_path),
        "capture_stdio": False,
    }

    try:
        server_logging.configure_process_logging(settings)
        server_logging.configure_process_logging(settings)
        console_handlers = [
            handler
            for handler in root.handlers
            if isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, logging.FileHandler)
        ]
        file_handlers = [
            handler
            for handler in root.handlers
            if isinstance(handler, logging.FileHandler)
        ]

        assert len(console_handlers) == 1
        assert len(file_handlers) == 1
        root.info("terminal-sync-check")
        assert "terminal-sync-check" in capsys.readouterr().err
        assert "terminal-sync-check" in (tmp_path / "server.log").read_text(
            encoding="utf-8"
        )
    finally:
        for handler in root.handlers:
            handler.close()


def test_json_answer_persists_request_steps_and_complete_response(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _enable_test_logging(monkeypatch, tmp_path)

    class Pipeline:
        def answer(self, query: str, **kwargs: Any) -> dict[str, Any]:
            tracer = kwargs["tracer"]
            tracer.begin("route", "Route")
            tracer.end("route", detail="direct result")
            return {
                "answer": "durably recorded answer",
                "nested": {"all": ["response", "fields"]},
            }

    monkeypatch.setattr(server, "build_pipeline", lambda **kwargs: Pipeline())

    with server.app.test_client() as client:
        response = client.post(
            "/api/answer",
            json={"query": "record this complete request", "search": "off"},
            headers={"Authorization": "Bearer test-secret", "X-Audit-Test": "kept"},
        )

    assert response.status_code == 200
    events = _read_events(tmp_path)
    assert [event["event"] for event in events] == [
        "request_received",
        "context_prepared",
        "workflow_step",
        "workflow_step",
        "response_ready",
        "request_complete",
    ]
    assert events[0]["request_payload"]["query"] == "record this complete request"
    assert "Authorization" not in events[0]["request"]["header_values"]
    assert events[0]["request"]["header_values"]["X-Audit-Test"] == "kept"
    assert events[-2]["response_payload"]["nested"] == {
        "all": ["response", "fields"]
    }

    access_records = [
        json.loads(line)
        for line in (tmp_path / "access.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert access_records[-1]["event"] == "http_response"
    assert access_records[-1]["request_id"] == events[0]["request_id"]


def test_stream_answer_persists_live_steps_and_final_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _enable_test_logging(monkeypatch, tmp_path)

    class Pipeline:
        def answer(self, query: str, **kwargs: Any) -> dict[str, Any]:
            tracer = kwargs["tracer"]
            tracer.begin("search", "Search")
            tracer.end("search", detail="one source")
            return {"answer": "streamed result", "search_hits": [{"title": "one"}]}

    monkeypatch.setattr(server, "build_pipeline", lambda **kwargs: Pipeline())

    with server.app.test_client() as client:
        response = client.post(
            "/api/answer/stream",
            json={"query": "stream this request", "search": "on"},
            buffered=True,
        )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "event: step" in body
    assert "event: result" in body
    assert "event: done" in body

    events = _read_events(tmp_path)
    assert [event["event"] for event in events][-2:] == [
        "response_ready",
        "request_complete",
    ]
    assert events[-2]["response_payload"]["answer"] == "streamed result"


def test_stream_answer_forwards_react_step_details_in_order(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _enable_test_logging(monkeypatch, tmp_path)

    class Pipeline:
        def answer(self, query: str, **kwargs: Any) -> dict[str, Any]:
            tracer = kwargs["tracer"]
            tracer.begin("react_iteration_1", "第 1 轮", detail="模型正在决定下一步")
            tracer.begin("react_tool_1_1", "工具调用：web_search", detail="查询：pricing")
            tracer.end(
                "react_tool_1_1",
                detail="完成 · 返回 2 条结果",
                items=[
                    {"label": "查询", "value": "pricing"},
                    {"label": "结果", "value": "2 条"},
                ],
                records=[
                    {
                        "title": "Pricing",
                        "url": "https://example.com/pricing?token=hidden",
                        "snippet": "API price",
                    }
                ],
                record_kind="search_results",
                record_label="搜索结果 · 1",
            )
            tracer.begin("react_evaluate_1", "第 1 轮评估", detail="正在检查证据与约束")
            tracer.end(
                "react_evaluate_1",
                title="第 1 轮评估",
                detail="继续检索（缺：comparison）",
                items=[{"label": "判定", "value": "继续检索"}],
            )
            tracer.end("react_iteration_1", detail="本轮完成")
            return {
                "answer": "streamed React result",
                "control": {"react_trace": list(tracer.events)},
            }

    monkeypatch.setattr(server, "build_pipeline", lambda **kwargs: Pipeline())

    with server.app.test_client() as client:
        response = client.post(
            "/api/answer/stream",
            json={"query": "trace this React turn", "search": "on"},
            buffered=True,
        )

    body = response.get_data(as_text=True)
    frames = [frame for frame in body.split("\n\n") if frame.startswith("event: step\n")]
    step_payloads = [json.loads(frame.split("\ndata: ", 1)[1]) for frame in frames]
    step_ids = [payload["id"] for payload in step_payloads]
    assert response.status_code == 200
    assert step_ids == [
        "react_iteration_1",
            "react_tool_1_1",
            "react_tool_1_1",
            "react_evaluate_1",
            "react_evaluate_1",
            "react_iteration_1",
        ]
    assert step_payloads[2]["items"][1] == {"label": "结果", "value": "2 条"}
    assert step_payloads[2]["record_kind"] == "search_results"
    assert step_payloads[2]["records"][0]["url"] == "https://example.com/pricing"
    assert "hidden" not in str(step_payloads[2])
    assert step_payloads[4]["items"] == [{"label": "判定", "value": "继续检索"}]
    assert "items" not in step_payloads[-1]

    events = _read_events(tmp_path)
    recorded_steps = [event["workflow_event"] for event in events if event["event"] == "workflow_step"]
    assert [event["id"] for event in recorded_steps] == step_ids


def test_pipeline_error_is_persisted_with_traceback(monkeypatch, tmp_path: Path) -> None:
    _enable_test_logging(monkeypatch, tmp_path)

    class Pipeline:
        def answer(self, query: str, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("intentional logging failure")

    monkeypatch.setattr(server, "build_pipeline", lambda **kwargs: Pipeline())

    with server.app.test_client() as client:
        response = client.post("/api/answer", json={"query": "record the failure"})

    assert response.status_code == 500
    events = _read_events(tmp_path)
    failure = next(event for event in events if event["event"] == "pipeline_error")
    assert failure["error"] == "intentional logging failure"
    assert "RuntimeError: intentional logging failure" in failure["traceback"]
