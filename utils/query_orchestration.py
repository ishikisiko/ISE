"""Deterministic contracts for planning, executing, and verifying evidence work.

The module deliberately has no dependency on LangChain or a concrete search
provider.  That keeps query semantics, execution budgets, and audit-safe
provenance usable by the CLI, Flask, RAG, and recovery paths alike.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence
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


class PlanStepKind(str, Enum):
    """Bounded execution modes understood by the plan controller."""

    DIRECT_ANSWER = "direct_answer"
    LOCAL_RETRIEVAL = "local_retrieval"
    DOMAIN_API = "domain_api"
    WEB_SEARCH = "web_search"
    TEMPORAL_RECOVERY = "temporal_recovery"
    QUERY_REFORMULATION = "query_reformulation"
    OFFICIAL_DOMAIN_RECOVERY = "official_domain_recovery"
    DIRECT_REFERENCE = "direct_reference"
    CLARIFICATION = "clarification"


class VerificationStatus(str, Enum):
    COMPLETE = "complete"
    RECOVERABLE_GAP = "recoverable_gap"
    CLARIFICATION_REQUIRED = "clarification_required"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"


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


def _safe_mapping(value: Any, *, depth: int = 0) -> Any:
    """Project arbitrary provider metadata into a compact audit-safe value."""
    if depth > 3:
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
    if time_constraint.days or explicit_temporal_match:
        claim_classes.append("temporal")

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
        if analysis.constraints.get("temporal_required"):
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


@dataclass
class QueryPlanStep:
    step_id: str
    kind: PlanStepKind
    purpose: str
    query: Optional[str] = None
    source_types: List[str] = field(default_factory=list)
    allowed_providers: List[str] = field(default_factory=list)
    max_results: int = 0
    recovery_only: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.step_id,
            "kind": self.kind.value,
            "purpose": _bounded_text(self.purpose, 160),
            "query": _bounded_text(self.query, 240) if self.query else None,
            "source_types": _dedupe_strings(self.source_types, limit=6),
            "allowed_providers": _dedupe_strings(self.allowed_providers, limit=10),
            "max_results": int(self.max_results),
            "recovery_only": bool(self.recovery_only),
            "metadata": _safe_mapping(self.metadata),
        }


@dataclass
class QueryPlan:
    analysis: QueryAnalysis
    policies: List[EvidencePolicy] = field(default_factory=list)
    steps: List[QueryPlanStep] = field(default_factory=list)
    query_budget: int = 3
    result_budget: int = 8
    time_budget_ms: int = 20000
    recovery_budget: int = 1
    clarification_required: bool = False

    def policy_names(self) -> List[str]:
        return [policy.name for policy in self.policies]

    def step_for_kind(self, kind: PlanStepKind, *, include_recovery: bool = False) -> Optional[QueryPlanStep]:
        for step in self.steps:
            if step.kind == kind and (include_recovery or not step.recovery_only):
                return step
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policies": [policy.to_dict() for policy in self.policies],
            "steps": [step.to_dict() for step in self.steps],
            "budgets": {
                "query": self.query_budget,
                "result": self.result_budget,
                "time_ms": self.time_budget_ms,
                "recovery": self.recovery_budget,
            },
            "clarification_required": self.clarification_required,
        }


def _query_subject_tokens(query: str) -> List[str]:
    """Extract brand/subject tokens from a query when no entities were detected.

    Keeps latin product tokens (e.g. "OpenAI", "GLM") and meaningful CJK runs
    after stripping intent cues, so the fallback search query does not collapse
    to bare cues like "价格 pricing" and drop the subject entirely.
    """
    text = ENTITY_SUFFIX_RE.sub("", str(query or "")).strip()
    latin_tokens = [
        token
        for token in ENTITY_TOKEN_RE.findall(text)
        if len(token) >= 2 and token.casefold() not in _QUERY_SUBJECT_STOPWORDS
    ]
    latin_tokens = _dedupe_strings(latin_tokens, limit=5)
    if latin_tokens:
        return latin_tokens
    cue_set = set(PRICE_CUES) | set(CURRENT_CUES) | set(COMPLIANCE_CUES) | set(COMPARISON_CUES)
    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    kept = [run for run in cjk_runs if run not in cue_set]
    return _dedupe_strings(kept, limit=5)


def deterministic_query_for_plan(analysis: QueryAnalysis) -> str:
    """Produce a safe fallback query without relying on a prompt template."""
    base = _dedupe_strings(analysis.entities, limit=5)
    if not base:
        # Entity extraction only catches comparison members and version-digit
        # model tokens, so plain brand names (OpenAI, Anthropic, ...) are
        # absent. Preserve the subject of the original query instead of
        # searching with intent cues alone, which would drop the brand and
        # prevent official-domain evidence from ever surfacing.
        base = _query_subject_tokens(analysis.query)
    cues: List[str] = []
    claim_classes = {value.casefold() for value in analysis.claim_classes}
    if "pricing" in claim_classes or "numeric" in claim_classes:
        cues.extend(["价格", "pricing"])
    if "current" in claim_classes:
        cues.extend(["最新", "latest"])
    if "compliance" in claim_classes:
        cues.extend(["官方", "policy"])
    if "temporal" in claim_classes:
        cues.extend(["趋势", "historical"])
    parts = _dedupe_strings(base + cues, limit=8)
    return _bounded_text(" ".join(parts) or analysis.query, 500)


def reformulate_query_for_recovery(
    analysis: QueryAnalysis,
    missing_constraints: Sequence[str],
) -> str:
    """Build one deterministic recovery query from typed evidence gaps."""
    missing = [str(value).casefold() for value in missing_constraints]
    members = _dedupe_strings(analysis.comparison_members or analysis.entities, limit=8)
    base_query = deterministic_query_for_plan(analysis)
    claim_classes = {value.casefold() for value in analysis.claim_classes}
    intent_cues: List[str] = []
    if "pricing" in claim_classes or "numeric" in claim_classes:
        intent_cues.extend(["价格", "pricing"])
    if "current" in claim_classes:
        intent_cues.extend(["最新", "latest"])
    if "compliance" in claim_classes:
        intent_cues.extend(["官方", "policy"])

    if "authority" in missing:
        targets = members or _dedupe_strings(analysis.entities, limit=5)
        return _bounded_text(
            " ".join(targets + ["official", "pricing", "价格", "定价"])
            or f"{base_query} official",
            500,
        )

    comparison_missing = next(
        (value.split(":", 1)[1] for value in missing if value.startswith("comparison:") and ":" in value),
        None,
    )
    if comparison_missing:
        member = next(
            (candidate for candidate in members if candidate.casefold() == comparison_missing),
            comparison_missing,
        )
        return _bounded_text(
            " ".join(_dedupe_strings([member] + intent_cues, limit=6)),
            500,
        )

    if "no_evidence" in missing or "evidence" in missing:
        return base_query
    return base_query


def _official_recovery_targets(
    analysis: QueryAnalysis,
    official_domains: Optional[Mapping[str, Any]],
    *,
    limit: int = 4,
    resolved_domains: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Build target-specific official-domain recovery metadata without I/O.

    Host-pattern parsing delegates to the official-domain resolver so the plan
    layer and the tier layer can never disagree about an ownership boundary.
    The import is deferred because
    ``evidence.source_layer`` imports back from this module at module load.

    ``official_domains`` is the static configured mapping (pins/aliases).
    ``resolved_domains`` carries entities the dynamic resolver already ruled
    ``official`` this turn (keyed by entity label or alias), so recovery can
    proactively fetch official pages for entities that were never pinned.
    Static entries win on overlap; resolved entries fill the gaps and are
    marked ``origin: "resolved"`` for traceability.
    """
    from evidence.official_domain_resolver import HostPattern
    from evidence.source_tiering import normalize_entity_stem

    normalized: Dict[str, List[str]] = {}
    origins: Dict[str, str] = {}
    if isinstance(official_domains, Mapping):
        for alias, configured in official_domains.items():
            if str(alias).startswith("_"):
                continue
            stem = normalize_entity_stem(alias)
            values = [configured] if isinstance(configured, str) else configured
            if not stem or not isinstance(values, Iterable):
                continue
            domains = _dedupe_strings(
                (
                    pattern.serialize()
                    for value in values
                    if (pattern := HostPattern.parse(value)) is not None
                ),
                limit=12,
            )
            if domains:
                normalized[stem] = domains
                origins[stem] = "pin"
    if isinstance(resolved_domains, Mapping):
        for alias, configured in resolved_domains.items():
            if str(alias).startswith("_"):
                continue
            stem = normalize_entity_stem(alias)
            if not stem or stem in normalized:
                continue
            values = [configured] if isinstance(configured, str) else configured
            if not isinstance(values, Iterable):
                continue
            domains = _dedupe_strings(
                (
                    pattern.serialize()
                    for value in values
                    if (pattern := HostPattern.parse(value)) is not None
                ),
                limit=12,
            )
            if domains:
                normalized[stem] = domains
                origins[stem] = "resolved"

    targets: List[Dict[str, Any]] = []
    seen = set()
    for member in analysis.comparison_members:
        entity = str(member or "").strip()
        stem = normalize_entity_stem(entity)
        if not entity or not stem or stem in seen or stem not in normalized:
            continue
        seen.add(stem)
        domains = normalized[stem]
        site_filters = " ".join(
            f"site:{HostPattern.parse(domain).host}"
            for domain in domains[:3]
            if HostPattern.parse(domain) is not None
        )
        targets.append(
            {
                "entity": entity,
                "stem": stem,
                "domains": domains,
                "origin": origins.get(stem, "pin"),
                "query": _bounded_text(
                    f"{entity} API official pricing 官方价格 定价 {site_filters}",
                    500,
                ),
            }
        )
        if len(targets) >= max(1, int(limit)):
            break
    return targets


