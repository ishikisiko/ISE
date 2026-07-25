"""Deterministic analysis, evidence ledger, termination critic, and trace.

The module deliberately has no dependency on LangChain or a concrete search
provider.  That keeps query semantics, execution budgets, and audit-safe
provenance usable by the CLI, Flask, RAG, and recovery paths alike.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit

from utils.time_parser import TimeConstraint, parse_time_constraint


COMPARISON_CUES = (
    "对比",
    "比较",
    "差异",
    "区别",
    "compare",
    "comparison",
    "versus",
    " vs ",
)
CURRENT_CUES = ("最新", "当前", "现在", "今天", "今日", "latest", "current", "today", "now")
EXPLICIT_TEMPORAL_PATTERNS = (
    r"(?:过去|近|最近)\s*(?:\d+|一|两|三|四|五|六|七|八|九|十)+\s*(?:年|个月|月|天|周)",
    r"(?:历年|历史|趋势|变化趋势|逐年|时间序列)",
    r"\b(?:last|past|over\s+the\s+last)\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:years?|months?|weeks?|days?)\b",
    r"\b(?:historical|history|trend|year[- ]over[- ]year|time\s+series)\b",
    r"\b20\d{2}\s*(?:-|to|through|–|—)\s*20\d{2}\b",
)
HISTORICAL_COVERAGE_PATTERNS = (
    r"(?:过去|近|最近)\s*(?:\d+|一|两|三|四|五|六|七|八|九|十)+\s*年",
    r"(?:历年|历史|变化趋势|逐年|时间序列)",
    r"\b(?:last|past|over\s+the\s+last)\s+"
    r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+years?\b",
    r"\b(?:historical|history|year[- ]over[- ]year|time\s+series)\b",
    r"\b20\d{2}\s*(?:-|to|through|–|—)\s*20\d{2}\b",
)
PRICE_CUES = (
    "价格",
    "定价",
    "费用",
    "收费",
    "成本",
    "套餐",
    "price",
    "pricing",
    "cost",
    "rate",
    "tariff",
)
PRICING_EVIDENCE_CUES = (
    "价格",
    "定价",
    "费用",
    "收费",
    "成本",
    "price",
    "pricing",
    "cost",
    "rate",
    "tariff",
)
COMPLIANCE_CUES = ("合规", "监管", "条款", "compliance", "regulation", "policy", "terms")
COMPARISON_COVERAGE_CUES = (
    "compare", "comparison", "vs", "versus", "对比", "比较", "区别", "差异",
)
MULTI_HOP_CUES = (
    "why", "how", "cause", "reason", "trend", "summarize", "contrast", "analyze",
    "为什么", "原因", "趋势", "分析", "总结", "对比", "比较",
)
AMBIGUOUS_REFERENCE_CUES = ("这个", "那个", "它", "前者", "后者", "this", "that", "it", "former", "latter")
SENSITIVE_FIELD_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "prompt",
    "full_content",
    "headers",
)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|cookie|token|secret|password)\s*([:=])\s*[^\s,;]+"
)
URL_WITH_QUERY_RE = re.compile(r"https?://[^\s?#]+(?:\?[^\s#]*)?(?:#[^\s]*)?")
TRACKING_OR_SENSITIVE_QUERY_MARKERS = (
    "utm_",
    "gclid",
    "fbclid",
    "token",
    "key",
    "auth",
    "signature",
    "session",
)

DEFAULT_TERMINATION_CONFIG: Dict[str, Any] = {
    "max_iterations": 5,
    "judge_interval": 2,
    "repeat_threshold": 2,
    "no_progress_threshold": 2,
    "tool_error_threshold": 2,
    "new_evidence_min_ratio": 0.1,
}


def normalize_termination_config(config: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Normalize the single M4 critic/judge/budget configuration block."""
    merged = dict(DEFAULT_TERMINATION_CONFIG)
    if not isinstance(config, Mapping):
        return merged
    for key in merged:
        if key not in config:
            continue
        try:
            if key == "new_evidence_min_ratio":
                merged[key] = max(0.0, min(1.0, float(config[key])))
            else:
                merged[key] = max(1, int(config[key]))
        except (TypeError, ValueError):
            continue
    return merged


ENTITY_SUFFIX_RE = re.compile(
    r"(?:\bapi\b|\bapi\s*(?:价格|定价|price|pricing)?|价格|定价|费用|收费|成本|套餐|"
    r"price|pricing|cost|comparison|compare|对比|比较|哪个好|怎么样|是什么|多少|的)+$",
    re.IGNORECASE,
)
TEMPORAL_ENTITY_TRAILING_RE = re.compile(
    r"\s+(?:over\s+the\s+last|in\s+the\s+last|last|past)\s+"
    r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:years?|months?|weeks?|days?)$",
    re.IGNORECASE,
)
ENTITY_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]*")

# Generic tokens that carry no entity signal. Used to keep brand names (e.g.
# "OpenAI", "GLM") in the fallback search query after stripping intent cues.
_QUERY_SUBJECT_STOPWORDS = frozenset(
    str(token).casefold()
    for token in (
        "api", "model", "models", "sdk", "doc", "docs", "service", "services",
        "platform", "platforms", "app", "apps", "tool", "tools",
        "price", "pricing", "cost", "rate", "tariff", "fee", "fees",
        "latest", "current", "today", "now", "new",
        "compare", "comparison", "versus", "vs", "and", "or", "of", "for", "the",
        "compliance", "regulation", "policy", "terms",
        # Question/function words carry no entity signal. Listed here so the
        # brand-candidate extractor can safely accept lowercase brand tokens
        # without also admitting "what/how/is/...".
        "what", "which", "when", "where", "who", "whom", "whose", "why", "how",
        "is", "are", "was", "were", "be", "been", "being", "am",
        "do", "does", "did", "doing", "done",
        "can", "could", "should", "would", "shall", "will", "may", "might", "must",
        "has", "have", "had", "having",
        "tell", "show", "list", "find", "give", "explain", "describe",
        "about", "with", "that", "this", "these", "those", "from", "into", "on",
        "in", "at", "by", "to", "as", "your", "their", "they", "them", "it",
        "its", "there", "here", "please", "want", "need", "get", "use", "using",
        "used", "between", "than", "then", "also", "any", "some", "all",
    )
)


def _bounded_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    text = SENSITIVE_ASSIGNMENT_RE.sub(r"\1\2[redacted]", text)
    text = URL_WITH_QUERY_RE.sub(
        lambda match: match.group(0).split("?", 1)[0].split("#", 1)[0],
        text,
    )
    return text if len(text) <= limit else f"{text[: max(0, limit - 3)]}..."


