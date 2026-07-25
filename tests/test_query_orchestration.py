"""Tests for M5 query analysis, ledger, critic, and execution trace."""

from __future__ import annotations

from evidence import EvidenceItem
from utils.query_orchestration import (
    CriticBudgetState,
    CriticEvidenceState,
    EvidenceLedger,
    EvidencePolicyRegistry,
    QueryAnalysis,
    QueryExecutionTrace,
    TerminationAction,
    TerminationContext,
    analyze_query,
    evaluate_termination,
    merge_optional_analysis,
    normalize_termination_config,
)


def _item(
    reference: str,
    content: str,
    *,
    tier: str = "unknown",
    call_id: str = "web_search:1",
) -> EvidenceItem:
    return EvidenceItem(
        source_type="web",
        source_id="web_search",
        title=content,
        content=content,
        reference=reference,
        snippet=content,
        metadata={
            "source_tier": tier,
            "originating_tool_call": call_id,
        },
    )


def test_analyze_query_derives_comparison_and_authority_constraints() -> None:
    analysis = analyze_query(
        "Compare OpenAI and Anthropic current API prices",
        allow_search=True,
    )
    assert analysis.constraints["comparison_required"] is True
    assert analysis.constraints["authority_required"] is True
    assert analysis.requires_evidence is True
    assert analysis.comparison_members[0] == "OpenAI"
    assert analysis.comparison_members[1].startswith("Anthropic")


def test_analyze_query_blocks_unresolved_comparison() -> None:
    analysis = analyze_query("Compare it with the other one", allow_search=True)
    assert analysis.critical_ambiguity is True
    assert analysis.ambiguities


def test_search_disable_is_authoritative() -> None:
    analysis = analyze_query("current OpenAI API price", allow_search=False)
    assert analysis.search_allowed is False
    assert analysis.requires_evidence is False


def test_current_query_requires_freshness_not_multi_year_coverage() -> None:
    analysis = analyze_query("北京现在天气如何？", allow_search=True)
    assert analysis.constraints["temporal_required"] is True
    assert analysis.constraints["freshness_required"] is True
    assert analysis.constraints["historical_coverage_required"] is False
    assert [policy.name for policy in EvidencePolicyRegistry().derive(analysis)] == [
        "authority",
        "freshness",
    ]


def test_multi_year_query_requires_historical_coverage() -> None:
    analysis = analyze_query("对比过去五年的价格趋势", allow_search=True)
    assert analysis.constraints["temporal_required"] is True
    assert analysis.constraints["historical_coverage_required"] is True
    assert "historical" in analysis.claim_classes


def test_optional_analysis_cannot_clear_deterministic_ambiguity() -> None:
    analysis = analyze_query("Compare it", allow_search=True)
    merged = merge_optional_analysis(
        analysis,
        {"entities": ["OpenAI"], "critical_ambiguity": False},
    )
    assert merged.critical_ambiguity is True
    assert "OpenAI" in merged.entities


def test_policy_registry_derives_composable_policies() -> None:
    analysis = QueryAnalysis(
        query="q",
        comparison_members=["A", "B"],
        constraints={
            "authority_required": True,
            "comparison_required": True,
            "temporal_required": True,
            "historical_coverage_required": True,
            "freshness_required": False,
        },
    )
    assert [policy.name for policy in EvidencePolicyRegistry().derive(analysis)] == [
        "authority",
        "comparison_coverage",
        "temporal_coverage",
    ]


def test_ledger_retains_authoritative_member_evidence() -> None:
    analysis = QueryAnalysis(
        query="compare",
        comparison_members=["OpenAI", "Anthropic"],
        constraints={"authority_required": True, "comparison_required": True},
    )
    ledger = EvidenceLedger(analysis)
    ledger.ingest(
        [
            _item(
                "https://openai.com/pricing",
                "OpenAI pricing 2026",
                tier="official",
            )
        ]
    )
    ledger.apply_limits()
    assert len(ledger.retained_items()) == 1
    summary = ledger.coverage_summary()
    assert summary["authoritative_entries"] == 1
    assert summary["comparison_members_covered"] == ["OpenAI"]


def test_ledger_limits_unknown_authority_evidence() -> None:
    analysis = QueryAnalysis(
        query="price",
        constraints={"authority_required": True},
    )
    ledger = EvidenceLedger(analysis)
    ledger.ingest([_item("https://example.com/a", "price estimate")])
    assert ledger.coverage_summary()["limited"] == 1
    assert ledger.retained_items() == []


def test_ledger_deduplicates_and_preserves_actual_calls() -> None:
    analysis = QueryAnalysis(query="q")
    ledger = EvidenceLedger(analysis)
    ledger.ingest(
        [
            _item("https://example.com/a?secret=1", "first", call_id="web_search:1"),
            _item("https://example.com/a?secret=2", "second", call_id="web_search:2"),
        ]
    )
    assert len(ledger.entries) == 1
    assert ledger.entries[0].originating_calls == ["web_search:1", "web_search:2"]
    assert ledger.entries[0].merged_count == 1

    trace = QueryExecutionTrace()
    trace.record_ledger(ledger)
    decisions = trace.to_dict()["events"][0]["decisions"]
    assert decisions[0]["originating_calls"] == ["web_search:1", "web_search:2"]


