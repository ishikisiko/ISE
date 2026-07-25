"""Production ReAct orchestrator backed by the explicit LangGraph loop."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain.langchain_react_tools import (
    create_react_tools_from_config,
    ReActLocalDocTool,
)
from search.search import SearchClient
from utils.query_orchestration import normalize_termination_config
from utils.timing_utils import TimingRecorder


class ReactAgentOrchestrator:
    """ReAct-based orchestrator using LangChain agent for iterative reasoning.

    This orchestrator uses LangChain's ReAct agent to iteratively reason about
    queries and call tools (web search, registered skills, local docs) as needed.
    """

    def __init__(
        self,
        llm: BaseChatModel,
        tools: Optional[List[Any]] = None,
        max_iterations: int = 5,
        *,
        search_client: Optional[SearchClient] = None,
        data_path: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        show_timings: bool = False,
        judge_llm: Optional[BaseChatModel] = None,
    ) -> None:
        """Initialize the ReactAgentOrchestrator.

        Args:
            llm: LangChain chat model
            tools: Optional list of tools; if not provided, created from config
            max_iterations: Maximum number of ReAct iterations
            search_client: Optional search client
            data_path: Optional path to local documents
            config: Optional configuration dictionary
            show_timings: Whether to record and return timing information
            judge_llm: Optional LLM used by the langgraph loop judge
        """
        self.llm = llm
        self.max_iterations = max_iterations
        self.show_timings = show_timings
        self.config = config or {}
        self.judge_llm = judge_llm

        # Use provided tools or create from config
        if tools:
            self.tools = tools
        else:
            self.tools = create_react_tools_from_config(
                config=self.config,
                llm=llm,
                search_client=search_client,
                data_path=data_path,
            )

        from orchestrators.react_loop_graph import langgraph_available

        if not langgraph_available():
            raise RuntimeError("langgraph is required for the unified M4 termination critic")

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
        analysis: Optional[Any] = None,
        execution_trace: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Answer a query using ReAct agent.

        Args:
            query: User query
            num_search_results: Number of search results (used if search tool called)
            per_source_search_results: Results per source
            num_retrieved_docs: Number of local docs to retrieve
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature
            allow_search: Whether to allow search
            reference_limit: Limit on reference sources in response
            force_search: Force search even if agent decides not to
            images: Optional image data (not currently used in ReAct mode)
            analysis: Optional shared ``QueryAnalysis`` (from ``analyze_query``).
                When supplied (M1 loop main path) the loop derives its success
                checklist from it instead of recomputing one from the query.

        Returns:
            Dictionary with answer, control metadata, and search_hits
        """
        self._reset_tool_budgets()
        timing_recorder = TimingRecorder(enabled=self.show_timings)
        timing_recorder.start()

        return self._answer_with_langgraph(
            query,
            allow_search=allow_search,
            conversation_id=conversation_id,
            tracer=tracer,
            timing_recorder=timing_recorder,
            analysis=analysis,
            execution_trace=execution_trace,
        )

    def _answer_with_langgraph(
        self,
        query: str,
        *,
        allow_search: bool,
        tracer: Optional[Any],
        timing_recorder: TimingRecorder,
        conversation_id: Optional[str] = None,
        analysis: Optional[Any] = None,
        execution_trace: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Run the explicit LangGraph loop and build a compatible response."""
        from orchestrators.react_loop_graph import ReactLoopGraphRunner
        from utils.workflow_trace import ensure_tracer

        tracer = ensure_tracer(tracer)
        termination_config = self.config.get("termination") or {}
        user_input = query
        history_window = 5
        resumed = False
        if conversation_id:
            from orchestrators.conversation_store import get_conversation_manager

            mgr = get_conversation_manager()
            if mgr.enabled:
                history_window = mgr.history_window
                resumed = bool(
                    mgr.has_checkpoint(conversation_id)
                    and not mgr.last_turn_is_topic_reset(conversation_id)
                )

        tracer.begin("react_loop", "ReAct 循环", detail="langgraph 引擎")
        try:
            active_tools = [
                tool
                for tool in self.tools
                if allow_search
                or tool.name not in {"web_search", "search_recovery", "fetch_url"}
            ]
            for tool in active_tools:
                bind_recorder = getattr(tool, "set_timing_recorder", None)
                if callable(bind_recorder):
                    bind_recorder(timing_recorder)
                bind_analysis = getattr(tool, "set_analysis", None)
                if callable(bind_analysis):
                    bind_analysis(analysis)
            runner = ReactLoopGraphRunner(
                llm=self.llm,
                tools=active_tools,
                max_iterations=self.max_iterations,
                termination_config=termination_config,
                judge_llm=self.judge_llm,
                query=query,
                history_window=history_window,
                tracer=tracer,
                analysis=analysis,
                timing_recorder=timing_recorder,
                execution_trace=execution_trace,
            )
            loop_result = runner.run(user_input, conversation_id=conversation_id)
            resumed = resumed or bool(loop_result.get("conversation_resumed"))
        except Exception as exc:
            tracer.error("react_loop", detail=str(exc))
            return {
                "query": query,
                "answer": f"Agent execution failed: {exc}",
                "search_hits": [],
                "llm_raw": None,
                "llm_warning": None,
                "llm_error": str(exc),
                "control": {
                    "search_performed": False,
                    "decision": {
                        "needs_search": False,
                        "reason": f"react_agent_error: {exc}",
                    },
                    "search_mode": "react_agent_error",
                    "keywords": [],
                    "hybrid_mode": False,
                    "local_docs_present": self._has_local_docs_tool(),
                    "search_allowed": allow_search,
                },
            }

        reason_labels = {
            "constraints_satisfied": "约束满足",
            "continue": "继续检索",
            "final_answer_rejected": "答案未达标，继续补充",
            "exhausted": "迭代用尽",
            "stagnated": "检索停滞",
            "unrecoverable": "工具持续失败",
            "evidence_insufficient": "证据不足",
            "clarification_required": "需要澄清",
            "invalid_tool_request": "工具调用格式无效",
            "process_narration": "过程性文本，继续补充",
        }
        verdict_items = [
            {
                "label": f"第 {v.get('iteration', '?')} 轮",
                "value": (
                    reason_labels.get(v.get("reason"), str(v.get("reason") or ""))
                    + (f"（缺：{'、'.join(v.get('constraints_missing') or [])}）" if v.get("constraints_missing") else "")
                ),
            }
            for v in (loop_result.get("verdicts") or [])
        ]
        status_badges = {
            "succeeded": {"text": "循环成功", "tone": "ok"},
            "exhausted": {"text": "迭代用尽", "tone": "warn"},
            "stagnated": {"text": "检索停滞", "tone": "warn"},
            "unrecoverable": {"text": "不可恢复", "tone": "err"},
            "evidence_insufficient": {"text": "证据不足", "tone": "warn"},
            "clarification_required": {"text": "需要澄清", "tone": "warn"},
        }
        loop_status = loop_result.get("loop_status")
        badge = status_badges.get(loop_status)
        tracer.end(
            "react_loop",
            detail=f"{badge['text']} · {loop_result.get('iterations')} 轮迭代" if badge else None,
            # Per-iteration evaluation events already carry detailed verdicts.
            # Keep this outer event as a compact status summary.
            items=None if loop_result.get("trace_events") else verdict_items,
            status="done" if loop_status == "succeeded" else "error",
            badge=badge,
        )

        search_hits: List[Dict[str, Any]] = []
        response: Dict[str, Any] = {
            "query": query,
            "answer": loop_result.get("answer") or "",
            "search_hits": search_hits,
            "evidence_items": [],
            "evidence_records": list(loop_result.get("evidence_records") or []),
            "evidence_sources_active": [],
            "evidence_sources_used": [],
            "evidence_source_types_active": [],
            "evidence_source_types_used": [],
            "llm_raw": None,
            "llm_warning": None,
            "llm_error": None,
        }

        evidence_records = list(loop_result.get("evidence_records") or [])
        seen_references: set[str] = set()
        for record in evidence_records:
            if not isinstance(record, dict) or record.get("source_type") != "web":
                continue
            reference = str(record.get("reference") or "").strip()
            if not reference or reference in seen_references:
                continue
            seen_references.add(reference)
            search_hits.append(
                {
                    "title": str(record.get("title") or reference),
                    "url": reference,
                    "snippet": str(record.get("content") or ""),
                }
            )
        response["search_hits"] = search_hits
        record_source_types = sorted(
            {str(rec.get("source_type") or "").strip() for rec in evidence_records if isinstance(rec, dict)}
            - {""}
        )
        skill_tools_used = sorted(
            {
                str(rec.get("tool_name") or "").strip()
                for rec in evidence_records
                if isinstance(rec, dict) and rec.get("source_type") == "domain"
            }
        )
        used_tools = sorted(
            {
                str(record.get("tool_name") or "").strip()
                for record in evidence_records
                if isinstance(record, dict) and str(record.get("tool_name") or "").strip()
            }
        )
        response["evidence_sources_active"] = [
            {"source_type": "tool", "source_id": tool.name, "display_name": tool.name}
            for tool in active_tools
        ]
        response["evidence_sources_used"] = [
            {"source_type": "tool", "source_id": name, "display_name": name}
            for name in used_tools
        ]
        response["evidence_source_types_active"] = sorted(
            {"tool" for _tool in active_tools}
        )
        response["evidence_source_types_used"] = record_source_types

        control: Dict[str, Any] = {
            "search_performed": bool(loop_result.get("search_hits")) or bool(search_hits) or bool(evidence_records),
            "decision": {
                "needs_search": True,
                "reason": "agentic_loop_iteration",
            },
            "search_mode": "agentic_loop",
            "keywords": [],
            "hybrid_mode": False,
            "local_docs_present": self._has_local_docs_tool(),
            "search_allowed": allow_search,
            "max_iterations": self.max_iterations,
            "loop_status": loop_result.get("loop_status"),
            "loop_iterations": loop_result.get("iterations"),
            "loop_verdicts": list(loop_result.get("verdicts") or []),
            "loop_termination_reason": loop_result.get("termination_reason"),
            "react_trace": list(loop_result.get("trace_events") or []),
            "react_trace_truncated": bool(loop_result.get("trace_truncated")),
            "final_executor": "agentic_loop",
            "conversation_resumed": resumed,
            "evidence_sources_active": response.get("evidence_sources_active") or [],
            "evidence_sources_used": response.get("evidence_sources_used") or [],
            "evidence_source_types_active": response.get("evidence_source_types_active") or [],
            "evidence_source_types_used": response.get("evidence_source_types_used") or record_source_types,
            "loop_evidence_records": len(evidence_records),
            "skill_tools_used": skill_tools_used,
            "termination_policy": {
                "max_iterations": self.max_iterations,
                "judge_interval": int(
                    (self.config.get("termination") or {}).get("judge_interval", 2) or 2
                ),
                "judge_enabled": bool(
                    ((self.config.get("termination") or {}).get("judge") or {}).get(
                        "enabled", True
                    )
                ),
                "tool_budgets": {
                    tool.name: status()
                    for tool in active_tools
                    if callable(status := getattr(tool, "get_budget_status", None))
                },
            },
        }
        if loop_result.get("judge_error"):
            control["loop_judge_error"] = loop_result["judge_error"]
        response["control"] = control

        if timing_recorder.enabled:
            timing_recorder.stop()
            timing_payload = timing_recorder.to_dict()
            if timing_payload:
                timing_payload["领域智能类型"] = "ReAct Agent (LangGraph)"
                response["response_times"] = timing_payload

        return response

    def _has_local_docs_tool(self) -> bool:
        """Check if local docs tool is available."""
        return any(
            isinstance(t, ReActLocalDocTool) for t in self.tools
        )

    def _reset_tool_budgets(self) -> None:
        for tool in self.tools:
            reset = getattr(tool, "reset_budget", None)
            if callable(reset):
                reset()

    @classmethod
    def create_from_config(
        cls,
        config: Optional[Dict[str, Any]] = None,
        llm: Optional[BaseChatModel] = None,
        search_client: Optional[SearchClient] = None,
        **kwargs: Any,
    ) -> "ReactAgentOrchestrator":
        """Create a ReactAgentOrchestrator from configuration.

        Args:
            config: Configuration dictionary
            llm: Optional LangChain chat model
            search_client: Optional search client
            **kwargs: Additional arguments passed to ReactAgentOrchestrator

        Returns:
            Configured ReactAgentOrchestrator instance
        """
        if config is None:
            import json as json_module
            import os
            config_path = os.getenv("NLP_CONFIG_PATH", "config.json")
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json_module.load(f)
            else:
                config = {}

        if llm is None:
            from langchain.langchain_llm import create_chat_model
            llm = create_chat_model(config=config)

        # Extract relevant config sections
        data_path = kwargs.pop("data_path", None) or config.get("dataPath") or config.get("data_path")

        # Get max iterations from config if not in kwargs
        max_iterations = kwargs.pop("max_iterations", None)
        if max_iterations is None:
            termination_raw = config.get("termination") or {}
            max_iterations = normalize_termination_config(termination_raw)["max_iterations"]

        # Create tools from config
        tools = create_react_tools_from_config(
            config=config,
            llm=llm,
            search_client=search_client,
            data_path=data_path,
        )

        show_timings = kwargs.pop("show_timings", config.get("displayResponseTimes", False))
        judge_llm = kwargs.pop("judge_llm", None)
        judge_cfg = (config.get("termination") or {}).get("judge") or {}
        if judge_llm is None and judge_cfg.get("enabled", False):
            provider = judge_cfg.get("provider") or judge_cfg.get("model")
            if provider:
                from langchain.langchain_llm import create_role_chat_model

                judge_llm = create_role_chat_model(config, judge_cfg)
            else:
                judge_llm = llm

        return cls(
            llm=llm,
            tools=tools,
            max_iterations=max_iterations,
            search_client=search_client,
            data_path=data_path,
            config=config,
            show_timings=show_timings,
            judge_llm=judge_llm,
            **kwargs,
        )