def _dedupe_strings(values: Iterable[Any], *, limit: int = 12) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for value in values:
        text = _bounded_text(value, 160).strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def _contains_any(text: str, cues: Sequence[str]) -> bool:
    lowered = text.casefold()
    return any(cue.casefold() in lowered for cue in cues)


def extract_numbers(text: Any) -> List[str]:
    """Extract bounded numeric details for unsupported-claim checks."""
    return re.findall(r"\b\d+(?:\.\d+)?%?\b", str(text or ""))


def _answer_body(answer: Any) -> str:
    text = str(answer or "").strip()
    for delimiter in ("\n\n**网络来源", "\n\n**本地文档来源"):
        if delimiter in text:
            return text.split(delimiter, 1)[0]
    return text


def _coverage_tokens(text: Any) -> List[str]:
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", str(text or "").casefold())


def evidence_increment_ratio(pool_text: Any, new_observation: Any) -> float:
    """Return the fraction of observation tokens not already in the pool."""
    observation_tokens = set(_coverage_tokens(new_observation))
    if not observation_tokens:
        return 0.0
    new_tokens = observation_tokens - set(_coverage_tokens(pool_text))
    return len(new_tokens) / len(observation_tokens)


def check_constraint_coverage(
    query: str,
    evidence_text: str,
    draft_answer: str,
    time_constraint: Optional[TimeConstraint] = None,
) -> tuple[List[str], List[str]]:
    """Check deterministic time, comparison, and multi-hop answer criteria."""
    met: List[str] = []
    missing: List[str] = []
    body = _answer_body(draft_answer)
    query_lower = str(query or "").casefold()

    if time_constraint and getattr(time_constraint, "days", None):
        time_text = f"{body} {str(evidence_text or '').strip()}"
        has_time_signal = bool(
            re.search(r"(?<!\d)20\d{2}(?!\d)", time_text)
        ) or _contains_any(
            time_text,
            (
                "current date", "today", "latest", "recent", "now",
                "今天", "今日", "当前", "现在", "最近", "最新", "过去", "近",
            ),
        )
        (met if has_time_signal else missing).append("time_constraint")

    if _contains_any(query_lower, COMPARISON_COVERAGE_CUES):
        covered = _contains_any(
            body,
            (
                "相比", "而", "同时", "vs", "versus", "compared", "both",
                "分别", "对比", "比较",
            ),
        ) and len(body) >= 120
        (met if covered else missing).append("comparison")

    if _contains_any(query_lower, MULTI_HOP_CUES):
        (met if len(body) >= 160 else missing).append("multi_hop_reasoning")

    return met, missing


def _safe_mapping(value: Any, *, depth: int = 0) -> Any:
    """Project arbitrary provider metadata into a compact audit-safe value."""
    # Execution-trace events legitimately contain two nested record levels
    # (event -> ledger decision -> provenance list). Keep those facts intact;
    # deeper arbitrary provider payloads are still bounded and truncated.
    if depth > 5:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _bounded_text(value, 240)
    if isinstance(value, Mapping):
        safe: Dict[str, Any] = {}
        for key, child in value.items():
            name = str(key)
            if any(marker in name.casefold() for marker in SENSITIVE_FIELD_MARKERS):
                continue
            safe[name] = _safe_mapping(child, depth=depth + 1)
            if len(safe) >= 20:
                safe["truncated"] = True
                break
        return safe
    if isinstance(value, (list, tuple, set)):
        return [_safe_mapping(item, depth=depth + 1) for item in list(value)[:20]]
    return _bounded_text(value, 240)


def canonical_reference(reference: Any) -> str:
    """Return a stable, audit-safe reference identity.

    Query parameters are intentionally omitted.  They are rarely useful for
    evidence identity and may contain tokens or session identifiers.
    """
    raw = str(reference or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return _bounded_text(raw, 240)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return _bounded_text(raw.split("?", 1)[0].split("#", 1)[0], 240)
    host = parsed.netloc.casefold()
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.casefold(), host, path, "", ""))


def _clean_entity_fragment(value: str) -> str:
    text = value.strip(" \t\n,，、;；:：()[]{}<>《》\"'`?？!！")
    text = re.sub(
        r"^(?:请|帮我|请问|告诉我|将|把|关于|对|和|与|and|compare|comparison)\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = TEMPORAL_ENTITY_TRAILING_RE.sub("", text)
    text = ENTITY_SUFFIX_RE.sub("", text).strip(" 的-_/：:")
    return _bounded_text(text, 80)


def _comparison_member_mentioned(text: Any, member: Any) -> bool:
    """Match model labels across harmless spaces and Unicode hyphen variants."""
    text_value = str(text or "").casefold()
    member_value = str(member or "").casefold()
    if not text_value or not member_value:
        return False
    if member_value in text_value:
        return True
    normalized_text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text_value)
    normalized_member = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", member_value)
    return bool(normalized_member and normalized_member in normalized_text)


def _extract_brand_candidates(query: str) -> List[str]:
    """Surface brand/product tokens independent of comparison structure.

    The deterministic analyzer historically only emitted entities that were
    comparison members or version-bearing model tokens, so non-comparison
    queries about a brand ("anthropic claude pricing") came back entity-less
    and official-domain recognition had nothing to bind to. These candidates
    feed the official-domain resolver, which verifies them independently, so
    recall is preferred here and false positives are cheap: they simply fail
    to verify and the tier stays ``unknown``.
    """
    candidates: List[str] = []
    for token in ENTITY_TOKEN_RE.findall(query):
        if len(token) < 2:
            continue
        lowered = token.casefold()
        if lowered in _QUERY_SUBJECT_STOPWORDS:
            continue
        has_digit = any(ch.isdigit() for ch in token)
        capitalized = token[0].isupper()
        # Accept proper-noun capitalization, versioned model tokens, or a long
        # lowercase alphabetic run (brand typed lowercase). Short lowercase
        # tokens are too ambiguous (e.g. "an", "or") and are skipped.
        if has_digit or capitalized or (len(lowered) >= 4 and token.isalpha()):
            candidates.append(token)
    return _dedupe_strings(candidates, limit=8)


