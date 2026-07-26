from __future__ import annotations

from evidence.ledger import EvidenceLedger, render_evidence_entry


def _record(**overrides):
    record = {
        "source_type": "web",
        "source_tier": "unknown",
        "reference": "https://example.com/pricing",
        "title": "Pricing",
        "content": "snippet text",
        "metadata": {},
    }
    record.update(overrides)
    return record


def test_render_entry_marks_snippet_only_with_tier_and_entity():
    record = _record(metadata={"source_tier_entities": ["Kimi"]})
    entry = render_evidence_entry(2, record)
    assert entry.startswith("[E2] unknown · Kimi · 仅摘要")
    assert "https://example.com/pricing" in entry
    assert "snippet text" in entry


def test_render_entry_marks_fetched_full_text_with_size_and_date():
    record = _record(
        source_tier="official",
        metadata={
            "retrieval_kind": "fetch_url",
            "content_chars": 1081,
            "retrieved_at": "2026-07-26",
        },
    )
    entry = render_evidence_entry(6, record)
    assert entry.startswith("[E6] official · 已抓全文 1081 字 · 抓取于 2026-07-26")


def test_render_entry_prefers_published_date_when_present():
    record = _record(
        metadata={"published_at": "2026-07-20", "retrieved_at": "2026-07-26"}
    )
    entry = render_evidence_entry(1, record)
    assert "发布于 2026-07-20" in entry
    assert "抓取于" not in entry


def test_register_reuses_id_for_same_canonical_url():
    ledger = EvidenceLedger()
    first = ledger.register(_record(reference="https://example.com/a?token=x"))
    second = ledger.register(_record(reference="https://example.com/a"))
    assert first == second


def test_register_upgrades_to_richer_record_for_same_url():
    ledger = EvidenceLedger()
    snippet = _record(content="short snippet")
    eid = ledger.register(snippet)
    fetched = _record(
        source_tier="official",
        content="full page body " * 40,
        metadata={"retrieval_kind": "fetch_url", "content_chars": 600},
    )
    same_eid = ledger.register(fetched)
    assert same_eid == eid
    assert ledger.resolve(eid)["source_tier"] == "official"
    assert "已抓全文" in ledger.render_entry(eid)


def test_register_does_not_downgrade_fetched_record_to_snippet():
    ledger = EvidenceLedger()
    fetched = _record(
        source_tier="official",
        content="full page body " * 40,
        metadata={"retrieval_kind": "fetch_url", "content_chars": 600},
    )
    eid = ledger.register(fetched)
    ledger.register(_record(content="short snippet"))
    assert ledger.resolve(eid)["source_tier"] == "official"


def test_distinct_urls_get_distinct_ids():
    ledger = EvidenceLedger()
    a = ledger.register(_record(reference="https://example.com/a"))
    b = ledger.register(_record(reference="https://example.com/b"))
    assert a != b


def test_render_entry_truncates_oversized_content():
    ledger = EvidenceLedger(max_entry_chars=400)
    record = _record(content="x" * 5000)
    eid = ledger.register(record)
    entry = ledger.render_entry(eid)
    assert len(entry) <= 400 + 60
    assert "truncated" in entry
