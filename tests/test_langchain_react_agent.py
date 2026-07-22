from __future__ import annotations

from typing import Any

import pytest

import server
from evidence import EvidenceItem
from langchain.langchain_orchestrator import LangChainOrchestrator
from langchain.langchain_rag import SearchRAGChain
from langchain.langchain_react_tools import (
    ReActDomainTool,
    ReActLocalDocTool,
    ReActSearchRecoveryTool,
    ReActSearchTool,
)
from langchain.postcheck import merge_judge_verdict, screen_search_answer
from orchestrators.react_agent_orchestrator import ReactAgentOrchestrator
from search.search import SearchClient, SearchHit
from utils.time_parser import parse_time_constraint


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


class StubSourceSelector:
    def select_sources(self, query: str, timing_recorder: Any = None):
        return "weather", ["weather"]

    def fetch_domain_data(self, query: str, domain: str, timing_recorder: Any = None):
        return {
            "handled": True,
            "answer": "Weather is sunny.",
            "data": {"temperature": {"degrees": 25}},
            "continue_search": True,
        }


def test_react_tools_wrap_search_domain_and_local_docs(monkeypatch, tmp_path):
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

    domain_tool = ReActDomainTool(source_selector=StubSourceSelector(), llm=None)
    domain_result = domain_tool._run("weather in sf")
    assert "Weather is sunny." in domain_result
    assert "Evidence Source: domain:weather" in domain_result

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


def test_react_agent_orchestrator_answer_returns_compatible_payload(monkeypatch):
    class StubExecutor:
        def invoke(self, agent_input: dict[str, Any]) -> dict[str, str]:
            assert agent_input == {"input": "compare apple and microsoft"}
            return {"output": "Final synthesized answer"}

    monkeypatch.setattr(
        "orchestrators.react_agent_orchestrator.LangChainOrchestrator.create_react_agent",
        lambda **kwargs: StubExecutor(),
    )

    tool = ReActLocalDocTool(data_path="/tmp", llm=None)
    orchestrator = ReactAgentOrchestrator(
        llm=object(),
        tools=[tool],
        max_iterations=3,
        engine="legacy",
    )

    result = orchestrator.answer("compare apple and microsoft")

    assert result["answer"] == "Final synthesized answer"
    assert result["search_hits"] == []
    assert result["control"]["search_mode"] == "react_agent"
    assert result["control"]["max_iterations"] == 3
    assert result["control"]["local_docs_present"] is True
    assert result["control"]["evidence_sources_active"] == []


def test_react_agent_orchestrator_accepts_fallback_context(monkeypatch):
    class StubExecutor:
        def invoke(self, agent_input: dict[str, Any]) -> dict[str, str]:
            assert "Previous Answer" in agent_input["input"]
            assert "Post-check Failure Types: missing_time_constraint" in agent_input["input"]
            return {"output": "Recovered with fallback"}

    monkeypatch.setattr(
        "orchestrators.react_agent_orchestrator.LangChainOrchestrator.create_react_agent",
        lambda **kwargs: StubExecutor(),
    )

    orchestrator = ReactAgentOrchestrator(llm=object(), tools=[], max_iterations=2, engine="legacy")
    result = orchestrator.answer(
        "What changed over the last week?",
        fallback_context={
            "previous_answer": "Old answer",
            "failure_types": ["missing_time_constraint"],
            "missing_constraints": ["time_constraint"],
            "evidence_summary": "Existing search hit",
            "recovery_goal": "Cover the missing time constraint.",
            "search_hits": [{"title": "existing", "url": "https://example.com", "snippet": "snippet"}],
            "evidence_items": [{"source_type": "web", "source_id": "combined"}],
            "evidence_sources_active": [{"source_type": "web", "source_id": "combined"}],
            "evidence_sources_used": [{"source_type": "web", "source_id": "combined"}],
            "evidence_source_types_active": ["web"],
            "evidence_source_types_used": ["web"],
        },
    )

    assert result["answer"] == "Recovered with fallback"
    assert result["control"]["search_mode"] == "react_fallback"
    assert result["control"]["fallback_triggered"] is True
    assert result["control"]["final_executor"] == "react_fallback"
    assert result["search_hits"][0]["title"] == "existing"
    assert result["control"]["evidence_source_types_used"] == ["web"]


