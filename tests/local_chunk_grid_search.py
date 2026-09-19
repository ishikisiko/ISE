"""Local RAG chunk-size / overlap / embedding-model grid search with gold-chunk metrics (D5).

Indexes a fixed corpus under every ``(embedding model, chunk_size, chunk_overlap)``
triple and, for each ``k`` in ``--top-k``, reports document- and chunk-level
retrieval metrics against ``dataset/local_chunk_gold.csv``::

    python tests/local_chunk_grid_search.py --data-path tests/fixtures/local_corpus \\
        --dataset-file dataset/local_chunk_gold.csv --top-k 3,5 --output-file runtime/quality/<run>/local_rag_eval.json

    # several embedding models on the same grid, one JSON with a model x setting matrix
    python tests/local_chunk_grid_search.py --data-path tests/fixtures/local_corpus,tests/fixtures/local_corpus_ext \\
        --dataset-file dataset/local_chunk_gold.csv,dataset/local_chunk_gold_ext.csv \\
        --embedding-models qwen3.7-text-embedding,qwen3.7-text-embedding-flash,huggingface:intfloat/multilingual-e5-small \\
        --rerank-model qwen3-rerank --rerank-candidates 10

Model specs are ``[provider:]model``. Without a provider the ``embeddings`` block
of config.json is used (only the model name is overridden); ``huggingface:<repo>``
builds a local sentence-transformers embedder with normalised vectors and, for
E5-style repos, the ``query: `` / ``passage: `` prompts.

Metrics per model, setting and ``k``:

* ``doc_hit_at_k`` / ``mrr`` -- gold document among the top-k sources (legacy).
* ``chunk_hit_at_k`` / ``mrr_chunk`` -- a top-k chunk contains a gold span
  (whitespace-normalised substring or token overlap >= 0.8).
* ``context_recall`` -- share of a question's gold spans covered by top-k chunks.
* ``score_margin`` -- FAISS L2 distance of the best gold chunk minus the best
  non-gold chunk (smaller distance = more relevant, so **negative is good**).
* ``abstention_candidates`` -- minimum top-k distance of ``is_absent`` questions.
* ``absent_auroc`` -- how well the top-1 distance separates absent from
  answerable questions (1.0 = every absent question is farther than every
  answerable one; 0.5 = chance).
* ``absent_reject_at_answerable_recall_95`` -- share of absent questions
  rejected by the distance threshold that still keeps 95 % of answerable
  questions (the operating point a distance-based abstention gate would use).
* ``cross_lingual_hit`` -- ``chunk_hit_at_k`` restricted to ``language`` values
  containing ``->`` (question and document languages differ).

With ``--rerank-model`` each query first retrieves ``--rerank-candidates`` chunks,
reranks them through the configured DashScope-compatible endpoint and keeps the
top ``max(k)``; scores are then ``1 - relevance`` so "smaller is better" holds.

The legacy ``tests/search_quality_minimal_dataset.csv`` layout (``category`` +
``gold_doc_id``) is still accepted; it only yields document-level metrics.
Only embedding (and optional rerank) requests are made; no LLM is involved.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import os
import statistics
import sys
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.quality.local_gold_check import span_contained, split_spans  # noqa: E402

TOOL_K = 3  # ``local_docs`` retrieves k=3; report it alongside k=5.
DEFAULT_SETTING = (1000, 200)  # product default ``localRag.chunk_size / chunk_overlap``
FIXED_SLICE = [(800, 0), (1000, 200)]  # settings compared across models regardless of grid
ANSWERABLE_RECALL_TARGET = 0.95

# ``huggingface:<repo>`` presets: sentence-transformers encode kwargs per model family.
HF_PROMPT_PRESETS: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = [
    # (repo substring, document encode kwargs, query encode kwargs)
    ("multilingual-e5", {"prompt": "passage: "}, {"prompt": "query: "}),
    ("/e5-", {"prompt": "passage: "}, {"prompt": "query: "}),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a local document chunk-size/chunk-overlap/embedding-model retrieval experiment."
    )
    parser.add_argument("--data-path", required=True, help="Directory (or comma-separated directories) containing the local documents to index.")
    parser.add_argument(
        "--dataset-file",
        default="dataset/local_chunk_gold.csv",
        help="Gold CSV (qid, query, gold_doc_id, gold_span, is_absent, language) or the legacy minimal dataset; comma-separated files are concatenated.",
    )
    parser.add_argument("--config", default=None, help="Optional path to config.json. Defaults to NLP_CONFIG_PATH env or ./config.json.")
    parser.add_argument("--category", default="local_rag", help="Category filter for the legacy minimal dataset layout.")
    parser.add_argument("--chunk-sizes", default="300,500,800,1000,1500", help="Comma-separated chunk sizes to test.")
    parser.add_argument("--chunk-overlaps", default="0,50,100,150,200,300", help="Comma-separated chunk overlaps to test.")
    parser.add_argument("--top-k", default=f"{TOOL_K},5", help="Comma-separated k values to report (default 3,5).")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit on the number of evaluated queries.")
    parser.add_argument("--embedding-model", default=None, help="Single embedding model override (legacy; same as --embedding-models with one entry).")
    parser.add_argument("--embedding-models", default=None, help="Comma-separated '[provider:]model' specs evaluated on the same grid.")
    parser.add_argument("--rerank-model", default=None, help="Rerank each query's candidates with this model (credentials from config.rerank.providers.qwen).")
    parser.add_argument("--rerank-candidates", type=int, default=10, help="Chunks retrieved before reranking (default 10).")
    parser.add_argument("--details", action="store_true", help="Print per-query ranks for each parameter pair.")
    parser.add_argument("--output-file", default=None, help="Optional path to save the experiment report as JSON.")
    parser.add_argument("--output-format", choices=["table", "json"], default="table", help="Console output format.")
    return parser.parse_args()


def load_config(path: Optional[str]) -> Dict[str, Any]:
    config_path = path or os.environ.get("NLP_CONFIG_PATH") or "config.json"
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_int_list(raw_value: str, field: str) -> List[int]:
    values: List[int] = []
    for token in str(raw_value).split(","):
        cleaned = token.strip()
        if not cleaned:
            continue
        try:
            parsed = int(cleaned)
        except ValueError as exc:
            raise ValueError(f"'{field}' contains a non-integer value: {cleaned}") from exc
        if parsed < 0:
            raise ValueError(f"'{field}' cannot contain negative values.")
        values.append(parsed)
    if not values:
        raise ValueError(f"'{field}' must contain at least one integer.")
    return values


def parse_str_list(raw_value: Optional[str]) -> List[str]:
    return [token.strip() for token in str(raw_value or "").split(",") if token.strip()]


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def load_queries(
    dataset_file: str,
    *,
    category: str = "local_rag",
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Load gold rows; supports the gold-chunk CSV and the legacy minimal dataset."""
    queries: List[Dict[str, Any]] = []
    with open(dataset_file, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(line for line in handle if line.strip())
        fieldnames = [name for name in (reader.fieldnames or [])]
        legacy = "category" in fieldnames and "gold_span" not in fieldnames
        for row in reader:
            if legacy and str(row.get("category") or "").strip().lower() != category.strip().lower():
                continue
            query = str(row.get("query") or "").strip()
            gold_doc_id = str(row.get("gold_doc_id") or "").strip()
            is_absent = _truthy(row.get("is_absent"))
            if not query or (not gold_doc_id and not is_absent):
                continue
            queries.append(
                {
                    "id": str(row.get("qid") or row.get("id") or "").strip(),
                    "query": query,
                    "gold_doc_id": gold_doc_id,
                    "gold_spans": split_spans(row.get("gold_span")),
                    "is_absent": is_absent,
                    "language": str(row.get("language") or "").strip(),
                }
            )
            if limit is not None and len(queries) >= limit:
                break
    return queries


def load_query_files(dataset_files: Sequence[str], *, category: str = "local_rag", limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Concatenate several gold files; ``limit`` caps the total."""
    queries: List[Dict[str, Any]] = []
    for dataset_file in dataset_files:
        remaining = None if limit is None else max(limit - len(queries), 0)
        if remaining == 0:
            break
        queries.extend(load_queries(dataset_file, category=category, limit=remaining))
    return queries


def source_matches_gold(source: str, gold_doc_id: str) -> bool:
    source_text = str(source or "").strip()
    gold_text = str(gold_doc_id or "").strip()
    if not source_text or not gold_text:
        return False

    source_lower = source_text.lower()
    gold_lower = gold_text.lower()
    source_base = os.path.basename(source_lower)
    gold_base = os.path.basename(gold_lower)
    return (
        gold_lower in source_lower
        or gold_base in source_base
        or gold_base in source_lower
    )


def first_gold_rank(sources: Iterable[str], gold_doc_id: str) -> Optional[int]:
    for index, source in enumerate(sources, start=1):
        if source_matches_gold(source, gold_doc_id):
            return index
    return None


def chunk_is_gold(content: str, source: str, row: Dict[str, Any]) -> List[int]:
    """Indices of the gold spans this chunk covers (empty = not a gold chunk)."""
    if not row.get("gold_spans"):
        return []
    if row.get("gold_doc_id") and not source_matches_gold(source, row["gold_doc_id"]):
        return []
    return [index for index, span in enumerate(row["gold_spans"]) if span_contained(span, content)[0]]


def _median(values: List[float]) -> Optional[float]:
    return statistics.median(values) if values else None


# ---------------------------------------------------------------------------
# Embedding model specs
# ---------------------------------------------------------------------------

def parse_model_spec(spec: str, base_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """``[provider:]model`` -> ``{"spec", "provider", "model", "config", "model_name"}``.

    ``config`` is what ``LangChainVectorStore`` receives; ``model_name`` the
    override. A bare model keeps the configured provider/credentials.
    """
    cleaned = str(spec or "").strip()
    config = copy.deepcopy(base_config) if isinstance(base_config, dict) else {}
    embeddings = config.get("embeddings") if isinstance(config.get("embeddings"), dict) else {}
    provider, model = "", cleaned
    if ":" in cleaned and not cleaned.startswith(("http://", "https://")):
        head, tail = cleaned.split(":", 1)
        if head.strip().lower() in {"huggingface", "openai_compatible"}:
            provider, model = head.strip().lower(), tail.strip()
    if provider == "huggingface":
        hf: Dict[str, Any] = {"provider": "huggingface", "model": model, "encode_kwargs": {"normalize_embeddings": True}, "query_encode_kwargs": {"normalize_embeddings": True}}
        for needle, doc_kwargs, query_kwargs in HF_PROMPT_PRESETS:
            if needle in model:
                hf["encode_kwargs"].update(doc_kwargs)
                hf["query_encode_kwargs"].update(query_kwargs)
                break
        config["embeddings"] = hf
        return {"spec": cleaned, "provider": "huggingface", "model": model, "config": config, "model_name": model,
                "encode_kwargs": hf["encode_kwargs"], "query_encode_kwargs": hf["query_encode_kwargs"]}
    if provider == "openai_compatible":
        merged = dict(embeddings)
        merged["provider"] = "openai_compatible"
        merged["model"] = model
        config["embeddings"] = merged
        return {"spec": cleaned, "provider": "openai_compatible", "model": model, "config": config, "model_name": model}
    resolved_provider = str(embeddings.get("provider") or ("openai_compatible" if embeddings else "huggingface"))
    resolved_model = model or str(embeddings.get("model") or "")
    return {"spec": cleaned or resolved_model, "provider": resolved_provider, "model": resolved_model, "config": config, "model_name": model or None}


# ---------------------------------------------------------------------------
# Rerank
# ---------------------------------------------------------------------------

def build_reranker(config: Dict[str, Any], model: Optional[str]) -> Optional[Any]:
    """``Qwen3Reranker`` from ``config.rerank.providers.qwen`` with the model overridden."""
    if not model:
        return None
    rerank_cfg = config.get("rerank") if isinstance(config.get("rerank"), dict) else {}
    providers = rerank_cfg.get("providers") if isinstance(rerank_cfg.get("providers"), dict) else {}
    qwen = providers.get("qwen") if isinstance(providers.get("qwen"), dict) else {}
    api_key = str(qwen.get("api_key") or os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit("--rerank-model needs config.rerank.providers.qwen.api_key (or DASHSCOPE_API_KEY).")
    from search.rerank import Qwen3Reranker

    return Qwen3Reranker(api_key, model=model, base_url=qwen.get("base_url") or None, request_timeout=int(qwen.get("timeout") or 15))


def rerank_ranked(reranker: Any, query: str, ranked: List[Dict[str, Any]], *, keep: int) -> Tuple[List[Dict[str, Any]], bool]:
    """Reorder ``ranked`` by rerank relevance; scores become ``1 - relevance``.

    Returns ``(new_ranked, applied)``; on an empty/failed rerank the original
    order is kept and ``applied`` is False.
    """
    if not ranked:
        return ranked, False
    ranking: List[Dict[str, Any]] = []
    for attempt in (1, 2):  # one retry: the endpoint occasionally hits its 15 s read timeout
        try:
            ranking = reranker.rerank_texts(query, [item["content"] for item in ranked])
            break
        except Exception as exc:  # noqa: BLE001 - keep the evaluation going; record it
            sys.stderr.write(f"[grid] rerank failed (attempt {attempt}) for {query[:40]!r}: {exc}\n")
            ranking = []
    if not ranking:
        return ranked[:keep], False
    by_index = {str(index): item for index, item in enumerate(ranked)}
    reordered: List[Dict[str, Any]] = []
    seen = set()
    # The endpoint returns relevance order, but sort defensively (None last).
    ranking = sorted(ranking, key=lambda entry: (entry.get("score") is None, -(entry.get("score") or 0.0)))
    for entry in ranking:
        item = by_index.get(str(entry.get("id")))
        if item is None or entry.get("id") in seen:
            continue
        seen.add(entry.get("id"))
        relevance = entry.get("score")
        new_item = dict(item)
        new_item["rerank_score"] = relevance
        new_item["score"] = (1.0 - float(relevance)) if relevance is not None else float(item["score"])
        reordered.append(new_item)
    for index, item in by_index.items():
        if index not in seen:
            reordered.append(dict(item, rerank_score=None))
    for position, item in enumerate(reordered, start=1):
        item["rank"] = position
    return reordered[:keep], True


# ---------------------------------------------------------------------------
# Absent-question separation
# ---------------------------------------------------------------------------

def absent_separation(answerable: Sequence[float], absent: Sequence[float], *, recall_target: float = ANSWERABLE_RECALL_TARGET) -> Dict[str, Optional[float]]:
    """Distance-based abstention quality.

    ``auroc``: probability that a random absent question's distance exceeds a
    random answerable one's (ties count half). ``reject_at_answerable_recall``:
    threshold = smallest answerable distance that keeps ``recall_target`` of
    answerable questions at or below it; the share of absent questions above
    that threshold is what a distance gate would reject at that recall.
    """
    answerable = [float(v) for v in answerable if v is not None]
    absent = [float(v) for v in absent if v is not None]
    if not answerable or not absent:
        return {"auroc": None, "threshold": None, "reject_at_answerable_recall": None, "answerable_recall_target": recall_target}
    wins = 0.0
    for a in absent:
        for b in answerable:
            if a > b:
                wins += 1.0
            elif a == b:
                wins += 0.5
    auroc = wins / (len(absent) * len(answerable))
    ordered = sorted(answerable)
    keep = max(1, min(len(ordered), math.ceil(recall_target * len(ordered))))
    threshold = ordered[keep - 1]
    rejected = sum(1 for a in absent if a > threshold) / len(absent)
    return {"auroc": round(auroc, 4), "threshold": round(threshold, 4), "reject_at_answerable_recall": round(rejected, 4), "answerable_recall_target": recall_target}


# ---------------------------------------------------------------------------
# Indexing / evaluation
# ---------------------------------------------------------------------------

def index_paths(store: Any, data_paths: Sequence[str]) -> int:
    """Index one directory via ``index_from_directory`` or several via one ``index`` call."""
    paths = [path for path in data_paths if path]
    if len(paths) == 1:
        return store.index_from_directory(paths[0])
    from langchain.langchain_support import LangChainFileReader

    documents: List[Any] = []
    for path in paths:
        documents.extend(LangChainFileReader(path).load())
    return store.index(documents)


def evaluate_setting(
    *,
    data_path: Any,
    queries: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]],
    embedding_model: Optional[str],
    chunk_size: int,
    chunk_overlap: int,
    top_ks: Sequence[int],
    store_factory: Optional[Callable[..., Any]] = None,
    reranker: Optional[Any] = None,
    rerank_candidates: int = 10,
) -> Dict[str, Any]:
    """Index once, then score every query for each ``k``."""
    if store_factory is None:
        from langchain.langchain_support import LangChainVectorStore

        store_factory = LangChainVectorStore
    data_paths = list(data_path) if isinstance(data_path, (list, tuple)) else [data_path]
    index_start = time.perf_counter()
    store = store_factory(
        model_name=embedding_model,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        config=config,
    )
    chunk_count = index_paths(store, data_paths)
    index_ms = (time.perf_counter() - index_start) * 1000
    max_k = max(top_ks)
    retrieve_k = max(max_k, rerank_candidates) if reranker is not None else max_k

    per_k: Dict[int, Dict[str, Any]] = {
        k: {"doc_hits": 0, "doc_rr": 0.0, "chunk_hits": 0, "chunk_rr": 0.0, "recalls": [], "margins": [], "abstention": [],
            "answerable_min": [], "cross_hits": 0, "cross_total": 0, "answerable": 0}
        for k in top_ks
    }
    total_query_ms = 0.0
    rerank_applied = 0
    query_results: List[Dict[str, Any]] = []

    for row in queries:
        search_start = time.perf_counter()
        scored = store.search_with_scores(row["query"], k=retrieve_k)
        ranked = [
            {
                "rank": index,
                "source": doc.source or "",
                "content": doc.content or "",
                "score": float(score),
                "gold_spans": chunk_is_gold(doc.content or "", doc.source or "", row),
            }
            for index, (doc, score) in enumerate(scored, start=1)
        ]
        if reranker is not None:
            ranked, applied = rerank_ranked(reranker, row["query"], ranked, keep=max_k)
            rerank_applied += int(applied)
        query_ms = (time.perf_counter() - search_start) * 1000
        total_query_ms += query_ms
        row_result: Dict[str, Any] = {
            "id": row["id"],
            "query": row["query"],
            "gold_doc_id": row["gold_doc_id"],
            "is_absent": row["is_absent"],
            "language": row["language"],
            "query_ms": round(query_ms, 3),
            "retrieved": [
                {k_: v_ for k_, v_ in (
                    ("rank", item["rank"]), ("source", item["source"]), ("score", round(item["score"], 4)),
                    ("gold", bool(item["gold_spans"])), ("rerank_score", item.get("rerank_score")),
                ) if not (k_ == "rerank_score" and reranker is None)}
                for item in ranked
            ],
            "by_k": {},
        }
        for k in top_ks:
            top = ranked[:k]
            stats = per_k[k]
            entry: Dict[str, Any] = {}
            min_distance = min((item["score"] for item in top), default=None)
            if row["is_absent"]:
                stats["abstention"].append(min_distance)
                entry["abstention_min_distance"] = min_distance
            else:
                stats["answerable"] += 1
                stats["answerable_min"].append(min_distance)
                doc_rank = first_gold_rank([item["source"] for item in top], row["gold_doc_id"]) if row["gold_doc_id"] else None
                chunk_rank = next((item["rank"] for item in top if item["gold_spans"]), None)
                covered = {span for item in top for span in item["gold_spans"]}
                recall = (len(covered) / len(row["gold_spans"])) if row["gold_spans"] else None
                if doc_rank is not None:
                    stats["doc_hits"] += 1
                    stats["doc_rr"] += 1.0 / doc_rank
                if chunk_rank is not None:
                    stats["chunk_hits"] += 1
                    stats["chunk_rr"] += 1.0 / chunk_rank
                if recall is not None:
                    stats["recalls"].append(recall)
                gold_scores = [item["score"] for item in ranked if item["gold_spans"]]
                other_scores = [item["score"] for item in ranked if not item["gold_spans"]]
                margin = (min(gold_scores) - min(other_scores)) if gold_scores and other_scores else None
                if margin is not None:
                    stats["margins"].append(margin)
                if "->" in row["language"]:
                    stats["cross_total"] += 1
                    if chunk_rank is not None:
                        stats["cross_hits"] += 1
                entry.update(doc_rank=doc_rank, chunk_rank=chunk_rank, context_recall=recall, score_margin=margin, min_distance=min_distance)
            row_result["by_k"][str(k)] = entry
        query_results.append(row_result)

    query_count = len(queries)
    metrics_by_k: Dict[str, Any] = {}
    for k in top_ks:
        stats = per_k[k]
        answerable = stats["answerable"]
        separation = absent_separation(stats["answerable_min"], stats["abstention"])
        metrics_by_k[str(k)] = {
            "doc_hit_at_k": round(stats["doc_hits"] / answerable, 4) if answerable else None,
            "mrr": round(stats["doc_rr"] / answerable, 4) if answerable else None,
            "chunk_hit_at_k": round(stats["chunk_hits"] / answerable, 4) if answerable else None,
            "mrr_chunk": round(stats["chunk_rr"] / answerable, 4) if answerable else None,
            "context_recall": round(sum(stats["recalls"]) / len(stats["recalls"]), 4) if stats["recalls"] else None,
            "score_margin_median": _median(stats["margins"]),
            "score_margin_negative_share": (sum(1 for m in stats["margins"] if m < 0) / len(stats["margins"])) if stats["margins"] else None,
            "abstention_candidates": [value for value in stats["abstention"]],
            "abstention_min_distance_median": _median([v for v in stats["abstention"] if v is not None]),
            "answerable_min_distance_median": _median([v for v in stats["answerable_min"] if v is not None]),
            "absent_auroc": separation["auroc"],
            "absent_threshold_at_answerable_recall_95": separation["threshold"],
            "absent_reject_at_answerable_recall_95": separation["reject_at_answerable_recall"],
            "cross_lingual_hit": (stats["cross_hits"] / stats["cross_total"]) if stats["cross_total"] else None,
            "answerable_queries": answerable,
            "absent_queries": len(stats["abstention"]),
        }
    primary = metrics_by_k[str(top_ks[0])]
    result = {
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "chunk_count": chunk_count,
        "query_count": query_count,
        # Legacy headline fields keep the old table shape (first k).
        "doc_hit_at_k": primary["doc_hit_at_k"] if primary["doc_hit_at_k"] is not None else 0.0,
        "mrr": primary["mrr"] if primary["mrr"] is not None else 0.0,
        "chunk_hit_at_k": primary["chunk_hit_at_k"],
        "mrr_chunk": primary["mrr_chunk"],
        "index_ms": round(index_ms, 3),
        "avg_query_ms": round(total_query_ms / query_count, 3) if query_count else 0.0,
        "by_k": metrics_by_k,
        "queries": query_results,
    }
    if reranker is not None:
        result["rerank"] = {"candidates": retrieve_k, "applied_queries": rerank_applied}
    return result


def render_table(results: List[Dict[str, Any]], top_ks: Sequence[int]) -> str:
    header = "chunk_size\tchunk_overlap\tchunks\tindex_ms\tavg_query_ms"
    for k in top_ks:
        header += f"\tdoc_hit@{k}\tmrr@{k}\tchunk_hit@{k}\tmrr_chunk@{k}\tctx_recall@{k}\tmargin_med@{k}\tabsent_auroc@{k}"
    lines = [header]
    for item in results:
        line = f"{item['chunk_size']}\t{item['chunk_overlap']}\t{item['chunk_count']}\t{item['index_ms']:.1f}\t{item['avg_query_ms']:.1f}"
        for k in top_ks:
            block = item["by_k"][str(k)]
            def fmt(value: Any) -> str:
                return "-" if value is None else f"{value:.4f}"
            line += f"\t{fmt(block['doc_hit_at_k'])}\t{fmt(block['mrr'])}\t{fmt(block['chunk_hit_at_k'])}\t{fmt(block['mrr_chunk'])}\t{fmt(block['context_recall'])}\t{fmt(block['score_margin_median'])}\t{fmt(block.get('absent_auroc'))}"
        lines.append(line)
    return "\n".join(lines)


def rank_results(results: List[Dict[str, Any]], primary_k: int) -> None:
    def key(item: Dict[str, Any]) -> Tuple[float, float, float, float]:
        block = item["by_k"][str(primary_k)]
        chunk_hit = block["chunk_hit_at_k"] if block["chunk_hit_at_k"] is not None else block["doc_hit_at_k"] or 0.0
        mrr = block["mrr_chunk"] if block["mrr_chunk"] is not None else block["mrr"] or 0.0
        return (-float(chunk_hit or 0.0), -float(mrr or 0.0), float(item["avg_query_ms"]), float(item["index_ms"]))

    results.sort(key=key)


def setting_rank(results: List[Dict[str, Any]], setting: Tuple[int, int]) -> Optional[int]:
    return next((index for index, item in enumerate(results, start=1) if (item["chunk_size"], item["chunk_overlap"]) == setting), None)


def find_setting(results: List[Dict[str, Any]], setting: Tuple[int, int]) -> Optional[Dict[str, Any]]:
    return next((item for item in results if (item["chunk_size"], item["chunk_overlap"]) == setting), None)


def _slice_metrics(item: Optional[Dict[str, Any]], k: int) -> Optional[Dict[str, Any]]:
    """The cross-model comparison columns for one (model, setting)."""
    if item is None:
        return None
    block = item["by_k"][str(k)]
    return {
        "chunk_hit_at_k": block["chunk_hit_at_k"],
        "mrr_chunk": block["mrr_chunk"],
        "context_recall": block["context_recall"],
        "cross_lingual_hit": block["cross_lingual_hit"],
        "absent_auroc": block.get("absent_auroc"),
        "absent_reject_at_answerable_recall_95": block.get("absent_reject_at_answerable_recall_95"),
        "chunk_count": item["chunk_count"],
        "index_ms": item["index_ms"],
        "avg_query_ms": item["avg_query_ms"],
    }


def build_model_summary(models: List[Dict[str, Any]], primary_k: int) -> Dict[str, Any]:
    """``matrix`` (one row per model: best + fixed-slice settings) and ``fixed_slice`` (setting -> model -> metrics)."""
    matrix: List[Dict[str, Any]] = []
    fixed_slice: Dict[str, Dict[str, Any]] = {f"{cs}/{co}": {} for cs, co in FIXED_SLICE}
    for entry in models:
        results = entry["results"]
        best = results[0] if results else None
        row: Dict[str, Any] = {
            "model": entry["spec"],
            "provider": entry["provider"],
            "best_setting": f"{best['chunk_size']}/{best['chunk_overlap']}" if best else None,
            "best": _slice_metrics(best, primary_k),
            "default_setting_rank": entry["default_setting_rank"],
            "settings_evaluated": len(results),
            "index_ms_total": entry["index_ms_total"],
            "embedding_calls": entry["embedding_calls"],
        }
        for cs, co in FIXED_SLICE:
            metrics = _slice_metrics(find_setting(results, (cs, co)), primary_k)
            row[f"at_{cs}_{co}"] = metrics
            if metrics is not None:
                fixed_slice[f"{cs}/{co}"][entry["spec"]] = metrics
        matrix.append(row)
    return {"matrix": matrix, "fixed_slice": fixed_slice}


def run_model(
    *,
    model_spec: Dict[str, Any],
    data_paths: Sequence[str],
    queries: List[Dict[str, Any]],
    chunk_sizes: Sequence[int],
    chunk_overlaps: Sequence[int],
    top_ks: Sequence[int],
    reranker: Optional[Any],
    rerank_candidates: int,
    store_factory: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for chunk_size in chunk_sizes:
        for chunk_overlap in chunk_overlaps:
            if chunk_overlap >= chunk_size:
                continue
            started = time.perf_counter()
            results.append(
                evaluate_setting(
                    data_path=list(data_paths),
                    queries=queries,
                    config=model_spec["config"],
                    embedding_model=model_spec["model_name"],
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    top_ks=top_ks,
                    store_factory=store_factory,
                    reranker=reranker,
                    rerank_candidates=rerank_candidates,
                )
            )
            sys.stderr.write(
                f"[grid] {model_spec['spec']} {chunk_size}/{chunk_overlap}: chunks={results[-1]['chunk_count']} "
                f"hit@{top_ks[0]}={results[-1]['chunk_hit_at_k']} ({time.perf_counter() - started:.1f}s)\n"
            )
    if not results:
        raise SystemExit("No valid (chunk_size, chunk_overlap) combinations to evaluate.")
    rank_results(results, top_ks[0])
    return {
        "spec": model_spec["spec"],
        "model": model_spec["model"],
        "provider": model_spec["provider"],
        "encode_kwargs": model_spec.get("encode_kwargs"),
        "query_encode_kwargs": model_spec.get("query_encode_kwargs"),
        "results": results,
        "best": results[0],
        "default_setting_rank": setting_rank(results, DEFAULT_SETTING),
        "index_ms_total": round(sum(item["index_ms"] for item in results), 3),
        "embedding_calls": {
            "chunks_embedded": sum(item["chunk_count"] for item in results),
            "queries_embedded": sum(item["query_count"] for item in results),
        },
    }


def main() -> int:
    args = parse_args()
    top_ks = parse_int_list(args.top_k, "top_k")
    if any(k <= 0 for k in top_ks):
        raise SystemExit("'--top-k' values must be greater than 0.")
    if args.rerank_candidates <= 0:
        raise SystemExit("'--rerank-candidates' must be greater than 0.")

    chunk_sizes = parse_int_list(args.chunk_sizes, "chunk_sizes")
    chunk_overlaps = parse_int_list(args.chunk_overlaps, "chunk_overlaps")
    config = load_config(args.config)
    data_paths = parse_str_list(args.data_path)
    dataset_files = parse_str_list(args.dataset_file)
    queries = load_query_files(dataset_files, category=args.category, limit=args.limit)

    if not queries:
        raise SystemExit("No matching local queries found in the dataset.")

    specs = parse_str_list(args.embedding_models)
    if args.embedding_model:
        specs = [args.embedding_model] + [spec for spec in specs if spec != args.embedding_model]
    if not specs:
        specs = [""]  # configured model
    model_specs = [parse_model_spec(spec, config) for spec in specs]
    reranker = build_reranker(config, args.rerank_model)

    models = [
        run_model(
            model_spec=model_spec,
            data_paths=data_paths,
            queries=queries,
            chunk_sizes=chunk_sizes,
            chunk_overlaps=chunk_overlaps,
            top_ks=top_ks,
            reranker=reranker,
            rerank_candidates=args.rerank_candidates,
        )
        for model_spec in model_specs
    ]
    summary = build_model_summary(models, top_ks[0])
    primary = models[0]
    report = {
        "data_path": os.path.abspath(data_paths[0]),
        "data_paths": [os.path.abspath(path) for path in data_paths],
        "dataset_file": os.path.abspath(dataset_files[0]),
        "dataset_files": [os.path.abspath(path) for path in dataset_files],
        # Primary (first) model keeps the single-model layout for existing readers.
        "embedding_model": primary["model"],
        "embedding_provider": primary["provider"],
        "top_ks": list(top_ks),
        "query_count": len(queries),
        "absent_queries": sum(1 for row in queries if row["is_absent"]),
        "chunk_sizes": list(chunk_sizes),
        "chunk_overlaps": list(chunk_overlaps),
        "rerank": {"model": args.rerank_model, "candidates": args.rerank_candidates} if args.rerank_model else None,
        "best": primary["best"],
        "default_setting_rank": primary["default_setting_rank"],
        "results": primary["results"],
        "models": models,
        "matrix": summary["matrix"],
        "fixed_slice": summary["fixed_slice"],
        "score_semantics": (
            "1 - rerank relevance: smaller is more relevant; score_margin = best gold score - best non-gold score (negative is good)."
            if args.rerank_model else
            "FAISS L2 distance: smaller is more relevant; score_margin = best gold distance - best non-gold distance (negative is good)."
        ),
    }

    if args.output_format == "json":
        print(json.dumps({key: value for key, value in report.items() if key not in {"results", "models"}}, ensure_ascii=False, indent=2))
    else:
        for entry in models:
            print(f"== model {entry['spec']} ({entry['provider']}) default_rank={entry['default_setting_rank']}")
            print(render_table(entry["results"], top_ks))
            if args.details:
                for item in entry["results"]:
                    print(f"== chunk_size={item['chunk_size']} overlap={item['chunk_overlap']}")
                    for query in item["queries"]:
                        print(f"  - {query['id'] or '?'} {query['by_k']} gold={query['gold_doc_id']}")

    if args.output_file:
        parent = os.path.dirname(args.output_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.output_file, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
