"""Autonomy policy: the single carrier of rule strength for the agentic loop.

The loop's rule strength used to be hardcoded across seven call sites in
``ReactLoopGraphRunner``. This module lifts it into an immutable
``AutonomyPolicy`` value object that is resolved once per request and threaded
through the loop. Two named presets exist:

* ``guided`` (default): every rule binds the model the way it did before this
  capability was introduced, so default behaviour is unchanged and independently
  revertable.
* ``autonomous``: checklist becomes a hint, the deterministic critic and the
  citation check stay advisory, the LLM judge is off, narration guard is off,
  forced synthesis is off, and ``ask_user`` is owned by the model. Budgets are
  widened but still bounded.

The policy deliberately offers **no** value that disables or downgrades
``preflight`` (skill argument pre-checks). High autonomy means the model owns
planning and wrap-up; it never means the model owns the legality of an
outbound call's parameters. See the ``add-autonomy-policy`` design D5.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple


# ---------------------------------------------------------------------------
# Allowed enum values (kept as plain strings so the policy stays serialisable).
# ---------------------------------------------------------------------------
CHECKLIST_INJECTION_VALUES = ("enforce", "hint")
BINDING_VALUES = ("binding", "advisory")
NARRATION_GUARD_VALUES = ("on", "off")
FORCED_SYNTHESIS_VALUES = ("on", "off")
CLARIFICATION_OWNER_VALUES = ("system", "model")
RESOLUTION_SOURCES = ("request", "config", "default")
VALID_MODES = ("guided", "autonomous")

# The canonical retrieval tool names whose per-query call ceiling is governed
# by the budget group. Order is stable for deterministic display.
DEFAULT_TOOL_NAMES: Tuple[str, ...] = (
    "web_search",
    "fetch_url",
    "search_recovery",
    "local_docs",
    "recall_evidence",
    "ask_user",
)


@dataclass(frozen=True)
class ContextCompactionBudget:
    """The subset of context-compaction knobs that belong to the budget group.

    These are the values the loop tunes for long vs. short loops; the rest of
    the compaction config (per-model windows, calibration) is infrastructure.
    """

    threshold: float = 0.75
    keep_recent_rounds: int = 2
    max_compactions_per_run: int = 2
    summary_max_tokens: int = 800
    evidence_pool_max_entries: int = 32

    def to_config_dict(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "threshold": self.threshold,
            "keep_recent_rounds": self.keep_recent_rounds,
            "max_compactions_per_run": self.max_compactions_per_run,
            "summary_max_tokens": self.summary_max_tokens,
            "evidence_pool_max_entries": self.evidence_pool_max_entries,
        }


@dataclass(frozen=True)
class BudgetGroup:
    """A finite budget set for one autonomy preset.

    Every number is a hard upper bound. ``autonomous`` widens the *numbers*,
    never the existence of a bound (invariant I4).
    """

    max_iterations: int
    max_synthesis_attempts: int
    tool_budgets: Mapping[str, int]
    context_compaction: ContextCompactionBudget
    degradation_notes: Tuple[str, ...] = ()

    def tool_limit(self, name: str) -> Optional[int]:
        """Return the per-tool ceiling for ``name`` or ``None`` if unset."""
        value = self.tool_budgets.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return None


@dataclass(frozen=True)
class AutonomyPolicy:
    """The immutable rule-strength carrier for one resolved request.

    ``mode``/``source`` are observability fields; the remaining fields are the
    rule strengths the loop consults. There is intentionally no field that can
    turn ``preflight`` off (it is always binding in every preset).
    """

    mode: str
    source: str
    checklist_injection: str
    critic_verdict: str
    citation_check: str
    judge_enabled: bool
    narration_guard: str
    forced_synthesis: str
    clarification_owner: str
    budgets: BudgetGroup

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            raise ValueError(f"unknown autonomy mode: {self.mode!r}")
        if self.source not in RESOLUTION_SOURCES:
            raise ValueError(f"unknown autonomy source: {self.source!r}")
        if self.checklist_injection not in CHECKLIST_INJECTION_VALUES:
            raise ValueError(
                f"checklist_injection must be one of {CHECKLIST_INJECTION_VALUES}, "
                f"got {self.checklist_injection!r}"
            )
        if self.critic_verdict not in BINDING_VALUES:
            raise ValueError(
                f"critic_verdict must be one of {BINDING_VALUES}, "
                f"got {self.critic_verdict!r}"
            )
        if self.citation_check not in BINDING_VALUES:
            raise ValueError(
                f"citation_check must be one of {BINDING_VALUES}, "
                f"got {self.citation_check!r}"
            )
        if self.narration_guard not in NARRATION_GUARD_VALUES:
            raise ValueError(
                f"narration_guard must be one of {NARRATION_GUARD_VALUES}, "
                f"got {self.narration_guard!r}"
            )
        if self.forced_synthesis not in FORCED_SYNTHESIS_VALUES:
            raise ValueError(
                f"forced_synthesis must be one of {FORCED_SYNTHESIS_VALUES}, "
                f"got {self.forced_synthesis!r}"
            )
        if self.clarification_owner not in CLARIFICATION_OWNER_VALUES:
            raise ValueError(
                f"clarification_owner must be one of {CLARIFICATION_OWNER_VALUES}, "
                f"got {self.clarification_owner!r}"
            )

    # -- convenience predicates ------------------------------------------------
    @property
    def advisory_critic(self) -> bool:
        return self.critic_verdict == "advisory"

    @property
    def advisory_citation(self) -> bool:
        return self.citation_check == "advisory"

    @property
    def narration_guard_on(self) -> bool:
        return self.narration_guard == "on"

    @property
    def forced_synthesis_on(self) -> bool:
        return self.forced_synthesis == "on"

    @property
    def model_owns_clarification(self) -> bool:
        return self.clarification_owner == "model"

    def to_public_dict(self) -> Dict[str, Any]:
        """Bounded, safe-to-emit view of the policy for control metadata."""
        return {
            "mode": self.mode,
            "source": self.source,
            "checklist_injection": self.checklist_injection,
            "critic_verdict": self.critic_verdict,
            "citation_check": self.citation_check,
            "judge_enabled": self.judge_enabled,
            "narration_guard": self.narration_guard,
            "forced_synthesis": self.forced_synthesis,
            "clarification_owner": self.clarification_owner,
        }


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------
_GUIDED_COMPACTION = ContextCompactionBudget(
    threshold=0.75,
    keep_recent_rounds=2,
    max_compactions_per_run=2,
    summary_max_tokens=800,
    evidence_pool_max_entries=32,
)
_AUTONOMOUS_COMPACTION = ContextCompactionBudget(
    # Long (15-20 round) loops hit the 0.75 threshold far too early and would
    # trigger tier-2 summaries every few rounds. Widen the window and keep more
    # recent rounds so the model retains actionable context. Initial values; the
    # P3 baseline run may retune them (see tasks 8.5/8.6).
    threshold=0.85,
    keep_recent_rounds=4,
    max_compactions_per_run=4,
    summary_max_tokens=1200,
    evidence_pool_max_entries=48,
)

#: The ``guided`` preset. Rule strength matches pre-capability behaviour. Its
#: budget group holds the built-in defaults used when config has no ``termination``
#: block; in normal operation ``resolve_autonomy_policy`` reads the live
#: ``termination`` values so guided stays equivalent to the configured budget.
GUIDED_PRESET = AutonomyPolicy(
    mode="guided",
    source="default",
    checklist_injection="enforce",
    critic_verdict="binding",
    citation_check="binding",
    judge_enabled=True,
    narration_guard="on",
    forced_synthesis="on",
    clarification_owner="system",
    budgets=BudgetGroup(
        max_iterations=5,
        max_synthesis_attempts=2,
        tool_budgets=MappingProxyType(
            {"web_search": 3, "fetch_url": 3, "search_recovery": 2, "local_docs": 2, "recall_evidence": 3, "ask_user": 2}
        ),
        context_compaction=_GUIDED_COMPACTION,
    ),
)

#: The ``autonomous`` preset. Checklist is a hint; critic and citation checks
#: are advisory; the LLM judge is off; narration guard and forced synthesis are
#: off; the model owns clarification. Budgets are widened but bounded.
AUTONOMOUS_PRESET = AutonomyPolicy(
    mode="autonomous",
    source="default",
    checklist_injection="hint",
    critic_verdict="advisory",
    citation_check="advisory",
    judge_enabled=False,
    narration_guard="off",
    forced_synthesis="off",
    clarification_owner="model",
    budgets=BudgetGroup(
        max_iterations=15,
        max_synthesis_attempts=3,
        tool_budgets=MappingProxyType(
            {"web_search": 12, "fetch_url": 9, "search_recovery": 9, "local_docs": 6, "recall_evidence": 3, "ask_user": 2}
        ),
        context_compaction=_AUTONOMOUS_COMPACTION,
    ),
)

_PRESETS: Mapping[str, AutonomyPolicy] = MappingProxyType(
    {"guided": GUIDED_PRESET, "autonomous": AUTONOMOUS_PRESET}
)


def preset_for(mode: str) -> AutonomyPolicy:
    """Return the named preset (a copy whose ``source`` is ``default``)."""
    if mode not in _PRESETS:
        raise ValueError(
            f"unknown autonomy mode {mode!r}; expected one of {VALID_MODES}"
        )
    return _PRESETS[mode]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def _parse_positive_int(
    raw: Any, default: int, *, label: str, notes: List[str]
) -> int:
    """Coerce ``raw`` to a positive int, falling back to ``default`` on failure."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        notes.append(f"{label}: unparseable value {raw!r}, using default {default}")
        return default
    if value <= 0:
        notes.append(f"{label}: non-positive value {value}, using default {default}")
        return default
    return value