def test_postcheck_rules_detect_missing_time_and_unsupported_numbers():
    verdict = screen_search_answer(
        query="过去三年英伟达营收变化是什么？",
        answer="英伟达营收是 9999 亿美元。",
        search_hits=[{"title": "NVIDIA revenue", "url": "https://example.com", "snippet": "Revenue was 609亿美元 in 2024."}],
        retrieved_docs=[],
        time_constraint=parse_time_constraint("过去三年英伟达营收变化是什么？"),
    )

    assert verdict.passes_postcheck is False
    assert "missing_time_constraint" in verdict.failure_types
    assert "unsupported_specific_detail" in verdict.failure_types
    assert verdict.should_fallback_to_react is True


def test_merge_judge_verdict_disables_fallback_for_nonrecoverable_failures():
    verdict = screen_search_answer(
        query="今天的比赛比分是多少？",
        answer="未在搜索结果和本地文档中找到具体数据。",
        search_hits=[],
        retrieved_docs=[],
        time_constraint=parse_time_constraint("今天的比赛比分是多少？"),
    )
    merged = merge_judge_verdict(
        verdict,
        {
            "passes_postcheck": False,
            "should_fallback_to_react": True,
            "recoverable": True,
            "failure_types": ["acknowledged_insufficient_information"],
            "missing_constraints": [],
            "evidence_sufficiency": "insufficient",
            "reason": "judge_confirmed_nonrecoverable",
        },
    )

    assert merged.should_fallback_to_react is False
    assert merged.recoverable is False


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
            "domain_result": {"answer": "Weather is sunny."},
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


class StubSelectorNoDomain:
    def select_sources(self, query: str, timing_recorder: Any = None):
        return "general", []

    def generate_domain_specific_query(self, query: str, domain: str):
        return query

    def fetch_domain_data(self, query: str, domain: str, timing_recorder: Any = None):
        return None


def test_langchain_orchestrator_triggers_react_fallback(monkeypatch):
    monkeypatch.setattr(LangChainOrchestrator, "_build_decision_chain", lambda self: None)
    monkeypatch.setattr(LangChainOrchestrator, "_build_keyword_chain", lambda self: None)
    orchestrator = LangChainOrchestrator(
        llm=StubLLM('{"passes_postcheck": false, "should_fallback_to_react": true, "recoverable": true, "failure_types": ["unsupported_specific_detail"], "missing_constraints": ["time_constraint"], "evidence_sufficiency": "insufficient", "reason": "needs_more_grounding"}'),
        routing_llm=StubRoutingLLM('{"needs_search": true, "reason": "search_required"}'),
        classifier_llm=StubLLM(""),
        postcheck_llm=StubLLM('{"passes_postcheck": false, "should_fallback_to_react": true, "recoverable": true, "failure_types": ["unsupported_specific_detail"], "missing_constraints": ["time_constraint"], "evidence_sufficiency": "insufficient", "reason": "needs_more_grounding"}'),
        search_client=StubSearchClient([]),
        source_selector=StubSelectorNoDomain(),
        config={
            "postcheck": {
                "enabled": True,
                "react_fallback": {"enabled": True, "max_iterations": 2},
                "judge": {"enabled": True},
            }
        },
    )

    monkeypatch.setattr(
        orchestrator,
        "_run_primary_rag",
        lambda **kwargs: StubPipeline().answer(kwargs["query"]),
    )
    monkeypatch.setattr(
        orchestrator,
        "_make_routing_decision",
        lambda query, timing_recorder=None: {"needs_search": True, "reason": "search_required"},
    )
    monkeypatch.setattr(orchestrator, "_generate_keywords", lambda query, timing_recorder=None: {"keywords": [query]})
    monkeypatch.setattr(orchestrator, "_get_react_fallback_orchestrator", lambda: StubFallbackOrchestrator())

    result = orchestrator.answer("过去三年英伟达营收变化是什么？")

    assert result["answer"] == "Recovered answer"
    assert result["control"]["fallback_triggered"] is True
    assert result["control"]["final_executor"] == "react_fallback"
    assert result["control"]["postcheck"]["should_fallback_to_react"] is True
    assert result["control"]["evidence_source_types_used"] == ["web"]


