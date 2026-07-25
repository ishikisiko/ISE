from __future__ import annotations

from typing import Any

import pytest

import server
from evidence import EvidenceItem
from langchain.langchain_orchestrator import LangChainOrchestrator
from langchain.langchain_rag import SearchRAGChain
from langchain.langchain_react_tools import (
    ReActLocalDocTool,
    ReActSearchRecoveryTool,
    ReActSearchTool,
)
from orchestrators.react_agent_orchestrator import ReactAgentOrchestrator
from search.search import SearchClient, SearchHit
from utils.workflow_trace import WorkflowTracer


class StubSearchClient(SearchClient):
    def __init__(self, hits: list[SearchHit]) -> None:
        super().__init__()
        self._hits = hits

    def search(
        self,
        query: str,
        num_results: int = 5,
        *,
        per_source_limit: int | None = None,
        freshness: str | None = None,
        date_restrict: str | None = None,
    ) -> list[SearchHit]:
        return list(self._hits)


def test_react_tools_wrap_search_and_local_docs(monkeypatch, tmp_path):
    search_tool = ReActSearchTool(
        search_client=StubSearchClient(
            [
                SearchHit(
                    title="OpenAI",
                    url="https://openai.com/",
                    snippet="AI research and products.",
                )
            ]
        )
    )
    assert "OpenAI" in search_tool._run("openai")
    assert "https://openai.com/" in search_tool._run("openai")

    local_tool = ReActLocalDocTool(data_path=str(tmp_path), llm=None)

    class StubLocalSource:
        def retrieve(self, query: str, options: Any):
            return [
                type(
                    "Item",
                    (),
                    {
                        "title": "notes.md",
                        "snippet": "Local answer",
                        "content": "Local answer",
                        "source_type": "local",
                        "source_id": str(tmp_path),
                        "reference": "notes.md",
                    },
                )()
            ]

    monkeypatch.setattr(local_tool, "_get_source", lambda: StubLocalSource())
    result = local_tool._run("local question")
    assert "Relevant Local Evidence" in result
    assert "notes.md" in result


def test_react_search_recovery_tool_formats_high_level_payload(monkeypatch):
    tool = ReActSearchRecoveryTool(llm=object(), search_client=StubSearchClient([]), data_path=None)

    class StubSearchRAGChain:
        def answer(self, *args: Any, **kwargs: Any):
            return {
                "answer": "Recovered answer",
                "search_hits": [
                    {"title": "OpenAI", "url": "https://openai.com/", "snippet": "AI research and products."}
                ],
                "retrieved_docs": [{"source": "notes.md", "content": "Doc summary"}],
                "evidence_sources_used": [{"source_type": "web", "source_id": "combined"}],
                "evidence_summary": "1. [web:combined] OpenAI | https://openai.com/ | AI research and products.",
            }

    monkeypatch.setattr(tool, "_get_chain", lambda: StubSearchRAGChain())
    result = tool._run("recover this answer")
    assert "Recovered answer" in result
    assert "https://openai.com/" in result
    assert "notes.md" in result
    assert "web:combined" in result


