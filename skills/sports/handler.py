"""TheSportsDB-backed schedules and results skill."""

from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional

import requests

from evidence import EvidenceItem, RetrievalOptions
from skills._base import RuntimeSkillHandler, has_query_term
from skills.contracts import PreflightResult, SkillManifest
from utils.config_validation import configured_value


SPORTS_TERMS = (
    "game", "match", "score", "sports", "nba", "nfl", "wimbledon", "premier league",
    "比赛", "比賽", "比分", "赛事", "賽事", "足球", "篮球", "籃球", "网球", "網球",
    "世界杯", "英超", "奥运", "奧運",
)

TEAM_ALIASES = {
    "golden state warriors": "Golden State Warriors",
    "warriors": "Golden State Warriors",
    "勇士": "Golden State Warriors",
    "lakers": "Los Angeles Lakers",
    "湖人": "Los Angeles Lakers",
    "celtics": "Boston Celtics",
    "凯尔特人": "Boston Celtics",
    "凱爾特人": "Boston Celtics",
    "manchester united": "Manchester United",
    "man united": "Manchester United",
    "曼联": "Manchester United",
    "曼聯": "Manchester United",
}


class SportsSkillHandler(RuntimeSkillHandler):
    display_name = "Sports Schedule"

    def __init__(self, *, config: Dict[str, Any], manifest: SkillManifest) -> None:
        super().__init__(config=config, manifest=manifest)
        self.api_key = configured_value(
            config.get("SPORTSDB_API_KEY") or os.getenv("SPORTSDB_API_KEY")
        ) or "123"
        self.base_url = f"https://www.thesportsdb.com/api/v1/json/{self.api_key}"

    def handles_query(self, query: str) -> bool:
        return has_query_term(query, SPORTS_TERMS)

    def preflight(self, args: Dict[str, Any]) -> PreflightResult:
        query = str((args or {}).get("query") or "").strip()
        if not query:
            return PreflightResult.reject("query_required")
        if not self.handles_query(query):
            return PreflightResult.reject("not_sports_query", query=query)
        lowered = query.casefold()
        teams = list(dict.fromkeys(
            canonical for alias, canonical in TEAM_ALIASES.items() if alias in lowered
        ))
        if teams:
            schedule = "next" if any(term in lowered for term in ("next", "upcoming", "when", "下一", "下场", "下場")) else "previous"
            return PreflightResult.accept(
                query=query,
                mode="team_schedule",
                entity=teams[0],
                opponent=teams[1] if len(teams) > 1 else None,
                schedule=schedule,
            )
        event = self.extract_event(query)
        if event:
            return PreflightResult.accept(query=query, mode="event_search", entity=event)
        return PreflightResult.reject("sports_entity_required", query=query)

    @staticmethod
    def extract_event(query: str) -> str:
        text = re.sub(
            r"^(?:who won|what was|tell me|when is|please find)\s+", "", query,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\b(?:the|latest|next|recent|score of|score|game|match|result)\b",
            " ",
            text,
            flags=re.IGNORECASE,
        )
        text = " ".join(text.strip(" ?!.,").split())
        return text if len(text) >= 3 else ""

    def retrieve(self, query: str, options: RetrievalOptions) -> List[EvidenceItem]:
        preflight = self.preflight({"query": query})
        return self.run(preflight.normalized_args, options) if preflight.accepted else []

    def run(self, args: Dict[str, Any], options: RetrievalOptions) -> List[EvidenceItem]:
        if args.get("mode") == "team_schedule":
            team = self._request(
                "searchteams.php",
                {"t": args["entity"]},
                options.timing_recorder,
                "sportsdb_team_search",
            )
            teams = team.get("teams") or []
            if not teams:
                return []
            endpoint = "eventsnext.php" if args.get("schedule") == "next" else "eventslast.php"
            payload = self._request(
                endpoint,
                {"id": teams[0].get("idTeam")},
                options.timing_recorder,
                "sportsdb_schedule",
            )
        else:
            endpoint = "searchevents.php"
            payload = self._request(
                endpoint,
                {"e": args["entity"]},
                options.timing_recorder,
                "sportsdb_event_search",
            )
        events = payload.get("events") or []
        if not events:
            return []
        event = self._select_event(events, args.get("opponent"))
        answer = self.format_event(str(args["entity"]), event)
        reference = "https://www.thesportsdb.com/documentation"
        return [
            self._item(
                title=event.get("strEvent") or str(args["entity"]),
                content=answer,
                reference=reference,
                provider="thesportsdb",
                data=event,
                options=options,
                metadata={"mode": args.get("mode"), "schedule": args.get("schedule")},
            )
        ]

    def _request(
        self,
        endpoint: str,
        params: Dict[str, Any],
        timing_recorder: Optional[Any],
        source: str,
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        success = False
        try:
            response = requests.get(
                f"{self.base_url}/{endpoint}",
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
                timing_recorder,
                source,
                "TheSportsDB",
                started,
                success=success,
            )

    @staticmethod
    def _select_event(events: List[Dict[str, Any]], opponent: Optional[str]) -> Dict[str, Any]:
        if opponent:
            lowered = opponent.casefold()
            for event in events:
                if lowered in " ".join(
                    [str(event.get("strHomeTeam") or ""), str(event.get("strAwayTeam") or "")]
                ).casefold():
                    return event
        return events[0]

    @staticmethod
    def format_event(entity: str, event: Dict[str, Any]) -> str:
        home = event.get("strHomeTeam") or "未知主队"
        away = event.get("strAwayTeam") or "未知客队"
        home_score = event.get("intHomeScore")
        away_score = event.get("intAwayScore")
        score = f"{home_score}-{away_score}" if home_score is not None and away_score is not None else "未开始"
        return (
            f"{entity} 相关赛事：\n"
            f"对阵：{home} vs {away}\n"
            f"联赛：{event.get('strLeague') or '未知联赛'}\n"
            f"比分：{score}\n"
            f"时间：{event.get('dateEvent') or '未知日期'} {event.get('strTime') or ''}".rstrip()
        )