def test_langchain_orchestrator_local_only_uses_primary_pipeline(monkeypatch):
    monkeypatch.setattr(LangChainOrchestrator, "_build_decision_chain", lambda self: None)
    monkeypatch.setattr(LangChainOrchestrator, "_build_keyword_chain", lambda self: None)
    orchestrator = LangChainOrchestrator(
        llm=StubLLM("local answer"),
        routing_llm=StubRoutingLLM(""),
        classifier_llm=StubLLM(""),
        source_selector=StubSelectorNoDomain(),
        data_path="/tmp",
    )

    monkeypatch.setattr(orchestrator, "_snapshot_local_docs", lambda: (("doc.md", 1.0),))
    monkeypatch.setattr(
        orchestrator,
        "_run_primary_rag",
        lambda **kwargs: {
            "query": kwargs["query"],
            "answer": "local answer",
            "search_hits": [],
            "retrieved_docs": [],
            "evidence_items": [],
            "evidence_summary": "",
            "evidence_sources_active": [{"source_type": "local", "source_id": "/tmp"}],
            "evidence_sources_used": [],
            "evidence_source_types_active": ["local"],
            "evidence_source_types_used": [],
        },
    )

    result = orchestrator.answer("Explain this document", allow_search=False)

    assert result["answer"] == "local answer"
    assert result["control"]["search_mode"] == "local_rag"
    assert result["control"]["final_executor"] == "default_pipeline"
    assert result["control"]["evidence_source_types_active"] == ["local"]


def test_langchain_orchestrator_search_unavailable_uses_default_pipeline_metadata(monkeypatch):
    monkeypatch.setattr(LangChainOrchestrator, "_build_decision_chain", lambda self: None)
    monkeypatch.setattr(LangChainOrchestrator, "_build_keyword_chain", lambda self: None)
    orchestrator = LangChainOrchestrator(
        llm=StubLLM("fallback answer"),
        routing_llm=StubRoutingLLM(""),
        classifier_llm=StubLLM(""),
        source_selector=StubSelectorNoDomain(),
        search_client=None,
    )

    monkeypatch.setattr(orchestrator, "_snapshot_local_docs", lambda: None)
    monkeypatch.setattr(
        orchestrator,
        "_make_routing_decision",
        lambda query, timing_recorder=None: {"needs_search": True, "reason": "search_required"},
    )
    monkeypatch.setattr(orchestrator, "_generate_keywords", lambda query, timing_recorder=None: {"keywords": [query]})
    monkeypatch.setattr(
        orchestrator,
        "_run_primary_rag",
        lambda **kwargs: {
            "query": kwargs["query"],
            "answer": "fallback answer",
            "search_hits": [],
            "retrieved_docs": [],
            "evidence_items": [],
            "evidence_summary": "",
            "evidence_sources_active": [],
            "evidence_sources_used": [],
            "evidence_source_types_active": [],
            "evidence_source_types_used": [],
        },
    )

    result = orchestrator.answer("What happened today?")

    assert result["answer"] == "fallback answer"
    assert result["control"]["search_mode"] == "search_unavailable"
    assert result["control"]["search_allowed"] is True
    assert result["control"]["final_executor"] == "default_pipeline"
    assert result["control"]["evidence_sources_active"] == []


def test_server_build_pipeline_uses_langchain_even_when_react_mode_requested(monkeypatch):
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

    monkeypatch.setattr(server, "load_base_config", lambda: {**base_config, "orchestrator_mode": "react"})
    assert server.build_pipeline() == "default-orchestrator"
    assert len(default_calls) == 1

    monkeypatch.setattr(server, "load_base_config", lambda: {**base_config, "orchestrator_mode": "langchain"})
    assert server.build_pipeline() == "default-orchestrator"
    assert len(default_calls) == 2


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


