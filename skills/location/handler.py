"""Deterministic nearby-place search using Places Text Search (New)."""

from __future__ import annotations

import math
import re
import time
from typing import Any, Dict, List, Optional

import requests

from evidence import EvidenceItem, RetrievalOptions
from skills._base import GoogleApiSkill, has_query_term
from skills.contracts import PreflightResult


LOCATION_TERMS = (
    "nearest", "nearby", "closest", " near ", "locate", "附近", "最近的", "离",
    "距離", "周边", "周邊",
)


class LocationSkillHandler(GoogleApiSkill):
    display_name = "Nearby Places"

    def handles_query(self, query: str) -> bool:
        return has_query_term(query, LOCATION_TERMS)

    def preflight(self, args: Dict[str, Any]) -> PreflightResult:
        query = str((args or {}).get("query") or "").strip()
        if not query:
            return PreflightResult.reject("query_required")
        if not self.handles_query(query):
            return PreflightResult.reject("not_location_query", query=query)
        parsed = self.extract_query(query)
        if not parsed:
            return PreflightResult.reject("explicit_location_required", query=query)
        reference = parsed["reference_location"].casefold().strip(" .")
        if reference in {"me", "my location", "my current location", "current location", "我", "我的位置", "当前位置", "當前位置"}:
            return PreflightResult.reject("explicit_location_required", query=query)
        return PreflightResult.accept(query=query, **parsed)

    @staticmethod
    def extract_query(query: str) -> Optional[Dict[str, str]]:
        patterns_cn = (
            r"距离(.+?)最近的(.+?)(?:是哪|在哪|有哪|$)",
            r"离(.+?)最近的(.+?)(?:是哪|在哪|有哪|$)",
            r"(.+?)附近的(.+?)(?:是哪|在哪|有哪|在哪里|$)",
            r"(.+?)附近有(?:什么|哪些)?(.+)",
            r"(.+?)周边的(.+)",
        )
        for pattern in patterns_cn:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                reference = match.group(1).strip()
                target = re.sub(
                    r"(?:是哪家|在哪里|有哪些|是什么|在哪里)$", "", match.group(2)
                ).strip(" ?!，。")
                if reference and target:
                    return {"reference_location": reference, "target_type": target}

        patterns_en = (
            r"(?:nearest|closest)\s+(.+?)\s+(?:to|from|near)\s+(.+)",
            r"find\s+(.+?)\s+near\s+(.+)",
            r"(.+?)\s+near\s+(.+)",
        )
        for pattern in patterns_en:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                return {
                    "reference_location": match.group(2).strip(" ?!.,"),
                    "target_type": match.group(1).strip(" ?!.,"),
                }
        return None

    def retrieve(self, query: str, options: RetrievalOptions) -> List[EvidenceItem]:
        preflight = self.preflight({"query": query})
        return self.run(preflight.normalized_args, options) if preflight.accepted else []

    def run(self, args: Dict[str, Any], options: RetrievalOptions) -> List[EvidenceItem]:
        geocode = self.geocode(
            str(args["reference_location"]), timing_recorder=options.timing_recorder
        )
        if geocode.get("error"):
            return []
        places = self._search_text(
            geocode,
            str(args["target_type"]),
            timing_recorder=options.timing_recorder,
        )
        if places.get("error") or not places.get("places"):
            return []
        answer = self.format_answer(
            str(args["reference_location"]), geocode, str(args["target_type"]), places["places"]
        )
        return [
            self._item(
                title=f"{args['target_type']} near {args['reference_location']}",
                content=answer,
                reference="https://places.googleapis.com/v1/places:searchText",
                provider="google_places",
                data=places,
                options=options,
                metadata={
                    "reference_location": geocode,
                    "target_type": args["target_type"],
                },
            )
        ]

    def _search_text(
        self,
        geocode: Dict[str, Any],
        target: str,
        *,
        timing_recorder: Optional[Any],
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        success = False
        try:
            response = requests.post(
                "https://places.googleapis.com/v1/places:searchText",
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": self.google_api_key,
                    "X-Goog-FieldMask": (
                        "places.displayName,places.formattedAddress,places.location,"
                        "places.rating,places.userRatingCount,places.googleMapsUri"
                    ),
                },
                json={
                    "textQuery": target,
                    "locationBias": {
                        "circle": {
                            "center": {
                                "latitude": geocode["lat"],
                                "longitude": geocode["lng"],
                            },
                            "radius": 5000.0,
                        }
                    },
                    "maxResultCount": 10,
                    "languageCode": "zh-CN",
                },
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            payload = response.json()
            success = True
            return payload
        except Exception as exc:
            return {"error": self._safe_error(exc)}
        finally:
            self._record_timing(
                timing_recorder,
                "google_places_text",
                "Google Places Text Search",
                started,
                success=success,
            )

    @classmethod
    def format_answer(
        cls,
        reference_location: str,
        geocode: Dict[str, Any],
        target_type: str,
        places: List[Dict[str, Any]],
    ) -> str:
        ranked = []
        for place in places:
            location = place.get("location") or {}
            distance = cls._distance_km(
                geocode.get("lat"), geocode.get("lng"),
                location.get("latitude"), location.get("longitude"),
            )
            ranked.append((distance, place))
        ranked.sort(key=lambda item: item[0] if item[0] is not None else float("inf"))
        reference = geocode.get("formatted_address") or reference_location
        lines = [f"{reference} 附近的 {target_type}："]
        for index, (distance, place) in enumerate(ranked[:5], start=1):
            name = (place.get("displayName") or {}).get("text") or "未知名称"
            address = place.get("formattedAddress") or ""
            suffix = ""
            if distance is not None:
                suffix = f"，约 {int(distance * 1000)} 米" if distance < 1 else f"，约 {distance:.1f} 公里"
            rating = place.get("rating")
            if rating is not None:
                suffix += f"，评分 {rating}"
            lines.append(f"{index}. {name}{suffix}{f'，{address}' if address else ''}")
        return "\n".join(lines)

    @staticmethod
    def _distance_km(
        lat1: Any, lng1: Any, lat2: Any, lng2: Any
    ) -> Optional[float]:
        if None in {lat1, lng1, lat2, lng2}:
            return None
        phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
        d_phi = math.radians(float(lat2) - float(lat1))
        d_lambda = math.radians(float(lng2) - float(lng1))
        value = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return 6371.0 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))
