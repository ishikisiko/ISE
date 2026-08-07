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
    canonical_reference,
    check_constraint_coverage,
    evidence_increment_ratio,
    evaluate_termination,
    normalize_termination_config,
)
from utils.retrieval_trace import emit_search_call_step, search_call_snapshots
from utils.search_routing import extract_json_object
from utils.timing_utils import extract_token_usage
from utils.time_parser import TimeConstraint
from utils.workflow_trace import WorkflowTracer, ensure_tracer
from utils.config_validation import validate_context_compaction_config
from evidence.source_verdict import is_authoritative_tier
from evidence.pricing_claims import (
    collect_complete_pricing_facts,
    extract_pricing_facts,
    pricing_answer_failures,
    render_pricing_answer,
)
from orchestrators.context_compaction import (
    TokenBudget,
    assert_tool_call_pairing,
    deterministic_summary,
    fold_evidence_messages,
    partition,
    render_decision_trace,
    resolve_context_window,
    safe_cut_index,
    summarize,
    truncate_tail,
)

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

# DeepSeek models emit tool calls as proprietary ``<｜｜DSML｜｜...>`` text
# markup (U+FF5C fullwidth pipes) instead of native function-call objects when
# a provider does not translate them. The ReAct loop normalizes that markup
# back into structured tool calls so execution is not silently skipped.
_DSML_PIPE = "\uff5c"
_DSML_HEAD = "<" + _DSML_PIPE * 2 + "DSML" + _DSML_PIPE * 2
_DSML_TAIL = "</" + _DSML_PIPE * 2 + "DSML" + _DSML_PIPE * 2
_DSML_INVOKE = re.compile(
    re.escape(_DSML_HEAD)
    + r'invoke\s+name="([^"]+)"\s*>(?P<body>.*?)'
    + re.escape(_DSML_TAIL)
    + r"invoke\s*>",
    re.DOTALL,
)
_DSML_PARAMETER = re.compile(
    re.escape(_DSML_HEAD)
    + r'parameter\s+name="([^"]+)"[^>]*>(?P<value>.*?)'
    + re.escape(_DSML_TAIL)
    + r"parameter\s*>",
    re.DOTALL,
)


def _extract_dsml_tool_calls(text: str) -> List[Tuple[str, Dict[str, str]]]:
    """Parse DeepSeek DSML tool-call markup into ``(name, args)`` pairs.

    DSML looks like::

        <｜｜DSML｜｜tool_calls>
          <｜｜DSML｜｜invoke name="web_search">
            <｜｜DSML｜｜parameter name="query" string="true">q</｜｜DSML｜｜parameter>
          </｜｜DSML｜｜invoke>
        </｜｜DSML｜｜tool_calls>

    Returns every parsed invoke (empty list when none are present). Parameter
    values are kept as strings; schema coercion happens at validation time.
    """
    calls: List[Tuple[str, Dict[str, str]]] = []
    for invoke in _DSML_INVOKE.finditer(text):
        name = invoke.group(1).strip()
        args: Dict[str, str] = {}
        for param in _DSML_PARAMETER.finditer(invoke.group("body")):
            args[param.group(1).strip()] = param.group("value").strip()
        calls.append((name, args))
    return calls


_NUMBERED_RESULT = re.compile(r"(?m)^\s*\d+\.\s+")
_TRACE_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|authorization|cookie|token|secret|password)\s*([:=])\s*[^\s,;]+"
)
_TRACE_URL_QUERY = re.compile(r"https?://[^\s?#]+(?:\?[^\s#]*)?(?:#[^\s]*)?")
_TEXTUAL_TOOL_ERRORS = (
    "error:",
    "search failed:",
    "fetch failed:",
    "tool error:",
    "tool failed:",
)

# Failures that only more evidence can close. Everything else the critic can
# emit (citation markers, judge-detected mismatches between a figure and the
# source it cites, an omitted comparison member) is fixed by editing the draft,
# so the rejection message must not send the model back to the tools for them.
_RETRIEVAL_FIXABLE_FAILURES = frozenset(
    {
        "acknowledged_insufficient_information",
        "authority_policy_not_met",
        "citation_needs_official_source",
        "comparison_coverage_missing",
        "missing_comparison_coverage",
        "missing_time_constraint",
        "needs_multi_hop_reasoning",
        "no_evidence",
        "search_unavailable",
        "target_official_coverage_missing",
        "target_official_pricing_coverage_missing",
        "temporal_coverage_missing",
    }
)
_TRACE_EVENT_LIMIT = 40
_FINAL_ANSWER_PREFIX_RE = re.compile(
    r'^\s*\{\s*"action"\s*:\s*"final"\s*,\s*"answer"\s*:\s*"',
    re.IGNORECASE,
)


def _unwrap_final_answer(text: Any) -> Optional[str]:
    """Recover the plain answer from a stray ``{"action":"final","answer":...}`` wrapper.

    The shim action protocol wraps final answers in JSON. Strict parsing fails
    when the answer itself contains unescaped quotes or literal newlines, which
    would otherwise leak the wrapper into the user-facing answer. Try strict JSON
    first, then fall back to a tolerant prefix/suffix strip.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    payload = extract_json_object(raw)
    if isinstance(payload, dict) and payload.get("action") == "final":
        answer = payload.get("answer")
        if isinstance(answer, str) and answer.strip():
            return answer.strip()
    match = _FINAL_ANSWER_PREFIX_RE.match(raw)
    if match and raw.rstrip().endswith("}"):
        inner = raw[match.end():].rstrip()
        if inner.endswith('"}'):
            inner = inner[:-2]
        elif inner.endswith('"'):
            inner = inner[:-1]
        inner = inner.strip()
        if inner:
            return (
                inner.replace("\\n", "\n")
                .replace("\\\"", '"')
                .replace("\\\\", "\\")
            )
    return None


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
    "ready_to_synthesize": "证据齐备，转入综合",
    "forced_synthesis": "检索结束，生成可交付结论",
    "context_compaction": "上下文超预算，先压缩历史",
    "pricing_source_recovery": "切换到已配置的官方价目页",
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
    coverage_gaps: List[str] = field(default_factory=list)

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
            "coverage_gaps": list(self.coverage_gaps),
        }


TOOL_CALLING_SYSTEM_PROMPT = """你是一个智能搜索助手。你可以使用工具来收集信息回答用户问题。

