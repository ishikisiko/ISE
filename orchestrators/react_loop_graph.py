"""LangGraph-based ReAct loop with explicit per-iteration evaluation.

This module implements the sole ReAct state machine (`act -> observe -> evaluate`).
Success and failure are decided by the shared M4 termination critic: the model
proposes, the critic disposes.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Dict, List, Optional, Tuple
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage

from utils.query_orchestration import (
    CriticBudgetState,
    CriticEvidenceState,
    TerminationAction,
    TerminationContext,
    check_constraint_coverage,
    evidence_increment_ratio,
    evaluate_termination,
    extract_numbers,
    normalize_termination_config,
)
from utils.retrieval_trace import emit_search_call_step, search_call_snapshots
from utils.search_routing import extract_json_object
from utils.timing_utils import extract_token_usage
from utils.time_parser import TimeConstraint
from utils.workflow_trace import WorkflowTracer, ensure_tracer

LOOP_STATUSES = (
    "succeeded",
    "exhausted",
    "stagnated",
    "unrecoverable",
    "evidence_insufficient",
    "clarification_required",
)

_FUNCTION_TAG = re.compile(
    r"<function>\s*(?P<name>[^<]{1,80}?)\s*</function>",
    re.IGNORECASE | re.DOTALL,
)
_QUERY_TAG = re.compile(
    r"<query>\s*(?P<query>.*?)\s*</query>",
    re.IGNORECASE | re.DOTALL,
)
_NUMBERED_RESULT = re.compile(r"(?m)^\s*\d+\.\s+")
_TRACE_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|authorization|cookie|token|secret|password)\s*([:=])\s*[^\s,;]+"
)
_TRACE_URL_QUERY = re.compile(r"https?://[^\s?#]+(?:\?[^\s#]*)?(?:#[^\s]*)?")
_TEXTUAL_TOOL_ERRORS = (
    "error:",
    "search failed:",
    "tool error:",
    "tool failed:",
)
_TRACE_EVENT_LIMIT = 40
_PROCESS_NARRATION_MARKERS = (
    "用户要求",
    "用户反馈",
    "我需要",
    "让我",
    "我明白",
    "我会",
    "我将",
    "接下来",
    "i need to",
    "let me ",
    "i will ",
    "the user asks",
    "based on the user",
)
_PROCESS_NARRATION_ACTION_MARKERS = (
    "搜索",
    "检索",
    "查询",
    "查找",
    "查阅",
    "查看",
    "浏览",
    "收集",
    "获取",
    "search",
    "look up",
    "review",
    "gather",
    "find",
)
_VERDICT_REASON_LABELS = {
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


def langgraph_available() -> bool:
    """Return True when langgraph can be imported (lazy check)."""
    try:
        import langgraph  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass
class LoopVerdict:
    """Structured per-iteration verdict produced by the evaluate node."""

    iteration: int
    new_evidence: bool = False
    constraints_met: List[str] = field(default_factory=list)
    constraints_missing: List[str] = field(default_factory=list)
    should_continue: bool = True
    reason: str = "continue"
    judge_used: bool = False
    judge_error: Optional[str] = None
    action: str = TerminationAction.CONTINUE.value
    deterministic_pass: bool = False
    hard_stop: bool = False
    failure_types: List[str] = field(default_factory=list)
    rule_hits: List[Dict[str, str]] = field(default_factory=list)
    evidence_sufficiency: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iteration": self.iteration,
            "new_evidence": self.new_evidence,
            "constraints_met": list(self.constraints_met),
            "constraints_missing": list(self.constraints_missing),
            "should_continue": self.should_continue,
            "reason": self.reason,
            "judge_used": self.judge_used,
            "judge_error": self.judge_error,
            "action": self.action,
            "deterministic_pass": self.deterministic_pass,
            "hard_stop": self.hard_stop,
            "failure_types": list(self.failure_types),
            "rule_hits": list(self.rule_hits),
            "evidence_sufficiency": self.evidence_sufficiency,
        }


TOOL_CALLING_SYSTEM_PROMPT = """你是一个智能搜索助手。你可以使用工具来收集信息回答用户问题。

