"""LangChain-based RAG pipelines using LCEL (LangChain Expression Language).

This module provides modern, composable RAG implementations using LangChain's
LCEL syntax for building retrieval-augmented generation pipelines.
"""

from __future__ import annotations

import os
import sys
import time
import logging
import re
from dataclasses import asdict
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple, Union

from langchain_core.documents import Document as LCDocument
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import (
    Runnable,
    RunnableLambda,
    RunnableParallel,
    RunnablePassthrough,
)

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence import (
    DomainEvidenceSource,
    EvidenceItem,
    LocalEvidenceSource,
    RetrievalOptions,
    WebEvidenceSource,
    build_evidence_summary,
    describe_used_sources,
    evidence_items_to_documents,
    evidence_items_to_search_hits,
    has_indexable_local_documents,
    normalize_reference_label,
)
from langchain.langchain_support import Document, FileReader, LangChainVectorStore
from langchain.langchain_tools import SearchRetriever, WebSearchTool
from search.search import SearchClient, SearchHit, GoogleSearchClient
from search.reference_fetch import (
    ReferenceExtractorRouter,
    build_reference_extractors,
)
from evidence.source_tiering import (
    classify_web_source_tier,
    official_entity_for_url,
)
from evidence.official_domain_resolver import build_official_domain_resolver
from evidence.official_domain_resolver import host_matches
from utils.retrieval_trace import emit_search_call_step, search_call_snapshots
from utils.timing_utils import TimingRecorder
from utils.workflow_trace import ensure_tracer
from utils.query_orchestration import (
    EvidenceLedger,
    PlanController,
    PlanStepKind,
    PlanStepResult,
    QueryExecutionTrace,
    QueryPlan,
    VerificationStatus,
    verify_evidence_plan,
)
from utils.query_config import (
    TIME_RANGE_CONFIG,
    QUERY_SIMPLIFICATION_PROMPT,
    DEFAULT_CONFIG
)

logger = logging.getLogger(__name__)


# Default prompts
DEFAULT_LOCAL_RAG_SYSTEM_PROMPT = """You are a helpful assistant. 
Answer the user's question based on the provided context from local documents.
Always answer in the same language as the user's question.
If the context doesn't contain relevant information, say so clearly."""

DEFAULT_SEARCH_RAG_SYSTEM_PROMPT = """You are an information assistant.
Answer user questions concisely using ONLY the provided search results and/or local documents.
CRITICAL: Do NOT fabricate, invent, or guess any specific data (such as scores, numbers, statistics, dates, or names) that is not EXPLICITLY stated in the provided context.
If specific information is not found, clearly state '未在搜索结果或本地文档中找到具体数据' or 'specific data not found'.
When unsure, acknowledge the uncertainty instead of guessing.
Always answer in the same language as the user's question."""

DEFAULT_DIRECT_FALLBACK_SYSTEM_PROMPT = """You are a knowledgeable assistant.
Answer clearly based on your existing knowledge.
Always answer in the same language as the user's question."""


class NullSearchClient(SearchClient):
    """No-op search client used to keep a single primary execution path."""

    source_id = "null"
    display_name = "Disabled Search"

    def search(
        self,
        query: str,
        num_results: int = 5,
        *,
        per_source_limit: Optional[int] = None,
        freshness: Optional[str] = None,
        date_restrict: Optional[str] = None,
    ) -> List[SearchHit]:
        _ = (query, num_results, per_source_limit, freshness, date_restrict)
        self._reset_timings()
        return []