def _extract_comparison_members(query: str) -> List[str]:
    lowered = query.casefold()
    cue_index = -1
    cue_length = 0
    for cue in COMPARISON_CUES:
        index = lowered.find(cue.casefold())
        if index >= 0 and (cue_index < 0 or index < cue_index):
            cue_index = index
            cue_length = len(cue)
    if cue_index < 0:
        return []

    tail = query[cue_index + cue_length :]
    prefix = query[:cue_index]
    separator = r"(?:\s*(?:,|，|、|/|;|；)\s*|\s+vs\.?\s+|\s+versus\s+|\s+and\s+|\s*(?:和|与)\s*)"
    fragments = re.split(separator, tail, flags=re.IGNORECASE)
    # Chinese requests also commonly put the comparison word at the end:
    # "苹果和微软的区别".  Only use the prefix when the explicit tail cannot
    # already resolve two members, so a leading polite phrase is not mistaken
    # for an entity in ordinary "对比 A 和 B" wording.
    if len([fragment for fragment in fragments if _clean_entity_fragment(fragment)]) < 2:
        fragments.extend(re.split(separator, prefix, flags=re.IGNORECASE))
    members: List[str] = []
    for fragment in fragments:
        cleaned = _clean_entity_fragment(fragment)
        if cleaned and len(cleaned) >= 2:
            members.append(cleaned)

    # A compact Latin model/product token is more reliable than a long tail
    # fragment such as "fable5 api pricing".
    tokens = [token for token in ENTITY_TOKEN_RE.findall(tail) if len(token) >= 2]
    model_like = [token for token in tokens if any(char.isdigit() for char in token)]
    if len(model_like) >= 2:
        return _dedupe_strings(model_like, limit=8)
    return _dedupe_strings(members, limit=8)


@dataclass
class QueryAnalysis:
    """Shared, serializable interpretation of a user's retrieval constraints."""

    query: str
    intent_shape: str = "information_request"
    entities: List[str] = field(default_factory=list)
    comparison_members: List[str] = field(default_factory=list)
    ambiguities: List[str] = field(default_factory=list)
    critical_ambiguity: bool = False
    claim_classes: List[str] = field(default_factory=list)
    constraints: Dict[str, Any] = field(default_factory=dict)
    freshness: Optional[str] = None
    time_scope: Dict[str, Any] = field(default_factory=dict)
    search_allowed: bool = True
    search_requested: bool = False
    requested_sources: List[str] = field(default_factory=list)
    domain_hint: Optional[str] = None
    requires_evidence: bool = False
    analysis_source: str = "deterministic"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent_shape": self.intent_shape,
            "entities": _dedupe_strings(self.entities, limit=8),
            "comparison_members": _dedupe_strings(self.comparison_members, limit=8),
            "ambiguities": _dedupe_strings(self.ambiguities, limit=8),
            "critical_ambiguity": bool(self.critical_ambiguity),
            "claim_classes": _dedupe_strings(self.claim_classes, limit=8),
            "constraints": _safe_mapping(self.constraints),
            "freshness": self.freshness,
            "time_scope": _safe_mapping(self.time_scope),
            "search_allowed": bool(self.search_allowed),
            "search_requested": bool(self.search_requested),
            "requested_sources": _dedupe_strings(self.requested_sources, limit=10),
            "domain_hint": _bounded_text(self.domain_hint, 80) if self.domain_hint else None,
            "requires_evidence": bool(self.requires_evidence),
            "analysis_source": self.analysis_source,
        }


def analyze_query(
    query: str,
    *,
    allow_search: bool,
    requested_sources: Optional[Sequence[str]] = None,
    domain_hint: Optional[str] = None,
    time_constraint: Optional[TimeConstraint] = None,
    requires_evidence: Optional[bool] = None,
) -> QueryAnalysis:
    """Build a conservative deterministic analysis before any retrieval work."""
    raw_query = str(query or "").strip()
    lowered = raw_query.casefold()
    time_constraint = time_constraint or parse_time_constraint(raw_query)
    comparison_members = _extract_comparison_members(raw_query)
    has_comparison = _contains_any(raw_query, COMPARISON_CUES)
    claim_classes: List[str] = []
    if has_comparison:
        claim_classes.append("comparison")
    if _contains_any(raw_query, PRICE_CUES):
        claim_classes.extend(("numeric", "pricing"))
    if _contains_any(raw_query, CURRENT_CUES):
        claim_classes.append("current")
    if _contains_any(raw_query, COMPLIANCE_CUES):
        claim_classes.append("compliance")
    explicit_temporal_match = next(
        (match for pattern in EXPLICIT_TEMPORAL_PATTERNS if (match := re.search(pattern, raw_query, re.IGNORECASE))),
        None,
    )
    historical_coverage_match = next(
        (
            match
            for pattern in HISTORICAL_COVERAGE_PATTERNS
            if (match := re.search(pattern, raw_query, re.IGNORECASE))
        ),
        None,
    )
    if time_constraint.days or explicit_temporal_match:
        claim_classes.append("temporal")
    if historical_coverage_match:
        claim_classes.append("historical")

    entities = list(comparison_members)
    model_tokens = [
        token
        for token in ENTITY_TOKEN_RE.findall(raw_query)
        if len(token) >= 2 and any(char.isdigit() for char in token)
    ]
    # Brand candidates let the official-domain resolver bind to entities even
    # when the query is not a comparison and carries no version token. The
    # resolver verifies each candidate, so recall is preferred over precision.
    brand_candidates = _extract_brand_candidates(raw_query)
    entities = _dedupe_strings(entities + model_tokens + brand_candidates, limit=8)

    ambiguities: List[str] = []
    critical = False
    if has_comparison and len(comparison_members) < 2:
        ambiguities.append("comparison_members_unresolved")
        critical = True
    has_pronoun = any(
        (cue in lowered if any("\u4e00" <= char <= "\u9fff" for char in cue)
         else bool(re.search(rf"(?<!\w){re.escape(cue)}(?!\w)", lowered)))
        for cue in AMBIGUOUS_REFERENCE_CUES
    )
    if has_pronoun and (not entities or len(comparison_members) < 2):
        ambiguities.append("unresolved_entity_reference")
        critical = True

    explicit_time = bool(time_constraint.days) or bool(explicit_temporal_match)
    constraints = {
        "authority_required": any(kind in claim_classes for kind in ("numeric", "current", "compliance")),
        "comparison_required": has_comparison,
        "temporal_required": explicit_time,
        "historical_coverage_required": bool(historical_coverage_match),
        "freshness_required": "current" in claim_classes,
        "ambiguity_blocking": critical,
    }
    inferred_evidence = bool(
        allow_search
        and (
            has_comparison
            or explicit_time
            or any(kind in claim_classes for kind in ("numeric", "current", "compliance"))
        )
    )
    if requires_evidence is not None:
        inferred_evidence = bool(requires_evidence and allow_search)

    return QueryAnalysis(
        query=raw_query,
        intent_shape="comparison" if has_comparison else "information_request",
        entities=entities,
        comparison_members=comparison_members,
        ambiguities=ambiguities,
        critical_ambiguity=critical,
        claim_classes=_dedupe_strings(claim_classes, limit=8),
        constraints=constraints,
        freshness=time_constraint.freshness if time_constraint.days else ("current" if "current" in claim_classes else None),
        time_scope={
            "days": time_constraint.days,
            "expression": (
                _bounded_text(time_constraint.time_expression, 80)
                if time_constraint.time_expression
                else _bounded_text(explicit_temporal_match.group(0), 80)
                if explicit_temporal_match
                else None
            ),
            "date_restrict": time_constraint.google_date_restrict if time_constraint.days else None,
        },
        search_allowed=bool(allow_search),
        search_requested=bool(allow_search),
        requested_sources=_dedupe_strings(requested_sources or [], limit=10),
        domain_hint=_bounded_text(domain_hint, 80) if domain_hint and str(domain_hint).casefold() != "general" else None,
        requires_evidence=inferred_evidence,
    )


