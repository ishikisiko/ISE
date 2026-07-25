"""Unit coverage for deterministic web evidence source tiers."""

from evidence import (
    classify_web_source_tier,
    normalize_entity_stem,
    official_domain_targets,
    official_entity_for_url,
    registrable_domain,
)


def test_normalizes_product_versions_and_extracts_registrable_domains() -> None:
    assert normalize_entity_stem("GLM5.2") == "glm"
    assert normalize_entity_stem("kimik3") == "kimi"
    # Non-ASCII labels must keep a usable stem instead of collapsing to "".
    assert normalize_entity_stem("小米") == "小米"
    assert normalize_entity_stem("阿里云") == "阿里云"
    assert normalize_entity_stem("Hermès") == "hermès"
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


def test_official_aliases_only_apply_to_current_query_targets() -> None:
    domains = {
        "fable": ["fable.ai"],
        "glm": ["zhipu.cn"],
        "kimi": ["moonshot.cn"],
        "fireworks": ["fireworks.ai"],
    }
    entities = ["fable5", "glm5.2", "kimik3"]

    assert [target["entity"] for target in official_domain_targets(entities, domains)] == entities
    assert (
        official_entity_for_url(
            "https://open.bigmodel.cn/pricing",
            entities=entities,
            official_domains={**domains, "glm": ["bigmodel.cn", "open.bigmodel.cn"]},
        )
        == "glm5.2"
    )
    assert (
        official_entity_for_url(
            "https://fireworks.ai/pricing",
            entities=entities,
            official_domains=domains,
        )
        is None
    )
    assert (
        classify_web_source_tier(
            "https://fireworks.ai/pricing",
            entities=entities,
            official_domains=domains,
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
