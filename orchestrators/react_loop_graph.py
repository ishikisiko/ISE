"""LangGraph-based ReAct loop with explicit per-iteration evaluation.

This module implements an explicit state machine (`act -> observe -> evaluate`)
as an alternative engine to the legacy LangChain AgentExecutor loop. Success
and failure of the loop are decided by the evaluate node: the model proposes,
the evaluator disposes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Annotated, Any, Dict, List, Optional, Tuple
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from langchain.postcheck import (
    _extract_numbers,
    check_constraint_coverage,
    evidence_increment_ratio,
)
from utils.search_routing import extract_json_object
from utils.time_parser import TimeConstraint

DEFAULT_EVALUATION_CONFIG: Dict[str, Any] = {
    "judge_interval": 2,
    "repeat_threshold": 2,
    "no_progress_threshold": 2,
    "tool_error_threshold": 2,
    "new_evidence_min_ratio": 0.1,
}

LOOP_STATUSES = ("succeeded", "exhausted", "stagnated", "unrecoverable")


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
    # Graph construction
    # ------------------------------------------------------------------
    def build_graph(self) -> Any:
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
        return builder.compile()

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------
    def _act(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if self._use_native_tools:
            messages = [SystemMessage(content=self.system_prompt)] + list(state["messages"])
            response = self._llm_with_tools.invoke(messages)
        else:
            response = self._act_shim(list(state["messages"]))
        tool_calls = getattr(response, "tool_calls", None) or []
        return {
            "messages": [response],
            "iteration": state["iteration"] + 1,
            "final_proposed": not tool_calls,
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

        tool_messages: List[ToolMessage] = []
        new_observations: List[str] = []
        error_streak = state["tool_error_streak"]
        had_success = state["had_successful_observation"]
        fingerprints: List[str] = []

        for call in tool_calls:
            fingerprints.append(self._fingerprint(call))
            tool = self.tools_by_name.get(call.get("name", ""))
            if tool is None:
                content = f"Error: unknown tool '{call.get('name')}'"
                error_streak += 1
            else:
                try:
                    result = tool.invoke(call.get("args") or {})
                    content = result if isinstance(result, str) else str(result)
                    error_streak = 0
                    had_success = True
                    new_observations.append(content)
                except Exception as exc:  # noqa: BLE001 - tool errors are loop data
                    content = f"Error: tool '{call.get('name')}' failed: {exc}"
                    error_streak += 1
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
        final_proposed = state["final_proposed"]
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
        if self.judge_llm is not None:
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

        if forced:
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
    def run(self, user_input: str) -> Dict[str, Any]:
        """Execute the loop and return a structured result."""
        graph = self.build_graph()
        initial_state = {
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
            "termination_reason": None,
            "final_answer": None,
            "judge_error": None,
        }
        recursion_limit = self.max_iterations * 4 + 10
        final_state = graph.invoke(initial_state, config={"recursion_limit": recursion_limit})

        termination = final_state.get("termination_reason") or "exhausted"
        answer = final_state.get("final_answer") or self._best_effort_answer("", termination)
        return {
            "answer": answer,
            "loop_status": termination,
            "termination_reason": termination,
            "iterations": final_state.get("iteration", 0),
            "verdicts": list(final_state.get("verdicts") or []),
            "constraints_met": list(final_state.get("constraints_met") or []),
            "constraints_missing": list(final_state.get("constraints_missing") or []),
            "judge_error": final_state.get("judge_error"),
            "search_hits": [],
        }
