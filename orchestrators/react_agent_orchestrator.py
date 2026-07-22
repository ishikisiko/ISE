"""React Agent Orchestrator - LangChain ReAct-based query handling.

This module provides an alternative orchestrator implementation using
LangChain's ReAct agent for iterative reasoning and tool calling.
"""

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
    ReActSearchTool,
    ReActSearchRecoveryTool,
    ReActDomainTool,
    ReActLocalDocTool,
)
from langchain.langchain_orchestrator import LangChainOrchestrator
from search.search import SearchClient
from utils.timing_utils import TimingRecorder


class ReactAgentOrchestrator:
    """ReAct-based orchestrator using LangChain agent for iterative reasoning.

    This orchestrator uses LangChain's ReAct agent to iteratively reason about
    queries and call tools (web search, domain API, local docs) as needed.
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
        engine: Optional[str] = None,
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
            engine: "langgraph" for the explicit state-machine loop, "legacy"
                for the AgentExecutor loop; defaults to config reactAgent.engine
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

        configured_engine = (
            engine
            or (self.config.get("reactAgent", {}) or {}).get("engine")
            or "langgraph"
        )
        self.engine = str(configured_engine).strip().lower() or "legacy"

        self._agent_executor = None
        if self.engine == "langgraph":
            from orchestrators.react_loop_graph import langgraph_available

            if not langgraph_available():
                print("[react_agent] langgraph 未安装，回退到 legacy AgentExecutor 引擎")
                self.engine = "legacy"

        if self.engine != "langgraph":
            self.engine = "legacy"
            # Create the ReAct agent executor
            self._agent_executor = LangChainOrchestrator.create_react_agent(
                llm=llm,
                tools=self.tools,
                max_iterations=max_iterations,
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
        fallback_context: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
        tracer: Optional[Any] = None,
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

        Returns:
            Dictionary with answer, control metadata, and search_hits
        """
        timing_recorder = TimingRecorder(enabled=self.show_timings)
        timing_recorder.start()

        if self.engine == "langgraph":
            return self._answer_with_langgraph(
                query,
                allow_search=allow_search,
                fallback_context=fallback_context,
                conversation_id=conversation_id,
                tracer=tracer,
                timing_recorder=timing_recorder,
            )

        try:
            # Build input for the agent
            agent_input = {"input": self._build_agent_input(query, fallback_context)}

            # Execute the agent
            result = self._agent_executor.invoke(agent_input)

            # Parse the output
            output = result.get("output", "")

            # Build response structure compatible with other orchestrators
            search_hits = self._extract_search_hits(output)

            response: Dict[str, Any] = {
                "query": query,
                "answer": output,
                "search_hits": search_hits or list(fallback_context.get("search_hits") or []) if fallback_context else search_hits,
                "evidence_items": list(fallback_context.get("evidence_items") or []) if fallback_context else [],
                "evidence_sources_active": list(fallback_context.get("evidence_sources_active") or []) if fallback_context else [],
                "evidence_sources_used": list(fallback_context.get("evidence_sources_used") or []) if fallback_context else [],
                "evidence_source_types_active": list(fallback_context.get("evidence_source_types_active") or []) if fallback_context else [],
                "evidence_source_types_used": list(fallback_context.get("evidence_source_types_used") or []) if fallback_context else [],
                "llm_raw": None,
                "llm_warning": None,
                "llm_error": None,
            }

            # Add control metadata
            control: Dict[str, Any] = {
                "search_performed": len(search_hits) > 0,
                "decision": {
                    "needs_search": len(search_hits) > 0,
                    "reason": "react_agent_iteration" if not fallback_context else "react_fallback_iteration",
                },
                "search_mode": "react_agent" if not fallback_context else "react_fallback",
                "keywords": [],
                "hybrid_mode": False,
                "local_docs_present": self._has_local_docs_tool(),
                "search_allowed": allow_search,
                "max_iterations": self.max_iterations,
                "engine": self.engine,
                "final_executor": "react_fallback" if fallback_context else "react_agent",
                "fallback_triggered": bool(fallback_context),
                "evidence_sources_active": response.get("evidence_sources_active") or [],
                "evidence_sources_used": response.get("evidence_sources_used") or [],
                "evidence_source_types_active": response.get("evidence_source_types_active") or [],
                "evidence_source_types_used": response.get("evidence_source_types_used") or [],
            }
            if fallback_context:
                control["fallback_context"] = self._fallback_context_meta(fallback_context)

            response["control"] = control

            # Add timing information if enabled
            if timing_recorder.enabled:
                timing_recorder.stop()
                timing_payload = timing_recorder.to_dict()
                if timing_payload:
                    timing_payload["领域智能类型"] = "ReAct Agent"
                    response["response_times"] = timing_payload

            return response

        except Exception as exc:
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
                    "engine": self.engine,
                },
            }

    def _answer_with_langgraph(
        self,
        query: str,
        *,
        allow_search: bool,
        fallback_context: Optional[Dict[str, Any]],
        tracer: Optional[Any],
        timing_recorder: TimingRecorder,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the explicit LangGraph loop and build a compatible response."""
        from orchestrators.react_loop_graph import ReactLoopGraphRunner
        from utils.workflow_trace import ensure_tracer

        tracer = ensure_tracer(tracer)
        evaluation_config = (self.config.get("reactAgent", {}) or {}).get("evaluation")
        user_input = self._build_agent_input(query, fallback_context)
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
            runner = ReactLoopGraphRunner(
                llm=self.llm,
                tools=self.tools,
                max_iterations=self.max_iterations,
                evaluation_config=evaluation_config,
                judge_llm=self.judge_llm,
                query=query,
                fallback_context=fallback_context,
                history_window=history_window,
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
                    "engine": self.engine,
                },
            }

        reason_labels = {
            "constraints_satisfied": "约束满足",
            "continue": "继续检索",
            "final_answer_rejected": "答案未达标，继续补充",
            "exhausted": "迭代用尽",
            "stagnated": "检索停滞",
            "unrecoverable": "工具持续失败",
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
        }
        loop_status = loop_result.get("loop_status")
        badge = status_badges.get(loop_status)
        tracer.end(
            "react_loop",
            detail=f"{badge['text']} · {loop_result.get('iterations')} 轮迭代" if badge else None,
            items=verdict_items,
            status="done" if loop_status == "succeeded" else "error",
            badge=badge,
        )

        search_hits = list(fallback_context.get("search_hits") or []) if fallback_context else []
        response: Dict[str, Any] = {
            "query": query,
            "answer": loop_result.get("answer") or "",
            "search_hits": search_hits,
            "evidence_items": list(fallback_context.get("evidence_items") or []) if fallback_context else [],
            "evidence_sources_active": list(fallback_context.get("evidence_sources_active") or []) if fallback_context else [],
            "evidence_sources_used": list(fallback_context.get("evidence_sources_used") or []) if fallback_context else [],
            "evidence_source_types_active": list(fallback_context.get("evidence_source_types_active") or []) if fallback_context else [],
            "evidence_source_types_used": list(fallback_context.get("evidence_source_types_used") or []) if fallback_context else [],
            "llm_raw": None,
            "llm_warning": None,
            "llm_error": None,
        }

        control: Dict[str, Any] = {
            "search_performed": bool(loop_result.get("search_hits")) or bool(search_hits),
            "decision": {
                "needs_search": True,
                "reason": "react_agent_iteration" if not fallback_context else "react_fallback_iteration",
            },
            "search_mode": "react_agent" if not fallback_context else "react_fallback",
            "keywords": [],
            "hybrid_mode": False,
            "local_docs_present": self._has_local_docs_tool(),
            "search_allowed": allow_search,
            "max_iterations": self.max_iterations,
            "engine": self.engine,
            "loop_status": loop_result.get("loop_status"),
            "loop_iterations": loop_result.get("iterations"),
            "loop_verdicts": list(loop_result.get("verdicts") or []),
            "loop_termination_reason": loop_result.get("termination_reason"),
            "final_executor": "react_fallback" if fallback_context else "react_agent",
            "fallback_triggered": bool(fallback_context),
            "conversation_resumed": resumed,
            "evidence_sources_active": response.get("evidence_sources_active") or [],
            "evidence_sources_used": response.get("evidence_sources_used") or [],
            "evidence_source_types_active": response.get("evidence_source_types_active") or [],
            "evidence_source_types_used": response.get("evidence_source_types_used") or [],
        }
        if loop_result.get("judge_error"):
            control["loop_judge_error"] = loop_result["judge_error"]
        if fallback_context:
            control["fallback_context"] = self._fallback_context_meta(fallback_context)

        response["control"] = control

        if timing_recorder.enabled:
            timing_recorder.stop()
            timing_payload = timing_recorder.to_dict()
            if timing_payload:
                timing_payload["领域智能类型"] = "ReAct Agent (LangGraph)"
                response["response_times"] = timing_payload

        return response

    def _extract_search_hits(self, output: str) -> List[Dict[str, Any]]:
        """Extract search hits from agent output if present."""
        # ReAct agent output doesn't typically contain structured search_hits
        # This is a placeholder that can be enhanced if needed
        return []

    def _build_agent_input(
        self,
        query: str,
        fallback_context: Optional[Dict[str, Any]],
    ) -> str:
        """Build the ReAct agent input, optionally with fallback context.

        When ``fallback_context`` carries a ``user_feedback`` key the message is
        framed as a human follow-up on the previous answer; otherwise the
        existing recovery-agent framing is used.
        """
        if not fallback_context:
            return query

        user_feedback = str(fallback_context.get("user_feedback") or "").strip()
        if user_feedback:
            lines = [
                "用户对上一轮回答提出反馈，请据此调整。",
                "",
                f"用户反馈：{user_feedback}",
            ]
            previous_answer = str(fallback_context.get("previous_answer") or "").strip()
            if previous_answer:
                lines.extend(["", f"上一轮回答：\n{previous_answer}"])
            missing_constraints = fallback_context.get("missing_constraints") or []
            if missing_constraints:
                lines.extend(["", f"需覆盖的约束：{', '.join(map(str, missing_constraints))}"])
            inherited_time = str(fallback_context.get("inherited_time_constraint") or "").strip()
            if inherited_time:
                lines.extend(["", f"继承的时间约束：{inherited_time}"])
            lines.extend(
                [
                    "",
                    "可直接修改上一轮回答（无需重新检索），或在需要时调用工具补充信息后重新作答。",
                ]
            )
            return "\n".join(lines)

        lines = [
            f"Original Query:\n{query}",
            "",
            "You are acting as a recovery agent. Improve the previous answer using tools only when needed.",
        ]

        previous_answer = str(fallback_context.get("previous_answer") or "").strip()
        if previous_answer:
            lines.extend(["", f"Previous Answer:\n{previous_answer}"])

        failure_types = fallback_context.get("failure_types") or []
        if failure_types:
            lines.extend(["", f"Post-check Failure Types: {', '.join(map(str, failure_types))}"])

        missing_constraints = fallback_context.get("missing_constraints") or []
        if missing_constraints:
            lines.extend(["", f"Missing Constraints: {', '.join(map(str, missing_constraints))}"])

        evidence_summary = str(fallback_context.get("evidence_summary") or "").strip()
        if evidence_summary:
            lines.extend(["", f"Available Evidence Summary:\n{evidence_summary}"])

        evidence_source_types = fallback_context.get("evidence_source_types_used") or []
        if evidence_source_types:
            lines.extend(["", f"Available Evidence Source Types: {', '.join(map(str, evidence_source_types))}"])

        recovery_goal = str(fallback_context.get("recovery_goal") or "").strip()
        if recovery_goal:
            lines.extend(["", f"Recovery Goal:\n{recovery_goal}"])

        return "\n".join(lines)

    def _fallback_context_meta(self, fallback_context: Dict[str, Any]) -> Dict[str, Any]:
        """Return safe metadata about the fallback context."""
        return {
            "failure_types": list(fallback_context.get("failure_types") or []),
            "missing_constraints": list(fallback_context.get("missing_constraints") or []),
            "has_previous_answer": bool(fallback_context.get("previous_answer")),
            "has_evidence_summary": bool(fallback_context.get("evidence_summary")),
            "evidence_source_types_active": list(fallback_context.get("evidence_source_types_active") or []),
            "evidence_source_types_used": list(fallback_context.get("evidence_source_types_used") or []),
        }

    def _has_local_docs_tool(self) -> bool:
        """Check if local docs tool is available."""
        return any(
            isinstance(t, ReActLocalDocTool) for t in self.tools
        )

    def _has_search_recovery_tool(self) -> bool:
        """Check if high-level search recovery tool is available."""
        return any(isinstance(t, ReActSearchRecoveryTool) for t in self.tools)

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
            max_iterations = config.get("reactAgent", {}).get("max_iterations", 5)

        # Create tools from config
        tools = create_react_tools_from_config(
            config=config,
            llm=llm,
            search_client=search_client,
            data_path=data_path,
        )

        show_timings = kwargs.pop("show_timings", config.get("displayResponseTimes", False))

        return cls(
            llm=llm,
            tools=tools,
            max_iterations=max_iterations,
            search_client=search_client,
            data_path=data_path,
            config=config,
            show_timings=show_timings,
            **kwargs,
        )
