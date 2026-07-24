"""LangChain-compatible search tools for web search providers."""

from __future__ import annotations

import os
import sys
import json
from typing import Any, Dict, List, Optional, Type, Union

from langchain_core.callbacks import CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from search.search import (
    CombinedSearchClient,
    BraveSearchClient,
    BrightDataSERPClient,
    FirecrawlSearchClient,
    GoogleSearchClient,
    ParallelSearchClient,
    PrioritySearchClient,
    SearchClient,
    SearchHit,
    TavilySearchClient,
)
from utils.config_validation import configured_value


class WebSearchInput(BaseModel):
    """Input schema for web search tools."""
    
    query: str = Field(description="The search query to execute")
    num_results: int = Field(default=5, description="Number of results to return")


class WebSearchTool(BaseTool):
    """LangChain tool wrapper for web search.
    
    Wraps any configured SearchClient implementation
    as a LangChain tool that can be used with agents.
    """
    
    name: str = "web_search"
    description: str = (
        "Useful for searching the web for current information. "
        "Input should be a search query string. "
        "Returns a list of search results with titles, URLs, and snippets."
    )
    args_schema: Type[BaseModel] = WebSearchInput
    
    search_client: SearchClient = Field(exclude=True)
    return_direct: bool = False
    
    class Config:
        arbitrary_types_allowed = True

    def __init__(self, search_client: SearchClient, **kwargs: Any) -> None:
        super().__init__(search_client=search_client, **kwargs)

    def _run(
        self,
        query: str,
        num_results: int = 5,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Execute the search and return formatted results."""
        try:
            hits = self.search_client.search(query, num_results=num_results)
            return self._format_results(hits)
        except Exception as exc:
            return f"Search failed: {exc}"

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
    
    def search_raw(
        self,
        query: str,
        num_results: int = 5,
        **kwargs: Any,
    ) -> List[SearchHit]:
        """Execute search and return raw SearchHit objects."""
        return self.search_client.search(query, num_results=num_results, **kwargs)


class BrightDataSearchTool(WebSearchTool):
    """LangChain tool for Bright Data SERP search."""
    
    name: str = "brightdata_search"
    description: str = (
        "Search the web using Bright Data SERP. "
        "Useful for Google-style search results proxied through Bright Data."
    )

    def __init__(self, api_token: str, zone: str, **kwargs: Any) -> None:
        client = BrightDataSERPClient(api_token=api_token, zone=zone)
        super().__init__(search_client=client, **kwargs)


class BraveSearchTool(WebSearchTool):
    """LangChain tool for Brave Search."""

    name: str = "brave_search"
    description: str = (
        "Search the web using Brave Search. "
        "Preferred general web search path with primary and fallback keys."
    )

    def __init__(self, primary_api_key: str, secondary_api_key: Optional[str] = None, **kwargs: Any) -> None:
        client = BraveSearchClient(
            primary_api_key=primary_api_key,
            secondary_api_key=secondary_api_key,
        )
        super().__init__(search_client=client, **kwargs)


class GoogleSearchTool(WebSearchTool):
    """LangChain tool for Google Custom Search."""
    
    name: str = "google_search"
    description: str = (
        "Search the web using Google Custom Search API. "
        "Provides authoritative search results from Google."
    )

    def __init__(self, api_key: str, cx: str, **kwargs: Any) -> None:
        client = GoogleSearchClient(api_key=api_key, cx=cx)
        super().__init__(search_client=client, **kwargs)





class CombinedSearchTool(WebSearchTool):
    """LangChain tool that combines multiple search providers."""
    
    name: str = "combined_search"
    description: str = (
        "Search the web using multiple search providers simultaneously. "
        "Aggregates and deduplicates results from all available sources."
    )

    def __init__(self, clients: List[SearchClient], **kwargs: Any) -> None:
        combined_client = CombinedSearchClient(clients)
        super().__init__(search_client=combined_client, **kwargs)


# Factory function to create search tool from config
def create_search_tool_from_config(config: Dict[str, Any]) -> Optional[WebSearchTool]:
    """Create a search tool from configuration dictionary.
    
    Args:
        config: Configuration dictionary (from config.json)
    
    Returns:
        WebSearchTool instance or None if no search providers configured
    """
    clients: List[SearchClient] = []
    fallback_clients: List[SearchClient] = []
    brave_client: Optional[SearchClient] = None

    brave_cfg = config.get("braveSearch") or {}
    brave_primary_key = configured_value(brave_cfg.get("primary_api_key"))
    brave_secondary_key = configured_value(brave_cfg.get("secondary_api_key"))
    if brave_primary_key:
        try:
            brave_client = BraveSearchClient(
                primary_api_key=brave_primary_key,
                secondary_api_key=brave_secondary_key or None,
                base_url=(brave_cfg.get("base_url") or "https://api.search.brave.com/res/v1/web/search"),
                timeout=int(brave_cfg.get("timeout", 15)),
                rps=float(brave_cfg.get("rps", 1)),
                secondary_rps=float(
                    brave_cfg.get("secondary_rps", brave_cfg.get("rps", 1))
                ),
                monthly_limit=int(brave_cfg.get("monthly_limit", 2000)),
                usage_log_path=str(brave_cfg.get("usage_log_path") or "runtime/brave_search_usage.jsonl"),
            )
        except Exception as exc:
            print(f"[search tool] Brave Search disabled: {exc}")

    firecrawl_cfg = config.get("firecrawlSearch") or {}
    firecrawl_key = configured_value(firecrawl_cfg.get("api_key"))
    if firecrawl_key:
        try:
            fallback_clients.append(
                FirecrawlSearchClient(
                    api_key=firecrawl_key,
                    base_url=(
                        firecrawl_cfg.get("base_url")
                        or "https://api.firecrawl.dev/v2/search"
                    ),
                    timeout=int(firecrawl_cfg.get("timeout", 30)),
                    search_depth=firecrawl_cfg.get("search_depth"),
                )
            )
        except Exception as exc:
            print(f"[search tool] Firecrawl disabled: {exc}")

    tavily_cfg = config.get("tavilySearch") or {}
    tavily_key = configured_value(tavily_cfg.get("api_key"))
    if tavily_key:
        try:
            fallback_clients.append(
                TavilySearchClient(
                    api_key=tavily_key,
                    base_url=(tavily_cfg.get("base_url") or "https://api.tavily.com/search"),
                    timeout=int(tavily_cfg.get("timeout", 20)),
                    search_depth=str(tavily_cfg.get("search_depth") or "basic"),
                )
            )
        except Exception as exc:
            print(f"[search tool] Tavily disabled: {exc}")

    parallel_cfg = config.get("parallelSearch") or {}
    parallel_key = configured_value(parallel_cfg.get("api_key"))
    if parallel_key:
        try:
            fallback_clients.append(
                ParallelSearchClient(
                    api_key=parallel_key,
                    base_url=(
                        parallel_cfg.get("base_url")
                        or "https://api.parallel.ai/v1beta/search"
                    ),
                    timeout=int(parallel_cfg.get("timeout", 30)),
                    mode=str(parallel_cfg.get("mode") or "fast"),
                    max_chars_per_result=int(
                        parallel_cfg.get("max_chars_per_result", 1500)
                    ),
                )
            )
        except Exception as exc:
            print(f"[search tool] Parallel disabled: {exc}")

    bright_cfg = config.get("brightDataSearch") or {}
    bright_token = configured_value(bright_cfg.get("api_token"))
    bright_zone = (bright_cfg.get("zone") or "").strip()
    if bright_token and bright_zone:
        try:
            fallback_clients.append(
                BrightDataSERPClient(
                    api_token=bright_token,
                    zone=bright_zone,
                    base_url=(bright_cfg.get("base_url") or "https://api.brightdata.com/request"),
                    timeout=int(bright_cfg.get("timeout", 20)),
                    search_url_template=str(
                        bright_cfg.get("search_url_template")
                        or "https://www.google.com/search?q={query}"
                    ),
                )
            )
        except Exception as exc:
            print(f"[search tool] Bright Data disabled: {exc}")
    
    # Google Custom Search
    google_cfg = config.get("googleSearch") or {}
    google_key = configured_value(google_cfg.get("api_key") or config.get("GOOGLE_API_KEY"))
    google_cx = configured_value(google_cfg.get("cx") or config.get("GOOGLE_CX"))
    if google_key and google_cx:
        try:
            google_kwargs: Dict[str, Any] = {}
            if google_cfg.get("gl"):
                google_kwargs["gl"] = google_cfg["gl"]
            if google_cfg.get("lr"):
                google_kwargs["lr"] = google_cfg["lr"]
            fallback_clients.append(GoogleSearchClient(api_key=google_key, cx=google_cx, **google_kwargs))
        except Exception as exc:
            print(f"[search tool] Google Search disabled: {exc}")
    
    if brave_client is not None:
        clients.append(brave_client)
        if fallback_clients:
            if len(fallback_clients) > 1:
                clients.append(CombinedSearchClient(fallback_clients))
            else:
                clients.extend(fallback_clients)
    else:
        clients.extend(fallback_clients)

    if not clients:
        return None
    
    if len(clients) == 1:
        return WebSearchTool(search_client=clients[0])
    if brave_client is not None:
        return WebSearchTool(search_client=PrioritySearchClient(clients))
    return CombinedSearchTool(clients=clients)


# Retriever wrapper for RAG use
class SearchRetriever:
    """Wrapper that converts search results to LangChain Documents for RAG.
    
    This allows using web search as a retriever in LangChain chains.
    """
    
    def __init__(
        self,
        search_tool: WebSearchTool,
        k: int = 5,
        include_metadata: bool = True,
    ) -> None:
        self.search_tool = search_tool
        self.k = k
        self.include_metadata = include_metadata
    
    def get_relevant_documents(self, query: str) -> List:
        """Retrieve documents relevant to the query."""
        from langchain_core.documents import Document as LCDocument
        
        hits = self.search_tool.search_raw(query, num_results=self.k)
        
        documents = []
        for hit in hits:
            content = f"{hit.title or ''}\n\n{hit.snippet or ''}"
            metadata = {
                "source": hit.url or "",
                "title": hit.title or "",
            } if self.include_metadata else {}
            
            documents.append(LCDocument(page_content=content, metadata=metadata))
        
        return documents
    
    async def aget_relevant_documents(self, query: str) -> List:
        """Async version of get_relevant_documents."""
        # For now, just call sync version
        return self.get_relevant_documents(query)