def test_search_rag_chain_projects_compatibility_fields_from_evidence_items(monkeypatch):
    chain = SearchRAGChain(
        llm=StubLLM("Unified answer"),
        search_client=StubSearchClient([]),
        data_path=None,
    )

    monkeypatch.setattr(
        chain,
        "_retrieve_evidence",
        lambda *args, **kwargs: {
            "effective_query": "unified query",
            "evidence_items": [
                EvidenceItem(
                    source_type="domain",
                    source_id="domain:weather",
                    title="weather evidence",
                    content="Weather is sunny.",
                    reference="google-weather",
                    snippet="Weather is sunny.",
                ),
                EvidenceItem(
                    source_type="web",
                    source_id="combined",
                    title="OpenAI",
                    content="AI research and products.",
                    reference="https://openai.com/",
                    snippet="AI research and products.",
                ),
                EvidenceItem(
                    source_type="local",
                    source_id="/tmp/docs",
                    title="notes.md",
                    content="Local document snippet",
                    reference="notes.md",
                    snippet="Local document snippet",
                    metadata={"source": "notes.md"},
                ),
            ],
            "active_sources": [
                {"source_type": "domain", "source_id": "domain:weather"},
                {"source_type": "web", "source_id": "combined"},
                {"source_type": "local", "source_id": "/tmp/docs"},
            ],
            "used_sources": [
                {"source_type": "domain", "source_id": "domain:weather"},
                {"source_type": "web", "source_id": "combined"},
                {"source_type": "local", "source_id": "/tmp/docs"},
            ],
            "search_error": None,
            "search_warnings": [],
            "rerank_meta": [],
            "fusion_meta": [],
            "skill_result": {"answer": "Weather is sunny."},
        },
    )

    result = chain.answer("Need unified evidence")

    assert result["answer"].startswith("Unified answer")
    assert result["search_hits"][0]["title"] == "OpenAI"
    assert result["retrieved_docs"][0]["source"] == "notes.md"
    assert result["evidence_source_types_used"] == ["domain", "local", "web"]
    assert "领域来源" in result["answer"]


class StubLLM:
    provider = "stub"
    model_name = "stub-model"

    def __init__(self, response: str = "") -> None:
        self.response = response

    def invoke(self, messages: list[Any], *args: Any, **kwargs: Any):
        class Response:
            def __init__(self, content: str) -> None:
                self.content = content
                self.response_metadata = {"stub": True}

        return Response(self.response)


class StubRoutingLLM(StubLLM):
    pass


class StubPipeline:
    def answer(self, *args: Any, **kwargs: Any):
        return {
            "query": args[0],
            "answer": "英伟达营收是 9999 亿美元。",
            "search_hits": [
                {"title": "Revenue", "url": "https://example.com", "snippet": "Revenue was 609亿美元 in 2024."}
            ],
            "retrieved_docs": [],
            "llm_raw": None,
            "evidence_items": [{"source_type": "web", "source_id": "combined", "title": "Revenue", "reference": "https://example.com", "snippet": "Revenue was 609亿美元 in 2024."}],
            "evidence_summary": "1. [web:combined] Revenue | https://example.com | Revenue was 609亿美元 in 2024.",
            "evidence_sources_active": [{"source_type": "web", "source_id": "combined"}],
            "evidence_sources_used": [{"source_type": "web", "source_id": "combined"}],
            "evidence_source_types_active": ["web"],
            "evidence_source_types_used": ["web"],
        }


class StubFallbackOrchestrator:
    def answer(self, query: str, **kwargs: Any):
        fallback_context = kwargs.get("fallback_context") or {}
        return {
            "query": query,
            "answer": "Recovered answer",
            "search_hits": list(fallback_context.get("search_hits") or []),
            "evidence_items": list(fallback_context.get("evidence_items") or []),
            "evidence_sources_active": list(fallback_context.get("evidence_sources_active") or []),
            "evidence_sources_used": list(fallback_context.get("evidence_sources_used") or []),
            "evidence_source_types_active": list(fallback_context.get("evidence_source_types_active") or []),
            "evidence_source_types_used": list(fallback_context.get("evidence_source_types_used") or []),
            "control": {
                "search_mode": "react_fallback",
            },
        }


