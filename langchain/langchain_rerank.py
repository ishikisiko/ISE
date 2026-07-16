"""LangChain-compatible document compressors for reranking.

This module provides reranker implementations as LangChain DocumentCompressors,
enabling seamless integration with LangChain's retrieval pipelines.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

import requests
from langchain_core.callbacks import Callbacks
from langchain_core.documents import Document as LCDocument
from langchain_core.documents.compressor import BaseDocumentCompressor
from pydantic import Field

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from search.search import SearchHit
from utils.config_validation import configured_value


class Qwen3DocumentCompressor(BaseDocumentCompressor):
    """LangChain document compressor using Qwen3 rerank API.
    
    This compressor uses the DashScope Qwen3 rerank endpoint to rerank
    documents based on relevance to the query.
    """
    
    api_key: str = Field(description="DashScope API key")
    model: str = Field(default="qwen3-rerank", description="Model name")
    base_url: str = Field(
        default="https://dashscope.aliyuncs.com/api/v1/services/rerank",
        description="API base URL"
    )
    request_timeout: int = Field(default=15, description="Request timeout in seconds")
    top_n: Optional[int] = Field(default=None, description="Number of top results to return")
    min_score: float = Field(default=0.0, description="Minimum relevance score threshold")
    
    class Config:
        arbitrary_types_allowed = True

    def compress_documents(
        self,
        documents: Sequence[LCDocument],
        query: str,
        callbacks: Optional[Callbacks] = None,
    ) -> Sequence[LCDocument]:
        """Rerank documents using Qwen3 rerank API."""
        if not documents:
            return []
        
        # Prepare document texts
        doc_texts = []
        for idx, doc in enumerate(documents):
            text = doc.page_content.strip()
            if not text:
                text = doc.metadata.get("title", f"Document {idx + 1}")
            doc_texts.append(text)
        
        # Call rerank API
        endpoint = f"{self.base_url.rstrip('/')}/text-rerank/text-rerank"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        
        payload = {
            "model": self.model,
            "input": {
                "query": query,
                "documents": doc_texts,
            },
            "parameters": {
                "return_documents": True,
                "top_n": self.top_n or len(doc_texts),
            }
        }
        
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            # On failure, return original documents
            print(f"Rerank API failed: {exc}")
            return list(documents)
        
        # Extract ranking results
        ranking = self._extract_ranking(data)
        if not ranking:
            return list(documents)
        
        # Reorder documents based on ranking
        reranked: List[LCDocument] = []
        docs_list = list(documents)
        
        for entry in ranking:
            doc_id = entry.get("id")
            score = entry.get("score")
            
            if doc_id is None or not isinstance(doc_id, int):
                continue
            if doc_id >= len(docs_list):
                continue
            
            # Apply minimum score filter
            if score is not None and score < self.min_score:
                continue
            
            doc = docs_list[doc_id]
            # Add relevance score to metadata
            doc.metadata["relevance_score"] = score
            reranked.append(doc)
        
        return reranked

    @staticmethod
    def _extract_ranking(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract ranking from API response."""
        candidates = [
            payload.get("output", {}).get("results"),
            payload.get("output", {}).get("rankings"),
            payload.get("data", {}).get("results"),
            payload.get("results"),
        ]
        
        for candidate in candidates:
            if not candidate:
                continue
            
            parsed = []
            for item in candidate:
                if not isinstance(item, dict):
                    continue
                
                document = item.get("document") or {}
                doc_id = (
                    item.get("index")
                    or document.get("index")
                    or document.get("id")
                    or item.get("document_id")
                    or item.get("id")
                )
                
                score = (
                    item.get("relevance_score")
                    or item.get("score")
                    or document.get("score")
                )
                
                if doc_id is None:
                    continue
                
                try:
                    doc_id = int(doc_id)
                except (TypeError, ValueError):
                    continue
                
                try:
                    score = float(score) if score is not None else None
                except (TypeError, ValueError):
                    score = None
                
                parsed.append({"id": doc_id, "score": score})
            
            if parsed:
                return parsed
        
        return []


