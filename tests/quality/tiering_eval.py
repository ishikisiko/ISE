"""D6 offline evaluation: source tiering and official-domain resolution (Q2-02).

* ``dataset/source_tier_gold.csv`` -> ``classify_source`` with a pins-only
  resolver (no discovery, no fetches): 5x5 confusion matrix, tier accuracy,
  ``official_precision`` / ``official_recall``, ``denylist_compliance`` and
  ``non_evidence_exclusion``.
* ``dataset/official_domain_gold.csv`` -> ``OfficialDomainResolver`` replayed
  from ``tests/fixtures/official_domains_replay.sqlite`` (timestamps frozen):
  ``resolver_accuracy`` and ``resolver_none_rate``.

    python -m tests.quality.tiering_eval --output-file runtime/quality/<run>/evidence_eval_offline.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evidence.official_domain_resolver import OfficialDomainResolver, resolver_config_from_mapping  # noqa: E402
from evidence.source_tiering import registrable_domain  # noqa: E402
from evidence.source_verdict import CANONICAL_TIERS, classify_source  # noqa: E402
from search.reference_fetch import ReferenceExtraction  # noqa: E402
from tests.quality.common import ROOT, read_csv_rows, utc_now, write_json  # noqa: E402
from tests.quality.export_resolver_replay import REPLAY_CLOCK  # noqa: E402

DEFAULT_TIER_GOLD = "dataset/source_tier_gold.csv"
DEFAULT_DOMAIN_GOLD = "dataset/official_domain_gold.csv"
DEFAULT_REPLAY = "tests/fixtures/official_domains_replay.sqlite"
DEFAULT_CONFIG = "config.example.json"
AUTHORITATIVE = {"official", "first_party"}


class _NoFetch:
    """Offline stand-in for the resolver's self-proof fetch client."""

    source_id = "no_fetch"

    def extract(self, urls: Any, **kwargs: Any) -> ReferenceExtraction:
        return ReferenceExtraction(provider=self.source_id)


def load_resolution_block(config_path: str) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    orchestration = config.get("orchestration") or {}
    block = dict(orchestration.get("official_domain_resolution") or {})
    return block


def build_offline_resolver(block: Dict[str, Any], *, cache_path: Optional[str], enabled: bool) -> OfficialDomainResolver:
    """Pins + tables from config; every network path disabled."""
    settings = dict(block)
    settings.update(
        {
            "enabled": enabled,
            "graph_probes_enabled": False,
            "pin_shadow_audit": False,
            "structured_sources": [],
            "max_discovery_providers": 0,
            "cache_path": cache_path or str(Path(tempfile.mkdtemp()) / "tiering_cache.sqlite"),
            "cache_ttl_days": 3650,
        }
    )
    config = resolver_config_from_mapping(settings)
    return OfficialDomainResolver(config, search_clients=[], fetch_client=_NoFetch(), clock=lambda: REPLAY_CLOCK)


def evaluate_tiers(rows: List[Dict[str, Any]], resolver: OfficialDomainResolver) -> Dict[str, Any]:
    confusion: Dict[str, Dict[str, int]] = {gold: {pred: 0 for pred in CANONICAL_TIERS} for gold in CANONICAL_TIERS}
    per_url: List[Dict[str, Any]] = []
    for row in rows:
        url = str(row.get("url") or "").strip()
        entity = str(row.get("entity") or "").strip()
        gold = str(row.get("gold_tier") or "unknown").strip()
        verdict = classify_source(url, entities=[entity] if entity else [], official_domains=None, resolver=resolver)
        pred = verdict.tier
        confusion.setdefault(gold, {pred_tier: 0 for pred_tier in CANONICAL_TIERS})
        confusion[gold][pred] = confusion[gold].get(pred, 0) + 1
        per_url.append({"qid": row.get("qid"), "url": url, "entity": entity, "gold": gold, "pred": pred, "category": row.get("category"), "why": verdict.why})
    total = len(per_url)
    strict_correct = sum(1 for item in per_url if item["gold"] == item["pred"])
    collapsed_correct = sum(
        1 for item in per_url
        if (item["gold"] in AUTHORITATIVE and item["pred"] in AUTHORITATIVE) or (item["gold"] not in AUTHORITATIVE and item["gold"] == item["pred"])
    )
    predicted_official = [item for item in per_url if item["pred"] == "official"]
    gold_official = [item for item in per_url if item["gold"] == "official"]
    predicted_authoritative = [item for item in per_url if item["pred"] in AUTHORITATIVE]
    denylist_rows = [item for item in per_url if item["category"] == "aggregator"]
    non_evidence_rows = [item for item in per_url if item["gold"] == "excluded"]
    lookalikes = [item for item in per_url if item["category"] == "third_party_or_lookalike" and item["entity"]]
    return {
        "urls": total,
        "confusion_matrix": confusion,
        "tier_accuracy_strict": (strict_correct / total) if total else None,
        "tier_accuracy_authoritative_collapsed": (collapsed_correct / total) if total else None,
        "official_precision": (sum(1 for item in predicted_official if item["gold"] == "official") / len(predicted_official)) if predicted_official else None,
        "official_recall": (sum(1 for item in gold_official if item["pred"] == "official") / len(gold_official)) if gold_official else None,
        "authoritative_precision": (sum(1 for item in predicted_authoritative if item["gold"] == "official") / len(predicted_authoritative)) if predicted_authoritative else None,
        "authoritative_recall": (sum(1 for item in gold_official if item["pred"] in AUTHORITATIVE) / len(gold_official)) if gold_official else None,
        "denylist_compliance": (sum(1 for item in denylist_rows if item["pred"] not in AUTHORITATIVE) / len(denylist_rows)) if denylist_rows else None,
        "non_evidence_exclusion": (sum(1 for item in non_evidence_rows if item["pred"] == "excluded") / len(non_evidence_rows)) if non_evidence_rows else None,
        "lookalike_false_authority": [item["url"] for item in lookalikes if item["pred"] in AUTHORITATIVE],
        "lookalike_false_authority_rate": (sum(1 for item in lookalikes if item["pred"] in AUTHORITATIVE) / len(lookalikes)) if lookalikes else None,
        "errors": [item for item in per_url if item["gold"] != item["pred"]],
        "per_url": per_url,
    }


