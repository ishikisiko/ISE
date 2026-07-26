"""Deterministic parsing and arithmetic for token-pricing questions.

The search loop uses this module for two separate decisions: whether an
extracted pricing page contains every rate needed by the user's workload, and
whether the final total can be reproduced without trusting model arithmetic.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse


_PRICE_CUE_RE = re.compile(
    r"(?:价格|定价|费用|收费|成本|多少钱|price|pricing|cost|rate)",
    re.IGNORECASE,
)
_QUANTITY_ROLE_RE = re.compile(
    r"(?P<amount>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>k|m|b|千|万|百万|亿)?\s*"
    r"(?P<role>输入\s*缓存\s*命中|缓存\s*命中\s*输入|缓存\s*输入|"
    r"输出|输入|cached\s+input|cache(?:d)?(?:\s+hit)?(?:\s+input)?|output|input)",
    re.IGNORECASE,
)
_MODEL_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-._][A-Za-z0-9]+)+")
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9.])(?P<value>\d+(?:\.\d+)?)(?![A-Za-z0-9.])"
)

_UNIT_MULTIPLIERS = {
    "": Decimal(1),
    "k": Decimal(1_000),
    "千": Decimal(1_000),
    "万": Decimal(10_000),
    "m": Decimal(1_000_000),
    "百万": Decimal(1_000_000),
    "亿": Decimal(100_000_000),
    "b": Decimal(1_000_000_000),
}
_ROLE_LABELS = {
    "input": "输入",
    "output": "输出",
    "cached_input": "缓存命中输入",
}
_ROLE_PATTERNS = {
    "cached_input": (
        r"缓存\s*(?:命中\s*)?(?:输入)?",
        r"(?:cached\s+input|cache(?:d)?\s+hit)",
    ),
    "output": (r"输出", r"output"),
    "input": (r"(?<!缓存命中)(?<!缓存)输入", r"(?<!cached\s)input"),
}
def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    return "0" if text == "-0" else text


def _normalize_subject(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _detect_currency(text: str) -> Optional[str]:
    lowered = text.casefold()
    if any(marker in lowered for marker in ("$", "美元", "usd")):
        return "USD"
    if (
        any(marker in lowered for marker in ("¥", "￥", "人民币", "rmb", "cny"))
        or re.search(r"(?<!美)元", text)
    ):
        return "CNY"
    return None


def _role_name(value: str) -> str:
    normalized = re.sub(r"\s+", "", value.casefold())
    if "缓存" in normalized or "cache" in normalized:
        return "cached_input"
    if "输出" in normalized or "output" in normalized:
        return "output"
    return "input"


def _subject_from_query(query: str, entities: Optional[Sequence[str]]) -> str:
    quantity_spans = [match.span() for match in _QUANTITY_ROLE_RE.finditer(query)]
    candidates = list(entities or []) + _MODEL_TOKEN_RE.findall(query)
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text or not any(char.isdigit() for char in text):
            continue
        matches = list(re.finditer(re.escape(text), query, re.IGNORECASE))
        if matches and all(
            any(start <= match.start() < end for start, end in quantity_spans)
            for match in matches
        ):
            continue
        return text
    return ""


def parse_pricing_request(
    query: Any,
    *,
    entities: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Return a serializable workload-pricing requirement, or an empty dict."""
    text = str(query or "").strip()
    if not text or not _PRICE_CUE_RE.search(text):
        return {}

    quantities: Dict[str, Dict[str, str]] = {}
    for match in _QUANTITY_ROLE_RE.finditer(text):
        try:
            amount = Decimal(match.group("amount"))
        except InvalidOperation:
            continue
        unit = str(match.group("unit") or "").casefold()
        multiplier = _UNIT_MULTIPLIERS.get(unit)
        if multiplier is None:
            continue
        role = _role_name(match.group("role"))
        token_count = amount * multiplier
        quantities[role] = {
            "count": _decimal_text(token_count),
            "display": " ".join(match.group(0).split()),
        }

    if not quantities:
        return {}

    lowered = text.casefold()
    currency = _detect_currency(text)
    channel = None
    if any(marker in lowered for marker in ("z.ai", "国际", "海外", "global")):
        channel = "global"
    elif any(marker in lowered for marker in ("bigmodel", "智谱开放平台", "国内")):
        channel = "domestic"

    ordered_roles = [
        role for role in ("input", "output", "cached_input") if role in quantities
    ]
    return {
        "operation": "pricing_total",
        "subject": _subject_from_query(text, entities),
        "quantities": {role: quantities[role] for role in ordered_roles},
        "required_rates": ordered_roles,
        "currency": currency,
        "channel": channel,
    }