def test_server_build_pipeline_ignores_retired_orchestrator_mode_key(monkeypatch):
    base_config = {
        "providers": {
            "minimax": {
                "api_key": "valid-key",
                "model": "MiniMax-M2.7-highspeed",
            }
        },
        "braveSearch": {},
        "brightDataSearch": {},
        "googleSearch": {},
        "rerank": {},
        "displayResponseTimes": False,
    }

    monkeypatch.setattr(server, "create_chat_model", lambda config=None: object())
    monkeypatch.setattr(server, "build_search_client", lambda config, sources=None: None)
    monkeypatch.setattr(server, "build_reranker", lambda config: (None, config.get("rerank", {})))

    default_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        server,
        "create_langchain_orchestrator",
        lambda **kwargs: default_calls.append(kwargs) or "default-orchestrator",
    )

    # `orchestrator_mode` was removed in the M5 alignment audit. A stale value
    # left in a deployed config must not be able to divert the pipeline.
    for stale in ("react", "langchain", "legacy"):
        monkeypatch.setattr(
            server, "load_base_config", lambda stale=stale: {**base_config, "orchestrator_mode": stale}
        )
        assert server.build_pipeline() == "default-orchestrator"
    assert len(default_calls) == 3


def test_server_build_pipeline_passes_resolved_chunk_settings(monkeypatch):
    base_config = {
        "providers": {
            "minimax": {
                "api_key": "valid-key",
                "model": "MiniMax-M2.7-highspeed",
            }
        },
        "localRag": {
            "chunk_size": 777,
            "chunk_overlap": 111,
        },
        "braveSearch": {},
        "brightDataSearch": {},
        "googleSearch": {},
        "rerank": {},
        "displayResponseTimes": False,
    }

    monkeypatch.setattr(server, "create_chat_model", lambda config=None: object())
    monkeypatch.setattr(server, "build_search_client", lambda config, sources=None: None)
    monkeypatch.setattr(server, "build_reranker", lambda config: (None, config.get("rerank", {})))

    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        server,
        "create_langchain_orchestrator",
        lambda **kwargs: captured.append(kwargs) or "default-orchestrator",
    )
    monkeypatch.setattr(server, "load_base_config", lambda: base_config)

    assert server.build_pipeline() == "default-orchestrator"
    assert captured[-1]["chunk_size"] == 777
    assert captured[-1]["chunk_overlap"] == 111


def test_react_agent_orchestrator_langgraph_missing_package_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "orchestrators.react_loop_graph.langgraph_available",
        lambda: False,
    )
    with pytest.raises(RuntimeError, match="langgraph is required"):
        ReactAgentOrchestrator(
            llm=object(),
            tools=[],
            max_iterations=2,
            config={},
        )


def test_react_agent_orchestrator_langgraph_loop_status_metadata():
    from orchestrators.react_loop_graph import langgraph_available

    if not langgraph_available():
        pytest.skip("langgraph not installed")

    from tests.test_react_loop_graph import FakeTools, NativeScriptedChatModel, _tool_call

    tools = [FakeTools.make("web_search", ["2025年苹果和微软分别公布财报，苹果相比微软更强，而微软同时领先，" * 6])]
    llm = NativeScriptedChatModel(
        replies=[_tool_call("web_search"), "2025年苹果相比微软更强，而微软同时领先，" * 6]
    )
    orchestrator = ReactAgentOrchestrator(
        llm=llm,
        tools=tools,
        max_iterations=4,
        config={},
    )
    tracer = WorkflowTracer()
    result = orchestrator.answer("苹果和微软的区别", tracer=tracer)

    control = result["control"]
    assert control["loop_status"] == "succeeded"
    assert isinstance(control["loop_verdicts"], list) and control["loop_verdicts"]
    # 既有字段保持兼容
    assert control["search_mode"] == "agentic_loop"
    assert control["max_iterations"] == 4
    assert "decision" in control
    assert control["react_trace"]
    assert control["react_trace_truncated"] is False
    assert any(event["id"] == "react_tool_1_1" for event in control["react_trace"])
    assert any(event["id"] == "react_tool_1_1" for event in tracer.events)
    outer_event = next(
        event for event in tracer.events if event["id"] == "react_loop" and event["status"] == "done"
    )
    assert "items" not in outer_event
    assert result["answer"]
