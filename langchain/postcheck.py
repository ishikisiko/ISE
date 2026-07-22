"""Post-check utilities for validating search answers before returning them."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from utils.time_parser import TimeConstraint


INSUFFICIENT_INFO_PATTERNS = (
    "未在搜索结果或本地文档中找到具体数据",
    "未在搜索结果和本地文档中找到具体数据",
    "specific data not found",
    "information is insufficient",
    "信息不足",
    "无法确认",
    "cannot determine",
)

COMPARISON_PATTERNS = ("compare", "comparison", "vs", "versus", "对比", "比较", "区别", "差异")
MULTI_HOP_PATTERNS = (
    "why",
    "how",
    "cause",
    "reason",
    "trend",
    "summarize",
    "contrast",
    "analyze",
    "为什么",
    "原因",
    "趋势",
    "分析",
    "总结",
    "对比",
    "比较",
)


@dataclass
class PostcheckVerdict:
    """Structured post-check verdict used by the default search pipeline."""

    eligible: bool = True
    skipped_reason: Optional[str] = None
    rule_hits: List[Dict[str, str]] = field(default_factory=list)
    judge_used: bool = False
    judge_error: Optional[str] = None
    passes_postcheck: bool = True
    should_fallback_to_react: bool = False
    recoverable: bool = False
    failure_types: List[str] = field(default_factory=list)
    missing_constraints: List[str] = field(default_factory=list)
    evidence_sufficiency: str = "sufficient"
    reason: str = "passed_rule_screen"

    def add_rule(self, rule_id: str, detail: str) -> None:
        """Record a screening rule hit."""
        self.rule_hits.append({"rule": rule_id, "detail": detail})

    def to_dict(self) -> Dict[str, Any]:
        """Convert verdict to a JSON-serializable dictionary."""
        return {
            "eligible": self.eligible,
            "skipped_reason": self.skipped_reason,
            "rule_hits": list(self.rule_hits),
            "judge_used": self.judge_used,
            "judge_error": self.judge_error,
            "passes_postcheck": self.passes_postcheck,
            "should_fallback_to_react": self.should_fallback_to_react,
            "recoverable": self.recoverable,
            "failure_types": list(self.failure_types),
            "missing_constraints": list(self.missing_constraints),
            "evidence_sufficiency": self.evidence_sufficiency,
            "reason": self.reason,
        }


def _normalize_text(text: Any) -> str:
    if text is None:
        return ""
    return str(text).strip()


def _answer_body(answer: str) -> str:
    for delimiter in ("\n\n**网络来源", "\n\n**本地文档来源"):
        if delimiter in answer:
            return answer.split(delimiter, 1)[0]
    return answer


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in patterns)


def _extract_numbers(text: str) -> List[str]:
    return re.findall(r"\b\d+(?:\.\d+)?%?\b", text)


def _stringify_evidence(search_hits: List[Dict[str, Any]], retrieved_docs: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for hit in search_hits:
        parts.append(
            " ".join(
                [
                    _normalize_text(hit.get("title")),
                    _normalize_text(hit.get("snippet")),
                    _normalize_text(hit.get("url")),
                ]
            )
        )
    for doc in retrieved_docs:
        parts.append(
            " ".join(
                [
                    _normalize_text(doc.get("source")),
                    _normalize_text(doc.get("content")),
                ]
            )
        )
    return " ".join(parts).lower()


def _tokenize_for_overlap(text: str) -> List[str]:
    """Tokenize text for overlap comparison (latin words/numbers + single CJK chars)."""
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower())


def evidence_increment_ratio(pool_text: str, new_observation: str) -> float:
    """Return the ratio of unique tokens in new_observation absent from pool_text.

    A ratio near 0 means the observation adds no new information; near 1 means
    it is entirely new. Empty observations always return 0.0.
    """
    observation_tokens = set(_tokenize_for_overlap(_normalize_text(new_observation)))
    if not observation_tokens:
        return 0.0
    pool_tokens = set(_tokenize_for_overlap(_normalize_text(pool_text)))
    new_tokens = observation_tokens - pool_tokens
    return len(new_tokens) / len(observation_tokens)


def check_constraint_coverage(
    query: str,
    evidence_text: str,
    draft_answer: str,
    time_constraint: Optional[TimeConstraint] = None,
) -> tuple[List[str], List[str]]:
    """Check which query constraints are covered by the current draft and evidence.

    Returns (constraints_met, constraints_missing). Constraint identifiers:
    "time_constraint", "comparison", "multi_hop_reasoning".
    """
    met: List[str] = []
    missing: List[str] = []
    body = _answer_body(_normalize_text(draft_answer))
    body_lower = body.lower()
    query_lower = (query or "").lower()

    if time_constraint and getattr(time_constraint, "days", None):
        time_text = f"{body} {_normalize_text(evidence_text)}"
        has_time_signal = bool(re.search(r"(?<!\d)20\d{2}(?!\d)", time_text)) or _contains_any(
            time_text.lower(),
            ("current date", "today", "latest", "recent", "今天", "当前", "最近", "最新", "过去", "近"),
        )
        (met if has_time_signal else missing).append("time_constraint")

    if _contains_any(query_lower, COMPARISON_PATTERNS):
        comparison_markers = ("相比", "而", "同时", "vs", "versus", "compared", "both", "分别", "对比", "比较")
        covered = _contains_any(body_lower, comparison_markers) and len(body) >= 120
        (met if covered else missing).append("comparison")

    if _contains_any(query_lower, MULTI_HOP_PATTERNS):
        covered = len(body) >= 160
        (met if covered else missing).append("multi_hop_reasoning")

    return met, missing


def screen_search_answer(
    *,
    query: str,
    answer: str,
    search_hits: Optional[List[Dict[str, Any]]] = None,
    retrieved_docs: Optional[List[Dict[str, Any]]] = None,
    time_constraint: Optional[TimeConstraint] = None,
    search_error: Optional[str] = None,
) -> PostcheckVerdict:
    """Run rule-based screening on a search answer before optional LLM judging."""
    verdict = PostcheckVerdict()
    hits = search_hits or []
    docs = retrieved_docs or []
    body = _answer_body(_normalize_text(answer))
    body_lower = body.lower()
    evidence_text = _stringify_evidence(hits, docs)

    if not body:
        verdict.passes_postcheck = False
        verdict.recoverable = True
        verdict.should_fallback_to_react = True
        verdict.evidence_sufficiency = "insufficient"
        verdict.failure_types.append("empty_answer")
        verdict.reason = "empty_answer"
        verdict.add_rule("empty_answer", "Search pipeline returned an empty answer body.")
        return verdict

    if search_error:
        verdict.passes_postcheck = False
        verdict.recoverable = False
        verdict.should_fallback_to_react = False
        verdict.evidence_sufficiency = "insufficient"
        verdict.failure_types.append("search_unavailable")
        verdict.reason = "search_unavailable"
        verdict.add_rule("search_unavailable", "Search pipeline reported a search error.")
        return verdict

    if _contains_any(body_lower, INSUFFICIENT_INFO_PATTERNS):
        verdict.passes_postcheck = False
        verdict.recoverable = False
        verdict.should_fallback_to_react = False
        verdict.evidence_sufficiency = "insufficient"
        verdict.failure_types.append("acknowledged_insufficient_information")
        verdict.reason = "answer_acknowledged_insufficient_information"
        verdict.add_rule(
            "answer_acknowledged_insufficient_information",
            "Answer explicitly states that evidence is insufficient.",
        )
        return verdict

    if not hits and not docs:
        verdict.passes_postcheck = False
        verdict.recoverable = True
        verdict.should_fallback_to_react = True
        verdict.evidence_sufficiency = "insufficient"
        verdict.failure_types.append("insufficient_evidence")
        verdict.reason = "no_evidence_returned"
        verdict.add_rule("no_evidence_returned", "No search hits or local documents were available.")

    if time_constraint and time_constraint.days:
        body_has_time_signal = bool(re.search(r"\b20\d{2}\b", body)) or _contains_any(
            body_lower,
            ("current date", "today", "latest", "recent", "今天", "当前", "最近", "最新", "过去", "近"),
        )
        if not body_has_time_signal:
            verdict.passes_postcheck = False
            verdict.recoverable = True
            verdict.should_fallback_to_react = True
            verdict.missing_constraints.append("time_constraint")
            verdict.failure_types.append("missing_time_constraint")
            verdict.reason = "missing_time_constraint"
            verdict.add_rule(
                "missing_time_constraint",
                "Query carries a time constraint but the answer does not reflect it.",
            )

    if _contains_any(query, COMPARISON_PATTERNS):
        comparison_markers = ("相比", "而", "同时", "vs", "versus", "compared", "both", "分别", "对比", "比较")
        if not _contains_any(body_lower, comparison_markers) or len(body) < 120:
            verdict.passes_postcheck = False
            verdict.recoverable = True
            verdict.should_fallback_to_react = True
            verdict.missing_constraints.append("comparison")
            verdict.failure_types.append("missing_comparison_coverage")
            verdict.reason = "missing_comparison_coverage"
            verdict.add_rule(
                "missing_comparison_coverage",
                "Comparison-style query appears to be answered without explicit comparison coverage.",
            )

    answer_numbers = [token for token in _extract_numbers(body) if token not in {"1", "2", "3", "4", "5"}]
    unsupported_numbers = [token for token in answer_numbers if token.lower() not in evidence_text]
    if unsupported_numbers:
        verdict.passes_postcheck = False
        verdict.recoverable = True
        verdict.should_fallback_to_react = True
        verdict.failure_types.append("unsupported_specific_detail")
        verdict.reason = "unsupported_specific_detail"
        preview = ", ".join(unsupported_numbers[:4])
        verdict.add_rule(
            "unsupported_specific_detail",
            f"Answer contains numeric details not found in the available evidence: {preview}",
        )

    if _contains_any(query, MULTI_HOP_PATTERNS) and len(body) < 160:
        verdict.passes_postcheck = False
        verdict.recoverable = True
        verdict.should_fallback_to_react = True
        verdict.failure_types.append("needs_multi_hop_reasoning")
        verdict.reason = "needs_multi_hop_reasoning"
        verdict.add_rule(
            "needs_multi_hop_reasoning",
            "Query looks multi-hop or analytical but the answer is very short.",
        )

    if verdict.rule_hits and not hits and not docs:
        verdict.evidence_sufficiency = "insufficient"

    if verdict.rule_hits and verdict.evidence_sufficiency == "sufficient":
        verdict.evidence_sufficiency = "unknown"

    if not verdict.rule_hits:
        verdict.reason = "passed_rule_screen"
        verdict.evidence_sufficiency = "sufficient" if hits or docs else "unknown"
        verdict.passes_postcheck = True
        verdict.should_fallback_to_react = False

    return verdict


def merge_judge_verdict(
    rule_verdict: PostcheckVerdict,
    judge_payload: Dict[str, Any],
) -> PostcheckVerdict:
    """Merge a structured judge verdict into the rule-screen verdict."""
    verdict = PostcheckVerdict(
        eligible=rule_verdict.eligible,
        skipped_reason=rule_verdict.skipped_reason,
        rule_hits=list(rule_verdict.rule_hits),
        judge_used=True,
        judge_error=rule_verdict.judge_error,
        passes_postcheck=rule_verdict.passes_postcheck,
        should_fallback_to_react=rule_verdict.should_fallback_to_react,
        recoverable=rule_verdict.recoverable,
        failure_types=list(rule_verdict.failure_types),
        missing_constraints=list(rule_verdict.missing_constraints),
        evidence_sufficiency=rule_verdict.evidence_sufficiency,
        reason=rule_verdict.reason,
    )

    judge_failure_types = judge_payload.get("failure_types") or []
    if isinstance(judge_failure_types, str):
        judge_failure_types = [judge_failure_types]
    verdict.failure_types = [
        value for value in dict.fromkeys(list(verdict.failure_types) + [str(item) for item in judge_failure_types if item])
    ]

    missing_constraints = judge_payload.get("missing_constraints") or []
    if isinstance(missing_constraints, str):
        missing_constraints = [missing_constraints]
    verdict.missing_constraints = [
        value
        for value in dict.fromkeys(list(verdict.missing_constraints) + [str(item) for item in missing_constraints if item])
    ]

    evidence = _normalize_text(judge_payload.get("evidence_sufficiency"))
    if evidence:
        verdict.evidence_sufficiency = evidence

    passes = judge_payload.get("passes_postcheck")
    if isinstance(passes, bool):
        verdict.passes_postcheck = passes

    judge_reason = _normalize_text(judge_payload.get("reason"))
    if judge_reason:
        verdict.reason = judge_reason

    judge_should_fallback = judge_payload.get("should_fallback_to_react")
    if isinstance(judge_should_fallback, bool):
        verdict.should_fallback_to_react = judge_should_fallback

    recoverable = judge_payload.get("recoverable")
    if isinstance(recoverable, bool):
        verdict.recoverable = recoverable
    else:
        verdict.recoverable = verdict.should_fallback_to_react

    if any(
        failure in verdict.failure_types
        for failure in ("search_unavailable", "acknowledged_insufficient_information", "configuration_error")
    ):
        verdict.recoverable = False
        verdict.should_fallback_to_react = False

    return verdict