def _currency_from_text(text: str) -> Optional[str]:
    return _detect_currency(text)


def _per_tokens_from_text(text: str) -> Optional[str]:
    lowered = text.casefold()
    patterns = (
        (r"(?:每|/|per\s*)\s*(?:1\s*)?(?:m|million|百万)\s*(?:tokens?|tok)?", "1000000"),
        (r"[（(]\s*百万\s*tokens?\s*[)）]", "1000000"),
        (r"(?:每|/|per\s*)\s*(?:1\s*)?(?:k|thousand|千)\s*(?:tokens?|tok)?", "1000"),
        (r"[（(]\s*千\s*tokens?\s*[)）]", "1000"),
    )
    for pattern, value in patterns:
        if re.search(pattern, lowered, re.IGNORECASE):
            return value
    return None


def _extract_labeled_rate(text: str, role: str) -> Optional[str]:
    currency = r"(?:¥|￥|\$|人民币|美元|元|RMB|CNY|USD)?"
    suffix = r"(?:人民币|美元|元|RMB|CNY|USD)?"
    for label in _ROLE_PATTERNS[role]:
        label_first = re.compile(
            rf"(?:{label})\s*(?:单价|价格|price|rate)?\s*[:：=|]?\s*"
            rf"{currency}\s*(?P<value>\d+(?:\.\d+)?)\s*{suffix}",
            re.IGNORECASE,
        )
        match = label_first.search(text)
        if match:
            return _decimal_text(Decimal(match.group("value")))

        value_first = re.compile(
            rf"{currency}\s*(?P<value>\d+(?:\.\d+)?)\s*{suffix}\s*"
            rf"(?:/[^,，;；|\n]{{0,30}})?\s*(?:{label})",
            re.IGNORECASE,
        )
        match = value_first.search(text)
        if match:
            return _decimal_text(Decimal(match.group("value")))
    return None


