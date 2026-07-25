from __future__ import annotations

import copy
import json
import os
import queue
import sys
import threading
import time
import traceback
import uuid
from functools import lru_cache
from typing import Any, Dict, Generator, Optional, List

from flask import Flask, Response, g, jsonify, request
from werkzeug.serving import run_simple
from werkzeug.serving import WSGIRequestHandler
from werkzeug.utils import secure_filename

# Add project directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import build_search_client, build_reranker
from utils.chunking import resolve_chunk_settings
from utils.config_validation import configured_value
from utils.server_logging import (
    QueryAuditLog,
    configure_process_logging,
    resolve_server_logging_settings,
    write_access_event,
)
from utils.temperature_config import get_temperature_for_task
from utils.workflow_trace import WorkflowTracer
from langchain.langchain_llm import create_chat_model
from langchain.langchain_orchestrator import create_langchain_orchestrator, LangChainOrchestrator

# Per-conversation locks ensuring same-thread requests run sequentially so the
# checkpointed state is not interleaved. Maps conversation_id -> threading.Lock.
_CONVERSATION_LOCKS: Dict[str, threading.Lock] = {}
_CONVERSATION_LOCKS_GUARD = threading.Lock()

# Adjust paths for the new structure
base_dir = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(base_dir, "frontend"), static_url_path="")
UPLOAD_FOLDER = os.path.join(base_dir, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


def _server_logging_settings() -> Dict[str, Any]:
    """Read optional web logging settings without affecting API availability."""
    override = app.config.get("SERVER_LOGGING_SETTINGS")
    if isinstance(override, dict):
        return resolve_server_logging_settings({"server_logging": override})
    if app.config.get("TESTING"):
        return resolve_server_logging_settings(None)
    try:
        return resolve_server_logging_settings(load_base_config())
    except Exception:
        return resolve_server_logging_settings(None)


def _request_metadata() -> Dict[str, Any]:
    """Capture request facts for audit records; redaction happens at write time."""
    return {
        "method": request.method,
        "path": request.path,
        "query_string": request.query_string.decode("utf-8", errors="replace"),
        "remote_addr": request.remote_addr,
        "content_type": request.content_type,
        # The audit sanitizer keeps ordinary header values while dropping only
        # credential-shaped names such as Authorization and Cookie.
        "header_values": dict(request.headers),
    }


def _new_query_audit(endpoint: str, payload: Any) -> QueryAuditLog:
    """Start a durable query event stream before validation begins."""
    audit = QueryAuditLog(
        _server_logging_settings(),
        endpoint=endpoint,
        request_id=getattr(g, "request_id", None),
    )
    audit.record(
        "request_received",
        request=_request_metadata(),
        request_payload=payload,
    )
    return audit


@app.before_request
def _begin_access_log() -> None:
    g.request_id = uuid.uuid4().hex
    g.request_started_at = time.perf_counter()


@app.after_request
def _persist_access_log(response: Response) -> Response:
    request_id = getattr(g, "request_id", None)
    started_at = getattr(g, "request_started_at", None)
    duration_ms = (
        round((time.perf_counter() - started_at) * 1000, 1)
        if isinstance(started_at, float)
        else None
    )
    write_access_event(
        _server_logging_settings(),
        event="http_response",
        request_id=request_id,
        request=_request_metadata(),
        status_code=response.status_code,
        duration_ms=duration_ms,
        response_content_type=response.content_type,
        response_content_length=response.calculate_content_length(),
    )
    if request_id:
        response.headers["X-Request-ID"] = request_id
    return response


@app.teardown_request
def _persist_unhandled_request_error(error: Optional[BaseException]) -> None:
    if error is None:
        return
    write_access_event(
        _server_logging_settings(),
        event="http_exception",
        request_id=getattr(g, "request_id", None),
        request=_request_metadata(),
        error=str(error),
        traceback="".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        ),
    )


class ConfigurationError(RuntimeError):
    """Raised when the application configuration is invalid."""


