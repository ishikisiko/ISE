"""LangGraph-based ReAct loop with explicit per-iteration evaluation.

This module implements an explicit state machine (`act -> observe -> evaluate`)
as an alternative engine to the legacy LangChain AgentExecutor loop. Success
and failure of the loop are decided by the evaluate node: the model proposes,
the evaluator disposes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Dict, List, Optional, Tuple
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage

from langchain.postcheck import (
    _extract_numbers,
    check_constraint_coverage,
    evidence_increment_ratio,
)
from utils.retrieval_trace import emit_search_call_step, search_call_snapshots
from utils.search_routing import extract_json_object
from utils.time_parser import TimeConstraint
from utils.workflow_trace import WorkflowTracer, ensure_tracer

DEFAULT_EVALUATION_CONFIG: Dict[str, Any] = {
    "judge_interval": 2,
    "repeat_threshold": 2,
    "no_progress_threshold": 2,
    "tool_error_threshold": 2,
    "new_evidence_min_ratio": 0.1,
}

LOOP_STATUSES = ("succeeded", "exhausted", "stagnated", "unrecoverable")

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
    "invalid_tool_request": "工具调用格式无效",
    "process_narration": "过程性文本，继续补充",
}


def normalize_evaluation_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge user evaluation config over defaults with safe coercion."""
    merged = dict(DEFAULT_EVALUATION_CONFIG)
    if not config:
        return merged
    for key in merged:
        if key not in config:
            continue
        value = config[key]
        try:
            if key == "new_evidence_min_ratio":
                merged[key] = float(value)
            else:
                merged[key] = int(value)
        except (TypeError, ValueError):
            continue
    return merged


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
        evaluation_config: Optional[Dict[str, Any]] = None,
        judge_llm: Optional[BaseChatModel] = None,
        query: str = "",
        time_constraint: Optional[TimeConstraint] = None,
        fallback_context: Optional[Dict[str, Any]] = None,
        history_window: int = 5,
        tracer: Optional[Any] = None,
    ) -> None:
        self.llm = llm
        self.tools = list(tools or [])
        self.tools_by_name = {getattr(t, "name", ""): t for t in self.tools}
        self.max_iterations = max(1, int(max_iterations or 5))
        self.eval_cfg = normalize_evaluation_config(evaluation_config)
        self.judge_llm = judge_llm
        self.query = query
        self.time_constraint = time_constraint
        self.fallback_context = fallback_context or {}
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
        """Derive the initial constraint checklist from fallback context or query."""
        missing = [str(c) for c in (self.fallback_context.get("missing_constraints") or []) if c]
        failure_types = [str(f) for f in (self.fallback_context.get("failure_types") or []) if f]
        checklist: List[str] = []
        for item in missing + failure_types:
            if item and item not in checklist:
                checklist.append(item)
        if checklist:
            return checklist
        _, derived = check_constraint_coverage(self.query, "", "", self.time_constraint)
        return derived

    def _format_success_criteria(self) -> str:
        parts: List[str] = []
        recovery_goal = str(self.fallback_context.get("recovery_goal") or "").strip()
        if recovery_goal:
            parts.append(f"补救目标：{recovery_goal}")
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

    @staticmethod
    def _is_textual_tool_error(content: str) -> bool:
        return (content or "").lstrip().casefold().startswith(_TEXTUAL_TOOL_ERRORS)

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
        error_streak = state["tool_error_streak"]
        had_success = state["had_successful_observation"]
        fingerprints: List[str] = []

        for position, call in enumerate(tool_calls, start=1):
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
                except Exception as exc:  # noqa: BLE001 - tool errors are loop data
                    content = f"Error: tool '{tool_name}' failed: {exc}"
                    error_streak += 1
                    failed = True

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

    def _evaluate(self, state: Dict[str, Any]) -> Dict[str, Any]:
        iteration = state["iteration"]
        self.tracer.begin(
            self._evaluation_step_id(iteration),
            f"第 {iteration} 轮评估",
            detail="正在检查证据与约束",
        )
        final_proposed = state["final_proposed"]
        invalid_tool_request = state.get("invalid_tool_request")
        invalid_final_response = state.get("invalid_final_response")
        draft = self._last_ai_text(state["messages"])
        pool_text = "\n".join(state["evidence_pool"])

        met, missing = check_constraint_coverage(self.query, pool_text, draft, self.time_constraint)
        met = [c for c in met if c in self.initial_checklist]
        missing = [c for c in missing if c in self.initial_checklist]

        no_progress_streak = state["no_progress_streak"]
        if not final_proposed:
            no_progress_streak = 0 if state["last_round_new_evidence"] else no_progress_streak + 1

        unsupported: List[str] = []
        if final_proposed and draft:
            numbers = [
                token
                for token in _extract_numbers(draft)
                if token not in {"1", "2", "3", "4", "5"}
            ]
            unsupported = [token for token in numbers if token.lower() not in pool_text.lower()]

        forced = self._forced_termination(state, final_proposed, no_progress_streak, missing, unsupported)

        judge_used = False
        judge_error = state["judge_error"]
        judge_payload: Optional[Dict[str, Any]] = None
        if self.judge_llm is not None and not invalid_tool_request and not invalid_final_response:
            due_interval = iteration > 0 and iteration % self.eval_cfg["judge_interval"] == 0
            if due_interval or forced:
                judge_payload, judge_error = self._run_judge(draft, met, missing, state)
                judge_used = judge_payload is not None

        if judge_payload:
            passes = judge_payload.get("passes_postcheck")
            judge_missing = judge_payload.get("missing_constraints") or []
            if isinstance(judge_missing, str):
                judge_missing = [judge_missing]
            if passes is True:
                missing = []
            elif passes is False:
                merged = list(dict.fromkeys(missing + [str(m) for m in judge_missing if m]))
                missing = merged or missing

        verdict = LoopVerdict(
            iteration=iteration,
            new_evidence=bool(state["last_round_new_evidence"]),
            constraints_met=met,
            constraints_missing=missing,
            judge_used=judge_used,
            judge_error=judge_error if not judge_used and judge_error else None,
        )

        update: Dict[str, Any] = {
            "constraints_met": met,
            "constraints_missing": missing,
            "no_progress_streak": no_progress_streak,
            "judge_error": judge_error,
        }

        termination_reason: Optional[str] = None
        final_answer: Optional[str] = None

        if invalid_tool_request:
            if iteration >= self.max_iterations:
                termination_reason = "exhausted"
                final_answer = self._best_effort_answer("", "exhausted")
                verdict.should_continue = False
            else:
                update["messages"] = [
                    HumanMessage(
                        content=(
                            "上一轮工具调用格式无效，工具未执行。请使用可用工具的结构化调用"
                            "或直接给出最终答案。"
                        )
                    )
                ]
                verdict.should_continue = True
            verdict.reason = "invalid_tool_request"
        elif invalid_final_response:
            if iteration >= self.max_iterations:
                termination_reason = "exhausted"
                final_answer = self._best_effort_answer("", "exhausted")
                verdict.should_continue = False
            else:
                update["messages"] = [
                    HumanMessage(
                        content=(
                            "上一轮只描述了检索计划，并非可展示的最终答案。请使用可用工具的"
                            "结构化调用，或直接给出面向用户的最终答案；不要说明你准备做什么。"
                        )
                    )
                ]
                verdict.should_continue = True
            verdict.reason = "process_narration"
        elif forced:
            termination_reason = forced
            final_answer = self._best_effort_answer(draft, forced)
            verdict.should_continue = False
            verdict.reason = forced
        elif final_proposed:
            if not missing and not unsupported:
                termination_reason = "succeeded"
                final_answer = draft
                verdict.should_continue = False
                verdict.reason = "constraints_satisfied"
            elif iteration >= self.max_iterations:
                termination_reason = "exhausted"
                final_answer = self._best_effort_answer(draft, "exhausted")
                verdict.should_continue = False
                verdict.reason = "exhausted"
            else:
                rejected = list(missing)
                if unsupported:
                    rejected.append("unsupported_specific_detail")
                feedback = HumanMessage(
                    content=(
                        "你的回答尚未满足以下约束："
                        + "、".join(rejected)
                        + "。请继续使用工具收集信息，然后重新作答。"
                    )
                )
                update["messages"] = [feedback]
                verdict.should_continue = True
                verdict.reason = "final_answer_rejected"
        else:
            verdict.should_continue = True
            verdict.reason = "continue"

        update["termination_reason"] = termination_reason
        update["final_answer"] = final_answer
        update["verdicts"] = list(state["verdicts"]) + [verdict.to_dict()]
        self._trace_verdict(iteration, verdict)
        return update

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
    def _forced_termination(
        self,
        state: Dict[str, Any],
        final_proposed: bool,
        no_progress_streak: int,
        missing: List[str],
        unsupported: List[str],
    ) -> Optional[str]:
        if (
            state["tool_error_streak"] >= self.eval_cfg["tool_error_threshold"]
            and not state["had_successful_observation"]
        ):
            return "unrecoverable"
        if final_proposed:
            return None
        if state["fingerprint_streak"] >= self.eval_cfg["repeat_threshold"]:
            return "stagnated"
        if no_progress_streak >= self.eval_cfg["no_progress_threshold"]:
            return "stagnated"
        if state["iteration"] >= self.max_iterations:
            accepted = final_proposed and not missing and not unsupported
            if not accepted:
                return "exhausted"
        return None

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
            "passes_postcheck, missing_constraints, evidence_sufficiency, reason."
        )
        user_prompt = (
            f"Query:\n{self.query}\n\n"
            f"Current Draft Answer:\n{draft}\n\n"
            f"Constraints Met: {json.dumps(met, ensure_ascii=False)}\n"
            f"Constraints Missing: {json.dumps(missing, ensure_ascii=False)}\n\n"
            f"Latest Observations:\n{observations_preview}\n\n"
            "Judge whether the current evidence and draft already satisfy the user's request. "
            "Set passes_postcheck=true only when the loop can stop with a satisfactory answer."
        )
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
            return draft
        reasons = {
            "exhausted": "迭代次数用尽，未能获得完整答案。",
            "stagnated": "检索陷入停滞，未能获得新的有效信息。",
            "unrecoverable": "工具连续失败，无法完成检索。",
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
            "conversation_resumed": resume,
        }
        trace_events, trace_truncated = self._trace_events()
        result["trace_events"] = trace_events
        result["trace_truncated"] = trace_truncated
        if resume:
            result["evidence_pool_size"] = len(final_state.get("evidence_pool") or [])
        return result