def _resolve_tool_budgets(
    raw: Any,
    defaults: Mapping[str, int],
    *,
    notes: List[str],
) -> Mapping[str, int]:
    """Merge per-tool ceilings from ``raw`` over the preset ``defaults``.

    Unknown keys are preserved (a deployment may register custom tool names) but
    invalid values fall back to the preset default for that tool.
    """
    resolved: Dict[str, int] = dict(defaults)
    if not isinstance(raw, dict):
        return MappingProxyType(resolved)
    for name, value in raw.items():
        key = str(name)
        try:
            ceiling = int(value)
        except (TypeError, ValueError):
            fallback = defaults.get(key)
            if fallback is None:
                continue
            notes.append(
                f"tool_budgets.{key}: unparseable value {value!r}, using default {fallback}"
            )
            resolved[key] = fallback
            continue
        if ceiling <= 0:
            fallback = defaults.get(key)
            if fallback is None:
                notes.append(f"tool_budgets.{key}: non-positive value ignored")
                continue
            notes.append(
                f"tool_budgets.{key}: non-positive value {ceiling}, using default {fallback}"
            )
            resolved[key] = fallback
            continue
        resolved[key] = ceiling
    return MappingProxyType(resolved)


def _resolve_compaction(
    raw: Any, default: ContextCompactionBudget, *, notes: List[str]
) -> ContextCompactionBudget:
    if not isinstance(raw, dict):
        return default

    def _frac(key: str, current: float) -> float:
        try:
            value = float(raw.get(key, current))
        except (TypeError, ValueError):
            notes.append(f"context_compaction.{key}: unparseable, using default {current}")
            return current
        if not (0.05 <= value <= 0.98):
            notes.append(
                f"context_compaction.{key}: out-of-range {value}, using default {current}"
            )
            return current
        return value

    def _posint(key: str, current: int, minimum: int = 1) -> int:
        try:
            value = int(raw.get(key, current))
        except (TypeError, ValueError):
            notes.append(f"context_compaction.{key}: unparseable, using default {current}")
            return current
        if value < minimum:
            notes.append(
                f"context_compaction.{key}: below {minimum} ({value}), using default {current}"
            )
            return current
        return value

    return ContextCompactionBudget(
        threshold=_frac("threshold", default.threshold),
        keep_recent_rounds=_posint("keep_recent_rounds", default.keep_recent_rounds),
        max_compactions_per_run=_posint(
            "max_compactions_per_run", default.max_compactions_per_run
        ),
        summary_max_tokens=_posint("summary_max_tokens", default.summary_max_tokens),
        evidence_pool_max_entries=_posint(
            "evidence_pool_max_entries", default.evidence_pool_max_entries
        ),
    )


