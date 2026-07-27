"""Deterministic primitives for bounded ReAct conversation context.

The loop owns graph routing; this module deliberately contains only accounting,
message-shape, and summary helpers so they can be exercised without a graph or
network provider.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages


DEFAULT_CONTEXT_WINDOW = 128_000
DEFAULT_CALIBRATION = 2.0
DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "context_window": DEFAULT_CONTEXT_WINDOW,
    "per_model_window": {},
    "threshold": 0.75,
    "keep_recent_rounds": 2,
    "max_compactions_per_run": 2,
    "summary_max_tokens": 800,
    "use_judge_llm": True,
    "evidence_pool_max_entries": 32,
}

_EID_RE = re.compile(r"\[E(\d+)\]")
_TOOL_WRAPPER_PREFIX = "[工具"


def _positive_int(value: Any, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


def _fraction(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(0.98, max(0.05, parsed))


def normalize_context_compaction_config(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a validated, complete compaction configuration mapping."""
    source = raw if isinstance(raw, dict) else {}
    if isinstance(source.get("context_compaction"), dict):
        source = source["context_compaction"]
    per_model = source.get("per_model_window")
    if not isinstance(per_model, dict):
        per_model = {}
    normalized_windows: Dict[str, int] = {}
    for name, window in per_model.items():
        key = str(name or "").strip()
        if key:
            normalized_windows[key] = _positive_int(window, DEFAULT_CONTEXT_WINDOW)
    return {
        "enabled": bool(source.get("enabled", DEFAULT_CONFIG["enabled"])),
        "context_window": _positive_int(
            source.get("context_window"), DEFAULT_CONTEXT_WINDOW
        ),
        "per_model_window": normalized_windows,
        "threshold": _fraction(source.get("threshold"), DEFAULT_CONFIG["threshold"]),
        "keep_recent_rounds": _positive_int(
            source.get("keep_recent_rounds"), DEFAULT_CONFIG["keep_recent_rounds"]
        ),
        "max_compactions_per_run": _positive_int(
            source.get("max_compactions_per_run"),
            DEFAULT_CONFIG["max_compactions_per_run"],
        ),
        "summary_max_tokens": _positive_int(
            source.get("summary_max_tokens"), DEFAULT_CONFIG["summary_max_tokens"]
        ),
        "use_judge_llm": bool(
            source.get("use_judge_llm", DEFAULT_CONFIG["use_judge_llm"])
        ),
        "evidence_pool_max_entries": _positive_int(
            source.get("evidence_pool_max_entries"),
            DEFAULT_CONFIG["evidence_pool_max_entries"],
        ),
    }


def resolve_context_window(config: Dict[str, Any], model_name: Optional[str]) -> int:
    """Resolve an exact-model window before falling back to the global default."""
    normalized = normalize_context_compaction_config(config)
    name = str(model_name or "").strip()
    return int(
        normalized["per_model_window"].get(name, normalized["context_window"])
    )


def message_text(message: Any) -> str:
    """Extract text content without serializing provider metadata."""
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


