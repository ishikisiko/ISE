"""Hand-computed samples for the Q3 offline evaluators (citation / evidence / loop / cost / fetch)."""
from __future__ import annotations

import pytest

from tests.quality import citation_eval, cost_eval, evidence_eval, fetch_eval, loop_eval
from tests.quality.common import delivered

pytestmark = pytest.mark.quality_offline


def _record(eid: int, tier: str, *, fetched: bool = False, content: str = "x", reference: str = None, dated: bool = False):
    metadata = {"eid": eid}
    if fetched:
        metadata["retrieval_kind"] = "fetch_url"
        metadata["content_chars"] = len(content)
    if dated:
        metadata["retrieved_at"] = "2026-09-09"
    return {"source_type": "web", "source_tier": tier, "reference": reference or f"https://site{eid}.example/p", "content": content, "metadata": metadata}


def test_citation_eval_counts_failures_and_rates():
    row = {
        "qid": "q1", "mode": "guided", "dataset": "final_answer",
        "answer": "The price is $12 [E1]. It weighs 300 grams. Launched in 1998 [E9]. Population 2.3 million [E2] (unverified).",
        "control": {"query_analysis": {"claim_classes": ["numeric", "pricing", "current"], "constraints": {}}, "loop_verdicts": [{"failure_types": ["citation_not_authoritative"], "rule_hits": [{"rule": "citation_verification"}]}]},
        "evidence_records": [_record(1, "official", fetched=True, content="p" * 700, dated=True), _record(2, "aggregator")],
    }
    item = citation_eval.evaluate_row(row)
    assert item["flags"] == {"requires_official_pricing": True, "temporal_required": True}
    assert item["hallucinated_citation_count"] == 1  # [E9]
    assert item["numeric_sentences"] == 4
    assert item["citation_recall"] == pytest.approx(0.75)  # "300 grams" lacks a citation
    assert item["citations_per_answer"] == 3
    assert item["failure_counts"]["citation_not_authoritative"] == 0  # E2 sentence is hedged
    assert item["unverified_hedge_rate"] == pytest.approx(0.25)
    assert item["pricing_source_compliance"] == 1.0
    assert item["recency_compliance"] is True  # E1 is an official record with a retrieval date
    summary = citation_eval.summarize([item])
    assert summary["hard_gate"]["questions_with_hallucinated_citations"] == ["q1"]
    assert summary["loop_agreement_sample"]["agree"] == 1


def test_citation_eval_recency_failure_when_no_dated_authoritative_source():
    row = {
        "qid": "q2", "mode": "guided", "answer": "Version 3.7 shipped on 2026-01-02 [E1].",
        "control": {"query_analysis": {"claim_classes": ["current"], "constraints": {}}, "loop_verdicts": []},
        "evidence_records": [_record(1, "official")],
    }
    item = citation_eval.evaluate_row(row)
    assert item["failure_counts"]["citation_recency_missing"] == 1
    assert item["recency_compliance"] is False
    existence = dict(row, control={"query_analysis": {"claim_classes": ["current"], "existence_query": True, "constraints": {}}, "loop_verdicts": []})
    assert citation_eval.evaluate_row(existence)["flags"]["temporal_required"] is False


def test_evidence_eval_aggregator_leak_and_member_coverage():
    row = {
        "qid": "q1", "mode": "guided", "query": "苹果和微软的区别", "answer": "A [E1] B [E2] C [E3]",
        "control": {
            "evidence_coverage": {"entries": 5, "retained": 3, "limited": 1, "rejected": 1, "merged": 2, "authoritative_entries": 1, "comparison_members_covered": ["苹果"], "decisions": []},
            "query_analysis": {"constraints": {"authority_required": True, "comparison_required": True}},
        },
        "evidence_records": [_record(1, "aggregator"), _record(2, "official"), _record(3, "unknown")],
    }
    gold = evidence_eval.load_member_gold()
    item = evidence_eval.evaluate_row(row, gold)
    assert item["aggregator_leak_rate"] == pytest.approx(1 / 3)
    assert item["authoritative_share"] == pytest.approx(1 / 3)
    assert item["member_coverage_f1"] == pytest.approx(2 * 1.0 * 0.5 / 1.5)
    summary = evidence_eval.summarize([item])
    assert summary["decision_distribution"]["retained"] == pytest.approx(0.6)
    assert summary["hard_gate"]["aggregator_leak_authority_required_count"] == 1


def _verdict(iteration, reason, *, new_evidence=True, judge_used=False, judge_error=None, sufficiency="partial", gaps=None, action="continue"):
    return {"iteration": iteration, "reason": reason, "new_evidence": new_evidence, "judge_used": judge_used, "judge_error": judge_error,
            "evidence_sufficiency": sufficiency, "advisory_gaps": gaps or [], "action": action}


