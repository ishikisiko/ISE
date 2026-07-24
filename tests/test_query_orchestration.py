"""Regression coverage for the shared query-planning contract."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from langchain.langchain_rag import SearchRAGChain
from evidence import EvidenceItem
from langchain.langchain_orchestrator import LangChainOrchestrator
from search.search import SearchClient, SearchHit
from utils.audit_log import build_audit_record
from utils.query_orchestration import (
    EvidenceLedger,
    PlanController,
    PlanStepKind,
    PlanStepResult,
    QueryAnalysis,
    QueryExecutionTrace,
    QueryPlan,
    QueryPlanStep,
    VerificationStatus,
    analyze_query,
    build_query_plan,
    merge_optional_analysis,
    verify_evidence_plan,
)


class _SearchStub(SearchClient):
    source_id = "brave"
    display_name = "Brave"

    def __init__(self, hits: list[SearchHit]) -> None:
        super().__init__()
        self.hits = hits

    def search(self, query: str, num_results: int = 5, **kwargs: Any) -> list[SearchHit]:
        self._reset_timings()
        self._append_timing({"source": "brave", "label": "Brave", "duration_ms": 1.0})
        return list(self.hits[:num_results])


class _MultiProviderSearchStub(_SearchStub):
    source_id = "priority"
    display_name = "Priority Search"

    def __init__(self, hits: list[SearchHit]) -> None:
        super().__init__(hits)
        self._last_errors: list[dict[str, str]] = []

    def search(self, query: str, num_results: int = 5, **kwargs: Any) -> list[SearchHit]:
        self._reset_timings()
        self._last_errors = [{"source": "brave", "error": "timeout"}]
        self._append_timing(
            {
                "source": "brave",
                "label": "Brave",
                "duration_ms": 2.0,
                "error": "timeout",
            }
        )
        self._append_timing(
            {
                "source": "firecrawl",
                "label": "Firecrawl",
                "duration_ms": 3.0,
                "fallback": True,
            }
        )
        return list(self.hits[:num_results])

    def get_last_errors(self) -> list[dict[str, str]]:
        return list(self._last_errors)


def test_evidence_package_imports_in_a_fresh_process_without_a_cycle() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from evidence import EvidenceItem; print(EvidenceItem.__name__)",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.rstrip().endswith("EvidenceItem")


class _LLMStub:
    provider = "stub"
    model_name = "stub-model"

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return type("Response", (), {"content": "draft answer", "response_metadata": {}})()


class _GeneralSelector:
    def select_sources(self, query: str, timing_recorder: Any = None) -> tuple[str, list[str]]:
        return "general", []

    def generate_domain_specific_query(self, query: str, domain: str) -> str:
        return query

    def fetch_domain_data(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("general query must not call an unplanned domain API")


class _TemporalLabelSelector(_GeneralSelector):
    def select_sources(self, query: str, timing_recorder: Any = None) -> tuple[str, list[str]]:
        return "temporal_change", []


class _StructuredDomainSelector(_GeneralSelector):
    domain_keywords = {
        "weather": ["weather", "天气"],
        "transportation": ["traffic", "交通"],
        "finance": ["stock", "股票", "股价"],
        "sports": ["sports", "比赛"],
        "location": ["nearby", "附近"],
    }

    def __init__(self) -> None:
        self.calls = 0

    def select_sources(self, query: str, timing_recorder: Any = None) -> tuple[str, list[str]]:
        self.calls += 1
        raise AssertionError("generic evidence plan must not invoke domain classification")


def _item(
    reference: str,
    content: str,
    *,
    tier: str = "official",
    step: str = "web",
) -> EvidenceItem:
    return EvidenceItem(
        source_type="web",
        source_id="brave",
        title=reference,
        content=content,
        reference=reference,
        snippet=content,
        metadata={
            "source_tier": tier,
            "originating_plan_step": step,
            "authorization": "must-not-leak",
        },
    )


def test_pricing_comparison_analysis_is_generic_policy_composition() -> None:
    analysis = analyze_query(
        "对比fable5 api价格和glm5.2,kimik3",
        allow_search=True,
        requested_sources=["brave"],
    )
    plan = build_query_plan(analysis, has_local_docs=False)

    assert analysis.comparison_members == ["fable5", "glm5.2", "kimik3"]
    assert analysis.critical_ambiguity is False
    assert {"authority", "comparison_coverage"}.issubset(plan.policy_names())
    assert "temporal_coverage" not in plan.policy_names()
    assert plan.step_for_kind(PlanStepKind.WEB_SEARCH).allowed_providers == ["brave"]
    assert plan.step_for_kind(PlanStepKind.TEMPORAL_RECOVERY, include_recovery=True) is None


def test_generic_and_explicit_temporal_comparisons_select_different_plans() -> None:
    generic = build_query_plan(
        analyze_query("比较苹果和微软", allow_search=True),
        has_local_docs=False,
    )
    temporal = build_query_plan(
        analyze_query("比较苹果和微软过去三年的营收趋势", allow_search=True),
        has_local_docs=False,
    )

    assert "comparison_coverage" in generic.policy_names()
    assert "temporal_coverage" not in generic.policy_names()
    assert generic.step_for_kind(PlanStepKind.TEMPORAL_RECOVERY, include_recovery=True) is None
    assert {"comparison_coverage", "temporal_coverage"}.issubset(temporal.policy_names())
    assert temporal.step_for_kind(PlanStepKind.TEMPORAL_RECOVERY, include_recovery=True)


def test_analysis_adds_freshness_and_structured_domain_steps_from_shared_constraints() -> None:
    analysis = analyze_query(
        "今天上海天气如何",
        allow_search=True,
        domain_hint="weather",
    )
    plan = build_query_plan(analysis, has_local_docs=False)

    assert analysis.constraints["freshness_required"] is True
    assert "freshness" in plan.policy_names()
    assert plan.step_for_kind(PlanStepKind.DOMAIN_API) is not None
    assert plan.step_for_kind(PlanStepKind.DOMAIN_API).metadata["domain"] == "weather"


def test_ambiguity_and_optional_analysis_never_relax_deterministic_guards() -> None:
    analysis = analyze_query("对比它和 glm5.2 的价格", allow_search=True)
    assert analysis.critical_ambiguity is True
    assert "unresolved_entity_reference" in analysis.ambiguities

    offline = analyze_query("比较苹果和微软", allow_search=False)
    merged = merge_optional_analysis(
        offline,
        {
            "entities": ["Apple"],
            "critical_ambiguity": False,
            "search_allowed": True,
        },
    )
    assert merged.search_allowed is False
    assert merged.requires_evidence is False


def test_plan_controller_enforces_provider_and_budget_boundaries() -> None:
    analysis = QueryAnalysis(query="q", search_allowed=True, requires_evidence=True)
    web = QueryPlanStep(
        step_id="web",
        kind=PlanStepKind.WEB_SEARCH,
        purpose="web",
        allowed_providers=["brave"],
    )
    recovery = QueryPlanStep(
        step_id="recover",
        kind=PlanStepKind.TEMPORAL_RECOVERY,
        purpose="recover",
        recovery_only=True,
    )
    plan = QueryPlan(
        analysis=analysis,
        steps=[web, recovery],
        query_budget=1,
        result_budget=2,
        recovery_budget=1,
    )
    trace = QueryExecutionTrace()
    controller = PlanController(plan, trace)

    first = controller.run_step(
        web,
        lambda step: PlanStepResult(items=[1, 2, 3], providers=["brave"]),
    )
    second = controller.run_step(
        recovery,
        lambda step: PlanStepResult(items=[4], providers=["brave"]),
    )
    blocked = PlanController(plan, QueryExecutionTrace()).run_step(
        web,
        lambda step: PlanStepResult(items=[1], providers=["unplanned"]),
    )

    assert first.items == [1, 2]
    assert second.status == "skipped"
    assert second.reason == "query_budget_exhausted"
    assert blocked.status == "blocked"
    assert blocked.items == []
    assert "brave" in trace.to_dict()["executed"]

    failure_trace = QueryExecutionTrace()
    failed = PlanController(
        QueryPlan(analysis=analysis, steps=[web]),
        failure_trace,
    ).run_step(
        web,
        lambda step: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
    )
    clarification = PlanController(
        QueryPlan(analysis=analysis, clarification_required=True),
        QueryExecutionTrace(),
    ).run_step(web, lambda step: PlanStepResult(items=[1], providers=["brave"]))

    assert failed.status == "error"
    assert failed.reason == "provider unavailable"
    assert failure_trace.to_dict()["events"][-1]["status"] == "error"
    assert clarification.status == "skipped"
    assert clarification.reason == "clarification_required"


def test_ledger_deduplicates_and_only_retains_policy_accepted_evidence() -> None:
    plan = build_query_plan(
        analyze_query("对比fable5 api价格和glm5.2,kimik3", allow_search=True),
        has_local_docs=False,
    )
    ledger = EvidenceLedger(plan)
    ledger.ingest(
        [
            _item("https://blog.example/other", "fable5 glm5.2 kimik3", tier="unknown"),
            _item("https://vendor.example/fable?token=secret", "fable5 price"),
            _item("https://vendor.example/fable#top", "fable5 price duplicate"),
            _item("https://vendor.example/glm", "glm5.2 price"),
            _item("https://vendor.example/kimi", "kimik3 price"),
        ]
    )
    ledger.apply_limits(max_items=3)
    outcome = verify_evidence_plan(plan, ledger)
    summary = ledger.coverage_summary()

    assert summary["merged"] == 1
    assert summary["rejected"] == 1
    assert all(item.metadata["canonical_reference"].find("token") == -1 for item in ledger.retained_items())
    assert len(ledger.retained_items()) == 3
    assert outcome.status == VerificationStatus.COMPLETE


def test_verifier_returns_each_typed_outcome() -> None:
    clarification_plan = build_query_plan(
        analyze_query("对比它和 glm5.2 的价格", allow_search=True),
        has_local_docs=False,
    )
    assert (
        verify_evidence_plan(clarification_plan, EvidenceLedger(clarification_plan)).status
        == VerificationStatus.CLARIFICATION_REQUIRED
    )

    insufficient_plan = build_query_plan(
        analyze_query("fable5 api价格", allow_search=True),
        has_local_docs=False,
    )
    assert (
        verify_evidence_plan(insufficient_plan, EvidenceLedger(insufficient_plan)).status
        == VerificationStatus.EVIDENCE_INSUFFICIENT
    )

    recoverable_plan = build_query_plan(
        analyze_query("fable5 过去三年价格趋势", allow_search=True),
        has_local_docs=False,
    )
    assert (
        verify_evidence_plan(recoverable_plan, EvidenceLedger(recoverable_plan)).status
        == VerificationStatus.RECOVERABLE_GAP
    )


def test_trace_and_audit_projection_strip_sensitive_values() -> None:
    plan = build_query_plan(
        analyze_query("fable5 api价格", allow_search=True),
        has_local_docs=False,
    )
    trace = QueryExecutionTrace(configured=["brave"], requested=["brave"], eligible=["brave"])
    trace.record_plan(plan)
    step = plan.step_for_kind(PlanStepKind.WEB_SEARCH)
    trace.begin(step)
    trace.finish(
        step,
        status="done",
        providers=["brave"],
        attempts=[{"provider": "brave", "authorization": "secret", "headers": {"token": "bad"}}],
    )
    projection = trace.to_dict()
    record = build_audit_record(
        conversation_id="trace",
        query="q token=secret https://example.com/page?token=secret",
        allow_search=True,
        result={
            "control": {
                "execution_trace": projection,
                "query_plan": {"large": list(range(2000))},
            }
        },
        max_bytes_per_record=900,
    )

    rendered = str(record)
    assert "secret" not in rendered
    assert "bad" not in rendered
    assert projection["configured"] == ["brave"]
    assert projection["executed"] == ["brave"]
    assert record["control"]["execution_trace"]["executed"] == ["brave"]


def test_rag_only_executes_temporal_recovery_when_the_plan_declares_it(monkeypatch) -> None:
    chain = SearchRAGChain(
        llm=object(),
        search_client=_SearchStub([SearchHit("result", "https://example.com/a", "no year")]),
        data_path=None,
    )
    calls: list[str] = []
    monkeypatch.setattr(
        chain,
        "_perform_granular_search_fallback",
        lambda *args, **kwargs: calls.append("recovery") or [],
    )

    generic = build_query_plan(
        analyze_query("比较fable5和glm5.2", allow_search=True),
        has_local_docs=False,
    )
    generic_trace = QueryExecutionTrace()
    chain._retrieve_evidence(
        "比较fable5和glm5.2",
        search_query=None,
        num_search_results=3,
        per_source_limit=3,
        num_retrieved_docs=1,
        enable_search=True,
        enable_local_docs=False,
        freshness=None,
        date_restrict=None,
        timing_recorder=None,
        query_plan=generic,
        evidence_ledger=EvidenceLedger(generic),
        execution_trace=generic_trace,
        plan_controller=PlanController(generic, generic_trace),
    )
    assert calls == []

    temporal = build_query_plan(
        analyze_query("比较fable5和glm5.2过去三年趋势", allow_search=True),
        has_local_docs=False,
    )
    temporal_trace = QueryExecutionTrace()
    chain._retrieve_evidence(
        "比较fable5和glm5.2过去三年趋势",
        search_query=None,
        num_search_results=3,
        per_source_limit=3,
        num_retrieved_docs=1,
        enable_search=True,
        enable_local_docs=False,
        freshness=None,
        date_restrict=None,
        timing_recorder=None,
        query_plan=temporal,
        evidence_ledger=EvidenceLedger(temporal),
        execution_trace=temporal_trace,
        plan_controller=PlanController(temporal, temporal_trace),
    )
    assert calls == ["recovery"]


def test_rag_preserves_multi_provider_fallback_timing_in_the_plan_trace() -> None:
    analysis = QueryAnalysis(
        query="provider fallback",
        search_allowed=True,
        requires_evidence=True,
        requested_sources=["brave", "firecrawl"],
    )
    plan = build_query_plan(analysis, has_local_docs=False)
    trace = QueryExecutionTrace(
        configured=["brave", "firecrawl"],
        requested=["brave", "firecrawl"],
        eligible=["brave", "firecrawl"],
    )
    chain = SearchRAGChain(
        llm=object(),
        search_client=_MultiProviderSearchStub(
            [SearchHit("result", "https://example.com/a", "usable evidence")]
        ),
        data_path=None,
    )
    retrieval = chain._retrieve_evidence(
        "provider fallback",
        search_query=None,
        num_search_results=3,
        per_source_limit=3,
        num_retrieved_docs=1,
        enable_search=True,
        enable_local_docs=False,
        freshness=None,
        date_restrict=None,
        timing_recorder=None,
        query_plan=plan,
        evidence_ledger=EvidenceLedger(plan),
        execution_trace=trace,
        plan_controller=PlanController(plan, trace),
    )

    attempts = retrieval["search_provider_trace"]["attempts"]
    assert retrieval["search_provider_trace"]["executed"] == ["brave", "firecrawl"]
    assert attempts[0]["provider"] == "brave"
    assert attempts[0]["reason"] == "timeout"
    assert attempts[1] == {
        "provider": "firecrawl",
        "status": "done",
        "duration_ms": 3.0,
        "fallback": True,
    }
    assert trace.to_dict()["executed"] == ["brave", "firecrawl"]


def test_default_orchestrator_exposes_plan_trace_and_enforces_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(LangChainOrchestrator, "_build_decision_chain", lambda self: None)
    monkeypatch.setattr(LangChainOrchestrator, "_build_keyword_chain", lambda self: None)
    search = _SearchStub(
        [
            SearchHit("Fable", "https://third.example/fable", "fable5 api price"),
            SearchHit("GLM", "https://third.example/glm", "glm5.2 api price"),
            SearchHit("Kimi", "https://third.example/kimi", "kimik3 api price"),
        ]
    )
    orchestrator = LangChainOrchestrator(
        llm=_LLMStub(),
        routing_llm=_LLMStub(),
        classifier_llm=_LLMStub(),
        search_client=search,
        source_selector=_TemporalLabelSelector(),
        requested_search_sources=["brave"],
        active_search_sources=["brave"],
        configured_search_sources=["brave"],
        config={"orchestration": {"enforce_verification": True}},
    )
    monkeypatch.setattr(
        orchestrator,
        "_make_routing_decision",
        lambda *args, **kwargs: {"needs_search": True, "reason": "test"},
    )
    monkeypatch.setattr(orchestrator, "_generate_keywords", lambda *args, **kwargs: {"keywords": []})

    result = orchestrator.answer("对比fable5 api价格和glm5.2,kimik3")
    control = result["control"]

    assert result["answer"].startswith("当前检索到的证据不足")
    assert control["keyword_generation"]["fallback_used"] is True
    assert control["verification"]["status"] == "evidence_insufficient"
    assert [step["kind"] for step in control["query_plan"]["steps"]] == ["web_search"]
    assert control["execution_trace"]["executed"] == ["brave"]
    assert control["providers"] == {
        "configured": ["brave"],
        "requested": ["brave"],
        "eligible": ["brave"],
        "executed": ["brave"],
    }


def test_generic_evidence_plan_skips_legacy_domain_classification(monkeypatch) -> None:
    monkeypatch.setattr(LangChainOrchestrator, "_build_decision_chain", lambda self: None)
    monkeypatch.setattr(LangChainOrchestrator, "_build_keyword_chain", lambda self: None)
    selector = _StructuredDomainSelector()
    orchestrator = LangChainOrchestrator(
        llm=_LLMStub(),
        routing_llm=_LLMStub(),
        classifier_llm=_LLMStub(),
        search_client=_SearchStub(
            [SearchHit("Fable", "https://third.example/fable", "fable5 api price")]
        ),
        source_selector=selector,
        requested_search_sources=["brave"],
        active_search_sources=["brave"],
        configured_search_sources=["brave"],
        config={"orchestration": {"enforce_verification": True}},
    )
    monkeypatch.setattr(
        orchestrator,
        "_make_routing_decision",
        lambda *args, **kwargs: {"needs_search": True, "reason": "test"},
    )
    monkeypatch.setattr(orchestrator, "_generate_keywords", lambda *args, **kwargs: {"keywords": []})

    result = orchestrator.answer("对比fable5 api价格和glm5.2,kimik3")

    assert selector.calls == 0
    assert result["control"]["query_plan"]["steps"][0]["kind"] == "web_search"
    assert result["control"]["execution_trace"]["executed"] == ["brave"]


def test_flask_response_preserves_additive_orchestration_control(monkeypatch) -> None:
    import server

    class Pipeline:
        def answer(self, query: str, **kwargs: Any) -> dict[str, Any]:
            return {
                "query": query,
                "answer": "controlled result",
                "search_hits": [],
                "retrieved_docs": [],
                "control": {
                    "search_mode": "search",
                    "query_plan": {"steps": [{"kind": "web_search"}]},
                    "execution_trace": {"executed": ["brave"], "events": []},
                    "verification": {"status": "complete"},
                },
            }

    monkeypatch.setattr(server, "build_pipeline", lambda **kwargs: Pipeline())
    server.app.config["TESTING"] = True
    with server.app.test_client() as client:
        response = client.post("/api/answer", json={"query": "controlled API query"})

    assert response.status_code == 200
    control = response.get_json()["control"]
    assert control["query_plan"]["steps"][0]["kind"] == "web_search"
    assert control["execution_trace"]["executed"] == ["brave"]
    assert control["verification"]["status"] == "complete"
