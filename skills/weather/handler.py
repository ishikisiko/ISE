"""Google Weather and Air Quality skill."""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

import requests

from evidence import EvidenceItem, RetrievalOptions
from skills._base import GoogleApiSkill, has_query_term
from skills.contracts import PreflightResult


WEATHER_TERMS = (
    "weather", "temperature", "rain", "raining", "snow", "humid", "humidity",
    "forecast", "air quality", "aqi", "pm2.5", "pm10", "smog", "天气", "天氣",
    "气温", "氣溫", "温度", "溫度", "下雨", "下雪", "湿度", "濕度", "预报",
    "預報", "空气质量", "空氣質量", "空气污染", "雾霾",
)

AIR_QUALITY_TERMS = (
    "air quality", "aqi", "pm2.5", "pm10", "smog", "空气质量", "空氣質量",
    "空气污染", "空氣污染", "雾霾",
)

FORECAST_TERMS = (
    "tomorrow", "forecast", "next week", "明天", "后天", "後天", "预报", "預報",
)


class WeatherSkillHandler(GoogleApiSkill):
    display_name = "Weather Conditions"

    def handles_query(self, query: str) -> bool:
        return has_query_term(query, WEATHER_TERMS)

    def preflight(self, args: Dict[str, Any]) -> PreflightResult:
        query = str((args or {}).get("query") or "").strip()
        if not query:
            return PreflightResult.reject("query_required")
        if not self.handles_query(query):
            return PreflightResult.reject("not_weather_query", query=query)
        location = self.extract_location(query)
        if not location:
            return PreflightResult.reject("location_required", query=query)
        lowered = query.casefold()
        mode = "air_quality" if any(term in lowered for term in AIR_QUALITY_TERMS) else (
            "forecast" if any(term in lowered for term in FORECAST_TERMS) else "current"
        )
        day_offset = 2 if any(term in lowered for term in ("后天", "後天")) else (
            1 if any(term in lowered for term in ("tomorrow", "明天")) else 0
        )
        return PreflightResult.accept(
            query=query,
            location=location,
            mode=mode,
            day_offset=day_offset,
        )

    @staticmethod
    def extract_location(query: str) -> str:
        text = str(query or "").strip()
        english_patterns = (
            r"(?:weather|temperature|humidity|air quality|forecast)\s+(?:in|for|at)\s+(.+)",
            r"(?:raining|snowing)\s+in\s+(.+)",
            r"(?:rain|snow)\s+in\s+(.+)",
        )
        for pattern in english_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                location = match.group(1)
                location = re.sub(
                    r"\s+(?:today|tomorrow|right now|now)(?:\s+again)?[?!.]*$",
                    "",
                    location,
                    flags=re.IGNORECASE,
                )
                return location.strip(" ?!.,")

        chinese_patterns = (
            r"(.+?)(?:今天|明天|后天|後天)?(?:的)?(?:天气|天氣|气温|氣溫|温度|溫度|空气质量|空氣質量|空气污染|空氣污染)",
            r"(.+?)(?:会不会|會不會|是否)?(?:下雨|下雪)",
        )
        for pattern in chinese_patterns:
            match = re.search(pattern, text)
            if match:
                location = re.sub(r"^(?:请问|請問|查询|查一下)", "", match.group(1))
                return location.strip(" ?!，。")

        match = re.search(r"\b(?:in|at|for)\b\s+([^?!.]+)", text, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    def retrieve(self, query: str, options: RetrievalOptions) -> List[EvidenceItem]:
        preflight = self.preflight({"query": query})
        return self.run(preflight.normalized_args, options) if preflight.accepted else []

    def run(self, args: Dict[str, Any], options: RetrievalOptions) -> List[EvidenceItem]:
        geocode = self.geocode(
            str(args["location"]), timing_recorder=options.timing_recorder
        )
        if geocode.get("error"):
            return []
        mode = str(args.get("mode") or "current")
        if mode == "air_quality":
            data = self._air_quality(geocode, options.timing_recorder)
            endpoint = "https://airquality.googleapis.com/v1/currentConditions:lookup"
            content = self.format_air_quality(args["location"], geocode, data)
        elif mode == "forecast":
            data = self._forecast(geocode, options.timing_recorder)
            endpoint = "https://weather.googleapis.com/v1/forecast/days:lookup"
            content = self.format_forecast(
                args["location"], geocode, data, int(args.get("day_offset") or 0)
            )
        else:
            data = self._current(geocode, options.timing_recorder)
            endpoint = "https://weather.googleapis.com/v1/currentConditions:lookup"
            content = self.format_current(args["location"], geocode, data)
        if not data or data.get("error") or not content:
            return []
        return [
            self._item(
                title=f"{args['location']} {mode}",
                content=content,
                reference=endpoint,
                provider="google_weather" if mode != "air_quality" else "google_air_quality",
                data=data,
                options=options,
                metadata={"location": geocode, "mode": mode},
            )
        ]

    def _current(self, geocode: Dict[str, Any], timing_recorder: Optional[Any]) -> Dict[str, Any]:
        return self._get_weather(
            "currentConditions:lookup", geocode, timing_recorder, "google_weather", "Google Weather"
        )

    def _forecast(self, geocode: Dict[str, Any], timing_recorder: Optional[Any]) -> Dict[str, Any]:
        return self._get_weather(
            "forecast/days:lookup",
            geocode,
            timing_recorder,
            "google_weather_forecast",
            "Google Weather Forecast",
            extra={"days": 3, "pageSize": 3},
        )

    def _get_weather(
        self,
        path: str,
        geocode: Dict[str, Any],
        timing_recorder: Optional[Any],
        source: str,
        label: str,
        *,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        success = False
        params = {
            "key": self.google_api_key,
            "location.latitude": geocode["lat"],
            "location.longitude": geocode["lng"],
            "languageCode": "zh-CN",
            **(extra or {}),
        }
        try:
            response = requests.get(
                f"https://weather.googleapis.com/v1/{path}",
                params=params,
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
                timing_recorder, source, label, started, success=success
            )

    def _air_quality(self, geocode: Dict[str, Any], timing_recorder: Optional[Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        success = False
        try:
            response = requests.post(
                "https://airquality.googleapis.com/v1/currentConditions:lookup",
                params={"key": self.google_api_key},
                json={
                    "location": {"latitude": geocode["lat"], "longitude": geocode["lng"]},
                    "extraComputations": [
                        "HEALTH_RECOMMENDATIONS",
                        "DOMINANT_POLLUTANT_CONCENTRATION",
                        "POLLUTANT_CONCENTRATION",
                        "LOCAL_AQI",
                    ],
                    "universalAqi": True,
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
                "google_air_quality",
                "Google Air Quality",
                started,
                success=success,
            )

    @staticmethod
    def format_current(location_hint: str, geocode: Dict[str, Any], data: Dict[str, Any]) -> str:
        condition = data.get("weatherCondition", {}).get("description", {}).get("text", "未知")
        temperature = data.get("temperature", {}).get("degrees", "未知")
        humidity = data.get("relativeHumidity", "未知")
        wind = data.get("wind", {}).get("speed", {}).get("value", "未知")
        location = geocode.get("formatted_address") or location_hint
        return f"{location} 当前天气：{condition}，{temperature}°C，湿度 {humidity}%，风速 {wind} km/h。"

    @staticmethod
    def format_forecast(
        location_hint: str,
        geocode: Dict[str, Any],
        data: Dict[str, Any],
        day_offset: int,
    ) -> str:
        days = data.get("forecastDays") or []
        if not days:
            return ""
        selected = days[min(max(day_offset, 0), len(days) - 1)]
        date = selected.get("displayDate") or {}
        date_text = f"{date.get('year', '?')}-{date.get('month', '?'):0>2}-{date.get('day', '?'):0>2}"
        daytime = selected.get("daytimeForecast") or {}
        condition = daytime.get("weatherCondition", {}).get("description", {}).get("text", "未知")
        high = selected.get("maxTemperature", {}).get("degrees", "未知")
        low = selected.get("minTemperature", {}).get("degrees", "未知")
        humidity = daytime.get("relativeHumidity", "未知")
        location = geocode.get("formatted_address") or location_hint
        return f"{location} {date_text} 天气预报：{condition}，{low}°C 至 {high}°C，日间湿度 {humidity}%。"

    @staticmethod
    def format_air_quality(location_hint: str, geocode: Dict[str, Any], data: Dict[str, Any]) -> str:
        indexes = data.get("indexes") or []
        if not indexes:
            return ""
        index = next((item for item in indexes if item.get("code") == "uaqi"), indexes[0])
        location = geocode.get("formatted_address") or location_hint
        answer = f"{location} 当前空气质量：AQI {index.get('aqi', '未知')}（{index.get('category', '未知')}）"
        pollutant = index.get("dominantPollutant")
        if pollutant:
            answer += f"，主要污染物 {pollutant}"
        recommendation = (data.get("healthRecommendations") or {}).get("generalPopulation")
        if recommendation:
            answer += f"。健康建议：{recommendation}"
        return answer + "。"
