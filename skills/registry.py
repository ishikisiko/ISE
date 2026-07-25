"""Discovery, validation, and availability gating for runtime skills."""

from __future__ import annotations

import importlib
import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from evidence import RetrievalOptions
from utils.config_validation import configured_value

from .contracts import PreflightResult, Skill, SkillManifest, SkillRunResult


class SkillManifestError(ValueError):
    """Raised when a skill manifest cannot satisfy the runtime contract."""


@dataclass(frozen=True)
class SkillAvailability:
    name: str
    available: bool
    reason: str


def _require_mapping(value: Any, field_name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise SkillManifestError(f"{field_name} must be a mapping")
    return value


def load_skill_manifest(path: Path) -> SkillManifest:
    """Load and validate one ``skill.yaml`` manifest."""

    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    root = _require_mapping(raw, "manifest")
    tool = _require_mapping(root.get("tool"), "tool")
    budget = _require_mapping(root.get("budget"), "budget")
    args_schema = _require_mapping(tool.get("args_schema"), "tool.args_schema")

    required = {
        "name": root.get("name"),
        "handler": root.get("handler"),
        "tool.name": tool.get("name"),
        "tool.description": tool.get("description"),
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        raise SkillManifestError(f"missing required fields: {', '.join(missing)}")
    if args_schema.get("type") != "object" or not isinstance(args_schema.get("properties"), dict):
        raise SkillManifestError("tool.args_schema must be a JSON object schema")

    normalized_budget: Dict[str, int] = {}
    for key in ("max_calls_per_query", "timeout_seconds", "max_evidence_items"):
        value = int(budget.get(key, 0) or 0)
        if value <= 0:
            raise SkillManifestError(f"budget.{key} must be a positive integer")
        normalized_budget[key] = value

    return SkillManifest(
        name=str(root["name"]).strip(),
        version=int(root.get("version", 1) or 1),
        handler=str(root["handler"]).strip(),
        tool_name=str(tool["name"]).strip(),
        description=str(tool["description"]).strip(),
        args_schema=args_schema,
        budget=normalized_budget,
        availability=_require_mapping(root.get("availability") or {}, "availability"),
        package_dir=str(path.parent),
    )


class SkillRegistry:
    """Build the active tool surface from validated, available skill packages."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, *, root: Optional[str] = None) -> None:
        self.config = config or {}
        self.root = Path(root or Path(__file__).resolve().parent)
        self._skills: Dict[str, Skill] = {}
        self._by_tool_name: Dict[str, Skill] = {}
        self._availability: Dict[str, SkillAvailability] = {}

    @classmethod
    def from_config(
        cls,
        config: Optional[Dict[str, Any]] = None,
        *,
        root: Optional[str] = None,
    ) -> "SkillRegistry":
        registry = cls(config, root=root)
        registry.discover()
        return registry

    def discover(self) -> None:
        self._skills.clear()
        self._by_tool_name.clear()
        self._availability.clear()
        skill_cfg = self.config.get("skills") or {}
        if isinstance(skill_cfg, dict) and skill_cfg.get("enabled") is False:
            return
        disabled = set(skill_cfg.get("disabled") or []) if isinstance(skill_cfg, dict) else set()

        for path in sorted(self.root.glob("*/skill.yaml")):
            manifest = load_skill_manifest(path)
            if manifest.name in disabled:
                self._availability[manifest.name] = SkillAvailability(
                    manifest.name, False, "disabled_by_config"
                )
                continue
            available, reason = self._check_availability(manifest.availability)
            if not available:
                self._availability[manifest.name] = SkillAvailability(
                    manifest.name, False, reason
                )
                continue
            try:
                handler_cls = self._import_handler(manifest.handler)
                handler = handler_cls(config=self.config, manifest=manifest)
            except Exception as exc:
                self._availability[manifest.name] = SkillAvailability(
                    manifest.name, False, f"handler_init_failed:{type(exc).__name__}"
                )
                continue
            if not isinstance(handler, Skill):
                self._availability[manifest.name] = SkillAvailability(
                    manifest.name, False, "handler_contract_mismatch"
                )
                continue
            if manifest.name in self._skills or manifest.tool_name in self._by_tool_name:
                raise SkillManifestError(f"duplicate skill or tool name: {manifest.name}")
            self._skills[manifest.name] = handler
            self._by_tool_name[manifest.tool_name] = handler
            self._availability[manifest.name] = SkillAvailability(
                manifest.name, True, "available"
            )

    @staticmethod
    def _import_handler(entrypoint: str) -> Any:
        module_name, separator, attribute = entrypoint.partition(":")
        if not separator or not module_name or not attribute:
            raise SkillManifestError("handler must use module:attribute syntax")
        module = importlib.import_module(module_name)
        return getattr(module, attribute)

    def _check_availability(self, availability: Dict[str, Any]) -> tuple[bool, str]:
        any_of = list(availability.get("any_of") or [])
        all_of = list(availability.get("all_of") or [])
        if all_of:
            missing = [requirement for requirement in all_of if not self._requirement_met(requirement)]
            if missing:
                return False, f"missing:{','.join(map(str, missing))}"
        if any_of and not any(self._requirement_met(requirement) for requirement in any_of):
            return False, f"missing_any:{','.join(map(str, any_of))}"
        return True, "available"

    def _requirement_met(self, requirement: Any) -> bool:
        text = str(requirement or "").strip()
        kind, separator, value = text.partition(":")
        if not separator or not value:
            return False
        if kind == "python":
            return importlib.util.find_spec(value) is not None
        if kind == "config":
            current: Any = self.config
            for part in value.split("."):
                current = current.get(part) if isinstance(current, dict) else None
            return bool(configured_value(current or os.getenv(value)))
        return False

    def active_skills(self) -> List[Skill]:
        return list(self._skills.values())

    def get(self, name_or_tool: str) -> Optional[Skill]:
        return self._skills.get(name_or_tool) or self._by_tool_name.get(name_or_tool)

    def match_query(self, query: str) -> Optional[Skill]:
        for skill in self.active_skills():
            if skill.handles_query(query):
                return skill
        return None

    def execute(
        self,
        name_or_tool: str,
        args: Dict[str, Any],
        *,
        options: Optional[RetrievalOptions] = None,
    ) -> SkillRunResult:
        skill = self.get(name_or_tool)
        if skill is None:
            return SkillRunResult(PreflightResult.reject("skill_unavailable"))
        preflight = skill.preflight(args)
        if not preflight.accepted:
            return SkillRunResult(preflight)
        merged_args = dict(args)
        merged_args.update(preflight.normalized_args)
        items = skill.run(merged_args, options or RetrievalOptions())
        limit = skill.manifest.budget["max_evidence_items"]
        return SkillRunResult(preflight, list(items[:limit]))

    def availability(self) -> List[SkillAvailability]:
        return list(self._availability.values())

    def __iter__(self) -> Iterable[Skill]:
        return iter(self.active_skills())