def merge_optional_analysis(
    analysis: QueryAnalysis,
    candidate: Optional[Mapping[str, Any]],
) -> QueryAnalysis:
    """Merge optional LLM suggestions without relaxing deterministic safeguards."""
    if not isinstance(candidate, Mapping):
        return analysis
    extra_entities = candidate.get("entities") or []
    if isinstance(extra_entities, str):
        extra_entities = [extra_entities]
    extra_claims = candidate.get("claim_classes") or []
    if isinstance(extra_claims, str):
        extra_claims = [extra_claims]
    analysis.entities = _dedupe_strings(list(analysis.entities) + list(extra_entities), limit=8)
    analysis.claim_classes = _dedupe_strings(list(analysis.claim_classes) + list(extra_claims), limit=8)
    analysis.analysis_source = "deterministic+validated_llm"
    # The optional model may identify additional uncertainty, but never clears a
    # deterministic critical ambiguity or re-enables externally disabled search.
    extra_ambiguities = candidate.get("ambiguities") or []
    if isinstance(extra_ambiguities, str):
        extra_ambiguities = [extra_ambiguities]
    analysis.ambiguities = _dedupe_strings(list(analysis.ambiguities) + list(extra_ambiguities), limit=8)
    if candidate.get("critical_ambiguity") is True:
        analysis.critical_ambiguity = True
        analysis.constraints["ambiguity_blocking"] = True
    return analysis


@dataclass(frozen=True)
class EvidencePolicy:
    name: str
    required: bool = True
    accepted_source_tiers: tuple[str, ...] = ()
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "required": self.required,
            "accepted_source_tiers": list(self.accepted_source_tiers),
            "details": _safe_mapping(self.details),
        }


class EvidencePolicyRegistry:
    """Small composable policy registry derived from analysis constraints."""

    def derive(self, analysis: QueryAnalysis) -> List[EvidencePolicy]:
        policies: List[EvidencePolicy] = []
        if analysis.constraints.get("authority_required"):
            policies.append(
                EvidencePolicy(
                    name="authority",
                    accepted_source_tiers=("official", "first_party", "authoritative"),
                    details={"claim_classes": list(analysis.claim_classes)},
                )
            )
        if analysis.constraints.get("comparison_required"):
            policies.append(
                EvidencePolicy(
                    name="comparison_coverage",
                    details={"members": list(analysis.comparison_members)},
                )
            )
        if analysis.constraints.get("historical_coverage_required"):
            policies.append(
                EvidencePolicy(
                    name="temporal_coverage",
                    details=dict(analysis.time_scope),
                )
            )
        if analysis.constraints.get("freshness_required"):
            policies.append(EvidencePolicy(name="freshness", details={"freshness": analysis.freshness}))
        if analysis.critical_ambiguity:
            policies.append(EvidencePolicy(name="ambiguity", details={"blocking": True}))
        return policies


class TerminationAction(str, Enum):
    """The only actions emitted by the shared termination critic."""

    RETURN = "return"
    CONTINUE = "continue"
    CLARIFY = "clarify"
    RETURN_INSUFFICIENT = "return_insufficient"
    EXHAUSTED = "exhausted"
    STAGNATED = "stagnated"
    UNRECOVERABLE = "unrecoverable"


@dataclass(frozen=True)
class CriticEvidenceState:
    """Bounded evidence facts consumed by the deterministic critic."""

    retained_count: int = 0
    available_count: int = 0
    authoritative_count: int = 0
    covered_entities: tuple[str, ...] = ()
    covered_official_entities: tuple[str, ...] = ()
    covered_constraints: tuple[str, ...] = ()


@dataclass(frozen=True)
class CriticBudgetState:
    """Execution ceilings used by every termination verdict."""

    iteration: int = 0
    max_iterations: Optional[int] = None

    @property
    def exhausted(self) -> bool:
        return self.max_iterations is not None and self.iteration >= self.max_iterations


@dataclass
class TerminationContext:
    """Normalized facts for one shared deterministic termination decision."""

    phase: str
    requires_evidence: bool = False
    final_proposed: bool = True
    answer: str = ""
    critical_ambiguities: List[str] = field(default_factory=list)
    policies: List[str] = field(default_factory=list)
    comparison_members: List[str] = field(default_factory=list)
    official_targets: List[str] = field(default_factory=list)
    requires_official_pricing: bool = False
    evidence: CriticEvidenceState = field(default_factory=CriticEvidenceState)
    constraints_met: List[str] = field(default_factory=list)
    constraints_missing: List[str] = field(default_factory=list)
    unsupported_details: List[str] = field(default_factory=list)
    empty_answer: bool = False
    search_error: bool = False
    acknowledged_insufficient: bool = False
    invalid_tool_request: bool = False
    invalid_final_response: bool = False
    new_evidence: bool = True
    fingerprint_streak: int = 0
    no_progress_streak: int = 0
    tool_error_streak: int = 0
    had_successful_observation: bool = False
    repeat_threshold: int = 2
    no_progress_threshold: int = 2
    tool_error_threshold: int = 2
    can_continue: bool = True
    budget: CriticBudgetState = field(default_factory=CriticBudgetState)
    budget_failure: Optional[str] = None
    judge_payload: Optional[Mapping[str, Any]] = None
    judge_error: Optional[str] = None


