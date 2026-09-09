"""Local RAG chunk-size / overlap grid search with gold-chunk metrics (D5).

Indexes a fixed corpus under every ``(chunk_size, chunk_overlap)`` pair and,
for each ``k`` in ``--top-k``, reports document- and chunk-level retrieval
metrics against ``dataset/local_chunk_gold.csv``::

    python tests/local_chunk_grid_search.py --data-path tests/fixtures/local_corpus \\
        --dataset-file dataset/local_chunk_gold.csv --top-k 3,5 --output-file runtime/quality/<run>/local_rag_eval.json

Metrics per setting and ``k``:

* ``doc_hit_at_k`` / ``mrr`` -- gold document among the top-k sources (legacy).
* ``chunk_hit_at_k`` / ``mrr_chunk`` -- a top-k chunk contains a gold span
  (whitespace-normalised substring or token overlap >= 0.8).
* ``context_recall`` -- share of a question's gold spans covered by top-k chunks.
* ``score_margin`` -- FAISS L2 distance of the best gold chunk minus the best
  non-gold chunk (smaller distance = more relevant, so **negative is good**).
* ``abstention_candidates`` -- minimum top-k distance of ``is_absent`` questions,
  kept for a later abstention-threshold analysis.
* ``cross_lingual_hit`` -- ``chunk_hit_at_k`` restricted to ``language`` values
  containing ``->`` (question and document languages differ).

The legacy ``tests/search_quality_minimal_dataset.csv`` layout (``category`` +
``gold_doc_id``) is still accepted; it only yields document-level metrics.
Only embedding requests are made; no LLM is involved.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.quality.local_gold_check import span_contained, split_spans  # noqa: E402

TOOL_K = 3  # ``local_docs`` retrieves k=3; report it alongside k=5.


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a local document chunk-size/chunk-overlap retrieval experiment."
    )
    parser.add_argument("--data-path", required=True, help="Directory containing the local documents to index.")
    parser.add_argument(
        "--dataset-file",
        default="dataset/local_chunk_gold.csv",
        help="Gold CSV (qid, query, gold_doc_id, gold_span, is_absent, language) or the legacy minimal dataset.",
    )
    parser.add_argument("--config", default=None, help="Optional path to config.json. Defaults to NLP_CONFIG_PATH env or ./config.json.")
    parser.add_argument("--category", default="local_rag", help="Category filter for the legacy minimal dataset layout.")
    parser.add_argument("--chunk-sizes", default="300,500,800,1000,1500", help="Comma-separated chunk sizes to test.")
    parser.add_argument("--chunk-overlaps", default="0,50,100,150,200,300", help="Comma-separated chunk overlaps to test.")
    parser.add_argument("--top-k", default=f"{TOOL_K},5", help="Comma-separated k values to report (default 3,5).")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit on the number of evaluated queries.")
    parser.add_argument("--embedding-model", default=None, help="Optional embedding model override passed to LangChainVectorStore.")
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


def evaluate_setting(
    *,
    data_path: str,
    queries: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]],
    embedding_model: Optional[str],
    chunk_size: int,
    chunk_overlap: int,
    top_ks: Sequence[int],
    store_factory: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Index once, then score every query for each ``k``."""
    if store_factory is None:
        from langchain.langchain_support import LangChainVectorStore

        store_factory = LangChainVectorStore
    index_start = time.perf_counter()
    store = store_factory(
        model_name=embedding_model,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        config=config,
    )
    chunk_count = store.index_from_directory(data_path)
    index_ms = (time.perf_counter() - index_start) * 1000
    max_k = max(top_ks)

    per_k: Dict[int, Dict[str, Any]] = {
        k: {"doc_hits": 0, "doc_rr": 0.0, "chunk_hits": 0, "chunk_rr": 0.0, "recalls": [], "margins": [], "abstention": [],
            "cross_hits": 0, "cross_total": 0, "answerable": 0}
        for k in top_ks
    }
    total_query_ms = 0.0
    query_results: List[Dict[str, Any]] = []

    for row in queries:
        search_start = time.perf_counter()
        scored = store.search_with_scores(row["query"], k=max_k)
        query_ms = (time.perf_counter() - search_start) * 1000
        total_query_ms += query_ms
        ranked = [
            {
                "rank": index,
                "source": doc.source or "",
                "score": float(score),
                "gold_spans": chunk_is_gold(doc.content or "", doc.source or "", row),
            }
            for index, (doc, score) in enumerate(scored, start=1)
        ]
        row_result: Dict[str, Any] = {
            "id": row["id"],
            "query": row["query"],
            "gold_doc_id": row["gold_doc_id"],
            "is_absent": row["is_absent"],
            "language": row["language"],
            "query_ms": round(query_ms, 3),
            "retrieved": [{"rank": item["rank"], "source": item["source"], "score": round(item["score"], 4), "gold": bool(item["gold_spans"])} for item in ranked],
            "by_k": {},
        }
        for k in top_ks:
            top = ranked[:k]
            stats = per_k[k]
            entry: Dict[str, Any] = {}
            if row["is_absent"]:
                min_distance = min((item["score"] for item in top), default=None)
                stats["abstention"].append(min_distance)
                entry["abstention_min_distance"] = min_distance
            else:
                stats["answerable"] += 1
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
                entry.update(doc_rank=doc_rank, chunk_rank=chunk_rank, context_recall=recall, score_margin=margin)
            row_result["by_k"][str(k)] = entry
        query_results.append(row_result)

    query_count = len(queries)
    metrics_by_k: Dict[str, Any] = {}
    for k in top_ks:
        stats = per_k[k]
        answerable = stats["answerable"]
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
            "cross_lingual_hit": (stats["cross_hits"] / stats["cross_total"]) if stats["cross_total"] else None,
            "answerable_queries": answerable,
        }
    primary = metrics_by_k[str(top_ks[0])]
    return {
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


def render_table(results: List[Dict[str, Any]], top_ks: Sequence[int]) -> str:
    header = "chunk_size\tchunk_overlap\tchunks\tindex_ms\tavg_query_ms"
    for k in top_ks:
        header += f"\tdoc_hit@{k}\tmrr@{k}\tchunk_hit@{k}\tmrr_chunk@{k}\tctx_recall@{k}\tmargin_med@{k}"
    lines = [header]
    for item in results:
        line = f"{item['chunk_size']}\t{item['chunk_overlap']}\t{item['chunk_count']}\t{item['index_ms']:.1f}\t{item['avg_query_ms']:.1f}"
        for k in top_ks:
            block = item["by_k"][str(k)]
            def fmt(value: Any) -> str:
                return "-" if value is None else f"{value:.4f}"
            line += f"\t{fmt(block['doc_hit_at_k'])}\t{fmt(block['mrr'])}\t{fmt(block['chunk_hit_at_k'])}\t{fmt(block['mrr_chunk'])}\t{fmt(block['context_recall'])}\t{fmt(block['score_margin_median'])}"
        lines.append(line)
    return "\n".join(lines)


def rank_results(results: List[Dict[str, Any]], primary_k: int) -> None:
    def key(item: Dict[str, Any]) -> Tuple[float, float, float, float]:
        block = item["by_k"][str(primary_k)]
        chunk_hit = block["chunk_hit_at_k"] if block["chunk_hit_at_k"] is not None else block["doc_hit_at_k"] or 0.0
        mrr = block["mrr_chunk"] if block["mrr_chunk"] is not None else block["mrr"] or 0.0
        return (-float(chunk_hit or 0.0), -float(mrr or 0.0), float(item["avg_query_ms"]), float(item["index_ms"]))

    results.sort(key=key)


def main() -> int:
    args = parse_args()
    top_ks = parse_int_list(args.top_k, "top_k")
    if any(k <= 0 for k in top_ks):
        raise SystemExit("'--top-k' values must be greater than 0.")

    chunk_sizes = parse_int_list(args.chunk_sizes, "chunk_sizes")
    chunk_overlaps = parse_int_list(args.chunk_overlaps, "chunk_overlaps")
    config = load_config(args.config)
    queries = load_queries(args.dataset_file, category=args.category, limit=args.limit)

    if not queries:
        raise SystemExit("No matching local queries found in the dataset.")

    results: List[Dict[str, Any]] = []
    for chunk_size in chunk_sizes:
        for chunk_overlap in chunk_overlaps:
            if chunk_overlap >= chunk_size:
                continue
            results.append(
                evaluate_setting(
                    data_path=args.data_path,
                    queries=queries,
                    config=config,
                    embedding_model=args.embedding_model,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    top_ks=top_ks,
                )
            )

    if not results:
        raise SystemExit("No valid (chunk_size, chunk_overlap) combinations to evaluate.")

    rank_results(results, top_ks[0])
    embedding = (config.get("embeddings") or {}) if isinstance(config, dict) else {}
    report = {
        "data_path": os.path.abspath(args.data_path),
        "dataset_file": os.path.abspath(args.dataset_file),
        "embedding_model": args.embedding_model or embedding.get("model"),
        "embedding_provider": embedding.get("provider"),
        "top_ks": list(top_ks),
        "query_count": len(queries),
        "absent_queries": sum(1 for row in queries if row["is_absent"]),
        "best": results[0],
        "default_setting_rank": next(
            (index for index, item in enumerate(results, start=1) if item["chunk_size"] == 1000 and item["chunk_overlap"] == 200),
            None,
        ),
        "results": results,
        "score_semantics": "FAISS L2 distance: smaller is more relevant; score_margin = best gold distance - best non-gold distance (negative is good).",
    }

    if args.output_format == "json":
        print(json.dumps({key: value for key, value in report.items() if key != "results"}, ensure_ascii=False, indent=2))
    else:
        print(render_table(results, top_ks))
        if args.details:
            for item in results:
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
