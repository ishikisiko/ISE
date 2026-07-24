"""Unit coverage for deterministic web evidence source tiers."""

from evidence import classify_web_source_tier, normalize_entity_stem, registrable_domain


def test_normalizes_product_versions_and_extracts_registrable_domains() -> None:
    assert normalize_entity_stem("GLM5.2") == "glm"
    assert normalize_entity_stem("kimik3") == "kimi"
    assert registrable_domain("https://docs.zhipu.cn/pricing") == "zhipu.cn"
    assert registrable_domain("https://api.example.co.uk/v1") == "example.co.uk"


def test_official_domain_alias_takes_precedence_over_stem_matching() -> None:
    tier = classify_web_source_tier(
        "https://pricing.glm.com/models",
        entities=["glm5.2"],
        official_domains={"glm": ["glm.com"]},
    )

    assert tier == "official"


def test_first_party_and_unknown_domains_remain_distinct() -> None:
    assert (
        classify_web_source_tier(
            "https://docs.fable.ai/pricing",
            entities=["fable5"],
        )
        == "first_party"
    )
    assert (
        classify_web_source_tier(
            "https://independent-review.example/pricing",
            entities=["fable5"],
        )
        == "unknown"
    )
    assert (
        classify_web_source_tier(
            "https://docs.zhipu.cn/pricing",
            entities=["fable5"],
            official_domains={"glm": ["zhipu.cn"]},
        )
        == "unknown"
    )
