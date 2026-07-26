"""Mechanical per-claim citation verification for the critic.

Replaces two blunt answer-quality rules with precise, per-sentence checks:

* ``unsupported_specific_detail`` matched draft numbers against the entire
  evidence pool with string inclusion, so a figure from an aggregator snippet
  counted as "supported". Here every numeric sentence must carry a citation
  that resolves to an authoritative record.
* ``answer_temporal_coverage`` forced the model to sprinkle a year into the
  answer. Here recency is grounded in the cited record's own fetch/publish
  date.

The check is deliberately mechanical: it verifies that a citation exists,
resolves to a real evidence record (no ``[En]`` hallucination), and that the
record's tier and fetch status satisfy the assertion's requirement. Whether a
cited source actually *says* what the sentence claims is a semantic question
left to the judge LLM, not to this module.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from evidence.source_verdict import is_authoritative_tier

_CITATION_RE = re.compile(r"\[E(\d{1,4})\]")
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?%?\b")
# Benign small numbers that rarely need a source (list counts, versions).
_BENIGN_NUMBERS = {"1", "2", "3", "4", "5"}
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+|\.\s+")
_PRICING_CUES_RE = re.compile(
    r"[$¥€£]|价格|定价|费用|收费|成本|美元|人民币|每百万|每千|per\s*(?:1[mk]|token|million|thousand)|/\s*1[mk]\b",
    re.IGNORECASE,
)
_DATE_KEYS = ("retrieved_at", "published_at")


def _is_fetched(record: Dict[str, Any]) -> bool:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return False
    if str(metadata.get("retrieval_kind") or "") == "fetch_url":
        return True
    content_chars = metadata.get("content_chars")
    return isinstance(content_chars, int) and content_chars > 0


def _has_date(record: Dict[str, Any]) -> bool:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return any(str(metadata.get(key) or "").strip() for key in _DATE_KEYS)


def _eid_map(records: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    mapping: Dict[int, Dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            continue
        eid = metadata.get("eid")
        if isinstance(eid, bool):
            continue
        if isinstance(eid, int) and eid > 0:
            # Prefer the richest record if an id somehow repeats.
            existing = mapping.get(eid)
            if existing is None or _is_fetched(record):
                mapping[eid] = record
    return mapping


def _significant_numbers(sentence: str) -> List[str]:
    body = _CITATION_RE.sub(" ", sentence)
    return [
        token
        for token in _NUMBER_RE.findall(body)
        if token not in _BENIGN_NUMBERS
    ]


def _truncate(text: str, limit: int = 80) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def check_citations(
    answer: Any,
    records: List[Dict[str, Any]],
    *,
    requires_official_pricing: bool = False,
    temporal_required: bool = False,
) -> List[Dict[str, str]]:
    """Verify ``[En]`` citations in ``answer`` against ``records``.

    Returns a list of failure dicts with keys ``type``, ``sentence`` and
    ``detail`` (an actionable, model-facing explanation). An empty list means
    every numeric claim is backed by a resolving, appropriately-tiered source.
    """
    text = str(answer or "").strip()
    if not text:
        return []

    eid_to_record = _eid_map(records)
    cited_eids = {int(match) for match in _CITATION_RE.findall(text)}

    failures: List[Dict[str, str]] = []

    # A citation that resolves to nothing is an [En] hallucination.
    for eid in sorted(cited_eids - set(eid_to_record)):
        failures.append(
            {
                "type": "citation_unresolved",
                "sentence": f"[E{eid}]",
                "detail": (
                    f"引用了不存在的证据编号 [E{eid}]；请只引用台账中实际出现"
                    "的编号。"
                ),
            }
        )

    sentences = [
        segment.strip()
        for segment in _SENTENCE_SPLIT_RE.split(text)
        if segment and segment.strip()
    ]
    for sentence in sentences:
        numbers = _significant_numbers(sentence)
        if not numbers:
            continue
        local_eids = [int(match) for match in _CITATION_RE.findall(sentence)]
        if not local_eids:
            failures.append(
                {
                    "type": "citation_missing",
                    "sentence": _truncate(sentence),
                    "detail": (
                        "这句话包含具体数值却没有标注来源编号 [En]；请在其后"
                        "标注对应证据编号。"
                    ),
                }
            )
            continue
        local_records = [
            eid_to_record[eid] for eid in local_eids if eid in eid_to_record
        ]
        if not local_records:
            # All local citations were hallucinated; already reported above.
            continue
        if not any(is_authoritative_tier(r.get("source_tier")) for r in local_records):
            failures.append(
                {
                    "type": "citation_not_authoritative",
                    "sentence": _truncate(sentence),
                    "detail": (
                        "数值引用的来源不是官方/一手（official/first_party）；"
                        "请改用 official 来源，或明确标注该数值未经官方核实。"
                    ),
                }
            )
            continue
        if requires_official_pricing and _PRICING_CUES_RE.search(sentence):
            if not any(
                str(r.get("source_tier") or "").casefold() == "official"
                and _is_fetched(r)
                for r in local_records
            ):
                failures.append(
                    {
                        "type": "citation_needs_official_source",
                        "sentence": _truncate(sentence),
                        "detail": (
                            "价格类数值必须引用 official 且“已抓全文”的来源"
                            "（摘要不足以核实价格）；请抓取官方价格页后再作答。"
                        ),
                    }
                )

    if temporal_required:
        dated = [
            eid_to_record[eid]
            for eid in cited_eids
            if eid in eid_to_record and _has_date(eid_to_record[eid])
        ]
        authoritative_dated = [
            record
            for record in dated
            if is_authoritative_tier(record.get("source_tier"))
        ]
        if not authoritative_dated:
            failures.append(
                {
                    "type": "citation_recency_missing",
                    "sentence": "",
                    "detail": (
                        "该问题有时效要求，但引用的官方/一手中没有带来源日期"
                        "（抓取/发布日期）的记录；请引用带日期的官方来源以证明"
                        "时效。"
                    ),
                }
            )

    return failures