class PayloadError(ValueError):
    """Raised when an answer request payload is invalid."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _coerce_bool(raw_value: Any) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        return normalized in {"true", "1", "yes", "y", "on"}
    if isinstance(raw_value, (int, float)):
        return bool(raw_value)
    return False


def _coerce_positive_int(raw_value: Any, field: str) -> Optional[int]:
    if raw_value is None:
        return None
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        raise PayloadError(f"'{field}' must be a positive integer.")
    if parsed <= 0:
        raise PayloadError(f"'{field}' must be a positive integer.")
    return parsed


def _conversation_lock(conversation_id: str) -> Optional[threading.Lock]:
    """Return a dedicated lock for a conversation id (None if no id)."""
    if not conversation_id:
        return None
    with _CONVERSATION_LOCKS_GUARD:
        lock = _CONVERSATION_LOCKS.get(conversation_id)
        if lock is None:
            lock = threading.Lock()
            _CONVERSATION_LOCKS[conversation_id] = lock
        return lock


def ensure_json_serializable(obj: Any) -> Any:
    """Recursively ensure all values in a dict/list are JSON serializable."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): ensure_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [ensure_json_serializable(item) for item in obj]
    # Convert any other type to string
    try:
        return str(obj)
    except Exception:
        return None


@lru_cache(maxsize=1)
def load_base_config() -> Dict[str, Any]:
    """Load the project configuration file.

    The path can be overridden via the NLP_CONFIG_PATH environment variable.
    """

    config_path = os.environ.get("NLP_CONFIG_PATH", "config.json")
    if not os.path.exists(config_path):
        raise ConfigurationError(
            f"Configuration file '{config_path}' not found. "
            "Create it based on config.example.json."
        )

    with open(config_path, "r", encoding="utf-8") as config_file:
        return json.load(config_file)


def _normalize_search_sources(raw_sources: Optional[List[str]]) -> List[str]:
    normalized: List[str] = []
    if not raw_sources:
        return normalized
    for item in raw_sources:
        if not isinstance(item, str):
            continue
        token = item.strip().lower()
        if not token or token in normalized:
            continue
        normalized.append(token)
    return normalized


