"""ReAct tools for LangChain agents.

This module wraps registered skills, web search, local docs, and high-level search
recovery tools as LangChain BaseTool implementations suitable for use with
ReAct agents.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Type

from langchain_core.callbacks import CallbackManagerForToolRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence import (
    LocalEvidenceSource,
    RetrievalOptions,
    WebEvidenceSource,
    build_evidence_summary,
    source_identity_label,
)
from evidence.official_domain_resolver import build_official_domain_resolver
from langchain.langchain_rag import SearchRAGChain
from search.search import SearchClient, SearchHit
from skills import SkillRegistry
from utils.query_orchestration import QueryAnalysis
from utils.timing_utils import TimingRecorder


class WebSearchInput(BaseModel):
    """Input schema for web search tool."""
    query: str = Field(description="The search query to execute")


class SearchRecoveryInput(BaseModel):
    """Input schema for high-level search recovery tool."""
    query: str = Field(description="The search recovery query to execute")


class SkillQueryInput(BaseModel):
    """Common input shape for the first generation of registered skills."""

    query: str = Field(description="The complete user query to validate and execute")


class LocalDocInput(BaseModel):
    """Input schema for local document tool."""
    query: str = Field(description="The query for local documents")


class ReActSearchTool(BaseTool):
    """LangChain Tool wrapping search_client.search() for ReAct agents."""

    name: str = "web_search"
    description: str = (
        "Search the web for current information. "
        "Input should be a search query string. "
        "Returns a list of search results with titles, URLs, and snippets."
    )
    args_schema: Type[BaseModel] = WebSearchInput
    return_direct: bool = False

    search_client: SearchClient = Field(exclude=True)
    app_config: Dict[str, Any] = Field(default_factory=dict, exclude=True)
    max_calls_per_query: int = Field(default=3, exclude=True)
    _calls_in_run: int = PrivateAttr(default=0)
    _analysis: Optional[QueryAnalysis] = PrivateAttr(default=None)
    _web_source: Optional[WebEvidenceSource] = PrivateAttr(default=None)
    _last_evidence_records: List[Dict[str, Any]] = PrivateAttr(default_factory=list)

    class Config:
        arbitrary_types_allowed = True

    def __init__(
        self,
        search_client: SearchClient,
        *,
        config: Optional[Dict[str, Any]] = None,
        max_calls_per_query: int = 3,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            search_client=search_client,
            app_config=config or {},
            max_calls_per_query=max(1, int(max_calls_per_query)),
            **kwargs,
        )
        orchestration = self.app_config.get("orchestration") or {}
        if not isinstance(orchestration, dict):
            orchestration = {}
        clients = list(
            getattr(search_client, "clients", None) or [search_client]
        )
        resolver = build_official_domain_resolver(
            orchestration,
            search_clients=clients,
        )
        self._web_source = WebEvidenceSource(
            search_client,
            official_domains=orchestration.get("official_domains"),
            official_resolver=resolver,
        )

    def _run(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Execute the search and return formatted results."""
        self._last_evidence_records = []
        if self._calls_in_run >= self.max_calls_per_query:
            return json.dumps(
                {
                    "status": "budget_exhausted",
                    "reason": "max_calls_per_query",
                    "limit": self.max_calls_per_query,
                },
                ensure_ascii=False,
            )
        self._calls_in_run += 1
        try:
            entities = list(
                dict.fromkeys(
                    list(getattr(self._analysis, "comparison_members", None) or [])
                    + list(getattr(self._analysis, "entities", None) or [])
                )
            )
            source = self._web_source or WebEvidenceSource(self.search_client)
            items = source.retrieve(
                query,
                RetrievalOptions(
                    num_results=5,
                    metadata={"source_tier_entities": entities},
                ),
            )
            self._last_evidence_records = [
                {
                    "source_type": "web",
                    "source_tier": str((item.metadata or {}).get("source_tier") or "unknown"),
                    "reference": str(item.reference or ""),
                    "title": str(item.title or ""),
                    "content": str(item.snippet or item.content or ""),
                    "metadata": dict(getattr(item, "metadata", None) or {}),
                }
                for item in items
                if not (item.metadata or {}).get("exclude_from_evidence")
            ]
            hits = [
                SearchHit(
                    title=str(item.title or ""),
                    url=str(item.reference or ""),
                    snippet=str(item.snippet or item.content or ""),
                )
                for item in items
                if not (item.metadata or {}).get("exclude_from_evidence")
            ]
            return self._format_results(hits)
        except Exception as exc:
            return f"Search failed: {exc}"

    def reset_budget(self) -> None:
        self._calls_in_run = 0
        self._last_evidence_records = []

    def set_analysis(self, analysis: Optional[QueryAnalysis]) -> None:
        self._analysis = analysis

    def get_last_evidence_records(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._last_evidence_records]

    def get_budget_status(self) -> Dict[str, int]:
        return {"limit": self.max_calls_per_query, "used": self._calls_in_run}

    def get_last_search_api_calls(self) -> List[Dict[str, Any]]:
        """Expose the concrete provider calls for the workflow audit only."""
        getter = getattr(self.search_client, "get_last_call_records", None)
        if not callable(getter):
            return []
        return [record for record in list(getter() or []) if isinstance(record, dict)]

    def _format_results(self, hits: List[SearchHit]) -> str:
        """Format search results as a readable string."""
        if not hits:
            return "No search results found."

        results = []
        for i, hit in enumerate(hits, 1):
            result = f"{i}. {hit.title or 'Untitled'}\n"
            result += f"   URL: {hit.url or 'N/A'}\n"
            result += f"   {hit.snippet or 'No description available.'}"
            results.append(result)

        return "\n\n".join(results)


