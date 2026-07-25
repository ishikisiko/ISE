"""Shared runtime helpers for provider-backed skill handlers."""

from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, Optional

import requests

from evidence import EvidenceItem, EvidenceSource, EvidenceSourceType, RetrievalOptions
from utils.config_validation import configured_value
from utils.workflow_trace import safe_trace_text

from .contracts import SkillManifest


def has_query_term(query: str, terms: tuple[str, ...]) -> bool:
    """Match Latin terms by token boundary and CJK terms by substring."""
    lowered = str(query or "").casefold()
    for raw_term in terms:
        term = str(raw_term).casefold().strip()
        if not term:
            continue
        if term.isascii():
            pattern = r"(?<!\w)" + re.sub(r"\\\s+", r"\\s+", re.escape(term)) + r"(?!\w)"
            if re.search(pattern, lowered):
                return True
        elif term in lowered:
            return True
    return False


class RuntimeSkillHandler(EvidenceSource):
    """Common EvidenceSource implementation details for runtime skills."""

    source_type = EvidenceSourceType.DOMAIN

    def __init__(self, *, config: Dict[str, Any], manifest: SkillManifest) -> None:
        self.config = config
        self.manifest = manifest
        self.source_id = f"skill:{manifest.name}"
        self.display_name = manifest.name.replace("_", " ").title()
        configured_timeout = int(config.get("request_timeout", 12) or 12)
        self.request_timeout = max(
            3,
            min(configured_timeout, manifest.budget["timeout_seconds"]),
        )

    def _item(
        self,
        *,
        title: str,
        content: str,
        reference: str,
        provider: str,
        data: Any,
        options: RetrievalOptions,
        continue_search: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EvidenceItem:
        payload = {
            "domain": self.manifest.name,
            "skill": self.manifest.name,
            "tool_name": self.manifest.tool_name,
            "provider": provider,
            "data": data,
            "continue_search": continue_search,
            "source_tier": "authoritative",
            "retrieval_kind": "skill",
            "canonical_reference": reference,
            **(metadata or {}),
        }
        for key in ("originating_tool_call", "covered_claims"):
            if options.metadata.get(key) is not None:
                payload[key] = options.metadata[key]
        return EvidenceItem(
            source_type=self.source_type.value,
            source_id=self.source_id,
            title=title,
            content=content,
            reference=reference,
            snippet=" ".join(content.split())[:320],
            metadata=payload,
            rank=1,
        )

    @staticmethod
    def _record_timing(
        timing_recorder: Optional[Any],
        source: str,
        label: str,
        started: float,
        *,
        success: bool,
    ) -> None:
        if timing_recorder:
            duration_ms = (time.perf_counter() - started) * 1000
            timing_recorder.record_search_timing(
                source=source,
                label=label,
                duration_ms=duration_ms,
            )
            timing_recorder.record_tool_call(
                tool=source,
                duration_ms=duration_ms,
                success=success,
                extra={"label": label, "kind": "skill_provider"},
            )

    def _safe_error(self, exc: Exception) -> str:
        """Return a bounded provider failure without credentials or URL queries."""
        message = f"{type(exc).__name__}: {exc}"
        secrets = {
            configured_value(getattr(self, attribute, ""))
            for attribute in ("google_api_key", "api_key")
        }
        for key, value in self.config.items():
            if any(marker in str(key).casefold() for marker in ("key", "token", "secret", "password")):
                secrets.add(configured_value(value))
        for secret in sorted(secrets - {""}, key=len, reverse=True):
            message = message.replace(secret, "[redacted]")
        return safe_trace_text(message, limit=320)


class GoogleApiSkill(RuntimeSkillHandler):
    """Base for skills that use a Google Maps Platform API key."""

    def __init__(self, *, config: Dict[str, Any], manifest: SkillManifest) -> None:
        super().__init__(config=config, manifest=manifest)
        google_config = config.get("googleSearch") or {}
        self.google_api_key = configured_value(
            config.get("GOOGLE_API_KEY")
            or google_config.get("api_key")
            or os.getenv("GOOGLE_API_KEY")
        )
        self.google_geocode_url = str(
            config.get("GOOGLE_GEOCODE_URL")
            or "https://maps.googleapis.com/maps/api/geocode/json"
        )

    def geocode(
        self,
        location: str,
        *,
        timing_recorder: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if not self.google_api_key:
            return {"error": "missing_google_api_key"}
        started = time.perf_counter()
        success = False
        try:
            response = requests.get(
                self.google_geocode_url,
                params={"address": location, "key": self.google_api_key},
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            results = response.json().get("results") or []
            if not results:
                return {"error": "no_geocode_results"}
            result = results[0]
            coordinates = result.get("geometry", {}).get("location", {})
            lat = coordinates.get("lat")
            lng = coordinates.get("lng")
            if lat is None or lng is None:
                return {"error": "invalid_coordinates"}
            success = True
            return {
                "lat": lat,
                "lng": lng,
                "formatted_address": result.get("formatted_address") or location,
            }
        except Exception as exc:
            return {"error": self._safe_error(exc)}
        finally:
            self._record_timing(
                timing_recorder,
                "google_geocode",
                "Google Geocode",
                started,
                success=success,
            )
