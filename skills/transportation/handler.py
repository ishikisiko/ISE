"""Google Routes skill with deterministic endpoint parsing."""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

import requests

from evidence import EvidenceItem, RetrievalOptions
from skills._base import GoogleApiSkill, has_query_term
from skills.contracts import PreflightResult


TRANSPORT_TERMS = (
    " from ", "从", "交通", "公交", "地铁", "地鐵", "路线", "路線", "怎么走",
    "directions", "route", "drive", "driving", "transit", "train", "flight",
)


class TransportationSkillHandler(GoogleApiSkill):
    display_name = "Route Directions"

    def handles_query(self, query: str) -> bool:
        return has_query_term(query, TRANSPORT_TERMS)

    def preflight(self, args: Dict[str, Any]) -> PreflightResult:
        query = str((args or {}).get("query") or "").strip()
        if not query:
            return PreflightResult.reject("query_required")
        if not self.handles_query(query):
            return PreflightResult.reject("not_transportation_query", query=query)
        route = self.extract_route(query)
        if not route:
            return PreflightResult.reject("route_endpoints_required", query=query)
        vague_endpoints = {
            "airport", "the airport", "downtown", "city center", "my location",
            "当前位置", "當前位置", "机场", "機場", "市中心",
        }
        if any(
            str(route[key]).casefold().strip(" .") in vague_endpoints
            for key in ("origin", "destination")
        ):
            return PreflightResult.reject("explicit_route_endpoints_required", query=query)
        return PreflightResult.accept(query=query, **route)

    @staticmethod
    def extract_route(query: str) -> Optional[Dict[str, str]]:
        match = re.search(r"从(.+?)到(.+?)(?:怎么走|如何走|的路线|路線|[?？。]|$)", query)
        if match:
            origin, destination = match.group(1).strip(), match.group(2).strip()
        else:
            match = re.search(
                r"(?:drive|walk|bike|travel|go|get|directions?)?(?:\s+me)?\s*from\s+(.+?)\s+to\s+(.+?)(?:[?!.]|$)",
                query,
                re.IGNORECASE,
            )
            if not match:
                return None
            origin, destination = match.group(1).strip(), match.group(2).strip()
        if not origin or not destination:
            return None
        lowered = query.casefold()
        mode = "TRANSIT" if any(term in lowered for term in ("transit", "train", "subway", "bus", "公交", "地铁", "地鐵", "火车", "高铁")) else (
            "WALK" if any(term in lowered for term in ("walk", "walking", "步行")) else (
                "BICYCLE" if any(term in lowered for term in ("bike", "bicycle", "骑行", "單車")) else "DRIVE"
            )
        )
        return {"origin": origin, "destination": destination, "mode": mode}

    def retrieve(self, query: str, options: RetrievalOptions) -> List[EvidenceItem]:
        preflight = self.preflight({"query": query})
        return self.run(preflight.normalized_args, options) if preflight.accepted else []

    def run(self, args: Dict[str, Any], options: RetrievalOptions) -> List[EvidenceItem]:
        origin = self.geocode(str(args["origin"]), timing_recorder=options.timing_recorder)
        destination = self.geocode(
            str(args["destination"]), timing_recorder=options.timing_recorder
        )
        if origin.get("error") or destination.get("error"):
            return []
        data = self._compute_route(
            origin,
            destination,
            str(args.get("mode") or "DRIVE"),
            timing_recorder=options.timing_recorder,
        )
        if data.get("error") or not data.get("routes"):
            return []
        answer = self.format_answer(origin, destination, str(args.get("mode") or "DRIVE"), data)
        return [
            self._item(
                title=f"{args['origin']} to {args['destination']}",
                content=answer,
                reference="https://routes.googleapis.com/directions/v2:computeRoutes",
                provider="google_routes",
                data=data,
                options=options,
                metadata={"origin": origin, "destination": destination, "mode": args.get("mode")},
            )
        ]

    def _compute_route(
        self,
        origin: Dict[str, Any],
        destination: Dict[str, Any],
        mode: str,
        *,
        timing_recorder: Optional[Any],
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        payload: Dict[str, Any] = {
            "origin": {"location": {"latLng": {"latitude": origin["lat"], "longitude": origin["lng"]}}},
            "destination": {"location": {"latLng": {"latitude": destination["lat"], "longitude": destination["lng"]}}},
            "travelMode": mode,
            "languageCode": "zh-CN",
            "units": "METRIC",
        }
        if mode == "DRIVE":
            payload["routingPreference"] = "TRAFFIC_AWARE"
        try:
            success = False
            response = requests.post(
                "https://routes.googleapis.com/directions/v2:computeRoutes",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": self.google_api_key,
                    "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.description",
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
                "google_routes",
                "Google Routes",
                started,
                success=success,
            )

    @staticmethod
    def format_answer(
        origin: Dict[str, Any],
        destination: Dict[str, Any],
        mode: str,
        data: Dict[str, Any],
    ) -> str:
        route = (data.get("routes") or [{}])[0]
        distance_km = float(route.get("distanceMeters") or 0) / 1000
        duration_text = str(route.get("duration") or "0s")
        try:
            seconds = int(float(duration_text.rstrip("s")))
        except ValueError:
            seconds = 0
        hours, remainder = divmod(seconds, 3600)
        minutes = remainder // 60
        duration = f"{hours} 小时 {minutes} 分钟" if hours else f"{minutes} 分钟"
        labels = {"DRIVE": "驾车", "TRANSIT": "公共交通", "WALK": "步行", "BICYCLE": "骑行"}
        return (
            f"{origin.get('formatted_address')} -> {destination.get('formatted_address')}\n"
            f"{labels.get(mode, mode)}：预计 {duration}，距离 {distance_km:.1f} 公里。"
        )