@dataclass
class TerminationDecision:
    """Explainable output from the shared critic and optional semantic judge."""

    action: TerminationAction
    reason: str
    success: bool = False
    should_continue: bool = False
    deterministic_pass: bool = False
    hard_stop: bool = False
    missing_constraints: List[str] = field(default_factory=list)
    failure_types: List[str] = field(default_factory=list)
    rule_hits: List[Dict[str, str]] = field(default_factory=list)
    constraints_met: List[str] = field(default_factory=list)
    recoverable: bool = False
    judge_used: bool = False
    judge_error: Optional[str] = None
    evidence_sufficiency: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "success": bool(self.success),
            "should_continue": bool(self.should_continue),
            "deterministic_pass": bool(self.deterministic_pass),
            "hard_stop": bool(self.hard_stop),
            "missing_constraints": _dedupe_strings(self.missing_constraints, limit=16),
            "failure_types": _dedupe_strings(self.failure_types, limit=16),
            "rule_hits": [_safe_mapping(item) for item in self.rule_hits[:16]],
            "constraints_met": _dedupe_strings(self.constraints_met, limit=16),
            "recoverable": bool(self.recoverable),
            "judge_used": bool(self.judge_used),
            "judge_error": _bounded_text(self.judge_error, 240) if self.judge_error else None,
            "evidence_sufficiency": self.evidence_sufficiency,
        }


def _critic_recovery_allowed(context: TerminationContext, missing: Sequence[str]) -> bool:
    return bool(missing) and context.can_continue and not context.budget.exhausted