def _resolve_budget_group(
    raw: Optional[Mapping[str, Any]],
    preset_budgets: BudgetGroup,
    *,
    notes: List[str],
) -> BudgetGroup:
    """Build a ``BudgetGroup`` from ``raw`` over the preset ``preset_budgets``.

    Missing/invalid numbers fall back to the preset's built-in defaults and
    append a degradation note (invariant I4: a bound always exists).
    """
    source = raw if isinstance(raw, Mapping) else {}

    max_iterations = _parse_positive_int(
        source.get("max_iterations", preset_budgets.max_iterations),
        preset_budgets.max_iterations,
        label="budgets.max_iterations",
        notes=notes,
    )
    max_synthesis_attempts = _parse_positive_int(
        source.get("max_synthesis_attempts", preset_budgets.max_synthesis_attempts),
        preset_budgets.max_synthesis_attempts,
        label="budgets.max_synthesis_attempts",
        notes=notes,
    )
    tool_budgets = _resolve_tool_budgets(
        source.get("tool_budgets"), preset_budgets.tool_budgets, notes=notes
    )
    context_compaction = _resolve_compaction(
        source.get("context_compaction"), preset_budgets.context_compaction, notes=notes
    )
    return BudgetGroup(
        max_iterations=max_iterations,
        max_synthesis_attempts=max_synthesis_attempts,
        tool_budgets=tool_budgets,
        context_compaction=context_compaction,
        degradation_notes=tuple(notes),
    )