def build_query_plan(
    analysis: QueryAnalysis,
    *,
    has_local_docs: bool,
    needs_evidence: Optional[bool] = None,
    query_budget: int = 3,
    result_budget: int = 8,
    time_budget_ms: int = 20000,
    recovery_budget: int = 1,
    registry: Optional[EvidencePolicyRegistry] = None,
    official_domains: Optional[Mapping[str, Any]] = None,
    official_target_limit: int = 4,
    resolved_official_domains: Optional[Mapping[str, Any]] = None,
) -> QueryPlan:
    """Build the bounded execution plan from shared constraints."""
    if needs_evidence is not None:
        analysis.requires_evidence = bool(needs_evidence and analysis.search_allowed)
    policies = (registry or EvidencePolicyRegistry()).derive(analysis)
    plan = QueryPlan(
        analysis=analysis,
        policies=policies,
        query_budget=max(1, int(query_budget)),
        result_budget=max(1, int(result_budget)),
        time_budget_ms=max(1000, int(time_budget_ms)),
        recovery_budget=max(0, int(recovery_budget)),
        clarification_required=bool(analysis.critical_ambiguity and analysis.requires_evidence),
    )
    if plan.clarification_required:
        plan.steps.append(
            QueryPlanStep(
                step_id="clarification",
                kind=PlanStepKind.CLARIFICATION,
                purpose="Resolve a critical entity or constraint ambiguity before retrieval.",
            )
        )
        return plan

    if not analysis.requires_evidence:
        if has_local_docs and not analysis.search_allowed:
            plan.steps.append(
                QueryPlanStep(
                    step_id="local",
                    kind=PlanStepKind.LOCAL_RETRIEVAL,
                    purpose="Retrieve relevant local documents without external search.",
                    source_types=["local"],
                    max_results=plan.result_budget,
                )
            )
        else:
            plan.steps.append(
                QueryPlanStep(
                    step_id="direct",
                    kind=PlanStepKind.DIRECT_ANSWER,
                    purpose="Use the existing direct-answer path without evidence retrieval.",
                )
            )
        return plan

    if analysis.domain_hint:
        plan.steps.append(
            QueryPlanStep(
                step_id="domain",
                kind=PlanStepKind.DOMAIN_API,
                purpose="Retrieve applicable structured domain evidence.",
                source_types=["domain"],
                max_results=1,
                metadata={"domain": analysis.domain_hint},
            )
        )
    if has_local_docs:
        plan.steps.append(
            QueryPlanStep(
                step_id="local",
                kind=PlanStepKind.LOCAL_RETRIEVAL,
                purpose="Retrieve locally indexed evidence.",
                source_types=["local"],
                max_results=min(5, plan.result_budget),
            )
        )
    if analysis.search_allowed:
        plan.steps.append(
            QueryPlanStep(
                step_id="web",
                kind=PlanStepKind.WEB_SEARCH,
                purpose="Collect web evidence permitted by the selected policies.",
                query=deterministic_query_for_plan(analysis),
                source_types=["web"],
                allowed_providers=list(analysis.requested_sources),
                max_results=plan.result_budget,
                metadata={"freshness": analysis.freshness},
            )
        )
    if "temporal_coverage" in plan.policy_names() and analysis.search_allowed:
        plan.steps.append(
            QueryPlanStep(
                step_id="temporal_recovery",
                kind=PlanStepKind.TEMPORAL_RECOVERY,
                purpose="Fill an explicitly requested temporal evidence gap only when needed.",
                query=deterministic_query_for_plan(analysis),
                source_types=["web"],
                allowed_providers=list(analysis.requested_sources),
                max_results=min(4, plan.result_budget),
                recovery_only=True,
                metadata=dict(analysis.time_scope),
            )
        )
    official_targets = (
        _official_recovery_targets(
            analysis,
            official_domains,
            limit=official_target_limit,
            resolved_domains=resolved_official_domains,
        )
        if analysis.constraints.get("authority_required")
        and analysis.constraints.get("comparison_required")
        else []
    )
    if analysis.search_allowed and plan.recovery_budget > 0 and official_targets:
        web_step = plan.step_for_kind(PlanStepKind.WEB_SEARCH)
        if web_step is not None:
            web_step.max_results = max(1, plan.result_budget - len(official_targets))
        plan.steps.append(
            QueryPlanStep(
                step_id="official_domain_recovery",
                kind=PlanStepKind.OFFICIAL_DOMAIN_RECOVERY,
                purpose="Recover configured official-domain evidence for each comparison target.",
                source_types=["web"],
                allowed_providers=list(analysis.requested_sources),
                max_results=min(max(1, len(official_targets)), plan.result_budget),
                recovery_only=True,
                metadata={
                    "recovery_kind": "official_domain_recovery",
                    "targets": official_targets,
                },
            )
        )
    elif (
        analysis.search_allowed
        and plan.recovery_budget > 0
        and {"authority", "comparison_coverage"}.intersection(plan.policy_names())
    ):
        plan.steps.append(
            QueryPlanStep(
                step_id="query_reformulation",
                kind=PlanStepKind.QUERY_REFORMULATION,
                purpose="Re-search with a deterministic query for an authority or comparison coverage gap.",
                query=deterministic_query_for_plan(analysis),
                source_types=["web"],
                allowed_providers=list(analysis.requested_sources),
                max_results=min(4, plan.result_budget),
                recovery_only=True,
                metadata={"recovery_kind": "query_reformulation"},
            )
        )
    return plan


