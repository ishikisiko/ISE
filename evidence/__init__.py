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

__all__ = [
    "EvidenceItem",
    "EvidenceSource",
    "EvidenceSourceType",
    "LocalEvidenceSource",
    "RetrievalOptions",
    "WebEvidenceSource",
    "build_evidence_summary",
    "classify_web_source_tier",
    "describe_used_sources",
    "evidence_items_to_documents",
    "evidence_items_to_search_hits",
    "has_indexable_local_documents",
    "normalize_reference_label",
    "normalize_entity_stem",
    "official_domain_targets",
    "official_entity_for_url",
    "provisional_entity_for_url",
    "registrable_domain",
    "source_identity_label",
]
