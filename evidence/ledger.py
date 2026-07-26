"""Stable, model-facing evidence ledger for the ReAct loop.

The model previously received tool observations as bare ``title / URL /
snippet`` triples with no provenance, so it could not tell an official page
from an aggregator and had no citation handle to reference. This module
assigns each piece of evidence a stable ``[En]`` citation ID keyed by its
canonical URL and renders model-facing ledger entries that carry the source
tier, the matched entity, the fetch status (snippet-only vs. fetched full
text), and a date.

IDs are stable per canonical URL within a run: when the same URL is later
fetched, the ID is reused and the stored record is upgraded to the richest
available version, so a citation to ``[E1]`` always resolves to the best copy
we hold. The richer record therefore never duplicates a URL across the
conversation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.query_orchestration import canonical_reference

_FETCH_KIND = "fetch_url"


def _record_key(record: Dict[str, Any]) -> str:
    """Return the dedup identity for a record.

    Canonical URL when one is available; otherwise a title+source fallback so
    local documents without URLs still dedup sensibly.
    """
    reference = str(record.get("reference") or "").strip()
    if reference:
        key = canonical_reference(reference)
        if key:
            return key
    title = str(record.get("title") or "").strip().casefold()
    source_type = str(record.get("source_type") or "").strip().casefold()
    return f"{source_type}:{title}"


def _is_fetched(record: Dict[str, Any]) -> bool:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return False
    if str(metadata.get("retrieval_kind") or "") == _FETCH_KIND:
        return True
    content_chars = metadata.get("content_chars")
    return isinstance(content_chars, int) and content_chars > 0


def _is_richer(candidate: Dict[str, Any], current: Dict[str, Any]) -> bool:
    """Fetched full text beats a snippet; longer content breaks ties."""
    cand_fetched = _is_fetched(candidate)
    cur_fetched = _is_fetched(current)
    if cand_fetched != cur_fetched:
        return cand_fetched
    return len(str(candidate.get("content") or "")) > len(
        str(current.get("content") or "")
    )


def _first(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def render_evidence_entry(eid: int, record: Dict[str, Any]) -> str:
    """Render one model-facing ledger entry for ``record``.

    Layout::

        [E3] official · Kimi · 已抓全文 1081 字 · 抓取于 2026-07-26
        依据: config pin: kimi.com
        https://platform.kimi.com/docs/pricing/chat-k27-code
        <content>
    """
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    tier = str(record.get("source_tier") or "unknown").strip() or "unknown"

    header = f"[E{eid}] {tier}"

    entity = (
        str(metadata.get("source_target") or "").strip()
        or _first(metadata.get("source_tier_entities"))
        or _first(metadata.get("authority_provisional_entity"))
    )
    if entity:
        header += f" · {entity}"

    fetched = _is_fetched(record)
    source_type = str(record.get("source_type") or "").strip().casefold()
    content = str(record.get("content") or "")
    if fetched:
        chars = metadata.get("content_chars")
        chars = chars if isinstance(chars, int) and chars > 0 else len(content)
        header += f" · 已抓全文 {chars} 字"
    elif source_type == "local":
        header += " · 本地文档"
    else:
        header += " · 仅摘要"

    date = str(metadata.get("published_at") or "").strip()
    if date:
        header += f" · 发布于 {date}"
    else:
        retrieved = str(metadata.get("retrieved_at") or "").strip()
        if retrieved:
            header += f" · 抓取于 {retrieved}"

    reference = str(record.get("reference") or "").strip()
    title = str(record.get("title") or "").strip()
    parts = [header]

    why = str(metadata.get("source_verdict_why") or "").strip()
    disagreement = str(metadata.get("source_verdict_disagreement") or "").strip()
    if why:
        parts.append(f"依据: {why}")
    if disagreement:
        parts.append(f"分歧: {disagreement}")

    if title and title != reference:
        parts.append(title)
    if reference:
        parts.append(reference)
    if content:
        parts.append(content)
    return "\n".join(parts)


class EvidenceLedger:
    """Per-run registry mapping canonical URLs to stable citation IDs."""

    def __init__(self, *, max_entry_chars: int = 8000) -> None:
        self.max_entry_chars = max(200, int(max_entry_chars or 8000))
        self._by_key: Dict[str, int] = {}
        self._records: Dict[int, Dict[str, Any]] = {}
        self._next = 1

    def register(self, record: Dict[str, Any]) -> int:
        """Assign (or reuse) the citation ID for ``record`` and return it.

        The richest version seen for a canonical URL is retained so a later
        citation resolves to the fetched full text rather than the snippet.
        """
        key = _record_key(record)
        eid = self._by_key.get(key)
        if eid is None:
            eid = self._next
            self._next += 1
            self._by_key[key] = eid
            self._records[eid] = record
        elif _is_richer(record, self._records[eid]):
            self._records[eid] = record
        return eid

    def resolve(self, eid: int) -> Optional[Dict[str, Any]]:
        return self._records.get(eid)

    def render_entry(self, eid: int, record: Optional[Dict[str, Any]] = None) -> str:
        """Render the ledger entry for ``eid`` (defaults to the stored record)."""
        stored = record if record is not None else self._records.get(eid)
        if stored is None:
            return f"[E{eid}] unknown"
        entry = render_evidence_entry(eid, stored)
        if len(entry) > self.max_entry_chars:
            entry = (
                entry[: self.max_entry_chars]
                + f"\n... [truncated; {len(entry)} chars total]"
            )
        return entry

    def render_entries(self, pairs: List[Any]) -> str:
        """Render ``[(eid, record), ...]`` joined by blank lines."""
        return "\n\n".join(
            self.render_entry(eid, record) for eid, record in pairs
        )
