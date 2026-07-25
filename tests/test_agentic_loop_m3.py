"""M3 skill migrations and router-removal regressions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from evidence import EvidenceSource, RetrievalOptions
from langchain.langchain_orchestrator import LangChainOrchestrator
from langchain.langchain_react_tools import ReActSkillTool, create_react_tools_from_config
from orchestrators.react_loop_graph import ReactLoopGraphRunner
from skills import SkillRegistry
from utils.timing_utils import TimingRecorder


ROOT = Path(__file__).resolve().parents[1]
M3_SKILLS = ("weather", "location", "transportation", "sports")
TOOL_NAMES = {
    "weather": "weather_conditions",
    "location": "nearby_places",
    "transportation": "route_directions",
    "sports": "sports_schedule",
}


@pytest.fixture()
def registry() -> SkillRegistry:
    return SkillRegistry.from_config({"GOOGLE_API_KEY": "test-key"})


@pytest.mark.parametrize("skill_name", M3_SKILLS)
def test_m3_manifest_mounts_an_evidence_source(registry: SkillRegistry, skill_name: str) -> None:
    handler = registry.get(skill_name)

    assert isinstance(handler, EvidenceSource)
    assert handler.manifest.tool_name == TOOL_NAMES[skill_name]
    assert handler.manifest.budget["max_calls_per_query"] > 0
    assert handler.manifest.budget["timeout_seconds"] > 0
    assert handler.manifest.budget["max_evidence_items"] > 0


@pytest.mark.parametrize("skill_name", M3_SKILLS)
def test_m3_prose_examples_are_executable_evals(
    registry: SkillRegistry, skill_name: str
) -> None:
    handler = registry.get(skill_name)
    cases = [
        json.loads(line)
        for line in (ROOT / "skills" / skill_name / "evals/cases.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    for case in cases:
        assert handler.handles_query(case["query"]) is (
            case["expect"] == skill_name
        ), case["query"]
        assert handler.preflight({"query": case["query"]}).reason == case["preflight"]


def test_react_tool_surface_is_registry_driven_without_domain_router() -> None:
    tools = create_react_tools_from_config(config={"GOOGLE_API_KEY": "test-key"})
    names = [tool.name for tool in tools]

    assert set(TOOL_NAMES.values()).issubset(names)
    assert names.count("finance_market_data") == 1
    assert "domain_api" not in names


def test_preflight_rejection_is_a_loop_tool_error(registry: SkillRegistry) -> None:
    tool = ReActSkillTool(skill_handler=registry.get("weather"))

    payload = json.loads(tool._run("Will it rain tomorrow?"))

    assert payload["status"] == "rejected"
    assert payload["reason"] == "location_required"
    assert ReactLoopGraphRunner._is_textual_tool_error(json.dumps(payload)) is True


def test_skill_preflight_resolves_generic_pronoun_ambiguity() -> None:
    class LLMStub:
        provider = "stub"
        model_name = "stub"

        def invoke(self, *args: Any, **kwargs: Any) -> Any:
            return type("Response", (), {"content": "unused"})()

    class LoopStub:
        def __init__(self) -> None:
            self.analysis = None

        def answer(self, query: str, **kwargs: Any) -> dict[str, Any]:
            self.analysis = kwargs.get("analysis")
            return {
                "query": query,
                "answer": "loop answer",
                "search_hits": [],
                "evidence_records": [],
                "control": {
                    "loop_status": "succeeded",
                    "loop_iterations": 1,
                    "loop_verdicts": [],
                },
            }

    orchestrator = LangChainOrchestrator(
        llm=LLMStub(),
        config={"GOOGLE_API_KEY": "test-key"},
    )
    loop = LoopStub()
    orchestrator._loop_orchestrator = loop

    orchestrator.answer("Is it raining in New York?")

    assert loop.analysis is not None
    assert loop.analysis.critical_ambiguity is False
    assert "unresolved_entity_reference" not in loop.analysis.ambiguities


def test_accepted_skill_no_data_is_observed_as_loop_tool_error(
    registry: SkillRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    weather = registry.get("weather")
    monkeypatch.setattr(weather, "geocode", lambda *args, **kwargs: {
        "lat": 1.0,
        "lng": 2.0,
        "formatted_address": "Beijing",
    })
    monkeypatch.setattr(weather, "_forecast", lambda *args, **kwargs: {})
    tool = ReActSkillTool(skill_handler=weather)

    payload = json.loads(tool._run("Will it snow in Beijing tomorrow?"))

    assert payload["status"] == "no_data"
    assert ReactLoopGraphRunner._is_textual_tool_error(json.dumps(payload)) is True

def test_m3_skill_results_preserve_provider_and_tool_provenance(
    registry: SkillRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = RetrievalOptions(metadata={"originating_tool_call": "skill_1"})
    geocode = {"lat": 1.0, "lng": 2.0, "formatted_address": "Test Place"}

    weather = registry.get("weather")
    monkeypatch.setattr(weather, "geocode", lambda *args, **kwargs: geocode)
    monkeypatch.setattr(weather, "_current", lambda *args, **kwargs: {
        "weatherCondition": {"description": {"text": "Sunny"}},
        "temperature": {"degrees": 25},
        "relativeHumidity": 60,
        "wind": {"speed": {"value": 8}},
    })

    location = registry.get("location")
    monkeypatch.setattr(location, "geocode", lambda *args, **kwargs: geocode)
    monkeypatch.setattr(location, "_search_text", lambda *args, **kwargs: {
        "places": [{
            "displayName": {"text": "KFC"},
            "formattedAddress": "1 Test Road",
            "location": {"latitude": 1.001, "longitude": 2.001},
        }]
    })

    transportation = registry.get("transportation")
    monkeypatch.setattr(transportation, "geocode", lambda name, **kwargs: {
        **geocode, "formatted_address": name
    })
    monkeypatch.setattr(transportation, "_compute_route", lambda *args, **kwargs: {
        "routes": [{"duration": "600s", "distanceMeters": 5000}]
    })

    sports = registry.get("sports")
    responses: list[dict[str, Any]] = [
        {"teams": [{"idTeam": "1"}]},
        {"events": [{
            "strEvent": "Warriors vs Lakers",
            "strHomeTeam": "Warriors",
            "strAwayTeam": "Lakers",
            "strLeague": "NBA",
            "dateEvent": "2026-07-26",
        }]},
    ]
    monkeypatch.setattr(sports, "_request", lambda *args, **kwargs: responses.pop(0))

    queries = {
        "weather": "What is the weather in Bend, Oregon today?",
        "location": "nearest KFC to HKUST",
        "transportation": "drive from SFO to San Jose",
        "sports": "When is the next NBA game for the Warriors?",
    }
    for skill_name, query in queries.items():
        result = registry.execute(skill_name, {"query": query}, options=options)
        assert result.preflight.accepted is True
        assert len(result.evidence_items) == 1
        item = result.evidence_items[0]
        assert item.source_id == f"skill:{skill_name}"
        assert item.source_type == "domain"
        assert item.metadata["skill"] == skill_name
        assert item.metadata["tool_name"] == TOOL_NAMES[skill_name]
        assert item.metadata["provider"]
        assert item.metadata["originating_tool_call"] == "skill_1"


def test_router_symbols_and_source_selector_file_are_gone() -> None:
    assert not (ROOT / "search/source_selector.py").exists()
    # The legacy orchestrator was a second runtime with its own stopping
    # logic and no audit; it is deleted rather than exempted.
    assert not (ROOT / "orchestrators/smart_orchestrator.py").exists()
    production = [
        ROOT / "langchain/langchain_orchestrator.py",
        ROOT / "langchain/langchain_rag.py",
        ROOT / "langchain/langchain_react_tools.py",
        ROOT / "orchestrators/react_agent_orchestrator.py",
        ROOT / "evidence/source_layer.py",
        ROOT / "utils/query_orchestration.py",
    ]
    forbidden = (
        "domain_api",
        "ReActDomainTool",
        "DomainEvidenceSource",
        "select_sources",
        "generate_domain_specific_query",
        "classify_domain",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in production)
    assert not any(symbol in combined for symbol in forbidden)


def test_skill_provider_errors_redact_url_queries(registry: SkillRegistry) -> None:
    handler = registry.get("weather")
    error = requests.HTTPError(
        "404 for https://weather.googleapis.com/v1/currentConditions:lookup?key=secret-value&location.latitude=1"
    )

    message = handler._safe_error(error)

    assert "secret-value" not in message
    assert "?" not in message
    assert "HTTPError" in message

    sports_registry = SkillRegistry.from_config({"SPORTSDB_API_KEY": "premium-secret"})
    sports = sports_registry.get("sports")
    path_error = requests.HTTPError(
        "401 for https://www.thesportsdb.com/api/v1/json/premium-secret/eventsnext.php?id=1"
    )
    assert "premium-secret" not in sports._safe_error(path_error)


def test_skill_provider_calls_enter_timing_tool_calls(
    registry: SkillRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self.payload

    def fake_get(url: str, **kwargs: Any) -> Response:
        if "geocode" in url:
            return Response({
                "results": [{
                    "formatted_address": "New York",
                    "geometry": {"location": {"lat": 1.0, "lng": 2.0}},
                }]
            })
        return Response({
            "weatherCondition": {"description": {"text": "Sunny"}},
            "temperature": {"degrees": 25},
            "relativeHumidity": 50,
            "wind": {"speed": {"value": 8}},
        })

    monkeypatch.setattr("requests.get", fake_get)
    recorder = TimingRecorder(enabled=True)
    recorder.start()

    result = registry.execute(
        "weather",
        {"query": "Is it raining in New York?"},
        options=RetrievalOptions(timing_recorder=recorder),
    )
    timings = recorder.to_dict()

    assert len(result.evidence_items) == 1
    assert [entry["tool"] for entry in timings["tool_calls"]] == [
        "google_geocode",
        "google_weather",
    ]
    assert all(entry["success"] for entry in timings["tool_calls"])
