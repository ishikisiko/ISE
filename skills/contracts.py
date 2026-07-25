"""Stable contracts shared by runtime skill packages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Protocol, runtime_checkable

from evidence import EvidenceItem, RetrievalOptions


@dataclass(frozen=True)
class PreflightResult:
    """Deterministic admission decision made before any provider call."""

    accepted: bool
    reason: str
    normalized_args: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def accept(cls, **normalized_args: Any) -> "PreflightResult":
        return cls(True, "accepted", normalized_args)

    @classmethod
    def reject(cls, reason: str, **normalized_args: Any) -> "PreflightResult":
        return cls(False, reason, normalized_args)


@dataclass(frozen=True)
class SkillManifest:
    """Validated subset of ``skill.yaml`` used by the runtime."""

    name: str
    version: int
    handler: str
    tool_name: str
    description: str
    args_schema: Dict[str, Any]
    budget: Dict[str, int]
    availability: Dict[str, Any]
    package_dir: str


@dataclass(frozen=True)
class SkillRunResult:
    """Registry execution result, including rejected preflight decisions."""

    preflight: PreflightResult
    evidence_items: List[EvidenceItem] = field(default_factory=list)


@runtime_checkable
class Skill(Protocol):
    """Protocol implemented by every skill handler.

    Handlers also inherit :class:`evidence.EvidenceSource`, making skill output
    native input for the existing ledger and audit pipeline.
    """

    manifest: SkillManifest

    def handles_query(self, query: str) -> bool:
        ...

    def preflight(self, args: Dict[str, Any]) -> PreflightResult:
        ...

    def run(
        self,
        args: Dict[str, Any],
        options: RetrievalOptions,
    ) -> List[EvidenceItem]:
        ...
