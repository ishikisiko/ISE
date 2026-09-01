"""Unit tests for ``orchestrators/autonomy_policy.py``.

Covers task 2.7: resolution priority, profile override, unknown-mode rejection,
invalid-budget fallback, ``guided`` equivalence when the ``autonomy`` block is
absent, and the request-payload rejection of an unknown mode name.
"""

from typing import Any, Dict

import pytest

from orchestrators.autonomy_policy import (
    AUTONOMOUS_PRESET,
    GUIDED_PRESET,
    assert_no_preflight_toggle,
    resolve_autonomy_policy,
)
from utils.config_validation import validate_autonomy_config


class TestResolutionPriority:
    def test_default_when_no_block_and_no_request(self):
        policy = resolve_autonomy_policy({})
        assert policy.mode == "guided"
        assert policy.source == "default"

    def test_config_mode_used_when_no_request(self):
        policy = resolve_autonomy_policy({"autonomy": {"mode": "autonomous"}})
        assert policy.mode == "autonomous"
        assert policy.source == "config"

    def test_request_overrides_config(self):
        policy = resolve_autonomy_policy(
            {"autonomy": {"mode": "autonomous"}}, request_mode="guided"
        )
        assert policy.mode == "guided"
        assert policy.source == "request"

    def test_request_overrides_default(self):
        policy = resolve_autonomy_policy({}, request_mode="autonomous")
        assert policy.mode == "autonomous"
        assert policy.source == "request"

    def test_request_override_does_not_mutate_resolver(self):
        # A request override must not leak into a subsequent default resolution.
        resolve_autonomy_policy({}, request_mode="autonomous")
        again = resolve_autonomy_policy({})
        assert again.mode == "guided"
        assert again.source == "default"


class TestPresetEquivalence:
    def test_missing_autonomy_block_equivalent_to_guided_preset(self):
        config = {
            "termination": {
                "max_iterations": GUIDED_PRESET.budgets.max_iterations,
                "max_synthesis_attempts": GUIDED_PRESET.budgets.max_synthesis_attempts,
                "tool_budgets": dict(GUIDED_PRESET.budgets.tool_budgets),
            },
            "orchestration": {
                "context_compaction": GUIDED_PRESET.budgets.context_compaction.to_config_dict(),
            },
        }
        policy = resolve_autonomy_policy(config)
        assert policy.mode == "guided"
        assert policy.source == "default"
        # Rule-strength fields identical to the preset.
        assert policy.checklist_injection == GUIDED_PRESET.checklist_injection
        assert policy.critic_verdict == GUIDED_PRESET.critic_verdict
        assert policy.citation_check == GUIDED_PRESET.citation_check
        assert policy.narration_guard == GUIDED_PRESET.narration_guard
        assert policy.forced_synthesis == GUIDED_PRESET.forced_synthesis
        assert policy.clarification_owner == GUIDED_PRESET.clarification_owner
        # Budgets derived from the termination block match the preset defaults.
        assert policy.budgets.max_iterations == GUIDED_PRESET.budgets.max_iterations
        assert (
            policy.budgets.max_synthesis_attempts
            == GUIDED_PRESET.budgets.max_synthesis_attempts
        )

    def test_guided_preset_strength_matches_pre_capability(self):
        guided = GUIDED_PRESET
        assert guided.checklist_injection == "enforce"
        assert guided.critic_verdict == "binding"
        assert guided.citation_check == "binding"
        assert guided.narration_guard == "on"
        assert guided.forced_synthesis == "on"
        assert guided.clarification_owner == "system"

    def test_autonomous_preset_strength(self):
        auto = AUTONOMOUS_PRESET
        assert auto.checklist_injection == "hint"
        assert auto.critic_verdict == "advisory"
        assert auto.citation_check == "advisory"
        assert auto.judge_enabled is False
        assert auto.narration_guard == "off"
        assert auto.forced_synthesis == "off"
        assert auto.clarification_owner == "model"