def _target_table_row(text: str, subject: str) -> Optional[Tuple[str, str]]:
    subject_key = _normalize_subject(subject)
    if not subject_key:
        return None
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "|" not in line or subject_key not in _normalize_subject(line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4 or subject_key not in _normalize_subject(cells[0]):
            continue
        context = "\n".join(lines[max(0, index - 8) : index + 2])
        return line, context
    return None


def _rates_from_table_row(row: str, context: str) -> Dict[str, str]:
    cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
    numeric_cells: List[Optional[str]] = []
    for cell in cells:
        match = _NUMBER_RE.search(cell)
        numeric_cells.append(
            _decimal_text(Decimal(match.group("value"))) if match else None
        )

    header_rows = [
        line
        for line in context.splitlines()
        if "|" in line
        and any(
            marker in line.casefold()
            for marker in ("输入", "输出", "缓存", "input", "output", "cache")
        )
    ]
    for header_row in reversed(header_rows):
        headers = [
            re.sub(r"<[^>]+>", " ", cell).strip().casefold()
            for cell in header_row.strip().strip("|").split("|")
        ]
        if len(headers) != len(cells):
            continue
        mapped: Dict[str, str] = {}
        for index, header in enumerate(headers):
            if not numeric_cells[index]:
                continue
            compact = re.sub(r"\s+", " ", header)
            if "输出" in compact or "output" in compact:
                mapped["output"] = str(numeric_cells[index])
            elif (
                "缓存命中" in compact
                or "cached input" in compact
                or "cache hit" in compact
            ):
                mapped["cached_input"] = str(numeric_cells[index])
            elif (
                ("输入" in compact and "缓存" not in compact)
                or ("input" in compact and "cached" not in compact)
            ):
                mapped["input"] = str(numeric_cells[index])
        if mapped:
            return mapped

    # Common pricing tables use model, context, input, output, cache storage,
    # cache hit. We only accept that positional form when the nearby header
    # explicitly names all three required columns.
    header = context.casefold()
    if (
        len(cells) >= 6
        and all(marker in header for marker in ("输入", "输出", "缓存"))
        and numeric_cells[2]
        and numeric_cells[3]
        and numeric_cells[5]
    ):
        return {
            "input": str(numeric_cells[2]),
            "output": str(numeric_cells[3]),
            "cached_input": str(numeric_cells[5]),
        }

    rates: Dict[str, str] = {}
    for role in ("cached_input", "output", "input"):
        value = _extract_labeled_rate(row, role)
        if value is not None:
            rates[role] = value
    return rates


def extract_pricing_facts(
    content: Any,
    requirements: Mapping[str, Any],
) -> Dict[str, Any]:
    """Extract only a target model's rate tuple from one page body."""
    text = str(content or "")
    required = [str(role) for role in requirements.get("required_rates") or []]
    subject = str(requirements.get("subject") or "").strip()
    subject_key = _normalize_subject(subject)
    subject_present = not subject_key or subject_key in _normalize_subject(text)
    if not text.strip() or not subject_present:
        return {
            "subject": subject,
            "rates": {},
            "missing_rates": required,
            "currency": None,
            "per_tokens": None,
            "complete": False,
        }

    target_match = None
    if subject:
        parts = [re.escape(part) for part in re.findall(r"[A-Za-z0-9]+", subject)]
        if parts:
            target_match = re.search(r"[-._\s]*".join(parts), text, re.IGNORECASE)
    if target_match:
        start = max(0, target_match.start() - 900)
        end = min(len(text), target_match.end() + 1800)
        window = text[start:end]
    else:
        window = text[:2700]

    table = _target_table_row(text, subject)
    rates = _rates_from_table_row(*table) if table else {}
    if not rates:
        for role in ("cached_input", "output", "input"):
            value = _extract_labeled_rate(window, role)
            if value is not None:
                rates[role] = value

    currency = _currency_from_text((table[1] if table else "") + "\n" + window)
    per_tokens = _per_tokens_from_text((table[1] if table else "") + "\n" + window)
    requested_currency = requirements.get("currency")
    currency_matches = not requested_currency or currency == requested_currency
    missing = [role for role in required if role not in rates]
    complete = bool(required) and not missing and bool(currency) and bool(per_tokens) and currency_matches
    return {
        "subject": subject,
        "rates": {role: rates[role] for role in required if role in rates},
        "missing_rates": missing,
        "currency": currency,
        "per_tokens": per_tokens,
        "currency_matches": currency_matches,
        "complete": complete,
    }


def pricing_content_acceptance(
    content: Any,
    requirements: Mapping[str, Any],
) -> Tuple[bool, str]:
    """Return the semantic extraction verdict used by the fetch router."""
    facts = extract_pricing_facts(content, requirements)
    if facts.get("complete"):
        return True, "complete_pricing_tuple"
    missing = list(facts.get("missing_rates") or [])
    if not facts.get("currency"):
        missing.append("currency")
    if not facts.get("per_tokens"):
        missing.append("billing_unit")
    if facts.get("currency_matches") is False:
        missing.append("requested_currency")
    return False, "missing:" + ",".join(dict.fromkeys(missing))


def pricing_channel_for_reference(reference: Any) -> Optional[str]:
    """Map known official GLM billing surfaces to non-interchangeable channels."""
    domain = (urlparse(str(reference or "")).hostname or "").casefold()
    if domain == "z.ai" or domain.endswith(".z.ai"):
        return "global"
    if domain == "bigmodel.cn" or domain.endswith(".bigmodel.cn"):
        return "domestic"
    return None


def pricing_reference_matches(
    requirements: Mapping[str, Any],
    reference: Any,
) -> bool:
    requested = str(requirements.get("channel") or "").strip()
    if not requested:
        return True
    return pricing_channel_for_reference(reference) == requested


def collect_complete_pricing_facts(
    records: Iterable[Mapping[str, Any]],
    requirements: Mapping[str, Any],
    *,
    official_only: bool = True,
) -> List[Dict[str, Any]]:
    """Collect complete, full-fetch rate tuples without mixing sources."""
    facts: List[Dict[str, Any]] = []
    seen = set()
    for record in records:
        metadata = record.get("metadata") or {}
        tier = str(record.get("source_tier") or "").casefold()
        if official_only and tier != "official":
            continue
        if str(metadata.get("retrieval_kind") or "") != "fetch_url":
            continue
        if not pricing_reference_matches(requirements, record.get("reference")):
            continue
        extracted = extract_pricing_facts(record.get("content"), requirements)
        if not extracted.get("complete"):
            continue
        key = (
            extracted.get("currency"),
            extracted.get("per_tokens"),
            tuple(sorted((extracted.get("rates") or {}).items())),
        )
        if key in seen:
            continue
        seen.add(key)
        raw_eid = metadata.get("eid")
        if isinstance(raw_eid, int) and not isinstance(raw_eid, bool) and raw_eid > 0:
            eid = f"E{raw_eid}"
        elif re.fullmatch(r"E\d+", str(raw_eid or "")):
            eid = str(raw_eid)
        else:
            eid = ""
        extracted.update(
            {
                "eid": eid,
                "reference": str(record.get("reference") or "").strip(),
                "source_tier": tier,
            }
        )
        facts.append(extracted)
    return facts


def calculate_pricing_total(
    requirements: Mapping[str, Any],
    facts: Mapping[str, Any],
) -> Dict[str, Any]:
    """Calculate each requested component and total using Decimal arithmetic."""
    per_tokens = Decimal(str(facts.get("per_tokens") or "0"))
    if per_tokens <= 0:
        raise ValueError("A positive billing unit is required.")
    rates = facts.get("rates") or {}
    quantities = requirements.get("quantities") or {}
    components: Dict[str, Dict[str, str]] = {}
    total = Decimal(0)
    for role in requirements.get("required_rates") or []:
        if role not in rates or role not in quantities:
            raise ValueError(f"Missing pricing input for {role}.")
        tokens = Decimal(str(quantities[role]["count"]))
        rate = Decimal(str(rates[role]))
        cost = tokens / per_tokens * rate
        total += cost
        components[role] = {
            "count": _decimal_text(tokens),
            "rate": _decimal_text(rate),
            "cost": _decimal_text(cost),
        }
    return {
        "currency": facts.get("currency"),
        "per_tokens": _decimal_text(per_tokens),
        "components": components,
        "total": _decimal_text(total),
    }


def render_pricing_answer(
    requirements: Mapping[str, Any],
    fact_sets: Sequence[Mapping[str, Any]],
) -> str:
    """Render a reproducible answer, keeping each channel/source independent."""
    subject = str(requirements.get("subject") or "该模型")
    requested_channel = requirements.get("channel")
    requested_currency = requirements.get("currency")
    lines: List[str] = []
    if not requested_channel and not requested_currency:
        scope = "分别" if len(fact_sets) > 1 else ""
        lines.append(
            "你没有指定计费渠道或币种；以下"
            + scope
            + "按已核验到的官方渠道计算，不混用不同渠道的单价。"
        )

    for facts in fact_sets:
        calculation = calculate_pricing_total(requirements, facts)
        currency = str(calculation["currency"])
        symbol = "¥" if currency == "CNY" else "$" if currency == "USD" else f"{currency} "
        reference = str(facts.get("reference") or "")
        domain = (urlparse(reference).hostname or "").casefold()
        channel = pricing_channel_for_reference(reference)
        if channel == "global":
            channel_label = "Z.ai 国际站"
        elif channel == "domestic":
            channel_label = "智谱开放平台国内站"
        else:
            channel_label = domain or "官方渠道"
        eid = str(facts.get("eid") or "").strip()
        citation = f" [{eid}]" if re.fullmatch(r"E\d+", eid) else ""
        rates = facts.get("rates") or {}
        rate_parts = [
            f"{_ROLE_LABELS[role]} {symbol}{rates[role]}/百万 tokens"
            for role in requirements.get("required_rates") or []
        ]
        lines.append(
            f"{subject} 官方价表（{channel_label}，{currency}）："
            + "，".join(rate_parts)
            + f"{citation}。"
        )

        formula_parts = []
        for role in requirements.get("required_rates") or []:
            component = calculation["components"][role]
            millions = Decimal(component["count"]) / Decimal(calculation["per_tokens"])
            formula_parts.append(
                f"{_decimal_text(millions)}×{component['rate']}={component['cost']}"
            )
        lines.append(
            "计算："
            + " + ".join(formula_parts)
            + f"，合计 **{symbol}{calculation['total']}**{citation}。"
        )
    return "\n\n".join(lines)


def pricing_answer_failures(
    answer: Any,
    requirements: Mapping[str, Any],
    fact_sets: Sequence[Mapping[str, Any]],
) -> List[Dict[str, str]]:
    """Check that a pricing answer contains the deterministic expected total."""
    text = str(answer or "")
    failures: List[Dict[str, str]] = []
    for facts in fact_sets:
        expected = calculate_pricing_total(requirements, facts)["total"]
        if not re.search(rf"(?<![\d.]){re.escape(expected)}(?![\d.])", text):
            failures.append(
                {
                    "type": "pricing_total_mismatch",
                    "detail": f"定价总额应由已核验费率确定性计算为 {expected}。",
                    "sentence": "",
                }
            )
    return failures
