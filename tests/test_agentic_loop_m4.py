"""M4 shared termination critic, judge precedence, and budget regressions."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from orchestrators.react_loop_graph import ReactLoopGraphRunner
from utils.query_orchestration import (
    CriticBudgetState,
    CriticEvidenceState,
    TerminationAction,
    TerminationContext,
    evaluate_termination,
)


ROOT = Path(__file__).resolve().parents[1]


def test_positive_judge_cannot_clear_deterministic_authority_gap() -> None:
    decision = evaluate_termination(
        TerminationContext(
            phase="loop",
            requires_evidence=True,
            final_proposed=True,
            answer="A complete-looking answer",
            policies=["authority"],
            evidence=CriticEvidenceState(
                retained_count=1,
                available_count=1,
                authoritative_count=0,
            ),
            budget=CriticBudgetState(iteration=1, max_iterations=3),
            judge_payload={
                "passes": True,
                "missing_constraints": [],
                "reason": "looks complete",
            },
        )
    )

    assert decision.action == TerminationAction.CONTINUE
    assert decision.deterministic_pass is False
    assert decision.judge_used is True
    assert "authority" in decision.missing_constraints


def test_negative_judge_can_veto_deterministic_pass() -> None:
    decision = evaluate_termination(
        TerminationContext(
            phase="loop",
            final_proposed=True,
            answer="A rule-complete answer",
            budget=CriticBudgetState(iteration=1, max_iterations=3),
            judge_payload={
                "passes": False,
                "missing_constraints": ["semantic_detail"],
                "reason": "important explanation missing",
            },
        )
    )

    assert decision.action == TerminationAction.CONTINUE
    assert decision.deterministic_pass is True
    assert decision.missing_constraints == ["semantic_detail"]
    assert "semantic_sufficiency_missing" in decision.failure_types


def test_successful_termination_is_explained_by_a_critic_rule() -> None:
    decision = evaluate_termination(
        TerminationContext(
            phase="loop",
            final_proposed=True,
            answer="Complete answer",
            budget=CriticBudgetState(iteration=1, max_iterations=3),
        )
    )

    assert decision.action == TerminationAction.RETURN
    assert decision.rule_hits == [
        {
            "rule": "constraints_satisfied",
            "detail": "The deterministic evidence and constraint checklist passed.",
        }
    ]


def test_iteration_budget_is_a_hard_stop_even_when_gap_is_recoverable() -> None:
    decision = evaluate_termination(
        TerminationContext(
            phase="loop",
            requires_evidence=True,
            final_proposed=True,
            answer="Draft",
            evidence=CriticEvidenceState(),
            budget=CriticBudgetState(iteration=2, max_iterations=2),
            judge_payload={
                "passes": False,
                "missing_constraints": ["more_evidence"],
                "reason": "continue",
            },
        )
    )

    assert decision.action == TerminationAction.EXHAUSTED
    assert decision.hard_stop is True
    assert decision.should_continue is False
    assert "iteration_budget_exhausted" in decision.failure_types


def test_search_failure_is_a_hard_stop_that_judge_cannot_reverse() -> None:
    decision = evaluate_termination(
        TerminationContext(
            phase="loop",
            requires_evidence=True,
            final_proposed=True,
            answer="Draft",
            search_error=True,
            budget=CriticBudgetState(iteration=1, max_iterations=3),
            judge_payload={"passes": True, "reason": "accept"},
        )
    )

    assert decision.action == TerminationAction.UNRECOVERABLE
    assert decision.hard_stop is True
    assert "search_unavailable" in decision.failure_types


def test_sole_loop_execution_adapter_calls_the_shared_critic() -> None:
    loop_source = inspect.getsource(ReactLoopGraphRunner._evaluate)
    assert "evaluate_termination" in loop_source
    loop_module = (ROOT / "orchestrators/react_loop_graph.py").read_text(encoding="utf-8")
    orchestrator_module = (
        ROOT / "orchestrators/react_agent_orchestrator.py"
    ).read_text(encoding="utf-8")
    langchain_module = (
        ROOT / "langchain/langchain_orchestrator.py"
    ).read_text(encoding="utf-8")
    assert "_forced_termination" not in loop_module
    assert "AgentExecutor" not in orchestrator_module
    assert "create_react_agent" not in langchain_module
    assert "_apply_postcheck" not in langchain_module


def test_example_config_has_one_termination_budget_and_judge_block() -> None:
    config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))

    assert config["termination"]["max_iterations"] == 5
    assert config["termination"]["judge"]["enabled"] is True
    assert config["termination"]["tool_budgets"]["web_search"] == 3
    assert "postcheck" not in config
    assert "reactAgent" not in config
    assert "engine" not in config