def _guided_budgets_from_termination(
    config: Mapping[str, Any], preset: AutonomyPolicy, notes: List[str]
) -> BudgetGroup:
    """Read guided budgets from the existing ``termination`` block.

    This keeps ``guided`` equivalent to the configured budget (invariant I5 /
    task 3.12): the budget group mirrors ``termination.max_iterations``,
    ``termination.max_synthesis_attempts``, ``termination.tool_budgets`` and the
    ``orchestration.context_compaction`` knobs.
    """
    termination = config.get("termination") if isinstance(config, Mapping) else None
    termination = termination if isinstance(termination, Mapping) else {}
    orchestration = config.get("orchestration") if isinstance(config, Mapping) else None
    orchestration = orchestration if isinstance(orchestration, Mapping) else {}
    compaction_raw = orchestration.get("context_compaction")
    compaction = _resolve_compaction(
        compaction_raw if isinstance(compaction_raw, Mapping) else {},
        preset.budgets.context_compaction,
        notes=notes,
    )
    tool_budgets = _resolve_tool_budgets(
        termination.get("tool_budgets"), preset.budgets.tool_budgets, notes=notes
    )
    max_iterations = _parse_positive_int(
        termination.get("max_iterations", preset.budgets.max_iterations),
        preset.budgets.max_iterations,
        label="termination.max_iterations",
        notes=notes,
    )
    max_synthesis_attempts = _parse_positive_int(
        termination.get("max_synthesis_attempts", preset.budgets.max_synthesis_attempts),
        preset.budgets.max_synthesis_attempts,
        label="termination.max_synthesis_attempts",
        notes=notes,
    )
    return BudgetGroup(
        max_iterations=max_iterations,
        max_synthesis_attempts=max_synthesis_attempts,
        tool_budgets=tool_budgets,
        context_compaction=compaction,
        degradation_notes=tuple(notes),
    )


# Rule-strength field names that ``autonomy.profiles.<mode>`` may override.
_STRENGTH_FIELDS = (
    "checklist_injection",
    "critic_verdict",
    "citation_check",
    "judge_enabled",
    "narration_guard",
    "forced_synthesis",
    "clarification_owner",
)