def test_ledger_result_budget_rejects_overflow() -> None:
    analysis = QueryAnalysis(query="q")
    ledger = EvidenceLedger(analysis, result_budget=1)
    ledger.ingest(
        [
            _item("https://example.com/a", "a"),
            _item("https://example.com/b", "b"),
        ]
    )
    ledger.apply_limits()
    assert ledger.coverage_summary()["rejected"] == 1


def test_critic_clarifies_critical_ambiguity() -> None:
    decision = evaluate_termination(
        TerminationContext(
            phase="loop",
            critical_ambiguities=["comparison_members_unresolved"],
        )
    )
    assert decision.action == TerminationAction.CLARIFY
    assert decision.hard_stop is True


def test_critic_returns_when_constraints_and_evidence_pass() -> None:
    decision = evaluate_termination(
        TerminationContext(
            phase="loop",
            requires_evidence=True,
            final_proposed=True,
            answer="Grounded answer",
            evidence=CriticEvidenceState(retained_count=1, available_count=1),
            had_successful_observation=True,
        )
    )
    assert decision.action == TerminationAction.RETURN
    assert decision.success is True


def test_critic_hard_stops_at_iteration_budget() -> None:
    decision = evaluate_termination(
        TerminationContext(
            phase="loop",
            requires_evidence=True,
            constraints_missing=["authority"],
            budget=CriticBudgetState(iteration=3, max_iterations=3),
        )
    )
    assert decision.action == TerminationAction.EXHAUSTED
    assert decision.hard_stop is True


def test_critic_returns_insufficient_when_no_tool_can_continue() -> None:
    decision = evaluate_termination(
        TerminationContext(
            phase="loop",
            requires_evidence=True,
            final_proposed=True,
            answer="Provisional answer",
            evidence=CriticEvidenceState(),
            can_continue=False,
            budget=CriticBudgetState(iteration=1, max_iterations=3),
        )
    )
    assert decision.action == TerminationAction.RETURN_INSUFFICIENT
    assert decision.hard_stop is True


def test_positive_judge_cannot_clear_deterministic_gap() -> None:
    decision = evaluate_termination(
        TerminationContext(
            phase="loop",
            requires_evidence=True,
            final_proposed=True,
            answer="draft",
            policies=["authority"],
            evidence=CriticEvidenceState(retained_count=1, available_count=1),
            judge_payload={"passes": True, "missing_constraints": []},
            budget=CriticBudgetState(iteration=1, max_iterations=3),
        )
    )
    assert decision.action != TerminationAction.RETURN
    assert "authority" in decision.missing_constraints


def test_negative_judge_can_veto_deterministic_pass() -> None:
    decision = evaluate_termination(
        TerminationContext(
            phase="loop",
            final_proposed=True,
            answer="complete",
            judge_payload={
                "passes": False,
                "missing_constraints": ["semantic_sufficiency"],
            },
            budget=CriticBudgetState(iteration=1, max_iterations=3),
        )
    )
    assert decision.action == TerminationAction.CONTINUE
    assert "semantic_sufficiency" in decision.missing_constraints


def test_execution_trace_records_analysis_tool_ledger_and_terminal() -> None:
    analysis = QueryAnalysis(query="q")
    ledger = EvidenceLedger(analysis)
    ledger.ingest([_item("https://example.com/a", "evidence")])
    ledger.apply_limits()

    trace = QueryExecutionTrace(
        configured=["brave"],
        requested=["brave"],
        eligible=["brave"],
    )
    trace.record_analysis(analysis)
    trace.record_tool_call(
        tool="web_search",
        status="done",
        iteration=1,
        position=1,
        query="q",
        source_type="web",
        source_tier="official",
        item_count=1,
    )
    trace.record_ledger(ledger)
    trace.record_termination({"action": "return", "reason": "constraints_satisfied"})

    payload = trace.to_dict()
    assert payload["executed"] == ["web_search"]
    assert [event["kind"] for event in payload["events"]] == [
        "analysis",
        "tool_call",
        "evidence_ledger",
        "termination",
    ]


def test_execution_trace_is_bounded() -> None:
    trace = QueryExecutionTrace()
    for position in range(10):
        trace.record_tool_call(
            tool="web_search",
            status="done",
            iteration=1,
            position=position,
        )
    payload = trace.to_dict(max_events=3)
    assert len(payload["events"]) == 3
    assert payload["truncated"] is True


def test_normalize_termination_config_keeps_global_bounds() -> None:
    normalized = normalize_termination_config(
        {
            "max_iterations": 7,
            "judge_interval": 3,
            "repeat_threshold": 4,
        }
    )
    assert normalized["max_iterations"] == 7
    assert normalized["judge_interval"] == 3
    assert normalized["repeat_threshold"] == 4