@dataclass
class VerificationOutcome:
    status: VerificationStatus
    missing_constraints: List[str] = field(default_factory=list)
    failure_types: List[str] = field(default_factory=list)
    recoverable: bool = False
    next_action: Optional[str] = None
    rule_hits: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "missing_constraints": _dedupe_strings(self.missing_constraints, limit=12),
            "failure_types": _dedupe_strings(self.failure_types, limit=12),
            "recoverable": bool(self.recoverable),
            "next_action": self.next_action,
            "rule_hits": [_safe_mapping(item) for item in self.rule_hits[:12]],
        }


@dataclass
class LedgerEntry:
    canonical_reference: str
    source_type: str
    source_id: str
    source_tier: str
    originating_steps: List[str] = field(default_factory=list)
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
            "originating_steps": _dedupe_strings(self.originating_steps, limit=8),
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

    def __init__(self, plan: QueryPlan) -> None:
        self.plan = plan
        self.entries: List[LedgerEntry] = []
        self._index: Dict[str, LedgerEntry] = {}

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
            for member in self.plan.analysis.comparison_members
            if _comparison_member_mentioned(text, member)
        ]
        metadata = _item_value(item, "metadata", {}) or {}
        if isinstance(metadata, Mapping):
            official_target = str(metadata.get("official_target") or "").strip()
            if official_target and any(
                official_target.casefold() == member.casefold()
                for member in self.plan.analysis.comparison_members
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
        policies = self.plan.policy_names()
        authority = next((policy for policy in self.plan.policies if policy.name == "authority"), None)
        if authority and tier not in authority.accepted_source_tiers:
            return "limited", "authority_policy_not_met"
        if "comparison_coverage" in policies and not covered_entities:
            return "limited", "comparison_member_not_identified"
        return "retained", "policy_satisfied"

    def ingest(self, items: Iterable[Any], *, default_step_id: Optional[str] = None) -> None:
        for item in items:
            reference = canonical_reference(_item_value(item, "reference", ""))
            self._write_canonical_reference(item, reference)
            source_type = str(_item_value(item, "source_type", "unknown") or "unknown")
            source_id = str(_item_value(item, "source_id", "unknown") or "unknown")
            key = reference or f"{source_type}:{source_id}:{_bounded_text(_item_value(item, 'title', ''), 120).casefold()}"
            metadata = _item_value(item, "metadata", {}) or {}
            step_id = default_step_id
            if isinstance(metadata, Mapping):
                step_id = str(metadata.get("originating_plan_step") or step_id or "") or None
            covered_entities, covered_constraints = self._coverage(item)
            tier = self._source_tier(item)
            if key in self._index:
                existing = self._index[key]
                existing.originating_steps = _dedupe_strings(existing.originating_steps + ([step_id] if step_id else []), limit=8)
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
                originating_steps=[step_id] if step_id else [],
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
        item_cap = max(1, int(max_items or self.plan.result_budget))
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


def verify_evidence_plan(
    plan: QueryPlan,
    ledger: EvidenceLedger,
    *,
    answer: Optional[str] = None,
) -> VerificationOutcome:
    """Evaluate evidence and, when available, the draft answer structurally."""
    if plan.clarification_required:
        return VerificationOutcome(
            status=VerificationStatus.CLARIFICATION_REQUIRED,
            missing_constraints=list(plan.analysis.ambiguities) or ["critical_ambiguity"],
            failure_types=["critical_ambiguity"],
            recoverable=False,
            next_action="clarify",
            rule_hits=[{"rule": "critical_ambiguity", "detail": "A required entity or constraint is unresolved."}],
        )

    # Limited and rejected entries are useful diagnostics, but cannot satisfy
    # a planned factual constraint. Limited entries can still distinguish a
    # source-authority gap from a total retrieval failure.
    entries = [entry for entry in ledger.entries if entry.decision == "retained"]
    available_entries = [entry for entry in ledger.entries if entry.decision != "rejected"]
    if not plan.analysis.requires_evidence:
        return VerificationOutcome(status=VerificationStatus.COMPLETE, next_action="return")

    reformulation_step = plan.step_for_kind(
        PlanStepKind.QUERY_REFORMULATION,
        include_recovery=True,
    )
    temporal_step = plan.step_for_kind(
        PlanStepKind.TEMPORAL_RECOVERY,
        include_recovery=True,
    )
    official_recovery_step = plan.step_for_kind(
        PlanStepKind.OFFICIAL_DOMAIN_RECOVERY,
        include_recovery=True,
    )

    def recovery_allowed_for(missing_constraints: Sequence[str]) -> bool:
        if plan.recovery_budget <= 0:
            return False
        typed_missing = {constraint for constraint in missing_constraints if not constraint.startswith("answer_")}
        if typed_missing.intersection({"no_evidence", "authority"}) or any(
            constraint.startswith("official:") for constraint in typed_missing
        ):
            return (
                official_recovery_step is not None
                or reformulation_step is not None
                or temporal_step is not None
            )
        if any(constraint.startswith("comparison:") for constraint in typed_missing):
            return official_recovery_step is not None or reformulation_step is not None
        if typed_missing.intersection({"temporal_coverage"}):
            return temporal_step is not None
        if "answer_temporal_coverage" in missing_constraints:
            return temporal_step is not None
        return False

    def insufficiency_failure(missing_constraints: Sequence[str]) -> List[str]:
        structural_gap = any(not constraint.startswith("answer_") for constraint in missing_constraints)
        if (
            structural_gap
            and plan.analysis.search_allowed
            and plan.recovery_budget <= 0
        ):
            return ["recovery_budget_exhausted"]
        return []

    if not entries:
        missing: List[str] = []
        failures: List[str] = []
        rule_hits: List[Dict[str, str]] = []
        authority = next((policy for policy in plan.policies if policy.name == "authority"), None)
        if available_entries and authority:
            missing.append("authority")
            failures.append("authority_policy_not_met")
            rule_hits.append(
                {
                    "rule": "authority",
                    "detail": "Evidence exists, but none meets the authority policy.",
                }
            )
        else:
            missing.append("no_evidence")
            failures.append("no_evidence")
            rule_hits.append(
                {"rule": "no_evidence", "detail": "No evidence was retained for the planned query."}
            )
        recoverable = recovery_allowed_for(missing)
        if not recoverable:
            failures.extend(insufficiency_failure(missing))
        return VerificationOutcome(
            status=VerificationStatus.RECOVERABLE_GAP if recoverable else VerificationStatus.EVIDENCE_INSUFFICIENT,
            missing_constraints=missing,
            failure_types=_dedupe_strings(failures, limit=12),
            recoverable=recoverable,
            next_action="recover" if recoverable else "return_insufficient",
            rule_hits=rule_hits,
        )

    missing: List[str] = []
    failures: List[str] = []
    rule_hits: List[Dict[str, str]] = []
    policy_names = plan.policy_names()
    if "comparison_coverage" in policy_names:
        covered = {member.casefold() for entry in entries for member in entry.covered_entities}
        missing_members = [member for member in plan.analysis.comparison_members if member.casefold() not in covered]
        if missing_members:
            missing.extend(f"comparison:{member}" for member in missing_members)
            failures.append("comparison_coverage_missing")
            rule_hits.append({"rule": "comparison_coverage", "detail": "Missing: " + ", ".join(missing_members[:4])})
        if answer:
            answer_missing = [
                member
                for member in plan.analysis.comparison_members
                if not _comparison_member_mentioned(answer, member)
            ]
            if answer_missing:
                missing.extend(f"answer_comparison:{member}" for member in answer_missing)
                failures.append("answer_comparison_coverage_missing")
                rule_hits.append(
                    {
                        "rule": "answer_comparison_coverage",
                        "detail": "Draft omits: " + ", ".join(answer_missing[:4]),
                    }
                )
    authority = next((policy for policy in plan.policies if policy.name == "authority"), None)
    if authority and not any(entry.source_tier in authority.accepted_source_tiers for entry in entries):
        missing.append("authority")
        failures.append("authority_policy_not_met")
        rule_hits.append({"rule": "authority", "detail": "No retained evidence meets the authority policy."})
    if official_recovery_step is not None:
        targets = official_recovery_step.metadata.get("targets") or []
        required_targets = [
            str(target.get("entity") or "").strip()
            for target in targets
            if isinstance(target, Mapping) and str(target.get("entity") or "").strip()
        ]
        requires_pricing = "pricing" in plan.analysis.claim_classes
        covered_official = {
            member.casefold()
            for entry in entries
            if entry.source_tier == "official"
            and (
                not requires_pricing
                or "pricing_coverage" in entry.covered_constraints
            )
            for member in entry.covered_entities
        }
        missing_targets = [
            target for target in required_targets if target.casefold() not in covered_official
        ]
        if missing_targets:
            missing.extend(f"official:{target}" for target in missing_targets)
            failures.append(
                "target_official_pricing_coverage_missing"
                if requires_pricing
                else "target_official_coverage_missing"
            )
            rule_hits.append(
                {
                    "rule": "target_official_coverage",
                    "detail": (
                        "Missing official pricing evidence: "
                        if requires_pricing
                        else "Missing official evidence: "
                    )
                    + ", ".join(missing_targets[:4]),
                }
            )
    if "temporal_coverage" in policy_names and not any("temporal_coverage" in entry.covered_constraints for entry in entries):
        missing.append("temporal_coverage")
        failures.append("temporal_coverage_missing")
        rule_hits.append({"rule": "temporal_coverage", "detail": "No retained evidence contains a temporal coverage signal."})
    if "temporal_coverage" in policy_names and answer and not re.search(r"(?<!\d)20\d{2}(?!\d)", answer):
        missing.append("answer_temporal_coverage")
        failures.append("answer_temporal_coverage_missing")
        rule_hits.append(
            {
                "rule": "answer_temporal_coverage",
                "detail": "Draft does not state a temporal coverage signal.",
            }
        )

    if not missing:
        return VerificationOutcome(status=VerificationStatus.COMPLETE, next_action="return")
    recovery_allowed = recovery_allowed_for(missing)
    if not recovery_allowed:
        failures.extend(insufficiency_failure(missing))
    return VerificationOutcome(
        status=VerificationStatus.RECOVERABLE_GAP if recovery_allowed else VerificationStatus.EVIDENCE_INSUFFICIENT,
        missing_constraints=missing,
        failure_types=failures,
        recoverable=recovery_allowed,
        next_action="recover" if recovery_allowed else "return_insufficient",
        rule_hits=rule_hits,
    )


@dataclass
class PlanStepResult:
    items: List[Any] = field(default_factory=list)
    payload: Any = None
    providers: List[str] = field(default_factory=list)
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    status: str = "done"
    reason: Optional[str] = None


class QueryExecutionTrace:
    """Append-only, bounded execution facts for response control and audit."""

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
        self._started: Dict[str, float] = {}

    def begin(self, step: QueryPlanStep) -> None:
        self._started[step.step_id] = time.perf_counter()
        self.events.append({"step_id": step.step_id, "kind": step.kind.value, "status": "active", "purpose": _bounded_text(step.purpose, 120)})

    def record_analysis(self, analysis: QueryAnalysis) -> None:
        self.events.append(
            {
                "step_id": "analysis",
                "kind": "analysis",
                "status": "done",
                "analysis": analysis.to_dict(),
            }
        )

    def record_plan(self, plan: QueryPlan) -> None:
        self.events.append(
            {
                "step_id": "plan",
                "kind": "plan",
                "status": "done",
                "step_count": len(plan.steps),
                "policies": plan.policy_names(),
                "clarification_required": plan.clarification_required,
            }
        )

    def finish(
        self,
        step: QueryPlanStep,
        *,
        status: str,
        providers: Optional[Sequence[str]] = None,
        attempts: Optional[Sequence[Mapping[str, Any]]] = None,
        item_count: int = 0,
        reason: Optional[str] = None,
    ) -> None:
        started = self._started.pop(step.step_id, None)
        providers = _dedupe_strings(providers or [], limit=12)
        self.executed = _dedupe_strings(self.executed + providers, limit=24)
        event: Dict[str, Any] = {
            "step_id": step.step_id,
            "kind": step.kind.value,
            "status": status,
            "providers": providers,
            "item_count": max(0, int(item_count)),
        }
        if started is not None:
            event["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        if reason:
            event["reason"] = _bounded_text(reason, 160)
        if attempts:
            event["attempts"] = [_safe_mapping(attempt) for attempt in list(attempts)[:12]]
        self.events.append(event)

    def skip(self, step: QueryPlanStep, reason: str) -> None:
        self._started.pop(step.step_id, None)
        self.events.append({"step_id": step.step_id, "kind": step.kind.value, "status": "skipped", "reason": _bounded_text(reason, 160)})

    def record_verification(self, outcome: VerificationOutcome) -> None:
        self.events.append({"step_id": "verification", "kind": "verification", "status": outcome.status.value, "outcome": outcome.to_dict()})

    def record_ledger(self, ledger: EvidenceLedger) -> None:
        """Append a compact retention projection after all evidence is fused."""
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

    def record_recovery(
        self,
        *,
        executor: str,
        status: str,
        reason: Optional[str] = None,
        query: Optional[str] = None,
    ) -> None:
        """Record a bounded fallback decision that is outside source retrieval."""
        event: Dict[str, Any] = {
            "step_id": "recovery",
            "kind": "recovery",
            "executor": _bounded_text(executor, 80),
            "status": _bounded_text(status, 40),
        }
        if reason:
            event["reason"] = _bounded_text(reason, 160)
        if query:
            event["query"] = _bounded_text(query, 240)
        self.events.append(event)

    def to_dict(self, *, max_events: int = 24) -> Dict[str, Any]:
        events = [_safe_mapping(event) for event in self.events[:max_events]]
        return {
            "configured": list(self.configured),
            "requested": list(self.requested),
            "eligible": list(self.eligible),
            "executed": list(self.executed),
            "events": events,
            "truncated": len(self.events) > max_events,
        }


class PlanController:
    """Execute only plan-authorized callbacks while enforcing shared budgets."""

    def __init__(self, plan: QueryPlan, trace: QueryExecutionTrace) -> None:
        self.plan = plan
        self.trace = trace
        # Planning and keyword generation precede plan execution. Start the
        # budget at the first authorized retrieval so a slow auxiliary LLM
        # call cannot consume the recovery window.
        self.started_at: Optional[float] = None
        self.queries_used = 0
        self.results_used = 0
        self.recoveries_used = 0

    def _counts_as_query(self, step: QueryPlanStep) -> bool:
        return step.kind in {
            PlanStepKind.DOMAIN_API,
            PlanStepKind.WEB_SEARCH,
            PlanStepKind.TEMPORAL_RECOVERY,
            PlanStepKind.QUERY_REFORMULATION,
            PlanStepKind.OFFICIAL_DOMAIN_RECOVERY,
            PlanStepKind.DIRECT_REFERENCE,
        }

    def can_run(self, step: QueryPlanStep) -> Optional[str]:
        """Expose a read-only budget check for orchestrator recovery loops."""
        return self._can_run(step)

    def _can_run(self, step: QueryPlanStep) -> Optional[str]:
        if self.plan.clarification_required and step.kind != PlanStepKind.CLARIFICATION:
            return "clarification_required"
        if (
            self.started_at is not None
            and (time.perf_counter() - self.started_at) * 1000 >= self.plan.time_budget_ms
        ):
            return "time_budget_exhausted"
        if self._counts_as_query(step) and self.queries_used >= self.plan.query_budget:
            return "query_budget_exhausted"
        if step.recovery_only and self.recoveries_used >= self.plan.recovery_budget:
            return "recovery_budget_exhausted"
        return None

    def run_step(self, step: QueryPlanStep, executor: Callable[[QueryPlanStep], PlanStepResult]) -> PlanStepResult:
        if self.started_at is None:
            self.started_at = time.perf_counter()
        blocked = self._can_run(step)
        if blocked:
            self.trace.skip(step, blocked)
            return PlanStepResult(status="skipped", reason=blocked)
        self.trace.begin(step)
        try:
            result = executor(step)
            if not isinstance(result, PlanStepResult):
                raise TypeError("Plan executors must return PlanStepResult.")
        except Exception as exc:  # noqa: BLE001 - execution boundaries must be traceable
            result = PlanStepResult(status="error", reason=str(exc))
        allowed = {provider.casefold() for provider in step.allowed_providers if provider}
        actual = [str(provider) for provider in result.providers if str(provider)]
        if allowed and actual:
            unexpected = [provider for provider in actual if provider.casefold() not in allowed]
            if unexpected:
                result.items = []
                result.status = "blocked"
                result.reason = "provider_not_authorized: " + ", ".join(unexpected[:4])
        if self._counts_as_query(step):
            self.queries_used += 1
        if step.recovery_only:
            self.recoveries_used += 1
        remaining = max(0, self.plan.result_budget - self.results_used)
        result.items = list(result.items or [])[:remaining]
        self.results_used += len(result.items)
        self.trace.finish(
            step,
            status=result.status,
            providers=result.providers,
            attempts=result.attempts,
            item_count=len(result.items),
            reason=result.reason,
        )
        return result