def evaluate_resolver(rows: List[Dict[str, Any]], resolver: OfficialDomainResolver) -> Dict[str, Any]:
    per_entity: List[Dict[str, Any]] = []
    for row in rows:
        entity = str(row.get("entity") or "").strip()
        gold_domains = {registrable_domain(item.strip()) for item in str(row.get("gold_domains") or "").split("|") if item.strip()}
        resolution = resolver.resolve(entity)
        resolved = [registrable_domain(item) for item in (resolution.resolved_domains or [])]
        is_official = bool(getattr(resolution, "is_official", False))
        correct: Optional[bool]
        if not gold_domains:
            # An entity with no official domain is resolved correctly when the resolver refuses to call anything official.
            correct = not is_official
        else:
            correct = is_official and any(domain in gold_domains for domain in resolved)
        per_entity.append({
            "qid": row.get("qid"), "entity": entity, "is_pinned": str(row.get("is_pinned") or "0") == "1", "category": row.get("category"),
            "gold_domains": sorted(gold_domains), "confidence": resolution.confidence, "resolved": resolved, "correct": correct,
            "wrong_official": is_official and bool(gold_domains) and not any(domain in gold_domains for domain in resolved),
        })
    total = len(per_entity)
    unpinned = [item for item in per_entity if not item["is_pinned"]]
    return {
        "entities": total,
        "unpinned_entities": len(unpinned),
        "resolver_accuracy": (sum(1 for item in per_entity if item["correct"]) / total) if total else None,
        "resolver_accuracy_unpinned": (sum(1 for item in unpinned if item["correct"]) / len(unpinned)) if unpinned else None,
        "resolver_none_rate": (sum(1 for item in per_entity if item["confidence"] in {"candidate", "none"}) / total) if total else None,
        "resolver_none_rate_unpinned": (sum(1 for item in unpinned if item["confidence"] in {"candidate", "none"}) / len(unpinned)) if unpinned else None,
        "wrong_official_count": sum(1 for item in per_entity if item["wrong_official"]),
        "wrong_official": [item for item in per_entity if item["wrong_official"]],
        "confidence_distribution": {key: sum(1 for item in per_entity if item["confidence"] == key) for key in sorted({item["confidence"] for item in per_entity})},
        "per_entity": per_entity,
    }


def run(
    *,
    tier_gold: str = DEFAULT_TIER_GOLD,
    domain_gold: str = DEFAULT_DOMAIN_GOLD,
    replay: str = DEFAULT_REPLAY,
    config_path: str = DEFAULT_CONFIG,
) -> Dict[str, Any]:
    block = load_resolution_block(str(ROOT / config_path) if not Path(config_path).is_absolute() else config_path)
    tier_resolver = build_offline_resolver(block, cache_path=None, enabled=False)
    tiers = evaluate_tiers(read_csv_rows(ROOT / tier_gold), tier_resolver)
    replay_copy = Path(tempfile.mkdtemp()) / "replay.sqlite"
    shutil.copyfile(ROOT / replay, replay_copy)
    replay_resolver = build_offline_resolver(block, cache_path=str(replay_copy), enabled=True)
    resolver = evaluate_resolver(read_csv_rows(ROOT / domain_gold), replay_resolver)
    return {
        "created_at": utc_now(),
        "mode": "offline: pins/tables only for tiering; cached relation graphs replayed for the resolver",
        "inputs": {"tier_gold": tier_gold, "domain_gold": domain_gold, "replay_fixture": replay, "config": config_path},
        "tiering": {key: value for key, value in tiers.items() if key != "per_url"},
        "resolver": {key: value for key, value in resolver.items() if key != "per_entity"},
        "per_url": tiers["per_url"],
        "per_entity": resolver["per_entity"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tier-gold", default=DEFAULT_TIER_GOLD)
    parser.add_argument("--domain-gold", default=DEFAULT_DOMAIN_GOLD)
    parser.add_argument("--replay", default=DEFAULT_REPLAY)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--output-file", default=None)
    args = parser.parse_args()
    report = run(tier_gold=args.tier_gold, domain_gold=args.domain_gold, replay=args.replay, config_path=args.config)
    if args.output_file:
        write_json(args.output_file, report)
    printable = {
        "tiering": {k: v for k, v in report["tiering"].items() if k != "errors"},
        "resolver": {k: v for k, v in report["resolver"].items() if k != "wrong_official"},
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
