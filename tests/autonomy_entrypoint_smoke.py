"""Real-provider CLI / Flask entrypoint smoke with isolated local state.

Only configuration loading and result observation are adapted. The actual
argument parser, route, pipeline builder, orchestrator, model and HTTP calls run.
This is not a listening-server test and is separate from the frozen 160 runs.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import redirect_stderr, redirect_stdout
import json
from pathlib import Path
import sys
from unittest.mock import patch

from tests.autonomy_study import ROOT, utc_now, write_new
from tests.baseline_runner import extract_llm_stats
from tests.study_transport import TransportObserver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", choices=("cli", "flask"), required=True)
    args = parser.parse_args()
    root = ROOT / "runtime/baseline/autonomy-20260908-measured" / ("r2-smoke-" + args.entry)
    root.mkdir(exist_ok=True)
    if (root / "execution.log").exists():
        raise SystemExit("Refusing to overwrite a prior smoke attempt")
    config = json.loads((ROOT / "config.json").read_text())
    config.setdefault("conversation", {})["checkpoint_path"] = str(root / "checkpoint.sqlite")
    config.setdefault("audit", {}).update({"dir": str(root / "audit"), "enabled": True})
    config["server_logging"] = {"enabled": False}
    uploads = root / "uploads"
    uploads.mkdir(exist_ok=True)
    observer = TransportObserver(root / "transport.jsonl")
    observer.install()
    records = []
    status = None
    error = None
    started = utc_now()
    with (root / "execution.log").open("x") as log, redirect_stdout(log), redirect_stderr(log):
        try:
            query = "What is gold's chemical symbol?"
            if args.entry == "cli":
                import main as cli
                from langchain.langchain_orchestrator import LangChainOrchestrator
                answer = LangChainOrchestrator.answer

                def observe_answer(instance, *argv, **kwargs):
                    result = answer(instance, *argv, **kwargs)
                    records.append(result)
                    return result

                argv = ["main.py", query, "--autonomy", "autonomous", "--max-tokens", "500",
                        "--temperature", "0.2", "--data-path", str(uploads),
                        "--conversation-id", "autonomy-r2-cli-smoke", "--pretty"]
                with patch.object(sys, "argv", argv), \
                     patch.object(cli, "load_runtime_config", lambda: copy.deepcopy(config)), \
                     patch.object(LangChainOrchestrator, "answer", observe_answer):
                    cli.main()
            else:
                import server
                server.app.config.update(TESTING=True, UPLOAD_FOLDER=str(uploads),
                                         SERVER_LOGGING_SETTINGS={"enabled": False})
                with patch.object(server, "load_base_config", lambda: copy.deepcopy(config)):
                    response = server.app.test_client().post("/api/answer", json={
                        "query": query, "autonomy": "autonomous", "max_tokens": 500,
                        "temperature": 0.2, "conversation_id": "autonomy-r2-flask-smoke",
                    })
                status = response.status_code
                records.append(response.get_json())
                assert status == 200
            assert len(records) == 1
            result = records[0]
            assert not result.get("llm_error")
            assert "Au" in result.get("answer", "")
            assert result["control"]["autonomy"]["mode"] == "autonomous"
            stats = extract_llm_stats(result)
            assert stats["llm_call_count"] == stats["llm_calls_with_tokens"] > 0
            assert stats["total_tokens"] == observer.summary()["transport_total_tokens"] > 0
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    from utils.audit_log import sanitize_audit_value
    record = {"entry": args.entry, "started_at": started, "finished_at": utc_now(),
              "http_status": status, "passed": error is None, "error": sanitize_audit_value(error),
              "llm_calls_mocked": False, "listening_server_test": False,
              "result": sanitize_audit_value(records[-1], max_depth=None) if records else None,
              "application_usage": extract_llm_stats(records[-1]) if records else None,
              "transport": observer.summary()}
    write_new(root / "result.json", record)
    print(json.dumps({key: record[key] for key in ("entry", "passed", "error", "application_usage", "transport")}))
    if error:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