class LocalRAGChain:
    """Local RAG pipeline using LangChain LCEL.
    
    This class provides a modern LCEL-based implementation of local document
    retrieval-augmented generation.
    """
    
    def __init__(
        self,
        llm: BaseChatModel,
        data_path: str,
        *,
        config: Optional[Dict[str, Any]] = None,
        embedding_model: Optional[str] = None,
        system_prompt: str = DEFAULT_LOCAL_RAG_SYSTEM_PROMPT,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ) -> None:
        self.llm = llm
        self.config = config or {}
        self.system_prompt = system_prompt
        
        # Initialize vector store
        self.vector_store = LangChainVectorStore(
            model_name=embedding_model,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            config=self.config,
        )
        
        # Load and index documents
        print("Loading and indexing documents...")
        chunk_count = self.vector_store.index_from_directory(data_path)
        print(f"Indexed {chunk_count} chunks.")
        
        # Build the LCEL chain
        self._chain = self._build_chain()
    
    def _build_chain(self) -> Runnable:
        """Build the LCEL chain for local RAG."""
        
        # Create retriever
        retriever = self.vector_store.as_retriever(search_kwargs={"k": 5})
        
        # Format documents function
        def format_docs(docs: List[LCDocument]) -> str:
            return "\n\n".join(
                f"[Document {i+1}]\nSource: {doc.metadata.get('source', 'Unknown')}\n{doc.page_content}"
                for i, doc in enumerate(docs)
            )
        
        # Create prompt template
        prompt = ChatPromptTemplate.from_messages([
            ("system", self.system_prompt),
            ("human", "Context:\n{context}\n\nQuestion: {question}"),
        ])
        
        # Build the chain using LCEL
        chain = (
            RunnableParallel(
                context=retriever | RunnableLambda(format_docs),
                question=RunnablePassthrough(),
            )
            | prompt
            | self.llm
            | StrOutputParser()
        )
        
        return chain
    
    def invoke(self, query: str, **kwargs: Any) -> str:
        """Run the RAG chain and return the answer."""
        return self._chain.invoke(query, **kwargs)
    
    def stream(self, query: str, **kwargs: Any) -> Iterator[str]:
        """Stream the RAG chain response."""
        for chunk in self._chain.stream(query, **kwargs):
            yield chunk
    
    def answer(
        self,
        query: str,
        *,
        num_retrieved_docs: int = 5,
        max_tokens: int = 5000,
        temperature: float = 0.3,
        timing_recorder: Optional[TimingRecorder] = None,
    ) -> Dict[str, Any]:
        """Answer a query with full response metadata (legacy interface).
        
        This method provides backward compatibility with the old LocalRAG interface.
        """
        # Retrieve documents
        retrieved_docs = self.vector_store.search(query, k=num_retrieved_docs)
        context = "\n".join([doc.content for doc in retrieved_docs])
        
        # Build messages
        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=f"Context:\n{context}\n\nQuestion: {query}"),
        ]
        
        # Generate response
        response_start = time.perf_counter()
        try:
            response = self.llm.invoke(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            content = response.content if hasattr(response, 'content') else str(response)
        finally:
            if timing_recorder:
                duration_ms = (time.perf_counter() - response_start) * 1000
                timing_recorder.record_llm_call(
                    label="local_rag_answer",
                    duration_ms=duration_ms,
                    provider=getattr(self.llm, "provider", None),
                    model=getattr(self.llm, "model_name", None),
                )
        
        # Build answer with source references
        answer = content
        if answer and retrieved_docs:
            answer += "\n\n**本地文档来源：**\n"
            for idx, doc in enumerate(retrieved_docs, start=1):
                source = doc.source or f"文档 {idx}"
                answer += f"{idx}. {source}\n"
        
        return {
            "query": query,
            "answer": answer,
            "retrieved_docs": [asdict(doc) for doc in retrieved_docs],
            "llm_raw": response.response_metadata if hasattr(response, "response_metadata") else None,
            "search_hits": [],
        }


class SearchRAGChain:
    """Search-augmented RAG pipeline using LangChain LCEL.
    
    Combines web search with optional local document retrieval for comprehensive
    information retrieval.
    """
    
    def __init__(
        self,
        llm: BaseChatModel,
        search_client: SearchClient,
        *,
        data_path: Optional[str] = None,
        system_prompt: str = DEFAULT_SEARCH_RAG_SYSTEM_PROMPT,
        config: Optional[Dict[str, Any]] = None,
        embedding_model: Optional[str] = None,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        reranker: Optional[Any] = None,
        min_rerank_score: float = 0.0,
        max_per_domain: int = 1,
        source_selector: Optional[Any] = None,
        tracer: Optional[Any] = None,
    ) -> None:
        self.llm = llm
        self.search_client = search_client
        self.config = config or {}
        self.system_prompt = system_prompt
        self.reranker = reranker
        self.min_rerank_score = min_rerank_score
        self.max_per_domain = max(1, max_per_domain)
        self.source_selector = source_selector

        # Initialize local vector store if data_path provided
        tracer = ensure_tracer(tracer)
        self.vector_store: Optional[LangChainVectorStore] = None
        if data_path:
            if not has_indexable_local_documents(data_path):
                tracer.skip("local_index", "准备本地向量索引", detail="无可索引本地文档")
            else:
                print("Loading and indexing local documents...")
                tracer.begin("local_index", "准备本地向量索引", detail="加载嵌入模型并索引文档")
                try:
                    self.vector_store = LangChainVectorStore(
                        model_name=embedding_model,
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                        config=self.config,
                    )
                    chunk_count = self.vector_store.index_from_directory(data_path)
                    print(f"Indexed {chunk_count} chunks from local documents.")
                    tracer.end("local_index", detail=f"{chunk_count} 个分块")
                except Exception as e:
                    print(f"Failed to load local documents: {e}")
                    tracer.end("local_index", detail="无可用本地文档", status="skipped")
                    self.vector_store = None

        orchestration = self.config.get("orchestration") or {}
        official_domains = (
            orchestration.get("official_domains")
            if isinstance(orchestration, dict)
            else None
        )
        # Build the discover -> verify -> cache official-domain resolver. It
        # honors configured ``pins`` (folded from the legacy ``official_domains``
        # map) with top priority and only performs network discovery for stems
        # that are not pinned, when ``official_domain_resolution.enabled`` is on.
        discovery_clients = list(
            getattr(search_client, "clients", None) or [search_client]
        )
        self._official_resolver = build_official_domain_resolver(
            orchestration if isinstance(orchestration, dict) else {},
            search_clients=discovery_clients,
        )
        self.web_source = WebEvidenceSource(
            search_client,
            official_domains=official_domains if isinstance(official_domains, dict) else None,
            official_resolver=self._official_resolver,
        )
        self.local_source = LocalEvidenceSource(
            vector_store=self.vector_store,
            data_path=data_path,
            embedding_model=embedding_model,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            config=self.config,
        )
        self.domain_source = DomainEvidenceSource(source_selector)

        # Official-page extraction: pull full page content from official /
        # first-party domains so answers ground in primary documentation rather
        # than shallow search snippets. The router is built lazily and only
        # from providers whose keys are already configured.
        orchestration_cfg = self.config.get("orchestration") or {}
        if not isinstance(orchestration_cfg, dict):
            orchestration_cfg = {}
        extraction_cfg = orchestration_cfg.get("official_page_extraction")
        if isinstance(extraction_cfg, bool):
            extraction_cfg = {"enabled": extraction_cfg}
        if not isinstance(extraction_cfg, dict):
            extraction_cfg = {}
        self.official_page_extraction_enabled = bool(extraction_cfg.get("enabled", True))
        self.official_page_max_urls = max(
            1, min(int(extraction_cfg.get("max_urls", 4) or 4), 10)
        )
        self.official_page_max_chars = max(
            200, int(extraction_cfg.get("max_chars_per_page", 4000) or 4000)
        )
        # When search returns no official/first-party hits, still extract full
        # content from the top-ranked results so the answer sees real pages
        # instead of shallow snippets. Bounded and separately configurable.
        self.official_page_fill_non_official = bool(
            extraction_cfg.get("fill_non_official", True)
        )
        self.official_page_max_non_official_urls = max(
            0, min(int(extraction_cfg.get("max_non_official_urls", 2) or 2), 8)
        )
        self._reference_router: Optional[ReferenceExtractorRouter] = None
        self._official_domains_map = (
            official_domains if isinstance(official_domains, dict) else None
        )

    def _format_evidence_context(self, items: List[EvidenceItem]) -> Dict[str, str]:
        """Format evidence items into grouped prompt context blocks."""
        grouped: Dict[str, List[str]] = {
            "domain": [],
            "web": [],
            "local": [],
        }

        for index, item in enumerate(items, start=1):
            label = normalize_reference_label(item)
            content = item.snippet or item.content
            if item.source_type == "web":
                grouped["web"].append(
                    f"{index}. {item.title or f'Result {index}'}\n"
                    f"   URL: {label or 'N/A'}\n"
                    f"   {content or 'No snippet available.'}"
                )
            elif item.source_type == "local":
                preview = content[:500]
                if len(content) > 500:
                    preview += "..."
                grouped["local"].append(f"{index}. {label}\n   {preview}")
            elif item.source_type == "domain":
                grouped["domain"].append(
                    f"{index}. {item.title or 'Domain Evidence'}\n"
                    f"   Source: {label}\n"
                    f"   {content}"
                )

        return {
            "domain": "\n".join(grouped["domain"]),
            "web": "\n".join(grouped["web"]),
            "local": "\n".join(grouped["local"]),
        }

    @staticmethod
    def _qualified_limited_evidence_notice(query: str) -> str:
        """Give a bounded answer when a provider returns empty limited-context text."""
        if any("\u4e00" <= char <= "\u9fff" for char in query):
            return (
                "检索到了相关来源，但它们未满足该问题所需的权威性标准。"
                "以下链接只能作为待核实线索，具体事实请以官方来源为准。"
            )
        return (
            "Relevant sources were found, but they do not meet the required authority tier. "
            "Treat the links below as leads to verify against an official source."
        )

    def _dedupe_and_rank_evidence(
        self,
        items: List[EvidenceItem],
    ) -> Tuple[List[EvidenceItem], List[Dict[str, Any]]]:
        """Apply unified evidence deduplication and assign final ranks."""
        deduped: List[EvidenceItem] = []
        metadata: List[Dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()

        for item in items:
            key = (
                item.source_type,
                str(item.reference or item.title).strip().lower(),
                " ".join(str(item.content or item.snippet or "").split())[:180].lower(),
            )
            if key in seen:
                metadata.append(
                    {
                        "source_type": item.source_type,
                        "source_id": item.source_id,
                        "reference": normalize_reference_label(item),
                        "dropped": "duplicate_evidence",
                    }
                )
                continue
            seen.add(key)
            deduped.append(item)

        for rank, item in enumerate(deduped, start=1):
            item.rank = rank

        return deduped, metadata

    def _collect_domain_evidence(
        self,
        query: str,
        *,
        domain: Optional[str],
        domain_result: Optional[Dict[str, Any]],
        extra_context: Optional[str],
        enable_domain: bool,
        timing_recorder: Optional[TimingRecorder],
        plan_metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[EvidenceItem], Optional[Dict[str, Any]]]:
        """Normalize domain API evidence into unified evidence items."""
        if not (domain_result or extra_context or (enable_domain and self.source_selector)):
            return [], domain_result

        options = RetrievalOptions(
            num_results=1,
            timing_recorder=timing_recorder,
            metadata={
                "domain": domain,
                "domain_result": domain_result,
                "extra_context": extra_context,
                **(plan_metadata or {}),
            },
        )
        items = self.domain_source.retrieve(query, options)

        if domain_result is None and items:
            domain_result = {
                "answer": items[0].content,
                "handled": True,
                "continue_search": True,
            }
            domain_meta = items[0].metadata or {}
            if domain_meta.get("data") is not None:
                domain_result["data"] = domain_meta.get("data")

        return items, domain_result

    def _retrieve_evidence(
        self,
        query: str,
        *,
        search_query: Optional[str],
        num_search_results: int,
        per_source_limit: Optional[int],
        num_retrieved_docs: int,
        enable_search: bool,
        enable_local_docs: bool,
        freshness: Optional[str],
        date_restrict: Optional[str],
        timing_recorder: Optional[TimingRecorder],
        domain: Optional[str] = None,
        domain_result: Optional[Dict[str, Any]] = None,
        extra_context: Optional[str] = None,
        enable_domain: bool = False,
        tracer: Optional[Any] = None,
        query_plan: Optional[QueryPlan] = None,
        evidence_ledger: Optional[EvidenceLedger] = None,
        execution_trace: Optional[QueryExecutionTrace] = None,
        plan_controller: Optional[PlanController] = None,
        web_step_kind: PlanStepKind = PlanStepKind.WEB_SEARCH,
        enable_temporal_recovery: bool = True,
    ) -> Dict[str, Any]:
        """Retrieve and normalize evidence from enabled first-class sources."""
        tracer = ensure_tracer(tracer)
        effective_query = search_query.strip() if search_query else query
        active_sources: List[Dict[str, Any]] = []
        evidence_items: List[EvidenceItem] = []
        rerank_meta: List[Dict[str, Any]] = []
        fusion_meta: List[Dict[str, Any]] = []
        search_error: Optional[str] = None
        search_warnings: List[str] = []
        search_timings: List[Dict[str, Any]] = []
        search_attempts: List[Dict[str, Any]] = []
        executed_providers: List[str] = []
        search_api_calls: List[Dict[str, Any]] = []
        search_api_index = 0

        domain_step = query_plan.step_for_kind(PlanStepKind.DOMAIN_API) if query_plan else None
        web_step = (
            query_plan.step_for_kind(web_step_kind, include_recovery=True)
            if query_plan
            else None
        )
        local_step = query_plan.step_for_kind(PlanStepKind.LOCAL_RETRIEVAL) if query_plan else None
        temporal_step = (
            query_plan.step_for_kind(PlanStepKind.TEMPORAL_RECOVERY, include_recovery=True)
            if query_plan and enable_temporal_recovery
            else None
        )
        tier_entities: List[str] = []
        if query_plan is not None:
            tier_entities = list(
                dict.fromkeys(
                    list(query_plan.analysis.comparison_members)
                    + list(query_plan.analysis.entities)
                )
            )
        if query_plan is not None:
            enable_domain = bool(enable_domain and domain_step)
            enable_search = bool(enable_search and web_step)
            enable_local_docs = bool(enable_local_docs and local_step)
            if not domain_step:
                domain_result = None
                extra_context = None

        def execute_step(
            step: Optional[Any],
            executor: Callable[[Any], PlanStepResult],
        ) -> PlanStepResult:
            """Run a plan step or preserve the legacy behavior without a plan."""
            if step is None:
                return executor(None)
            if plan_controller is not None:
                return plan_controller.run_step(step, executor)
            if execution_trace is not None:
                execution_trace.begin(step)
            try:
                result = executor(step)
                if not isinstance(result, PlanStepResult):
                    raise TypeError("Plan executors must return PlanStepResult.")
            except Exception as exc:  # noqa: BLE001 - retrieval failures are response data
                result = PlanStepResult(status="error", reason=str(exc))
            if execution_trace is not None:
                execution_trace.finish(
                    step,
                    status=result.status,
                    providers=result.providers,
                    attempts=result.attempts,
                    item_count=len(result.items),
                    reason=result.reason,
                )
            return result

        def snapshot_provider_state(client: Any) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
            timings_getter = getattr(client, "get_last_timings", None)
            timings = list(timings_getter() or []) if callable(timings_getter) else []
            errors_getter = getattr(client, "get_last_errors", None)
            errors = list(errors_getter() or []) if callable(errors_getter) else []
            return timings, errors

        def snapshot_search_call_records(client: Any) -> List[Dict[str, Any]]:
            records_getter = getattr(client, "get_last_call_records", None)
            if not callable(records_getter):
                return []
            return [record for record in list(records_getter() or []) if isinstance(record, dict)]

        def emit_search_call_records(records: List[Dict[str, Any]]) -> None:
            """Emit concrete provider requests before later evidence fusion."""
            nonlocal search_api_index
            for snapshot in search_call_snapshots(records):
                search_api_index += 1
                search_api_calls.append(snapshot)
                emit_search_call_step(
                    tracer,
                    snapshot,
                    step_id=f"search_api_{web_step_kind.value}_{search_api_index}",
                )

        def record_provider_state(
            timings: List[Dict[str, Any]],
            errors: List[Dict[str, Any]],
        ) -> tuple[List[str], List[Dict[str, Any]]]:
            providers: List[str] = []
            attempts: List[Dict[str, Any]] = []
            for entry in timings:
                if not isinstance(entry, dict):
                    continue
                provider = str(entry.get("source") or "").strip()
                if provider and provider not in providers:
                    providers.append(provider)
                attempt: Dict[str, Any] = {
                    "provider": provider or str(entry.get("label") or "search"),
                    "status": "error" if entry.get("error") else "done",
                }
                if entry.get("duration_ms") is not None:
                    attempt["duration_ms"] = entry.get("duration_ms")
                if entry.get("fallback"):
                    attempt["fallback"] = True
                if entry.get("error"):
                    attempt["reason"] = str(entry.get("error"))[:160]
                attempts.append(attempt)
            for entry in errors:
                if not isinstance(entry, dict):
                    continue
                provider = str(entry.get("source") or "搜索服务")
                detail = str(entry.get("error") or "未知错误")
                search_warnings.append(f"{provider} 出现异常：{detail}")
                if not any(item.get("provider") == provider and item.get("reason") == detail for item in attempts):
                    attempts.append({"provider": provider, "status": "error", "reason": detail[:160]})
            return providers, attempts

        domain_items, domain_result = self._collect_domain_evidence(
            query,
            domain=domain,
            domain_result=domain_result,
            extra_context=extra_context,
            enable_domain=enable_domain,
            timing_recorder=timing_recorder,
            plan_metadata=(
                {
                    "originating_plan_step": domain_step.step_id,
                    "source_tier": "authoritative",
                    "retrieval_kind": "domain_api",
                }
                if domain_step
                else None
            ),
        )
        if domain_items:
            active_sources.append(self.domain_source.describe_with_domain(domain or domain_items[0].metadata.get("domain")))
            evidence_items.extend(domain_items)

        if enable_search:
            active_sources.append(self.web_source.describe())
            tracer.begin("search", "联网检索", detail=effective_query)
            hits_count = 0
            per_source_cap = per_source_limit or num_search_results

            def retrieve_official_domains(step: Any) -> PlanStepResult:
                """Run one bounded target-domain search per required entity."""
                nonlocal search_timings, search_attempts, executed_providers
                targets = [
                    target
                    for target in (step.metadata.get("targets") or [])
                    if isinstance(target, dict)
                    and str(target.get("entity") or "").strip()
                    and isinstance(target.get("domains"), list)
                ]
                recovered_items: List[EvidenceItem] = []
                providers: List[str] = []
                attempts: List[Dict[str, Any]] = []
                warnings: List[str] = []
                for target in targets:
                    entity = str(target["entity"]).strip()
                    domains = {
                        str(domain).strip().casefold()
                        for domain in target["domains"]
                        if str(domain).strip()
                    }
                    target_query = str(target.get("query") or effective_query).strip()
                    target_hits: List[SearchHit] = []
                    try:
                        search_for_domains = getattr(
                            self.search_client,
                            "search_for_domains",
                            None,
                        )
                        if callable(search_for_domains):
                            target_hits = list(
                                search_for_domains(
                                    target_query,
                                    domains,
                                    num_results=1,
                                    per_source_limit=1,
                                    freshness=freshness,
                                    date_restrict=date_restrict,
                                )
                                or []
                            )
                        else:
                            target_hits = list(
                                self.search_client.search(
                                    target_query,
                                    num_results=1,
                                    per_source_limit=1,
                                    freshness=freshness,
                                    date_restrict=date_restrict,
                                )
                                or []
                            )
                            target_hits = [
                                hit
                                for hit in target_hits
                                if any(host_matches(domain, hit.url) for domain in domains)
                            ]
                    except Exception as exc:  # noqa: BLE001 - target misses stay auditable
                        search_warnings.append(f"{entity} 官方域名检索异常：{exc}")

                    timings, errors = snapshot_provider_state(self.search_client)
                    call_records = snapshot_search_call_records(self.search_client)
                    if not call_records and len(timings) == 1:
                        timing = timings[0]
                        if isinstance(timing, dict):
                            call_records = [
                                {
                                    "source": timing.get("source"),
                                    "label": timing.get("label"),
                                    "query": target_query,
                                    "duration_ms": timing.get("duration_ms"),
                                    "status": "error" if timing.get("error") else "done",
                                    "result_count": len(target_hits),
                                    "results": [
                                        {
                                            "title": hit.title,
                                            "url": hit.url,
                                            "snippet": hit.snippet,
                                        }
                                        for hit in target_hits
                                    ],
                                    "error": timing.get("error"),
                                    "target": entity,
                                }
                            ]
                    for record in call_records:
                        record.setdefault("target", entity)
                    emit_search_call_records(call_records)
                    search_timings.extend(timings)
                    target_providers, target_attempts = record_provider_state(timings, errors)
                    for attempt in target_attempts:
                        attempt["target"] = entity
                    providers.extend(
                        provider
                        for provider in target_providers
                        if provider not in providers
                    )
                    attempts.extend(target_attempts)
                    executed_providers.extend(
                        provider
                        for provider in target_providers
                        if provider not in executed_providers
                    )

                    target_hit = next(
                        (
                            hit
                            for hit in target_hits
                            if any(host_matches(domain, hit.url) for domain in domains)
                        ),
                        None,
                    )
                    if target_hit is None:
                        warnings.append(f"未找到 {entity} 的配置官方域名结果")
                        attempts.append(
                            {
                                "provider": "official_domain_recovery",
                                "target": entity,
                                "status": "missing",
                            }
                        )
                        continue
                    recovered_items.extend(
                        self.web_source.hits_to_items(
                            [target_hit],
                            provenance={
                                "originating_plan_step": step.step_id,
                                "retrieval_kind": "official_domain_recovery",
                                "official_target": entity,
                            },
                            tier_entities=[entity],
                        )
                    )
                    attempts.append(
                        {
                            "provider": "official_domain_recovery",
                            "target": entity,
                            "status": "done",
                        }
                    )
                search_attempts.extend(attempts)
                return PlanStepResult(
                    items=recovered_items,
                    providers=providers,
                    attempts=attempts,
                    warnings=warnings,
                )

            def retrieve_web(step: Any) -> PlanStepResult:
                nonlocal rerank_meta, search_error, search_timings, search_attempts, executed_providers
                if step is not None and step.kind == PlanStepKind.OFFICIAL_DOMAIN_RECOVERY:
                    return retrieve_official_domains(step)
                step_limit = (
                    max(1, int(step.max_results or num_search_results))
                    if step is not None
                    else num_search_results
                )
                per_source_cap = min(per_source_limit or num_search_results, step_limit)
                fetch_limit = step_limit
                if self.reranker and hasattr(self.search_client, "clients"):
                    fetch_limit = max(step_limit, per_source_cap * len(self.search_client.clients))
                try:
                    search_items = self.web_source.retrieve(
                        effective_query,
                        RetrievalOptions(
                            num_results=fetch_limit,
                            per_source_limit=per_source_cap,
                            freshness=freshness,
                            date_restrict=date_restrict,
                            metadata={
                                "originating_plan_step": step.step_id if step else None,
                                "retrieval_kind": "general_search",
                                "source_tier_entities": tier_entities,
                            },
                        ),
                    )
                    # Source tiering marks non-evidence hosts explicitly. Keep
                    # this second guard at the RAG boundary so alternate web
                    # sources cannot reintroduce them into fusion.
                    search_items = [
                        item
                        for item in search_items
                        if not (item.metadata or {}).get("exclude_from_evidence")
                    ]
                    timings, errors = snapshot_provider_state(self.search_client)
                    call_records = snapshot_search_call_records(self.search_client)
                    if not call_records and len(timings) == 1:
                        timing = timings[0]
                        if isinstance(timing, dict):
                            call_records = [
                                {
                                    "source": timing.get("source"),
                                    "label": timing.get("label"),
                                    "query": effective_query,
                                    "duration_ms": timing.get("duration_ms"),
                                    "status": "error" if timing.get("error") else "done",
                                    "result_count": len(search_items),
                                    "results": [
                                        {"title": hit.title, "url": hit.url, "snippet": hit.snippet}
                                        for hit in evidence_items_to_search_hits(search_items)
                                    ],
                                    "error": timing.get("error"),
                                    "fallback": bool(timing.get("fallback")),
                                }
                            ]
                    emit_search_call_records(call_records)
                    search_timings.extend(timings)
                    providers, attempts = record_provider_state(timings, errors)
                    executed_providers.extend(provider for provider in providers if provider not in executed_providers)
                    search_attempts.extend(attempts)
                    hits = evidence_items_to_search_hits(search_items)
                    hits, rerank_meta = self._apply_rerank(query, hits, limit=step_limit)
                    return PlanStepResult(
                        items=self.web_source.hits_to_items(
                            hits,
                            provenance={
                                "originating_plan_step": step.step_id if step else None,
                                "retrieval_kind": "general_search",
                            },
                            tier_entities=tier_entities,
                        ),
                        providers=providers,
                        attempts=attempts,
                    )
                except Exception as exc:  # noqa: BLE001 - preserve search failure in payload
                    search_error = str(exc)
                    timings, errors = snapshot_provider_state(self.search_client)
                    call_records = snapshot_search_call_records(self.search_client)
                    if not call_records:
                        call_records = [
                            {
                                "source": timing.get("source"),
                                "label": timing.get("label"),
                                "query": effective_query,
                                "duration_ms": timing.get("duration_ms"),
                                "status": "error",
                                "result_count": 0,
                                "results": [],
                                "error": timing.get("error") or search_error,
                                "fallback": bool(timing.get("fallback")),
                            }
                            for timing in timings
                            if isinstance(timing, dict)
                        ]
                    emit_search_call_records(call_records)
                    search_timings.extend(timings)
                    providers, attempts = record_provider_state(timings, errors)
                    executed_providers.extend(provider for provider in providers if provider not in executed_providers)
                    search_attempts.extend(attempts)
                    return PlanStepResult(
                        status="error",
                        reason=search_error,
                        providers=providers,
                        attempts=attempts,
                    )

            web_result = execute_step(web_step, retrieve_web)
            web_items = list(web_result.items or [])
            evidence_items.extend(web_items)
            hits = evidence_items_to_search_hits(web_items)
            hits_count = len(hits)
            if web_result.status in {"error", "blocked", "skipped"} and not search_error:
                search_error = web_result.reason or web_result.status

            # Official-page extraction: fetch full content from official /
            # first-party domains and feed it into the evidence pool so the
            # answer can ground in primary documentation, not just snippets.
            try:
                extracted_items, extraction_records = self._enrich_official_pages(
                    query=query,
                    web_hits=hits,
                    tier_entities=tier_entities,
                    tracer=tracer,
                )
            except Exception as exc:  # noqa: BLE001 - enrichment is best effort
                print(f"[official_page_extraction] error: {exc}")
                extracted_items, extraction_records = [], []
            if extracted_items:
                evidence_items.extend(extracted_items)
                hits_count += len(extracted_items)
            if extraction_records:
                for record in extraction_records:
                    search_api_index += 1
                    search_api_calls.append(record)
                    emit_search_call_step(
                        tracer,
                        record,
                        step_id=f"search_api_extract_{search_api_index}",
                    )

            if temporal_step is not None and not search_error:
                missing_years = self._detect_missing_years(
                    query,
                    hits,
                    temporal_requested=True,
                )
                if missing_years:
                    logger.info(
                        "Insufficient historical data found (missing: %s), performing granular search fallback.",
                        missing_years,
                    )

                    temporal_providers: List[str] = []
                    temporal_attempts: List[Dict[str, Any]] = []

                    def collect_temporal_attempts(
                        timings: List[Dict[str, Any]],
                        errors: List[Dict[str, Any]],
                    ) -> None:
                        providers, attempts = record_provider_state(timings, errors)
                        search_timings.extend(timings)
                        search_attempts.extend(attempts)
                        temporal_attempts.extend(attempts)
                        temporal_providers.extend(
                            provider for provider in providers if provider not in temporal_providers
                        )
                        executed_providers.extend(
                            provider for provider in providers if provider not in executed_providers
                        )

                    def retrieve_temporal(step: Any) -> PlanStepResult:
                        granular_hits = self._perform_granular_search_fallback(
                            query,
                            effective_query,
                            num_search_results,
                            per_source_cap,
                            freshness,
                            date_restrict,
                            timing_recorder,
                            missing_years=missing_years,
                            attempt_collector=collect_temporal_attempts,
                            call_observer=emit_search_call_records,
                        )
                        return PlanStepResult(
                            items=self.web_source.hits_to_items(
                                granular_hits,
                                provenance={
                                    "originating_plan_step": step.step_id if step else None,
                                    "retrieval_kind": "temporal_recovery",
                                },
                                tier_entities=tier_entities,
                            ),
                            providers=temporal_providers,
                            attempts=temporal_attempts,
                        )

                    temporal_result = execute_step(temporal_step, retrieve_temporal)
                    evidence_items.extend(temporal_result.items or [])
                    hits_count = len(evidence_items_to_search_hits(evidence_items))

            if timing_recorder and search_timings:
                timing_recorder.extend_search_timings(search_timings)
            if search_error:
                tracer.error("search", detail="检索异常")
            else:
                step_items = []
                for entry in search_timings:
                    if not isinstance(entry, dict):
                        continue
                    label = str(entry.get("label") or entry.get("source") or "搜索源")
                    try:
                        value = f"{float(entry.get('duration_ms', 0.0)):.0f} ms"
                    except (TypeError, ValueError):
                        value = "--"
                    if entry.get("error"):
                        value += f" · {entry['error']}"
                    step_items.append({"label": label, "value": value})
                detail_parts = [f"{hits_count} 条结果"]
                source_names = sorted({str(t.get("source")) for t in search_timings if isinstance(t, dict) and t.get("source")})
                if source_names:
                    detail_parts.append("、".join(source_names[:4]))
                tracer.end("search", detail=" · ".join(detail_parts), items=step_items or None)

        if enable_local_docs and self.local_source.is_available():
            tracer.begin("local", "本地文档检索")
            active_sources.append(self.local_source.describe())

            def retrieve_local(step: Any) -> PlanStepResult:
                items = self.local_source.retrieve(
                    query,
                    RetrievalOptions(
                        num_results=num_retrieved_docs,
                        metadata={
                            "originating_plan_step": step.step_id if step else None,
                            "source_tier": "local",
                            "retrieval_kind": "local_retrieval",
                        },
                    ),
                )
                return PlanStepResult(items=items, providers=[self.local_source.source_id])

            local_result = execute_step(local_step, retrieve_local)
            evidence_items.extend(local_result.items or [])
            if local_result.status in {"error", "blocked", "skipped"}:
                tracer.error("local", detail=local_result.reason or local_result.status)
            else:
                tracer.end("local", detail=f"{len(local_result.items)} 个片段")

        tracer.begin("rerank", "证据重排融合")
        evidence_items, fusion_meta = self._dedupe_and_rank_evidence(evidence_items)
        answer_basis = "retrieved_evidence"
        if evidence_ledger is not None:
            evidence_ledger.ingest(evidence_items)
            evidence_ledger.apply_limits(
                max_items=query_plan.result_budget if query_plan else num_search_results,
            )
            retained_items = evidence_ledger.retained_items()
            limited_items = evidence_ledger.limited_items()
            evidence_items = retained_items or limited_items
            if limited_items and not retained_items:
                answer_basis = "limited_evidence"
            elif retained_items:
                answer_basis = "retained_evidence"
            fusion_meta.extend(
                {
                    "reference": entry.canonical_reference,
                    "decision": entry.decision,
                    "reason": entry.reason,
                }
                for entry in evidence_ledger.entries
            )
            if execution_trace is not None:
                execution_trace.record_ledger(evidence_ledger)
        tracer.end("rerank", detail=f"保留 {len(evidence_items)} 条证据")
        return {
            "effective_query": effective_query,
            "evidence_items": evidence_items,
            "active_sources": active_sources,
            "used_sources": describe_used_sources(evidence_items),
            "search_error": search_error,
            "search_warnings": search_warnings,
            "rerank_meta": rerank_meta,
            "fusion_meta": fusion_meta,
            "domain_result": domain_result,
            "search_provider_trace": {
                "executed": executed_providers,
                "attempts": search_attempts,
            },
            "search_api_calls": search_api_calls,
            "answer_basis": answer_basis,
        }
    
    def _format_search_hits(self, hits: List[SearchHit]) -> str:
        """Format search hits for prompt context."""
        if not hits:
            return "No search results were returned."
        
        formatted = []
        for idx, hit in enumerate(hits, 1):
            formatted.append(
                f"{idx}. {hit.title or f'Result {idx}'}\n"
                f"   URL: {hit.url or 'N/A'}\n"
                f"   {hit.snippet or 'No snippet available.'}"
            )
        return "\n".join(formatted)

    def _get_reference_router(self) -> Optional[ReferenceExtractorRouter]:
        """Build the page-extraction router lazily from configured providers."""
        if not self.official_page_extraction_enabled:
            return None
        if self._reference_router is not None:
            return self._reference_router
        extractors = build_reference_extractors(self.config or {})
        if not extractors:
            self._reference_router = None
            return None
        self._reference_router = ReferenceExtractorRouter(extractors)
        return self._reference_router

    def _collect_resolution_audit(self, entities: List[str]) -> List[Dict[str, Any]]:
        """Return audit records for discovered (non-pinned) official entities.

        Only discovered resolutions are surfaced; pinned stems carry no
        discovery rationale worth auditing -- unless the pin shadow audit
        recorded a disagreement, in which case the (never overridden) pin is
        surfaced so a wrong or expired pin is visible instead of silent.
        Cache reads power this, so it is cheap to call per enrichment.
        """
        resolver = self._official_resolver
        if resolver is None:
            return []
        audit: List[Dict[str, Any]] = []
        for entity in entities or []:
            label = str(entity or "").strip()
            if not label:
                continue
            try:
                resolution = resolver.resolve(label)
            except Exception:  # noqa: BLE001
                continue
            if not resolution.is_official:
                continue
            shadow_disagreement = any(
                getattr(s, "kind", "") == "pin_shadow_disagreement"
                for s in resolution.signals
            )
            # Skip pure-pin resolutions: nothing was discovered. A shadow-audit
            # disagreement is the exception -- that is precisely what must be
            # visible.
            if all(getattr(s, "tier", "") == "pin" for s in resolution.signals) and not shadow_disagreement:
                continue
            record: Dict[str, Any] = {
                "entity": label,
                "stem": resolution.stem,
                "domain": resolution.domain,
                "signals": [
                    {
                        "kind": getattr(s, "kind", ""),
                        "source": getattr(s, "source", ""),
                        "tier": getattr(s, "tier", ""),
                    }
                    for s in resolution.signals
                ],
            }
            if shadow_disagreement:
                record["pin_shadow_audit"] = next(
                    (
                        getattr(s, "detail", "")
                        for s in resolution.signals
                        if getattr(s, "kind", "") == "pin_shadow_disagreement"
                    ),
                    "",
                )
            audit.append(record)
        return audit

    def _enrich_official_pages(
        self,
        query: str,
        web_hits: List[SearchHit],
        tier_entities: List[str],
        tracer: Optional[Any] = None,
    ) -> tuple[List[EvidenceItem], List[Dict[str, Any]]]:
        """Extract full content from official/first-party hits.

        Returns additional evidence items plus per-provider extraction records
        (surfaced as ``search_api_calls`` of kind ``extracted_pages`` in the UI).
        Failures degrade gracefully: any error yields no items, never a crash.
        """
        if not web_hits:
            return [], []
        router = self._get_reference_router()
        if router is None:
            return [], []

        entities = list(tier_entities) if tier_entities else []
        seen_urls: Set[str] = set()
        official_selected: List[str] = []
        for hit in web_hits:
            if len(official_selected) >= self.official_page_max_urls:
                break
            url = (hit.url or "").strip()
            if not url or url in seen_urls:
                continue
            tier = classify_web_source_tier(
                url,
                entities=entities,
                official_domains=self._official_domains_map,
                resolver=self._official_resolver,
            )
            official_target = official_entity_for_url(
                url,
                entities=entities,
                official_domains=self._official_domains_map,
                resolver=self._official_resolver,
            )
            if official_target or tier == "first_party":
                seen_urls.add(url)
                official_selected.append(url)
        selected = list(official_selected)
        # Fallback: when search surfaces no official-domain hits (or to fill the
        # remaining budget), extract the top-ranked remaining hits so the answer
        # still reads full pages instead of shallow snippets.
        if self.official_page_fill_non_official and self.official_page_max_non_official_urls:
            if official_selected:
                non_official_cap = self.official_page_max_non_official_urls
            else:
                # Nothing official at all: use the full budget for top hits.
                non_official_cap = self.official_page_max_urls
            added = 0
            for hit in web_hits:
                if added >= non_official_cap or len(selected) >= self.official_page_max_urls:
                    break
                url = (hit.url or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                selected.append(url)
                added += 1
        if not selected:
            return [], []

        tracer_ref = ensure_tracer(tracer)
        official_count = sum(
            1
            for url in selected
            if official_entity_for_url(
                url,
                entities=entities,
                official_domains=self._official_domains_map,
                resolver=self._official_resolver,
            )
        )
        non_official_count = len(selected) - official_count
        step_detail_parts = [f"{official_count} 官方页"] if official_count else []
        if non_official_count:
            step_detail_parts.append(f"{non_official_count} 非官方页")
        # Surface why each discovered (non-pinned) official entity was judged
        # official, so the audit trace explains the domain verdict.
        resolution_audit = self._collect_resolution_audit(entities)
        if resolution_audit:
            step_detail_parts.append(
                f"{len(resolution_audit)} 解析官方域"
            )
        tracer_ref.begin(
            "official_extract",
            "抓取官方文档",
            detail="、".join(step_detail_parts) or f"{len(selected)} 个页面",
        )
        started_at = time.perf_counter()
        try:
            extraction = router.extract(selected, objective=query or None)
        except Exception as exc:  # noqa: BLE001 - extraction is best effort
            print(f"[official_page_extraction] failed: {exc}")
            tracer_ref.error("official_extract", detail="抓取异常")
            return [], []
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)

        synthetic_hits: List[SearchHit] = []
        records: List[Dict[str, Any]] = []
        result_rows: List[Dict[str, Any]] = []
        for content in extraction.contents:
            text = (content.content or "").strip()
            if not text:
                continue
            snippet = text[: self.official_page_max_chars]
            title = content.title or content.url
            synthetic_hits.append(
                SearchHit(title=title, url=content.url, snippet=snippet)
            )
            official_target = official_entity_for_url(
                content.url,
                entities=entities,
                official_domains=self._official_domains_map,
                resolver=self._official_resolver,
            )
            result_rows.append(
                {
                    "url": content.url,
                    "title": title,
                    "provider": content.provider,
                    "content_chars": len(text),
                    "status": "done",
                    "official": bool(official_target),
                    "official_target": official_target,
                }
            )

        failure_rows = [
            {
                "url": failure.requested_url,
                "provider": failure.provider,
                "status": "error",
                "error": failure.error_type,
            }
            for failure in extraction.failures
        ]

        if synthetic_hits:
            items = self.web_source.hits_to_items(
                synthetic_hits,
                provenance={"retrieval_kind": "official_page_extraction"},
                tier_entities=entities,
            )
        else:
            items = []

        if result_rows or failure_rows:
            extracted_official = sum(1 for row in result_rows if row.get("official"))
            label = "官方文档抓取" if extracted_official else "网页正文抓取"
            records.append(
                {
                    "source": "reference_extract",
                    "label": label,
                    "query": query or "",
                    "duration_ms": duration_ms,
                    "status": "error" if not synthetic_hits else "done",
                    "result_count": len(synthetic_hits),
                    "kind": "extracted_pages",
                    "official_count": extracted_official,
                    "records": result_rows + failure_rows,
                    "attempts": list(extraction.attempts),
                }
            )
        if resolution_audit:
            records.append(
                {
                    "source": "official_domain_resolution",
                    "label": "官方域解析",
                    "query": query or "",
                    "status": "done",
                    "kind": "resolved_entities",
                    "records": resolution_audit,
                }
            )

        detail = (
            f"{len(synthetic_hits)} 个页面"
            + (f"、{len(failure_rows)} 失败" if failure_rows else "")
            or "无内容"
        )
        tracer_ref.end("official_extract", detail=detail)
        return items, records

    
    def _format_local_docs(self, docs: List[Document]) -> str:
        """Format local documents for prompt context."""
        if not docs:
            return ""
        
        formatted = []
        for idx, doc in enumerate(docs, 1):
            source = doc.source or f"Document {idx}"
            content = doc.content[:500]
            if len(doc.content) > 500:
                content += "..."
            formatted.append(f"{idx}. {source}\n   {content}")
        return "\n".join(formatted)
    
    def _apply_rerank(
        self,
        query: str,
        hits: List[SearchHit],
        limit: Optional[int] = None,
    ) -> tuple[List[SearchHit], List[Dict[str, Any]]]:
        """Apply reranking to search results."""
        if not self.reranker or not hits:
            return hits, []
        
        try:
            from urllib.parse import urlparse
            
            reranked = self.reranker.rerank(query, hits)
            filtered: List[SearchHit] = []
            metadata: List[Dict[str, Any]] = []
            domain_counts: Dict[str, int] = {}
            max_results = limit or len(reranked)
            
            for item in reranked:
                if item.score < self.min_rerank_score:
                    metadata.append({
                        "url": item.hit.url,
                        "score": item.score,
                        "dropped": "below_min_score",
                    })
                    continue
                
                domain = urlparse(item.hit.url).netloc if item.hit.url else None
                if domain and domain_counts.get(domain, 0) >= self.max_per_domain:
                    metadata.append({
                        "url": item.hit.url,
                        "score": item.score,
                        "dropped": "per_domain_limit",
                    })
                    continue
                
                filtered.append(item.hit)
                metadata.append({
                    "url": item.hit.url,
                    "score": item.score,
                    "kept": True,
                })
                
                if domain:
                    domain_counts[domain] = domain_counts.get(domain, 0) + 1
                
                if len(filtered) >= max_results:
                    break
            
            return (filtered or hits, metadata)
        except Exception as exc:
            return hits, [{"error": str(exc)}]

    def _check_google_client_availability(self) -> Optional[Any]:
        """Check availability of Google search client."""
        # Check if search_client is CombinedSearchClient with clients
        if hasattr(self.search_client, "clients"):
            for client in self.search_client.clients:
                if hasattr(client, "source_id") and client.source_id == "google":
                    return client
        
        # Check if search_client itself is a GoogleSearchClient
        if hasattr(self.search_client, "source_id") and self.search_client.source_id == "google":
            return self.search_client
            
        return None

    def _extract_years_from_hits(self, hits: List[SearchHit]) -> Set[str]:
        """Extract years found in search hits."""
        if not hits:
            return set()
            
        # Check both snippets and titles
        combined_text = " ".join(f"{hit.title} {hit.snippet}" for hit in hits).lower()
        year_pattern = r'\b(20\d{2})\b'
        years_found = set(re.findall(year_pattern, combined_text))
        return years_found

    def _detect_missing_years(
        self,
        query: str,
        hits: List[SearchHit],
        *,
        temporal_requested: bool = False,
    ) -> List[str]:
        """Detect which years from the specified time range are missing in the search results."""
        query_lower = query.lower()
        
        # Check if this is a time-based query
        is_time_query = False
        time_range_years = DEFAULT_CONFIG["max_granular_search_years"]
        coverage_threshold = DEFAULT_CONFIG["default_coverage_threshold"]
        
        # Check for specific time ranges
        for range_name, config in TIME_RANGE_CONFIG.items():
            if any(k in query_lower for k in config["keywords"]):
                is_time_query = True
                time_range_years = config["years"]
                coverage_threshold = config["coverage_threshold"]
                break
        
        # The caller can only set this after the plan selected temporal
        # coverage.  Do not revive the old broad keyword-triggered behavior.
        if temporal_requested:
            is_time_query = True
        
        if not is_time_query:
            return []
            
        import datetime
        now_year = datetime.datetime.now().year
        start_year = now_year - time_range_years + 1
        target_years = {str(y) for y in range(start_year, now_year + 1)}
        
        found_years = self._extract_years_from_hits(hits)
        
        # Check if we have sufficient coverage
        if len(found_years.intersection(target_years)) / len(target_years) < coverage_threshold:
             return sorted(list(target_years), reverse=True)

        # Calculate missing years
        missing = target_years - found_years
        
        # If we are missing more than a few years, return them
        if missing:
            return sorted(list(missing), reverse=True)
            
        return []

    def _perform_granular_search_fallback(
        self, 
        original_query: str, 
        effective_query: str, 
        num_search_results: int, 
        per_source_cap: int,
        freshness: Optional[str],
        date_restrict: Optional[str],
        timing_recorder: Optional[TimingRecorder],
        missing_years: Optional[List[str]] = None,
        attempt_collector: Optional[Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], None]] = None,
        call_observer: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ) -> List[SearchHit]:
        """Perform granular search for historical data."""
        
        granular_hits = []
        google_client = self._check_google_client_availability()
        active_client = google_client if google_client else self.search_client

        def run_granular_search(search_text: str, **kwargs: Any) -> List[SearchHit]:
            """Capture every request before a later year query resets client state."""
            try:
                return active_client.search(search_text, **kwargs) or []
            finally:
                timings_getter = getattr(active_client, "get_last_timings", None)
                timings = list(timings_getter() or []) if callable(timings_getter) else []
                errors_getter = getattr(active_client, "get_last_errors", None)
                errors = list(errors_getter() or []) if callable(errors_getter) else []
                records_getter = getattr(active_client, "get_last_call_records", None)
                call_records = list(records_getter() or []) if callable(records_getter) else []
                if call_observer is not None:
                    call_observer(
                        [record for record in call_records if isinstance(record, dict)]
                    )
                if attempt_collector is not None:
                    attempt_collector(timings, errors)
                elif timing_recorder is not None and timings:
                    timing_recorder.extend_search_timings(timings)
        
        # Determine years to search
        if missing_years:
            selected_years = missing_years
            # If too many missing years (e.g. > 8), we might want to cap it to avoid excessive API calls
            # But for "last 10 years", usually at most 10.
            # Let's cap at 8 to be safe, prioritizing recent ones.
            if len(selected_years) > 8:
                selected_years = selected_years[:8]
        else:
            # Fallback to default sampling if no missing_years provided
            import datetime
            current_year = datetime.datetime.now().year
            years = [str(year) for year in range(current_year - 9, current_year + 1)]
            if len(years) > 6:
                step = max(1, len(years) // 5)
                selected_years = [years[i] for i in range(0, len(years), step)]
                if years[-1] not in selected_years:
                    selected_years.append(years[-1])
            else:
                selected_years = years

        logger.info(f"Granular search targeting years: {selected_years}")
        
        # Prepare base query
        base_query = effective_query if effective_query and len(effective_query) < len(original_query) * 1.5 else original_query
        
        # If base_query is too long/complex (likely a full sentence), simplify it using LLM
        if len(base_query) > DEFAULT_CONFIG["max_query_length_for_simplification"]:
            try:
                prompt = QUERY_SIMPLIFICATION_PROMPT.format(query=base_query)
                # Simple invocation to get keywords
                from langchain_core.messages import HumanMessage
                response = self.llm.invoke([HumanMessage(content=prompt)])
                content = response.content if hasattr(response, 'content') else str(response)
                cleaned_keywords = content.strip().replace('"', '').replace("'", "")
                if cleaned_keywords and len(cleaned_keywords) < len(base_query):
                    logger.info(f"Simplified query for granular search: '{base_query}' -> '{cleaned_keywords}'")
                    base_query = cleaned_keywords
            except Exception as e:
                logger.warning(f"Failed to simplify query for granular search: {e}")

        for year in selected_years:
            query_lower = original_query.lower()
            universities = []
            if "香港中文大學" in original_query or "香港中文大学" in original_query or "cuhk" in query_lower:
                universities.extend(["Chinese University of Hong Kong", "CUHK"])
            if "香港科技大學" in original_query or "香港科技大学" in original_query or "hkust" in query_lower:
                universities.extend(["Hong Kong University of Science and Technology", "HKUST"])
            
            year_query = f"{base_query} {year}"
            
            # Enhanced stock price query detection and handling
            is_stock_query = ("股价" in original_query or "stock price" in query_lower or 
                            "stock" in query_lower or "shares" in query_lower or
                            "英伟达" in original_query or "nvidia" in query_lower or
                            "英特尔" in original_query or "intel" in query_lower or
                            "NVDA" in original_query or "INTC" in original_query)
            
            needs_specific_data = ("具体" in original_query or "数值" in original_query or 
                                  "具体值" in original_query or "数据" in original_query or
                                  "变化" in original_query or "比较" in original_query or
                                  "specific" in query_lower or "data" in query_lower or 
                                  "value" in query_lower or "numbers" in query_lower or
                                  "compare" in query_lower or "change" in query_lower)
            
            if is_stock_query:
                # Extract company names for more targeted search
                companies = []
                if "英伟达" in original_query or "nvidia" in query_lower or "NVDA" in original_query:
                    companies.append(("NVIDIA", "NVDA", "英伟达"))
                if "英特尔" in original_query or "intel" in query_lower or "INTC" in original_query:
                    companies.append(("Intel", "INTC", "英特尔"))
                
                if companies:
                    # Generate specialized stock price queries for each company
                    for eng_name, ticker, cn_name in companies:
                        # Multiple query variants to maximize chances of finding data
                        stock_queries = [
                            f"{ticker} stock price {year} annual closing price",
                            f"{eng_name} {ticker} stock performance {year}",
                            f"{cn_name} {ticker} {year} 年股价 收盘价",
                        ]
                        
                        for sq in stock_queries:
                            try:
                                search_kwargs = {
                                    "num_results": 2,
                                    "freshness": None,
                                    "date_restrict": None,
                                }
                                sq_hits = run_granular_search(sq, **search_kwargs)
                                granular_hits.extend(sq_hits)
                                logger.info(f"Stock query '{sq}' found {len(sq_hits)} hits.")
                            except Exception as e:
                                logger.warning(f"Stock search '{sq}' failed: {e}")
                        
                        time.sleep(0.5)  # Avoid rate limits
                    continue  # Skip default year_query for stock queries
                else:
                    # Generic stock query enhancement
                    if any("\u4e00" <= c <= "\u9fff" for c in original_query):
                        year_query = f"{base_query} {year} 股价数据 收盘价 年度表现"
                    else:
                        year_query = f"{base_query} {year} stock price data closing price annual"
            
            if "qs" in query_lower and ("排名" in original_query or "ranking" in query_lower):
                if universities:
                    for uni in universities:
                        year_query = f"QS world university rankings {year} {uni}"
                        try:
                            search_kwargs = {
                                "num_results": max(2, num_search_results // (len(selected_years) * len(universities))),
                                "freshness": None, # Ignore freshness for historical search
                                "date_restrict": None, # Ignore date_restrict for historical search
                            }
                            if google_client:
                                pass
                            
                            year_hits = run_granular_search(year_query, **search_kwargs)
                            granular_hits.extend(year_hits)
                            time.sleep(1)  # Avoid rate limits
                        except Exception as e:
                            logger.warning(f"Year {year} search failed: {e}")
                    continue
                else:
                    year_query = f"QS world university rankings {year}"
            
            try:
                search_kwargs = {
                    "num_results": max(3, num_search_results // len(selected_years)),
                    "freshness": None, # Ignore freshness for historical search
                    "date_restrict": None, # Ignore date_restrict for historical search
                }
                if google_client:
                    pass
                    
                year_hits = run_granular_search(year_query, **search_kwargs)
                logger.info(f"Year {year} search found {len(year_hits)} hits.")
                granular_hits.extend(year_hits)
                time.sleep(1)  # Avoid rate limits
            except Exception as e:
                logger.warning(f"Year {year} search failed: {e}")
                
        return granular_hits
    
    def answer(
        self,
        query: str,
        *,
        search_query: Optional[str] = None,
        num_search_results: int = 5,
        per_source_limit: Optional[int] = None,
        num_retrieved_docs: int = 5,
        max_tokens: int = 5000,
        temperature: float = 0.3,
        enable_search: bool = True,
        enable_local_docs: bool = True,
        reference_limit: Optional[int] = None,
        freshness: Optional[str] = None,
        date_restrict: Optional[str] = None,
        timing_recorder: Optional[TimingRecorder] = None,
        images: Optional[List[Dict[str, str]]] = None,
        extra_context: Optional[str] = None,
        domain: Optional[str] = None,
        domain_result: Optional[Dict[str, Any]] = None,
        enable_domain: bool = False,
        tracer: Optional[Any] = None,
        query_plan: Optional[QueryPlan] = None,
        evidence_ledger: Optional[EvidenceLedger] = None,
        execution_trace: Optional[QueryExecutionTrace] = None,
        plan_controller: Optional[PlanController] = None,
        web_step_kind: PlanStepKind = PlanStepKind.WEB_SEARCH,
        enable_temporal_recovery: bool = True,
    ) -> Dict[str, Any]:
        """Answer a query using search + local docs RAG pipeline."""
        tracer = ensure_tracer(tracer)
        retrieval = self._retrieve_evidence(
            query,
            search_query=search_query,
            num_search_results=num_search_results,
            per_source_limit=per_source_limit,
            num_retrieved_docs=num_retrieved_docs,
            enable_search=enable_search,
            enable_local_docs=enable_local_docs,
            freshness=freshness,
            date_restrict=date_restrict,
            timing_recorder=timing_recorder,
            domain=domain,
            domain_result=domain_result,
            extra_context=extra_context,
            enable_domain=enable_domain,
            tracer=tracer,
            query_plan=query_plan,
            evidence_ledger=evidence_ledger,
            execution_trace=execution_trace,
            plan_controller=plan_controller,
            web_step_kind=web_step_kind,
            enable_temporal_recovery=enable_temporal_recovery,
        )
        evidence_items: List[EvidenceItem] = retrieval["evidence_items"]
        search_hits = evidence_items_to_search_hits(evidence_items)
        retrieved_docs = evidence_items_to_documents(evidence_items)
        domain_items = [item for item in evidence_items if item.source_type == "domain"]

        # Only a completely empty ledger is a hard pre-generation stop. A
        # limited-only ledger can support an explicitly qualified answer after
        # the recovery loop has exhausted its bounded attempts.
        preflight = None
        if query_plan is not None and evidence_ledger is not None:
            preflight = verify_evidence_plan(query_plan, evidence_ledger)
            if query_plan.analysis.requires_evidence and not evidence_ledger.entries:
                payload: Dict[str, Any] = {
                    "query": query,
                    "answer": "",
                    "search_hits": [asdict(hit) for hit in search_hits],
                    "retrieved_docs": [asdict(doc) for doc in retrieved_docs],
                    "llm_raw": None,
                    "rerank": retrieval["rerank_meta"] or None,
                    "fusion": retrieval["fusion_meta"] or None,
                    "evidence_items": [item.to_dict() for item in evidence_items],
                    "evidence_summary": build_evidence_summary(evidence_items),
                    "evidence_sources_active": retrieval["active_sources"],
                    "evidence_sources_used": retrieval["used_sources"],
                    "evidence_source_types_active": sorted(
                        {item["source_type"] for item in retrieval["active_sources"]}
                    ),
                    "evidence_source_types_used": sorted(
                        {item["source_type"] for item in retrieval["used_sources"]}
                    ),
                    "search_provider_trace": retrieval.get(
                        "search_provider_trace",
                        {"executed": [], "attempts": []},
                    ),
                    "search_api_calls": retrieval.get("search_api_calls", []),
                    "verification_precheck": preflight.to_dict(),
                    "answer_basis": "no_evidence",
                }
                if retrieval["search_error"]:
                    payload["search_error"] = retrieval["search_error"]
                if retrieval["search_warnings"]:
                    payload["search_warnings"] = retrieval["search_warnings"]
                return payload

        has_retrieval_context = bool(evidence_items)
        if has_retrieval_context:
            formatted_context = self._format_evidence_context(evidence_items)
            prompt_parts = [f"Question: {query}\n\n"]
            if formatted_context["domain"]:
                prompt_parts.append(f"Domain Evidence:\n{formatted_context['domain']}\n\n")
            if formatted_context["web"]:
                prompt_parts.append(f"Web Search Results:\n{formatted_context['web']}\n\n")
            if formatted_context["local"]:
                prompt_parts.append(f"Local Documents:\n{formatted_context['local']}\n\n")
            prompt_parts.append(
                "Based on the above information, please answer the question. "
                "If information is insufficient, acknowledge it."
            )
            user_prompt = "".join(prompt_parts)
            system_prompt = self.system_prompt
            if retrieval.get("answer_basis") == "limited_evidence":
                system_prompt = (
                    f"{system_prompt}\n\n"
                    "The available sources did not meet the required authority tier. "
                    "Answer only from this limited context and explicitly state the "
                    "source limitation and uncertainty."
                )
        else:
            user_prompt = query
            system_prompt = DEFAULT_DIRECT_FALLBACK_SYSTEM_PROMPT

        # Build messages
        messages = [
            SystemMessage(content=system_prompt),
        ]
        
        # Handle images for multimodal
        if images:
            # Check if LLM supports vision
            vision_keywords = ["grok", "gpt-4", "claude", "gemini", "glm-4v", "glm-4.5v", "claude-4.5-haiku", "vision", "minimax"]
            model_name = getattr(self.llm, 'model_name', '')
            is_vision_model = any(k in model_name.lower() for k in vision_keywords)
            
            if is_vision_model:
                content_list = [{"type": "text", "text": user_prompt}]
                for img in images:
                    b64 = img.get("base64", "")
                    if "," in b64:
                        b64 = b64.split(",")[1]
                    mime = img.get("mime_type", "image/jpeg")
                    content_list.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"}
                    })
                messages.append(HumanMessage(content=content_list))
            else:
                # For non-vision models, just send the original user prompt
                # The system prompt should contain information about images and any vision metadata
                messages.append(HumanMessage(content=user_prompt))
        else:
            messages.append(HumanMessage(content=user_prompt))
        
        # Generate response
        tracer.begin("generate", "生成回答")
        response_start = time.perf_counter()
        try:
            response = self.llm.invoke(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            content = response.content if hasattr(response, 'content') else str(response)
        except Exception as exc:
            tracer.error("generate", detail=str(exc)[:80])
            raise
        finally:
            if timing_recorder:
                duration_ms = (time.perf_counter() - response_start) * 1000
                timing_recorder.record_llm_call(
                    label="search_rag_answer",
                    duration_ms=duration_ms,
                    provider=getattr(self.llm, "provider", None),
                    model=getattr(self.llm, "model_name", None),
                )
        _provider = getattr(self.llm, "provider", None)
        _model_name = getattr(self.llm, "model_name", None)
        _model_suffix = f"{_provider}/{_model_name}" if _provider and _model_name else (_provider or _model_name)
        tracer.end("generate", detail=_model_suffix or None)
        
        # Build answer with references
        answer = content if isinstance(content, str) else str(content or "")
        limited_evidence_fallback = False
        if not answer.strip() and retrieval.get("answer_basis") == "limited_evidence":
            answer = self._qualified_limited_evidence_notice(query)
            limited_evidence_fallback = True
        reference_hits = search_hits if reference_limit is None else search_hits[:reference_limit]
        
        if answer:
            if reference_hits:
                answer += "\n\n**网络来源：**\n"
                for idx, hit in enumerate(reference_hits, 1):
                    title = hit.title or f"Result {idx}"
                    url = hit.url or ""
                    bullet = f"{idx}. [{title}]({url})" if url else f"{idx}. {title}"
                    answer += f"{bullet}\n"
            
            if retrieved_docs:
                answer += "\n\n**本地文档来源：**\n"
                for idx, doc in enumerate(retrieved_docs, 1):
                    source = doc.source or f"文档 {idx}"
                    answer += f"{idx}. {source}\n"

            if domain_items:
                answer += "\n\n**领域来源：**\n"
                for idx, item in enumerate(domain_items, 1):
                    answer += f"{idx}. {item.title or 'Domain Evidence'} | {normalize_reference_label(item)}\n"
        
        # Build response
        payload: Dict[str, Any] = {
            "query": query,
            "answer": answer,
            "search_hits": [asdict(hit) for hit in search_hits],
            "retrieved_docs": [asdict(doc) for doc in retrieved_docs],
            "llm_raw": response.response_metadata if hasattr(response, "response_metadata") else None,
            "rerank": retrieval["rerank_meta"] or None,
            "fusion": retrieval["fusion_meta"] or None,
            "evidence_items": [item.to_dict() for item in evidence_items],
            "evidence_summary": build_evidence_summary(evidence_items),
            "evidence_sources_active": retrieval["active_sources"],
            "evidence_sources_used": retrieval["used_sources"],
            "evidence_source_types_active": sorted({item["source_type"] for item in retrieval["active_sources"]}),
            "evidence_source_types_used": sorted({item["source_type"] for item in retrieval["used_sources"]}),
            "search_provider_trace": retrieval.get(
                "search_provider_trace",
                {"executed": [], "attempts": []},
            ),
            "search_api_calls": retrieval.get("search_api_calls", []),
            "answer_basis": retrieval.get("answer_basis"),
            "limited_evidence_fallback": limited_evidence_fallback,
        }
        
        if retrieval["search_error"]:
            payload["search_error"] = retrieval["search_error"]
        if retrieval["search_warnings"]:
            payload["search_warnings"] = retrieval["search_warnings"]
        
        return payload
    
    def answer_stream(
        self,
        query: str,
        *,
        search_query: Optional[str] = None,
        num_search_results: int = 5,
        per_source_limit: Optional[int] = None,
        num_retrieved_docs: int = 5,
        max_tokens: int = 5000,
        temperature: float = 0.3,
        enable_search: bool = True,
        enable_local_docs: bool = True,
        reference_limit: Optional[int] = None,
        freshness: Optional[str] = None,
        date_restrict: Optional[str] = None,
        timing_recorder: Optional[TimingRecorder] = None,
        domain: Optional[str] = None,
        domain_result: Optional[Dict[str, Any]] = None,
        enable_domain: bool = False,
    ) -> Iterator[str]:
        """Stream answer using search + local docs RAG pipeline."""
        import json

        retrieval = self._retrieve_evidence(
            query,
            search_query=search_query,
            num_search_results=num_search_results,
            per_source_limit=per_source_limit,
            num_retrieved_docs=num_retrieved_docs,
            enable_search=enable_search,
            enable_local_docs=enable_local_docs,
            freshness=freshness,
            date_restrict=date_restrict,
            timing_recorder=timing_recorder,
            domain=domain,
            domain_result=domain_result,
            enable_domain=enable_domain,
        )
        evidence_items: List[EvidenceItem] = retrieval["evidence_items"]
        search_hits = evidence_items_to_search_hits(evidence_items)
        retrieved_docs = evidence_items_to_documents(evidence_items)

        preliminary = {
            "query": query,
            "search_hits": [asdict(hit) for hit in search_hits],
            "retrieved_docs": [asdict(doc) for doc in retrieved_docs],
            "rerank": retrieval["rerank_meta"] or None,
            "fusion": retrieval["fusion_meta"] or None,
            "search_query": retrieval["effective_query"],
            "evidence_items": [item.to_dict() for item in evidence_items],
            "evidence_sources_active": retrieval["active_sources"],
            "evidence_sources_used": retrieval["used_sources"],
        }
        yield json.dumps({"type": "preliminary", "data": preliminary})
        
        # Build prompt
        formatted_context = self._format_evidence_context(evidence_items)
        prompt_parts = [f"Question: {query}\n\n"]
        if formatted_context["domain"]:
            prompt_parts.append(f"Domain Evidence:\n{formatted_context['domain']}\n\n")
        if formatted_context["web"]:
            prompt_parts.append(f"Web Search Results:\n{formatted_context['web']}\n\n")
        if formatted_context["local"]:
            prompt_parts.append(f"Local Documents:\n{formatted_context['local']}\n\n")
        prompt_parts.append("Answer the question based on the above information.")
        
        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content="".join(prompt_parts)),
        ]
        
        # Stream response
        response_start = time.perf_counter()
        full_answer = ""
        
        try:
            for chunk in self.llm.stream(messages, max_tokens=max_tokens, temperature=temperature):
                content = chunk.content if hasattr(chunk, 'content') else str(chunk)
                if content:
                    full_answer += content
                    yield json.dumps({"type": "content", "data": content})
        finally:
            if timing_recorder:
                duration_ms = (time.perf_counter() - response_start) * 1000
                timing_recorder.record_llm_call(
                    label="search_rag_answer_stream",
                    duration_ms=duration_ms,
                    provider=getattr(self.llm, "provider", None),
                    model=getattr(self.llm, "model_name", None),
                )
        
        # Yield references
        reference_hits = search_hits if reference_limit is None else search_hits[:reference_limit]
        if reference_hits:
            ref_text = "\n\n**网络来源：**\n"
            for idx, hit in enumerate(reference_hits, 1):
                title = hit.title or f"Result {idx}"
                url = hit.url or ""
                bullet = f"{idx}. [{title}]({url})" if url else f"{idx}. {title}"
                ref_text += f"{bullet}\n"
            yield json.dumps({"type": "references", "data": ref_text})
        
        if retrieved_docs:
            ref_text = "\n\n**本地文档来源：**\n"
            for idx, doc in enumerate(retrieved_docs, 1):
                source = doc.source or f"文档 {idx}"
                ref_text += f"{idx}. {source}\n"
            yield json.dumps({"type": "local_references", "data": ref_text})

        domain_items = [item for item in evidence_items if item.source_type == "domain"]
        if domain_items:
            ref_text = "\n\n**领域来源：**\n"
            for idx, item in enumerate(domain_items, 1):
                ref_text += f"{idx}. {item.title or 'Domain Evidence'} | {normalize_reference_label(item)}\n"
            yield json.dumps({"type": "domain_references", "data": ref_text})


