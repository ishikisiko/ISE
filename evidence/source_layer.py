from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from search.search import SearchClient, SearchHit
from utils.query_orchestration import canonical_reference
from .source_tiering import classify_web_source_tier, official_entity_for_url

if TYPE_CHECKING:
    from langchain.langchain_support import Document, LangChainVectorStore


class EvidenceSourceType(str, Enum):
    WEB = "web"
    LOCAL = "local"
    DOMAIN = "domain"


INDEXABLE_DOCUMENT_SUFFIXES = (".txt", ".md", ".pdf")


def has_indexable_local_documents(data_path: Optional[str]) -> bool:
    """Check for indexable local files without loading an embedding model."""
    if not data_path or not os.path.isdir(data_path):
        return False
    for root, _, files in os.walk(data_path):
        for name in files:
            if not name.lower().endswith(INDEXABLE_DOCUMENT_SUFFIXES):
                continue
            if os.path.isfile(os.path.join(root, name)):
                return True
    return False


@dataclass
class RetrievalOptions:
    num_results: int = 5
    per_source_limit: Optional[int] = None
    freshness: Optional[str] = None
    date_restrict: Optional[str] = None
    timing_recorder: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceItem:
    source_type: str
    source_id: str
    title: str
    content: str
    reference: str
    snippet: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: Optional[float] = None
    rank: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _preview_text(text: str, limit: int = 320) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit - 3]}..."


def normalize_reference_label(item: EvidenceItem) -> str:
    reference = str(item.reference or "").strip()
    if reference:
        return reference
    if item.source_type == EvidenceSourceType.LOCAL.value:
        return item.metadata.get("source") or item.title or "local document"
    return item.title or item.source_id


def source_identity_label(source_type: str, source_id: str) -> str:
    source_type = str(source_type or "").strip()
    source_id = str(source_id or "").strip()
    if source_type and source_id.startswith(f"{source_type}:"):
        return source_id
    if source_type and source_id:
        return f"{source_type}:{source_id}"
    return source_id or source_type


def evidence_items_to_search_hits(items: List[EvidenceItem]) -> List[SearchHit]:
    hits: List[SearchHit] = []
    for item in items:
        if item.source_type != EvidenceSourceType.WEB.value:
            continue
        hits.append(
            SearchHit(
                title=item.title or item.reference or "Untitled",
                url=item.reference,
                snippet=item.snippet or _preview_text(item.content),
            )
        )
    return hits


def evidence_items_to_documents(items: List[EvidenceItem]) -> List["Document"]:
    # Loading ``langchain.langchain_support`` at module import time triggers
    # the package's orchestration exports.  Keep the conversion dependency at
    # its use boundary so the standalone evidence contract remains importable.
    from langchain.langchain_support import Document

    docs: List[Document] = []
    for item in items:
        if item.source_type != EvidenceSourceType.LOCAL.value:
            continue
        docs.append(
            Document(
                content=item.content,
                source=item.metadata.get("source") or item.reference or item.title,
            )
        )
    return docs