规则：
- 需要更多信息时，调用合适的工具。
- 当你已经收集到足够信息时，直接给出完整的最终答案（不要再调用任何工具）。
- 最终答案应当具体、完整，覆盖问题中的所有要求。
- 不要输出检索计划、下一步说明或自我对话；非工具调用文本必须是面向用户的最终答案。
- 工具返回的每条证据都带有编号 [En]、来源层级（official / first_party / unknown 等）和状态（仅摘要 / 已抓全文）。回答中每一个具体数值或事实，都必须在其后标注来源编号，例如 "输入价 $1.90 [E2]"。优先采用 official 且"已抓全文"的来源；仅摘要或 unknown 来源的数值未经核实，需明确标注其不确定性。
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
        context_compaction_config: Optional[Dict[str, Any]] = None,
        ledger: Optional[Any] = None,
    ) -> None:
        self.llm = llm
        self.tools = list(tools or [])
        self.tools_by_name = {getattr(t, "name", ""): t for t in self.tools}
        self.max_iterations = max(1, int(max_iterations or 5))
        self.eval_cfg = normalize_termination_config(termination_config)
        self.max_synthesis_attempts = self.eval_cfg["max_synthesis_attempts"]
        # Per-scenario reasoning levels (DeepSeek v4 thinks at "high" by
        # default; easy queries run faster at "low"/"disabled", hard queries
        # keep "high" for answer quality). Resolved per-query from the analysis
        # and applied as a per-call kwarg so shared model objects stay untouched.
        self._reasoning_policy = (termination_config or {}).get("reasoning_policy") or {}
        self._act_reasoning: Optional[str] = None
        self._judge_reasoning: Optional[str] = None
        self.judge_llm = judge_llm
        self.query = query
        self.time_constraint = time_constraint
        self.analysis = analysis
        self._resolve_reasoning_levels()
        self.timing_recorder = timing_recorder
        self.execution_trace = execution_trace
        # ConversationManager still owns history_window for transcript reads;
        # the loop must never use it to trim native tool-call messages.
        _ = history_window
        self.context_compaction_config = validate_context_compaction_config(
            context_compaction_config
        )
        self.ledger = ledger
        # Keep a private recorder for direct callers so the final response can
        # expose the same bounded facts even when no SSE listener is attached.
        self.tracer = ensure_tracer(tracer) if tracer is not None else WorkflowTracer()
        self._trace_start_index = 0
        self.initial_checklist = self._derive_checklist()
        self.system_prompt = TOOL_CALLING_SYSTEM_PROMPT.format(
            success_criteria=self._format_success_criteria()
        )
        if "recall_evidence" in self.tools_by_name:
            self.system_prompt += (
                "\n- 若历史工具结果只保留 [En] 指针，可调用 recall_evidence "
                "回灌已登记证据的完整正文；不得把它当作网络搜索工具。"
            )
        self._context_window = resolve_context_window(
            self.context_compaction_config,
            getattr(self.llm, "model_name", None) or getattr(self.llm, "model", None),
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
    def _resolve_reasoning_levels(self) -> None:
        """Pick per-scenario reasoning levels for the act and judge LLM calls.

        Easy scenarios (listing/existence, simple lookup, volatile-value) run
        at a low level for speed; hard scenarios (comparison, pricing/numeric,
        historical, compliance) keep a high level so the final answer keeps
        quality. Levels are passed per call, so a model shared across queries
        (the server caches it) is never mutated.
        """
        policy = self._reasoning_policy or {}
        if not policy:
            # No policy configured: leave calls at each model's own default.
            self._act_reasoning = None
            self._judge_reasoning = None
            return
        easy = str(policy.get("easy", "low")).lower()
        hard = str(policy.get("hard", "high")).lower()
        judge_easy = str(policy.get("judge_easy", "disabled")).lower()
        judge_hard = str(policy.get("judge_hard", "low")).lower()
        hard_classes = set(policy.get("hard_claim_classes") or ())
        claim = set(getattr(self.analysis, "claim_classes", None) or [])
        constraints = getattr(self.analysis, "constraints", None) or {}
        is_hard = bool(claim & hard_classes) or bool(
            constraints.get("comparison_required")
            or constraints.get("historical_coverage_required")
        )
        self._act_reasoning = hard if is_hard else easy
        self._judge_reasoning = judge_hard if is_hard else judge_easy

    def _derive_checklist(self) -> List[str]:
        """Derive the initial constraint checklist.

        Shared ``QueryAnalysis.constraints`` is authoritative. A direct caller
        without analysis gets a deterministic fallback checklist.
        """
        checklist: List[str] = []
        analysis_constraints = getattr(self.analysis, "constraints", None)
        existence_query = bool(getattr(self.analysis, "existence_query", False))
        if isinstance(analysis_constraints, dict) and analysis_constraints:
            if analysis_constraints.get("temporal_required") and not existence_query:
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
        analysis_constraints = getattr(self.analysis, "constraints", None)
        if (
            isinstance(analysis_constraints, dict)
            and analysis_constraints.get("authority_required")
        ):
            parts.append(
                "涉及价格、数字或当前事实时必须以权威来源为依据；若搜索命中官方页面"
                "但摘要没有所需数值，必须调用 fetch_url 阅读该页面后再回答"
            )
        pricing_requirements = self._pricing_requirements()
        if pricing_requirements:
            labels = {
                "input": "输入",
                "output": "输出",
                "cached_input": "缓存命中输入",
            }
            required = "、".join(
                labels.get(role, role)
                for role in pricing_requirements.get("required_rates") or []
            )
            parts.append(
                f"这是用量价格计算题；同一官方完整正文必须同时给出 {required} 的单价、"
                "币种和计费单位，缺一不可。信息齐备后不要继续检索"
            )
        if not parts:
            return ""
        return "\n成功标准：\n" + "\n".join(f"- {p}" for p in parts)

    # ------------------------------------------------------------------
    # Context budget and evidence-ledger helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_cut_index(messages: List[Any], desired: int) -> int:
        """Return a cut that never separates a native tool request from its result."""
        return safe_cut_index(messages, desired)

    def _new_token_budget(self) -> TokenBudget:
        return TokenBudget(
            system_prompt=self.system_prompt,
            max_tokens=int(getattr(self.llm, "max_tokens", 0) or 0),
            context_window=self._context_window,
        )

    def _token_budget(self, state: Optional[Dict[str, Any]] = None) -> TokenBudget:
        budget = self._new_token_budget()
        if isinstance(state, dict):
            budget.restore(state.get("token_budget_state"))
        return budget

    def _tool_budget_statuses(self) -> Dict[str, Dict[str, Any]]:
        statuses: Dict[str, Dict[str, Any]] = {}
        for tool in self.tools:
            getter = getattr(tool, "get_budget_status", None)
            if not callable(getter):
                continue
            try:
                status = getter() or {}
            except Exception:  # noqa: BLE001 - observability cannot stop the loop
                continue
            if isinstance(status, dict):
                statuses[str(getattr(tool, "name", "tool"))] = dict(status)
        return statuses

    def _context_metrics(self, state: Dict[str, Any]) -> Tuple[TokenBudget, int, float]:
        budget = self._token_budget(state)
        amount = budget.estimate(list(state.get("messages") or []))
        return budget, amount, amount / budget.context_window

    def _restore_ledger_records(self, records: List[Dict[str, Any]]) -> None:
        """Make checkpointed [En] records available to the recall tool again."""
        if self.ledger is None:
            return
        restore = getattr(self.ledger, "restore", None)
        if not callable(restore):
            return
        for record in records:
            if not isinstance(record, dict):
                continue
            metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
            try:
                eid = int(metadata.get("eid"))
            except (TypeError, ValueError):
                continue
            if eid > 0:
                restore(eid, record)

    def _can_compact(self, state: Dict[str, Any]) -> bool:
        if not self.context_compaction_config["enabled"]:
            return False
        if state.get("compaction_blocked"):
            return False
        _, _amount, ratio = self._context_metrics(state)
        return ratio >= float(self.context_compaction_config["threshold"])

    def _compaction_debounced(
        self,
        state: Dict[str, Any],
        *,
        current_budget: Optional[int] = None,
    ) -> bool:
        if int(state.get("compactions") or 0) >= int(
            self.context_compaction_config["max_compactions_per_run"]
        ):
            return True
        last_iteration = state.get("last_compaction_iteration")
        if last_iteration is not None and int(state.get("iteration") or 0) <= int(last_iteration):
            return True
        current = int(
            current_budget
            if current_budget is not None
            else self._context_metrics(state)[1]
        )
        prior = int(state.get("tokens_at_last_compaction") or 0)
        return bool(prior and current >= prior)

    def _trace_compaction(
        self,
        *,
        sequence: int,
        before_messages: int,
        after_messages: int,
        before_budget: int,
        after_budget: int,
        summary_source: str,
        blocked: bool = False,
    ) -> None:
        step_id = f"react_compact_{sequence}"
        self.tracer.begin(step_id, "上下文压缩", detail="正在压缩已消费的证据上下文")
        self.tracer.end(
            step_id,
            detail="压缩受阻" if blocked else "压缩完成",
            items=[
                {"label": "消息", "value": f"{before_messages} -> {after_messages}"},
                {
                    "label": "预算",
                    "value": f"{before_budget}/{self._context_window} -> {after_budget}/{self._context_window}",
                },
                {
                    "label": "summary_source",
                    "value": self._safe_trace_text(summary_source, limit=40),
                },
            ],
            status="error" if blocked else "done",
        )

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

    def _validated_tool_call(
        self,
        name: str,
        raw_args: Any,
    ) -> Tuple[Optional[dict], Optional[str]]:
        """Validate ``raw_args`` against the named tool's schema.

        Returns ``(call_dict, None)`` on success or ``(None, error_reason)``.
        Shared by the DSML and other text-markup normalizers.
        """
        if name not in self.tools_by_name:
            return None, f"unsupported_tool: {name}"
        tool = self.tools_by_name[name]
        args = raw_args
        if not isinstance(args, dict):
            field_names = list((getattr(tool, "args", None) or {}).keys())
            if len(field_names) != 1:
                return None, f"invalid_tool_arguments: {name}"
            args = {field_names[0]: args}
        try:
            schema = getattr(tool, "args_schema", None)
            if schema is not None and hasattr(schema, "model_validate"):
                args = schema.model_validate(args).model_dump()
            elif schema is not None and hasattr(schema, "parse_obj"):
                args = schema.parse_obj(args).dict()
        except Exception:  # noqa: BLE001 - invalid model markup is loop data
            return None, f"invalid_tool_arguments: {name}"
        return (
            {
                "name": name,
                "args": args,
                "id": f"call_{uuid4().hex[:12]}",
                "type": "tool_call",
            },
            None,
        )

    def _normalize_dsml_markup(self, text: str) -> Tuple[Any, Optional[str]]:
        """Convert DeepSeek ``<｜｜DSML｜｜>`` tool-call markup into tool calls.

        A single DSML block may carry several invokes; each one that validates
        against an enabled tool becomes a structured call. Unsupported or
        invalid invokes are dropped, and the first offending tool name is
        surfaced only when nothing usable remained.
        """
        parsed = _extract_dsml_tool_calls(text)
        if not parsed:
            return AIMessage(content=""), None
        tool_calls: List[dict] = []
        first_error: Optional[str] = None
        for name, raw_args in parsed:
            call, error = self._validated_tool_call(name, raw_args)
            if call is not None:
                tool_calls.append(call)
            elif first_error is None:
                first_error = error
        if tool_calls:
            return AIMessage(content="", tool_calls=tool_calls), None
        return AIMessage(content=""), first_error or f"invalid_tool_arguments: {parsed[0][0]}"

    def _normalize_function_markup(self, response: Any) -> Tuple[Any, Optional[str]]:
        """Convert supported XML or JSON fallback forms into a tool call.

        Some providers emit textual function markup even after native tool
        binding. It is not safe to treat that as an answer: only an enabled
        tool whose arguments validate against its schema is normalized.
        """
        text = self._message_text(response).strip()
        if _DSML_HEAD in text:
            return self._normalize_dsml_markup(text)
        if "<function" not in text.casefold():
            payload = extract_json_object(text)
            if not isinstance(payload, dict):
                return response, None

            action = str(payload.get("action") or "").strip()
            if action == "final" and payload.get("answer"):
                return AIMessage(content=str(payload["answer"])), None

            name = ""
            if action in self.tools_by_name:
                name = action
            elif action == "tool":
                name = str(payload.get("tool") or payload.get("name") or "").strip()
            elif payload.get("tool") or payload.get("name"):
                name = str(payload.get("tool") or payload.get("name") or "").strip()
            if not name:
                return response, None
            if name not in self.tools_by_name:
                return AIMessage(content=""), f"unsupported_tool: {name}"

            raw_args = payload.get("args", payload.get("arguments"))
            if raw_args is None:
                raw_args = {
                    key: value
                    for key, value in payload.items()
                    if key not in {"action", "tool", "name", "args", "arguments"}
                }
            tool = self.tools_by_name[name]
            if not isinstance(raw_args, dict):
                field_names = list((getattr(tool, "args", None) or {}).keys())
                if len(field_names) != 1:
                    return AIMessage(content=""), f"invalid_tool_arguments: {name}"
                raw_args = {field_names[0]: raw_args}
            try:
                schema = getattr(tool, "args_schema", None)
                if schema is not None and hasattr(schema, "model_validate"):
                    validated = schema.model_validate(raw_args)
                    raw_args = validated.model_dump()
                elif schema is not None and hasattr(schema, "parse_obj"):
                    validated = schema.parse_obj(raw_args)
                    raw_args = validated.dict()
            except Exception:  # noqa: BLE001 - invalid model markup is loop data
                return AIMessage(content=""), f"invalid_tool_arguments: {name}"

            call = {
                "name": name,
                "args": raw_args,
                "id": f"call_{uuid4().hex[:12]}",
                "type": "tool_call",
            }
            return AIMessage(content="", tool_calls=[call]), None

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
                "fetch_outcomes": List[Dict[str, Any]],
                "iteration": int,
                "verdicts": List[Dict[str, Any]],
                "constraints_met": List[str],
                "constraints_missing": List[str],
                "last_fingerprint": Optional[str],
                "seen_fingerprints": List[str],
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
                "next_action": str,
                "phase": str,
                "synthesis_attempts": int,
                "forced_synthesis": bool,
                "force_synthesis": bool,
                "degraded_synthesis": bool,
                "degraded_caveats": List[str],
                "compactions": int,
                "tokens_at_last_compaction": int,
                "compaction_blocked": bool,
                "last_compaction_iteration": Optional[int],
                "context_budget": int,
                "context_ratio": float,
                "peak_context_ratio": float,
                "summary_source": Optional[str],
                "token_budget_state": Dict[str, Any],
            },
        )

        builder = StateGraph(ReactLoopState)
        builder.add_node("act", self._act)
        builder.add_node("observe", self._observe)
        builder.add_node("evaluate", self._evaluate)
        builder.add_node("compact", self._compact)
        builder.add_node("synthesize", self._synthesize)
        builder.add_node("pricing_fetch", self._pricing_fetch)
        builder.add_edge(
            START,
            "pricing_fetch" if self._pricing_source_candidates() else "act",
        )
        builder.add_conditional_edges(
            "act",
            self._route_after_act,
            {
                "observe": "observe",
                "evaluate": "evaluate",
                "synthesize": "synthesize",
            },
        )
        builder.add_edge("observe", "evaluate")
        builder.add_edge("compact", "act")
        builder.add_edge("synthesize", "evaluate")
        builder.add_edge("pricing_fetch", "observe")
        builder.add_conditional_edges(
            "evaluate",
            self._route_after_evaluate,
            {
                "act": "act",
                "pricing_fetch": "pricing_fetch",
                "synthesize": "synthesize",
                "compact": "compact",
                "end": END,
            },
        )
        compile_kwargs: Dict[str, Any] = {}
        if checkpointer is not None:
            compile_kwargs["checkpointer"] = checkpointer
        return builder.compile(**compile_kwargs)

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------
    def _act(self, state: Dict[str, Any]) -> Dict[str, Any]:
        # A blocked compaction still returns through the graph's ``act`` edge,
        # but it must not make another provider call before forced synthesis.
        if state.get("force_synthesis"):
            return {"next_action": "synthesize"}
        iteration = int(state["iteration"]) + 1
        iteration_step_id = self._iteration_step_id(iteration)
        self.tracer.begin(iteration_step_id, f"第 {iteration} 轮", detail="模型正在决定下一步")
        started = time.perf_counter()
        response: Any = None
        try:
            if self._use_native_tools:
                messages = [SystemMessage(content=self.system_prompt)] + list(state["messages"])
                response = self._llm_with_tools.invoke(messages, reasoning=self._act_reasoning)
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

        token_budget = self._token_budget(state)
        token_budget.record_usage(
            list(state.get("messages") or []),
            getattr(response, "usage_metadata", None),
        )

        return {
            "messages": [response],
            "iteration": iteration,
            "phase": "loop",
            "next_action": "evaluate",
            "last_round_new_evidence": False,
            "last_round_observations": [],
            "final_proposed": not tool_calls,
            "invalid_tool_request": invalid_tool_request,
            "invalid_final_response": invalid_final_response,
            "token_budget_state": token_budget.to_state(),
        }

    def _act_shim(self, history: List[Any]) -> AIMessage:
        """Tool-calling via JSON prompt for chat models without bind_tools."""
        messages = [SystemMessage(content=self._shim_system_prompt())] + history
        response = self.llm.invoke(messages, reasoning=self._act_reasoning)
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
        new_fetch_outcomes: List[Dict[str, Any]] = []
        error_streak = state["tool_error_streak"]
        had_success = state["had_successful_observation"]
        fingerprints: List[str] = []
        seen_fingerprints = set(state.get("seen_fingerprints") or [])
        duplicate_fingerprint_count = int(state.get("fingerprint_streak") or 0)

        for position, call in enumerate(tool_calls, start=1):
            record_start = len(new_records)
            fingerprint = self._fingerprint(call)
            fingerprints.append(fingerprint)
            if fingerprint in seen_fingerprints:
                duplicate_fingerprint_count += 1
            elif fingerprint:
                seen_fingerprints.add(fingerprint)
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
                    new_fetch_outcomes.extend(self._tool_fetch_outcomes(tool))
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
                and tool_name in {"web_search", "search_recovery", "fetch_url"}
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
                        for tier in ("official", "first_party", "local", "aggregator")
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

        prior_pool = list(state.get("evidence_pool") or [])
        pool_text = "\n".join(prior_pool)
        ratios = [
            evidence_increment_ratio(pool_text, observation) for observation in new_observations
        ]
        min_ratio = self.eval_cfg["new_evidence_min_ratio"]
        new_evidence = bool(ratios) and any(ratio >= min_ratio for ratio in ratios)

        pool_limit = int(self.context_compaction_config["evidence_pool_max_entries"])
        bounded_pool = (prior_pool + new_observations)[-pool_limit:]

        return {
            "messages": tool_messages,
            "evidence_pool": bounded_pool,
            "evidence_records": list(state.get("evidence_records") or []) + new_records,
            "fetch_outcomes": list(state.get("fetch_outcomes") or []) + new_fetch_outcomes,
            "tool_error_streak": error_streak,
            "had_successful_observation": had_success,
            "last_fingerprint": combined_fingerprint or state["last_fingerprint"],
            "seen_fingerprints": sorted(seen_fingerprints),
            "fingerprint_streak": duplicate_fingerprint_count,
            "last_round_new_evidence": new_evidence,
            "last_round_observations": new_observations,
        }

    def _compact(self, state: Dict[str, Any], *, force: bool = False) -> Dict[str, Any]:
        """Apply deterministic pointerization, then summarize only when needed."""
        messages = list(state.get("messages") or [])
        budget, before_budget, before_ratio = self._context_metrics(state)
        before_messages = len(messages)
        sequence = int(state.get("compactions") or 0) + 1
        part = partition(
            messages,
            int(self.context_compaction_config["keep_recent_rounds"]),
        )
        if part.blocked or not part.compressible:
            self._trace_compaction(
                sequence=sequence,
                before_messages=before_messages,
                after_messages=before_messages,
                before_budget=before_budget,
                after_budget=before_budget,
                summary_source="blocked",
                blocked=True,
            )
            return {
                "compaction_blocked": True,
                "force_synthesis": True,
                "forced_synthesis": True,
                "next_action": "synthesize",
                "context_budget": before_budget,
                "context_ratio": before_ratio,
                "peak_context_ratio": max(
                    float(state.get("peak_context_ratio") or 0), before_ratio
                ),
                "summary_source": "blocked",
            }

        records = [
            record
            for record in list(state.get("evidence_records") or [])
            if isinstance(record, dict)
        ]
        folded = fold_evidence_messages(part.compressible, self.ledger, records)
        trace = render_decision_trace(
            state, tool_budgets=self._tool_budget_statuses()
        )
        tier1_messages = list(part.pinned) + folded + [
            HumanMessage(content="[上下文轨迹]\n" + trace)
        ] + list(part.recent)
        tier1_state = dict(state)
        tier1_state["messages"] = tier1_messages
        _tier1_budget, tier1_amount, tier1_ratio = self._context_metrics(tier1_state)
        final_messages = tier1_messages
        summary_source = "deterministic"
        protected = [
            message
            for message in part.compressible
            if (
                isinstance(message, HumanMessage)
                and not self._message_text(message).lstrip().startswith("[工具")
            )
            or (
                isinstance(message, AIMessage)
                and not getattr(message, "tool_calls", None)
                and bool(self._message_text(message).strip())
            )
        ]

        if tier1_ratio >= float(self.context_compaction_config["threshold"]):
            try:
                summary_text = summarize(
                    state,
                    part.compressible,
                    llm=self.llm,
                    judge_llm=self.judge_llm,
                    use_judge_llm=bool(
                        self.context_compaction_config["use_judge_llm"]
                    ),
                    summary_max_tokens=int(
                        self.context_compaction_config["summary_max_tokens"]
                    ),
                    ledger=self.ledger,
                    evidence_records=records,
                    tool_budgets=self._tool_budget_statuses(),
                )
                summary_source = "llm"
                summary_message = HumanMessage(
                    content="[上下文摘要]\n" + summary_text
                )
                final_messages = (
                    list(part.pinned)
                    + [summary_message]
                    + protected
                    + list(part.recent)
                )
            except Exception:  # noqa: BLE001 - compaction must never break the loop
                try:
                    summary_text = deterministic_summary(
                        state, tool_budgets=self._tool_budget_statuses()
                    )
                    summary_source = "deterministic"
                    final_messages = list(part.pinned) + [
                        HumanMessage(content="[上下文摘要]\n" + summary_text)
                    ] + protected + list(part.recent)
                except Exception:  # noqa: BLE001 - last resort is a safe tail trim
                    summary_source = "truncate"
                    final_messages = truncate_tail(
                        tier1_messages,
                        max(1, int(self._context_window * self.context_compaction_config["threshold"])),
                        preserve=list(part.pinned) + protected + list(part.recent),
                    )

        try:
            assert_tool_call_pairing(final_messages)
        except AssertionError:
            final_messages = tier1_messages
            summary_source = "deterministic"

        after_state = dict(state)
        after_state["messages"] = final_messages
        _after_budget, after_amount, after_ratio = self._context_metrics(after_state)
        self._trace_compaction(
            sequence=sequence,
            before_messages=before_messages,
            after_messages=len(final_messages),
            before_budget=before_budget,
            after_budget=after_amount,
            summary_source=summary_source,
        )
        from langgraph.graph.message import REMOVE_ALL_MESSAGES

        return {
            "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)] + final_messages,
            "compactions": sequence,
            "tokens_at_last_compaction": after_amount,
            "compaction_blocked": False,
            "last_compaction_iteration": int(state.get("iteration") or 0),
            "context_budget": after_amount,
            "context_ratio": after_ratio,
            "peak_context_ratio": max(
                float(state.get("peak_context_ratio") or 0), before_ratio, after_ratio
            ),
            "summary_source": summary_source,
            "force_synthesis": False,
            "forced_synthesis": bool(state.get("forced_synthesis")),
            "next_action": "act",
            "token_budget_state": budget.to_state(),
        }

    def _pricing_source_candidates(self) -> List[Dict[str, str]]:
        requirements = self._pricing_requirements()
        tool = self.tools_by_name.get("fetch_url")
        getter = getattr(tool, "get_pricing_source_candidates", None)
        if not requirements or not callable(getter):
            return []
        try:
            return [
                dict(candidate)
                for candidate in list(getter(requirements) or [])
                if isinstance(candidate, dict) and candidate.get("url")
            ]
        except Exception:  # noqa: BLE001 - configured recovery is best effort
            return []

    def _next_pricing_source(
        self, state: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, str]]:
        state = state or {}
        tool = self.tools_by_name.get("fetch_url")
        budget_getter = getattr(tool, "get_budget_status", None)
        if callable(budget_getter):
            try:
                budget = budget_getter() or {}
                if int(budget.get("used") or 0) >= int(budget.get("limit") or 0):
                    return None
            except (TypeError, ValueError):
                pass
        attempted = {
            canonical_reference(outcome.get("url"))
            for outcome in list(state.get("fetch_outcomes") or [])
            if isinstance(outcome, dict)
        }
        attempted.update(
            canonical_reference(record.get("reference"))
            for record in list(state.get("evidence_records") or [])
            if isinstance(record, dict)
            and str((record.get("metadata") or {}).get("retrieval_kind") or "")
            == "fetch_url"
        )
        seen_fingerprints = {
            str(value).casefold()
            for value in list(state.get("seen_fingerprints") or [])
        }
        return next(
            (
                candidate
                for candidate in self._pricing_source_candidates()
                if canonical_reference(candidate.get("url")) not in attempted
                and (
                    "fetch_url("
                    + canonical_reference(candidate.get("url")).casefold()
                    + ")"
                )
                not in seen_fingerprints
            ),
            None,
        )

    def _pricing_fetch(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Call a configured official price page without spending an LLM turn."""
        candidate = self._next_pricing_source(state)
        if candidate is None:
            raise RuntimeError("No unattempted configured pricing source remains.")
        iteration = int(state.get("iteration") or 0) + 1
        step_id = self._iteration_step_id(iteration)
        self.tracer.begin(
            step_id,
            f"第 {iteration} 轮",
            detail="正在读取已配置的官方价目页",
        )
        call = {
            "name": "fetch_url",
            "args": {
                "url": candidate["url"],
                "objective": self.query,
            },
            "id": f"call_{uuid4().hex[:12]}",
            "type": "tool_call",
        }
        return {
            "messages": [AIMessage(content="", tool_calls=[call])],
            "iteration": iteration,
            "phase": "loop",
            "next_action": "observe",
            "last_round_new_evidence": False,
            "last_round_observations": [],
            "final_proposed": False,
            "invalid_tool_request": None,
            "invalid_final_response": None,
        }

    def _pricing_requirements(self) -> Dict[str, Any]:
        requirements = getattr(self.analysis, "numeric_requirements", None)
        if not isinstance(requirements, dict):
            return {}
        if requirements.get("operation") != "pricing_total":
            return {}
        return requirements

    def _pricing_fact_sets(self, state: Dict[str, Any]) -> List[Dict[str, Any]]:
        requirements = self._pricing_requirements()
        if not requirements:
            return []
        records = [
            record
            for record in list(state.get("evidence_records") or [])
            if isinstance(record, dict)
        ]
        return collect_complete_pricing_facts(records, requirements)

    def _pricing_missing_constraints(self, state: Dict[str, Any]) -> List[str]:
        requirements = self._pricing_requirements()
        if not requirements or self._pricing_fact_sets(state):
            return []

        official_fetches = [
            record
            for record in list(state.get("evidence_records") or [])
            if isinstance(record, dict)
            and str(record.get("source_tier") or "").casefold() == "official"
            and str((record.get("metadata") or {}).get("retrieval_kind") or "")
            == "fetch_url"
        ]
        candidates = [
            extract_pricing_facts(record.get("content"), requirements)
            for record in official_fetches
        ]
        best = max(
            candidates,
            key=lambda item: len((item or {}).get("rates") or {}),
            default={},
        )
        gaps = [
            f"pricing_rate:{role}"
            for role in requirements.get("required_rates") or []
            if role not in (best.get("rates") or {})
        ]
        if not official_fetches:
            gaps.append("pricing_source:official_fetched_text")
        if not best.get("currency"):
            gaps.append("pricing_currency")
        if not best.get("per_tokens"):
            gaps.append("pricing_billing_unit")
        if best.get("currency_matches") is False:
            gaps.append("pricing_currency_mismatch")
        return list(dict.fromkeys(gaps))

    def _pricing_insufficient_answer(self, state: Dict[str, Any]) -> str:
        requirements = self._pricing_requirements()
        subject = str(requirements.get("subject") or "该模型")
        role_labels = {
            "input": "输入单价",
            "output": "输出单价",
            "cached_input": "缓存命中输入单价",
        }
        missing = [
            role_labels.get(gap.split(":", 1)[1], gap)
            for gap in self._pricing_missing_constraints(state)
            if gap.startswith("pricing_rate:")
        ]
        records = [
            record
            for record in list(state.get("evidence_records") or [])
            if isinstance(record, dict) and is_authoritative_tier(record.get("source_tier"))
        ]
        raw_eid = next(
            (
                (record.get("metadata") or {}).get("eid")
                for record in records
                if (record.get("metadata") or {}).get("eid")
            ),
            None,
        )
        if isinstance(raw_eid, int) and not isinstance(raw_eid, bool) and raw_eid > 0:
            eid = f"E{raw_eid}"
        else:
            eid = str(raw_eid or "").strip()
        citation = f" [{eid}]" if re.fullmatch(r"E\d+", eid) else ""
        if records:
            first = (
                f"已找到 {subject} 的权威相关页面，但抓取到的正文没有形成可核验的完整价目元组"
                f"{citation}。"
            )
        else:
            first = f"现有证据中没有 {subject} 的可核验官方完整价目元组。"
        detail = "、".join(missing) if missing else "币种或计费单位"
        return (
            first
            + f"当前仍缺少：{detail}。为避免把不同渠道或不同币种的费率混算，本次不输出猜测总价。"
        )

    def _synthesize(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Produce a tool-free deliverable after retrieval is ready or exhausted."""
        iteration = int(state.get("iteration") or 0) + 1
        attempt = int(state.get("synthesis_attempts") or 0) + 1
        step_id = self._iteration_step_id(iteration)
        self.tracer.begin(step_id, f"第 {iteration} 轮", detail="正在综合已核验证据")
        fact_sets = self._pricing_fact_sets(state)
        if fact_sets:
            answer = render_pricing_answer(self._pricing_requirements(), fact_sets)
            detail = "已完成确定性价格计算"
        elif self._pricing_requirements():
            answer = self._pricing_insufficient_answer(state)
            detail = "已生成证据不足说明"
        elif state.get("degraded_synthesis") and self._has_retained_evidence(state):
            # Degraded path: the loop exhausted with real evidence but no
            # usable draft. Generate a fresh cited answer from the retained
            # evidence pool instead of echoing an empty best-effort stub.
            answer = self._generate_grounded_synthesis(state)
            detail = "已基于已核证据生成降级综合答案"
        else:
            draft = self._last_ai_text(list(state.get("messages") or []))
            answer = self._best_effort_answer(draft, "exhausted")
            detail = "上下文预算触发收口"
        self.tracer.end(
            step_id,
            detail=detail,
            items=[{"label": "工具调用", "value": "无"}],
            status="done",
        )
        return {
            "messages": [AIMessage(content=answer)],
            "iteration": iteration,
            "phase": "synthesis",
            "synthesis_attempts": attempt,
            "forced_synthesis": True,
            "force_synthesis": False,
            "next_action": "evaluate",
            "final_proposed": True,
            "invalid_tool_request": None,
            "invalid_final_response": None,
            # Synthesis is a new candidate, not another failed retrieval round.
            "last_round_new_evidence": True,
            "last_round_observations": [],
            "fingerprint_streak": 0,
            "no_progress_streak": 0,
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
            "fetch_url": "web",
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

    @staticmethod
    def _tool_fetch_outcomes(tool: Any) -> List[Dict[str, Any]]:
        getter = getattr(tool, "get_last_fetch_outcomes", None)
        if not callable(getter):
            return []
        try:
            return [
                dict(outcome)
                for outcome in list(getter() or [])
                if isinstance(outcome, dict)
            ]
        except Exception:  # noqa: BLE001 - fetch accounting cannot fail the tool call
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

        no_progress_streak = (
            0
            if state["last_round_new_evidence"]
            else int(state["no_progress_streak"]) + 1
        )

        citation_failures: List[Dict[str, str]] = []
        if final_proposed and draft:
            citation_failures = self._check_draft_citations(state, draft)

        context = self._termination_context(
            state,
            draft=draft,
            final_proposed=final_proposed,
            constraints_met=met,
            constraints_missing=missing,
            citation_failures=citation_failures,
            no_progress_streak=no_progress_streak,
        )
        preliminary = evaluate_termination(context)

        judge_payload: Optional[Dict[str, Any]] = None
        judge_error = state.get("judge_error")
        should_judge = (
            self.judge_llm is not None
            and bool(draft.strip())
            and not bool(self._pricing_requirements())
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

        pricing_requirements = self._pricing_requirements()
        pricing_ready = bool(self._pricing_fact_sets(state))
        pricing_source = self._next_pricing_source(state)
        pricing_recovery = bool(
            pricing_requirements
            and not pricing_ready
            and pricing_source
            and str(state.get("phase") or "loop") == "loop"
            and decision.action != TerminationAction.CLARIFY
        )
        terminal_without_answer = decision.action in {
            TerminationAction.EXHAUSTED,
            TerminationAction.STAGNATED,
            TerminationAction.UNRECOVERABLE,
            TerminationAction.RETURN_INSUFFICIENT,
        }
        # When constraints are already satisfied and we have real evidence, but
        # the model keeps searching instead of answering, force synthesis near
        # the iteration cap rather than burning the remaining rounds to
        # exhaustion. This especially helps existence/inventory queries whose
        # checklist is empty and thus have no other deterministic stop signal.
        constraints_satisfied = (not missing) and bool(
            state.get("had_successful_observation")
        )
        late_loop_no_answer = bool(
            not pricing_recovery
            and not final_proposed
            and constraints_satisfied
            and str(state.get("phase") or "loop") == "loop"
            and int(state.get("iteration") or 0)
            >= max(3, self.max_iterations - 2)
            and int(state.get("synthesis_attempts") or 0)
            < self.max_synthesis_attempts
        )
        force_synthesis = bool(state.get("force_synthesis")) or late_loop_no_answer or bool(
            pricing_requirements
            and not pricing_recovery
            and str(state.get("phase") or "loop") == "loop"
            and int(state.get("synthesis_attempts") or 0)
            < self.max_synthesis_attempts
            and (pricing_ready or terminal_without_answer)
        )

        # Phase 1 architecture fix: when the critic reaches a terminal state
        # (exhausted / stagnated / insufficient) but we actually retained real
        # evidence, do not throw it away for an empty "exhausted" answer --
        # degrade to a single grounded synthesis pass that produces a cited
        # answer (with caveats for any advisory coverage gaps). Pricing keeps
        # its own deterministic synthesis; this path is for general queries.
        coverage_gaps = list(getattr(context, "coverage_gaps", None) or [])
        degraded_synthesis_force = bool(
            self.eval_cfg.get("degraded_synthesis", True)
            and not pricing_requirements
            and not pricing_recovery
            and not force_synthesis
            and str(state.get("phase") or "loop") == "loop"
            and decision.action in {
                TerminationAction.EXHAUSTED,
                TerminationAction.STAGNATED,
                TerminationAction.RETURN_INSUFFICIENT,
            }
            and decision.evidence_sufficiency in ("partial", "sufficient")
            and int(state.get("synthesis_attempts") or 0)
            < self.max_synthesis_attempts
        )
        if degraded_synthesis_force:
            force_synthesis = True

        _context_budget, context_amount, context_ratio = self._context_metrics(state)
        context_over_budget = self._can_compact(state)
        compact_next = bool(
            context_over_budget
            and not pricing_recovery
            and not force_synthesis
            and decision.action == TerminationAction.CONTINUE
        )
        if compact_next and self._compaction_debounced(
            state,
            current_budget=context_amount,
        ):
            compact_next = False
            force_synthesis = True

        reason = decision.reason
        if pricing_recovery:
            reason = "pricing_source_recovery"
        elif degraded_synthesis_force:
            reason = "degraded_synthesis"
        elif force_synthesis:
            reason = "ready_to_synthesize" if pricing_ready else "forced_synthesis"
        elif compact_next:
            reason = "context_compaction"
        elif invalid_tool_request:
            reason = "invalid_tool_request"
        elif invalid_final_response:
            reason = "process_narration"
        elif decision.action == TerminationAction.RETURN:
            reason = "constraints_satisfied"
        elif decision.action == TerminationAction.CONTINUE and final_proposed:
            reason = "final_answer_rejected"
        elif decision.action == TerminationAction.RETURN_INSUFFICIENT:
            reason = decision.reason or "evidence_insufficient"

        verdict = LoopVerdict(
            iteration=iteration,
            new_evidence=bool(state["last_round_new_evidence"]),
            constraints_met=list(decision.constraints_met),
            constraints_missing=list(decision.missing_constraints),
            should_continue=(
                True
                if pricing_recovery or force_synthesis
                else decision.should_continue
            ),
            reason=reason,
            judge_used=decision.judge_used,
            judge_error=decision.judge_error,
            action=(
                "pricing_fetch"
                if pricing_recovery
                else "synthesize"
                if force_synthesis
                else "compact"
                if compact_next
                else decision.action.value
            ),
            deterministic_pass=decision.deterministic_pass,
            hard_stop=(
                False
                if pricing_recovery or force_synthesis
                else decision.hard_stop
            ),
            failure_types=list(decision.failure_types),
            rule_hits=list(decision.rule_hits),
            evidence_sufficiency=decision.evidence_sufficiency,
            coverage_gaps=list(coverage_gaps),
        )

        update: Dict[str, Any] = {
            "constraints_met": list(decision.constraints_met),
            "constraints_missing": list(decision.missing_constraints),
            "no_progress_streak": no_progress_streak,
            "judge_error": judge_error,
            "context_budget": context_amount,
            "context_ratio": context_ratio,
            "degraded_synthesis": bool(degraded_synthesis_force),
            "degraded_caveats": list(coverage_gaps) if degraded_synthesis_force else [],
            "peak_context_ratio": max(
                float(state.get("peak_context_ratio") or 0), context_ratio
            ),
            "force_synthesis": force_synthesis,
        }

        termination_reason: Optional[str] = None
        final_answer: Optional[str] = None
        if pricing_recovery:
            update["next_action"] = "pricing_fetch"
        elif force_synthesis:
            update["next_action"] = "synthesize"
        elif compact_next:
            update["next_action"] = "compact"
        elif decision.action == TerminationAction.RETURN:
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
            answer_reason = (
                "authority_unverified"
                if decision.reason == "authority_unverified"
                else termination_reason
            )
            final_answer = self._best_effort_answer(draft, answer_reason)
        elif invalid_tool_request:
            detail = str(state.get("invalid_tool_request") or "").strip()
            tool_names = "、".join(sorted(self.tools_by_name))
            update["messages"] = [
                HumanMessage(
                    content=(
                        "上一轮的工具调用未被执行"
                        + (f"：{detail}" if detail else "")
                        + f"。可用工具只有：{tool_names}。请调用其中之一，"
                        "或直接给出面向用户的最终答案。"
                    )
                )
            ]
        elif invalid_final_response:
            update["messages"] = [
                HumanMessage(
                    content=(
                        "上一轮只陈述了接下来的计划，既没有调用工具，也没有给出答案。"
                        "请立即调用工具，或直接给出面向用户的最终答案；"
                        "不要描述你准备做什么。"
                    )
                )
            ]
        elif final_proposed and decision.should_continue:
            update["messages"] = [
                HumanMessage(content=self._rejection_message(state, decision))
            ]

        update["termination_reason"] = termination_reason
        update["final_answer"] = final_answer
        if not pricing_recovery and not force_synthesis and not compact_next:
            update["next_action"] = "end" if termination_reason else (
                "synthesize"
                if str(state.get("phase") or "loop") == "synthesis"
                else "act"
            )
        update["verdicts"] = list(state["verdicts"]) + [verdict.to_dict()]
        self._trace_verdict(iteration, verdict)
        return update

    def _rejection_message(self, state: Dict[str, Any], decision: Any) -> str:
        """Tell the model what to change, in terms it can act on.

        The critic already produces model-facing rule details ("这句话包含具体
        数值却没有标注来源编号 [En]"). Feeding back ``missing_constraints``
        instead echoed machine labels built from the model's own sentences
        (``citation_missing:根据 Kimi 官方定价页面...``), which said nothing
        actionable. Paired with an unconditional fetch nudge, the model kept
        re-running retrieval when the actual fix was to edit the answer.
        """
        details: List[str] = []
        for hit in list(getattr(decision, "rule_hits", None) or []):
            detail = str((hit or {}).get("detail") or "").strip()
            if detail and detail not in details:
                details.append(detail)
        if not details:
            details = [
                str(constraint)
                for constraint in list(decision.missing_constraints)[:6]
            ] or ["semantic_sufficiency"]

        failure_types = [str(value) for value in (decision.failure_types or [])]
        needs_retrieval = any(
            value in _RETRIEVAL_FIXABLE_FAILURES
            or value.startswith("constraint_missing:")
            for value in failure_types
        )
        closing = (
            self._official_fetch_instruction(state)
            if needs_retrieval
            else
            # Nothing here is fixed by more evidence: the draft must be edited
            # so each figure matches the source it cites. Sending the model
            # back to the tools burns budget and stagnates the loop instead of
            # returning the answer it already has.
            "请直接修改答案本身：让每个数值与其引用的证据严格对应，必要时补全或"
            "更换 [En] 标注；不要再调用检索工具。"
        )
        return "你的回答尚未通过校验：" + "；".join(details[:6]) + "。" + closing

    def _official_fetch_instruction(self, state: Dict[str, Any]) -> str:
        """Suggest fetching an unfetched authoritative hit, or steer away from
        URLs that already failed extraction.

        Previously this method pointed the model at the first authoritative URL
        it found in the evidence records, even when that exact URL had already
        been fetched and returned insufficient content. The model would then
        re-invoke ``fetch_url`` on the same doomed URL. We now consult
        ``state["fetch_outcomes"]`` (canonical URLs of every fetch attempt,
        success or failure) and skip candidates whose canonical form has been
        tried. When every authoritative candidate is exhausted we return a
        generic message that nudges the model toward a different source or
        acknowledging the gap, never toward the same URL again.
        """
        analysis_constraints = getattr(self.analysis, "constraints", None)
        if (
            "fetch_url" not in self.tools_by_name
            or not isinstance(analysis_constraints, dict)
            or not analysis_constraints.get("authority_required")
        ):
            return "请继续使用工具收集信息，然后重新作答。"

        records = [
            record
            for record in list(state.get("evidence_records") or [])
            if isinstance(record, dict)
        ]
        attempted = {
            canonical_reference(outcome.get("url"))
            for outcome in list(state.get("fetch_outcomes") or [])
            if isinstance(outcome, dict)
        }
        for record in records:
            reference = str(record.get("reference") or "").strip()
            if (
                reference.startswith(("http://", "https://"))
                and canonical_reference(reference) not in attempted
                and is_authoritative_tier(record.get("source_tier"))
            ):
                return (
                    "请现在直接调用 fetch_url 阅读已找到的权威页面 "
                    + reference
                    + "，不要描述检索计划；获取页面内容后再重新作答。"
                )
        return (
            "已有的权威来源均已尝试抓取但未能获得可用正文，或暂无未尝试的权威"
            "页面。请改用其他检索词寻找新的来源，或在证据确实不足时如实说明。"
            "不要对已失败的同一 URL 重复调用 fetch_url。"
        )

    def _check_draft_citations(
        self, state: Dict[str, Any], draft: str
    ) -> List[Dict[str, str]]:
        """Mechanically verify the draft's [En] citations against the ledger."""
        from evidence.citation_check import check_citations

        records = [
            record
            for record in list(state.get("evidence_records") or [])
            if isinstance(record, dict)
        ]
        claim_classes = set(getattr(self.analysis, "claim_classes", None) or [])
        analysis_constraints = getattr(self.analysis, "constraints", None)
        analysis_constraints = (
            analysis_constraints if isinstance(analysis_constraints, dict) else {}
        )
        requires_official_pricing = "pricing" in claim_classes
        temporal_required = bool(
            analysis_constraints.get("historical_coverage_required")
            or analysis_constraints.get("freshness_required")
            or "temporal" in claim_classes
            or "current" in claim_classes
        )
        # Existence/inventory questions ("现在有哪些插件") ask what exists, not
        # what changed: do not demand a dated authoritative source for them.
        if getattr(self.analysis, "existence_query", False):
            temporal_required = False
        try:
            failures = check_citations(
                draft,
                records,
                requires_official_pricing=requires_official_pricing,
                temporal_required=temporal_required,
            )
            requirements = self._pricing_requirements()
            fact_sets = self._pricing_fact_sets(state)
            if requirements and fact_sets:
                failures.extend(
                    pricing_answer_failures(draft, requirements, fact_sets)
                )
            return failures
        except Exception:  # noqa: BLE001 - citation check must not break the loop
            return []

    def _termination_context(
        self,
        state: Dict[str, Any],
        *,
        draft: str,
        final_proposed: bool,
        constraints_met: List[str],
        constraints_missing: List[str],
        citation_failures: List[Dict[str, str]],
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
        authoritative_count = sum(
            1
            for record in records
            if is_authoritative_tier(record.get("source_tier"))
        )
        provisional_authoritative_count = sum(
            1
            for record in records
            if bool((record.get("metadata") or {}).get("authority_provisional"))
        )
        covered_constraints = (
            ("temporal_coverage",)
            if re.search(r"(?<!\d)20\d{2}(?!\d)", evidence_text)
            else ()
        )
        requires_evidence = bool(
            getattr(self.analysis, "requires_evidence", False)
        )
        claim_classes = set(getattr(self.analysis, "claim_classes", None) or [])
        authority_required = bool(analysis_constraints.get("authority_required"))
        # Per-target official coverage only means something for an explicit
        # comparison: ``comparison_members`` is the curated list of things the
        # answer must cover one by one. ``analysis.entities`` is a token bag
        # ("Kimi K2.7 Code HighSpeed" -> K2.7 / Kimi / Code / HighSpeed) while
        # ``source_target`` can only ever name the single entity that owns the
        # domain, so requiring coverage per token is unsatisfiable and starves
        # the loop into stagnation. A single-entity query still gets authority
        # enforcement from the ``authority`` policy and, for pricing, from the
        # citation check's official + full-fetch requirement.
        official_targets: List[str] = []
        if authority_required:
            official_targets = list(dict.fromkeys(comparison_members))
        covered_official_entities = sorted(
            {
                str((record.get("metadata") or {}).get("source_target") or "").strip()
                for record in records
                if str(record.get("source_tier") or "").casefold() == "official"
                and str((record.get("metadata") or {}).get("source_target") or "").strip()
            }
        )
        pricing_gaps = self._pricing_missing_constraints(state)
        constraints_missing = list(
            dict.fromkeys(list(constraints_missing) + pricing_gaps)
        )
        phase = str(state.get("phase") or "loop")
        synthesis_attempts = int(state.get("synthesis_attempts") or 0)
        return TerminationContext(
            phase=phase,
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
            official_targets=official_targets,
            requires_official_pricing="pricing" in claim_classes,
            evidence=CriticEvidenceState(
                retained_count=len(records),
                available_count=len(records),
                authoritative_count=authoritative_count,
                provisional_authoritative_count=provisional_authoritative_count,
                covered_entities=tuple(covered_entities),
                covered_official_entities=tuple(covered_official_entities),
                covered_constraints=covered_constraints,
            ),
            constraints_met=constraints_met,
            constraints_missing=constraints_missing,
            citation_failures=citation_failures,
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
            coverage_mode=self.eval_cfg.get("coverage_mode", "blocking"),
            can_continue=(
                self._has_remaining_tool_budget()
                if phase == "loop"
                else synthesis_attempts < self.max_synthesis_attempts
                and not pricing_gaps
            ),
            budget=CriticBudgetState(
                iteration=int(state.get("iteration") or 0),
                max_iterations=(
                    self.max_iterations
                    if phase == "loop"
                    else self.max_iterations + self.max_synthesis_attempts + 1
                ),
            ),
        )

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------
    @staticmethod
    def _route_after_act(state: Dict[str, Any]) -> str:
        if state.get("force_synthesis"):
            return "synthesize"
        last = state["messages"][-1]
        return "observe" if getattr(last, "tool_calls", None) else "evaluate"

    @staticmethod
    def _route_after_evaluate(state: Dict[str, Any]) -> str:
        if state.get("termination_reason"):
            return "end"
        action = str(state.get("next_action") or "act")
        return (
            action
            if action in {"act", "pricing_fetch", "synthesize", "compact"}
            else "act"
        )

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
        evidence_pool = list(state.get("evidence_pool") or [])
        observations_preview = "\n".join(evidence_pool)[-3000:]
        system_prompt = (
            "You are a semantic-consistency judge for an iterative search agent loop. "
            "Citation formatting, source tiers, coverage checklists, and budgets are "
            "already enforced mechanically by a separate deterministic critic; do NOT "
            "re-check those. Your only job is to catch what mechanical checks cannot: "
            "a claim that cites a source but contradicts or misreads it, a number that "
            "does not match what its cited source states, or an answer that fails to "
            "address part of the user's request. "
            "Return JSON only with keys: "
            "passes, missing_constraints, evidence_sufficiency, reason."
        )
        user_prompt = (
            f"Query:\n{self.query}\n\n"
            f"Current Draft Answer:\n{draft}\n\n"
            f"Constraints Met: {json.dumps(met, ensure_ascii=False)}\n"
            f"Constraints Missing: {json.dumps(missing, ensure_ascii=False)}\n\n"
            f"Latest Observations (with [En] citation ids, source tiers, and fetch status):\n"
            f"{observations_preview}\n\n"
            "For each specific claim in the draft (numbers, prices, dates, attributions), "
            "verify it is consistent with the evidence it cites. Flag any claim that "
            "contradicts its cited source, states a figure its source does not support, or "
            "misattributes ownership. Also flag if the draft leaves part of the request "
            "unanswered. Set passes=true only when every specific claim is consistent with "
            "its cited evidence and the request is fully addressed."
        )
        started = time.perf_counter()
        response: Any = None
        try:
            response = self.judge_llm.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)],
                reasoning=self._judge_reasoning,
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
        tool_name = str(tool_call.get("name") or "")
        if tool_name == "fetch_url" and isinstance(args, dict):
            url = canonical_reference(args.get("url"))
            if url:
                return f"fetch_url({url.casefold()})"
        normalized = json.dumps(args, ensure_ascii=False, sort_keys=True)
        normalized = " ".join(normalized.lower().split())
        return f"{tool_name}({normalized})"

    @staticmethod
    def _last_ai_text(messages: List[Any]) -> str:
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                content = message.content
                if isinstance(content, str):
                    return content if content.strip() else ""
                if isinstance(content, list):
                    text_parts = [
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict) and part.get("type") == "text"
                    ]
                    joined = "".join(text_parts).strip()
                    return joined
                return ""
        return ""

    def _has_retained_evidence(self, state: Dict[str, Any]) -> bool:
        """True when the ledger still holds retained evidence records."""
        records = [
            record
            for record in list(state.get("evidence_records") or [])
            if isinstance(record, dict)
        ]
        return len(records) > 0

    def _generate_grounded_synthesis(self, state: Dict[str, Any]) -> str:
        """Generate a fresh, cited final answer from the retained evidence pool.

        Degraded-synthesis fallback for the Phase 1 architecture fix: when the
        loop reached a terminal state with real evidence but no usable draft,
        ask the model to answer directly from the verified evidence (no tools),
        surfacing any advisory coverage gaps as explicit caveats instead of
        fabricating them. Any failure degrades to the best-effort stub.
        """
        caveats = list(state.get("degraded_caveats") or [])
        caveat_line = ""
        if caveats:
            names = "、".join(
                str(gap).replace("comparison:", "").replace("answer_comparison:", "")
                for gap in caveats
            )[:240]
            caveat_line = (
                f"\n- 以下方面未检索到充分证据，必须在答案中如实说明“证据不足”，不得编造：{names}"
            )
        instruction = (
            "检索阶段已结束。请仅基于上方已核验的工具证据，直接给出面向用户的完整最终答案："
            "不要调用任何工具，也不要复述计划或过程。每个具体事实或数值后必须标注来源编号 [En]；"
            "对证据不足的方面如实说明。" + caveat_line
        )
        history = list(state.get("messages") or [])
        started = time.perf_counter()
        response: Any = None
        try:
            # Synthesis is a single tool-free answer: do NOT route the shim
            # provider through ``_act_shim`` (whose JSON action protocol would
            # wrap the answer and leak the wrapper when the answer's quotes /
            # newlines break strict JSON). Invoke the base model directly with
            # the same system prompt + history + plain-text instruction in both
            # modes -- no tool binding is needed for synthesis.
            messages = [
                SystemMessage(content=self.system_prompt),
            ] + history + [HumanMessage(content=instruction)]
            response = self.llm.invoke(messages, reasoning=self._act_reasoning)
            text = response.content if hasattr(response, "content") else str(response)
            if not isinstance(text, str):
                text = str(text)
            text = text.strip()
            # Defensive: strip any residual action-protocol wrapper the model
            # may still emit, even after the plain-text instruction.
            unwrapped = _unwrap_final_answer(text)
            if unwrapped:
                text = unwrapped
            if text:
                return text
        except Exception:  # noqa: BLE001 - degrade to best effort on any failure
            pass
        finally:
            if self.timing_recorder is not None:
                self.timing_recorder.record_llm_call(
                    label="degraded_synthesis",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    provider=getattr(self.llm, "provider", None),
                    model=getattr(self.llm, "model_name", None),
                    extra=extract_token_usage(response),
                )
        draft = self._last_ai_text(history)
        return self._best_effort_answer(draft, "exhausted")

    @staticmethod
    def _best_effort_answer(draft: str, reason: str) -> str:
        payload = extract_json_object(draft)
        if (
            isinstance(payload, dict)
            and any(key in payload for key in ("action", "tool", "name"))
        ) or "<function" in draft.casefold():
            draft = ""
        if draft:
            is_chinese = any("\u4e00" <= char <= "\u9fff" for char in draft)
            if reason == "authority_unverified":
                qualification = (
                    "已获取与目标实体相关的页面，但其官方归属未通过权威策略验证；以下内容仅供参考：\n"
                    if is_chinese
                    else (
                        "A page related to the target entity was retrieved, but its "
                        "official ownership could not be verified under the authority "
                        "policy; the following is provisional:\n"
                    )
                )
            else:
                qualification = (
                    "现有证据或执行预算不足，以下仅为当前候选信息，可能不完整或不准确：\n"
                    if is_chinese
                    else (
                        "Available evidence or execution budget was insufficient; the "
                        "following is only a provisional result and may be incomplete "
                        "or inaccurate:\n"
                    )
                )
            return qualification + draft
        reasons = {
            "exhausted": "迭代次数用尽，未能获得完整答案。",
            "stagnated": "检索陷入停滞，未能获得新的有效信息。",
            "unrecoverable": "工具连续失败，无法完成检索。",
            "evidence_insufficient": "现有证据不足，无法可靠完成回答。",
            "authority_unverified": "已获取相关页面，但无法验证其官方归属，不能可靠完成回答。",
            "clarification_required": "需要补充关键实体或约束后才能继续。",
        }
        return reasons.get(reason, "未能获得完整答案。")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def _build_initial_state(self, user_input: str) -> Dict[str, Any]:
        """Construct the full state for a brand-new ReAct run."""
        budget = self._new_token_budget()
        initial_messages = [HumanMessage(content=user_input)]
        return {
            "messages": initial_messages,
            "evidence_pool": [],
            "evidence_records": [],
            "fetch_outcomes": [],
            "iteration": 0,
            "verdicts": [],
            "constraints_met": [],
            "constraints_missing": list(self.initial_checklist),
            "last_fingerprint": None,
            "seen_fingerprints": [],
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
            "next_action": "act",
            "phase": "loop",
            "synthesis_attempts": 0,
            "forced_synthesis": False,
            "force_synthesis": False,
            "compactions": 0,
            "tokens_at_last_compaction": 0,
            "compaction_blocked": False,
            "last_compaction_iteration": None,
            "context_budget": budget.estimate(initial_messages),
            "context_ratio": budget.ratio(initial_messages),
            "peak_context_ratio": budget.ratio(initial_messages),
            "summary_source": None,
            "token_budget_state": budget.to_state(),
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
        the new turn. The checkpoint already contains any previous compaction.
        """
        try:
            snapshot = graph.get_state(config)
            values = getattr(snapshot, "values", None) or {}
            self._restore_ledger_records(
                list(values.get("evidence_records") or [])
            )
        except Exception:  # noqa: BLE001 - a missing checkpoint is harmless here
            pass
        return {
            "messages": [HumanMessage(content=user_input)],
            "iteration": 0,
            "fetch_outcomes": [],
            "constraints_met": [],
            "constraints_missing": list(self.initial_checklist),
            "last_fingerprint": None,
            "seen_fingerprints": [],
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
            "next_action": "act",
            "phase": "loop",
            "synthesis_attempts": 0,
            "forced_synthesis": False,
            "force_synthesis": False,
            "compactions": 0,
            "tokens_at_last_compaction": 0,
            "compaction_blocked": False,
            "last_compaction_iteration": None,
            "context_budget": 0,
            "context_ratio": 0.0,
            "peak_context_ratio": 0.0,
            "summary_source": None,
        }

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

    def _compact_checkpoint_if_needed(
        self,
        graph: Any,
        config: Dict[str, Any],
        *,
        force: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Compact a persisted thread before its next follow-up is appended."""
        try:
            snapshot = graph.get_state(config)
        except Exception:  # noqa: BLE001 - unavailable checkpoints remain normal runs
            return None
        values = dict(getattr(snapshot, "values", None) or {})
        if not values:
            return None
        self._restore_ledger_records(list(values.get("evidence_records") or []))
        if not force and not self._can_compact(values):
            return None
        update = self._compact(values, force=force)
        try:
            graph.update_state(config, update, as_node="compact")
        except Exception:  # noqa: BLE001 - do not lose a follow-up to maintenance
            return None
        return update

    def compact_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Manually compact an existing checkpoint without creating a new thread."""
        if not conversation_id:
            return None
        from orchestrators.conversation_store import get_conversation_manager

        manager = get_conversation_manager()
        if not manager.enabled or not manager.saver or not manager.has_checkpoint(conversation_id):
            return None
        graph = self.build_graph(checkpointer=manager.saver)
        config: Dict[str, Any] = {
            "configurable": {"thread_id": str(conversation_id)},
            "recursion_limit": (self.max_iterations + self.max_synthesis_attempts) * 5 + 10,
        }
        try:
            before = graph.get_state(config)
            before_values = getattr(before, "values", None) or {}
            before_count = len(list(before_values.get("messages") or []))
            update = self._compact_checkpoint_if_needed(graph, config, force=True)
            if update is None:
                return None
            after = graph.get_state(config)
            after_values = getattr(after, "values", None) or {}
            return {
                "before_messages": before_count,
                "after_messages": len(list(after_values.get("messages") or [])),
                "summary_source": update.get("summary_source"),
                "compactions": int(after_values.get("compactions") or 0),
            }
        except Exception:  # noqa: BLE001 - endpoint reports absence/failure safely
            return None

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

        config: Dict[str, Any] = {
            "recursion_limit": (
                self.max_iterations + self.max_synthesis_attempts
            ) * 5 + 10
        }
        resume = False
        if conversation_id and checkpointer and mgr is not None:
            config["configurable"] = {"thread_id": str(conversation_id)}
            resume = mgr.has_checkpoint(conversation_id) and not mgr.last_turn_is_topic_reset(
                str(conversation_id)
            )

        if resume:
            self._compact_checkpoint_if_needed(graph, config)
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
            "fetch_outcomes": list(final_state.get("fetch_outcomes") or []),
            "synthesis_attempts": int(final_state.get("synthesis_attempts") or 0),
            "forced_synthesis": bool(final_state.get("forced_synthesis")),
            "compactions": int(final_state.get("compactions") or 0),
            "peak_context_ratio": float(final_state.get("peak_context_ratio") or 0),
            "conversation_resumed": resume,
        }
        trace_events, trace_truncated = self._trace_events()
        result["trace_events"] = trace_events
        result["trace_truncated"] = trace_truncated
        if resume:
            result["evidence_pool_size"] = len(final_state.get("evidence_pool") or [])
        return result
