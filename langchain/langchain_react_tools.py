"""ReAct tools for LangChain agents.

This module wraps existing search, domain API, local doc, and high-level search
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
from pydantic import BaseModel, Field

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence import (
    DomainEvidenceSource,
    LocalEvidenceSource,
    RetrievalOptions,
    build_evidence_summary,
    source_identity_label,
)
from langchain.langchain_rag import SearchRAGChain
from search.search import SearchClient, SearchHit
from search.source_selector import IntelligentSourceSelector
from utils.timing_utils import TimingRecorder


class WebSearchInput(BaseModel):
    """Input schema for web search tool."""
    query: str = Field(description="The search query to execute")


class SearchRecoveryInput(BaseModel):
    """Input schema for high-level search recovery tool."""
    query: str = Field(description="The search recovery query to execute")


class DomainApiInput(BaseModel):
    """Input schema for domain API tool."""
    query: str = Field(description="The domain query to execute")


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

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, search_client: SearchClient, **kwargs: Any) -> None:
        super().__init__(search_client=search_client, **kwargs)

    def _run(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Execute the search and return formatted results."""
        try:
            hits = self.search_client.search(query, num_results=5)
            return self._format_results(hits)
        except Exception as exc:
            return f"Search failed: {exc}"

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
    source_selector: Optional[Any] = Field(default=None, exclude=True)

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
        source_selector: Optional[IntelligentSourceSelector] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            llm=llm,
            search_client=search_client,
            data_path=data_path,
            reranker=reranker,
            min_rerank_score=min_rerank_score,
            max_per_domain=max_per_domain,
            source_selector=source_selector,
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
                source_selector=self.source_selector,
            )
        return self._rag_chain

    def _run(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
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
                enable_domain=True,
            )
            self._last_payload = result
            raw_calls = result.get("search_api_calls") if isinstance(result, dict) else None
            self._last_search_api_calls = [
                record for record in list(raw_calls or []) if isinstance(record, dict)
            ]
            return self._format_payload(result)
        except Exception as exc:
            self._last_search_api_calls = []
            return f"Search recovery failed: {exc}"

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


class ReActDomainTool(BaseTool):
    """LangChain Tool wrapping IntelligentSourceSelector for ReAct agents."""

    name: str = "domain_api"
    description: str = (
        "Get professional domain-specific data including weather, finance, sports, and transportation. "
        "Input should be a domain query string. "
        "Returns structured data or natural language answer from specialized APIs."
    )
    args_schema: Type[BaseModel] = DomainApiInput
    return_direct: bool = False

    source_selector: Any = Field(exclude=True)
    llm: Optional[BaseChatModel] = Field(default=None, exclude=True)

    class Config:
        arbitrary_types_allowed = True

    def __init__(
        self,
        source_selector: IntelligentSourceSelector,
        llm: Optional[BaseChatModel] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(source_selector=source_selector, **kwargs)
        self.llm = llm

    def _run(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Execute domain API query and return formatted results."""
        try:
            evidence_source = DomainEvidenceSource(self.source_selector)
            evidence_items = evidence_source.retrieve(
                query,
                RetrievalOptions(timing_recorder=TimingRecorder(enabled=False)),
            )
            if not evidence_items:
                return "No domain-specific data found for this query."

            item = evidence_items[0]
            domain = item.metadata.get("domain") or "domain"
            answer = item.content
            data = item.metadata.get("data")
            if data and self.llm and not item.metadata.get("continue_search"):
                enhanced = self._enhance_answer(query, domain, data)
                if enhanced:
                    answer = enhanced

            lines = [answer]
            lines.append(f"Evidence Source: {source_identity_label(item.source_type, item.source_id)}")
            lines.append(f"Reference: {item.reference}")
            return "\n\n".join(line for line in lines if line)

        except Exception as exc:
            return f"Domain API error: {exc}"

    def _enhance_answer(
        self,
        query: str,
        domain: str,
        data: Any,
    ) -> Optional[str]:
        """Enhance domain answer with LLM."""
        if not self.llm:
            return None

        from langchain_core.messages import HumanMessage, SystemMessage

        prompts = {}

        if domain == "weather" and isinstance(data, dict):
            prompts["weather"] = (
                "你是天气助手。根据实时数据，给出自然、丰富的回复，包括当前状况、穿衣/出行建议。",
                f"位置：{data.get('location', {}).get('formatted_address', '未知')}\n"
                f"概况：{data.get('weatherCondition', {}).get('description', {}).get('text', '未知')}\n"
                f"温度：{data.get('temperature', {}).get('degrees', '未知')}°C"
            )
        elif domain == "finance":
            prompts["finance"] = (
                "你是金融助手。根据提供的股票数据，分析价格走势和表现。如果包含多只股票，请进行对比。",
                json.dumps(data, ensure_ascii=False, indent=2)
            )

        if domain not in prompts:
            return None

        system_prompt, data_summary = prompts[domain]
        user_prompt = f"查询：{query}\n数据：\n{data_summary}\n生成回复："

        try:
            from langchain_core.language_models.chat_models import BaseChatModel
            if isinstance(self.llm, BaseChatModel):
                response = self.llm.invoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ])
                content = response.content if hasattr(response, 'content') else str(response)
                return content
        except Exception:
            pass

        return None


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

    class Config:
        arbitrary_types_allowed = True

    def __init__(
        self,
        data_path: str,
        llm: Optional[BaseChatModel] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(data_path=data_path, **kwargs)
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
        if not self.data_path:
            return "Local knowledge base is not available (no data path configured)."

        source = self._get_source()
        if not source:
            return "Local knowledge base is not available (no data path configured)."

        try:
            evidence_items = source.retrieve(query, RetrievalOptions(num_results=3))
            if not evidence_items:
                return "No relevant documents found."

            lines = ["Relevant Local Evidence:"]
            for idx, item in enumerate(evidence_items, 1):
                lines.append(f"{idx}. {item.title}\n   {item.snippet}")
            lines.append("")
            lines.append("Unified Evidence:")
            lines.append(build_evidence_summary(evidence_items))
            return "\n".join(lines)

        except Exception as exc:
            return f"Local documents query failed: {exc}"


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

    source_selector = None
    try:
        source_selector = IntelligentSourceSelector(
            llm_client=None,  # We'll use the provided llm for enhancement
            use_llm=False,
            google_api_key=config.get("googleSearch", {}).get("api_key") or config.get("GOOGLE_API_KEY"),
            finnhub_api_key=config.get("FINNHUB_API_KEY"),
            sportsdb_api_key=config.get("SPORTSDB_API_KEY"),
            apisports_api_key=config.get("APISPORTS_KEY"),
            config=config,
        )
    except Exception:
        source_selector = None

    # Create search tools if search client is available
    if search_client:
        tools.append(ReActSearchTool(search_client=search_client))
        if llm:
            tools.append(
                ReActSearchRecoveryTool(
                    llm=llm,
                    search_client=search_client,
                    data_path=data_path,
                    reranker=reranker,
                    min_rerank_score=min_rerank_score,
                    max_per_domain=max_per_domain,
                    source_selector=source_selector,
                )
            )

    # Create domain API tool
    try:
        if source_selector is None:
            raise ValueError("source selector unavailable")
        tools.append(ReActDomainTool(
            source_selector=source_selector,
            llm=llm,
        ))
    except Exception:
        pass  # Domain API tool is optional

    # Create local docs tool if data path is available
    if data_path and os.path.isdir(data_path):
        tools.append(ReActLocalDocTool(
            data_path=data_path,
            llm=llm,
        ))

    return tools