class ReActSearchRecoveryTool(BaseTool):
    """High-level SearchRAG recovery tool for ReAct fallback."""

    name: str = "search_recovery"
    description: str = (
        "Run a high-level recovery search that reuses the default search RAG pipeline, "
        "including search, reranking, optional local document retrieval, and answer synthesis. "
        "Use this when the current answer is missing evidence, missing constraints, or needs a better synthesis."
    )
    args_schema: Type[BaseModel] = SearchRecoveryInput
    return_direct: bool = False

    llm: Any = Field(exclude=True)
    search_client: SearchClient = Field(exclude=True)
    data_path: Optional[str] = Field(default=None, exclude=True)
    reranker: Optional[Any] = Field(default=None, exclude=True)
    min_rerank_score: float = Field(default=0.0, exclude=True)
    max_per_domain: int = Field(default=1, exclude=True)
    app_config: Dict[str, Any] = Field(default_factory=dict, exclude=True)
    max_calls_per_query: int = Field(default=2, exclude=True)
    _calls_in_run: int = PrivateAttr(default=0)
    _analysis: Optional[QueryAnalysis] = PrivateAttr(default=None)
    _last_evidence_records: List[Dict[str, Any]] = PrivateAttr(default_factory=list)

    class Config:
        arbitrary_types_allowed = True

    def __init__(
        self,
        llm: BaseChatModel,
        search_client: SearchClient,
        *,
        data_path: Optional[str] = None,
        reranker: Optional[Any] = None,
        min_rerank_score: float = 0.0,
        max_per_domain: int = 1,
        config: Optional[Dict[str, Any]] = None,
        max_calls_per_query: int = 2,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            llm=llm,
            search_client=search_client,
            data_path=data_path,
            reranker=reranker,
            min_rerank_score=min_rerank_score,
            max_per_domain=max_per_domain,
            app_config=config or {},
            max_calls_per_query=max(1, int(max_calls_per_query)),
            **kwargs,
        )
        self._rag_chain: Optional[SearchRAGChain] = None
        self._last_payload: Optional[Dict[str, Any]] = None
        self._last_search_api_calls: List[Dict[str, Any]] = []

    def _get_chain(self) -> SearchRAGChain:
        if self._rag_chain is None:
            self._rag_chain = SearchRAGChain(
                llm=self.llm,
                search_client=self.search_client,
                data_path=self.data_path,
                reranker=self.reranker,
                min_rerank_score=self.min_rerank_score,
                max_per_domain=self.max_per_domain,
                config=self.app_config,
            )
        return self._rag_chain

    def _run(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        self._last_evidence_records = []
        if self._calls_in_run >= self.max_calls_per_query:
            return json.dumps(
                {
                    "status": "budget_exhausted",
                    "reason": "max_calls_per_query",
                    "limit": self.max_calls_per_query,
                },
                ensure_ascii=False,
            )
        self._calls_in_run += 1
        try:
            result = self._get_chain().answer(
                query,
                search_query=query,
                num_search_results=5,
                per_source_limit=5,
                num_retrieved_docs=3,
                max_tokens=1200,
                temperature=0.2,
                enable_search=True,
                enable_local_docs=True,
                enable_skill=False,
                analysis=self._analysis,
            )
            self._last_payload = result
            raw_calls = result.get("search_api_calls") if isinstance(result, dict) else None
            self._last_search_api_calls = [
                record for record in list(raw_calls or []) if isinstance(record, dict)
            ]
            self._last_evidence_records = self._evidence_records_from_payload(result)
            return self._format_payload(result)
        except Exception as exc:
            self._last_search_api_calls = []
            return f"Search recovery failed: {exc}"

    def reset_budget(self) -> None:
        self._calls_in_run = 0
        self._last_evidence_records = []
        self._last_search_api_calls = []

    def set_analysis(self, analysis: Optional[QueryAnalysis]) -> None:
        self._analysis = analysis

    def get_last_evidence_records(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._last_evidence_records]

    def get_budget_status(self) -> Dict[str, int]:
        return {"limit": self.max_calls_per_query, "used": self._calls_in_run}

    @staticmethod
    def _evidence_records_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for raw in list(payload.get("evidence_items") or []):
            if not isinstance(raw, dict):
                continue
            metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
            records.append(
                {
                    "source_type": str(raw.get("source_type") or "web"),
                    "source_tier": str(metadata.get("source_tier") or "unknown"),
                    "reference": str(raw.get("reference") or ""),
                    "title": str(raw.get("title") or ""),
                    "content": str(raw.get("snippet") or raw.get("content") or ""),
                    "metadata": dict(metadata),
                }
            )
        return records

    def get_last_search_api_calls(self) -> List[Dict[str, Any]]:
        """Return the recovery chain's provider-call audit snapshots."""
        return [dict(record) for record in self._last_search_api_calls]

    def _format_payload(self, payload: Dict[str, Any]) -> str:
        answer = str(payload.get("answer") or "").strip()
        search_hits = payload.get("search_hits") or []
        retrieved_docs = payload.get("retrieved_docs") or []

        parts: List[str] = []
        if answer:
            parts.append(f"Recovered Answer:\n{answer}")

        used_sources = payload.get("evidence_sources_used") or []
        if used_sources:
            labels = [
                source_identity_label(item.get("source_type"), item.get("source_id"))
                for item in used_sources
            ]
            parts.append(f"Evidence Sources Used:\n{', '.join(labels)}")

        if search_hits:
            parts.append("Evidence Summary:")
            for index, hit in enumerate(search_hits[:5], start=1):
                title = hit.get("title") or f"Result {index}"
                url = hit.get("url") or "N/A"
                snippet = hit.get("snippet") or ""
                parts.append(f"{index}. {title}\n   URL: {url}\n   {snippet}")

        if retrieved_docs:
            parts.append("Local Documents:")
            for index, doc in enumerate(retrieved_docs[:3], start=1):
                source = doc.get("source") or f"Document {index}"
                parts.append(f"{index}. {source}")

        evidence_summary = str(payload.get("evidence_summary") or "").strip()
        if evidence_summary:
            parts.append(f"Unified Evidence:\n{evidence_summary}")

        return "\n\n".join(parts) if parts else "Search recovery completed but returned no evidence."


class ReActLocalDocTool(BaseTool):
    """LangChain tool that reuses the unified local evidence source."""

    name: str = "local_docs"
    description: str = (
        "Query the local knowledge base for information from documents. "
        "Input should be a query string. "
        "Returns relevant document snippets."
    )
    args_schema: Type[BaseModel] = LocalDocInput
    return_direct: bool = False

    data_path: str = Field(exclude=True)
    llm: Optional[BaseChatModel] = Field(default=None, exclude=True)
    max_calls_per_query: int = Field(default=2, exclude=True)
    _calls_in_run: int = PrivateAttr(default=0)
    _last_evidence_records: List[Dict[str, Any]] = PrivateAttr(default_factory=list)

    class Config:
        arbitrary_types_allowed = True

    def __init__(
        self,
        data_path: str,
        llm: Optional[BaseChatModel] = None,
        max_calls_per_query: int = 2,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            data_path=data_path,
            max_calls_per_query=max(1, int(max_calls_per_query)),
            **kwargs,
        )
        self.llm = llm
        self._source = LocalEvidenceSource(data_path=data_path)

    def _get_source(self) -> Optional[LocalEvidenceSource]:
        """Return the configured local evidence source when documents exist."""
        if not self.data_path or not os.path.isdir(self.data_path):
            return None
        return self._source

    def _run(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Query local documents and return formatted results."""
        self._last_evidence_records = []
        if self._calls_in_run >= self.max_calls_per_query:
            return json.dumps(
                {
                    "status": "budget_exhausted",
                    "reason": "max_calls_per_query",
                    "limit": self.max_calls_per_query,
                },
                ensure_ascii=False,
            )
        self._calls_in_run += 1
        if not self.data_path:
            return "Local knowledge base is not available (no data path configured)."

        source = self._get_source()
        if not source:
            return "Local knowledge base is not available (no data path configured)."

        try:
            evidence_items = source.retrieve(query, RetrievalOptions(num_results=3))
            if not evidence_items:
                return "No relevant documents found."

            self._last_evidence_records = [
                {
                    "source_type": "local",
                    "source_tier": "local",
                    "reference": str(item.reference or ""),
                    "title": str(item.title or ""),
                    "content": str(item.snippet or item.content or ""),
                    "metadata": dict(getattr(item, "metadata", None) or {}),
                }
                for item in evidence_items
            ]

            lines = ["Relevant Local Evidence:"]
            for idx, item in enumerate(evidence_items, 1):
                lines.append(f"{idx}. {item.title}\n   {item.snippet}")
            lines.append("")
            lines.append("Unified Evidence:")
            lines.append(build_evidence_summary(evidence_items))
            return "\n".join(lines)

        except Exception as exc:
            return f"Local documents query failed: {exc}"

    def reset_budget(self) -> None:
        self._calls_in_run = 0
        self._last_evidence_records = []

    def get_last_evidence_records(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._last_evidence_records]

    def get_budget_status(self) -> Dict[str, int]:
        return {"limit": self.max_calls_per_query, "used": self._calls_in_run}


class ReActSkillTool(BaseTool):
    """LangChain adapter for one registry-managed skill handler."""

    name: str
    description: str
    args_schema: Type[BaseModel] = SkillQueryInput
    return_direct: bool = False

    skill_handler: Any = Field(exclude=True)
    _calls_in_run: int = PrivateAttr(default=0)
    _timing_recorder: Optional[TimingRecorder] = PrivateAttr(default=None)
    _last_evidence_records: List[Dict[str, Any]] = PrivateAttr(default_factory=list)

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, skill_handler: Any, **kwargs: Any) -> None:
        super().__init__(
            name=skill_handler.manifest.tool_name,
            description=skill_handler.manifest.description,
            skill_handler=skill_handler,
            **kwargs,
        )

    def _run(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        self._last_evidence_records = []
        call_limit = self.skill_handler.manifest.budget["max_calls_per_query"]
        if self._calls_in_run >= call_limit:
            return json.dumps(
                {
                    "status": "budget_exhausted",
                    "reason": "max_calls_per_query",
                    "limit": call_limit,
                },
                ensure_ascii=False,
            )
        self._calls_in_run += 1
        preflight = self.skill_handler.preflight({"query": query})
        if not preflight.accepted:
            return json.dumps(
                {
                    "status": "rejected",
                    "reason": preflight.reason,
                    "instruction": "Correct the arguments only from explicit user input, otherwise use general search.",
                },
                ensure_ascii=False,
            )
        items = self.skill_handler.run(
            preflight.normalized_args,
            RetrievalOptions(
                timing_recorder=self._timing_recorder or TimingRecorder(enabled=False)
            ),
        )
        if not items:
            return json.dumps(
                {
                    "status": "no_data",
                    "reason": "all_configured_providers_failed",
                    "instruction": "Use general web search instead.",
                },
                ensure_ascii=False,
            )
        self._last_evidence_records = [
            {
                "source_type": str(item.source_type or "domain"),
                "source_tier": str((item.metadata or {}).get("source_tier") or "authoritative"),
                "reference": str(item.reference or ""),
                "title": str(item.title or self.name),
                "content": str(item.snippet or item.content or ""),
                "metadata": dict(getattr(item, "metadata", None) or {}),
            }
            for item in items
        ]
        blocks = []
        for item in items:
            blocks.append(
                "\n".join(
                    [
                        item.content,
                        f"Evidence Source: {source_identity_label(item.source_type, item.source_id)}",
                        f"Reference: {item.reference}",
                    ]
                )
            )
        return "\n\n".join(blocks)

    def reset_budget(self) -> None:
        self._calls_in_run = 0
        self._last_evidence_records = []

    def get_last_evidence_records(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._last_evidence_records]

    def get_budget_status(self) -> Dict[str, int]:
        return {
            "limit": int(self.skill_handler.manifest.budget["max_calls_per_query"]),
            "used": self._calls_in_run,
        }

    def set_timing_recorder(self, recorder: Optional[TimingRecorder]) -> None:
        """Bind the request recorder so provider calls remain observable in loop mode."""
        self._timing_recorder = recorder


def create_react_tools_from_config(
    config: Dict[str, Any],
    llm: Optional[BaseChatModel] = None,
    search_client: Optional[SearchClient] = None,
    data_path: Optional[str] = None,
) -> List[BaseTool]:
    """Create ReAct tools from configuration.

    Args:
        config: Configuration dictionary
        llm: Optional LangChain chat model for domain API enhancement
        search_client: Search client for web search
        data_path: Optional path to local documents

    Returns:
        List of LangChain BaseTool instances
    """
    tools: List[BaseTool] = []
    skill_registry = SkillRegistry.from_config(config)
    tool_budgets = (config.get("termination") or {}).get("tool_budgets") or {}
    if not isinstance(tool_budgets, dict):
        tool_budgets = {}

    def tool_budget(name: str, default: int) -> int:
        raw = tool_budgets.get(name, default)
        if isinstance(raw, dict):
            raw = raw.get("max_calls_per_query", default)
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return default

    reranker = None
    rerank_cfg = config.get("rerank") or {}
    min_rerank_score = float(rerank_cfg.get("min_score", 0.0))
    max_per_domain = max(1, int(rerank_cfg.get("max_per_domain", 1)))

    if llm and rerank_cfg and (config.get("RERANK_PROVIDER") or rerank_cfg.get("provider")):
        try:
            from langchain.langchain_rerank import create_search_reranker

            reranker = create_search_reranker(config=config, min_score=min_rerank_score)
        except Exception:
            reranker = None

    # Create search tools if search client is available
    if search_client:
        tools.append(
            ReActSearchTool(
                search_client=search_client,
                config=config,
                max_calls_per_query=tool_budget("web_search", 3),
            )
        )
        if llm:
            tools.append(
                ReActSearchRecoveryTool(
                    llm=llm,
                    search_client=search_client,
                    data_path=data_path,
                    reranker=reranker,
                    min_rerank_score=min_rerank_score,
                    max_per_domain=max_per_domain,
                    config=config,
                    max_calls_per_query=tool_budget("search_recovery", 2),
                )
            )

    # The registry is the only source of skill tools. Availability is already
    # resolved before this point, so unavailable providers never enter the
    # model's tool surface.
    for skill_handler in skill_registry.active_skills():
        tools.append(ReActSkillTool(skill_handler=skill_handler))

    # Create local docs tool if data path is available
    if data_path and os.path.isdir(data_path):
        tools.append(ReActLocalDocTool(
            data_path=data_path,
            llm=llm,
            max_calls_per_query=tool_budget("local_docs", 2),
        ))

    return tools