def test_react_agent_orchestrator_engine_defaults_to_langgraph():
    from orchestrators.react_loop_graph import langgraph_available

    if not langgraph_available():
        pytest.skip("langgraph not installed")
    orchestrator = ReactAgentOrchestrator(llm=object(), tools=[], max_iterations=2)
    assert orchestrator.engine == "langgraph"
    assert orchestrator._agent_executor is None


def test_react_agent_orchestrator_engine_from_config(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("legacy executor must not be created in langgraph mode")

    monkeypatch.setattr(
        "orchestrators.react_agent_orchestrator.LangChainOrchestrator.create_react_agent",
        _boom,
    )
    from orchestrators.react_loop_graph import langgraph_available

    if not langgraph_available():
        pytest.skip("langgraph not installed")
    orchestrator = ReactAgentOrchestrator(
        llm=object(),
        tools=[],
        max_iterations=2,
        config={"reactAgent": {"engine": "langgraph"}},
    )
    assert orchestrator.engine == "langgraph"
    assert orchestrator._agent_executor is None


def test_react_agent_orchestrator_engine_kwarg_overrides_config(monkeypatch):
    monkeypatch.setattr(
        "orchestrators.react_agent_orchestrator.LangChainOrchestrator.create_react_agent",
        lambda **kwargs: object(),
    )
    orchestrator = ReactAgentOrchestrator(
        llm=object(),
        tools=[],
        max_iterations=2,
        config={"reactAgent": {"engine": "langgraph"}},
        engine="legacy",
    )
    assert orchestrator.engine == "legacy"


def test_react_agent_orchestrator_langgraph_missing_package_falls_back(monkeypatch):
    created = []
    monkeypatch.setattr(
        "orchestrators.react_agent_orchestrator.LangChainOrchestrator.create_react_agent",
        lambda **kwargs: created.append(kwargs) or object(),
    )
    monkeypatch.setattr(
        "orchestrators.react_loop_graph.langgraph_available",
        lambda: False,
    )
    orchestrator = ReactAgentOrchestrator(
        llm=object(),
        tools=[],
        max_iterations=2,
        config={"reactAgent": {"engine": "langgraph"}},
    )
    assert orchestrator.engine == "legacy"
    assert created, "legacy executor should be created on fallback"


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
        config={"reactAgent": {"engine": "langgraph"}},
    )
    result = orchestrator.answer("苹果和微软的区别")

    control = result["control"]
    assert control["loop_status"] == "succeeded"
    assert control["engine"] == "langgraph"
    assert isinstance(control["loop_verdicts"], list) and control["loop_verdicts"]
    # 既有字段保持兼容
    assert control["search_mode"] == "react_agent"
    assert control["max_iterations"] == 4
    assert control["fallback_triggered"] is False
    assert "decision" in control
    assert result["answer"]


def test_react_agent_orchestrator_langgraph_fallback_checklist_injection():
    from orchestrators.react_loop_graph import langgraph_available

    if not langgraph_available():
        pytest.skip("langgraph not installed")

    from tests.test_react_loop_graph import NativeScriptedChatModel

    llm = NativeScriptedChatModel(replies=["短答案"] * 3)
    orchestrator = ReactAgentOrchestrator(
        llm=llm,
        tools=[],
        max_iterations=2,
        config={"reactAgent": {"engine": "langgraph"}},
    )
    result = orchestrator.answer(
        "对比苹果和微软",
        fallback_context={
            "previous_answer": "旧答案",
            "failure_types": ["missing_comparison_coverage"],
            "missing_constraints": ["comparison"],
            "recovery_goal": "补足对比覆盖",
            "search_hits": [],
        },
    )
    control = result["control"]
    assert control["search_mode"] == "react_fallback"
    assert control["loop_status"] == "exhausted"
    reasons = [v["reason"] for v in control["loop_verdicts"]]
    assert "final_answer_rejected" in reasons