def evaluate_termination(context: TerminationContext) -> TerminationDecision:
    """Apply deterministic evidence, progress, and budget rules exactly once.

    The optional judge may add a semantic gap or veto a deterministic pass. It
    can never clear a deterministic gap, override a hard stop, or extend a
    budget. This precedence is the M4 contract shared by every executor.
    """

    missing: List[str] = []
    failures: List[str] = []
    rules: List[Dict[str, str]] = []

    def add_gap(constraint: str, failure: str, rule: str, detail: str) -> None:
        missing.append(constraint)
        failures.append(failure)
        rules.append({"rule": rule, "detail": detail})

    if context.critical_ambiguities:
        for ambiguity in context.critical_ambiguities:
            missing.append(str(ambiguity))
        failures.append("critical_ambiguity")
        rules.append(
            {
                "rule": "critical_ambiguity",
                "detail": "A required entity or constraint is unresolved.",
            }
        )
        return TerminationDecision(
            action=TerminationAction.CLARIFY,
            reason="critical_ambiguity",
            hard_stop=True,
            missing_constraints=missing,
            failure_types=failures,
            rule_hits=rules,
            constraints_met=list(context.constraints_met),
            judge_error=context.judge_error,
            evidence_sufficiency="insufficient",
        )

    if context.search_error:
        add_gap("search_unavailable", "search_unavailable", "search_unavailable", "Search execution is unavailable.")
    if context.acknowledged_insufficient:
        add_gap(
            "acknowledged_insufficient_information",
            "acknowledged_insufficient_information",
            "acknowledged_insufficient_information",
            "The draft explicitly states that available evidence is insufficient.",
        )
    if context.empty_answer and context.final_proposed:
        add_gap("answer", "empty_answer", "empty_answer", "The proposed answer is empty.")

    evidence = context.evidence
    policies = set(context.policies)
    if context.requires_evidence and evidence.retained_count <= 0:
        if evidence.available_count > 0 and "authority" in policies:
            add_gap("authority", "authority_policy_not_met", "authority", "Evidence exists but does not meet the authority policy.")
        else:
            add_gap("no_evidence", "no_evidence", "no_evidence", "No policy-accepted evidence is available.")
    if "authority" in policies and evidence.retained_count > 0 and evidence.authoritative_count <= 0:
        add_gap("authority", "authority_policy_not_met", "authority", "No retained evidence meets the authority policy.")

    covered_entities = {value.casefold() for value in evidence.covered_entities}
    if "comparison_coverage" in policies:
        for member in context.comparison_members:
            if member.casefold() not in covered_entities:
                add_gap(
                    f"comparison:{member}",
                    "comparison_coverage_missing",
                    "comparison_coverage",
                    f"Evidence does not cover comparison member: {member}.",
                )
        if context.answer:
            for member in context.comparison_members:
                if not _comparison_member_mentioned(context.answer, member):
                    add_gap(
                        f"answer_comparison:{member}",
                        "answer_comparison_coverage_missing",
                        "answer_comparison_coverage",
                        f"Draft omits comparison member: {member}.",
                    )

    covered_official = {value.casefold() for value in evidence.covered_official_entities}
    for target in context.official_targets:
        if target.casefold() not in covered_official:
            add_gap(
                f"official:{target}",
                "target_official_pricing_coverage_missing"
                if context.requires_official_pricing
                else "target_official_coverage_missing",
                "target_official_coverage",
                f"Official{' pricing' if context.requires_official_pricing else ''} evidence is missing for: {target}.",
            )

    covered_constraints = set(evidence.covered_constraints)
    if "temporal_coverage" in policies and "temporal_coverage" not in covered_constraints:
        add_gap(
            "temporal_coverage",
            "temporal_coverage_missing",
            "temporal_coverage",
            "No retained evidence contains a temporal coverage signal.",
        )
    if "temporal_coverage" in policies and context.answer and not re.search(
        r"(?<!\d)20\d{2}(?!\d)", context.answer
    ):
        add_gap(
            "answer_temporal_coverage",
            "answer_temporal_coverage_missing",
            "answer_temporal_coverage",
            "Draft does not state a temporal coverage signal.",
        )

    for constraint in context.constraints_missing:
        constraint_failure = {
            "time_constraint": "missing_time_constraint",
            "comparison": "missing_comparison_coverage",
            "multi_hop_reasoning": "needs_multi_hop_reasoning",
        }.get(str(constraint), f"constraint_missing:{constraint}")
        add_gap(
            str(constraint),
            constraint_failure,
            "constraint_coverage",
            f"The deterministic checklist is missing: {constraint}.",
        )
    for detail in context.unsupported_details:
        add_gap(
            "unsupported_specific_detail",
            "unsupported_specific_detail",
            "unsupported_specific_detail",
            f"Draft detail is not present in evidence: {detail}.",
        )

    deterministic_missing = _dedupe_strings(missing, limit=16)
    deterministic_failures = _dedupe_strings(failures, limit=16)
    deterministic_pass = not deterministic_missing

    judge_used = isinstance(context.judge_payload, Mapping)
    if judge_used:
        judge_missing = context.judge_payload.get("missing_constraints") or []
        if isinstance(judge_missing, str):
            judge_missing = [judge_missing]
        judge_passes = context.judge_payload.get("passes")
        if judge_passes is False:
            semantic_missing = [str(value) for value in judge_missing if str(value).strip()]
            if not semantic_missing:
                semantic_missing = ["semantic_sufficiency"]
            missing.extend(semantic_missing)
            failures.append("semantic_sufficiency_missing")
            rules.append(
                {
                    "rule": "semantic_judge",
                    "detail": _bounded_text(
                        context.judge_payload.get("reason") or "The semantic judge rejected the candidate answer.",
                        240,
                    ),
                }
            )
        # A positive judge verdict is deliberately advisory: deterministic
        # gaps remain intact and cannot be removed by model output.

    missing = _dedupe_strings(missing, limit=16)
    failures = _dedupe_strings(failures, limit=16)
    structural_missing = [
        value for value in missing if not value.startswith("answer_")
    ]
    if evidence.retained_count <= 0:
        evidence_sufficiency = "insufficient"
    elif structural_missing:
        evidence_sufficiency = "partial"
    else:
        evidence_sufficiency = "sufficient"

    hard_unrecoverable = context.search_error or context.acknowledged_insufficient
    if (
        context.tool_error_streak >= max(1, context.tool_error_threshold)
        and not context.had_successful_observation
    ):
        hard_unrecoverable = True
        failures = _dedupe_strings(failures + ["tool_errors_unrecoverable"], limit=16)
        rules.append({"rule": "tool_error_budget", "detail": "The tool error threshold was reached without a successful observation."})
    if hard_unrecoverable:
        return TerminationDecision(
            action=TerminationAction.UNRECOVERABLE,
            reason="unrecoverable",
            hard_stop=True,
            deterministic_pass=deterministic_pass,
            missing_constraints=missing,
            failure_types=failures,
            rule_hits=rules,
            constraints_met=list(context.constraints_met),
            judge_used=judge_used,
            judge_error=context.judge_error,
            evidence_sufficiency=evidence_sufficiency,
        )

    if context.budget_failure:
        failure = _bounded_text(context.budget_failure, 120)
        return TerminationDecision(
            action=(
                TerminationAction.EXHAUSTED
                if context.phase == "loop"
                else TerminationAction.RETURN_INSUFFICIENT
            ),
            reason="budget_exhausted",
            hard_stop=True,
            deterministic_pass=deterministic_pass,
            missing_constraints=missing,
            failure_types=_dedupe_strings(failures + [failure], limit=16),
            rule_hits=rules
            + [{"rule": "execution_budget", "detail": f"Execution stopped because {failure}."}],
            constraints_met=list(context.constraints_met),
            judge_used=judge_used,
            judge_error=context.judge_error,
            evidence_sufficiency=evidence_sufficiency,
        )

    if context.invalid_tool_request or context.invalid_final_response:
        failure = "invalid_tool_request" if context.invalid_tool_request else "process_narration"
        if context.budget.exhausted:
            return TerminationDecision(
                action=TerminationAction.EXHAUSTED,
                reason="exhausted",
                hard_stop=True,
                missing_constraints=missing,
                failure_types=_dedupe_strings(failures + [failure, "iteration_budget_exhausted"], limit=16),
                rule_hits=rules + [{"rule": failure, "detail": "The invalid model action consumed the remaining iteration budget."}],
                constraints_met=list(context.constraints_met),
                judge_used=judge_used,
                judge_error=context.judge_error,
                evidence_sufficiency=evidence_sufficiency,
            )
        return TerminationDecision(
            action=TerminationAction.CONTINUE,
            reason=failure,
            should_continue=True,
            missing_constraints=missing,
            failure_types=_dedupe_strings(failures + [failure], limit=16),
            rule_hits=rules,
            constraints_met=list(context.constraints_met),
            judge_used=judge_used,
            judge_error=context.judge_error,
            evidence_sufficiency=evidence_sufficiency,
        )

    if not context.final_proposed:
        stagnated = (
            context.fingerprint_streak >= max(1, context.repeat_threshold)
            or context.no_progress_streak >= max(1, context.no_progress_threshold)
        )
        if stagnated:
            return TerminationDecision(
                action=TerminationAction.STAGNATED,
                reason="stagnated",
                hard_stop=True,
                missing_constraints=missing,
                failure_types=_dedupe_strings(failures + ["no_progress"], limit=16),
                rule_hits=rules + [{"rule": "no_progress", "detail": "Repeated or non-incremental tool use reached its threshold."}],
                constraints_met=list(context.constraints_met),
                judge_used=judge_used,
                judge_error=context.judge_error,
                evidence_sufficiency=evidence_sufficiency,
            )
        if context.budget.exhausted:
            return TerminationDecision(
                action=TerminationAction.EXHAUSTED,
                reason="exhausted",
                hard_stop=True,
                missing_constraints=missing,
                failure_types=_dedupe_strings(failures + ["iteration_budget_exhausted"], limit=16),
                rule_hits=rules + [{"rule": "iteration_budget", "detail": "The configured iteration budget was exhausted."}],
                constraints_met=list(context.constraints_met),
                judge_used=judge_used,
                judge_error=context.judge_error,
                evidence_sufficiency=evidence_sufficiency,
            )
        return TerminationDecision(
            action=TerminationAction.CONTINUE,
            reason="continue",
            should_continue=True,
            deterministic_pass=deterministic_pass,
            missing_constraints=missing,
            failure_types=failures,
            rule_hits=rules,
            constraints_met=list(context.constraints_met),
            judge_used=judge_used,
            judge_error=context.judge_error,
            evidence_sufficiency=evidence_sufficiency,
        )

    if not missing:
        return TerminationDecision(
            action=TerminationAction.RETURN,
            reason="constraints_satisfied",
            success=True,
            deterministic_pass=True,
            rule_hits=[
                {
                    "rule": "constraints_satisfied",
                    "detail": "The deterministic evidence and constraint checklist passed.",
                }
            ],
            constraints_met=list(context.constraints_met),
            judge_used=judge_used,
            judge_error=context.judge_error,
            evidence_sufficiency=evidence_sufficiency,
        )

    if context.budget.exhausted:
        return TerminationDecision(
            action=TerminationAction.EXHAUSTED,
            reason="exhausted",
            hard_stop=True,
            deterministic_pass=deterministic_pass,
            missing_constraints=missing,
            failure_types=_dedupe_strings(failures + ["iteration_budget_exhausted"], limit=16),
            rule_hits=rules + [{"rule": "iteration_budget", "detail": "The configured iteration budget was exhausted."}],
            constraints_met=list(context.constraints_met),
            judge_used=judge_used,
            judge_error=context.judge_error,
            evidence_sufficiency=evidence_sufficiency,
        )

    recoverable = _critic_recovery_allowed(context, missing)
    if recoverable:
        return TerminationDecision(
            action=TerminationAction.CONTINUE,
            reason="recoverable_gap",
            should_continue=True,
            deterministic_pass=deterministic_pass,
            missing_constraints=missing,
            failure_types=failures,
            rule_hits=rules,
            constraints_met=list(context.constraints_met),
            recoverable=True,
            judge_used=judge_used,
            judge_error=context.judge_error,
            evidence_sufficiency=evidence_sufficiency,
        )

    return TerminationDecision(
        action=TerminationAction.RETURN_INSUFFICIENT,
        reason="evidence_insufficient",
        hard_stop=True,
        deterministic_pass=deterministic_pass,
        missing_constraints=missing,
        failure_types=failures,
        rule_hits=rules,
        constraints_met=list(context.constraints_met),
        judge_used=judge_used,
        judge_error=context.judge_error,
        evidence_sufficiency=evidence_sufficiency,
    )


