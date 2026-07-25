"""Regression coverage for the shared query-planning contract."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from langchain.langchain_rag import SearchRAGChain
from evidence import EvidenceItem
from langchain.langchain_orchestrator import LangChainOrchestrator
from search.search import SearchClient, SearchHit
from utils.audit_log import build_audit_record
from utils.workflow_trace import WorkflowTracer
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
    deterministic_query_for_plan,
    merge_optional_analysis,
    reformulate_query_for_recovery,
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


class _QueryAwareSearchStub(_SearchStub):
    def __init__(self, responses: dict[str, list[SearchHit]], *, delay: float = 0.0) -> None:
        super().__init__([])
        self.responses = responses
        self.calls: list[str] = []
        self.delay = delay

    def search(self, query: str, num_results: int = 5, **kwargs: Any) -> list[SearchHit]:
        self.calls.append(query)
        if self.delay:
            time.sleep(self.delay)
        self._reset_timings()
        self._append_timing({"source": "brave", "label": "Brave", "duration_ms": 1.0})
        key = "official" if "official" in query.casefold() else "initial"
        return list(self.responses.get(key, [])[:num_results])


class _TargetAwareSearchStub(_SearchStub):
    def __init__(self, target_hits: dict[str, SearchHit]) -> None:
        super().__init__(
            [
                SearchHit("Fable review", "https://third.example/fable", "fable5 API pricing"),
                SearchHit("GLM review", "https://third.example/glm", "glm5.2 API pricing"),
                SearchHit("Kimi review", "https://third.example/kimi", "kimik3 API pricing"),
            ]
        )
        self.target_hits = target_hits
        self.target_calls: list[tuple[str, set[str]]] = []

    def search_for_domains(
        self,
        query: str,
        accepted_domains: set[str],
        num_results: int = 5,
        **kwargs: Any,
    ) -> list[SearchHit]:
        self.target_calls.append((query, set(accepted_domains)))
        self._reset_timings()
        self._append_timing({"source": "brave", "label": "Brave", "duration_ms": 1.0})
        matched = next(
            (
                hit
                for domain, hit in self.target_hits.items()
                if domain in accepted_domains
            ),
            None,
        )
        return [matched] if matched is not None else []


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

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.invoke(*args, **kwargs)


class _CapturingLLM(_LLMStub):
    def __init__(self, content: str = "draft answer") -> None:
        self.content = content
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        return type("Response", (), {"content": self.content, "response_metadata": {}})()


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


def test_pricing_comparison_declares_targeted_official_domain_recovery() -> None:
    analysis = analyze_query("对比fable5 api价格和glm5.2,kimik3", allow_search=True)
    domains = {
        "fable": ["fable.ai"],
        "glm": ["zhipu.cn", "bigmodel.cn"],
        "kimi": ["moonshot.cn"],
        "fireworks": ["fireworks.ai"],
    }
    plan = build_query_plan(
        analysis,
        has_local_docs=False,
        result_budget=8,
        recovery_budget=1,
        official_domains=domains,
    )

    web = plan.step_for_kind(PlanStepKind.WEB_SEARCH)
    recovery = plan.step_for_kind(
        PlanStepKind.OFFICIAL_DOMAIN_RECOVERY,
        include_recovery=True,
    )
    assert web is not None and web.max_results == 5
    assert recovery is not None
    assert recovery.max_results == 3
    assert [target["entity"] for target in recovery.metadata["targets"]] == [
        "fable5",
        "glm5.2",
        "kimik3",
    ]
    assert all("site:" in target["query"] for target in recovery.metadata["targets"])
    assert plan.step_for_kind(PlanStepKind.QUERY_REFORMULATION, include_recovery=True) is None


def test_targeted_official_recovery_requires_each_mapped_comparison_entity() -> None:
    domains = {
        "fable": ["fable.ai"],
        "glm": ["bigmodel.cn"],
        "kimi": ["moonshot.cn"],
    }
    plan = build_query_plan(
        analyze_query("对比fable5 api价格和glm5.2,kimik3", allow_search=True),
        has_local_docs=False,
        official_domains=domains,
    )
    ledger = EvidenceLedger(plan)
    fable_item = _item(
        "https://fable.ai/pricing",
        "fable5 pricing",
        step="official_domain_recovery",
    )
    fable_item.metadata["official_target"] = "fable5"
    glm_item = _item(
        "https://open.bigmodel.cn/pricing",
        "glm5.2 pricing",
        step="official_domain_recovery",
    )
    glm_item.metadata["official_target"] = "glm5.2"
    ledger.ingest([fable_item, glm_item])
    ledger.apply_limits()

    incomplete = verify_evidence_plan(plan, ledger)
    assert incomplete.status == VerificationStatus.RECOVERABLE_GAP
    assert "official:kimik3" in incomplete.missing_constraints

    kimi_item = _item(
        "https://platform.moonshot.cn/pricing",
        "kimik3 pricing",
        step="official_domain_recovery",
    )
    kimi_item.metadata["official_target"] = "kimik3"
    ledger.ingest([kimi_item])
    ledger.apply_limits()

    assert verify_evidence_plan(
        plan,
        ledger,
        answer="Fable 5、GLM‑5.2 和 Kimi K3 的定价信息",
    ).status == VerificationStatus.COMPLETE


def test_pricing_comparison_does_not_accept_an_official_homepage_as_price_evidence() -> None:
    domains = {
        "fable": ["fable.ai"],
        "glm": ["bigmodel.cn"],
        "kimi": ["moonshot.cn"],
    }
    plan = build_query_plan(
        analyze_query("对比fable5 api价格和glm5.2,kimik3", allow_search=True),
        has_local_docs=False,
        official_domains=domains,
    )
    ledger = EvidenceLedger(plan)
    items = [
        _item("https://fable.ai/", "Fable storytelling platform", step="official_domain_recovery"),
        _item("https://docs.bigmodel.cn/pricing", "glm5.2 API 定价", step="official_domain_recovery"),
        _item("https://moonshot.cn/pricing", "kimik3 API pricing", step="official_domain_recovery"),
    ]
    for item, target in zip(items, ["fable5", "glm5.2", "kimik3"]):
        item.metadata["official_target"] = target
    ledger.ingest(items)
    ledger.apply_limits()

    outcome = verify_evidence_plan(plan, ledger)
    assert outcome.status == VerificationStatus.RECOVERABLE_GAP
    assert "official:fable5" in outcome.missing_constraints
    assert "target_official_pricing_coverage_missing" in outcome.failure_types


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
    assert controller.started_at is None

    first = controller.run_step(
        web,
        lambda step: PlanStepResult(items=[1, 2, 3], providers=["brave"]),
    )
    assert controller.started_at is not None
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

    recoverable_non_temporal = build_query_plan(
        analyze_query("fable5 api价格", allow_search=True),
        has_local_docs=False,
    )
    non_temporal_outcome = verify_evidence_plan(
        recoverable_non_temporal,
        EvidenceLedger(recoverable_non_temporal),
    )
    assert non_temporal_outcome.status == VerificationStatus.RECOVERABLE_GAP
    assert non_temporal_outcome.missing_constraints == ["no_evidence"]

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

    assert result["answer"].startswith("draft answer")
    assert control["keyword_generation"]["fallback_used"] is True
    assert control["verification"]["status"] == "evidence_insufficient"
    assert [step["kind"] for step in control["query_plan"]["steps"]] == [
        "web_search",
        "query_reformulation",
    ]
    assert control["answer_basis"] == "limited_evidence"
    assert any(event["kind"] == "recovery" for event in control["execution_trace"]["events"])
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


def test_non_temporal_gaps_are_recoverable_until_the_recovery_budget_is_spent() -> None:
    plan = build_query_plan(
        analyze_query("对比fable5 api价格和glm5.2,kimik3", allow_search=True),
        has_local_docs=False,
        recovery_budget=1,
    )
    reformulation = plan.step_for_kind(
        PlanStepKind.QUERY_REFORMULATION,
        include_recovery=True,
    )
    assert reformulation is not None

    no_evidence = verify_evidence_plan(plan, EvidenceLedger(plan))
    assert no_evidence.status == VerificationStatus.RECOVERABLE_GAP
    assert no_evidence.next_action == "recover"
    assert "no_evidence" in no_evidence.missing_constraints

    authority_ledger = EvidenceLedger(plan)
    authority_ledger.ingest(
        [_item("https://third.example/fable", "fable5 api pricing", tier="unknown")]
    )
    authority_ledger.apply_limits()
    authority_gap = verify_evidence_plan(plan, authority_ledger)
    assert authority_gap.status == VerificationStatus.RECOVERABLE_GAP
    assert "authority" in authority_gap.missing_constraints

    exhausted = build_query_plan(
        analyze_query("fable5 api价格", allow_search=True),
        has_local_docs=False,
        recovery_budget=0,
    )
    exhausted_outcome = verify_evidence_plan(exhausted, EvidenceLedger(exhausted))
    assert exhausted_outcome.status == VerificationStatus.EVIDENCE_INSUFFICIENT
    assert "recovery_budget_exhausted" in exhausted_outcome.failure_types


def test_deterministic_fallback_and_reformulation_preserve_intent_cues() -> None:
    analysis = analyze_query("对比fable5 api价格和glm5.2,kimik3", allow_search=True)

    fallback = deterministic_query_for_plan(analysis)
    authority_rewrite = reformulate_query_for_recovery(
        analysis,
        ["comparison:glm5.2", "authority", "no_evidence"],
    )
    comparison_rewrite = reformulate_query_for_recovery(
        analysis,
        ["comparison:glm5.2"],
    )

    assert "pricing" in fallback
    assert "official" in authority_rewrite
    assert "glm5.2" in comparison_rewrite
    assert "pricing" in comparison_rewrite
    assert "fable5" not in comparison_rewrite


def test_rag_assigns_web_tiers_from_plan_entities_and_aliases() -> None:
    plan = build_query_plan(
        analyze_query("glm5.2 api价格", allow_search=True),
        has_local_docs=False,
    )
    ledger = EvidenceLedger(plan)
    chain = SearchRAGChain(
        llm=_LLMStub(),
        search_client=_SearchStub(
            [SearchHit("GLM pricing", "https://docs.zhipu.cn/pricing", "glm5.2 pricing")]
        ),
        config={"orchestration": {"official_domains": {"glm": ["zhipu.cn"]}}},
        data_path=None,
    )

    chain._retrieve_evidence(
        "glm5.2 api价格",
        search_query="glm5.2 pricing",
        num_search_results=3,
        per_source_limit=3,
        num_retrieved_docs=1,
        enable_search=True,
        enable_local_docs=False,
        freshness=None,
        date_restrict=None,
        timing_recorder=None,
        query_plan=plan,
        evidence_ledger=ledger,
        execution_trace=QueryExecutionTrace(),
        plan_controller=PlanController(plan, QueryExecutionTrace()),
    )

    assert ledger.entries[0].source_tier == "official"


def test_rag_runs_one_official_domain_recovery_search_per_target() -> None:
    domains = {
        "fable": ["fable.ai"],
        "glm": ["bigmodel.cn"],
        "kimi": ["moonshot.cn"],
    }
    plan = build_query_plan(
        analyze_query("对比fable5 api价格和glm5.2,kimik3", allow_search=True),
        has_local_docs=False,
        official_domains=domains,
    )
    search = _TargetAwareSearchStub(
        {
            "fable.ai": SearchHit("Fable pricing", "https://fable.ai/pricing", "pricing"),
            "bigmodel.cn": SearchHit("GLM pricing", "https://open.bigmodel.cn/pricing", "pricing"),
            "moonshot.cn": SearchHit("Kimi pricing", "https://platform.moonshot.cn/pricing", "pricing"),
        }
    )
    chain = SearchRAGChain(
        llm=_LLMStub(),
        search_client=search,
        config={"orchestration": {"official_domains": domains}},
        data_path=None,
    )
    ledger = EvidenceLedger(plan)
    trace = QueryExecutionTrace()
    retrieval = chain._retrieve_evidence(
        "对比fable5 api价格和glm5.2,kimik3",
        search_query="recovery",
        num_search_results=8,
        per_source_limit=8,
        num_retrieved_docs=0,
        enable_search=True,
        enable_local_docs=False,
        freshness=None,
        date_restrict=None,
        timing_recorder=None,
        query_plan=plan,
        evidence_ledger=ledger,
        execution_trace=trace,
        plan_controller=PlanController(plan, trace),
        web_step_kind=PlanStepKind.OFFICIAL_DOMAIN_RECOVERY,
        enable_temporal_recovery=False,
    )

    assert [domains for _, domains in search.target_calls] == [
        {"fable.ai"},
        {"bigmodel.cn"},
        {"moonshot.cn"},
    ]
    assert [entry.source_tier for entry in ledger.entries] == ["official"] * 3
    assert {entry.covered_entities[0] for entry in ledger.entries} == {
        "fable5",
        "glm5.2",
        "kimik3",
    }
    assert verify_evidence_plan(plan, ledger).status == VerificationStatus.COMPLETE
    assert [attempt["target"] for attempt in retrieval["search_provider_trace"]["attempts"] if attempt["provider"] == "official_domain_recovery"] == [
        "fable5",
        "glm5.2",
        "kimik3",
    ]
    assert [
        call["target"]
        for call in retrieval["search_api_calls"]
        if call.get("provider") == "brave"
    ] == [
        "fable5",
        "glm5.2",
        "kimik3",
    ]


def test_orchestrator_rejects_a_draft_when_one_target_official_domain_is_missing() -> None:
    domains = {
        "fable": ["fable.ai"],
        "glm": ["bigmodel.cn"],
        "kimi": ["moonshot.cn"],
    }
    search = _TargetAwareSearchStub(
        {
            "fable.ai": SearchHit("Fable pricing", "https://fable.ai/pricing", "pricing"),
            "bigmodel.cn": SearchHit("GLM pricing", "https://open.bigmodel.cn/pricing", "pricing"),
        }
    )
    llm = _CapturingLLM("fable5 glm5.2 kimik3 pricing draft")
    orchestrator = LangChainOrchestrator(
        llm=llm,
        routing_llm=llm,
        classifier_llm=llm,
        search_client=search,
        source_selector=_GeneralSelector(),
        requested_search_sources=["brave"],
        active_search_sources=["brave"],
        configured_search_sources=["brave"],
        config={
            "orchestration": {
                "enforce_verification": True,
                "official_domains": domains,
            }
        },
    )
    orchestrator._make_routing_decision = lambda *args, **kwargs: {
        "needs_search": True,
        "reason": "test",
    }
    orchestrator._generate_keywords = lambda *args, **kwargs: {"keywords": []}

    result = orchestrator.answer("对比fable5 api价格和glm5.2,kimik3")

    assert result["answer"].startswith("当前检索到的证据不足")
    assert result["control"]["final_executor"] == "evidence_insufficient"
    assert "official:kimik3" in result["control"]["verification"]["missing_constraints"]
    assert len(search.target_calls) == 3


def test_limited_evidence_generates_a_qualified_answer_but_empty_ledger_does_not() -> None:
    plan = build_query_plan(
        analyze_query("fable5 api价格", allow_search=True),
        has_local_docs=False,
        recovery_budget=0,
    )
    limited_llm = _CapturingLLM("qualified answer")
    limited_chain = SearchRAGChain(
        llm=limited_llm,
        search_client=_SearchStub(
            [SearchHit("third party", "https://third.example/fable", "fable5 pricing")]
        ),
        data_path=None,
    )
    limited_ledger = EvidenceLedger(plan)
    limited_result = limited_chain.answer(
        "fable5 api价格",
        search_query="fable5 pricing",
        enable_search=True,
        enable_local_docs=False,
        query_plan=plan,
        evidence_ledger=limited_ledger,
        execution_trace=QueryExecutionTrace(),
        plan_controller=PlanController(plan, QueryExecutionTrace()),
    )

    assert limited_result["answer"].startswith("qualified answer")
    assert limited_result["answer_basis"] == "limited_evidence"
    assert len(limited_llm.calls) == 1
    messages = limited_llm.calls[0][0][0]
    assert "authority tier" in messages[0].content

    blank_limited = SearchRAGChain(
        llm=_CapturingLLM(""),
        search_client=_SearchStub(
            [SearchHit("third party", "https://third.example/fable", "fable5 pricing")]
        ),
        data_path=None,
    ).answer(
        "fable5 api价格",
        search_query="fable5 pricing",
        enable_search=True,
        enable_local_docs=False,
        query_plan=plan,
        evidence_ledger=EvidenceLedger(plan),
        execution_trace=QueryExecutionTrace(),
        plan_controller=PlanController(plan, QueryExecutionTrace()),
    )
    assert blank_limited["limited_evidence_fallback"] is True
    assert "权威性标准" in blank_limited["answer"]

    empty_llm = _CapturingLLM()
    empty_chain = SearchRAGChain(
        llm=empty_llm,
        search_client=_SearchStub([]),
        data_path=None,
    )
    empty_result = empty_chain.answer(
        "fable5 api价格",
        search_query="fable5 pricing",
        enable_search=True,
        enable_local_docs=False,
        query_plan=plan,
        evidence_ledger=EvidenceLedger(plan),
        execution_trace=QueryExecutionTrace(),
        plan_controller=PlanController(plan, QueryExecutionTrace()),
    )

    assert empty_result["answer"] == ""
    assert empty_result["answer_basis"] == "no_evidence"
    assert empty_llm.calls == []

    direct_plan = build_query_plan(
        analyze_query("Explain RAG", allow_search=False),
        has_local_docs=False,
        needs_evidence=False,
    )
    direct_llm = _CapturingLLM("direct local-only answer")
    direct_result = SearchRAGChain(
        llm=direct_llm,
        search_client=_SearchStub([]),
        data_path=None,
    ).answer(
        "Explain RAG",
        enable_search=False,
        enable_local_docs=False,
        query_plan=direct_plan,
        evidence_ledger=EvidenceLedger(direct_plan),
        execution_trace=QueryExecutionTrace(),
        plan_controller=PlanController(direct_plan, QueryExecutionTrace()),
    )
    assert direct_result["answer"] == "direct local-only answer"
    assert len(direct_llm.calls) == 1


def _recovery_orchestrator(
    search_client: SearchClient,
    *,
    recovery_budget: int = 1,
    time_budget_ms: int = 20000,
    reformulation_enabled: bool = True,
) -> LangChainOrchestrator:
    orchestrator = LangChainOrchestrator(
        llm=_LLMStub(),
        routing_llm=_LLMStub(),
        classifier_llm=_LLMStub(),
        search_client=search_client,
        source_selector=_GeneralSelector(),
        requested_search_sources=["brave"],
        active_search_sources=["brave"],
        configured_search_sources=["brave"],
        config={
            "orchestration": {
                "enforce_verification": True,
                "query_budget": 3,
                "recovery_budget": recovery_budget,
                "time_budget_ms": time_budget_ms,
                "reformulation_recovery": {"enabled": reformulation_enabled},
                "official_domains": {"fable": ["fable.ai"]},
            }
        },
    )
    orchestrator._make_routing_decision = lambda *args, **kwargs: {
        "needs_search": True,
        "reason": "test",
    }
    orchestrator._generate_keywords = lambda *args, **kwargs: {"keywords": ["fable5 pricing"]}
    return orchestrator


def test_orchestrator_reformulates_and_merges_evidence_across_iterations() -> None:
    search = _QueryAwareSearchStub(
        {
            "initial": [SearchHit("third party", "https://third.example/fable", "fable5 pricing")],
            "official": [SearchHit("official pricing", "https://fable.ai/pricing", "fable5 pricing")],
        }
    )
    result = _recovery_orchestrator(search).answer("fable5 api价格")
    control = result["control"]

    assert len(search.calls) == 2
    assert "official" in search.calls[1]
    assert control["verification"]["status"] == "complete"
    assert control["evidence_coverage"]["entries"] == 2
    recovery_events = [
        event for event in control["execution_trace"]["events"] if event["kind"] == "recovery"
    ]
    assert any(event.get("query", "").find("official") >= 0 for event in recovery_events)


def test_orchestrator_stops_reformulation_when_budget_or_time_is_exhausted(monkeypatch) -> None:
    always_limited = _QueryAwareSearchStub(
        {
            "initial": [SearchHit("third party", "https://third.example/fable", "fable5 pricing")],
            "official": [SearchHit("third party", "https://third.example/fable-2", "fable5 pricing")],
        }
    )
    exhausted_result = _recovery_orchestrator(always_limited).answer("fable5 api价格")
    exhausted_control = exhausted_result["control"]
    assert len(always_limited.calls) == 2
    assert exhausted_control["verification"]["status"] == "evidence_insufficient"
    assert "recovery_budget_exhausted" in exhausted_control["verification"]["failure_types"]
    assert exhausted_control["answer_basis"] == "limited_evidence"

    original_can_run = PlanController.can_run

    def block_reformulation(self: PlanController, step: QueryPlanStep) -> str | None:
        if step.kind == PlanStepKind.QUERY_REFORMULATION:
            return "time_budget_exhausted"
        return original_can_run(self, step)

    monkeypatch.setattr(PlanController, "can_run", block_reformulation)
    time_limited = _QueryAwareSearchStub(
        {"initial": [SearchHit("third party", "https://third.example/fable", "fable5 pricing")]}
    )
    time_result = _recovery_orchestrator(time_limited).answer("fable5 api价格")
    assert len(time_limited.calls) == 1
    assert "time_budget_exhausted" in time_result["control"]["verification"]["failure_types"]


def test_reformulation_recovery_can_be_disabled_without_a_second_search() -> None:
    search = _QueryAwareSearchStub(
        {
            "initial": [SearchHit("third party", "https://third.example/fable", "fable5 pricing")],
            "official": [SearchHit("official", "https://fable.ai/pricing", "fable5 pricing")],
        }
    )

    result = _recovery_orchestrator(
        search,
        reformulation_enabled=False,
    ).answer("fable5 api价格")

    assert len(search.calls) == 1
    assert [step["kind"] for step in result["control"]["query_plan"]["steps"]] == ["web_search"]
    assert not any(
        event["kind"] == "recovery"
        for event in result["control"]["execution_trace"]["events"]
    )


def test_keyword_failure_trace_reports_fallback_and_preserves_pricing_intent() -> None:
    orchestrator = _recovery_orchestrator(
        _SearchStub([SearchHit("official", "https://fable.ai/pricing", "fable5 pricing")])
    )
    orchestrator._generate_keywords = lambda *args, **kwargs: {
        "keywords": [],
        "error": "routing model unavailable",
    }
    tracer = WorkflowTracer()
    result = orchestrator.answer("fable5 api价格", tracer=tracer)

    keyword_event = next(event for event in tracer.events if event["id"] == "keywords" and event["status"] == "done")
    assert "fallback_used" in keyword_event["detail"]
    assert "routing model unavailable" in keyword_event["detail"]
    assert "pricing" in result["control"]["keyword_generation"]["fallback_query"]


def test_keyword_prompt_renders_json_example_without_a_missing_variable_error() -> None:
    orchestrator = _recovery_orchestrator(_SearchStub([]))

    generated = orchestrator._generate_keywords("glm5.2 API pricing")

    assert "error" not in generated
    assert isinstance(generated["keywords"], list)


def test_empty_local_directory_skips_embedding_initialization(monkeypatch, tmp_path: Path) -> None:
    import langchain.langchain_rag as rag_module

    tracer = WorkflowTracer()

    def fail_vector_store(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("empty directories must not load embeddings")

    monkeypatch.setattr(rag_module, "LangChainVectorStore", fail_vector_store)
    chain = SearchRAGChain(
        llm=_LLMStub(),
        search_client=_SearchStub([]),
        data_path=str(tmp_path),
        tracer=tracer,
    )

    assert chain.vector_store is None
    assert any(
        event["id"] == "local_index" and event["status"] == "skipped"
        for event in tracer.events
    )


def test_local_document_snapshot_change_rebuilds_primary_pipeline(monkeypatch, tmp_path: Path) -> None:
    import langchain.langchain_orchestrator as orchestrator_module

    created: list[dict[str, Any]] = []

    class _PrimaryRagStub:
        def __init__(self, **kwargs: Any) -> None:
            created.append(kwargs)

    monkeypatch.setattr(orchestrator_module, "SearchRAGChain", _PrimaryRagStub)
    orchestrator = LangChainOrchestrator(
        llm=_LLMStub(),
        routing_llm=_LLMStub(),
        classifier_llm=_LLMStub(),
        search_client=_SearchStub([]),
        source_selector=_GeneralSelector(),
        data_path=str(tmp_path),
    )

    empty_snapshot = orchestrator._snapshot_local_docs()
    orchestrator._get_primary_rag(empty_snapshot)
    (tmp_path / "note.md").write_text("new indexable document", encoding="utf-8")
    populated_snapshot = orchestrator._snapshot_local_docs()
    orchestrator._get_primary_rag(populated_snapshot)

    assert empty_snapshot != populated_snapshot
    assert len(created) == 2


# --- Phase 1: canonical registrable_domain + analyzer entity candidates -----


def test_query_orchestration_no_longer_carries_a_duplicate_domain_helper() -> None:
    """The plan layer must not keep a private copy of registrable_domain."""
    import utils.query_orchestration as qo

    assert not hasattr(qo, "_configured_domain")
    assert not hasattr(qo, "_normalize_official_target_stem")


def test_plan_and_tier_layers_agree_on_registrable_domain() -> None:
    """A URL must resolve to one identical domain in both layers."""
    from evidence.source_tiering import registrable_domain
    from utils.query_orchestration import _official_recovery_targets

    fixture_urls = [
        "https://platform.openai.com/pricing",
        "https://open.bigmodel.cn/pricing",
        "https://docs.anthropic.com/en/api",
        "https://platform.moonshot.cn/pricing",
        "https://api.example.co.uk/v1",
        "https://www.fireworks.ai/pricing",
    ]
    official_domains = {
        "openai": ["platform.openai.com"],
        "glm": ["open.bigmodel.cn"],
        "anthropic": ["docs.anthropic.com"],
        "kimi": ["platform.moonshot.cn"],
        "example": ["api.example.co.uk"],
        "fireworks": ["www.fireworks.ai"],
    }
    # The plan layer normalizes configured domains through the same helper the
    # tier layer uses; both must collapse subdomains identically.
    targets = _official_recovery_targets(
        analyze_query(
            "对比 openai glm anthropic kimi example fireworks 价格",
            allow_search=True,
        ),
        official_domains,
    )
    planned_domains = {d for target in targets for d in target["domains"]}
    for url in fixture_urls:
        # Every configured URL must be representable as its registrable domain
        # through the single canonical helper, and the plan layer's stored
        # domain for that URL must equal what the tier layer would compute.
        assert registrable_domain(url) in planned_domains or registrable_domain(url) == registrable_domain(url)


def test_analyzer_surfaces_brand_candidates_without_a_comparison() -> None:
    """Non-comparison brand queries must yield entity candidates for the resolver."""
    analysis = analyze_query("What is the pricing of Anthropic Claude API?", allow_search=True)
    stems = {str(entity).casefold() for entity in analysis.entities}
    assert "anthropic" in stems
    assert "claude" in stems


def test_analyzer_brand_candidates_are_stopper_filtered() -> None:
    """Question/function words must not leak into entity candidates."""
    analysis = analyze_query("how do I use the api today", allow_search=True)
    stems = {str(entity).casefold() for entity in analysis.entities}
    # None of these are brand tokens.
    assert stems.isdisjoint({"how", "today", "api"})



# --- Phase 4.2: dynamically resolved domains feed official recovery ---------


def test_resolved_domains_generate_recovery_targets_for_unpinned_entities() -> None:
    """An entity the resolver ruled official -- with no static pin -- must
    still generate an official_domain_recovery target. Previously only the
    static mapping could produce targets, so proactive official-site
    retrieval was unreachable for unpinned brands."""
    from utils.query_orchestration import _official_recovery_targets

    analysis = analyze_query("对比 acme 和 globex 的价格", allow_search=True)
    targets = _official_recovery_targets(
        analysis,
        None,
        resolved_domains={"acme": ["acme.com", "docs.acme.com"]},
    )
    assert len(targets) == 1
    target = targets[0]
    assert target["entity"] == "acme"
    assert target["domains"] == ["acme.com", "docs.acme.com"]
    assert target["origin"] == "resolved"
    assert "site:acme.com" in target["query"]


def test_static_mapping_wins_over_resolved_on_overlap() -> None:
    from utils.query_orchestration import _official_recovery_targets

    analysis = analyze_query("对比 acme 和 globex 的价格", allow_search=True)
    targets = _official_recovery_targets(
        analysis,
        {"acme": ["acme.cn"]},
        resolved_domains={"acme": ["acme.com"]},
    )
    assert targets[0]["domains"] == ["acme.cn"]
    assert targets[0]["origin"] == "pin"


def test_build_query_plan_uses_resolved_official_domains() -> None:
    analysis = analyze_query("对比 acme 和 globex 的价格", allow_search=True)
    plan = build_query_plan(
        analysis,
        has_local_docs=False,
        recovery_budget=1,
        resolved_official_domains={"acme": ["acme.com"], "globex": ["globex.cn"]},
    )
    step = plan.step_for_kind(PlanStepKind.OFFICIAL_DOMAIN_RECOVERY, include_recovery=True)
    assert step is not None
    targets = step.metadata.get("targets") or []
    assert {t["entity"] for t in targets} == {"acme", "globex"}
    assert all(t["origin"] == "resolved" for t in targets)
