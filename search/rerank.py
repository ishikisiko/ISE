"""Utilities for reranking search hits using external APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import requests

from search.search import SearchHit


@dataclass
class RerankedHit:
    """Container for a reranked search hit and its relevance score."""

    hit: SearchHit
    score: Optional[float]


class BaseReranker:
    """Abstract base class for rerankers."""

    def rerank(self, query: str, hits: List[SearchHit]) -> List[RerankedHit]:
        raise NotImplementedError


class Qwen3Reranker(BaseReranker):
    """Client for the DashScope Qwen3 rerank endpoint."""

    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "qwen3-rerank",
        base_url: Optional[str] = None,
        request_timeout: int = 15,
    ) -> None:
        if not api_key:
            raise ValueError("DashScope API key is required for Qwen3 reranking.")

        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.request_timeout = request_timeout

        self._endpoint = f"{self.base_url}/text-rerank/text-rerank"
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def rerank(self, query: str, hits: List[SearchHit]) -> List[RerankedHit]:
        if not hits:
            return []

        # 将文档转换为文本列表
        doc_texts = []
        for idx, hit in enumerate(hits):
            snippet = hit.snippet or ""
            title = hit.title or ""
            text = f"{title}\n{hit.url}\n{snippet}".strip()
            if not text:
                text = hit.url or f"Result {idx + 1}"
            doc_texts.append(text)

        ranking = self.rerank_texts(query, doc_texts)
        if not ranking:
            # Empty or unexpected payload: fall back to original order
            return [RerankedHit(hit=hit, score=None) for hit in hits]

        indexed_hits = {str(idx): hit for idx, hit in enumerate(hits)}
        reranked: List[RerankedHit] = []
        used_ids = set()

        for entry in ranking:
            doc_id = entry.get("id")
            if doc_id is None:
                continue
            if doc_id in used_ids:
                continue
            hit = indexed_hits.get(str(doc_id))
            if not hit:
                continue
            reranked.append(
                RerankedHit(hit=hit, score=entry.get("score"))
            )
            used_ids.add(doc_id)

        # Add any hits the API did not return so we never lose context entirely.
        if len(reranked) < len(hits):
            remaining = [hit for idx, hit in indexed_hits.items() if idx not in used_ids]
            reranked.extend(RerankedHit(hit=hit, score=None) for hit in remaining)

        return reranked

    def rerank_texts(self, query: str, doc_texts: List[str]) -> List[dict]:
        """Rerank raw passages; returns ``[{"id": "<input index>", "score": float|None}, ...]``
        in relevance order (empty on an unexpected payload). Used by both
        :meth:`rerank` and the local-RAG evaluation, which has chunks, not hits.
        """
        if not doc_texts:
            return []

        payload = {
            "model": self.model,
            "input": {
                "query": query,
                "documents": list(doc_texts),
            },
            "parameters": {
                "return_documents": False,
                "top_n": len(doc_texts),
            }
        }

        try:
            response = requests.post(
                self._endpoint,
                headers=self._headers,
                json=payload,
                timeout=self.request_timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            # 添加响应内容用于调试
            error_detail = str(exc)
            if hasattr(exc, 'response') and exc.response is not None:
                try:
                    error_body = exc.response.json()
                    error_detail = f"{exc} | Response: {error_body}"
                except:
                    error_detail = f"{exc} | Response text: {exc.response.text[:500]}"
            raise RuntimeError(f"Qwen3 rerank request failed: {error_detail}") from exc

        data = response.json()
        return self._extract_ranking(data)

    @staticmethod
    def _extract_ranking(payload: dict) -> List[dict]:
        """Best-effort parsing across possible DashScope schemas."""

        candidates = [
            payload.get("output", {}).get("results"),
            payload.get("output", {}).get("rankings"),
            payload.get("data", {}).get("results"),
            payload.get("results"),
            payload.get("data", {}).get("documents"),
            payload.get("data", {}).get("items"),
        ]

        for candidate in candidates:
            if not candidate:
                continue
            parsed = []
            for item in candidate:
                if not isinstance(item, dict):
                    continue
                document = item.get("document") or {}
                # 尝试多种可能的 ID 字段名
                doc_id = item.get("index")
                if doc_id is None:
                    doc_id = document.get("index")
                if doc_id is None:
                    doc_id = document.get("id")
                if doc_id is None:
                    doc_id = item.get("document_id")
                if doc_id is None:
                    doc_id = item.get("id")
                score = (
                    item.get("relevance_score")
                    or item.get("score")
                    or document.get("score")
                )
                if doc_id is None:
                    continue
                parsed_score: Optional[float]
                if score is None:
                    parsed_score = None
                else:
                    try:
                        parsed_score = float(score)
                    except (TypeError, ValueError):
                        parsed_score = None
                parsed.append({"id": str(doc_id), "score": parsed_score})
            if parsed:
                return parsed
        return []
