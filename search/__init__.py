# Search modules
from .search import (
    BraveSearchClient,
    BrightDataSERPClient,
    FirecrawlSearchClient,
    SearchClient,
    SearchHit,
    GoogleSearchClient,
    ParallelSearchClient,
    CombinedSearchClient,
    PrioritySearchClient,
    TavilySearchClient,
    FallbackSearchClient,
)
from .rerank import BaseReranker, Qwen3Reranker, RerankedHit

try:
    from .source_selector import IntelligentSourceSelector
except ImportError:  # pragma: no cover - optional runtime dependency path
    IntelligentSourceSelector = None

try:
    from .sports_api import SportsAPI
except ImportError:  # pragma: no cover - optional runtime dependency path
    SportsAPI = None

__all__ = [
    "SearchClient",
    "SearchHit",
    "BrightDataSERPClient",
    "BraveSearchClient",
    "FirecrawlSearchClient",
    "TavilySearchClient",
    "ParallelSearchClient",
    "GoogleSearchClient",
    "PrioritySearchClient",
    "CombinedSearchClient",
    "FallbackSearchClient",
    "BaseReranker",
    "Qwen3Reranker",
    "RerankedHit",
    "IntelligentSourceSelector",
    "SportsAPI",
]