@dataclass
class LedgerEntry:
    canonical_reference: str
    source_type: str
    source_id: str
    source_tier: str
    originating_calls: List[str] = field(default_factory=list)
    covered_entities: List[str] = field(default_factory=list)
    covered_constraints: List[str] = field(default_factory=list)
    decision: str = "retained"
    reason: str = "policy_satisfied"
    title: str = ""
    content_date: Optional[str] = None
    merged_count: int = 0
    raw_item: Any = field(default=None, repr=False, compare=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reference": self.canonical_reference,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_tier": self.source_tier,
            "originating_calls": _dedupe_strings(self.originating_calls, limit=8),
            "covered_entities": _dedupe_strings(self.covered_entities, limit=8),
            "covered_constraints": _dedupe_strings(self.covered_constraints, limit=8),
            "decision": self.decision,
            "reason": self.reason,
            "title": _bounded_text(self.title, 160),
            "content_date": self.content_date,
            "merged_count": self.merged_count,
        }


def _item_value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


class EvidenceLedger:
    """Canonicalize evidence and keep retention decisions separate from ranking."""

    def __init__(
        self,
        analysis: QueryAnalysis,
        *,
        policies: Optional[Sequence[EvidencePolicy]] = None,
        result_budget: int = 8,
    ) -> None:
        self.analysis = analysis
        self.policies = list(
            policies
            if policies is not None
            else EvidencePolicyRegistry().derive(analysis)
        )
        self.result_budget = max(1, int(result_budget))
        self.entries: List[LedgerEntry] = []
        self._index: Dict[str, LedgerEntry] = {}

    def policy_names(self) -> List[str]:
        return [policy.name for policy in self.policies]

    def _source_tier(self, item: Any) -> str:
        metadata = _item_value(item, "metadata", {}) or {}
        if isinstance(metadata, Mapping):
            tier = str(metadata.get("source_tier") or "").strip().casefold()
            if tier:
                return tier
        source_type = str(_item_value(item, "source_type", "")).casefold()
        if source_type == "domain":
            return "authoritative"
        if source_type == "local":
            return "local"
        return "unknown"

    def _coverage(self, item: Any) -> tuple[List[str], List[str]]:
        text = " ".join(
            str(_item_value(item, key, "") or "")
            for key in ("title", "content", "snippet", "reference")
        ).casefold()
        entities = [
            member
            for member in self.analysis.comparison_members
            if _comparison_member_mentioned(text, member)
        ]
        metadata = _item_value(item, "metadata", {}) or {}
        if isinstance(metadata, Mapping):
            official_target = str(metadata.get("official_target") or "").strip()
            if official_target and any(
                official_target.casefold() == member.casefold()
                for member in self.analysis.comparison_members
            ):
                entities.append(official_target)
        constraints: List[str] = []
        if entities:
            constraints.append("comparison_coverage")
        if _contains_any(text, PRICING_EVIDENCE_CUES):
            constraints.append("pricing_coverage")
        if re.search(r"(?<!\d)20\d{2}(?!\d)", text):
            constraints.append("temporal_coverage")
        return entities, constraints

    @staticmethod
    def _write_canonical_reference(item: Any, reference: str) -> None:
        """Expose a stable identity on normalized items without changing their API."""
        if isinstance(item, Mapping):
            metadata = item.get("metadata")
            if isinstance(metadata, dict):
                metadata.setdefault("canonical_reference", reference)
            return
        metadata = getattr(item, "metadata", None)
        if isinstance(metadata, dict):
            metadata.setdefault("canonical_reference", reference)

    def _decision(self, tier: str, covered_entities: Sequence[str]) -> tuple[str, str]:
        policies = self.policy_names()
        authority = next(
            (policy for policy in self.policies if policy.name == "authority"),
            None,
        )
        if authority and tier not in authority.accepted_source_tiers:
            return "limited", "authority_policy_not_met"
        if "comparison_coverage" in policies and not covered_entities:
            return "limited", "comparison_member_not_identified"
        return "retained", "policy_satisfied"

    def ingest(self, items: Iterable[Any], *, default_call_id: Optional[str] = None) -> None:
        for item in items:
            reference = canonical_reference(_item_value(item, "reference", ""))
            self._write_canonical_reference(item, reference)
            source_type = str(_item_value(item, "source_type", "unknown") or "unknown")
            source_id = str(_item_value(item, "source_id", "unknown") or "unknown")
            key = reference or f"{source_type}:{source_id}:{_bounded_text(_item_value(item, 'title', ''), 120).casefold()}"
            metadata = _item_value(item, "metadata", {}) or {}
            call_id = default_call_id
            if isinstance(metadata, Mapping):
                call_id = str(
                    metadata.get("originating_tool_call")
                    or call_id
                    or ""
                ) or None
            covered_entities, covered_constraints = self._coverage(item)
            tier = self._source_tier(item)
            if key in self._index:
                existing = self._index[key]
                existing.originating_calls = _dedupe_strings(
                    existing.originating_calls + ([call_id] if call_id else []),
                    limit=8,
                )
                existing.covered_entities = _dedupe_strings(existing.covered_entities + covered_entities, limit=8)
                existing.covered_constraints = _dedupe_strings(existing.covered_constraints + covered_constraints, limit=8)
                existing.merged_count += 1
                if existing.decision != "retained" and tier in {"official", "first_party", "authoritative"}:
                    existing.decision, existing.reason, existing.source_tier = "retained", "policy_satisfied", tier
                continue
            decision, reason = self._decision(tier, covered_entities)
            entry = LedgerEntry(
                canonical_reference=reference,
                source_type=source_type,
                source_id=source_id,
                source_tier=tier,
                originating_calls=[call_id] if call_id else [],
                covered_entities=covered_entities,
                covered_constraints=covered_constraints,
                decision=decision,
                reason=reason,
                title=str(_item_value(item, "title", "") or ""),
                content_date=(str(metadata.get("content_date")) if isinstance(metadata, Mapping) and metadata.get("content_date") else None),
                raw_item=item,
            )
            self._index[key] = entry
            self.entries.append(entry)

    def apply_limits(self, *, max_items: Optional[int] = None, max_references: Optional[int] = None) -> None:
        item_cap = max(1, int(max_items or self.result_budget))
        reference_cap = max(1, int(max_references or item_cap))
        retained = 0
        references = set()
        # Policy-accepted evidence wins a final cap over merely limited
        # discovery context, while preserving source order inside each class.
        candidates = [entry for entry in self.entries if entry.decision == "retained"]
        candidates.extend(entry for entry in self.entries if entry.decision == "limited")
        for entry in candidates:
            reference_key = entry.canonical_reference or f"{entry.source_type}:{entry.source_id}"
            if retained >= item_cap or (reference_key not in references and len(references) >= reference_cap):
                entry.decision = "rejected"
                entry.reason = "final_evidence_limit"
                continue
            retained += 1
            references.add(reference_key)

    def retained_items(self) -> List[Any]:
        """Return policy-accepted evidence for ordinary answer generation."""
        return [entry.raw_item for entry in self.entries if entry.decision == "retained" and entry.raw_item is not None]

    def limited_items(self) -> List[Any]:
        """Return constrained evidence for an explicitly qualified answer."""
        return [entry.raw_item for entry in self.entries if entry.decision == "limited" and entry.raw_item is not None]

    def coverage_summary(self) -> Dict[str, Any]:
        entries = list(self.entries)
        retained_entries = [entry for entry in entries if entry.decision == "retained"]
        covered_members = _dedupe_strings(
            (member for entry in retained_entries for member in entry.covered_entities),
            limit=12,
        )
        authoritative = sum(
            1
            for entry in retained_entries
            if entry.source_tier in {"official", "first_party", "authoritative"}
        )
        return {
            "entries": len(entries),
            "retained": sum(1 for entry in entries if entry.decision == "retained"),
            "limited": sum(1 for entry in entries if entry.decision == "limited"),
            "rejected": sum(1 for entry in entries if entry.decision == "rejected"),
            "merged": sum(entry.merged_count for entry in entries),
            "comparison_members_covered": covered_members,
            "authoritative_entries": authoritative,
            "decisions": [entry.to_dict() for entry in entries[:24]],
        }

    def to_dict(self) -> Dict[str, Any]:
        return self.coverage_summary()