def resolve_autonomy_policy(
    config: Optional[Mapping[str, Any]] = None,
    request_mode: Optional[str] = None,
) -> AutonomyPolicy:
    """Resolve the effective ``AutonomyPolicy`` for one request.

    Priority: ``request_mode`` > ``autonomy.mode`` (config) > ``guided``.

    * Mode is never inferred from query content, classification, or any LLM
      output -- only from the explicit request parameter and config.
    * An unknown mode name raises ``ValueError``; it is never silently coerced
      to a default.
    * ``autonomy.profiles.<mode>`` may locally override preset rule-strength
      fields and (for ``autonomous``) the budget group. For ``guided`` the budget
      group always comes from the live ``termination``/``orchestration`` blocks
      so default behaviour stays equivalent to the configured budget.
    * ``judge_enabled`` for ``guided`` follows ``termination.judge.enabled`` so
      the guided preset reproduces the pre-capability judge behaviour.
    """
    config_map: Mapping[str, Any] = config if isinstance(config, Mapping) else {}
    autonomy_cfg = config_map.get("autonomy")
    autonomy_cfg = autonomy_cfg if isinstance(autonomy_cfg, Mapping) else {}

    config_mode = autonomy_cfg.get("mode")
    if config_mode is not None and str(config_mode).strip():
        config_mode = str(config_mode).strip()
    else:
        config_mode = None

    if request_mode is not None and str(request_mode).strip():
        mode = str(request_mode).strip().lower()
        source = "request"
    elif config_mode:
        mode = str(config_mode).strip().lower()
        source = "config"
    else:
        mode = "guided"
        source = "default"

    if mode not in _PRESETS:
        raise ValueError(
            f"unknown autonomy mode {mode!r}; expected one of {VALID_MODES}"
        )

    preset = _PRESETS[mode]
    profiles = autonomy_cfg.get("profiles")
    profiles = profiles if isinstance(profiles, Mapping) else {}
    profile = profiles.get(mode)
    profile = profile if isinstance(profile, Mapping) else {}

    notes: List[str] = []

    # Rule-strength overrides from the profile.
    strength_overrides: Dict[str, Any] = {}
    for field_name in _STRENGTH_FIELDS:
        if field_name in profile:
            strength_overrides[field_name] = profile[field_name]

    # Budgets.
    if mode == "guided":
        budgets = _guided_budgets_from_termination(config_map, preset, notes)
    else:
        budgets = _resolve_budget_group(
            profile.get("budgets"), preset.budgets, notes=notes
        )

    # judge_enabled defaults: guided follows the live judge config so behaviour
    # is unchanged; autonomous is off unless the profile turns it on explicitly.
    if "judge_enabled" not in strength_overrides:
        if mode == "guided":
            judge_cfg = (
                config_map.get("termination", {}) if isinstance(config_map.get("termination"), Mapping) else {}
            )
            judge_cfg = judge_cfg.get("judge") if isinstance(judge_cfg.get("judge"), Mapping) else {}
            strength_overrides["judge_enabled"] = bool(judge_cfg.get("enabled", True))
        else:
            strength_overrides["judge_enabled"] = False

    policy = replace(
        preset,
        mode=mode,
        source=source,
        budgets=budgets,
        **strength_overrides,
    )
    # ``replace`` skips ``__post_init__`` validation only for fields it touches;
    # the dataclass re-runs ``__post_init__`` which validates all enum fields.
    return policy


def assert_no_preflight_toggle(policy: AutonomyPolicy) -> None:
    """Guard helper: confirm the policy cannot disable preflight.

    Used by the loop's guard test (task 3.11). ``preflight`` is always binding
    because the policy type simply has no field that could express otherwise.
    """
    public = set(policy.to_public_dict())
    assert "preflight" not in public, "policy must not expose a preflight toggle"
    # There is no attribute on the frozen dataclass that names a preflight knob.
    for forbidden in ("preflight", "skip_preflight", "disable_preflight"):
        assert not hasattr(policy, forbidden), (
            f"policy must not carry a {forbidden!r} attribute"
        )