规则：
- 需要更多信息时，调用合适的工具。
- 当你已经收集到足够信息时，直接给出完整的最终答案（不要再调用任何工具）。
- 最终答案应当具体、完整，覆盖问题中的所有要求。
- 不要输出检索计划、下一步说明或自我对话；非工具调用文本必须是面向用户的最终答案。
{success_criteria}"""


class ReactLoopGraphRunner:
    """Build and run the explicit ReAct loop graph for a single query."""

    def __init__(
        self,
        *,
        llm: BaseChatModel,
        tools: List[Any],
        max_iterations: int = 5,
        termination_config: Optional[Dict[str, Any]] = None,
        judge_llm: Optional[BaseChatModel] = None,
        query: str = "",
        time_constraint: Optional[TimeConstraint] = None,
        history_window: int = 5,
        tracer: Optional[Any] = None,
        analysis: Optional[Any] = None,
        timing_recorder: Optional[Any] = None,
        execution_trace: Optional[Any] = None,
    ) -> None:
        self.llm = llm
        self.tools = list(tools or [])
        self.tools_by_name = {getattr(t, "name", ""): t for t in self.tools}
        self.max_iterations = max(1, int(max_iterations or 5))
        self.eval_cfg = normalize_termination_config(termination_config)
        self.judge_llm = judge_llm
        self.query = query
        self.time_constraint = time_constraint
        self.analysis = analysis
        self.timing_recorder = timing_recorder
        self.execution_trace = execution_trace
        self.history_window = max(1, int(history_window))
        # Keep a private recorder for direct callers so the final response can
        # expose the same bounded facts even when no SSE listener is attached.
        self.tracer = ensure_tracer(tracer) if tracer is not None else WorkflowTracer()
        self._trace_start_index = 0
        self.initial_checklist = self._derive_checklist()
        self.system_prompt = TOOL_CALLING_SYSTEM_PROMPT.format(
            success_criteria=self._format_success_criteria()
        )
        self._llm_with_tools: Optional[Any] = None
        if self.tools:
            try:
                self._llm_with_tools = self.llm.bind_tools(self.tools)
            except NotImplementedError:
                self._llm_with_tools = None

    @property
    def _use_native_tools(self) -> bool:
        return self._llm_with_tools is not None

    def _shim_system_prompt(self) -> str:
        """JSON tool-calling instructions for models without native bind_tools."""
        specs = []
        for tool in self.tools:
            args = getattr(tool, "args", None) or {}
            specs.append(
                f"- {tool.name}: {getattr(tool, 'description', '')} 参数: {json.dumps(args, ensure_ascii=False)}"
            )
        tools_block = "\n".join(specs) if specs else "(无可用工具)"
        return (
            self.system_prompt
            + "\n\n可用工具：\n"
            + tools_block
            + "\n\n你必须只输出 JSON，二选一：\n"
            + '调用工具：{"action": "tool", "tool": "<工具名>", "args": {"query": "<查询>"}}\n'
            + '给出最终答案：{"action": "final", "answer": "<完整答案>"}'
        )

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------
    def _derive_checklist(self) -> List[str]:
        """Derive the initial constraint checklist.

        Shared ``QueryAnalysis.constraints`` is authoritative. A direct caller
        without analysis gets a deterministic fallback checklist.
        """
        checklist: List[str] = []
        analysis_constraints = getattr(self.analysis, "constraints", None)
        if isinstance(analysis_constraints, dict) and analysis_constraints:
            if analysis_constraints.get("temporal_required"):
                checklist.append("time_constraint")
            if analysis_constraints.get("comparison_required"):
                checklist.append("comparison")
            if checklist:
                return checklist

        _, derived = check_constraint_coverage(self.query, "", "", self.time_constraint)
        return derived

    def _format_success_criteria(self) -> str:
        parts: List[str] = []
        if self.initial_checklist:
            parts.append("本次回答必须满足：" + "、".join(self.initial_checklist))
        if not parts:
            return ""
        return "\n成功标准：\n" + "\n".join(f"- {p}" for p in parts)

    # ------------------------------------------------------------------
    # Safe workflow trace helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_trace_text(value: Any, *, limit: int = 180) -> str:
        """Return a bounded browser-safe trace value, never a raw transcript."""
        text = " ".join(str(value or "").split())
        text = _TRACE_SENSITIVE_ASSIGNMENT.sub(r"\1\2[redacted]", text)
        text = _TRACE_URL_QUERY.sub(
            lambda match: match.group(0).split("?", 1)[0].split("#", 1)[0],
            text,
        )
        if len(text) > limit:
            return text[: max(0, limit - 3)].rstrip() + "..."
        return text

    @staticmethod
    def _message_text(message: Any) -> str:
        """Extract textual content from an AI message without serializing metadata."""
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return str(content or "")

    @staticmethod
    def _iteration_step_id(iteration: int) -> str:
        return f"react_iteration_{iteration}"

    @staticmethod
    def _evaluation_step_id(iteration: int) -> str:
        return f"react_evaluate_{iteration}"

    @staticmethod
    def _tool_step_id(iteration: int, position: int) -> str:
        return f"react_tool_{iteration}_{position}"

    def _safe_tool_query(self, call: Dict[str, Any]) -> Optional[str]:
        args = call.get("args") or {}
        if not isinstance(args, dict):
            return None
        for key in ("query", "input"):
            value = args.get(key)
            if value is not None and str(value).strip():
                return self._safe_trace_text(value, limit=220)
        return None

    def _tool_trace_items(
        self,
        call: Dict[str, Any],
        *,
        result_value: str,
        failed: bool,
    ) -> List[Dict[str, str]]:
        items: List[Dict[str, str]] = []
        query = self._safe_tool_query(call)
        if query:
            items.append({"label": "查询", "value": query})

        if failed:
            summary = result_value.split(":", 1)[-1].strip() if ":" in result_value else result_value
            items.append({"label": "结果", "value": "调用失败"})
            if summary:
                items.append({"label": "原因", "value": self._safe_trace_text(summary, limit=160)})
            return items

        count = self._tool_result_count(call, result_value)
        if count is not None:
            items.append({"label": "结果", "value": f"{count} 条"})
        else:
            items.append({"label": "结果", "value": "已返回"})
        return items

    def _tool_result_count(self, call: Dict[str, Any], content: str) -> Optional[int]:
        """Infer a count only from the standard formatted result shape."""
        if str(call.get("name") or "") != "web_search":
            return None
        stripped = (content or "").strip().casefold()
        if stripped in {"no search results found.", "未找到相关结果"}:
            return 0
        count = len(_NUMBERED_RESULT.findall(content or ""))
        return count if count else None

    @staticmethod
    def _tool_search_api_calls(tool: Any) -> List[Dict[str, Any]]:
        """Read optional provider snapshots without parsing tool response text."""
        getter = getattr(tool, "get_last_search_api_calls", None)
        if not callable(getter):
            return []
        try:
            return search_call_snapshots(
                [record for record in list(getter() or []) if isinstance(record, dict)]
            )
        except Exception:
            return []

    def _has_remaining_tool_budget(self) -> bool:
        """Return whether at least one enabled tool can still be attempted."""
        for tool in self.tools:
            status = getattr(tool, "get_budget_status", None)
            if not callable(status):
                return True
            try:
                budget = status() or {}
                if int(budget.get("used") or 0) < int(budget.get("limit") or 0):
                    return True
            except (TypeError, ValueError):
                return True
        return False

    @staticmethod
    def _is_textual_tool_error(content: str) -> bool:
        text = (content or "").lstrip()
        if text.casefold().startswith(_TEXTUAL_TOOL_ERRORS):
            return True
        if text.startswith("{"):
            try:
                payload = json.loads(text)
            except (TypeError, ValueError):
                return False
            return isinstance(payload, dict) and payload.get("status") in {
                "rejected",
                "no_data",
                "error",
                "budget_exhausted",
            }
        return False

    def _trace_invalid_tool_request(self, iteration: int, reason: str) -> None:
        step_id = f"react_invalid_tool_{iteration}"
        self.tracer.begin(step_id, "工具调用格式", detail="检测到未识别的工具调用")
        self.tracer.end(
            step_id,
            detail="工具未执行",
            items=[
                {"label": "结果", "value": "格式无效"},
                {"label": "原因", "value": self._safe_trace_text(reason, limit=120)},
            ],
            status="error",
        )

    def _process_narration_reason(self, response: Any) -> Optional[str]:
        """Identify prose that promises a search instead of answering or calling a tool."""
        text = " ".join(self._message_text(response).split()).casefold()
        if not text:
            return None
        narration_markers = sum(marker in text for marker in _PROCESS_NARRATION_MARKERS)
        action_markers = sum(marker in text for marker in _PROCESS_NARRATION_ACTION_MARKERS)
        if narration_markers >= 2 and action_markers:
            return "search_plan_text"
        if narration_markers and action_markers and any(
            marker in text
            for marker in ("我需要", "让我", "我会", "我将", "接下来", "i need to", "let me ", "i will ")
        ):
            return "search_plan_text"
        return None

    def _trace_invalid_final_response(self, iteration: int, reason: str) -> None:
        step_id = f"react_invalid_final_{iteration}"
        self.tracer.begin(step_id, "回答格式", detail="检测到过程性文本")
        self.tracer.end(
            step_id,
            detail="未作为最终答案",
            items=[
                {"label": "结果", "value": "需要直接回答或调用工具"},
                {"label": "原因", "value": self._safe_trace_text(reason, limit=120)},
            ],
            status="error",
        )

    def _normalize_function_markup(self, response: Any) -> Tuple[Any, Optional[str]]:
        """Convert the supported XML-style function form into a tool call.

        Some providers emit a simple XML-like function format even after tool
        binding. It is not safe to treat that as an answer: only an enabled
        tool with a non-empty query is normalized, everything else is reported
        as an invalid request and replaced with an empty draft.
        """
        text = self._message_text(response).strip()
        if "<function" not in text.casefold():
            return response, None

        function_match = _FUNCTION_TAG.search(text)
        if function_match is None:
            return AIMessage(content=""), "unrecognized_function_markup"

        name = function_match.group("name").strip()
        if name not in self.tools_by_name:
            return AIMessage(content=""), f"unsupported_tool: {name or 'unknown'}"

        query_match = _QUERY_TAG.search(text)
        query = query_match.group("query").strip() if query_match else ""
        if not query:
            return AIMessage(content=""), "missing_tool_query"

        call = {
            "name": name,
            "args": {"query": query},
            "id": f"call_{uuid4().hex[:12]}",
            "type": "tool_call",
        }
        return AIMessage(content="", tool_calls=[call]), None

    def _trace_verdict(self, iteration: int, verdict: LoopVerdict) -> None:
        reason = _VERDICT_REASON_LABELS.get(verdict.reason, verdict.reason)
        items = [
            {"label": "判定", "value": reason},
            {"label": "动作", "value": verdict.action},
            {
                "label": "确定性 critic",
                "value": "通过" if verdict.deterministic_pass else "未通过",
            },
            {"label": "新证据", "value": "是" if verdict.new_evidence else "否"},
        ]
        if verdict.constraints_met:
            items.append(
                {
                    "label": "已满足",
                    "value": self._safe_trace_text("、".join(verdict.constraints_met), limit=160),
                }
            )
        if verdict.constraints_missing:
            items.append(
                {
                    "label": "缺少",
                    "value": self._safe_trace_text("、".join(verdict.constraints_missing), limit=160),
                }
            )
        if verdict.judge_used:
            items.append({"label": "评审", "value": "已执行"})
        elif verdict.judge_error:
            items.append({"label": "评审", "value": "失败，已使用规则"})
        else:
            items.append({"label": "评审", "value": "规则评估"})
        if verdict.rule_hits:
            items.append(
                {
                    "label": "规则命中",
                    "value": self._safe_trace_text(
                        "、".join(
                            str(hit.get("rule") or "")
                            for hit in verdict.rule_hits[:6]
                            if isinstance(hit, dict)
                        ),
                        limit=180,
                    ),
                }
            )

        detail = reason
        if verdict.constraints_missing:
            detail += "（缺：" + "、".join(verdict.constraints_missing) + "）"
        self.tracer.end(
            self._evaluation_step_id(iteration),
            detail=self._safe_trace_text(detail, limit=220),
            items=items,
            status="done",
        )
        self.tracer.end(
            self._iteration_step_id(iteration),
            detail="本轮完成",
            status="error" if verdict.reason == "unrecoverable" else "done",
        )

    def _trace_events(self) -> Tuple[List[Dict[str, Any]], bool]:
        events = list(getattr(self.tracer, "events", ()) or [])
        emitted = events[self._trace_start_index :]
        react_events = [
            dict(event)
            for event in emitted
            if str(event.get("id") or "").startswith("react_")
        ]
        if len(react_events) <= _TRACE_EVENT_LIMIT:
            return react_events, False
        head_count = _TRACE_EVENT_LIMIT // 2
        return (
            react_events[:head_count] + react_events[-(_TRACE_EVENT_LIMIT - head_count) :],
            True,
        )

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------
    def build_graph(self, checkpointer: Optional[Any] = None) -> Any:
        from langgraph.graph import END, START, StateGraph
        from langgraph.graph.message import add_messages
        from typing_extensions import TypedDict

        ReactLoopState = TypedDict(  # noqa: UP013 - functional syntax avoids lazy-import eval issues
            "ReactLoopState",
            {
                "messages": Annotated[list, add_messages],
                "evidence_pool": List[str],
                "evidence_records": List[Dict[str, Any]],
                "iteration": int,
                "verdicts": List[Dict[str, Any]],
                "constraints_met": List[str],
                "constraints_missing": List[str],
                "last_fingerprint": Optional[str],
                "fingerprint_streak": int,
                "no_progress_streak": int,
                "tool_error_streak": int,
                "had_successful_observation": bool,
                "last_round_new_evidence": bool,
                "last_round_observations": List[str],
                "final_proposed": bool,
                "invalid_tool_request": Optional[str],
                "invalid_final_response": Optional[str],
                "termination_reason": Optional[str],
                "final_answer": Optional[str],
                "judge_error": Optional[str],
            },
        )

        builder = StateGraph(ReactLoopState)
        builder.add_node("act", self._act)
        builder.add_node("observe", self._observe)
        builder.add_node("evaluate", self._evaluate)
        builder.add_edge(START, "act")
        builder.add_conditional_edges(
            "act",
            self._route_after_act,
            {"observe": "observe", "evaluate": "evaluate"},
        )
        builder.add_edge("observe", "evaluate")
        builder.add_conditional_edges(
            "evaluate",
            self._route_after_evaluate,
            {"act": "act", "end": END},
        )
        compile_kwargs: Dict[str, Any] = {}
        if checkpointer is not None:
            compile_kwargs["checkpointer"] = checkpointer
        return builder.compile(**compile_kwargs)

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------
    def _act(self, state: Dict[str, Any]) -> Dict[str, Any]:
        iteration = int(state["iteration"]) + 1
        iteration_step_id = self._iteration_step_id(iteration)
        self.tracer.begin(iteration_step_id, f"第 {iteration} 轮", detail="模型正在决定下一步")
        started = time.perf_counter()
        response: Any = None
        try:
            if self._use_native_tools:
                messages = [SystemMessage(content=self.system_prompt)] + list(state["messages"])
                response = self._llm_with_tools.invoke(messages)
            else:
                response = self._act_shim(list(state["messages"]))
        except Exception as exc:  # noqa: BLE001 - surfaced as a safe workflow failure
            self.tracer.error(
                iteration_step_id,
                detail="模型调用失败：" + self._safe_trace_text(type(exc).__name__, limit=80),
            )
            raise
        finally:
            if self.timing_recorder is not None:
                self.timing_recorder.record_llm_call(
                    label="loop_act",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    provider=getattr(self.llm, "provider", None),
                    model=getattr(self.llm, "model_name", None),
                    extra=extract_token_usage(response),
                )

        tool_calls = getattr(response, "tool_calls", None) or []
        invalid_tool_request: Optional[str] = None
        invalid_final_response: Optional[str] = None
        if not tool_calls:
            response, invalid_tool_request = self._normalize_function_markup(response)
            tool_calls = getattr(response, "tool_calls", None) or []
            if invalid_tool_request:
                self._trace_invalid_tool_request(iteration, invalid_tool_request)
            elif not tool_calls:
                invalid_final_response = self._process_narration_reason(response)
                if invalid_final_response:
                    self._trace_invalid_final_response(iteration, invalid_final_response)
                    response = AIMessage(content="")

        return {
            "messages": [response],
            "iteration": iteration,
            "last_round_new_evidence": False,
            "last_round_observations": [],
            "final_proposed": not tool_calls,
            "invalid_tool_request": invalid_tool_request,
            "invalid_final_response": invalid_final_response,
        }

    def _act_shim(self, history: List[Any]) -> AIMessage:
        """Tool-calling via JSON prompt for chat models without bind_tools."""
        messages = [SystemMessage(content=self._shim_system_prompt())] + history
        response = self.llm.invoke(messages)
        text = response.content if hasattr(response, "content") else str(response)
        if not isinstance(text, str):
            text = str(text)

        payload = extract_json_object(text)
        if payload and payload.get("action") == "tool":
            name = str(payload.get("tool") or "")
            if name in self.tools_by_name:
                args = payload.get("args") or {}
                if not isinstance(args, dict):
                    args = {"query": str(args)}
                call = {
                    "name": name,
                    "args": args,
                    "id": f"call_{uuid4().hex[:12]}",
                    "type": "tool_call",
                }
                return AIMessage(content="", tool_calls=[call])
        if payload and payload.get("action") == "final" and payload.get("answer"):
            return AIMessage(content=str(payload["answer"]))
        return AIMessage(content=text)

    def _observe(self, state: Dict[str, Any]) -> Dict[str, Any]:
        ai_message = state["messages"][-1]
        tool_calls = getattr(ai_message, "tool_calls", None) or []
        iteration = int(state["iteration"])

        tool_messages: List[ToolMessage] = []
        new_observations: List[str] = []
        new_records: List[Dict[str, Any]] = []
        error_streak = state["tool_error_streak"]
        had_success = state["had_successful_observation"]
        fingerprints: List[str] = []

        for position, call in enumerate(tool_calls, start=1):
            record_start = len(new_records)
            fingerprints.append(self._fingerprint(call))
            tool_name = str(call.get("name") or "")
            tool_step_id = self._tool_step_id(iteration, position)
            query = self._safe_tool_query(call)
            self.tracer.begin(
                tool_step_id,
                f"工具调用：{tool_name or 'unknown'}",
                detail=f"查询：{query}" if query else "正在调用工具",
            )
            tool = self.tools_by_name.get(tool_name)
            failed = False
            tool_started = time.perf_counter()
            if tool is None:
                content = f"Error: unknown tool '{tool_name}'"
                error_streak += 1
                failed = True
            else:
                try:
                    result = tool.invoke(call.get("args") or {})
                    content = result if isinstance(result, str) else str(result)
                    failed = self._is_textual_tool_error(content)
                    if failed:
                        error_streak += 1
                    else:
                        error_streak = 0
                        had_success = True
                        new_observations.append(content)
                        structured_records = self._tool_evidence_records(tool)
                        if structured_records:
                            for raw_record in structured_records:
                                record = dict(raw_record)
                                record.update(
                                    {
                                        "tool_name": tool_name,
                                        "query": query or "",
                                        "iteration": iteration,
                                        "position": position,
                                        "status": "done",
                                    }
                                )
                                record.setdefault("content", content)
                                record.setdefault("item_count", 1)
                                new_records.append(record)
                        else:
                            new_records.append(
                                self._evidence_record(
                                    tool_name=tool_name,
                                    query=query,
                                    iteration=iteration,
                                    position=position,
                                    content=content,
                                )
                            )
                except Exception as exc:  # noqa: BLE001 - tool errors are loop data
                    content = f"Error: tool '{tool_name}' failed: {exc}"
                    error_streak += 1
                    failed = True

            if (
                self.timing_recorder is not None
                and tool_name in {"web_search", "search_recovery"}
            ):
                self.timing_recorder.record_tool_call(
                    tool=tool_name,
                    duration_ms=(time.perf_counter() - tool_started) * 1000,
                    success=not failed,
                    extra={"kind": "loop_search_tool"},
                )

            if tool is not None:
                for api_position, snapshot in enumerate(
                    self._tool_search_api_calls(tool),
                    start=1,
                ):
                    emit_search_call_step(
                        self.tracer,
                        snapshot,
                        step_id=f"react_search_api_{iteration}_{position}_{api_position}",
                    )

            items = self._tool_trace_items(call, result_value=content, failed=failed)
            count = self._tool_result_count(call, content)
            if self.execution_trace is not None:
                observed_records = new_records[record_start:]
                source_types = [
                    str(record.get("source_type") or "")
                    for record in observed_records
                    if isinstance(record, dict)
                ]
                source_tiers = [
                    str(record.get("source_tier") or "")
                    for record in observed_records
                    if isinstance(record, dict)
                ]
                preferred_tier = next(
                    (
                        tier
                        for tier in ("official", "first_party", "authoritative", "local")
                        if tier in source_tiers
                    ),
                    next((tier for tier in source_tiers if tier), None),
                )
                self.execution_trace.record_tool_call(
                    tool=tool_name or "unknown",
                    status="error" if failed else "done",
                    iteration=iteration,
                    position=position,
                    query=query,
                    source_type=next(
                        (source_type for source_type in source_types if source_type),
                        None,
                    ),
                    source_tier=preferred_tier,
                    item_count=(
                        0
                        if failed
                        else len(observed_records)
                        if observed_records
                        else int(count or 0)
                    ),
                    reason=(
                        self._safe_trace_text(content, limit=160)
                        if failed
                        else None
                    ),
                )
            if failed:
                detail = "调用失败"
            elif count is not None:
                detail = f"完成 · 返回 {count} 条结果"
            else:
                detail = "调用完成"
            self.tracer.end(
                tool_step_id,
                detail=detail,
                items=items,
                status="error" if failed else "done",
            )
            tool_messages.append(self._tool_result_message(call, content))

        combined_fingerprint = " | ".join(sorted(fingerprints))
        if combined_fingerprint and combined_fingerprint == state["last_fingerprint"]:
            fingerprint_streak = state["fingerprint_streak"] + 1
        else:
            fingerprint_streak = 1

        pool_text = "\n".join(state["evidence_pool"])
        ratios = [
            evidence_increment_ratio(pool_text, observation) for observation in new_observations
        ]
        min_ratio = self.eval_cfg["new_evidence_min_ratio"]
        new_evidence = bool(ratios) and any(ratio >= min_ratio for ratio in ratios)

        return {
            "messages": tool_messages,
            "evidence_pool": list(state["evidence_pool"]) + new_observations,
            "evidence_records": list(state.get("evidence_records") or []) + new_records,
            "tool_error_streak": error_streak,
            "had_successful_observation": had_success,
            "last_fingerprint": combined_fingerprint or state["last_fingerprint"],
            "fingerprint_streak": fingerprint_streak,
            "last_round_new_evidence": new_evidence,
            "last_round_observations": new_observations,
        }

    def _tool_result_message(self, call: Dict[str, Any], content: str) -> Any:
        """Wrap a tool result for the conversation; HumanMessage in shim mode."""
        if self._use_native_tools:
            return ToolMessage(content=content, tool_call_id=call.get("id", ""))
        return HumanMessage(content=f"[工具 {call.get('name', '')} 返回]\n{content}")

    @staticmethod
    def _evidence_record(
        *,
        tool_name: str,
        query: Optional[str],
        iteration: int,
        position: int,
        content: str,
    ) -> Dict[str, Any]:
        """Build a provenance record for one successful tool observation.

        Maps tool names onto the source vocabulary understood by the ledger.
        """
        source_type_map = {
            "web_search": "web",
            "search_recovery": "web",
            "finance_market_data": "domain",
            "weather_conditions": "domain",
            "nearby_places": "domain",
            "route_directions": "domain",
            "sports_schedule": "domain",
            "local_docs": "local",
        }
        return {
            "tool_name": tool_name,
            "source_type": source_type_map.get(tool_name, "web"),
            "source_tier": (
                "authoritative"
                if source_type_map.get(tool_name) == "domain"
                else "local" if source_type_map.get(tool_name) == "local" else "unknown"
            ),
            "query": query or "",
            "iteration": int(iteration),
            "position": int(position),
            "content": content,
        }

    @staticmethod
    def _tool_evidence_records(tool: Any) -> List[Dict[str, Any]]:
        getter = getattr(tool, "get_last_evidence_records", None)
        if not callable(getter):
            return []
        try:
            return [
                dict(record)
                for record in list(getter() or [])
                if isinstance(record, dict)
            ]
        except Exception:  # noqa: BLE001 - provenance cannot fail the tool call
            return []

    def _evaluate(self, state: Dict[str, Any]) -> Dict[str, Any]:
        iteration = int(state["iteration"])
        self.tracer.begin(
            self._evaluation_step_id(iteration),
            f"第 {iteration} 轮评估",
            detail="统一 critic 正在检查证据、约束与预算",
        )
        final_proposed = bool(state["final_proposed"])
        invalid_tool_request = bool(state.get("invalid_tool_request"))
        invalid_final_response = bool(state.get("invalid_final_response"))
        draft = self._last_ai_text(state["messages"])
        pool_text = "\n".join(state["evidence_pool"])

        met, missing = check_constraint_coverage(
            self.query,
            pool_text,
            draft,
            self.time_constraint,
        )
        met = [value for value in met if value in self.initial_checklist]
        missing = [value for value in missing if value in self.initial_checklist]

        no_progress_streak = int(state["no_progress_streak"])
        if not final_proposed:
            no_progress_streak = (
                0
                if state["last_round_new_evidence"]
                else no_progress_streak + 1
            )

        unsupported: List[str] = []
        if final_proposed and draft:
            numbers = [
                token
                for token in extract_numbers(draft)
                if token not in {"1", "2", "3", "4", "5"}
            ]
            unsupported = [
                token for token in numbers if token.casefold() not in pool_text.casefold()
            ]

        context = self._termination_context(
            state,
            draft=draft,
            final_proposed=final_proposed,
            constraints_met=met,
            constraints_missing=missing,
            unsupported=unsupported,
            no_progress_streak=no_progress_streak,
        )
        preliminary = evaluate_termination(context)

        judge_payload: Optional[Dict[str, Any]] = None
        judge_error = state.get("judge_error")
        should_judge = (
            self.judge_llm is not None
            and not invalid_tool_request
            and not invalid_final_response
            and preliminary.action != TerminationAction.CLARIFY
            and (
                final_proposed
                or preliminary.hard_stop
                or (iteration > 0 and iteration % self.eval_cfg["judge_interval"] == 0)
            )
        )
        if should_judge:
            judge_payload, judge_error = self._run_judge(
                draft,
                preliminary.constraints_met,
                preliminary.missing_constraints,
                state,
            )
        context.judge_payload = judge_payload
        context.judge_error = judge_error
        decision = evaluate_termination(context)

        reason = decision.reason
        if invalid_tool_request:
            reason = "invalid_tool_request"
        elif invalid_final_response:
            reason = "process_narration"
        elif decision.action == TerminationAction.RETURN:
            reason = "constraints_satisfied"
        elif decision.action == TerminationAction.CONTINUE and final_proposed:
            reason = "final_answer_rejected"
        elif decision.action == TerminationAction.RETURN_INSUFFICIENT:
            reason = "evidence_insufficient"

        verdict = LoopVerdict(
            iteration=iteration,
            new_evidence=bool(state["last_round_new_evidence"]),
            constraints_met=list(decision.constraints_met),
            constraints_missing=list(decision.missing_constraints),
            should_continue=decision.should_continue,
            reason=reason,
            judge_used=decision.judge_used,
            judge_error=decision.judge_error,
            action=decision.action.value,
            deterministic_pass=decision.deterministic_pass,
            hard_stop=decision.hard_stop,
            failure_types=list(decision.failure_types),
            rule_hits=list(decision.rule_hits),
            evidence_sufficiency=decision.evidence_sufficiency,
        )

        update: Dict[str, Any] = {
            "constraints_met": list(decision.constraints_met),
            "constraints_missing": list(decision.missing_constraints),
            "no_progress_streak": no_progress_streak,
            "judge_error": judge_error,
        }

        termination_reason: Optional[str] = None
        final_answer: Optional[str] = None
        if decision.action == TerminationAction.RETURN:
            termination_reason = "succeeded"
            final_answer = draft
        elif decision.action in {
            TerminationAction.EXHAUSTED,
            TerminationAction.STAGNATED,
            TerminationAction.UNRECOVERABLE,
            TerminationAction.RETURN_INSUFFICIENT,
            TerminationAction.CLARIFY,
        }:
            termination_reason = {
                TerminationAction.RETURN_INSUFFICIENT: "evidence_insufficient",
                TerminationAction.CLARIFY: "clarification_required",
            }.get(decision.action, decision.action.value)
            final_answer = self._best_effort_answer(draft, termination_reason)
        elif invalid_tool_request:
            update["messages"] = [
                HumanMessage(
                    content=(
                        "上一轮工具调用格式无效，工具未执行。请使用可用工具的结构化调用"
                        "或直接给出最终答案。"
                    )
                )
            ]
        elif invalid_final_response:
            update["messages"] = [
                HumanMessage(
                    content=(
                        "上一轮只描述了检索计划，并非可展示的最终答案。请使用可用工具的"
                        "结构化调用，或直接给出面向用户的最终答案；不要说明你准备做什么。"
                    )
                )
            ]
        elif final_proposed and decision.should_continue:
            rejected = list(decision.missing_constraints) or ["semantic_sufficiency"]
            update["messages"] = [
                HumanMessage(
                    content=(
                        "你的回答尚未满足以下约束："
                        + "、".join(rejected)
                        + "。请继续使用工具收集信息，然后重新作答。"
                    )
                )
            ]

        update["termination_reason"] = termination_reason
        update["final_answer"] = final_answer
        update["verdicts"] = list(state["verdicts"]) + [verdict.to_dict()]
        self._trace_verdict(iteration, verdict)
        return update

    def _termination_context(
        self,
        state: Dict[str, Any],
        *,
        draft: str,
        final_proposed: bool,
        constraints_met: List[str],
        constraints_missing: List[str],
        unsupported: List[str],
        no_progress_streak: int,
    ) -> TerminationContext:
        records = [
            record
            for record in list(state.get("evidence_records") or [])
            if isinstance(record, dict)
        ]
        evidence_text = " ".join(
            str(record.get("content") or "") for record in records
        )
        analysis_constraints = getattr(self.analysis, "constraints", None)
        analysis_constraints = (
            analysis_constraints if isinstance(analysis_constraints, dict) else {}
        )
        policies: List[str] = []
        if analysis_constraints.get("authority_required"):
            policies.append("authority")
        if analysis_constraints.get("comparison_required"):
            policies.append("comparison_coverage")
        if analysis_constraints.get("historical_coverage_required"):
            policies.append("temporal_coverage")

        comparison_members = list(
            getattr(self.analysis, "comparison_members", None) or []
        )
        covered_entities = [
            member
            for member in comparison_members
            if member.casefold() in evidence_text.casefold()
        ]
        authoritative_tiers = {"official", "first_party", "authoritative"}
        authoritative_count = sum(
            1
            for record in records
            if str(record.get("source_tier") or "").casefold() in authoritative_tiers
        )
        covered_constraints = (
            ("temporal_coverage",)
            if re.search(r"(?<!\d)20\d{2}(?!\d)", evidence_text)
            else ()
        )
        requires_evidence = bool(
            getattr(self.analysis, "requires_evidence", False)
        )
        return TerminationContext(
            phase="loop",
            requires_evidence=requires_evidence,
            final_proposed=final_proposed,
            answer=draft,
            critical_ambiguities=(
                list(getattr(self.analysis, "ambiguities", None) or [])
                if bool(getattr(self.analysis, "critical_ambiguity", False))
                else []
            ),
            policies=policies,
            comparison_members=comparison_members,
            evidence=CriticEvidenceState(
                retained_count=len(records),
                available_count=len(records),
                authoritative_count=authoritative_count,
                covered_entities=tuple(covered_entities),
                covered_constraints=covered_constraints,
            ),
            constraints_met=constraints_met,
            constraints_missing=constraints_missing,
            unsupported_details=unsupported,
            empty_answer=final_proposed and not bool(draft.strip()),
            invalid_tool_request=bool(state.get("invalid_tool_request")),
            invalid_final_response=bool(state.get("invalid_final_response")),
            new_evidence=bool(state.get("last_round_new_evidence")),
            fingerprint_streak=int(state.get("fingerprint_streak") or 0),
            no_progress_streak=no_progress_streak,
            tool_error_streak=int(state.get("tool_error_streak") or 0),
            had_successful_observation=bool(
                state.get("had_successful_observation")
            ),
            repeat_threshold=self.eval_cfg["repeat_threshold"],
            no_progress_threshold=self.eval_cfg["no_progress_threshold"],
            tool_error_threshold=self.eval_cfg["tool_error_threshold"],
            can_continue=self._has_remaining_tool_budget(),
            budget=CriticBudgetState(
                iteration=int(state.get("iteration") or 0),
                max_iterations=self.max_iterations,
            ),
        )

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------
    @staticmethod
    def _route_after_act(state: Dict[str, Any]) -> str:
        last = state["messages"][-1]
        return "observe" if getattr(last, "tool_calls", None) else "evaluate"

    @staticmethod
    def _route_after_evaluate(state: Dict[str, Any]) -> str:
        return "end" if state.get("termination_reason") else "act"

    # ------------------------------------------------------------------
    # Evaluation helpers
    # ------------------------------------------------------------------
    def _run_judge(
        self,
        draft: str,
        met: List[str],
        missing: List[str],
        state: Dict[str, Any],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Run the loop judge LLM; degrade to rules-only on any failure."""
        observations_preview = "\n".join(state.get("last_round_observations") or [])[:1500]
        system_prompt = (
            "You are a strict judge for an iterative search agent loop. "
            "Return JSON only with keys: "
            "passes, missing_constraints, evidence_sufficiency, reason."
        )
        user_prompt = (
            f"Query:\n{self.query}\n\n"
            f"Current Draft Answer:\n{draft}\n\n"
            f"Constraints Met: {json.dumps(met, ensure_ascii=False)}\n"
            f"Constraints Missing: {json.dumps(missing, ensure_ascii=False)}\n\n"
            f"Latest Observations:\n{observations_preview}\n\n"
            "Judge whether the current evidence and draft already satisfy the user's request. "
            "Set passes=true only when the loop can stop with a satisfactory answer."
        )
        started = time.perf_counter()
        response: Any = None
        try:
            response = self.judge_llm.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
            )
            content = response.content if hasattr(response, "content") else str(response)
            payload = extract_json_object(content)
            if payload is None:
                return None, "judge_unparseable_response"
            return payload, None
        except Exception as exc:  # noqa: BLE001 - judge failure must not break the loop
            return None, str(exc)
        finally:
            if self.timing_recorder is not None:
                self.timing_recorder.record_llm_call(
                    label="termination_judge",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    provider=getattr(self.judge_llm, "provider", None),
                    model=getattr(self.judge_llm, "model_name", None),
                    extra=extract_token_usage(response),
                )

    @staticmethod
    def _fingerprint(tool_call: Dict[str, Any]) -> str:
        args = tool_call.get("args") or {}
        normalized = json.dumps(args, ensure_ascii=False, sort_keys=True)
        normalized = " ".join(normalized.lower().split())
        return f"{tool_call.get('name', '')}({normalized})"

    @staticmethod
    def _last_ai_text(messages: List[Any]) -> str:
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                content = message.content
                if isinstance(content, str) and content.strip():
                    return content
                if isinstance(content, list):
                    text_parts = [
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict) and part.get("type") == "text"
                    ]
                    joined = "".join(text_parts).strip()
                    if joined:
                        return joined
        return ""

    @staticmethod
    def _best_effort_answer(draft: str, reason: str) -> str:
        if draft:
            is_chinese = any("\u4e00" <= char <= "\u9fff" for char in draft)
            qualification = (
                "现有证据或执行预算不足，以下仅为当前候选信息，可能不完整或不准确：\n"
                if is_chinese
                else "Available evidence or execution budget was insufficient; the following is only a provisional result and may be incomplete or inaccurate:\n"
            )
            return qualification + draft
        reasons = {
            "exhausted": "迭代次数用尽，未能获得完整答案。",
            "stagnated": "检索陷入停滞，未能获得新的有效信息。",
            "unrecoverable": "工具连续失败，无法完成检索。",
            "evidence_insufficient": "现有证据不足，无法可靠完成回答。",
            "clarification_required": "需要补充关键实体或约束后才能继续。",
        }
        return reasons.get(reason, "未能获得完整答案。")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def _build_initial_state(self, user_input: str) -> Dict[str, Any]:
        """Construct the full state for a brand-new ReAct run."""
        return {
            "messages": [HumanMessage(content=user_input)],
            "evidence_pool": [],
            "evidence_records": [],
            "iteration": 0,
            "verdicts": [],
            "constraints_met": [],
            "constraints_missing": list(self.initial_checklist),
            "last_fingerprint": None,
            "fingerprint_streak": 0,
            "no_progress_streak": 0,
            "tool_error_streak": 0,
            "had_successful_observation": False,
            "last_round_new_evidence": False,
            "last_round_observations": [],
            "final_proposed": False,
            "invalid_tool_request": None,
            "invalid_final_response": None,
            "termination_reason": None,
            "final_answer": None,
            "judge_error": None,
        }

    def _build_followup_state_input(
        self,
        graph: Any,
        config: Dict[str, Any],
        user_input: str,
    ) -> Dict[str, Any]:
        """Construct a partial input that resumes from a checkpointed thread.

        ``evidence_pool`` and ``verdicts`` are intentionally omitted so the
        checkpointed values are retained; per-loop control fields are reset for
        the new turn. Stale tool/observation messages beyond the history window
        are removed via ``RemoveMessage`` to bound token cost.
        """
        removals = self._compute_message_removals(graph, config)
        return {
            "messages": removals + [HumanMessage(content=user_input)],
            "iteration": 0,
            "constraints_met": [],
            "constraints_missing": list(self.initial_checklist),
            "last_fingerprint": None,
            "fingerprint_streak": 0,
            "no_progress_streak": 0,
            "tool_error_streak": 0,
            "had_successful_observation": False,
            "last_round_new_evidence": False,
            "last_round_observations": [],
            "final_proposed": False,
            "invalid_tool_request": None,
            "invalid_final_response": None,
            "termination_reason": None,
            "final_answer": None,
            "judge_error": None,
        }

    def _compute_message_removals(self, graph: Any, config: Dict[str, Any]) -> List[Any]:
        """Return ``RemoveMessage`` ops for trimmable messages beyond the window.

        Only tool results (``ToolMessage``) and shim-mode observation wrappers
        (``HumanMessage`` starting with ``[工具``) are eligible for removal;
        user messages and final answers are always preserved.
        """
        try:
            snapshot = graph.get_state(config)
        except Exception:  # noqa: BLE001 - trimming is best effort
            return []
        values = getattr(snapshot, "values", None) or {}
        messages = list(values.get("messages") or [])

        trimmable = [
            m
            for m in messages
            if isinstance(m, ToolMessage)
            or (isinstance(m, HumanMessage) and str(getattr(m, "content", "")).lstrip().startswith("[工具"))
        ]
        # Keep a bounded number of recent trimmable entries (heuristic budget).
        keep_count = self.history_window * 2
        if len(trimmable) <= keep_count:
            return []
        to_remove = trimmable[: len(trimmable) - keep_count]
        return [RemoveMessage(id=m.id) for m in to_remove if getattr(m, "id", None)]

    @staticmethod
    def _checkpointed_verdict_count(graph: Any, config: Dict[str, Any]) -> int:
        """Return the number of verdicts already stored before a resumed turn."""
        try:
            snapshot = graph.get_state(config)
        except Exception:  # noqa: BLE001 - absent state means no prior verdicts
            return 0
        values = getattr(snapshot, "values", None) or {}
        verdicts = values.get("verdicts") or []
        return len(verdicts) if isinstance(verdicts, list) else 0

    def run(
        self,
        user_input: str,
        *,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute the loop and return a structured result.

        When ``conversation_id`` is supplied and a checkpoint exists for that
        thread, the run resumes from the checkpointed state (retaining the
        evidence pool and verdict history). Otherwise a fresh run is started;
        if a checkpointer is available the fresh run is still recorded under the
        thread so subsequent turns can resume.
        """
        self._trace_start_index = len(list(getattr(self.tracer, "events", ()) or []))
        mgr = None
        checkpointer = None
        if conversation_id:
            from orchestrators.conversation_store import get_conversation_manager

            mgr = get_conversation_manager()
            if mgr.enabled and mgr.saver:
                checkpointer = mgr.saver
        graph = self.build_graph(checkpointer=checkpointer)

        config: Dict[str, Any] = {"recursion_limit": self.max_iterations * 4 + 10}
        resume = False
        if conversation_id and checkpointer and mgr is not None:
            config["configurable"] = {"thread_id": str(conversation_id)}
            resume = mgr.has_checkpoint(conversation_id) and not mgr.last_turn_is_topic_reset(
                str(conversation_id)
            )

        prior_verdict_count = self._checkpointed_verdict_count(graph, config) if resume else 0
        if resume:
            state_input = self._build_followup_state_input(graph, config, user_input)
        else:
            state_input = self._build_initial_state(user_input)

        final_state = graph.invoke(state_input, config=config)

        termination = final_state.get("termination_reason") or "exhausted"
        answer = final_state.get("final_answer") or self._best_effort_answer("", termination)
        all_verdicts = list(final_state.get("verdicts") or [])
        current_verdicts = all_verdicts[prior_verdict_count:] if resume else all_verdicts
        result = {
            "answer": answer,
            "loop_status": termination,
            "termination_reason": termination,
            "iterations": final_state.get("iteration", 0),
            "verdicts": current_verdicts,
            "constraints_met": list(final_state.get("constraints_met") or []),
            "constraints_missing": list(final_state.get("constraints_missing") or []),
            "judge_error": final_state.get("judge_error"),
            "search_hits": [],
            "evidence_records": list(final_state.get("evidence_records") or []),
            "conversation_resumed": resume,
        }
        trace_events, trace_truncated = self._trace_events()
        result["trace_events"] = trace_events
        result["trace_truncated"] = trace_truncated
        if resume:
            result["evidence_pool_size"] = len(final_state.get("evidence_pool") or [])
        return result
