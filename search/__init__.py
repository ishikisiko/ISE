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
from .reference_fetch import (
    FIRECRAWL_SCRAPE_URL,
    PARALLEL_EXTRACT_URL,
    FirecrawlScrapeClient,
    ParallelExtractClient,
    ReferenceContent,
    ReferenceExtraction,
    ReferenceExtractor,
    ReferenceExtractorRouter,
    ReferenceFailure,
    build_reference_extractors,
)

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
    "FIRECRAWL_SCRAPE_URL",
    "PARALLEL_EXTRACT_URL",
    "FirecrawlScrapeClient",
    "ParallelExtractClient",
    "ReferenceContent",
    "ReferenceExtraction",
    "ReferenceExtractor",
    "ReferenceExtractorRouter",
    "ReferenceFailure",
    "build_reference_extractors",
    "BaseReranker",
    "Qwen3Reranker",
    "RerankedHit",
    "IntelligentSourceSelector",
    "SportsAPI",
]