def test_loop_eval_false_exhaustion_premature_and_advisory_rules():
    stagnated = {
        "qid": "s1", "mode": "guided", "answer": "some answer", "review": {"judgment": {"scores": {"core_correctness": 2}}},
        "control": {
            "loop_status": "stagnated", "loop_termination_reason": "stagnated", "loop_iterations": 4,
            "evidence_coverage": {"retained": 4},
            "loop_verdicts": [_verdict(1, "final_answer_rejected", judge_used=True), _verdict(2, "final_answer_rejected", new_evidence=False, judge_used=True, judge_error="timeout"), _verdict(3, "final_answer_rejected", new_evidence=False), _verdict(4, "stagnated", new_evidence=False, sufficiency="partial")],
            "termination_policy": {"tool_budgets": {"web_search": {"limit": 3, "used": 3}, "fetch_url": {"limit": 3, "used": 1}}},
            "execution_trace": {"events": []},
        },
    }
    premature = {
        "qid": "p1", "mode": "autonomous", "answer": "Google was founded on September 7, 1998.", "review": {"judgment": {"scores": {"core_correctness": 0}}},
        "control": {
            "loop_status": "succeeded", "loop_termination_reason": "succeeded", "loop_iterations": 2, "evidence_coverage": {"retained": 1},
            "loop_verdicts": [
                _verdict(1, "final_answer_rejected", gaps=[{"kind": "citation", "rule": "citation_missing"}, {"kind": "constraint", "rule": "member:x"}]),
                _verdict(2, "model_self_wrap", action="return", gaps=[{"kind": "citation", "rule": "citation_missing"}]),
            ],
            "termination_policy": {"tool_budgets": {"web_search": {"limit": 12, "used": 0}}},
            "execution_trace": {"events": [{"kind": "tool_call", "tool": "recall_evidence", "iteration": 2}]},
        },
    }
    plan_only = {"qid": "o1", "mode": "autonomous", "answer": "I will now fetch the documentation and report back.", "control": {"loop_status": "succeeded", "loop_iterations": 1, "loop_verdicts": [_verdict(1, "model_self_wrap", action="return")], "evidence_coverage": {}, "termination_policy": {"tool_budgets": {}}}}
    timeout = {"qid": "t1", "mode": "guided", "answer": "", "outcome": "harness_timeout", "control": {}}
    items = [loop_eval.evaluate_row(row) for row in (stagnated, premature, plan_only, timeout)]
    by_qid = {item["qid"]: item for item in items}
    assert by_qid["s1"]["false_exhaustion"] is True
    assert by_qid["s1"]["no_progress_streak"] == 3
    assert by_qid["s1"]["final_answer_rejected_count"] == 3
    assert by_qid["s1"]["judge_invocations"] == 2 and by_qid["s1"]["judge_errors"] == 1
    assert by_qid["s1"]["budget_hits"] == ["web_search"]
    assert by_qid["s1"]["iterations_to_first_answer"] == 1
    assert by_qid["p1"]["premature_success"] is True and by_qid["p1"]["delivered"] is True
    # iteration-1 gaps: citation gap not addressed (recall_evidence is not a web tool), constraint gap addressed (a tool ran); iteration-2 gaps: nothing follows -> ignored
    assert by_qid["p1"]["advisory_gaps"] == 3 and by_qid["p1"]["advisory_gaps_ignored"] == 2
    assert by_qid["o1"]["delivered"] is False and by_qid["o1"]["premature_success"] is True
    assert by_qid["t1"]["has_loop_status"] is False and by_qid["t1"]["false_exhaustion"] is None
    summary = loop_eval.summarize(items)
    assert summary["with_loop_status"] == 3 and summary["without_loop_status"] == ["t1"]
    assert summary["hard_gate"]["false_exhaustion_qids"] == ["s1"]
    assert summary["premature_success_rate"] == 1.0  # both succeeded rows are premature
    assert summary["premature_success_with_core_correct"] == {"n": 1, "rate": 1.0}
    assert summary["judge_error_rate"] == pytest.approx(1 / 7)
    assert summary["advisory_gap_ignore_rate"] == pytest.approx(2 / 3)
    assert summary["budget_utilization"]["web_search"]["hit_rate"] == pytest.approx(1 / 3)
    assert delivered({"answer": "木星 (Jupiter)"}) is True
    assert delivered({"answer": "Agent execution failed: boom"}) is False


