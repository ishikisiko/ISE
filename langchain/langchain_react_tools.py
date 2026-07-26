"""ReAct tools for LangChain agents.

This module wraps registered skills, web search, local docs, and high-level search
recovery tools as LangChain BaseTool implementations suitable for use with
ReAct agents.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Mapping, Optional, Type

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
    classify_source,
    normalize_entity_stem,
    provisional_entity_for_url,
    source_identity_label,
)
from evidence.official_domain_resolver import build_official_domain_resolver
from langchain.langchain_rag import SearchRAGChain
from search.search import SearchClient, SearchHit
from skills import SkillRegistry
from utils.query_orchestration import QueryAnalysis, canonical_reference
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


class FetchUrlInput(BaseModel):
    """Input schema for the URL fetch tool."""

    url: str = Field(description="The absolute http(s) URL to fetch and read")
    objective: str = Field(
        default="",
        description="Optional context: the user question or what to look for on the page",
    )


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
    _ledger: Any = PrivateAttr(default=None)
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
            ledger = self._get_ledger()
            entries = []
            for record in self._last_evidence_records:
                eid = ledger.register(record)
                record.setdefault("metadata", {})["eid"] = eid
                entries.append((eid, record))
            return ledger.render_entries(entries) if entries else "No search results found."
        except Exception as exc:
            return f"Search failed: {exc}"

    def reset_budget(self) -> None:
        self._calls_in_run = 0
        self._last_evidence_records = []

    def set_analysis(self, analysis: Optional[QueryAnalysis]) -> None:
        self._analysis = analysis

    def set_ledger(self, ledger: Any) -> None:
        self._ledger = ledger

    def _get_ledger(self) -> Any:
        if self._ledger is None:
            from evidence.ledger import EvidenceLedger

            self._ledger = EvidenceLedger()
        return self._ledger

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


class ReActFetchUrlTool(BaseTool):
    """LangChain Tool that fetches and reads the main text of a given URL.

    Unlike ``web_search`` (which returns shallow snippets), this tool extracts
    the full page content so the agent can ground answers in primary
    documentation such as an official API reference. Extraction reuses the
    shared :class:`ReferenceExtractorRouter`: a zero-key direct HTTP fetch is
    tried first, then configured extraction APIs (Firecrawl/Tavily/Parallel)
    act as fallback for JS-heavy pages.
    """

    name: str = "fetch_url"
    description: str = (
        "Fetch and read the main text content of a specific web page URL. "
        "Use this when web_search snippets are too shallow to answer the "
        "question, for example to read an official documentation page, an API "
        "reference, a spec, or a changelog whose URL you already found. "
        "Input is a URL and optionally an objective describing what you need. "
        "Returns the extracted page text (truncated)."
    )
    args_schema: Type[BaseModel] = FetchUrlInput
    return_direct: bool = False

    app_config: Dict[str, Any] = Field(default_factory=dict, exclude=True)
    max_calls_per_query: int = Field(default=3, exclude=True)
    max_chars: int = Field(default=8000, exclude=True)
    min_content_chars: int = Field(default=600, exclude=True)
    _calls_in_run: int = PrivateAttr(default=0)
    _seen_urls: set[str] = PrivateAttr(default_factory=set)
    _analysis: Optional[QueryAnalysis] = PrivateAttr(default=None)
    _official_domains: Dict[str, Any] = PrivateAttr(default_factory=dict)
    _official_resolver: Any = PrivateAttr(default=None)
    _router: Any = PrivateAttr(default=None)
    _ledger: Any = PrivateAttr(default=None)
    _last_evidence_records: List[Dict[str, Any]] = PrivateAttr(default_factory=list)
    _last_fetch_outcomes: List[Dict[str, Any]] = PrivateAttr(default_factory=list)

    class Config:
        arbitrary_types_allowed = True

    def __init__(
        self,
        *,
        config: Optional[Dict[str, Any]] = None,
        max_calls_per_query: int = 3,
        max_chars: Optional[int] = None,
        min_content_chars: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        app_config = config or {}
        orch = app_config.get("orchestration") or {}
        fetch_cfg = orch.get("fetch_url") if isinstance(orch, dict) else None
        if max_chars is None:
            if isinstance(fetch_cfg, dict):
                try:
                    max_chars = int(fetch_cfg.get("max_chars") or 0) or None
                except (TypeError, ValueError):
                    max_chars = None
        if min_content_chars is None and isinstance(fetch_cfg, dict):
            try:
                min_content_chars = int(
                    fetch_cfg.get("min_content_chars") or 0
                ) or None
            except (TypeError, ValueError):
                min_content_chars = None
        super().__init__(
            app_config=app_config,
            max_calls_per_query=max(1, int(max_calls_per_query)),
            max_chars=max(200, int(max_chars or 8000)),
            min_content_chars=max(1, int(min_content_chars or 600)),
            **kwargs,
        )
        orchestration = self.app_config.get("orchestration") or {}
        if not isinstance(orchestration, dict):
            orchestration = {}
        official_domains = orchestration.get("official_domains") or {}
        self._official_domains = (
            dict(official_domains) if isinstance(official_domains, dict) else {}
        )
        self._official_resolver = build_official_domain_resolver(orchestration)

    def _get_router(self) -> Any:
        """Build the extraction router lazily from runtime configuration."""
        if self._router is not None:
            return self._router
        from search.reference_fetch import (
            ReferenceExtractorRouter,
            build_reference_extractors,
        )

        extractors = build_reference_extractors(self.app_config or {})
        if not extractors:
            self._router = None
            return None
        self._router = ReferenceExtractorRouter(
            extractors,
            min_content_chars=self.min_content_chars,
        )
        return self._router

    def _run(
        self,
        url: str,
        objective: str = "",
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Fetch ``url`` and return its main text, truncated to ``max_chars``."""
        self._last_evidence_records = []
        self._last_fetch_outcomes = []
        if self._calls_in_run >= self.max_calls_per_query:
            return json.dumps(
                {
                    "status": "budget_exhausted",
                    "reason": "max_calls_per_query",
                    "limit": self.max_calls_per_query,
                },
                ensure_ascii=False,
            )

        raw_url = str(url or "").strip()
        objective = str(objective or "").strip()
        if not raw_url:
            return "Fetch failed: no URL provided."
        if not (raw_url.startswith("http://") or raw_url.startswith("https://")):
            return (
                f"Fetch failed: URL must start with http:// or https:// "
                f"(got: {raw_url[:80]})."
            )

        router = self._get_router()
        if router is None:
            # Do not burn budget on a misconfiguration; let the caller retry.
            return (
                "Fetch failed: no page extractor is configured. "
                "Enable orchestration.directFetch to use this tool."
            )

        url_key = canonical_reference(raw_url) or raw_url
        if url_key in self._seen_urls:
            exhausted_getter = getattr(router, "is_url_exhausted", None)
            exhausted = bool(exhausted_getter(raw_url)) if callable(exhausted_getter) else False
            outcome = {
                "url": url_key,
                "status": "no_data" if exhausted else "rejected",
                "chars": 0,
                "error_type": "url_exhausted" if exhausted else "duplicate_url",
                "exhausted": exhausted,
            }
            self._last_fetch_outcomes.append(outcome)
            payload = {
                "status": outcome["status"],
                "reason": outcome["error_type"],
                "url": url_key,
            }
            if exhausted:
                payload["exhausted"] = True
            return json.dumps(payload, ensure_ascii=False)
        self._seen_urls.add(url_key)

        self._calls_in_run += 1
        started_at = time.perf_counter()
        numeric_requirements = getattr(self._analysis, "numeric_requirements", None)
        numeric_requirements = (
            numeric_requirements
            if isinstance(numeric_requirements, Mapping)
            and numeric_requirements.get("operation") == "pricing_total"
            else None
        )
        accept_content = None
        if numeric_requirements:
            from evidence.pricing_claims import (
                pricing_content_acceptance,
                pricing_reference_matches,
            )

            def accept_content(content: str) -> Any:
                if not pricing_reference_matches(numeric_requirements, raw_url):
                    return False, "missing:requested_channel"
                return pricing_content_acceptance(content, numeric_requirements)
        try:
            extract_kwargs: Dict[str, Any] = {"objective": objective or None}
            if accept_content is not None:
                extract_kwargs["accept_content"] = accept_content
            extraction = router.extract([raw_url], **extract_kwargs)
        except Exception as exc:  # noqa: BLE001 - surface fetch errors to the agent
            self._last_fetch_outcomes.append(
                {
                    "url": url_key,
                    "status": "no_data",
                    "chars": 0,
                    "error_type": "extractor_exception",
                    "reason": str(exc)[:200],
                }
            )
            return f"Fetch failed: {exc}"

        content_obj = next(
            (
                item
                for item in extraction.contents
                if len((item.content or "").strip()) >= self.min_content_chars
            ),
            None,
        )
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)

        if content_obj is None:
            failure = next(
                (
                    item
                    for item in extraction.failures
                    if getattr(item, "error_type", "") == "objective_incomplete"
                ),
                extraction.failures[0] if extraction.failures else None,
            )
            error_type = str(
                getattr(failure, "error_type", "") or "no_content"
            ) if failure else "no_content"
            exhausted_getter = getattr(router, "is_url_exhausted", None)
            exhausted = bool(exhausted_getter(raw_url)) if callable(exhausted_getter) else False
            observed_chars = max(
                (
                    int(attempt.get("content_chars") or 0)
                    for attempt in extraction.attempts or []
                    if isinstance(attempt, dict)
                ),
                default=0,
            )
            self._last_fetch_outcomes.append(
                {
                    "url": url_key,
                    "status": "no_data",
                    "chars": observed_chars,
                    "error_type": error_type,
                    "exhausted": exhausted,
                    "attempts": list(extraction.attempts or []),
                }
            )
            note = (
                " All configured extractors have failed for this URL; retrying "
                "the same URL will not help. Try a different source instead."
                if exhausted
                else " The page may be blocked, JS-only, or require an "
                "extraction API key."
            )
            return json.dumps(
                {
                    "status": "no_data",
                    "url": url_key,
                    "error_type": error_type,
                    "exhausted": exhausted,
                    "note": note.strip(),
                },
                ensure_ascii=False,
            )

        full_text = (content_obj.content or "").strip()
        title = str(content_obj.title or "").strip()
        provider = str(getattr(content_obj, "provider", "") or "").strip()
        resolved_url = str(content_obj.url or raw_url).strip()
        truncated = len(full_text) > self.max_chars
        page_text = full_text[: self.max_chars]
        if truncated:
            page_text += f"\n... [truncated; {len(full_text)} chars total]"

        entities = list(
            dict.fromkeys(
                list(getattr(self._analysis, "comparison_members", None) or [])
                + list(getattr(self._analysis, "entities", None) or [])
            )
        )
        verdict = classify_source(
            resolved_url,
            entities=entities,
            official_domains=self._official_domains,
            resolver=self._official_resolver,
        )
        source_tier = verdict.tier
        provisional_entity = provisional_entity_for_url(
            resolved_url,
            entities=entities,
            resolver=self._official_resolver,
        )
        metadata = {
            "retrieval_kind": "fetch_url",
            "provider": provider,
            "content_chars": len(full_text),
            "truncated": truncated,
            "duration_ms": duration_ms,
            "retrieved_at": time.strftime("%Y-%m-%d"),
            "source_tier_entities": entities,
        }
        metadata.update(verdict.to_metadata())
        if provisional_entity and source_tier not in {
            "official",
            "first_party",
        }:
            metadata.update(
                {
                    "authority_provisional": True,
                    "authority_provisional_entity": provisional_entity,
                }
            )

        self._last_evidence_records = [
            {
                "source_type": "web",
                "source_tier": source_tier,
                "reference": resolved_url,
                "title": title or resolved_url,
                "content": page_text,
                "metadata": metadata,
            }
        ]
        self._last_fetch_outcomes.append(
            {
                "url": url_key,
                "resolved_url": resolved_url,
                "status": "success",
                "chars": len(full_text),
                "provider": provider,
                "attempts": list(extraction.attempts or []),
            }
        )

        ledger = self._get_ledger()
        eid = ledger.register(self._last_evidence_records[0])
        metadata["eid"] = eid
        return ledger.render_entry(eid)

    def reset_budget(self) -> None:
        self._calls_in_run = 0
        self._seen_urls.clear()
        self._last_evidence_records = []
        self._last_fetch_outcomes = []
        if self._router is not None:
            reset = getattr(self._router, "reset", None)
            if callable(reset):
                reset()

    def set_analysis(self, analysis: Optional[QueryAnalysis]) -> None:
        self._analysis = analysis

    def set_ledger(self, ledger: Any) -> None:
        self._ledger = ledger

    def _get_ledger(self) -> Any:
        if self._ledger is None:
            from evidence.ledger import EvidenceLedger

            self._ledger = EvidenceLedger()
        return self._ledger

    def get_last_evidence_records(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._last_evidence_records]

    def get_last_fetch_outcomes(self) -> List[Dict[str, Any]]:
        return [dict(outcome) for outcome in self._last_fetch_outcomes]

    def get_pricing_source_candidates(
        self,
        requirements: Optional[Mapping[str, Any]] = None,
    ) -> List[Dict[str, str]]:
        """Return configured official price pages matching the request channel."""
        requirements = requirements or getattr(
            self._analysis, "numeric_requirements", None
        )
        if not isinstance(requirements, Mapping):
            return []
        subject = normalize_entity_stem(requirements.get("subject"))
        if not subject:
            return []
        orchestration = self.app_config.get("orchestration") or {}
        source_map = (
            orchestration.get("pricing_sources")
            if isinstance(orchestration, Mapping)
            else None
        )
        if not isinstance(source_map, Mapping):
            return []
        raw_candidates = source_map.get(subject) or []
        if isinstance(raw_candidates, (str, Mapping)):
            raw_candidates = [raw_candidates]
        requested_channel = str(requirements.get("channel") or "").strip()
        requested_currency = str(requirements.get("currency") or "").strip()
        candidates: List[Dict[str, str]] = []
        seen = set()
        for raw_candidate in raw_candidates:
            if isinstance(raw_candidate, str):
                candidate = {"url": raw_candidate}
            elif isinstance(raw_candidate, Mapping):
                candidate = {
                    key: str(raw_candidate.get(key) or "").strip()
                    for key in ("url", "channel", "currency")
                }
            else:
                continue
            url = canonical_reference(candidate.get("url"))
            if not url or url in seen:
                continue
            if requested_channel and candidate.get("channel") != requested_channel:
                continue
            if requested_currency and candidate.get("currency") != requested_currency:
                continue
            seen.add(url)
            candidates.append(
                {
                    "url": url,
                    "channel": candidate.get("channel", ""),
                    "currency": candidate.get("currency", ""),
                }
            )
        return candidates

    def get_budget_status(self) -> Dict[str, int]:
        return {"limit": self.max_calls_per_query, "used": self._calls_in_run}


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
        # Let the agent read a specific page (e.g. official docs) that
        # web_search only surfaced as a shallow snippet. Independent budget.
        tools.append(
            ReActFetchUrlTool(
                config=config,
                max_calls_per_query=tool_budget("fetch_url", 3),
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