class TestProfileOverride:
    def test_profile_strength_override_merges_with_preset(self):
        policy = resolve_autonomy_policy(
            {"autonomy": {"mode": "guided", "profiles": {"guided": {"narration_guard": "off"}}}}
        )
        assert policy.narration_guard == "off"
        # Unoverridden fields keep the preset value.
        assert policy.critic_verdict == "binding"

    def test_autonomous_budget_override_from_profile(self):
        policy = resolve_autonomy_policy(
            {
                "autonomy": {
                    "mode": "autonomous",
                    "profiles": {
                        "autonomous": {"budgets": {"max_iterations": 20, "tool_budgets": {"web_search": 7}}}
                    },
                }
            }
        )
        assert policy.budgets.max_iterations == 20
        assert policy.budgets.tool_limit("web_search") == 7
        # Unoverridden tool keeps the preset default.
        assert policy.budgets.tool_limit("fetch_url") == AUTONOMOUS_PRESET.budgets.tool_limit(
            "fetch_url"
        )

    def test_guided_budgets_always_from_termination_regardless_of_profile_budget(self):
        # A budgets key under profiles.guided is ignored: guided reads the live
        # termination block so it stays equivalent to the configured budget.
        policy = resolve_autonomy_policy(
            {
                "termination": {"max_iterations": 4},
                "autonomy": {
                    "mode": "guided",
                    "profiles": {"guided": {"budgets": {"max_iterations": 99}}},
                },
            }
        )
        assert policy.budgets.max_iterations == 4


class TestUnknownModeRejection:
    def test_unknown_config_mode_raises(self):
        with pytest.raises(ValueError, match="unknown autonomy mode"):
            resolve_autonomy_policy({"autonomy": {"mode": "bogus"}})

    def test_unknown_request_mode_raises(self):
        with pytest.raises(ValueError, match="unknown autonomy mode"):
            resolve_autonomy_policy({}, request_mode="bogus")

    def test_validate_autonomy_config_rejects_unknown_mode(self):
        with pytest.raises(ValueError, match="autonomy.mode"):
            validate_autonomy_config({"mode": "weird"})

    def test_validate_autonomy_config_rejects_bad_strength(self):
        with pytest.raises(ValueError, match="checklist_injection"):
            validate_autonomy_config(
                {"profiles": {"guided": {"checklist_injection": "strict"}}}
            )


class TestInvalidBudgetFallback:
    def test_non_positive_iterations_fall_back_with_note(self):
        policy = resolve_autonomy_policy(
            {
                "autonomy": {
                    "mode": "autonomous",
                    "profiles": {
                        "autonomous": {"budgets": {"max_iterations": 0}}
                    },
                }
            }
        )
        assert policy.budgets.max_iterations == AUTONOMOUS_PRESET.budgets.max_iterations
        assert any("max_iterations" in note for note in policy.budgets.degradation_notes)

    def test_unparseable_budget_falls_back(self):
        policy = resolve_autonomy_policy(
            {
                "autonomy": {
                    "mode": "autonomous",
                    "profiles": {
                        "autonomous": {"budgets": {"max_iterations": "lots"}}
                    },
                }
            }
        )
        assert policy.budgets.max_iterations == AUTONOMOUS_PRESET.budgets.max_iterations

    def test_non_positive_tool_budget_falls_back(self):
        policy = resolve_autonomy_policy(
            {
                "autonomy": {
                    "mode": "autonomous",
                    "profiles": {
                        "autonomous": {"budgets": {"tool_budgets": {"web_search": -3}}}
                    },
                }
            }
        )
        assert (
            policy.budgets.tool_limit("web_search")
            == AUTONOMOUS_PRESET.budgets.tool_limit("web_search")
        )

    def test_bound_always_exists_even_when_invalid(self):
        # I4: invalid numbers degrade to a finite default, never to infinity.
        policy = resolve_autonomy_policy(
            {
                "autonomy": {
                    "mode": "autonomous",
                    "profiles": {
                        "autonomous": {
                            "budgets": {
                                "max_iterations": None,
                                "max_synthesis_attempts": -1,
                                "tool_budgets": {"web_search": 0},
                            }
                        }
                    },
                }
            }
        )
        assert policy.budgets.max_iterations >= 1
        assert policy.budgets.max_synthesis_attempts >= 1
        assert policy.budgets.tool_limit("web_search") >= 1


class TestPreflightGuard:
    def test_policy_has_no_preflight_toggle(self):
        for policy in (GUIDED_PRESET, AUTONOMOUS_PRESET):
            assert_no_preflight_toggle(policy)


class TestPayloadRejection:
    def test_unknown_payload_mode_is_rejected(self):
        # Mirrors server._coerce_autonomy_mode's PayloadError path.
        from server import _coerce_autonomy_mode, PayloadError

        with pytest.raises(PayloadError):
            _coerce_autonomy_mode("aggressive")

    def test_valid_payload_modes_are_accepted(self):
        from server import _coerce_autonomy_mode

        assert _coerce_autonomy_mode("autonomous") == "autonomous"
        assert _coerce_autonomy_mode("GUIDED") == "guided"
        assert _coerce_autonomy_mode(None) is None
        assert _coerce_autonomy_mode("") is None