class SearchHitReranker:
    """Reranker specifically for SearchHit objects.
    
    This provides a convenient interface for reranking search results
    while maintaining compatibility with the existing SearchHit type.
    """
    
    def __init__(
        self,
        compressor: BaseDocumentCompressor,
        max_per_domain: int = 1,
        min_score: float = 0.0,
    ) -> None:
        self.compressor = compressor
        self.max_per_domain = max(1, max_per_domain)
        self.min_score = min_score
    
    def rerank(
        self,
        query: str,
        hits: List[SearchHit],
        limit: Optional[int] = None,
    ) -> List[SearchHit]:
        """Rerank search hits and apply domain diversity filtering."""
        if not hits:
            return []
        
        # Convert SearchHits to LangChain Documents
        documents = []
        for hit in hits:
            content = f"{hit.title or ''}\n{hit.url or ''}\n{hit.snippet or ''}"
            doc = LCDocument(
                page_content=content.strip(),
                metadata={
                    "title": hit.title or "",
                    "url": hit.url or "",
                    "snippet": hit.snippet or "",
                }
            )
            documents.append(doc)
        
        # Rerank using compressor
        reranked_docs = self.compressor.compress_documents(documents, query)
        
        # Convert back to SearchHits with domain diversity
        from urllib.parse import urlparse
        
        result: List[SearchHit] = []
        domain_counts: Dict[str, int] = {}
        max_results = limit or len(hits)
        
        for doc in reranked_docs:
            score = doc.metadata.get("relevance_score")
            if score is not None and score < self.min_score:
                continue
            
            url = doc.metadata.get("url", "")
            domain = urlparse(url).netloc if url else None
            
            if domain and domain_counts.get(domain, 0) >= self.max_per_domain:
                continue
            
            hit = SearchHit(
                title=doc.metadata.get("title", ""),
                url=url,
                snippet=doc.metadata.get("snippet", ""),
            )
            result.append(hit)
            
            if domain:
                domain_counts[domain] = domain_counts.get(domain, 0) + 1
            
            if len(result) >= max_results:
                break
        
        return result


def create_qwen3_compressor(
    api_key: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Qwen3DocumentCompressor:
    """Factory function to create Qwen3 reranker from configuration.
    
    Args:
        api_key: DashScope API key (loaded from config if not provided)
        config: Configuration dictionary
        **kwargs: Additional arguments passed to Qwen3DocumentCompressor
    
    Returns:
        Configured Qwen3DocumentCompressor instance
    """
    if config is None:
        import json
        import os
        config_path = os.getenv("NLP_CONFIG_PATH", "config.json")
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        else:
            config = {}
    
    rerank_config = config.get("rerank", {})
    qwen_config = (
        rerank_config.get("providers", {}).get("qwen")
        or rerank_config.get("qwen")
        or {}
    )
    
    if api_key is None:
        api_key = qwen_config.get("api_key", "")
    api_key = configured_value(api_key)

    if not api_key:
        raise ValueError("DashScope API key is required for Qwen3 reranking.")
    
    return Qwen3DocumentCompressor(
        api_key=api_key,
        model=kwargs.get("model", qwen_config.get("model", "qwen3-rerank")),
        base_url=kwargs.get("base_url", qwen_config.get("base_url", Qwen3DocumentCompressor.model_fields["base_url"].default)),
        request_timeout=kwargs.get("request_timeout", qwen_config.get("timeout", 15)),
        min_score=kwargs.get("min_score", rerank_config.get("min_score", 0.0)),
        **{k: v for k, v in kwargs.items() if k not in ("model", "base_url", "request_timeout", "min_score")},
    )


# Convenience wrapper to create reranker from config
def create_search_reranker(
    config: Optional[Dict[str, Any]] = None,
    max_per_domain: int = 1,
    min_score: float = 0.0,
) -> Optional[SearchHitReranker]:
    """Create a search hit reranker from configuration.
    
    Args:
        config: Configuration dictionary
        max_per_domain: Maximum results per domain
        min_score: Minimum relevance score threshold
    
    Returns:
        SearchHitReranker instance or None if not configured
    """
    if config is None:
        import json
        import os
        config_path = os.getenv("NLP_CONFIG_PATH", "config.json")
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        else:
            config = {}
    
    rerank_config = config.get("rerank", {})
    if rerank_config.get("enabled") is False:
        return None
    provider = config.get("RERANK_PROVIDER") or rerank_config.get("provider")
    
    if not provider:
        return None
    
    provider_key = provider.lower()
    
    if provider_key in {"qwen", "qwen3", "qwen3-rerank"}:
        try:
            compressor = create_qwen3_compressor(config=config, min_score=min_score)
            return SearchHitReranker(
                compressor=compressor,
                max_per_domain=max_per_domain,
                min_score=min_score,
            )
        except ValueError as exc:
            print(f"Failed to create Qwen3 reranker: {exc}")
            return None
    
    print(f"Unsupported rerank provider: {provider}")
    return None