def approximate_tokens(messages: Iterable[Any]) -> int:
    """Use LangChain's provider-neutral approximate counter with a safe fallback."""
    materialized = list(messages)
    try:
        return max(0, int(count_tokens_approximately(materialized)))
    except Exception:  # noqa: BLE001 - malformed third-party messages are non-fatal
        return max(0, sum(len(message_text(message)) // 4 + 3 for message in materialized))


def _usage_input_tokens(value: Any) -> Optional[int]:
    if not isinstance(value, dict):
        return None
    raw = value.get("input_tokens")
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


@dataclass
class TokenBudget:
    """A calibrated preflight estimate for the next model call."""

    system_prompt: str = ""
    max_tokens: int = 0
    context_window: int = DEFAULT_CONTEXT_WINDOW
    default_calibration: float = DEFAULT_CALIBRATION
    measured_input_tokens: Optional[int] = None
    baseline_message_approx: int = 0
    calibration: float = DEFAULT_CALIBRATION
    calibration_samples: Tuple[float, ...] = ()

    def __post_init__(self) -> None:
        self.max_tokens = max(0, int(self.max_tokens or 0))
        self.context_window = max(1, int(self.context_window or DEFAULT_CONTEXT_WINDOW))
        self.default_calibration = max(0.1, float(self.default_calibration or DEFAULT_CALIBRATION))
        self.calibration = max(0.1, float(self.calibration or self.default_calibration))

    @property
    def reserve(self) -> int:
        return approximate_tokens([SystemMessage(content=self.system_prompt)]) + self.max_tokens

    def record_usage(self, messages: Sequence[Any], usage_metadata: Any) -> bool:
        """Update the measured baseline and a bounded sliding calibration average."""
        measured = _usage_input_tokens(usage_metadata)
        if measured is None:
            return False
        message_approx = approximate_tokens(messages)
        full_approx = max(
            1,
            message_approx + approximate_tokens([SystemMessage(content=self.system_prompt)]),
        )
        ratio = max(0.1, measured / full_approx)
        samples = list(self.calibration_samples)[-4:] + [ratio]
        self.calibration_samples = tuple(samples[-5:])
        self.calibration = sum(self.calibration_samples) / len(self.calibration_samples)
        self.measured_input_tokens = measured
        self.baseline_message_approx = message_approx
        return True

    def estimate(self, messages: Sequence[Any]) -> int:
        """Estimate input plus system/output reserve for a future model invocation."""
        approx = approximate_tokens(messages)
        if self.measured_input_tokens is None:
            input_estimate = self.default_calibration * approx
        else:
            delta = approx - self.baseline_message_approx
            input_estimate = max(
                0.0, self.measured_input_tokens + self.calibration * delta
            )
        return int(round(input_estimate + self.reserve))

    def ratio(self, messages: Sequence[Any]) -> float:
        return self.estimate(messages) / self.context_window

    def to_state(self) -> Dict[str, Any]:
        return {
            "measured_input_tokens": self.measured_input_tokens,
            "baseline_message_approx": self.baseline_message_approx,
            "calibration": self.calibration,
            "calibration_samples": list(self.calibration_samples),
        }

    def restore(self, state: Optional[Dict[str, Any]]) -> None:
        """Restore session-local calibration persisted by the graph state."""
        if not isinstance(state, dict):
            return
        measured = state.get("measured_input_tokens")
        try:
            self.measured_input_tokens = int(measured) if measured is not None else None
        except (TypeError, ValueError):
            self.measured_input_tokens = None
        self.baseline_message_approx = _positive_int(
            state.get("baseline_message_approx"), 0, minimum=0
        )
        raw_samples = state.get("calibration_samples")
        samples: List[float] = []
        if isinstance(raw_samples, (list, tuple)):
            for value in raw_samples[-5:]:
                try:
                    samples.append(max(0.1, float(value)))
                except (TypeError, ValueError):
                    continue
        self.calibration_samples = tuple(samples)
        raw_calibration = state.get("calibration")
        try:
            self.calibration = max(0.1, float(raw_calibration))
        except (TypeError, ValueError):
            self.calibration = (
                sum(samples) / len(samples) if samples else self.default_calibration
            )


def tool_call_ids(message: Any) -> List[str]:
    if not isinstance(message, AIMessage):
        return []
    calls = getattr(message, "tool_calls", None) or []
    return [
        str(call.get("id") or "").strip()
        for call in calls
        if isinstance(call, dict) and str(call.get("id") or "").strip()
    ]


def assert_tool_call_pairing(messages: Sequence[Any]) -> None:
    """Assert that every native-tool request has its matching ToolMessage."""
    responses = {
        str(getattr(message, "tool_call_id", "") or "").strip()
        for message in messages
        if isinstance(message, ToolMessage)
        and str(getattr(message, "tool_call_id", "") or "").strip()
    }
    missing = [
        call_id
        for message in messages
        for call_id in tool_call_ids(message)
        if call_id not in responses
    ]
    assert not missing, "orphaned tool calls: " + ", ".join(sorted(set(missing)))


def safe_cut_index(messages: Sequence[Any], desired: int) -> int:
    """Move a cut left until no native tool-call pair straddles it."""
    materialized = list(messages)
    cut = max(0, min(int(desired), len(materialized)))
    responses: Dict[str, int] = {}
    for index, message in enumerate(materialized):
        if isinstance(message, ToolMessage):
            call_id = str(getattr(message, "tool_call_id", "") or "").strip()
            if call_id:
                responses[call_id] = index
    while cut > 0:
        crossing = [
            index
            for index, message in enumerate(materialized[:cut])
            for call_id in tool_call_ids(message)
            if call_id in responses and responses[call_id] >= cut
        ]
        if not crossing:
            break
        cut = min(crossing)
    return cut


@dataclass(frozen=True)
class MessagePartition:
    pinned: List[Any]
    compressible: List[Any]
    recent: List[Any]
    cut_index: int
    blocked: bool


def _first_user_index(messages: Sequence[Any]) -> Optional[int]:
    for index, message in enumerate(messages):
        if not isinstance(message, HumanMessage):
            continue
        text = message_text(message).lstrip()
        if not text.startswith(_TOOL_WRAPPER_PREFIX) and not text.startswith("[上下文"):
            return index
    return None


def partition(messages: Sequence[Any], keep_recent_rounds: int) -> MessagePartition:
    """Split the history into pinned, compressible, and recent tool rounds."""
    materialized = list(messages)
    first_user = _first_user_index(materialized)
    if first_user is None:
        return MessagePartition([], [], materialized, 0, True)
    round_starts = [
        index for index, message in enumerate(materialized) if tool_call_ids(message)
    ]
    keep = max(1, int(keep_recent_rounds or 1))
    if len(round_starts) <= keep:
        return MessagePartition(materialized[: first_user + 1], [], materialized[first_user + 1 :], first_user + 1, True)
    desired = max(first_user + 1, round_starts[-keep])
    cut = safe_cut_index(materialized, desired)
    if cut <= first_user + 1:
        return MessagePartition(materialized[: first_user + 1], [], materialized[first_user + 1 :], cut, True)
    return MessagePartition(
        materialized[: first_user + 1],
        materialized[first_user + 1 : cut],
        materialized[cut:],
        cut,
        False,
    )


def _record_eid(record: Dict[str, Any]) -> Optional[int]:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    raw = metadata.get("eid")
    try:
        eid = int(raw)
    except (TypeError, ValueError):
        return None
    return eid if eid > 0 else None


def evidence_headers(
    ledger: Any,
    evidence_records: Sequence[Dict[str, Any]],
    content: str,
) -> List[str]:
    """Resolve citation ids in an observation into content-free ledger headers."""
    by_eid = {
        eid: record
        for record in evidence_records
        if isinstance(record, dict) and (eid := _record_eid(record)) is not None
    }
    headers: List[str] = []
    seen = set()
    for raw in _EID_RE.findall(content or ""):
        eid = int(raw)
        if eid in seen:
            continue
        seen.add(eid)
        record = None
        resolver = getattr(ledger, "resolve", None)
        if callable(resolver):
            record = resolver(eid)
        record = record or by_eid.get(eid)
        renderer = getattr(ledger, "render_header", None)
        if callable(renderer):
            headers.append(renderer(eid, record))
        elif record is not None:
            headers.append(f"[E{eid}] {str(record.get('reference') or '').strip()}")
        else:
            headers.append(f"[E{eid}] unknown")
    return headers


def _replace_content(message: Any, content: str) -> Any:
    copier = getattr(message, "model_copy", None)
    if callable(copier):
        return copier(update={"content": content})
    if isinstance(message, ToolMessage):
        return ToolMessage(
            content=content,
            tool_call_id=str(getattr(message, "tool_call_id", "") or ""),
            id=getattr(message, "id", None),
            name=getattr(message, "name", None),
        )
    if isinstance(message, HumanMessage):
        return HumanMessage(content=content, id=getattr(message, "id", None))
    return message


def fold_evidence_messages(
    messages: Sequence[Any],
    ledger: Any,
    evidence_records: Sequence[Dict[str, Any]],
) -> List[Any]:
    """Replace old tool bodies with ledger pointers while preserving message ids."""
    result: List[Any] = []
    for message in messages:
        is_tool_observation = isinstance(message, ToolMessage) or (
            isinstance(message, HumanMessage)
            and message_text(message).lstrip().startswith(_TOOL_WRAPPER_PREFIX)
        )
        if not is_tool_observation:
            result.append(message)
            continue
        headers = evidence_headers(ledger, evidence_records, message_text(message))
        if headers:
            pointer = "\n".join(headers)
            pointer += "\n[证据正文已折叠；需要原文时调用 recall_evidence。]"
        else:
            pointer = "[工具观察已折叠；该结果没有可回灌的 ledger 编号。]"
        result.append(_replace_content(message, pointer))
    return result


def _safe_detail(value: Any, limit: int = 360) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip()


def render_decision_trace(
    state: Dict[str, Any],
    *,
    tool_budgets: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """Deterministically render the state the model needs after compaction."""
    lines = ["[决策轨迹]"]
    calls: List[str] = []
    for message in list(state.get("messages") or []):
        for call in getattr(message, "tool_calls", None) or []:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "unknown")
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            calls.append(f"- 已调用 {name}: {_safe_detail(json.dumps(args, ensure_ascii=False, sort_keys=True), 220)}")
    if calls:
        lines.extend(calls[-16:])

    failures = [
        item for item in list(state.get("fetch_outcomes") or [])
        if isinstance(item, dict) and str(item.get("status") or "").casefold() != "success"
    ]
    for item in failures[-12:]:
        url = str(item.get("url") or item.get("resolved_url") or "unknown")
        reason = str(item.get("error_type") or item.get("reason") or item.get("status") or "failed")
        lines.append(f"- 不要重试抓取 {url}: {_safe_detail(reason, 180)}")

    for verdict in list(state.get("verdicts") or [])[-10:]:
        if not isinstance(verdict, dict):
            continue
        iteration = verdict.get("iteration", "?")
        reason = _safe_detail(verdict.get("reason") or "continue", 160)
        hits = verdict.get("rule_hits") or []
        details = [
            _safe_detail(hit.get("detail"), 180)
            for hit in hits
            if isinstance(hit, dict) and hit.get("detail")
        ]
        suffix = f"; rule_hits: {' | '.join(details[:3])}" if details else ""
        lines.append(f"- 第 {iteration} 轮评估: {reason}{suffix}")

    missing = [str(value) for value in list(state.get("constraints_missing") or []) if str(value)]
    if missing:
        lines.append("- 当前缺口: " + ", ".join(missing[:12]))
    if tool_budgets:
        summary = ", ".join(
            f"{name}={status.get('used', 0)}/{status.get('limit', 0)}"
            for name, status in sorted(tool_budgets.items())
            if isinstance(status, dict)
        )
        if summary:
            lines.append("- 工具预算: " + summary)
    if state.get("context_budget") is not None:
        lines.append(
            "- 上下文预算: "
            + str(state.get("context_budget"))
            + f" ({float(state.get('context_ratio') or 0):.3f})"
        )
    return "\n".join(lines)


SUMMARY_SYSTEM_PROMPT = """你负责压缩搜索代理的历史上下文。
只可依据输入中的决策轨迹、既有答案草稿和 [En] 证据编号；不得引入新数值、新来源或新推论。
严格按六段输出：
1. 待答问题与硬约束
2. 已确认结论
3. 已排除路径及原因
4. 尚缺证据缺口
5. 工具预算消耗
6. 建议的下一步
所有事实只能用已有 [En] 指代。"""


def summary_prompt(
    state: Dict[str, Any],
    span: Sequence[Any],
    ledger: Any,
    evidence_records: Sequence[Dict[str, Any]],
    *,
    tool_budgets: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """Build a projection that intentionally excludes raw ToolMessage bodies."""
    drafts = [
        message_text(message).strip()
        for message in span
        if isinstance(message, AIMessage)
        and not tool_call_ids(message)
        and message_text(message).strip()
    ]
    eids = []
    for record in evidence_records:
        if isinstance(record, dict) and (eid := _record_eid(record)) is not None:
            eids.append(eid)
    headers = []
    for eid in sorted(set(eids)):
        record = None
        resolver = getattr(ledger, "resolve", None)
        if callable(resolver):
            record = resolver(eid)
        renderer = getattr(ledger, "render_header", None)
        if callable(renderer):
            headers.append(renderer(eid, record))
    return "\n\n".join(
        [
            render_decision_trace(state, tool_budgets=tool_budgets),
            "[历史答案草稿]\n" + ("\n---\n".join(drafts) if drafts else "(无)"),
            "[可用证据目录]\n" + ("\n".join(headers) if headers else "(无)"),
        ]
    )


def unknown_summary_evidence_ids(
    text: str,
    ledger: Any,
    evidence_records: Sequence[Dict[str, Any]],
) -> List[int]:
    """Return cited ids that are absent from both the ledger and state records."""
    known = {
        eid
        for record in evidence_records
        if isinstance(record, dict) and (eid := _record_eid(record)) is not None
    }
    resolver = getattr(ledger, "resolve", None)
    unknown: List[int] = []
    for raw in _EID_RE.findall(text or ""):
        eid = int(raw)
        if eid in known:
            continue
        if callable(resolver) and resolver(eid) is not None:
            known.add(eid)
            continue
        if eid not in unknown:
            unknown.append(eid)
    return unknown


def summarize(
    state: Dict[str, Any],
    span: Sequence[Any],
    *,
    llm: Any,
    judge_llm: Any,
    use_judge_llm: bool,
    summary_max_tokens: int,
    ledger: Any,
    evidence_records: Sequence[Dict[str, Any]],
    tool_budgets: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """Call the preferred summary model with deterministic, body-free input."""
    model = judge_llm if use_judge_llm and judge_llm is not None else llm
    if model is None:
        raise RuntimeError("No summary model is available")
    prompt = summary_prompt(
        state,
        span,
        ledger,
        evidence_records,
        tool_budgets=tool_budgets,
    )
    response = model.invoke(
        [SystemMessage(content=SUMMARY_SYSTEM_PROMPT), HumanMessage(content=prompt)],
        temperature=0,
        max_tokens=max(1, int(summary_max_tokens)),
    )
    text = message_text(response).strip()
    if not text:
        raise RuntimeError("Summary model returned no text")
    unknown_ids = unknown_summary_evidence_ids(text, ledger, evidence_records)
    if unknown_ids:
        raise RuntimeError(
            "Summary referenced unknown evidence: "
            + ", ".join(f"E{eid}" for eid in unknown_ids)
        )
    return text


def deterministic_summary(
    state: Dict[str, Any],
    *,
    tool_budgets: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    return render_decision_trace(state, tool_budgets=tool_budgets)


def _message_marker(message: Any) -> Tuple[str, Any]:
    message_id = getattr(message, "id", None)
    return ("id", message_id) if message_id else ("object", id(message))


def _retain_complete_messages(
    messages: Sequence[Any],
    selected: Sequence[Any],
    preserve: Sequence[Any],
) -> List[Any]:
    """Keep selected messages plus required ones, closing over native tool pairs."""
    materialized = list(messages)
    selected_markers = {_message_marker(message) for message in selected}
    preserve_markers = {_message_marker(message) for message in preserve}
    keep = {
        index
        for index, message in enumerate(materialized)
        if _message_marker(message) in selected_markers | preserve_markers
    }
    requests: Dict[str, int] = {}
    responses: Dict[str, List[int]] = {}
    for index, message in enumerate(materialized):
        for call_id in tool_call_ids(message):
            requests[call_id] = index
        if isinstance(message, ToolMessage):
            call_id = str(getattr(message, "tool_call_id", "") or "").strip()
            if call_id:
                responses.setdefault(call_id, []).append(index)

    changed = True
    while changed:
        changed = False
        for index in tuple(keep):
            message = materialized[index]
            for call_id in tool_call_ids(message):
                for response_index in responses.get(call_id, []):
                    if response_index not in keep:
                        keep.add(response_index)
                        changed = True
            if isinstance(message, ToolMessage):
                call_id = str(getattr(message, "tool_call_id", "") or "").strip()
                request_index = requests.get(call_id)
                if request_index is not None and request_index not in keep:
                    keep.add(request_index)
                    changed = True
    return [message for index, message in enumerate(materialized) if index in keep]


def truncate_tail(
    messages: Sequence[Any],
    max_tokens: int,
    *,
    preserve: Optional[Sequence[Any]] = None,
) -> List[Any]:
    """Use LangChain's trimmer while retaining required messages and tool pairs."""
    materialized = list(messages)
    required = list(preserve or [])
    try:
        trimmed = trim_messages(
            materialized,
            max_tokens=max(1, int(max_tokens)),
            token_counter=count_tokens_approximately,
            strategy="last",
            include_system=False,
        )
        retained = _retain_complete_messages(materialized, trimmed, required)
        assert_tool_call_pairing(retained)
        return retained
    except Exception:  # noqa: BLE001 - fallback must always preserve loop progress
        first_user = _first_user_index(materialized)
        tail_start = safe_cut_index(materialized, max(0, len(materialized) - 6))
        prefix = materialized[: first_user + 1] if first_user is not None else []
        tail = materialized[max(tail_start, len(prefix)) :]
        result = _retain_complete_messages(materialized, prefix + tail, required)
        try:
            assert_tool_call_pairing(result)
            return result
        except AssertionError:
            return prefix