def describe_used_sources(items: List[EvidenceItem]) -> List[Dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    described: List[Dict[str, Any]] = []
    for item in items:
        key = (item.source_type, item.source_id)
        if key in seen:
            continue
        seen.add(key)
        described.append(
            {
                "source_type": item.source_type,
                "source_id": item.source_id,
                "reference": normalize_reference_label(item),
            }
        )
    return described


def build_evidence_summary(items: List[EvidenceItem], limit: int = 8) -> str:
    lines: List[str] = []
    for index, item in enumerate(items[:limit], start=1):
        lines.append(
            f"{index}. [{source_identity_label(item.source_type, item.source_id)}] "
            f"{item.title or normalize_reference_label(item)} | "
            f"{normalize_reference_label(item)} | "
            f"{item.snippet or _preview_text(item.content, limit=180)}"
        )
    return "\n".join(lines)


def _plan_provenance(options: RetrievalOptions) -> Dict[str, Any]:
    """Copy only plan metadata that downstream fusion and audit may expose."""
    metadata = options.metadata or {}
    return {
        key: metadata[key]
        for key in (
            "originating_plan_step",
            "source_tier",
            "retrieval_kind",
            "content_date",
            "covered_claims",
        )
        if metadata.get(key) is not None
    }


class EvidenceSource(ABC):
    source_type: EvidenceSourceType
    source_id: str
    display_name: str

    def describe(self) -> Dict[str, Any]:
        return {
            "source_type": self.source_type.value,
            "source_id": self.source_id,
            "display_name": self.display_name,
        }

    @abstractmethod
    def retrieve(self, query: str, options: RetrievalOptions) -> List[EvidenceItem]:
        raise NotImplementedError


class WebEvidenceSource(EvidenceSource):
    source_type = EvidenceSourceType.WEB

    def __init__(
        self,
        search_client: SearchClient,
        *,
        official_domains: Optional[Dict[str, Any]] = None,
        official_resolver: Any = None,
    ) -> None:
        self.search_client = search_client
        self.official_domains = official_domains or {}
        self.official_resolver = official_resolver
        self.source_id = getattr(search_client, "source_id", "web")
        self.display_name = getattr(search_client, "display_name", "Web Search")

    def hit_to_item(
        self,
        hit: SearchHit,
        *,
        rank: Optional[int] = None,
        score: Optional[float] = None,
        provenance: Optional[Dict[str, Any]] = None,
        tier_entities: Optional[List[str]] = None,
    ) -> EvidenceItem:
        metadata = {
            "search_hit": asdict(hit),
            "display_name": self.display_name,
            "canonical_reference": canonical_reference(hit.url),
        }
        metadata.update(provenance or {})
        if metadata.get("source_tier") not in {
            "official",
            "first_party",
            "authoritative",
        }:
            metadata["source_tier"] = classify_web_source_tier(
                hit.url,
                entities=tier_entities,
                official_domains=self.official_domains,
                resolver=self.official_resolver,
            )
        if metadata.get("source_tier") == "excluded":
            metadata["exclude_from_evidence"] = True
        official_target = official_entity_for_url(
            hit.url,
            entities=tier_entities,
            official_domains=self.official_domains,
            resolver=self.official_resolver,
        )
        if official_target:
            metadata["official_target"] = official_target
        return EvidenceItem(
            source_type=self.source_type.value,
            source_id=self.source_id,
            title=hit.title or hit.url or "Untitled",
            content=hit.snippet or hit.title or hit.url,
            reference=hit.url,
            snippet=hit.snippet or hit.title or hit.url,
            metadata=metadata,
            score=score,
            rank=rank,
        )

    def hits_to_items(
        self,
        hits: List[SearchHit],
        *,
        provenance: Optional[Dict[str, Any]] = None,
        tier_entities: Optional[List[str]] = None,
    ) -> List[EvidenceItem]:
        items: List[EvidenceItem] = []
        for index, hit in enumerate(hits, start=1):
            item = self.hit_to_item(
                hit,
                rank=index,
                provenance=provenance,
                tier_entities=tier_entities,
            )
            if not item.metadata.get("exclude_from_evidence"):
                items.append(item)
        return items

    def retrieve(self, query: str, options: RetrievalOptions) -> List[EvidenceItem]:
        hits = self.search_client.search(
            query,
            num_results=max(1, int(options.num_results)),
            per_source_limit=options.per_source_limit,
            freshness=options.freshness,
            date_restrict=options.date_restrict,
        )
        metadata = options.metadata or {}
        tier_entities = metadata.get("source_tier_entities") or []
        return self.hits_to_items(
            hits,
            provenance=_plan_provenance(options),
            tier_entities=list(tier_entities) if isinstance(tier_entities, list) else [],
        )


class LocalEvidenceSource(EvidenceSource):
    source_type = EvidenceSourceType.LOCAL

    def __init__(
        self,
        *,
        vector_store: Optional[LangChainVectorStore] = None,
        data_path: Optional[str] = None,
        embedding_model: Optional[str] = None,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.vector_store = vector_store
        self.data_path = data_path
        self.embedding_model = embedding_model
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.config = config or {}
        self._loaded = vector_store is not None
        self.source_id = os.path.abspath(data_path) if data_path else "local_docs"
        self.display_name = "Local Documents"

    def is_available(self) -> bool:
        if self.vector_store is not None and self.vector_store.is_ready:
            return True
        return has_indexable_local_documents(self.data_path)

    def _ensure_store(self) -> Optional[LangChainVectorStore]:
        if self.vector_store is not None and self.vector_store.is_ready:
            return self.vector_store
        if not has_indexable_local_documents(self.data_path):
            return None
        if self.vector_store is None:
            from langchain.langchain_support import LangChainVectorStore

            self.vector_store = LangChainVectorStore(
                model_name=self.embedding_model,
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
                config=self.config,
            )
        if not self._loaded:
            self.vector_store.index_from_directory(self.data_path)
            self._loaded = True
        return self.vector_store if self.vector_store.is_ready else None

    def retrieve(self, query: str, options: RetrievalOptions) -> List[EvidenceItem]:
        store = self._ensure_store()
        if store is None:
            return []

        docs_with_scores = store.search_with_scores(query, k=max(1, int(options.num_results)))
        items: List[EvidenceItem] = []
        for index, (doc, score) in enumerate(docs_with_scores, start=1):
            source = doc.source or f"Document {index}"
            items.append(
                EvidenceItem(
                    source_type=self.source_type.value,
                    source_id=self.source_id,
                    title=source,
                    content=doc.content,
                    reference=source,
                    snippet=_preview_text(doc.content),
                    metadata={
                        "source": source,
                        "canonical_reference": canonical_reference(source),
                        **_plan_provenance(options),
                    },
                    score=score,
                    rank=index,
                )
            )
        return items


class DomainEvidenceSource(EvidenceSource):
    source_type = EvidenceSourceType.DOMAIN

    def __init__(self, source_selector: Optional[Any] = None) -> None:
        self.source_selector = source_selector
        self.source_id = "domain_api"
        self.display_name = "Domain API"

    def describe_with_domain(self, domain: Optional[str]) -> Dict[str, Any]:
        payload = self.describe()
        if domain:
            payload["domain"] = domain
            payload["source_id"] = f"domain:{domain}"
        return payload

    def _coerce_domain_result(self, query: str, options: RetrievalOptions) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        metadata = options.metadata or {}
        domain = metadata.get("domain")
        domain_result = metadata.get("domain_result")
        if domain_result or not self.source_selector:
            return domain, domain_result

        timing_recorder = options.timing_recorder
        domain, _ = self.source_selector.select_sources(query, timing_recorder=timing_recorder)
        domain_result = self.source_selector.fetch_domain_data(query, domain, timing_recorder=timing_recorder)
        return domain, domain_result

    def retrieve(self, query: str, options: RetrievalOptions) -> List[EvidenceItem]:
        domain, domain_result = self._coerce_domain_result(query, options)
        metadata = options.metadata or {}
        extra_context = str(metadata.get("extra_context") or "").strip()

        if not domain_result and not extra_context:
            return []

        answer = str((domain_result or {}).get("answer") or extra_context or "").strip()
        raw_data = (domain_result or {}).get("data")
        if not answer and raw_data is None:
            return []

        if not answer and raw_data is not None:
            answer = json.dumps(raw_data, ensure_ascii=False, indent=2)

        source_id = f"domain:{domain or 'context'}"
        reference = str((domain_result or {}).get("endpoint") or (domain_result or {}).get("provider") or source_id)
        title = f"{domain or 'domain'} evidence"
        payload = {
            "domain": domain,
            "provider": (domain_result or {}).get("provider"),
            "handled": (domain_result or {}).get("handled"),
            "continue_search": (domain_result or {}).get("continue_search"),
            "data": raw_data,
            "canonical_reference": canonical_reference(reference),
        }
        payload.update(_plan_provenance(options))
        return [
            EvidenceItem(
                source_type=self.source_type.value,
                source_id=source_id,
                title=title,
                content=answer,
                reference=reference,
                snippet=_preview_text(answer),
                metadata=payload,
                rank=1,
            )
        ]
