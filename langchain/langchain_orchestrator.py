"""LangChain entrypoint for the sole agentic act/observe/evaluate loop."""

from __future__ import annotations

import os
import sys
import time
import requests
from typing import Any, Dict, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence import EvidenceItem
from search.search import SearchClient, apply_search_depth_override
from skills import SkillRegistry
from utils.time_parser import TimeConstraint, parse_time_constraint
from utils.search_routing import is_small_talk_query, normalize_sources
from utils.timing_utils import TimingRecorder, extract_token_usage
from utils.current_time import get_current_date_str
from utils.workflow_trace import WorkflowTracer, ensure_tracer
from utils.audit_log import AuditRecorder, resolve_audit_settings
from utils.query_orchestration import (
    EvidenceLedger,
    EvidencePolicyRegistry,
    QueryExecutionTrace,
    QueryAnalysis,
    analyze_query,
    normalize_termination_config,
)


class LangChainOrchestrator:
    """Thin request shell around the single LangGraph agentic loop."""

    DIRECT_ANSWER_SYSTEM_PROMPT = """You are a knowledgeable assistant. Answer clearly based on your existing knowledge.
Always answer in the same language as the user's question."""

    def __init__(
        self,
        llm: BaseChatModel,
        search_client: Optional[SearchClient] = None,
        *,
        termination_judge_llm: Optional[BaseChatModel] = None,
        data_path: Optional[str] = None,
        reranker: Optional[Any] = None,
        min_rerank_score: float = 0.0,
        max_per_domain: int = 1,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        show_timings: bool = False,
        google_api_key: Optional[str] = None,
        finnhub_api_key: Optional[str] = None,
        sportsdb_api_key: Optional[str] = None,
        apisports_api_key: Optional[str] = None,
        # Search source metadata
        requested_search_sources: Optional[List[str]] = None,
        active_search_sources: Optional[List[str]] = None,
        active_search_source_labels: Optional[List[str]] = None,
        missing_search_sources: Optional[List[str]] = None,
        configured_search_sources: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.llm = llm
        self.config = config or {}
        self.search_client = search_client
        self.data_path = data_path
        self.reranker = reranker
        self.min_rerank_score = min_rerank_score
        self.max_per_domain = max(1, max_per_domain)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.show_timings = show_timings
        self.google_api_key = google_api_key
        self.termination_judge_llm = termination_judge_llm or self.llm
        termination_raw = self.config.get("termination") or {}
        self.termination_config = normalize_termination_config(termination_raw)
        judge_raw = termination_raw.get("judge") or {}
        self.termination_config["judge"] = {
            "enabled": bool(judge_raw.get("enabled", False)),
            "interval": self.termination_config["judge_interval"],
        }
        self.orchestration_config = self._normalize_orchestration_config(
            self.config.get("orchestration") or {}
        )
        self._policy_registry = EvidencePolicyRegistry()
        self._current_analysis: Optional[QueryAnalysis] = None
        self._current_ledger: Optional[EvidenceLedger] = None
        self._current_execution_trace: Optional[QueryExecutionTrace] = None
        self._loop_orchestrator: Optional[Any] = None

        skill_config = dict(self.config)
        if google_api_key:
            skill_config["GOOGLE_API_KEY"] = google_api_key
        if finnhub_api_key:
            skill_config["FINNHUB_API_KEY"] = finnhub_api_key
        if sportsdb_api_key:
            skill_config["SPORTSDB_API_KEY"] = sportsdb_api_key
        if apisports_api_key:
            skill_config["APISPORTS_KEY"] = apisports_api_key
        self.skill_registry = SkillRegistry.from_config(skill_config)
        
        # Search source metadata
        self.requested_search_sources = self._normalize_sources(requested_search_sources)
        self.active_search_sources = self._normalize_sources(
            active_search_sources or getattr(search_client, "active_sources", [])
        )
        self.active_search_source_labels = [
            str(label).strip() for label in (active_search_source_labels or []) if str(label).strip()
        ]
        self.missing_search_sources = self._normalize_sources(missing_search_sources)
        self.configured_search_sources = self._normalize_sources(configured_search_sources)
        

    @staticmethod
    def _normalize_orchestration_config(config: Dict[str, Any]) -> Dict[str, Any]:
        """Keep only the analysis/ledger feature boundary after M5."""
        return {"enabled": bool(config.get("enabled", True))}

    def _initialize_orchestration(
        self,
        query: str,
        *,
        allow_search: bool,
        time_constraint: TimeConstraint,
    ) -> None:
        """Create the per-turn analysis and trace before any evidence work."""
        self._current_analysis = None
        self._current_ledger = None
        self._current_execution_trace = None
        if not self.orchestration_config["enabled"]:
            return

        analysis = analyze_query(
            query,
            allow_search=allow_search,
            requested_sources=self.requested_search_sources,
            time_constraint=time_constraint,
        )
        trace = QueryExecutionTrace(
            configured=self.configured_search_sources,
            requested=self.requested_search_sources,
            eligible=self.active_search_sources,
        )
        trace.record_analysis(analysis)
        self._current_analysis = analysis
        self._current_execution_trace = trace


    def _build_clarification_response(
        self,
        query: str,
        *,
        has_local_docs: bool,
    ) -> Dict[str, Any]:
        """Stop before tools when deterministic analysis cannot name its target."""
        analysis = self._current_analysis
        ambiguities = list(analysis.ambiguities) if analysis is not None else []
        is_chinese = any("\u4e00" <= char <= "\u9fff" for char in query)
        details = "、".join(ambiguities) if ambiguities else "关键实体或约束"
        answer = (
            f"需要先澄清 {details}，才能开始检索并比较。请补充要比较的准确对象或范围。"
            if is_chinese
            else "I need the exact entities or constraints before searching, so I do not guess the comparison target."
        )
        result = {
            "query": query,
            "answer": answer,
            "search_hits": [],
            "retrieved_docs": [],
            "evidence_items": [],
            "evidence_summary": "",
            "evidence_sources_active": [],
            "evidence_sources_used": [],
            "evidence_source_types_active": [],
            "evidence_source_types_used": [],
            "control": {
                "search_performed": False,
                "decision": {"needs_search": False, "reason": "clarification_required"},
                "search_mode": "clarification_required",
                "local_docs_present": has_local_docs,
                "search_allowed": bool(analysis.search_allowed) if analysis else True,
                "final_executor": "clarification_required",
            },
        }
        if self._current_execution_trace is not None:
            self._current_execution_trace.record_termination(
                {
                    "status": "clarification_required",
                    "reason": "critical_ambiguity",
                    "constraints_missing": ambiguities,
                }
            )
        return result

    def _attach_orchestration_metadata(self, result: Dict[str, Any]) -> None:
        """Attach the analysis, ledger, and execution trace for this turn."""
        if not self.orchestration_config["enabled"]:
            return
        control = result.setdefault("control", {})
        analysis = self._current_analysis
        ledger = self._current_ledger
        trace = self._current_execution_trace
        if analysis is not None:
            control.setdefault("query_analysis", analysis.to_dict())
        if ledger is not None:
            if not ledger.entries:
                ledger.ingest(result.get("evidence_items") or [])
                ledger.apply_limits()
            control.setdefault("evidence_coverage", ledger.coverage_summary())
        if trace is not None:
            control.setdefault("execution_trace", trace.to_dict())
            control.setdefault(
                "providers",
                {
                    "configured": list(trace.configured),
                    "requested": list(trace.requested),
                    "eligible": list(trace.eligible),
                    "executed": list(trace.executed),
                },
            )

    @staticmethod
    def _normalize_sources(sources: Optional[List[str]]) -> List[str]:
        """Normalize source list."""
        return normalize_sources(sources)

    def _is_small_talk(self, query: str) -> bool:
        """Check if query is small talk."""
        return is_small_talk_query(query)

    def _snapshot_local_docs(self) -> Optional[tuple]:
        """Create a snapshot of local documents for cache invalidation."""
        if not self.data_path or not os.path.isdir(self.data_path):
            return None
        
        records = []
        for root, _, files in os.walk(self.data_path):
            for name in files:
                if name.lower().endswith((".txt", ".md", ".pdf")):
                    full_path = os.path.join(root, name)
                    try:
                        records.append((full_path, os.path.getmtime(full_path)))
                    except OSError:
                        continue
        return tuple(sorted(records))

    def _direct_answer(
        self,
        query: str,
        timing_recorder: Optional[TimingRecorder] = None,
        images: Optional[List[Dict[str, str]]] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate direct answer without search."""
        messages = [
            SystemMessage(content=system_prompt or self.DIRECT_ANSWER_SYSTEM_PROMPT),
        ]
        
        if images:
            content_list = [{"type": "text", "text": query}]
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
            messages.append(HumanMessage(content=query))
        
        start = time.perf_counter()
        usage_response = None
        try:
            response = self.llm.invoke(messages)
            usage_response = response
            content = response.content if hasattr(response, 'content') else str(response)
            return {
                "content": content,
                "raw": response.response_metadata if hasattr(response, "response_metadata") else None,
            }
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            if timing_recorder:
                duration_ms = (time.perf_counter() - start) * 1000
                timing_recorder.record_llm_call(
                    label="direct_answer",
                    duration_ms=duration_ms,
                    provider=getattr(self.llm, "provider", None),
                    model=getattr(self.llm, "model_name", None),
                    extra=extract_token_usage(usage_response),
                )

    def answer(
        self,
        query: str,
        *,
        num_search_results: int = 10,
        per_source_search_results: Optional[int] = None,
        num_retrieved_docs: int = 5,
        max_tokens: int = 8000,
        temperature: float = 0.3,
        allow_search: bool = True,
        reference_limit: Optional[int] = None,
        force_search: bool = False,
        images: Optional[List[Dict[str, str]]] = None,
        conversation_id: Optional[str] = None,
        tracer: Optional[Any] = None,
        audit_mode: Optional[str] = None,
        search_depth: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Answer through the sole act/observe/evaluate executor."""
        audit_settings = resolve_audit_settings(self.config)
        audit_override = (audit_mode or "").strip().lower()
        if audit_override == "off":
            audit_active = False
            audit_external = False
        elif audit_override == "external":
            audit_active = True
            audit_external = True
        else:
            audit_active = bool(audit_settings.get("enabled"))
            audit_external = False
        self._audit_settings = audit_settings if audit_active else None
        self._audit_external = audit_external
        if audit_active and tracer is None:
            tracer = WorkflowTracer()
        else:
            tracer = ensure_tracer(tracer)
        self._current_tracer = tracer

        timing_recorder = TimingRecorder(enabled=self.show_timings or audit_active)
        timing_recorder.start()
        applied_search_depth = apply_search_depth_override(
            self.search_client, search_depth
        )

        self._conversation_id = conversation_id
        self._conversation_query = query
        self._conversation_allow_search = allow_search
        self._current_time_constraint = None
        self._topic_reset = False

        total_limit = max(1, int(num_search_results))
        per_source_limit = max(1, int(per_source_search_results or total_limit))
        force_search = bool(force_search and allow_search)
        time_constraint = parse_time_constraint(query)
        self._current_time_constraint = time_constraint if time_constraint.days else None
        effective_query = time_constraint.cleaned_query if time_constraint.days else query
        if time_constraint.days:
            effective_query = (
                f"{effective_query} (Current Date: {get_current_date_str()})"
            )

        self._initialize_orchestration(
            query,
            allow_search=allow_search,
            time_constraint=time_constraint,
        )
        snapshot = self._snapshot_local_docs()
        has_docs = bool(snapshot)

        if images:
            tracer.begin("visual", "图片理解", detail="正在解析图片内容")
            result = self._handle_visual_query(
                query,
                images,
                max_tokens,
                temperature,
                has_docs,
                allow_search,
                timing_recorder,
            )
            tracer.end("visual", detail="已生成回答")
            return self._finalize_response(result, timing_recorder)

        if self._is_small_talk(effective_query):
            tracer.begin("intent", "意图理解")
            tracer.end("intent", detail="识别为闲聊")
            tracer.begin("generate", "生成回答")
            result = self._handle_small_talk(
                query,
                max_tokens,
                temperature,
                has_docs,
                allow_search,
                timing_recorder,
            )
            tracer.end("generate", detail="模型直答")
            return self._finalize_response(result, timing_recorder)

        # Deterministic skill parsing may resolve a generic ambiguity before
        # retrieval, but never executes outside the model-controlled loop.
        matched_skill = self.skill_registry.match_query(query)
        if matched_skill is not None and self._current_analysis is not None:
            preflight = matched_skill.preflight({"query": query})
            if preflight.accepted:
                self._current_analysis.ambiguities = [
                    value
                    for value in self._current_analysis.ambiguities
                    if value != "unresolved_entity_reference"
                ]
                self._current_analysis.critical_ambiguity = bool(
                    self._current_analysis.ambiguities
                )

        if (
            self._current_analysis is not None
            and self._current_analysis.critical_ambiguity
        ):
            result = self._build_clarification_response(
                query,
                has_local_docs=has_docs,
            )
            return self._finalize_response(result, timing_recorder)

        tracer.begin("loop", "Agentic Loop", detail="act / observe / evaluate")
        result = self._run_loop_executor(
            query=query,
            effective_query=effective_query,
            allow_search=allow_search,
            conversation_id=conversation_id,
            time_constraint=time_constraint,
            num_search_results=total_limit,
            per_source_limit=per_source_limit,
            num_retrieved_docs=num_retrieved_docs,
            max_tokens=max_tokens,
            temperature=temperature,
            reference_limit=reference_limit,
            force_search=force_search,
            timing_recorder=timing_recorder,
            tracer=tracer,
        )
        control = result.setdefault("control", {})
        if applied_search_depth:
            control["search_depth"] = applied_search_depth
        tracer.end(
            "loop",
            detail=(
                f"{control.get('loop_status', 'done')} · "
                f"{control.get('loop_iterations', '?')} 轮"
            ),
            status="done" if control.get("loop_status") == "succeeded" else None,
        )
        return self._finalize_response(result, timing_recorder)

    def _handle_visual_query(
        self,
        query: str,
        images: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
        has_docs: bool,
        allow_search: bool,
        timing_recorder: Optional[TimingRecorder],
    ) -> Dict[str, Any]:
        """Handle queries with images."""
        # Check if LLM supports vision
        vision_keywords = ["grok", "gpt-4", "claude", "gemini", "glm-4v", "glm-4.5v", "claude-4.5-haiku", "vision", "minimax"]
        is_vision_model = any(k in self.llm.model_name.lower() if hasattr(self.llm, 'model_name') else '' for k in vision_keywords)
        
        # Try to get visual metadata from Google Vision API if available
        visual_context = ""
        if self.google_api_key:
            try:
                visual_info = self._perform_visual_retrieval(images, timing_recorder)
                if visual_info:
                    labels = [label.get("label") for label in visual_info.get("bestGuessLabels", [])]
                    entities = [entity.get("description") for entity in visual_info.get("webEntities", []) if entity.get("description")]
                    
                    visual_context = (
                        "我通过搜索引擎找到了关于这张图片的以下线索（元数据）：\n\n"
                        f"最佳猜测标签：[{', '.join(labels)}]\n\n"
                        f"关联实体：[{', '.join(entities)}]\n\n"
                        "请结合图片内容和上述线索回答用户的问题。如果线索不足，请诚实告知。请始终使用与用户提问相同的语言回答。"
                    )
            except Exception as e:
                print(f"Visual retrieval failed: {e}")
        
        if is_vision_model:
            system_prompt = "你是一个智能视觉助手。用户上传了一张图片。"
            if visual_context:
                system_prompt += "\n\n" + visual_context
            else:
                system_prompt += "\n请结合图片内容回答用户的问题。请始终使用与用户提问相同的语言回答。"
        else:
            system_prompt = "你是一个智能助手。用户上传了图片，但你无法查看图片内容。"
            if visual_context:
                system_prompt += "\n\n" + visual_context
                system_prompt += "\n\n虽然你无法直接查看图片，但可以根据上述元数据信息尝试回答用户的问题。请始终使用与用户提问相同的语言回答。"
            else:
                system_prompt += "\n请明确告知用户你无法查看图片，并询问他们是否可以描述图片内容或提供其他相关信息。请始终使用与用户提问相同的语言回答。"
        
        direct = self._direct_answer(
            query, timing_recorder, images=images, system_prompt=system_prompt
        )
        
        return {
            "query": query,
            "answer": direct.get("content", ""),
            "search_hits": [],
            "llm_raw": direct.get("raw"),
            "llm_error": direct.get("error"),
            "control": {
                "search_performed": False,
                "decision": {"needs_search": False, "reason": "image_content_present"},
                "search_mode": "image_content_present",
                "local_docs_present": has_docs,
                "search_allowed": allow_search,
            },
        }

    def _perform_visual_retrieval(self, images: List[Dict[str, str]], timing_recorder: Optional[TimingRecorder]) -> Optional[Dict[str, Any]]:
        if not self.google_api_key or not images:
            return None
        
        start = time.perf_counter()
        try:
            # Use the first image for visual retrieval
            img = images[0]
            b64_content = img.get("base64", "")
            if "," in b64_content:
                b64_content = b64_content.split(",")[1]
            
            url = f"https://vision.googleapis.com/v1/images:annotate?key={self.google_api_key}"
            payload = {
                "requests": [
                    {
                        "image": {
                            "content": b64_content
                        },
                        "features": [
                            {
                                "type": "WEB_DETECTION"
                            }
                        ]
                    }
                ]
            }
            
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            result = response.json()
            
            responses = result.get("responses", [])
            if responses:
                web_detection = responses[0].get("webDetection", {})
                return web_detection
            return None
            
        except Exception as e:
            print(f"Google Cloud Vision API error: {e}")
            return None
        finally:
            if timing_recorder:
                duration_ms = (time.perf_counter() - start) * 1000
                timing_recorder.record_tool_call(
                    tool="google_vision",
                    duration_ms=duration_ms,
                    success=True # Simplified
                )

    def _handle_small_talk(
        self,
        query: str,
        max_tokens: int,
        temperature: float,
        has_docs: bool,
        allow_search: bool,
        timing_recorder: Optional[TimingRecorder],
    ) -> Dict[str, Any]:
        """Handle small talk queries."""
        direct = self._direct_answer(query, timing_recorder)
        
        return {
            "query": query,
            "answer": direct.get("content", ""),
            "search_hits": [],
            "llm_raw": direct.get("raw"),
            "llm_error": direct.get("error"),
            "control": {
                "search_performed": False,
                "decision": {"needs_search": False, "reason": "small_talk_heuristic"},
                "search_mode": "small_talk",
                "local_docs_present": has_docs,
                "search_allowed": allow_search,
            },
        }

    def _finalize_response(
        self,
        result: Dict[str, Any],
        timing_recorder: TimingRecorder,
    ) -> Dict[str, Any]:
        """Finalize response with timing and metadata."""
        control = result.get("control", {})
        control.setdefault("final_executor", "agentic_loop")
        control.setdefault(
            "termination_policy",
            {
                "max_iterations": self.termination_config["max_iterations"],
                "judge_interval": self.termination_config["judge_interval"],
                "judge_enabled": self.termination_config["judge"]["enabled"],
                "repeat_threshold": self.termination_config["repeat_threshold"],
                "no_progress_threshold": self.termination_config["no_progress_threshold"],
                "tool_error_threshold": self.termination_config["tool_error_threshold"],
            },
        )
        
        # Add search source metadata
        control.setdefault("search_sources_requested", self.requested_search_sources)
        control.setdefault("search_sources_active", self.active_search_sources)
        control.setdefault("search_sources_configured", self.configured_search_sources)
        if self.missing_search_sources:
            control.setdefault("search_sources_missing", self.missing_search_sources)
        control.setdefault("evidence_sources_active", result.get("evidence_sources_active") or [])
        control.setdefault("evidence_sources_used", result.get("evidence_sources_used") or [])
        control.setdefault("evidence_source_types_active", result.get("evidence_source_types_active") or [])
        control.setdefault("evidence_source_types_used", result.get("evidence_source_types_used") or [])
        
        result["control"] = control
        self._attach_orchestration_metadata(result)
        
        # Add timing information
        if timing_recorder.enabled:
            timing_recorder.stop()
            timing_payload = timing_recorder.to_dict()
            if timing_payload:
                domain = control.get("domain", "")
                timing_payload["领域智能类型"] = domain if domain and domain.lower() != "general" else "无"
                result["response_times"] = timing_payload

        self._record_conversation_turn(result)
        self._record_audit_turn(result)
        return result

    # ------------------------------------------------------------------
    # Conversation resume
    # ------------------------------------------------------------------
    def _record_conversation_turn(self, result: Dict[str, Any]) -> None:
        """Persist this turn to the conversation record (all execution paths).

        The full ``result`` is stored so the sidebar can fully restore the turn
        (workflow metadata, sources, retrieved docs, timings and warnings).
        """
        cid = getattr(self, "_conversation_id", None)
        if not cid:
            return
        try:
            from orchestrators.conversation_store import get_conversation_manager

            mgr = get_conversation_manager()
            if not mgr.enabled:
                return
            mgr.record_turn(
                cid,
                getattr(self, "_conversation_query", ""),
                result.get("answer", "") or "",
                getattr(self, "_current_time_constraint", None),
                topic_reset=getattr(self, "_topic_reset", False),
                result=result if isinstance(result, dict) else None,
            )
        except Exception as exc:  # noqa: BLE001 - recording must never break a response
            print(f"[conversation] record_turn failed: {exc}")

    def _record_audit_turn(self, result: Dict[str, Any]) -> None:
        """Persist this turn's process audit when enabled (all execution paths)."""
        settings = getattr(self, "_audit_settings", None)
        if not settings or getattr(self, "_audit_external", False):
            return
        try:
            tracer = getattr(self, "_current_tracer", None)
            events = list(getattr(tracer, "events", None) or [])
            recorder = AuditRecorder(
                settings.get("dir"),
                include_answer=bool(settings.get("include_answer", True)),
                include_full_result=bool(settings.get("include_full_result", False)),
                max_files=int(settings.get("max_files", 200)),
                max_bytes_per_record=int(settings.get("max_bytes_per_record", 65536)),
            )
            recorder.record_turn(
                conversation_id=getattr(self, "_conversation_id", None),
                query=getattr(self, "_conversation_query", "") or "",
                allow_search=bool(getattr(self, "_conversation_allow_search", True)),
                events=events,
                result=result,
            )
        except Exception as exc:  # noqa: BLE001 - audit must never break a response
            print(f"[audit] record failed: {exc}")

    def _get_loop_orchestrator(self) -> Any:
        """Lazily create the sole LangGraph executor."""
        if self._loop_orchestrator is not None:
            return self._loop_orchestrator
        from orchestrators.react_agent_orchestrator import ReactAgentOrchestrator

        max_iterations = self.termination_config["max_iterations"]
        self._loop_orchestrator = ReactAgentOrchestrator.create_from_config(
            config=self.config,
            llm=self.llm,
            search_client=self.search_client,
            data_path=self.data_path,
            max_iterations=max_iterations,
            show_timings=True,
            judge_llm=self.termination_judge_llm,
        )
        return self._loop_orchestrator

    def _run_loop_executor(
        self,
        *,
        query: str,
        effective_query: str,
        allow_search: bool,
        conversation_id: Optional[str],
        time_constraint: TimeConstraint,
        num_search_results: int,
        per_source_limit: int,
        num_retrieved_docs: int,
        max_tokens: int,
        temperature: float,
        reference_limit: Optional[int],
        force_search: bool,
        timing_recorder: TimingRecorder,
        tracer: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Delegate every non-shortcut query to the sole loop executor."""
        orchestrator = self._get_loop_orchestrator()

        result = orchestrator.answer(
            query,
            num_search_results=num_search_results,
            per_source_search_results=per_source_limit,
            num_retrieved_docs=num_retrieved_docs,
            max_tokens=max_tokens,
            temperature=temperature,
            allow_search=allow_search,
            reference_limit=reference_limit,
            force_search=force_search,
            conversation_id=conversation_id,
            analysis=self._current_analysis,
            execution_trace=self._current_execution_trace,
            tracer=tracer,
        )
        timing_recorder.merge_payload(result.get("response_times"))
        self._ingest_loop_evidence(result, num_search_results=num_search_results)
        control = result.setdefault("control", {})
        control["final_executor"] = "agentic_loop"
        return result

    def _ingest_loop_evidence(self, result: Dict[str, Any], *, num_search_results: int) -> None:
        """Route actual loop observations through the sole evidence ledger."""
        records = [
            record
            for record in list(result.get("evidence_records") or [])
            if isinstance(record, dict)
        ]

        analysis = self._current_analysis
        if analysis is None or not self.orchestration_config["enabled"]:
            result["evidence_items"] = [
                self._evidence_item_from_record(rec).to_dict() for rec in records
            ]
            return

        ledger = EvidenceLedger(
            analysis,
            policies=self._policy_registry.derive(analysis),
            result_budget=num_search_results,
        )
        self._current_ledger = ledger

        items = [self._evidence_item_from_record(rec) for rec in records]
        if items:
            ledger.ingest(items)
        ledger.apply_limits()
        retained = ledger.retained_items() or ledger.limited_items()
        result["evidence_items"] = [item.to_dict() for item in retained]
        trace = self._current_execution_trace
        if trace is not None:
            # The loop records every attempted call directly. Keep this
            # grouped fallback for isolated adapters/tests that only provide
            # evidence records, without turning one result item into one call.
            existing_steps = {
                str(event.get("step_id") or "")
                for event in trace.events
                if isinstance(event, dict) and event.get("kind") == "tool_call"
            }
            grouped: Dict[tuple[int, int], List[Dict[str, Any]]] = {}
            for record in records:
                key = (
                    int(record.get("iteration") or 0),
                    int(record.get("position") or 0),
                )
                grouped.setdefault(key, []).append(record)
            for (iteration, position), call_records in grouped.items():
                step_id = f"tool_{max(0, iteration)}_{max(0, position)}"
                if step_id in existing_steps:
                    continue
                record = call_records[0]
                trace.record_tool_call(
                    tool=str(record.get("tool_name") or "unknown"),
                    status=str(record.get("status") or "done"),
                    iteration=iteration,
                    position=position,
                    query=str(record.get("query") or "") or None,
                    source_type=str(record.get("source_type") or "web"),
                    source_tier=str(record.get("source_tier") or "") or None,
                    item_count=len(call_records),
                    reason=str(record.get("reason") or "") or None,
                )
            trace.record_ledger(ledger)
            verdicts = list((result.get("control") or {}).get("loop_verdicts") or [])
            if verdicts:
                trace.record_termination(verdicts[-1])

    @staticmethod
    def _evidence_item_from_record(record: Any) -> EvidenceItem:
        """Build an :class:`EvidenceItem` carrying loop provenance (I1)."""

        if not isinstance(record, dict):
            record = {}
        tool_name = str(record.get("tool_name") or "unknown")
        iteration = int(record.get("iteration") or 0)
        position = int(record.get("position") or 0)
        query = str(record.get("query") or "")
        content = str(record.get("content") or "")
        tool_call_id = f"react_tool_{iteration}_{position}"
        reference = str(record.get("reference") or f"react:{tool_name}:{iteration}:{position}")
        return EvidenceItem(
            source_type=str(record.get("source_type") or "web"),
            source_id=tool_name,
            title=(query or tool_name)[:160],
            content=content,
            reference=reference,
            snippet=content[:400],
            metadata={
                "originating_tool_call": tool_call_id,
                "retrieval_kind": "agentic_loop",
                "source_tier": str(record.get("source_tier") or "unknown"),
                "tool_query": query,
                "tool_iteration": iteration,
                "tool_position": position,
            },
        )


# Factory function
def create_langchain_orchestrator(
    config: Optional[Dict[str, Any]] = None,
    llm: Optional[BaseChatModel] = None,
    search_client: Optional[SearchClient] = None,
    **kwargs: Any,
) -> LangChainOrchestrator:
    """Create a LangChain orchestrator from configuration.
    
    Args:
        config: Configuration dictionary (loaded from config.json if not provided)
        llm: LangChain chat model (created from config if not provided)
        search_client: Search client for web search
        **kwargs: Additional arguments passed to LangChainOrchestrator
    
    Returns:
        Configured LangChainOrchestrator instance
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
    
    if llm is None:
        from langchain.langchain_llm import create_chat_model
        llm = create_chat_model(config=config)
    
    # Create the optional semantic judge used by the shared termination critic.
    termination_judge_llm = None
    termination_cfg = config.get("termination") or {}
    judge_cfg = termination_cfg.get("judge") or {}
    if judge_cfg.get("enabled", False):
        provider = judge_cfg.get("provider") or judge_cfg.get("model")
        if provider:
            from langchain.langchain_llm import create_role_chat_model

            termination_judge_llm = create_role_chat_model(config, judge_cfg)
    
    return LangChainOrchestrator(
        llm=llm,
        search_client=search_client,
        termination_judge_llm=termination_judge_llm,
        google_api_key=config.get("googleSearch", {}).get("api_key") or config.get("GOOGLE_API_KEY"),
        sportsdb_api_key=config.get("SPORTSDB_API_KEY"),
        apisports_api_key=config.get("APISPORTS_KEY"),
        config=config,
        show_timings=kwargs.pop("show_timings", False),
        **kwargs,
    )
