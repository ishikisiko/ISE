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
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, Union

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
    normalize_reference_label,
)
from langchain.langchain_support import Document, FileReader, LangChainVectorStore
from langchain.langchain_tools import SearchRetriever, WebSearchTool
from search.search import SearchClient, SearchHit, GoogleSearchClient
from utils.timing_utils import TimingRecorder
from utils.workflow_trace import ensure_tracer
from utils.query_config import (
    TEMPORAL_CHANGE_KEYWORDS,
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

        self.web_source = WebEvidenceSource(search_client)
        self.local_source = LocalEvidenceSource(
            vector_store=self.vector_store,
            data_path=data_path,
            embedding_model=embedding_model,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            config=self.config,
        )
        self.domain_source = DomainEvidenceSource(source_selector)

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

        domain_items, domain_result = self._collect_domain_evidence(
            query,
            domain=domain,
            domain_result=domain_result,
            extra_context=extra_context,
            enable_domain=enable_domain,
            timing_recorder=timing_recorder,
        )
        if domain_items:
            active_sources.append(self.domain_source.describe_with_domain(domain or domain_items[0].metadata.get("domain")))
            evidence_items.extend(domain_items)

        if enable_search:
            active_sources.append(self.web_source.describe())
            tracer.begin("search", "联网检索", detail=effective_query)
            search_timings: List[Dict[str, Any]] = []
            hits_count = 0
            try:
                per_source_cap = per_source_limit or num_search_results
                fetch_limit = num_search_results
                if self.reranker and hasattr(self.search_client, "clients"):
                    fetch_limit = per_source_cap * len(self.search_client.clients)

                search_items = self.web_source.retrieve(
                    effective_query,
                    RetrievalOptions(
                        num_results=fetch_limit,
                        per_source_limit=per_source_cap,
                        freshness=freshness,
                        date_restrict=date_restrict,
                    ),
                )
                hits = evidence_items_to_search_hits(search_items)

                if self._is_temporal_change_query(query):
                    missing_years = self._detect_missing_years(query, hits)
                    if missing_years:
                        logger.info(
                            "Insufficient historical data found (missing: %s), performing granular search fallback.",
                            missing_years,
                        )
                        granular_hits = self._perform_granular_search_fallback(
                            query,
                            effective_query,
                            num_search_results,
                            per_source_cap,
                            freshness,
                            date_restrict,
                            timing_recorder,
                            missing_years=missing_years,
                        )
                        hits.extend(granular_hits)

                hits, rerank_meta = self._apply_rerank(query, hits, limit=num_search_results)
                hits_count = len(hits)
                evidence_items.extend(self.web_source.hits_to_items(hits))
            except Exception as exc:
                search_error = str(exc)
            finally:
                timings_getter = getattr(self.search_client, "get_last_timings", None)
                if callable(timings_getter):
                    search_timings = list(timings_getter() or [])
                    if timing_recorder:
                        timing_recorder.extend_search_timings(search_timings)

            get_last_errors = getattr(self.search_client, "get_last_errors", None)
            if callable(get_last_errors):
                errors = get_last_errors() or []
                for item in errors:
                    source = str(item.get("source") or "搜索服务")
                    detail = str(item.get("error") or "未知错误")
                    search_warnings.append(f"{source} 出现异常：{detail}")

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
            local_items = self.local_source.retrieve(
                query,
                RetrievalOptions(num_results=num_retrieved_docs),
            )
            evidence_items.extend(local_items)
            tracer.end("local", detail=f"{len(local_items)} 个片段")

        tracer.begin("rerank", "证据重排融合")
        evidence_items, fusion_meta = self._dedupe_and_rank_evidence(evidence_items)
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

    def _is_temporal_change_query(self, query: str) -> bool:
        """Check if query is related to temporal changes."""
        query_lower = query.lower()
        return any(keyword in query_lower for keyword in TEMPORAL_CHANGE_KEYWORDS)

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

    def _detect_missing_years(self, query: str, hits: List[SearchHit]) -> List[str]:
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
        
        # Also check if it's a temporal change query
        if not is_time_query and self._is_temporal_change_query(query):
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
        missing_years: Optional[List[str]] = None
    ) -> List[SearchHit]:
        """Perform granular search for historical data."""
        
        granular_hits = []
        google_client = self._check_google_client_availability()
        active_client = google_client if google_client else self.search_client
        
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
                                sq_hits = active_client.search(sq, **search_kwargs)
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
                            
                            year_hits = active_client.search(year_query, **search_kwargs)
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
                    
                year_hits = active_client.search(year_query, **search_kwargs)
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
        )
        evidence_items: List[EvidenceItem] = retrieval["evidence_items"]
        search_hits = evidence_items_to_search_hits(evidence_items)
        retrieved_docs = evidence_items_to_documents(evidence_items)
        domain_items = [item for item in evidence_items if item.source_type == "domain"]

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
        answer = content
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
