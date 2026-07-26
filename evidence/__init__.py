"""Unified evidence retrieval and normalization primitives."""

from .source_layer import (
    EvidenceItem,
    EvidenceSource,
    EvidenceSourceType,
    LocalEvidenceSource,
    RetrievalOptions,
    WebEvidenceSource,
    build_evidence_summary,
    describe_used_sources,
    evidence_items_to_documents,
    evidence_items_to_search_hits,
    has_indexable_local_documents,
    normalize_reference_label,
    source_identity_label,
)
from .source_tiering import (
    classify_web_source_tier,
    normalize_entity_stem,
    official_domain_targets,
    official_entity_for_url,
    provisional_entity_for_url,
    registrable_domain,
)
from .ledger import EvidenceLedger, render_evidence_entry
from .source_verdict import (
    AUTHORITATIVE_TIERS,
    CANONICAL_TIERS,
    SourceVerdict,
    classify_source,
    is_authoritative_tier,
    normalize_source_tier,
)

__all__ = [
    "AUTHORITATIVE_TIERS",
    "CANONICAL_TIERS",
    "EvidenceItem",
    "EvidenceLedger",
    "EvidenceSource",
    "SourceVerdict",
    "EvidenceSourceType",
    "LocalEvidenceSource",
    "RetrievalOptions",
    "WebEvidenceSource",
    "build_evidence_summary",
    "classify_source",
    "classify_web_source_tier",
    "describe_used_sources",
    "evidence_items_to_documents",
    "evidence_items_to_search_hits",
    "has_indexable_local_documents",
    "is_authoritative_tier",
    "normalize_reference_label",
    "normalize_entity_stem",
    "normalize_source_tier",
    "official_domain_targets",
    "official_entity_for_url",
    "provisional_entity_for_url",
    "registrable_domain",
    "render_evidence_entry",
    "source_identity_label",
]
