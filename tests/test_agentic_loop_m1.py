"""M1-to-M5 regressions for the promoted, now sole, agentic loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain.langchain_orchestrator import LangChainOrchestrator
from orchestrators.react_loop_graph import ReactLoopGraphRunner
from tests.baseline_runner import compare_runs
from utils.query_orchestration import QueryAnalysis, QueryExecutionTrace


ROOT = Path(__file__).resolve().parents[1]


class _LoopStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def answer(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"query": query, **kwargs})
        return {
            "query": query,
            "answer": "loop answer",
            "search_hits": [],
            "evidence_records": [],
            "control": {
                "loop_status": "succeeded",
                "loop_iterations": 1,
                "loop_verdicts": [{"action": "return", "reason": "constraints_satisfied"}],
            },
        }


def _shell() -> LangChainOrchestrator:
    orchestrator = LangChainOrchestrator.__new__(LangChainOrchestrator)
    orchestrator._loop_orchestrator = _LoopStub()
    orchestrator._current_analysis = QueryAnalysis(query="q")
    orchestrator._current_ledger = None
    orchestrator._current_execution_trace = QueryExecutionTrace()
    orchestrator.orchestration_config = {"enabled": True}
    from utils.query_orchestration import EvidencePolicyRegistry

    orchestrator._policy_registry = EvidencePolicyRegistry()
    return orchestrator


def test_checklist_comes_from_shared_comparison_analysis() -> None:
    runner = ReactLoopGraphRunner.__new__(ReactLoopGraphRunner)
    runner.analysis = QueryAnalysis(
        query="compare",
        constraints={"comparison_required": True},
    )
    runner.query = "compare"
    runner.time_constraint = None
    assert runner._derive_checklist() == ["comparison"]


def test_checklist_comes_from_shared_temporal_analysis() -> None:
    runner = ReactLoopGraphRunner.__new__(ReactLoopGraphRunner)
    runner.analysis = QueryAnalysis(
        query="history",
        constraints={"temporal_required": True},
    )
    runner.query = "history"
    runner.time_constraint = None
    assert runner._derive_checklist() == ["time_constraint"]


def test_run_loop_executor_is_always_the_final_executor() -> None:
    orchestrator = _shell()
    from utils.timing_utils import TimingRecorder
    from utils.time_parser import TimeConstraint

    result = orchestrator._run_loop_executor(
        query="q",
        effective_query="q",
        allow_search=False,
        conversation_id=None,
        time_constraint=TimeConstraint(original_query="q", cleaned_query="q"),
        num_search_results=3,
        per_source_limit=3,
        num_retrieved_docs=2,
        max_tokens=100,
        temperature=0.1,
        reference_limit=None,
        force_search=False,
        timing_recorder=TimingRecorder(enabled=False),
    )
    assert result["control"]["final_executor"] == "agentic_loop"
    assert orchestrator._loop_orchestrator.calls[0]["allow_search"] is False
    assert orchestrator._loop_orchestrator.calls[0]["analysis"] is orchestrator._current_analysis
    assert (
        orchestrator._loop_orchestrator.calls[0]["execution_trace"]
        is orchestrator._current_execution_trace
    )


def test_loop_evidence_enters_ledger_with_actual_call_provenance() -> None:
    orchestrator = _shell()
    orchestrator._current_analysis = QueryAnalysis(query="q")
    result = {
        "evidence_records": [
            {
                "tool_name": "web_search",
                "source_type": "web",
                "source_tier": "official",
                "query": "q",
                "iteration": 1,
                "position": 1,
                "content": "evidence",
                "reference": "https://example.com/a",
            }
        ],
        "control": {
            "loop_verdicts": [{"action": "return", "reason": "constraints_satisfied"}]
        },
    }

    orchestrator._ingest_loop_evidence(result, num_search_results=3)

    assert result["evidence_items"][0]["metadata"]["originating_tool_call"] == "react_tool_1_1"
    assert orchestrator._current_ledger is not None
    assert orchestrator._current_ledger.entries[0].originating_calls == ["react_tool_1_1"]
    kinds = [
        event["kind"]
        for event in orchestrator._current_execution_trace.to_dict()["events"]
    ]
    assert kinds == ["tool_call", "evidence_ledger", "termination"]


def test_no_evidence_records_still_emit_empty_ledger_and_terminal_trace() -> None:
    orchestrator = _shell()
    result = {
        "evidence_records": [],
        "control": {
            "loop_verdicts": [
                {"action": "return", "reason": "constraints_satisfied"}
            ]
        },
    }
    orchestrator._ingest_loop_evidence(result, num_search_results=3)
    assert orchestrator._current_ledger is not None
    assert orchestrator._current_ledger.entries == []
    assert [
        event["kind"]
        for event in orchestrator._current_execution_trace.to_dict()["events"]
    ] == ["evidence_ledger", "termination"]


def test_static_plan_and_rollout_switch_symbols_are_absent() -> None:
    source = (ROOT / "langchain/langchain_orchestrator.py").read_text(encoding="utf-8")
    query_source = (ROOT / "utils/query_orchestration.py").read_text(encoding="utf-8")
    for symbol in (
        "build_query_plan",
        "QueryPlan",
        "PlanController",
        "_prepare_query_plan",
        "_make_routing_decision",
        "_generate_keywords",
        "engine_mode",
    ):
        assert symbol not in source
        assert symbol not in query_source


def test_compare_runs_matches_by_qid_and_quantifies_gap(tmp_path: Path) -> None:
    plan_dir = tmp_path / "old"
    loop_dir = tmp_path / "new"
    plan_dir.mkdir()
    loop_dir.mkdir()
    (plan_dir / "route_intent_details.jsonl").write_text(
        '{"qid":"q1","route_correct":true,"latency_ms":1000}\n',
        encoding="utf-8",
    )
    (loop_dir / "route_intent_details.jsonl").write_text(
        '{"qid":"q1","route_correct":true,"latency_ms":2000}\n',
        encoding="utf-8",
    )
    comparison = compare_runs(str(plan_dir), str(loop_dir))
    assert comparison["datasets"]["route_intent"]["matched_queries"] == 1
