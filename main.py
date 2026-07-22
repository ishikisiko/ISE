import argparse
import json
import sys
import os
import uuid
from typing import Optional, Tuple, Dict, Any, List, Union, Set

# Add project directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm.api import LLMClient, HKGAIClient, resolve_model_api_style
from search.search import (
    CombinedSearchClient,
    BraveSearchClient,
    BrightDataSERPClient,
    FirecrawlSearchClient,
    GoogleSearchClient,
    ParallelSearchClient,
    PrioritySearchClient,
    SearchClient,
    TavilySearchClient,
)
from search.rerank import BaseReranker, Qwen3Reranker
from orchestrators.smart_orchestrator import SmartSearchOrchestrator
from utils.chunking import resolve_chunk_settings
from utils.config_validation import configured_value
from utils.temperature_config import get_temperature_for_task

# Import LangChain components for optional use
try:
    from langchain.langchain_llm import create_chat_model, LangChainLLMWrapper
    from langchain.langchain_orchestrator import create_langchain_orchestrator, LangChainOrchestrator
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False

ZAI_ANTHROPIC_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/anthropic"


def load_runtime_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load runtime configuration from an explicit path, env override, or config.json."""
    resolved_path = config_path or os.environ.get("NLP_CONFIG_PATH") or "config.json"
    with open(resolved_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _normalize_provider_base_url(provider: str, base_url: Optional[str]) -> Optional[str]:
    """Apply provider-specific normalization for base URLs.
    This function is intentionally defined near the top so it is available to
    `build_llm_client` which is executed during startup.
    """
    if not base_url:
        if provider == "zai":
            return ZAI_ANTHROPIC_DEFAULT_BASE_URL
        return base_url

    cleaned = str(base_url).strip()
    if not cleaned:
        return ZAI_ANTHROPIC_DEFAULT_BASE_URL if provider == "zai" else cleaned

    cleaned = cleaned.rstrip("/")

    if provider == "zai":
        # If the URL already points to an Anthropic-compatible base, preserve it
        if "/anthropic" in cleaned:
            return cleaned
        # Convert older coding/paas base to paas, but keep /anthropic paths alone
        if "/coding/paas" in cleaned:
            cleaned = cleaned.replace("/coding/paas", "/paas")
        if not cleaned.endswith("/v4"):
            cleaned = cleaned.rstrip("/") + "/v4" if cleaned.endswith("/paas") else cleaned
        return cleaned

    return cleaned


def build_search_client(
    config_or_key: Union[Dict[str, Any], str, None],
    *,
    sources: Optional[List[str]] = None,
) -> Optional[SearchClient]:
    """Build a search client from config supporting Brave, Bright Data, and Google."""

    allowed_sources = {
        "brave",
        "firecrawl",
        "tavily",
        "parallel",
        "brightdata",
        "google",
    }
    requested_order: Optional[List[str]] = None
    requested_lookup: Optional[Set[str]] = None
    if sources is not None:
        ordered: List[str] = []
        seen: Set[str] = set()
        for raw in sources:
            if not isinstance(raw, str):
                continue
            token = raw.strip().lower()
            if token not in allowed_sources or token in seen:
                continue
            seen.add(token)
            ordered.append(token)
        requested_order = ordered
        requested_lookup = set(ordered)

    def wants(source_id: str) -> bool:
        if requested_lookup is None:
            return True
        return source_id in requested_lookup

    def apply_metadata(
        client: SearchClient,
        *,
        active: List[SearchClient],
        configured: List[str],
        requested: Optional[List[str]],
        missing: List[str],
    ) -> SearchClient:
        active_ids = [getattr(instance, "source_id", type(instance).__name__.lower()) for instance in active]
        active_labels = [getattr(instance, "display_name", type(instance).__name__) for instance in active]
        requested_list = list(requested) if requested is not None else list(active_ids)
        setattr(client, "active_sources", active_ids)
        setattr(client, "active_source_labels", active_labels)
        setattr(client, "requested_sources", requested_list)
        setattr(client, "configured_sources", list(configured))
        setattr(client, "missing_requested_sources", list(missing))
        return client

    if isinstance(config_or_key, str):
        raise ValueError("String search backend configuration is no longer supported.")

    if not isinstance(config_or_key, dict):
        return None

    configured_flags: Dict[str, bool] = {
        "brave": False,
        "firecrawl": False,
        "tavily": False,
        "parallel": False,
        "brightdata": False,
        "google": False,
    }
    missing_requested: List[str] = []
    fallback_clients: List[SearchClient] = []
    brave_client: Optional[SearchClient] = None

    brave_cfg = config_or_key.get("braveSearch") or {}
    brave_primary_key = configured_value(brave_cfg.get("primary_api_key"))
    brave_secondary_key = configured_value(brave_cfg.get("secondary_api_key"))
    if brave_primary_key:
        configured_flags["brave"] = True
        if wants("brave"):
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
                    primary_switch_limit=int(brave_cfg.get("primary_switch_limit", 1500)),
                    usage_log_path=str(brave_cfg.get("usage_log_path") or "runtime/brave_search_usage.jsonl"),
                )
            except Exception as exc:
                print(f"[search] Brave Search disabled: {exc}")
    elif requested_lookup is not None and "brave" in requested_lookup:
        missing_requested.append("brave")

    firecrawl_cfg = config_or_key.get("firecrawlSearch") or {}
    firecrawl_key = configured_value(firecrawl_cfg.get("api_key"))
    if firecrawl_key:
        configured_flags["firecrawl"] = True
        if wants("firecrawl"):
            try:
                fallback_clients.append(
                    FirecrawlSearchClient(
                        api_key=firecrawl_key,
                        base_url=(
                            firecrawl_cfg.get("base_url")
                            or "https://api.firecrawl.dev/v2/search"
                        ),
                        timeout=int(firecrawl_cfg.get("timeout", 30)),
                    )
                )
            except Exception as exc:
                print(f"[search] Firecrawl disabled: {exc}")
    elif requested_lookup is not None and "firecrawl" in requested_lookup:
        missing_requested.append("firecrawl")

    tavily_cfg = config_or_key.get("tavilySearch") or {}
    tavily_key = configured_value(tavily_cfg.get("api_key"))
    if tavily_key:
        configured_flags["tavily"] = True
        if wants("tavily"):
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
                print(f"[search] Tavily disabled: {exc}")
    elif requested_lookup is not None and "tavily" in requested_lookup:
        missing_requested.append("tavily")

    parallel_cfg = config_or_key.get("parallelSearch") or {}
    parallel_key = configured_value(parallel_cfg.get("api_key"))
    if parallel_key:
        configured_flags["parallel"] = True
        if wants("parallel"):
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
                print(f"[search] Parallel disabled: {exc}")
    elif requested_lookup is not None and "parallel" in requested_lookup:
        missing_requested.append("parallel")

    bright_cfg = config_or_key.get("brightDataSearch") or {}
    bright_api_token = configured_value(bright_cfg.get("api_token"))
    bright_zone = (bright_cfg.get("zone") or "").strip()
    if bright_api_token and bright_zone:
        configured_flags["brightdata"] = True
        if wants("brightdata"):
            try:
                fallback_clients.append(
                    BrightDataSERPClient(
                        api_token=bright_api_token,
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
                print(f"[search] Bright Data disabled: {exc}")
    elif requested_lookup is not None and "brightdata" in requested_lookup:
        missing_requested.append("brightdata")

    # Google Custom Search JSON API
    google_cfg = config_or_key.get("googleSearch") or {}
    google_key = configured_value(google_cfg.get("api_key") or config_or_key.get("GOOGLE_API_KEY"))
    google_cx = configured_value(google_cfg.get("cx") or config_or_key.get("GOOGLE_CX"))
    if google_key and google_cx:
        configured_flags["google"] = True
        if wants("google"):
            google_kwargs: Dict[str, Any] = {}
            base_url = (google_cfg.get("base_url") or "").strip()
            if base_url:
                google_kwargs["base_url"] = base_url
            timeout_raw = google_cfg.get("timeout")
            if timeout_raw is not None:
                try:
                    google_kwargs["timeout"] = int(timeout_raw)
                except (TypeError, ValueError):
                    pass
            gl = (google_cfg.get("gl") or "").strip()
            if gl:
                google_kwargs["gl"] = gl
            lr = (google_cfg.get("lr") or "").strip()
            if lr:
                google_kwargs["lr"] = lr
            safe = (google_cfg.get("safe") or "").strip()
            if safe:
                google_kwargs["safe"] = safe

            try:
                fallback_clients.append(GoogleSearchClient(api_key=google_key, cx=google_cx, **google_kwargs))
            except Exception as exc:
                print(f"[search] Google Search disabled: {exc}")
    elif requested_lookup is not None and "google" in requested_lookup:
        missing_requested.append("google")

    configured = [source for source, flag in configured_flags.items() if flag]
    ordered_clients: List[SearchClient] = []
    metadata_clients: List[SearchClient] = []
    if brave_client is not None:
        ordered_clients.append(brave_client)
        metadata_clients.append(brave_client)

    if fallback_clients:
        metadata_clients.extend(fallback_clients)
        if requested_lookup is None and brave_client is not None and len(fallback_clients) > 1:
            ordered_clients.append(CombinedSearchClient(fallback_clients))
        elif requested_lookup is not None and "brave" in requested_lookup and len(fallback_clients) > 1:
            ordered_clients.append(CombinedSearchClient(fallback_clients))
        else:
            ordered_clients.extend(fallback_clients)

    if not ordered_clients:
        return None

    if len(ordered_clients) == 1:
        client = ordered_clients[0]
    elif brave_client is not None:
        client = PrioritySearchClient(ordered_clients)
    else:
        client = CombinedSearchClient(ordered_clients)

    return apply_metadata(
        client,
        active=metadata_clients or ordered_clients,
        configured=configured,
        requested=requested_order,
        missing=missing_requested,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Answer questions with optional web search and local RAG.")
    parser.add_argument("query", help="User question to answer.")
    parser.add_argument(
        "--data-path",
        type=str,
        default="./data",
        help="Path to the directory containing local files for local RAG mode.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=5000,
        help="Maximum number of tokens for the LLM response.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.3,
        help="Sampling temperature for the LLM.",
    )
    parser.add_argument(
        "--num-results",
        type=int,
        default=5,
        help="Number of search results to include in the prompt.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty print the JSON response.",
    )
    parser.add_argument(
        "--provider",
        type=str,
        help="Override the LLM provider or model (opencode-go, openai, anthropic, google, glm, zai, hkgai, openrouter, minimax, or a configured model ID).",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Override the LLM model (e.g., minimax/minimax-m2:free, deepseek/deepseek-r1-0528:free).",
    )
    parser.add_argument(
        "--disable-rerank",
        action="store_true",
        help="Skip search result reranking even if configured.",
    )
    parser.add_argument(
        "--search",
        choices=["on", "off"],
        default="on",
        help="Enable ('on') or disable ('off') live web search. Uploads remain available in both modes.",
    )
    parser.add_argument(
        "--use-legacy",
        action="store_true",
        help="Use legacy SmartSearchOrchestrator instead of LangChain-based orchestrator.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help="Override the local RAG chunk size for this run.",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=None,
        help="Override the local RAG chunk overlap for this run.",
    )
    parser.add_argument(
        "--conversation-id",
        type=str,
        default=None,
        help="Conversation id for multi-turn follow-ups. Omit to start a new conversation; the generated id is printed for reuse.",
    )
    return parser.parse_args()


def build_llm_client(
    config: dict,
    *,
    provider_or_model: Optional[str] = None,
    model_override: Optional[str] = None,
    llm_settings_override: Optional[Dict[str, Any]] = None,
    provider_config_override: Optional[Dict[str, Any]] = None,
) -> LLMClient:
    """Build LLM client based on provider or model configuration."""
    provider_or_model = provider_or_model or config.get("LLM_PROVIDER", "zai")
    
    # Check if it's a specific model (contains '/') and map to provider
    if "/" in provider_or_model:
        # First check if this model is in any provider's available_models
        found_provider = None
        for provider_name, provider_cfg in config.get("providers", {}).items():
            available = provider_cfg.get("available_models", [])
            if provider_or_model in available:
                found_provider = provider_name
                break
        
        if found_provider:
            provider = found_provider
            model_id = provider_or_model
        else:
            # Fallback: extract provider from model path
            provider = provider_or_model.split("/")[0]
            model_id = provider_or_model
    else:
        # Check if it's a known provider name
        supported_providers = ["opencode-go", "openai", "anthropic", "google", "glm", "zai", "hkgai", "openrouter", "minimax"]
        if provider_or_model in supported_providers:
            # It's a provider name
            provider = provider_or_model
            model_id = None
        else:
            # It might be a model ID from a provider's available_models
            # Search all providers to find which one has this model
            provider = None
            model_id = provider_or_model
            
            for provider_name, provider_cfg in config.get("providers", {}).items():
                # Check if it's the default model
                if provider_cfg.get("model") == provider_or_model:
                    provider = provider_name
                    break
                # Check if it's in available_models
                available = provider_cfg.get("available_models", [])
                if provider_or_model in available:
                    provider = provider_name
                    break
            
            if not provider:
                # Fallback: treat as provider name
                provider = provider_or_model
                model_id = None
    
    # Validate provider
    supported_providers = ["opencode-go", "openai", "anthropic", "google", "glm", "zai", "hkgai", "openrouter", "minimax"]
    if provider not in supported_providers:
        raise ValueError(f"Unsupported provider '{provider}'. Supported providers: {', '.join(supported_providers)}")
    
    if provider == "hkgai":
        # Use legacy HKGAI client for backward compatibility
        return HKGAIClient(api_key=config.get("providers", {}).get("hkgai", {}).get("api_key"))
    
    provider_config_raw = config.get("providers", {}).get(provider, {})
    if not provider_config_raw:
        raise ValueError(f"Provider '{provider}' not found in configuration")
    provider_config = dict(provider_config_raw)
    if provider_config_override:
        provider_config.update(provider_config_override)

    normalized_base = _normalize_provider_base_url(provider, provider_config.get("base_url"))
    if normalized_base and normalized_base != provider_config.get("base_url"):
        provider_config["base_url"] = normalized_base
        try:
            print(f"[llm] Normalized {provider} base URL to {normalized_base}")
        except Exception:
            pass

    # Get the model ID (use specified model or fall back to provider config)
    final_model_id = model_override or model_id or provider_config.get("model")
    if not final_model_id:
        raise ValueError(f"No model specified for provider '{provider}'")
    provider_config["model"] = final_model_id
    
    # Get global LLM settings
    base_llm_settings = config.get("llm_settings", {})
    llm_settings: Dict[str, Any]
    if isinstance(base_llm_settings, dict):
        llm_settings = dict(base_llm_settings)
    else:
        llm_settings = {}
    if isinstance(llm_settings_override, dict):
        llm_settings.update(llm_settings_override)
    default_timeout = llm_settings.get("default_timeout", 60)
    max_retries = llm_settings.get("max_retries", 3)
    backoff_factor = llm_settings.get("backoff_factor", 2.0)
    
    # Provider-specific timeout (fallback to global)
    provider_timeout = provider_config.get("request_timeout", default_timeout)
    provider_max_retries = provider_config.get("max_retries", max_retries)
    provider_backoff_factor = provider_config.get("backoff_factor", backoff_factor)
    
    # Thinking configuration (for MiniMax and other Anthropic-compatible endpoints)
    thinking_config = provider_config.get("thinking", {})
    thinking_enabled = thinking_config.get("enabled", False) if isinstance(thinking_config, dict) else False
    display_thinking = thinking_config.get("display_in_response", False) if isinstance(thinking_config, dict) else False
    
    return LLMClient(
        api_key=configured_value(provider_config.get("api_key")),
        model_id=final_model_id,
        base_url=provider_config.get("base_url"),
        request_timeout=provider_timeout,
        provider=provider,
        max_retries=provider_max_retries,
        backoff_factor=provider_backoff_factor,
        thinking_enabled=thinking_enabled,
        display_thinking=display_thinking,
        api_style=resolve_model_api_style(provider_config, final_model_id),
        model_base_urls=provider_config.get("model_base_urls")
    )


def build_domain_classifier_client(config: dict) -> Optional[LLMClient]:
    """Build a dedicated LLM client for domain classification if configured."""

    classifier_cfg = config.get("domainClassifier") or {}

    if classifier_cfg.get("enabled") is False or config.get("DOMAIN_CLASSIFIER_ENABLED") is False:
        return None

    def _normalize(value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None

    provider_override = _normalize(classifier_cfg.get("provider"))
    model_override = _normalize(classifier_cfg.get("model"))

    if provider_override is None:
        provider_override = _normalize(config.get("DOMAIN_CLASSIFIER_PROVIDER"))
    if model_override is None:
        model_override = _normalize(config.get("DOMAIN_CLASSIFIER_MODEL"))

    provider_or_model = provider_override or model_override
    if not provider_or_model:
        return None

    llm_settings_override = classifier_cfg.get("llm_settings")
    if not isinstance(llm_settings_override, dict):
        llm_settings_override = None
        fallback_settings = config.get("domain_classifier_llm_settings")
        if isinstance(fallback_settings, dict):
            llm_settings_override = fallback_settings

    provider_config_override: Optional[Dict[str, Any]] = None
    override_candidates = {
        key: classifier_cfg.get(key)
        for key in ("api_key", "base_url", "request_timeout", "max_retries", "backoff_factor")
        if classifier_cfg.get(key) is not None
    }
    if override_candidates:
        provider_config_override = override_candidates

    model_param = model_override if provider_override else None

    return build_llm_client(
        config,
        provider_or_model=provider_or_model,
        model_override=model_param,
        llm_settings_override=llm_settings_override,
        provider_config_override=provider_config_override,
    )


def build_routing_keywords_client(config: dict) -> Optional[LLMClient]:
    """Build a dedicated LLM client for routing and keyword generation if configured."""

    routing_cfg = config.get("routingAndKeywords") or {}

    if routing_cfg.get("enabled") is False or config.get("ROUTING_KEYWORDS_ENABLED") is False:
        return None

    def _normalize(value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None

    provider_override = _normalize(routing_cfg.get("provider"))
    model_override = _normalize(routing_cfg.get("model"))

    if provider_override is None:
        provider_override = _normalize(config.get("ROUTING_KEYWORDS_PROVIDER"))
    if model_override is None:
        model_override = _normalize(config.get("ROUTING_KEYWORDS_MODEL"))

    provider_or_model = provider_override or model_override
    if not provider_or_model:
        return None

    llm_settings_override = routing_cfg.get("llm_settings")
    if not isinstance(llm_settings_override, dict):
        llm_settings_override = None
        fallback_settings = config.get("routing_keywords_llm_settings")
        if isinstance(fallback_settings, dict):
            llm_settings_override = fallback_settings

    provider_config_override: Optional[Dict[str, Any]] = None
    override_candidates = {
        key: routing_cfg.get(key)
        for key in ("api_key", "base_url", "request_timeout", "max_retries", "backoff_factor")
        if routing_cfg.get(key) is not None
    }
    if override_candidates:
        provider_config_override = override_candidates

    model_param = model_override if provider_override else None

    return build_llm_client(
        config,
        provider_or_model=provider_or_model,
        model_override=model_param,
        llm_settings_override=llm_settings_override,
        provider_config_override=provider_config_override,
    )


def build_reranker(config: dict) -> Tuple[Optional[BaseReranker], Dict[str, Any]]:
    """Instantiate a reranker based on configuration."""

    rerank_config = config.get("rerank") or {}
    if rerank_config.get("enabled") is False:
        return None, rerank_config
    provider = config.get("RERANK_PROVIDER") or rerank_config.get("provider")
    if not provider:
        return None, rerank_config

    provider_key = provider.lower()

    if provider_key in {"qwen", "qwen3", "qwen3-rerank"}:
        provider_settings = (
            (rerank_config.get("providers") or {}).get("qwen")
            or rerank_config.get("qwen")
            or {}
        )
        api_key = configured_value(provider_settings.get("api_key"))
        if not api_key:
            raise ValueError("DashScope API key is required for Qwen3 reranking.")

        reranker = Qwen3Reranker(
            api_key=api_key,
            model=provider_settings.get("model", "qwen3-rerank"),
            base_url=provider_settings.get("base_url"),
            request_timeout=provider_settings.get("timeout", 15),
        )
        return reranker, rerank_config

    raise ValueError(f"Unsupported rerank provider '{provider}'.")


def main() -> None:
    args = parse_args()

    try:
        config = load_runtime_config()
    except FileNotFoundError as exc:
        missing_path = exc.filename or (os.environ.get("NLP_CONFIG_PATH") or "config.json")
        raise SystemExit(
            f"Configuration file '{missing_path}' not found. "
            "Set NLP_CONFIG_PATH or create config.json."
        ) from exc

    # Override provider or model if specified via command line
    if args.model:
        config["LLM_PROVIDER"] = args.model
    elif args.provider:
        config["LLM_PROVIDER"] = args.provider

    try:
        chunk_size, chunk_overlap = resolve_chunk_settings(
            config,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    allow_search = args.search == "on"

    reranker: Optional[BaseReranker] = None
    rerank_config: Dict[str, Any] = config.get("rerank") or {}
    if allow_search and not args.disable_rerank:
        reranker, rerank_config = build_reranker(config)

    min_rerank_score = float(rerank_config.get("min_score", 0.0))
    max_per_domain = max(1, int(rerank_config.get("max_per_domain", 1)))

    search_client: Optional[SearchClient] = None
    requested_sources: List[str] = []
    active_sources: List[str] = []
    active_labels: List[str] = []
    missing_sources: List[str] = []
    configured_sources: List[str] = []
    brave_cfg_cli = config.get("braveSearch") or {}
    if configured_value(brave_cfg_cli.get("primary_api_key")):
        configured_sources.append("brave")
    firecrawl_cfg_cli = config.get("firecrawlSearch") or {}
    if configured_value(firecrawl_cfg_cli.get("api_key")):
        configured_sources.append("firecrawl")
    tavily_cfg_cli = config.get("tavilySearch") or {}
    if configured_value(tavily_cfg_cli.get("api_key")):
        configured_sources.append("tavily")
    parallel_cfg_cli = config.get("parallelSearch") or {}
    if configured_value(parallel_cfg_cli.get("api_key")):
        configured_sources.append("parallel")
    bright_cfg_cli = config.get("brightDataSearch") or {}
    if configured_value(bright_cfg_cli.get("api_token")) and (bright_cfg_cli.get("zone") or "").strip():
        configured_sources.append("brightdata")
    google_cfg_cli = config.get("googleSearch") or {}
    google_key_cli = configured_value(google_cfg_cli.get("api_key") or config.get("GOOGLE_API_KEY"))
    google_cx_cli = configured_value(google_cfg_cli.get("cx") or config.get("GOOGLE_CX"))
    sportsdb_key_cli = configured_value(config.get("SPORTSDB_API_KEY"))
    apisports_key_cli = configured_value(config.get("APISPORTS_KEY"))
    
    if google_key_cli and google_cx_cli:
        configured_sources.append("google")
    
    # Build search client and extract metadata
    search_client = build_search_client(config)
    requested_sources = getattr(search_client, "requested_sources", []) if search_client else []
    active_sources = getattr(search_client, "active_sources", []) if search_client else []
    active_labels = getattr(search_client, "active_source_labels", []) if search_client else []
    missing_sources = getattr(search_client, "missing_requested_sources", []) if search_client else []
    configured_sources = getattr(search_client, "configured_sources", configured_sources) if search_client else configured_sources
    
    show_timings = args.pretty

    # Check if legacy orchestrator should be used (LangChain is now the default)
    use_legacy = getattr(args, 'use_legacy', False)
    
    if use_legacy:
        # Use legacy SmartSearchOrchestrator
        print("[main] Using legacy orchestrator")
        llm_client = build_llm_client(config)
        classifier_client = build_domain_classifier_client(config)
        routing_client = build_routing_keywords_client(config)
        
        orchestrator = SmartSearchOrchestrator(
            llm_client=llm_client,
            apisports_api_key=apisports_key_cli,
            classifier_llm_client=classifier_client,
            routing_llm_client=routing_client,
            search_client=search_client,
            data_path=args.data_path,
            reranker=reranker,
            min_rerank_score=min_rerank_score,
            max_per_domain=max_per_domain,
            requested_search_sources=requested_sources,
            active_search_sources=active_sources,
            active_search_source_labels=active_labels,
            missing_search_sources=missing_sources,
            configured_search_sources=configured_sources,
            show_timings=show_timings,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            google_api_key=google_key_cli,
            sportsdb_api_key=sportsdb_key_cli,
            config=config,
        )
    else:
        # Use LangChain-based orchestrator (default)
        if config.get("orchestrator_mode") == "react":
            print("[main] orchestrator_mode=react is deprecated as a top-level mode; using LangChain orchestrator with ReAct fallback")

        if not LANGCHAIN_AVAILABLE:
            print("[main] LangChain not available, falling back to legacy orchestrator")
            llm_client = build_llm_client(config)
            classifier_client = build_domain_classifier_client(config)
            routing_client = build_routing_keywords_client(config)

            orchestrator = SmartSearchOrchestrator(
                llm_client=llm_client,
                apisports_api_key=apisports_key_cli,
                classifier_llm_client=classifier_client,
                routing_llm_client=routing_client,
                search_client=search_client,
                data_path=args.data_path,
                reranker=reranker,
                min_rerank_score=min_rerank_score,
                max_per_domain=max_per_domain,
                requested_search_sources=requested_sources,
                active_search_sources=active_sources,
                active_search_source_labels=active_labels,
                missing_search_sources=missing_sources,
                configured_search_sources=configured_sources,
                show_timings=show_timings,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                google_api_key=google_key_cli,
                sportsdb_api_key=sportsdb_key_cli,
                config=config,
            )
        else:
            print("[main] Using LangChain orchestrator")
            from langchain.langchain_llm import create_chat_model
            from langchain.langchain_orchestrator import create_langchain_orchestrator

            # Create LangChain LLM
            llm = create_chat_model(config=config)

            # Create LangChain reranker if configured
            langchain_reranker = None
            if reranker is not None:
                try:
                    from langchain.langchain_rerank import create_qwen3_compressor
                    qwen_cfg = (rerank_config.get("providers") or {}).get("qwen") or rerank_config.get("qwen") or {}
                    langchain_reranker = create_qwen3_compressor(
                        api_key=qwen_cfg.get("api_key"),
                        model=qwen_cfg.get("model", "qwen3-rerank"),
                        base_url=qwen_cfg.get("base_url"),
                        timeout=qwen_cfg.get("timeout", 15),
                    )
                except Exception as exc:
                    print(f"[main] Failed to create LangChain reranker: {exc}")

            orchestrator = create_langchain_orchestrator(
                config=config,
                llm=llm,
                search_client=search_client,
                data_path=args.data_path,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                reranker=langchain_reranker,
                min_rerank_score=min_rerank_score,
                max_per_domain=max_per_domain,
                requested_search_sources=requested_sources,
                active_search_sources=active_sources,
                active_search_source_labels=active_labels,
                missing_search_sources=missing_sources,
                configured_search_sources=configured_sources,
                show_timings=show_timings,
            )
    
    # Use configured temperature for direct answer as default, but allow CLI override
    provider = config.get("LLM_PROVIDER", "minimax")
    if "/" in provider:
        # Extract provider from model path
        provider = provider.split("/")[0]
    
    configured_temp = get_temperature_for_task(config, "direct_answer", provider, args.temperature)
    
    # If user explicitly set temperature via CLI, use that value
    effective_temperature = args.temperature if args.temperature != 0.3 else configured_temp
    
    # Resolve conversation id: reuse provided, else mint a new one.
    conversation_id = args.conversation_id or f"cli-{uuid.uuid4().hex[:12]}"

    result = orchestrator.answer(
        args.query,
        num_search_results=args.num_results,
        num_retrieved_docs=args.num_results,
        max_tokens=args.max_tokens,
        temperature=effective_temperature,
        allow_search=allow_search,
        conversation_id=conversation_id,
    )
    result.setdefault("conversation_id", conversation_id)

    # Check if there are any errors or warnings
    has_error = result.get("llm_error") is not None
    search_warnings = result.get("search_warnings")
    has_warning = (result.get("llm_warning") is not None) or bool(search_warnings)
    no_answer = result.get("answer") is None

    # If there are errors/warnings or no answer, output full JSON
    timings_payload = result.get("response_times") if show_timings else None

    if has_error or has_warning or no_answer:
        if args.pretty:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(result, ensure_ascii=False))
    else:
        # Normal case: only output the answer
        print(result["answer"])
        if not args.conversation_id:
            print(f"\n[conversation_id] {conversation_id}（使用 --conversation-id {conversation_id} 继续追问）")
        if show_timings and isinstance(timings_payload, dict):
            print("\n[响应时间]")
            total_ms = timings_payload.get("total_ms")
            if isinstance(total_ms, (int, float)):
                print(f"- 总耗时: {total_ms:.2f} ms")
            search_timings = timings_payload.get("search_sources") or []
            for entry in search_timings:
                label = entry.get("label") or entry.get("source") or "Search"
                duration = entry.get("duration_ms")
                try:
                    duration_val = float(duration)
                except (TypeError, ValueError):
                    duration_val = None
                detail = f"{label}"
                if entry.get("error"):
                    detail += f"（错误: {entry['error']}）"
                if duration_val is not None:
                    print(f"- 搜索源[{detail}]: {duration_val:.2f} ms")
                else:
                    print(f"- 搜索源[{detail}]")
            llm_timings = timings_payload.get("llm_calls") or []
            for entry in llm_timings:
                label = entry.get("label") or "LLM"
                duration = entry.get("duration_ms")
                try:
                    duration_val = float(duration)
                except (TypeError, ValueError):
                    duration_val = None
                provider = entry.get("provider")
                model = entry.get("model")
                provider_info = ""
                if provider or model:
                    provider_info = f"（{provider or ''}{'/' if provider and model else ''}{model or ''}）"
                if duration_val is not None:
                    print(f"- LLM[{label}]{provider_info}: {duration_val:.2f} ms")
                else:
                    print(f"- LLM[{label}]{provider_info}")


if __name__ == "__main__":
    main()