class QueryExecutionTrace:
    """Append-only bounded facts for actual tool execution and evidence use."""

    def __init__(
        self,
        *,
        configured: Optional[Sequence[str]] = None,
        requested: Optional[Sequence[str]] = None,
        eligible: Optional[Sequence[str]] = None,
    ) -> None:
        self.configured = _dedupe_strings(configured or [], limit=16)
        self.requested = _dedupe_strings(requested or [], limit=16)
        self.eligible = _dedupe_strings(eligible or [], limit=16)
        self.events: List[Dict[str, Any]] = []
        self.executed: List[str] = []

    def record_analysis(self, analysis: QueryAnalysis) -> None:
        self.events.append(
            {
                "step_id": "analysis",
                "kind": "analysis",
                "status": "done",
                "analysis": analysis.to_dict(),
            }
        )

    def record_tool_call(
        self,
        *,
        tool: str,
        status: str,
        iteration: int,
        position: int,
        query: Optional[str] = None,
        source_type: Optional[str] = None,
        source_tier: Optional[str] = None,
        item_count: int = 0,
        reason: Optional[str] = None,
    ) -> None:
        tool_name = _bounded_text(tool, 80) or "unknown"
        self.executed = _dedupe_strings(self.executed + [tool_name], limit=24)
        event: Dict[str, Any] = {
            "step_id": f"tool_{max(0, int(iteration))}_{max(0, int(position))}",
            "kind": "tool_call",
            "tool": tool_name,
            "status": _bounded_text(status, 40) or "done",
            "iteration": max(0, int(iteration)),
            "position": max(0, int(position)),
            "item_count": max(0, int(item_count)),
        }
        if query:
            event["query"] = _bounded_text(query, 240)
        if source_type:
            event["source_type"] = _bounded_text(source_type, 40)
        if source_tier:
            event["source_tier"] = _bounded_text(source_tier, 40)
        if reason:
            event["reason"] = _bounded_text(reason, 160)
        self.events.append(event)

    def record_termination(self, verdict: Mapping[str, Any]) -> None:
        self.events.append(
            {
                "step_id": "termination",
                "kind": "termination",
                "status": _bounded_text(
                    verdict.get("action") or verdict.get("status") or verdict.get("reason"),
                    40,
                ),
                "verdict": _safe_mapping(verdict),
            }
        )

    def record_ledger(self, ledger: EvidenceLedger) -> None:
        summary = ledger.coverage_summary()
        self.events.append(
            {
                "step_id": "evidence_ledger",
                "kind": "evidence_ledger",
                "status": "done",
                "entries": summary["entries"],
                "retained": summary["retained"],
                "limited": summary["limited"],
                "rejected": summary["rejected"],
                "decisions": summary["decisions"],
            }
        )

    def to_dict(self, *, max_events: int = 32) -> Dict[str, Any]:
        events = [_safe_mapping(event) for event in self.events[:max_events]]
        return {
            "configured": list(self.configured),
            "requested": list(self.requested),
            "eligible": list(self.eligible),
            "executed": list(self.executed),
            "events": events,
            "truncated": len(self.events) > max_events,
        }
