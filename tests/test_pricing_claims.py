from __future__ import annotations

from evidence.pricing_claims import (
    calculate_pricing_total,
    extract_pricing_facts,
    parse_pricing_request,
    pricing_answer_failures,
    pricing_channel_for_reference,
    pricing_reference_matches,
    render_pricing_answer,
)


QUERY = "对于GLM5.2, 3M输入，300K输出，30M输入缓存命中的价格"


def _requirements():
    return parse_pricing_request(QUERY, entities=["GLM5.2"])


def test_parse_pricing_workload_preserves_each_usage_class():
    requirements = _requirements()

    assert requirements == {
        "operation": "pricing_total",
        "subject": "GLM5.2",
        "quantities": {
            "input": {"count": "3000000", "display": "3M输入"},
            "output": {"count": "300000", "display": "300K输出"},
            "cached_input": {
                "count": "30000000",
                "display": "30M输入缓存命中",
            },
        },
        "required_rates": ["input", "output", "cached_input"],
        "currency": None,
        "channel": None,
    }


def test_pricing_request_records_explicit_channel_and_currency():
    requirements = parse_pricing_request(
        "按 Z.ai 美元价格算 GLM-5.2 的 1M input 和 2M cached input cost",
        entities=["GLM-5.2"],
    )

    assert requirements["currency"] == "USD"
    assert requirements["channel"] == "global"
    assert requirements["required_rates"] == ["input", "cached_input"]


def test_explicit_channel_rejects_the_other_official_billing_surface():
    domestic = parse_pricing_request(
        "按国内价格算 GLM-5.2 的 1M 输入成本",
        entities=["GLM-5.2"],
    )

    assert pricing_channel_for_reference("https://bigmodel.cn/pricing") == "domestic"
    assert pricing_channel_for_reference("https://docs.z.ai/guides/overview/pricing") == "global"
    assert pricing_reference_matches(domestic, "https://bigmodel.cn/pricing") is True
    assert pricing_reference_matches(domestic, "https://docs.z.ai/guides/overview/pricing") is False


def test_complete_markdown_price_row_is_extracted_as_one_tuple():
    page = """# 产品价格
|模型名称 |上下文 (千tokens) |输入单价 (百万tokens) |输出单价 (百万tokens) |缓存存储 (百万tokens/小时) |缓存命中 (百万tokens) |
| --- | --- | --- | --- | --- | --- |
|GLM-5.2 |200 |8元 |28元 |1元 |2元 |
"""

    facts = extract_pricing_facts(page, _requirements())

    assert facts["complete"] is True
    assert facts["rates"] == {
        "input": "8",
        "output": "28",
        "cached_input": "2",
    }
    assert facts["currency"] == "CNY"
    assert facts["per_tokens"] == "1000000"


def test_english_table_header_maps_cached_input_before_output():
    page = """# Model Pricing
All prices below are USD per 1M tokens.
| Model | Input | Cached Input | Cached Input Storage | Output |
| --- | --- | --- | --- | --- |
| GLM-5.2 | $1.4 | $0.26 | Limited-time Free | $4.4 |
"""

    facts = extract_pricing_facts(page, _requirements())

    assert facts["complete"] is True
    assert facts["rates"] == {
        "input": "1.4",
        "output": "4.4",
        "cached_input": "0.26",
    }
    assert facts["currency"] == "USD"


def test_truncated_target_row_is_not_accepted_despite_long_page():
    page = (
        "# 产品价格\n"
        "输入单价 (百万tokens) 输出单价 (百万tokens) 缓存命中 (百万tokens)\n"
        "|GLM-5.2\n"
        + ("其他模型介绍，不是目标价格。" * 200)
    )

    facts = extract_pricing_facts(page, _requirements())

    assert facts["complete"] is False
    assert facts["missing_rates"] == ["input", "output", "cached_input"]


def test_decimal_total_and_rendered_formula_are_reproducible():
    facts = {
        "rates": {"input": "8", "output": "28", "cached_input": "2"},
        "currency": "CNY",
        "per_tokens": "1000000",
        "eid": "E8",
    }

    calculation = calculate_pricing_total(_requirements(), facts)
    answer = render_pricing_answer(_requirements(), [facts])

    assert calculation["components"]["input"]["cost"] == "24"
    assert calculation["components"]["output"]["cost"] == "8.4"
    assert calculation["components"]["cached_input"]["cost"] == "60"
    assert calculation["total"] == "92.4"
    assert "3×8=24 + 0.3×28=8.4 + 30×2=60" in answer
    assert "**¥92.4**" in answer
    assert answer.count("[E8]") == 2
    assert pricing_answer_failures(answer, _requirements(), [facts]) == []


def test_wrong_total_is_rejected_by_deterministic_answer_check():
    facts = {
        "rates": {"input": "8", "output": "28", "cached_input": "2"},
        "currency": "CNY",
        "per_tokens": "1000000",
    }

    failures = pricing_answer_failures(
        "GLM5.2 合计 ¥90 [E1]。",
        _requirements(),
        [facts],
    )

    assert failures[0]["type"] == "pricing_total_mismatch"