def test_cost_eval_matches_baseline_runner_totals_and_maps_stages():
    row = {
        "qid": "c1", "mode": "guided", "review": {"judgment": {"scores": {"core_correctness": 2}}},
        "control": {"loop_status": "succeeded", "compactions": 1},
        "metrics": {"latency_ms": 5000.0, "total_tokens": 900},
        "response_times": {
            "total_ms": 5000.0,
            "llm_calls": [
                {"label": "loop_act", "duration_ms": 1000, "total_tokens": 500},
                {"label": "termination_judge", "duration_ms": 200, "total_tokens": 100},
                {"label": "degraded_synthesis", "duration_ms": 300, "total_tokens": 300},
            ],
            "tool_calls": [
                {"tool": "web_search", "duration_ms": 400, "kind": "loop_search_tool"},
                {"tool": "provider_request", "duration_ms": 350, "kind": "search", "provider": "brave", "credits": 1.0},
                {"tool": "provider_request", "duration_ms": 50, "kind": "extract", "provider": "direct_fetch"},
                {"tool": "fetch_url", "duration_ms": 120, "kind": "loop_search_tool"},
                {"tool": "google_geocode", "duration_ms": 80, "kind": "skill_provider"},
            ],
            "search_sources": [{"source": "brave", "duration_ms": 350}],
        },
    }
    from tests import baseline_runner as baseline
    item = cost_eval.evaluate_row(row)
    assert item["latency_ms"] == baseline.extract_latency_ms(row)
    assert item["total_tokens"] == baseline.extract_llm_stats(row)["total_tokens"] == 900
    assert item["token_source"] == "response_times"
    assert item["tokens_by_stage"] == {"act": 500, "judge": 100, "synthesize": 300}
    assert item["stage_ms"] == {"llm": 1500.0, "search": 750.0, "fetch": 170.0, "skill": 80.0}
    assert item["external_calls_app"] == 3 and item["tool_calls_logical"] == 2
    assert item["provider_credits"] == 1.0
    summary = cost_eval.summarize([item])
    assert summary["cost_per_correct_answer"]["tokens_per_correct_answer"] == 900.0
    assert summary["budget_exhaustion_cost"]["questions"] == 0
    assert summary["stage_latency_ms"]["judge"]["mean"] == 200.0
    transport_only = dict(row, response_times={"total_ms": 10.0, "llm_calls": [{"label": "loop_act", "duration_ms": 5}]}, metrics={"transport_total_tokens": 42, "latency_ms": 10.0})
    assert cost_eval.evaluate_row(transport_only)["total_tokens"] == 42
    assert cost_eval.evaluate_row(transport_only)["token_source"] == "transport"


def test_fetch_eval_success_rate_attempts_and_gold_containment():
    gold = {"what is the tensile strength?": [{"url": "https://brief.example/skyweave", "span": "tensile strength of 1,850 megapascals"}]}
    row = {
        "qid": "f1", "mode": "guided", "query": "What is the tensile strength?",
        "control": {"loop_fetch_outcomes": [
            {"url": "https://brief.example/skyweave", "status": "success", "chars": 900, "provider": "tavily_extract", "attempts": [
                {"provider": "direct_fetch", "status": "failed", "reason": "insufficient_content"}, {"provider": "tavily_extract", "status": "success", "content_chars": 900}]},
            {"url": "https://other.example/", "status": "no_data", "chars": 0, "error_type": "no_content", "attempts": [{"provider": "direct_fetch", "status": "failed"}]},
            {"url": "https://brief.example/skyweave", "status": "rejected", "chars": 0, "error_type": "duplicate_url"},
        ]},
        "search_api_calls": [{"kind": "extracted_pages", "attempts": [{"provider": "direct_fetch", "requested_url": "https://x.example/a", "status": "success", "content_chars": 1200}]}],
        "evidence_records": [
            {"reference": "https://brief.example/skyweave", "content": "The laminate has a tensile\nstrength of 1,850 megapascals and", "metadata": {"retrieval_kind": "fetch_url", "truncated": True}},
        ],
    }
    item = fetch_eval.evaluate_row(row, gold, min_content_chars=600)
    assert item["fetch_attempted"] == 3 and item["fetch_success"] == 2  # duplicate_url excluded
    assert item["fetch_success_rate"] == pytest.approx(2 / 3)
    assert item["extractor_attempts_per_success"] == pytest.approx(1.5)
    assert item["provider_stats"]["direct_fetch"] == {"attempts": 3, "successes": 1}
    assert item["gold_span_containment"] is True and item["truncation_loss"] is False
    assert item["truncated_records"] == 1
    summary = fetch_eval.summarize([item])
    assert summary["extractor_success_by_provider"]["tavily_extract"]["success_rate"] == 1.0
    assert summary["gold_span_containment"] == {"denominator": 1, "positives": 1, "rate": 1.0}
    assert summary["gold_span_containment_when_fetched"]["rate"] == 1.0
    missing = dict(row, evidence_records=[{"reference": "https://brief.example/skyweave", "content": "unrelated text", "metadata": {"retrieval_kind": "fetch_url", "truncated": True}}])
    lost = fetch_eval.evaluate_row(missing, gold, min_content_chars=600)
    assert lost["gold_span_containment"] is False and lost["truncation_loss"] is True