def build_pipeline(
    model_override: Optional[str] = None,
    *,
    search_sources: Optional[List[str]] = None,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
) -> Any:
    """Create a LangChain pipeline configured for the current request."""

    # Deep copy to avoid mutating cached configuration between requests
    config = copy.deepcopy(load_base_config())
    providers_cfg = config.get("providers", {})

    def provider_has_valid_key(name: str) -> bool:
        cfg = providers_cfg.get(name) or {}
        return bool(configured_value(cfg.get("api_key")))

    def match_provider_by_model(model_id: str) -> Optional[str]:
        # First try to match exact model name
        for name, cfg in providers_cfg.items():
            if cfg.get("model") == model_id:
                return name
        
        # Then try to find in available_models
        for name, cfg in providers_cfg.items():
            avail = cfg.get("available_models", [])
            if model_id in avail:
                return name
        
        return None

    def resolve_default_provider() -> str:
        configured = config.get("LLM_PROVIDER")
        if configured:
            if configured in providers_cfg and provider_has_valid_key(configured):
                return configured
            matched = match_provider_by_model(configured)
            if matched and provider_has_valid_key(matched):
                if matched in providers_cfg and not providers_cfg[matched].get("model"):
                    providers_cfg[matched]["model"] = configured
                return matched

        preferred_order = [
            "opencode-go",
            "minimax",
            "zai",
            "glm",
            "openai",
            "anthropic",
            "google",
            "hkgai",
            "openrouter",
        ]
        for candidate in preferred_order:
            if candidate in providers_cfg and provider_has_valid_key(candidate):
                return candidate

        if configured and configured in providers_cfg:
            return configured

        return next(iter(providers_cfg.keys()), "minimax")

    config["LLM_PROVIDER"] = resolve_default_provider()
    if model_override:
        # Check if it's a model path (contains '/') and convert to provider
        if "/" in model_override:
            # Find provider that has this model in available_models
            matched_provider = None
            for p_name, p_cfg in providers_cfg.items():
                avail = p_cfg.get("available_models", [])
                if model_override in avail:
                    matched_provider = p_name
                    break
            provider = matched_provider or model_override.split("/")[0]
            config["LLM_PROVIDER"] = provider
            if provider in providers_cfg:
                providers_cfg[provider]["model"] = model_override
        else:
            if model_override in providers_cfg:
                # Direct provider selection (e.g., "glm")
                config["LLM_PROVIDER"] = model_override
            else:
                matched_provider = match_provider_by_model(model_override)
                if matched_provider:
                    config["LLM_PROVIDER"] = matched_provider
                    if matched_provider in providers_cfg:
                        providers_cfg[matched_provider]["model"] = model_override
                else:
                    # Model not found in any provider, raise an error instead of treating as provider
                    raise ConfigurationError(f"Model '{model_override}' not found in any provider configuration")

    try:
        resolved_chunk_size, resolved_chunk_overlap = resolve_chunk_settings(
            config,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc

    # Build LangChain LLM
    try:
        llm = create_chat_model(config=config)
    except Exception as exc:
        raise ConfigurationError(f"Failed to build LangChain LLM: {exc}")

    # Build search sources metadata
    normalized_sources = _normalize_search_sources(search_sources)
    configured_sources: List[str] = []
    brave_cfg = config.get("braveSearch") or {}
    if configured_value(brave_cfg.get("primary_api_key")):
        configured_sources.append("brave")
    firecrawl_cfg = config.get("firecrawlSearch") or {}
    if configured_value(firecrawl_cfg.get("api_key")):
        configured_sources.append("firecrawl")
    tavily_cfg = config.get("tavilySearch") or {}
    if configured_value(tavily_cfg.get("api_key")):
        configured_sources.append("tavily")
    parallel_cfg = config.get("parallelSearch") or {}
    if configured_value(parallel_cfg.get("api_key")):
        configured_sources.append("parallel")
    bright_cfg = config.get("brightDataSearch") or {}
    if configured_value(bright_cfg.get("api_token")) and (bright_cfg.get("zone") or "").strip():
        configured_sources.append("brightdata")
    google_cfg = config.get("googleSearch") or {}
    google_key = configured_value(google_cfg.get("api_key") or config.get("GOOGLE_API_KEY"))
    google_cx = configured_value(google_cfg.get("cx") or config.get("GOOGLE_CX"))
    if google_key and google_cx:
        configured_sources.append("google")

    search_client = None
    active_sources: List[str] = []
    active_labels: List[str] = []
    missing_sources: List[str] = []
    try:
        search_client = build_search_client(config, sources=normalized_sources if normalized_sources else None)
        if search_client is not None:
            active_sources = list(getattr(search_client, "active_sources", []))
            active_labels = list(getattr(search_client, "active_source_labels", []))
            missing_sources = list(getattr(search_client, "missing_requested_sources", []))
            if not configured_sources:
                configured_sources = list(getattr(search_client, "configured_sources", []))
    except Exception as exc:
        raise ConfigurationError(f"Failed to build search client: {exc}")

    if not missing_sources and normalized_sources:
        reference = active_sources if active_sources else configured_sources
        missing_sources = [src for src in normalized_sources if src not in reference]

    # Build reranker for LangChain
    rerank_config = config.get("rerank") or {}
    reranker: Optional[Any] = None
    try:
        from langchain.langchain_rerank import create_qwen3_compressor
        configured_reranker, rerank_config = build_reranker(config)
        qwen_cfg = (rerank_config.get("providers") or {}).get("qwen") or rerank_config.get("qwen") or {}
        if configured_reranker is not None:
            reranker = create_qwen3_compressor(
                api_key=qwen_cfg.get("api_key"),
                model=qwen_cfg.get("model", "qwen3-rerank"),
                base_url=qwen_cfg.get("base_url"),
                request_timeout=qwen_cfg.get("timeout", 15),
            )
    except Exception as exc:
        print(f"[server] LangChain reranker disabled: {exc}")
        reranker = None

    min_rerank_score = float(rerank_config.get("min_score", 0.0))
    max_per_domain = max(1, int(rerank_config.get("max_per_domain", 1)))
    show_timings = bool(config.get("displayResponseTimes", False))

    return create_langchain_orchestrator(
        config=config,
        llm=llm,
        search_client=search_client,
        data_path=app.config['UPLOAD_FOLDER'],
        chunk_size=resolved_chunk_size,
        chunk_overlap=resolved_chunk_overlap,
        reranker=reranker,
        min_rerank_score=min_rerank_score,
        max_per_domain=max_per_domain,
        requested_search_sources=normalized_sources,
        active_search_sources=active_sources,
        active_search_source_labels=active_labels,
        missing_search_sources=missing_sources,
        configured_search_sources=configured_sources,
        show_timings=show_timings,
        finnhub_api_key=(config.get("FINNHUB_API_KEY") or os.environ.get("FINNHUB_API_KEY")),
    )


@app.route("/")
def index() -> Any:
    return app.send_static_file("index.html")


@app.route("/api/health")
def health() -> Any:
    """Health check endpoint to verify the server and agent are properly initialized."""
    try:
        load_base_config()
        return jsonify({"status": "ok"})
    except Exception as exc:
        return jsonify({
            "status": "error",
            "error": str(exc),
        }), 500


@app.route("/api/models")
def get_available_models():
    """Get list of available models from configuration."""
    try:
        config = load_base_config()
        models = []
        seen_ids = set()
        
        # Get all models from providers (including available_models)
        for provider_name, provider_config in config.get("providers", {}).items():
            if not configured_value(provider_config.get("api_key")):
                continue
            # Add the default model
            default_model = provider_config.get("model")
            if default_model and default_model not in seen_ids:
                models.append({
                    "id": default_model,
                    "provider": provider_name,
                    "display_name": f"{provider_name.upper()} - {default_model}"
                })
                seen_ids.add(default_model)
            
            # Add all available_models if present
            available_models = provider_config.get("available_models", [])
            if available_models:
                for model in available_models:
                    if model not in seen_ids:
                        models.append({
                            "id": model,
                            "provider": provider_name,
                            "display_name": f"{provider_name.upper()} - {model}"
                        })
                        seen_ids.add(model)
        
        return jsonify({"models": models})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/files', methods=['GET'])
def list_files():
    files = os.listdir(app.config['UPLOAD_FOLDER'])
    return jsonify(files)


@app.route('/api/files', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    if file:
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        return jsonify({"message": "File uploaded successfully"})


@app.route('/api/files/<filename>', methods=['DELETE'])
def delete_file(filename):
    try:
        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        return jsonify({"message": "File deleted successfully"})
    except FileNotFoundError:
        return jsonify({"error": "File not found"}), 404




# ---------------------------------------------------------------------------
# Conversation management (sidebar)
# ---------------------------------------------------------------------------
def _conversation_manager():
    """Return the process-level ConversationManager singleton."""
    from orchestrators.conversation_store import get_conversation_manager
    return get_conversation_manager()


@app.route("/api/conversations")
def list_conversations() -> Any:
    """List persisted conversations for the sidebar, newest first."""
    try:
        mgr = _conversation_manager()
        conversations = mgr.list_conversations()
        enabled = mgr.enabled
    except Exception as exc:  # noqa: BLE001 - never 500 the sidebar
        print(f"[server] list_conversations failed: {exc}")
        conversations, enabled = [], False
    return jsonify({"conversations": conversations, "enabled": enabled})


@app.route("/api/conversations/<conversation_id>")
def get_conversation(conversation_id: str) -> Any:
    """Return the full turn history for a conversation."""
    cid = (conversation_id or "").strip()
    if not cid:
        return jsonify({"error": "conversation_id is required"}), 400
    title = None
    try:
        mgr = _conversation_manager()
        turns = mgr.get_all_turns(cid)
        title = mgr.get_conversation_title(cid)
    except Exception as exc:  # noqa: BLE001
        print(f"[server] get_conversation failed: {exc}")
        turns = []
    return jsonify({
        "conversation_id": cid,
        "title": title,
        "turns": turns,
    })


@app.route("/api/conversations/<conversation_id>", methods=["DELETE"])
def delete_conversation(conversation_id: str) -> Any:
    """Delete a conversation (checkpoint, turns, and custom title)."""
    cid = (conversation_id or "").strip()
    if not cid:
        return jsonify({"error": "conversation_id is required"}), 400
    try:
        _conversation_manager().delete_checkpoint(cid)
    except Exception as exc:  # noqa: BLE001
        print(f"[server] delete_conversation failed: {exc}")
        return jsonify({"error": str(exc)}), 500
    return jsonify({"message": "Conversation deleted"})


@app.route("/api/conversations/<conversation_id>/title", methods=["PUT", "POST"])
def rename_conversation(conversation_id: str) -> Any:
    """Set or clear a custom title for a conversation."""
    cid = (conversation_id or "").strip()
    if not cid:
        return jsonify({"error": "conversation_id is required"}), 400
    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    mgr = _conversation_manager()
    if not title:
        mgr.clear_conversation_title(cid)
        return jsonify({"conversation_id": cid, "title": None, "custom_title": False})
    if not mgr.set_conversation_title(cid, title):
        return jsonify({"error": "Failed to rename conversation"}), 500
    return jsonify({"conversation_id": cid, "title": title, "custom_title": True})



def _prepare_answer_context(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize an answer request payload.

    Shared by the JSON and SSE answer endpoints. Raises PayloadError for
    client-visible validation problems.
    """
    query = (payload.get("query") or "").strip()
    if not query:
        raise PayloadError("Missing 'query' in request body.")

    search_sources: Optional[List[str]] = None
    if payload.get("search_sources") is not None:
        raw_sources = payload["search_sources"]
        if not isinstance(raw_sources, list):
            raise PayloadError("'search_sources' must be an array.")
        allowed_sources = {
            "brave",
            "firecrawl",
            "tavily",
            "parallel",
            "brightdata",
            "google",
        }
        normalized_sources: List[str] = []
        seen = set()
        for item in raw_sources:
            if not isinstance(item, str):
                raise PayloadError("Invalid search source value.")
            token = item.strip().lower()
            if token not in allowed_sources:
                raise PayloadError(f"Unsupported search source '{item}'.")
            if token in seen:
                continue
            seen.add(token)
            normalized_sources.append(token)
        search_sources = normalized_sources

    search_pref = (payload.get("search") or "").strip().lower()
    if search_pref in {"on", "off"}:
        allow_search = search_pref == "on"
    else:
        legacy_mode = (payload.get("mode") or "search").strip().lower()
        allow_search = legacy_mode != "local"

    # Handle code blocks if present
    code_blocks = payload.get("code_blocks")
    if code_blocks and isinstance(code_blocks, list):
        print(f"[server] Received {len(code_blocks)} code blocks")

    force_search = _coerce_bool(payload.get("force_search")) and allow_search

    images = payload.get("images")
    if images and not isinstance(images, list):
        raise PayloadError("'images' must be a list.")

    reference_limit: Optional[int] = None
    search_reference_value = payload.get("search_reference_limit")
    fallback_display_value = payload.get("search_source_display_limit")
    legacy_num = _coerce_positive_int(payload.get("num_results"), "num_results")
    total_limit = _coerce_positive_int(payload.get("search_total_limit"), "search_total_limit")
    per_source_limit = _coerce_positive_int(payload.get("search_source_limit"), "search_source_limit")
    reference_limit = _coerce_positive_int(search_reference_value, "search_reference_limit")
    if reference_limit is None and fallback_display_value is not None:
        reference_limit = _coerce_positive_int(fallback_display_value, "search_source_display_limit")

    search_depth_raw = str(payload.get("search_depth") or "").strip().lower()
    search_depth = search_depth_raw or None

    default_total = legacy_num if legacy_num is not None else 5
    if total_limit is None:
        total_limit = default_total
    if per_source_limit is None:
        per_source_limit = total_limit
    num_retrieved_docs = legacy_num if legacy_num is not None else total_limit

    # Use configured temperature for direct answer as default, but allow request override
    config = load_base_config()
    provider = config.get("LLM_PROVIDER", "minimax")
    if "/" in provider:
        # Extract provider from model path
        provider = provider.split("/")[0]

    # Get temperature from request or use configured default
    request_temp = payload.get("temperature")
    if request_temp is not None:
        temperature = float(request_temp)
    else:
        temperature = get_temperature_for_task(config, "direct_answer", provider, 0.3)

    return {
        "query": query,
        "allow_search": allow_search,
        "search_sources": search_sources,
        "force_search": force_search,
        "images": images,
        "model": payload.get("model") or payload.get("provider"),
        "total_limit": total_limit,
        "per_source_limit": per_source_limit,
        "num_retrieved_docs": num_retrieved_docs,
        "reference_limit": reference_limit,
        "search_depth": search_depth,
        "temperature": temperature,
        "max_tokens": int(payload.get("max_tokens")) if payload.get("max_tokens") else 5000,
        "chunk_size": payload.get("chunk_size"),
        "chunk_overlap": payload.get("chunk_overlap"),
        "conversation_id": (str(payload.get("conversation_id")).strip()
                            if payload.get("conversation_id") else None),
    }


def _execute_answer(ctx: Dict[str, Any], tracer: Optional[WorkflowTracer] = None) -> Dict[str, Any]:
    """Build the pipeline and run the answer flow for a prepared context."""
    conversation_id = ctx.get("conversation_id")
    lock = _conversation_lock(conversation_id) if conversation_id else None
    if lock is not None:
        lock.acquire()
    try:
        return _execute_answer_unlocked(ctx, tracer)
    finally:
        if lock is not None:
            lock.release()


def _execute_answer_unlocked(ctx: Dict[str, Any], tracer: Optional[WorkflowTracer] = None) -> Dict[str, Any]:
    """Build the pipeline and run the answer flow for a prepared context."""
    pipeline = build_pipeline(
        model_override=ctx["model"],
        search_sources=ctx["search_sources"] if ctx["allow_search"] and ctx["search_sources"] else None,
        chunk_size=ctx["chunk_size"],
        chunk_overlap=ctx["chunk_overlap"],
    )

    print(f"[server] Processing query: {ctx['query'][:50]}...")
    result = pipeline.answer(
        ctx["query"],
        num_search_results=ctx["total_limit"],
        per_source_search_results=ctx["per_source_limit"],
        num_retrieved_docs=ctx["num_retrieved_docs"],
        max_tokens=ctx["max_tokens"],
        temperature=ctx["temperature"],
        allow_search=ctx["allow_search"],
        reference_limit=ctx["reference_limit"],
        force_search=ctx["force_search"],
        search_depth=ctx.get("search_depth"),
        images=ctx["images"],
        conversation_id=ctx.get("conversation_id"),
        tracer=tracer,
    )
    print(f"[server] Pipeline returned result with keys: {list(result.keys()) if isinstance(result, dict) else type(result)}")

    if not isinstance(result, dict):
        raise RuntimeError("服务器返回数据格式错误")

    if "answer" not in result:
        result["answer"] = "未能生成答案"

    if ctx["reference_limit"] is not None:
        control = result.get("control")
        if not isinstance(control, dict):
            control = {}
            result["control"] = control
        control["search_reference_limit"] = ctx["reference_limit"]

    return result


@app.post("/api/answer")
def answer() -> Any:
    payload: Dict[str, Any] = request.get_json(silent=True) or {}
    audit = _new_query_audit("/api/answer", payload)

    try:
        ctx = _prepare_answer_context(payload)
    except PayloadError as exc:
        audit.record(
            "validation_error",
            error=str(exc),
            status_code=exc.status,
        )
        return jsonify({"error": str(exc)}), exc.status
    audit.record("context_prepared", context=ctx)

    tracer = WorkflowTracer()
    tracer.on_event(
        lambda event: audit.record("workflow_step", workflow_event=event)
    )

    try:
        result = _execute_answer(ctx, tracer=tracer)
    except ConfigurationError as exc:
        audit.record("configuration_error", error=str(exc), status_code=500)
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:  # pragma: no cover - propagate runtime issues
        error_msg = str(exc).encode('utf-8', errors='replace').decode('utf-8')
        traceback_text = traceback.format_exc()
        print(f"[server] Pipeline execution error: {error_msg}")
        print(traceback_text)
        audit.record(
            "pipeline_error",
            error=error_msg,
            traceback=traceback_text,
            status_code=500,
        )
        return jsonify({"error": f"Pipeline execution failed: {error_msg}"}), 500

    # Log answer length
    answer_len = len(result.get("answer", "")) if isinstance(result.get("answer"), str) else 0
    print(f"[server] Answer length: {answer_len} chars")

    # Ensure all values are JSON serializable
    try:
        result = ensure_json_serializable(result)
        print(f"[server] Serialization successful")
    except Exception as exc:
        print(f"[server] Failed to serialize result: {exc}")
        audit.record("serialization_error", error=str(exc), status_code=500)
        return jsonify({"error": "响应数据序列化失败"}), 500

    # Try to create JSON to verify it works
    try:
        test_json = json.dumps(result, ensure_ascii=False)
        print(f"[server] JSON creation successful, size: {len(test_json)} bytes")
    except Exception as exc:
        print(f"[server] JSON creation failed: {exc}")
        audit.record("json_creation_error", error=str(exc), status_code=500)
        return jsonify({"error": f"JSON序列化失败: {str(exc)}"}), 500

    audit.record("response_ready", response_payload=result, status_code=200)
    audit.record("request_complete", status_code=200)
    return jsonify(result)


@app.post("/api/answer/stream")
def answer_stream() -> Any:
    """Stream workflow step events over SSE, then the final result.

    Frames:
      event: step   — one JSON object per workflow step state change
      event: result — the full final result (same shape as /api/answer)
      event: error  — {error: str} when the pipeline fails
      event: done   — marks the end of the stream
    """
    payload: Dict[str, Any] = request.get_json(silent=True) or {}
    audit = _new_query_audit("/api/answer/stream", payload)

    try:
        ctx = _prepare_answer_context(payload)
    except PayloadError as exc:
        audit.record(
            "validation_error",
            error=str(exc),
            status_code=exc.status,
        )
        return jsonify({"error": str(exc)}), exc.status
    audit.record("context_prepared", context=ctx)

    def generate() -> Generator[str, None, None]:
        events: "queue.Queue[Any]" = queue.Queue()
        tracer = WorkflowTracer()

        def persist_and_queue_step(event: Dict[str, Any]) -> None:
            audit.record("workflow_step", workflow_event=event)
            events.put(("step", event))

        tracer.on_event(persist_and_queue_step)
        holder: Dict[str, Any] = {}
        completed = False

        def run() -> None:
            try:
                holder["result"] = _execute_answer(ctx, tracer=tracer)
            except Exception as exc:  # pragma: no cover - propagate runtime issues
                error_msg = str(exc).encode("utf-8", errors="replace").decode("utf-8")
                traceback_text = traceback.format_exc()
                print(f"[server] Stream pipeline error: {error_msg}")
                print(traceback_text)
                holder["error"] = error_msg
                audit.record(
                    "pipeline_error",
                    error=error_msg,
                    traceback=traceback_text,
                    status_code=500,
                )
            finally:
                events.put(None)

        try:
            worker = threading.Thread(target=run, daemon=True)
            worker.start()

            while True:
                item = events.get()
                if item is None:
                    break
                kind, data = item
                yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")

            if "error" in holder:
                error_payload = {"error": holder["error"]}
                audit.record("response_error", response_payload=error_payload, status_code=500)
                yield f"event: error\ndata: {json.dumps(error_payload, ensure_ascii=False)}\n\n".encode("utf-8")
            else:
                try:
                    result = ensure_json_serializable(holder["result"])
                except Exception as exc:
                    error_payload = {"error": f"响应数据序列化失败: {exc}"}
                    audit.record(
                        "serialization_error",
                        error=str(exc),
                        response_payload=error_payload,
                        status_code=500,
                    )
                    yield f"event: error\ndata: {json.dumps(error_payload, ensure_ascii=False)}\n\n".encode("utf-8")
                else:
                    audit.record("response_ready", response_payload=result, status_code=200)
                    yield f"event: result\ndata: {json.dumps(result, ensure_ascii=False)}\n\n".encode("utf-8")
            audit.record("request_complete", status_code=200)
            completed = True
            yield b"event: done\ndata: {}\n\n"
        finally:
            if not completed:
                audit.record("stream_closed_before_complete")

    return Response(
        generate(),
        mimetype="text/event-stream",
        direct_passthrough=True,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


class StreamingRequestHandler(WSGIRequestHandler):
    """HTTP/1.1 handler so SSE chunks flush incrementally.

    Werkzeug's dev server defaults to HTTP/1.0, which disables chunked
    transfer encoding and forces clients to buffer the whole response until
    the connection closes — making live step streaming appear batched. HTTP/1.1
    enables per-chunk framing so each workflow step reaches the browser as it
    happens.
    """

    protocol_version = "HTTP/1.1"


if __name__ == "__main__":
    app.config['JSON_AS_ASCII'] = False  # 允许UTF-8字符
    try:
        configure_process_logging(_server_logging_settings())
    except Exception as exc:  # noqa: BLE001 - logging must not prevent startup
        print(f"[server] Failed to enable persistent logging: {exc}")
    run_simple(
        os.environ.get("HOST", "0.0.0.0"),
        int(os.environ.get("PORT", "8000")),
        app,
        threaded=True,
        request_handler=StreamingRequestHandler,
        use_reloader=False,
        use_debugger=False,
    )