# Factory functions for creating RAG chains
def create_local_rag_chain(
    data_path: str,
    llm: Optional[BaseChatModel] = None,
    **kwargs: Any,
) -> LocalRAGChain:
    """Create a local RAG chain.
    
    Args:
        data_path: Path to directory containing documents
        llm: LangChain chat model (created from config if not provided)
        **kwargs: Additional arguments passed to LocalRAGChain
    
    Returns:
        Configured LocalRAGChain instance
    """
    if llm is None:
        from langchain_llm import create_chat_model
        llm = create_chat_model()
    
    return LocalRAGChain(llm=llm, data_path=data_path, **kwargs)


def create_search_rag_chain(
    search_client: SearchClient,
    llm: Optional[BaseChatModel] = None,
    data_path: Optional[str] = None,
    **kwargs: Any,
) -> SearchRAGChain:
    """Create a search RAG chain.
    
    Args:
        search_client: Search client for web search
        llm: LangChain chat model (created from config if not provided)
        data_path: Optional path to local documents
        **kwargs: Additional arguments passed to SearchRAGChain
    
    Returns:
        Configured SearchRAGChain instance
    """
    if llm is None:
        from langchain_llm import create_chat_model
        llm = create_chat_model()
    
    return SearchRAGChain(
        llm=llm,
        search_client=search_client,
        data_path=data_path,
        **kwargs,
    )
