from __future__ import annotations

from evidence.citation_check import check_citations


def _record(eid, tier="official", fetched=True, content="body", retrieved_at="2026-07-26"):
    metadata = {"eid": eid}
    if fetched:
        metadata["retrieval_kind"] = "fetch_url"
        metadata["content_chars"] = len(content)
    if retrieved_at:
        metadata["retrieved_at"] = retrieved_at
    return {
        "source_type": "web",
        "source_tier": tier,
        "reference": f"https://example.com/{eid}",
        "content": content,
        "metadata": metadata,
    }


def test_numeric_sentence_with_official_citation_passes():
    records = [_record(1, tier="official", fetched=True)]
    answer = "Kimi K2.7 输入价为每百万 tokens ¥2.60 [E1]。"
    assert check_citations(answer, records, requires_official_pricing=True) == []


def test_numeric_sentence_without_citation_fails():
    records = [_record(1)]
    answer = "Kimi K2.7 输入价为每百万 tokens ¥2.60。"
    failures = check_citations(answer, records, requires_official_pricing=True)
    assert any(f["type"] == "citation_missing" for f in failures)


def test_hallucinated_citation_id_fails():
    records = [_record(1)]
    answer = "价格为 ¥2.60 [E9]。"
    failures = check_citations(answer, records)
    assert any(f["type"] == "citation_unresolved" for f in failures)


def test_aggregator_citation_fails_authority_gate():
    # The $1.90-from-aggregator case: a number cited to an aggregator source
    # must not count as authoritative support.
    records = [_record(2, tier="aggregator", fetched=False)]
    answer = "另一来源显示 Input $1.90 per 1M tokens [E2]。"
    failures = check_citations(answer, records, requires_official_pricing=True)
    assert any(f["type"] == "citation_not_authoritative" for f in failures)


def test_pricing_claim_requires_official_fetched_source():
    # Official tier but only a snippet (not fetched) is not enough for pricing.
    records = [_record(1, tier="official", fetched=False)]
    answer = "官方输入价 ¥2.60 [E1]。"
    failures = check_citations(answer, records, requires_official_pricing=True)
    assert any(f["type"] == "citation_needs_official_source" for f in failures)


def test_pricing_claim_passes_with_official_fetched():
    records = [_record(1, tier="official", fetched=True)]
    answer = "官方输入价 ¥2.60 [E1]。"
    assert check_citations(answer, records, requires_official_pricing=True) == []


def test_non_pricing_number_only_needs_authoritative_not_fetched():
    # A non-pricing numeric claim is satisfied by an authoritative snippet.
    records = [_record(1, tier="first_party", fetched=False)]
    answer = "该模型支持 128K 上下文 [E1]。"
    assert check_citations(answer, records, requires_official_pricing=True) == []


def test_benign_small_numbers_do_not_require_citation():
    records = []
    answer = "主要有 3 种计费方式。"
    assert check_citations(answer, records) == []


def test_temporal_recency_requires_dated_authoritative_source():
    # Time-sensitive query but the only cited record has no date.
    undated = _record(1, tier="official", fetched=True, retrieved_at=None)
    answer = "当前价格 ¥2.60 [E1]。"
    failures = check_citations(answer, [undated], temporal_required=True)
    assert any(f["type"] == "citation_recency_missing" for f in failures)


def test_temporal_recency_passes_with_dated_source():
    records = [_record(1, tier="official", fetched=True, retrieved_at="2026-07-26")]
    answer = "当前价格 ¥2.60 [E1]。"
    assert check_citations(answer, records, temporal_required=True) == []


def test_citation_marker_does_not_count_as_number():
    # "[E12]" contains 12; the marker itself must not demand its own citation.
    records = [_record(12, tier="official", fetched=True)]
    answer = "官方输入价 ¥2.60 [E12]。"
    assert check_citations(answer, records, requires_official_pricing=True) == []


def test_version_digits_in_product_name_do_not_demand_citation():
    # "K2.7" leaked a bare "7" through the number regex, so a pure lead-in
    # sentence with no claim in it was reported as an uncited numeric claim.
    records = [_record(1, tier="official", fetched=True)]
    answer = "根据 Kimi 官方定价页面，Kimi K2.7 Code HighSpeed 的价格如下（每 1M tokens）："
    assert check_citations(answer, records, requires_official_pricing=True) == []


def test_real_numbers_still_require_citation_alongside_version_names():
    records = [_record(1, tier="official", fetched=True)]
    answer = "Kimi K2.7 Code HighSpeed 的输出价格是 ¥54.00。"
    failures = check_citations(answer, records, requires_official_pricing=True)
    assert any(f["type"] == "citation_missing" for f in failures)


def test_unofficial_figure_labelled_as_unverified_is_accepted():
    # The failure's own detail offers "或明确标注该数值未经官方核实" as the
    # remedy, so an explicitly hedged third-party figure must pass.
    records = [_record(1, tier="official", fetched=True), _record(2, tier="unknown", fetched=False)]
    answer = "第三方平台列出 $1.90 / 1M tokens [E2]，该数据未经官方确认，仅供参考。"
    assert check_citations(answer, records, requires_official_pricing=True) == []


def test_unofficial_figure_without_hedge_is_still_rejected():
    records = [_record(1, tier="official", fetched=True), _record(2, tier="unknown", fetched=False)]
    answer = "输入价格为 $1.90 / 1M tokens [E2]。"
    failures = check_citations(answer, records, requires_official_pricing=True)
    assert any(f["type"] == "citation_not_authoritative" for f in failures)
