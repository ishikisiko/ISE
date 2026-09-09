from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from datetime import datetime, timezone
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

# Add project directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_JUDGMENT_MODE = "detailed"
TOP3_ONLY_JUDGMENT_MODE = "top3_only"
SUPPORTED_JUDGMENT_MODES = {DEFAULT_JUDGMENT_MODE, TOP3_ONLY_JUDGMENT_MODE}
GOLD_DOC_K_VALUES = (3, 5)
TIER_K_VALUES = (3, 5)
MIN_CATEGORY_SAMPLES = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect and evaluate search result quality for a batch of queries."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser(
        "collect",
        help="Run the search pipeline for queries and export top results for manual judgment.",
    )
    collect_parser.add_argument(
        "--queries-file",
        default=None,
        help="UTF-8 text file with one query per line. Blank lines and # comments are ignored.",
    )
    collect_parser.add_argument(
        "--dataset-file",
        default=None,
        help="CSV with qid/query (and optional category) columns; alternative to --queries-file.",
    )
    collect_parser.add_argument(
        "--output-file",
        required=True,
        help="Where to save the collected search result file.",
    )
    collect_parser.add_argument(
        "--gold-doc-file",
        default=None,
        help="CSV with query,gold_doc_url; gold hits are pre-filled into judgment.relevant_urls for review.",
    )
    collect_parser.add_argument(
        "--all-providers",
        action="store_true",
        help="Query every configured search provider independently (no LLM, no loop) and record per-provider results.",
    )
    collect_parser.add_argument(
        "--autonomy",
        choices=["guided", "autonomous"],
        default=None,
        help="Effective autonomy mode for the loop run (default: config).",
    )
    collect_parser.add_argument(
        "--config",
        default=None,
        help="Optional path to config.json. Defaults to NLP_CONFIG_PATH env or ./config.json.",
    )
    collect_parser.add_argument(
        "--data-path",
        default="./data",
        help="Directory containing local documents. Only used if the pipeline reads local docs.",
    )
    collect_parser.add_argument(
        "--num-results",
        type=int,
        default=5,
        help="How many search hits to export per query.",
    )
    collect_parser.add_argument(
        "--max-tokens",
        type=int,
        default=512,
        help="Maximum generation tokens for the answering step during collection.",
    )
    collect_parser.add_argument(
        "--temperature",
        type=float,
        default=0.1,
        help="Sampling temperature used when the orchestrator generates answers.",
    )
    collect_parser.add_argument(
        "--provider",
        type=str,
        help="Override the default LLM provider or model for this run.",
    )
    collect_parser.add_argument(
        "--model",
        type=str,
        help="Override the configured model for this run.",
    )
    collect_parser.add_argument(
        "--disable-rerank",
        action="store_true",
        help="Skip reranking even if rerank configuration exists.",
    )
    collect_parser.add_argument(
        "--show-timings",
        action="store_true",
        help="Include timing metadata in the collected payload.",
    )
    collect_parser.add_argument(
        "--force-search",
        action="store_true",
        help="Force search for every query during collection. Useful for search-result judging sets.",
    )

    loop_audit_parser = subparsers.add_parser(
        "loop-audit",
        help="Run queries through the unified LangGraph loop and collect termination verdicts.",
    )
    loop_audit_parser.add_argument(
        "--queries-file",
        required=True,
        help="UTF-8 text file with one query per line. Blank lines and # comments are ignored.",
    )
    loop_audit_parser.add_argument(
        "--output-file",
        default=None,
        help="Optional path to save the loop audit report as JSON.",
    )
    loop_audit_parser.add_argument(
        "--config",
        default=None,
        help="Optional path to config.json. Defaults to NLP_CONFIG_PATH env or ./config.json.",
    )
    loop_audit_parser.add_argument(
        "--max-iterations",
        type=int,
        default=4,
        help="Maximum ReAct iterations for the unified loop.",
    )

    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="Read manually judged search results and compute retrieval quality metrics.",
    )
    evaluate_parser.add_argument(
        "--annotations-file",
        required=True,
        help="Collected JSON file after manual judgment fields have been filled in.",
    )
    evaluate_parser.add_argument(
        "--output-file",
        default=None,
        help="Optional path to save the evaluation report as JSON.",
    )
    evaluate_parser.add_argument(
        "--print-details",
        action="store_true",
        help="Print per-query metric details in addition to the summary.",
    )
    evaluate_parser.add_argument(
        "--gold-doc-file",
        default=None,
        help="CSV with query,gold_doc_url used for gold_doc_recall_at_k (matched by normalized query).",
    )
    evaluate_parser.add_argument(
        "--config",
        default=None,
        help="Optional config.json for tier classification (pins / denylist only; the resolver never goes online here).",
    )
    evaluate_parser.add_argument(
        "--relevance-annotations",
        default=None,
        help="Annotated collect file whose judgments supply relevance for an --all-providers collect file.",
    )

    dataset_parser = subparsers.add_parser(
        "dataset",
        help="Inspect or export grouped evaluation dataset rows by category or query id.",
    )
    dataset_parser.add_argument(
        "--dataset-file",
        default="tests/search_quality_minimal_dataset.csv",
        help="CSV dataset file to inspect.",
    )
    dataset_parser.add_argument(
        "--category",
        action="append",
        default=[],
        help="Filter by category. Can be repeated.",
    )
    dataset_parser.add_argument(
        "--query-id",
        action="append",
        default=[],
        help="Filter by query id such as Q016. Can be repeated.",
    )
    dataset_parser.add_argument(
        "--layer",
        action="append",
        default=[],
        help="Filter by dataset layer such as route_intent, full_text_trigger, gold_doc, gold_chunk, final_answer.",
    )
    dataset_parser.add_argument(
        "--queries-only",
        action="store_true",
        help="Output only the query text, one per line.",
    )
    dataset_parser.add_argument(
        "--format",
        choices=["text", "json", "csv"],
        default="text",
        help="Output format when printing or exporting full rows.",
    )
    dataset_parser.add_argument(
        "--output-file",
        default=None,
        help="Optional output path for the filtered dataset or query list.",
    )
    dataset_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit on the number of returned rows.",
    )
    dataset_parser.add_argument(
        "--list-categories",
        action="store_true",
        help="Print category counts from the dataset and exit.",
    )

    external_parser = subparsers.add_parser(
        "map-external",
        help="Merge external layered CSV datasets under a directory into one unified CSV for this pipeline.",
    )
    external_parser.add_argument(
        "--dataset-dir",
        default="dataset",
        help="Directory containing the 5 external CSV files.",
    )
    external_parser.add_argument(
        "--output-file",
        default="tests/search_quality_external_merged.csv",
        help="Where to save the merged CSV.",
    )
    external_parser.add_argument(
        "--queries-output-file",
        default="tests/search_quality_external_search_queries.txt",
        help="Where to save the merged searchable query list.",
    )
    external_parser.add_argument(
        "--summary-output-file",
        default=None,
        help="Optional JSON path for merge summary metadata.",
    )

    return parser.parse_args()


def read_queries(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as handle:
        queries: List[str] = []
        for line in handle:
            cleaned = line.strip()
            if not cleaned or cleaned.startswith("#"):
                continue
            queries.append(cleaned)
    return queries


def read_csv_records(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        lines = [line for line in handle if line.strip()]
    return list(csv.DictReader(lines))


def load_config(path: Optional[str]) -> Dict[str, Any]:
    config_path = path or os.environ.get("NLP_CONFIG_PATH") or "config.json"
    with open(config_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def normalize_category_filters(values: List[str]) -> Set[str]:
    return {str(value).strip().lower() for value in values if str(value).strip()}


def normalize_query_id_filters(values: List[str]) -> Set[str]:
    return {str(value).strip().upper() for value in values if str(value).strip()}


def normalize_layer_filters(values: List[str]) -> Set[str]:
    return {str(value).strip().lower() for value in values if str(value).strip()}


def normalize_query_key(query: str) -> str:
    return " ".join(str(query or "").strip().lower().split())


def filter_dataset_rows(
    rows: List[Dict[str, Any]],
    *,
    categories: Optional[List[str]] = None,
    query_ids: Optional[List[str]] = None,
    layers: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    allowed_categories = normalize_category_filters(categories or [])
    allowed_query_ids = normalize_query_id_filters(query_ids or [])
    allowed_layers = normalize_layer_filters(layers or [])

    filtered: List[Dict[str, Any]] = []
    for row in rows:
        row_category = str(row.get("category") or "").strip().lower()
        row_query_id = str(row.get("id") or "").strip().upper()
        row_layers = {
            token.strip().lower()
            for token in str(row.get("dataset_layers") or "").split("|")
            if token.strip()
        }

        if allowed_categories and row_category not in allowed_categories:
            continue
        if allowed_query_ids and row_query_id not in allowed_query_ids:
            continue
        if allowed_layers and not (row_layers & allowed_layers):
            continue

        filtered.append(row)
        if limit is not None and len(filtered) >= max(0, limit):
            break

    return filtered


def build_category_summary(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for row in rows:
        category = str(row.get("category") or "").strip() or "unknown"
        summary[category] = summary.get(category, 0) + 1
    return summary


def build_layer_summary(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for row in rows:
        for token in str(row.get("dataset_layers") or "").split("|"):
            layer = token.strip()
            if not layer:
                continue
            summary[layer] = summary.get(layer, 0) + 1
    return summary


def render_dataset_rows_text(rows: List[Dict[str, Any]], *, queries_only: bool = False) -> str:
    lines: List[str] = []
    if queries_only:
        return "\n".join(str(row.get("query") or "") for row in rows)

    lines.append(f"Matched rows: {len(rows)}")
    for row in rows:
        lines.append(
            f"[{row.get('id')}] {row.get('category')} | {row.get('ideal_route')} | "
            f"{row.get('dataset_layers') or '-'} | {row.get('query')}"
        )
    return "\n".join(lines)


def write_dataset_rows_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    fieldnames = list(rows[0].keys()) if rows else []
    ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        if not fieldnames:
            handle.write("")
            return
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def handle_dataset_command(args: argparse.Namespace) -> None:
    rows = read_csv_records(args.dataset_file)

    if args.list_categories:
        category_summary = build_category_summary(rows)
        layer_summary = build_layer_summary(rows)
        print("Dataset categories")
        for category, count in sorted(category_summary.items()):
            print(f"- {category}: {count}")
        if layer_summary:
            print("\nDataset layers")
            for layer, count in sorted(layer_summary.items()):
                print(f"- {layer}: {count}")
        return

    filtered = filter_dataset_rows(
        rows,
        categories=args.category,
        query_ids=args.query_id,
        layers=args.layer,
        limit=args.limit,
    )

    if args.output_file:
        if args.queries_only:
            ensure_parent_dir(args.output_file)
            with open(args.output_file, "w", encoding="utf-8") as handle:
                for row in filtered:
                    handle.write(f"{row.get('query') or ''}\n")
            print(f"Saved {len(filtered)} queries to {args.output_file}")
            return

        if args.format == "json":
            ensure_parent_dir(args.output_file)
            with open(args.output_file, "w", encoding="utf-8") as handle:
                json.dump(filtered, handle, ensure_ascii=False, indent=2)
            print(f"Saved {len(filtered)} rows to {args.output_file}")
            return

        if args.format == "csv":
            write_dataset_rows_csv(args.output_file, filtered)
            print(f"Saved {len(filtered)} rows to {args.output_file}")
            return

        ensure_parent_dir(args.output_file)
        with open(args.output_file, "w", encoding="utf-8") as handle:
            handle.write(render_dataset_rows_text(filtered, queries_only=False))
            handle.write("\n")
        print(f"Saved {len(filtered)} rows to {args.output_file}")
        return

    if args.format == "json":
        print(json.dumps(filtered, ensure_ascii=False, indent=2))
        return

    if args.format == "csv":
        if filtered:
            writer = csv.DictWriter(sys.stdout, fieldnames=list(filtered[0].keys()))
            writer.writeheader()
            writer.writerows(filtered)
        return

    print(render_dataset_rows_text(filtered, queries_only=args.queries_only))


def choose_primary_category(record: Dict[str, Any]) -> str:
    if record.get("final_qid"):
        return "final_answer"
    if record.get("gchunk_qid"):
        return "gold_chunk"
    if record.get("gdoc_qid"):
        return "gold_doc"
    if record.get("trigger_qid"):
        return "full_text_trigger"
    if record.get("route_qid"):
        return "route_intent"
    return "external"


def choose_ideal_route(record: Dict[str, Any]) -> str:
    expected_route = str(record.get("route_expected_route") or "").strip().lower()
    need_fulltext = str(record.get("need_fulltext") or "").strip().lower() == "yes"

    mapping = {
        "chat": "small_talk",
        "weather_api": "skill",
        "sports_api": "skill",
        "finance_api": "skill",
        "time_api": "skill",
        "calculator": "skill",
    }
    if expected_route in mapping:
        return mapping[expected_route]
    if expected_route == "general_web":
        return "web_search_fulltext" if need_fulltext else "web_search_summary"
    if need_fulltext:
        return "web_search_fulltext"
    if record.get("gdoc_qid") or record.get("gchunk_qid") or record.get("final_qid"):
        return "web_search_summary"
    return expected_route or "web_search_summary"


def choose_domain_type(record: Dict[str, Any]) -> str:
    expected_route = str(record.get("route_expected_route") or "").strip().lower()
    mapping = {
        "weather_api": "weather",
        "sports_api": "sports",
        "finance_api": "finance",
        "time_api": "time",
        "calculator": "calculator",
    }
    return mapping.get(expected_route, "none")


def choose_source_scope(record: Dict[str, Any]) -> str:
    if str(record.get("route_expected_route") or "").strip().lower() == "chat":
        return "none"
    return "web"


def choose_gold_source_type(record: Dict[str, Any]) -> str:
    if record.get("gchunk_qid"):
        return "full_article"
    if record.get("gdoc_qid"):
        return "full_article"
    if record.get("final_qid"):
        return "multiple"
    return "none"


def write_queries_file(path: str, rows: List[Dict[str, Any]]) -> None:
    ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            query = str(row.get("query") or "").strip()
            if query:
                handle.write(f"{query}\n")


def map_external_dataset_rows(dataset_dir: str) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    source_specs = [
        ("route_intent", "route_intent_dataset.csv"),
        ("full_text_trigger", "full_text_trigger_dataset.csv"),
        ("gold_doc", "gold_doc_dataset.csv"),
        ("gold_chunk", "gold_chunk_dataset.csv"),
        ("final_answer", "final_answer_dataset.csv"),
    ]

    merged: Dict[str, Dict[str, Any]] = {}
    source_counts: Dict[str, int] = {}

    def ensure_record(query: str) -> Dict[str, Any]:
        key = normalize_query_key(query)
        if key not in merged:
            merged[key] = {
                "query": query.strip(),
                "dataset_layers_set": set(),
                "route_qid": "",
                "route_intent_label": "",
                "route_expected_route": "",
                "trigger_qid": "",
                "need_search": "",
                "need_fulltext": "",
                "fulltext_reason": "",
                "gdoc_qid": "",
                "gold_doc_url": "",
                "gchunk_qid": "",
                "gold_span": "",
                "chunk_reference_answer": "",
                "final_qid": "",
                "final_reference_answer": "",
                "must_include_facts": "",
                "allowed_sources": "",
                "time_sensitive": "",
            }
        return merged[key]

    for layer_name, filename in source_specs:
        path = os.path.join(dataset_dir, filename)
        rows = read_csv_records(path)
        source_counts[layer_name] = len(rows)
        for row in rows:
            query = str(row.get("query") or "").strip()
            if not query:
                continue
            record = ensure_record(query)
            record["dataset_layers_set"].add(layer_name)

            if layer_name == "route_intent":
                record["route_qid"] = str(row.get("qid") or record["route_qid"])
                record["route_intent_label"] = str(
                    row.get("intent_label") or record["route_intent_label"]
                )
                record["route_expected_route"] = str(
                    row.get("expected_route") or record["route_expected_route"]
                )
            elif layer_name == "full_text_trigger":
                record["trigger_qid"] = str(row.get("qid") or record["trigger_qid"])
                record["need_search"] = str(row.get("need_search") or record["need_search"])
                record["need_fulltext"] = (
                    "yes"
                    if str(row.get("need_fulltext") or "").strip().lower() == "true"
                    else "no"
                )
                record["fulltext_reason"] = str(row.get("reason") or record["fulltext_reason"])
            elif layer_name == "gold_doc":
                record["gdoc_qid"] = str(row.get("qid") or record["gdoc_qid"])
                record["gold_doc_url"] = str(row.get("gold_doc_url") or record["gold_doc_url"])
            elif layer_name == "gold_chunk":
                record["gchunk_qid"] = str(row.get("qid") or record["gchunk_qid"])
                record["gold_doc_url"] = str(row.get("gold_doc_url") or record["gold_doc_url"])
                record["gold_span"] = str(row.get("gold_span") or record["gold_span"])
                record["chunk_reference_answer"] = str(
                    row.get("reference_answer") or record["chunk_reference_answer"]
                )
            elif layer_name == "final_answer":
                record["final_qid"] = str(row.get("qid") or record["final_qid"])
                record["final_reference_answer"] = str(
                    row.get("reference_answer") or record["final_reference_answer"]
                )
                record["must_include_facts"] = str(
                    row.get("must_include_facts") or record["must_include_facts"]
                )
                record["allowed_sources"] = str(
                    row.get("allowed_sources") or record["allowed_sources"]
                )
                record["time_sensitive"] = str(row.get("time_sensitive") or record["time_sensitive"])

    records: List[Dict[str, Any]] = []
    for _, record in sorted(merged.items(), key=lambda item: item[1]["query"].lower()):
        dataset_layers = "|".join(sorted(record.pop("dataset_layers_set")))
        chosen_id = (
            record.get("final_qid")
            or record.get("gchunk_qid")
            or record.get("gdoc_qid")
            or record.get("trigger_qid")
            or record.get("route_qid")
        )
        output = {
            "id": chosen_id,
            "query": record["query"],
            "category": choose_primary_category(record),
            "difficulty": "hard"
            if str(record.get("need_fulltext") or "").strip().lower() == "yes"
            else "medium",
            "source_scope": choose_source_scope(record),
            "ideal_route": choose_ideal_route(record),
            "domain_type": choose_domain_type(record),
            "need_fulltext": record.get("need_fulltext") or "no",
            "need_rag": "no",
            "gold_source_type": choose_gold_source_type(record),
            "gold_doc_id": "",
            "gold_chunk_id": "",
            "reference_answer_points": record.get("must_include_facts")
            or record.get("chunk_reference_answer")
            or record.get("final_reference_answer")
            or "",
            "dataset_layers": dataset_layers,
            **record,
        }
        records.append(output)

    searchable_rows = [
        row
        for row in records
        if row["ideal_route"] not in {"small_talk"} and row["source_scope"] == "web"
    ]
    summary = {
        "dataset_dir": dataset_dir,
        "source_counts": source_counts,
        "merged_queries": len(records),
        "searchable_queries": len(searchable_rows),
        "layer_counts": build_layer_summary(records),
        "category_counts": build_category_summary(records),
    }
    return records, summary


def handle_map_external_command(args: argparse.Namespace) -> None:
    records, summary = map_external_dataset_rows(args.dataset_dir)
    write_dataset_rows_csv(args.output_file, records)
    print(f"Saved {len(records)} merged rows to {args.output_file}")

    searchable_rows = [
        row
        for row in records
        if row["ideal_route"] not in {"small_talk"} and row["source_scope"] == "web"
    ]
    write_queries_file(args.queries_output_file, searchable_rows)
    print(f"Saved {len(searchable_rows)} searchable queries to {args.queries_output_file}")

    if args.summary_output_file:
        ensure_parent_dir(args.summary_output_file)
        with open(args.summary_output_file, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)
        print(f"Saved merge summary to {args.summary_output_file}")


def default_judgment() -> Dict[str, Any]:
    return {
        "annotation_complete": False,
        "judgment_mode": DEFAULT_JUDGMENT_MODE,
        "relevant_ranks": [],
        "relevant_urls": [],
        # Optional graded relevance per rank (0 irrelevant / 1 relevant / 2 answer
        # evidence). When present it drives nDCG@5 and answer_hit_at_k.
        "relevance_grades": [],
        "gold_doc_urls": [],
        "top3_has_answer_evidence": None,
        "core_correct": None,
        "annotator": "",
        "annotated_at": "",
        "route_correct": None,
        "fulltext_decision_correct": None,
        "chunk_hit_at_5": None,
        "answer_correctness": None,
        "answer_completeness": None,
        "answer_groundedness": None,
        "abstention_quality": None,
        "notes": "",
    }


def normalize_url(url: str) -> str:
    cleaned = (url or "").strip()
    if not cleaned:
        return ""

    parsed = urlparse(cleaned)
    netloc = parsed.netloc.lower().strip()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = (parsed.path or "").rstrip("/")
    return f"{netloc}{path}"


def make_hit_key(hit: Dict[str, Any]) -> str:
    normalized_url = normalize_url(str(hit.get("url") or ""))
    if normalized_url:
        return normalized_url

    title = str(hit.get("title") or "").strip().lower()
    snippet = str(hit.get("snippet") or "").strip().lower()
    return f"{title}|{snippet}"


def normalize_search_hits(raw_hits: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for rank, hit in enumerate(raw_hits, start=1):
        normalized.append(
            {
                "rank": rank,
                "title": str(hit.get("title") or ""),
                "url": str(hit.get("url") or ""),
                "snippet": str(hit.get("snippet") or ""),
            }
        )
    return normalized


def build_orchestrator_for_collection(
    config: Dict[str, Any],
    *,
    data_path: str,
    disable_rerank: bool,
    show_timings: bool,
):
    from langchain.langchain_llm import create_chat_model
    from langchain.langchain_orchestrator import create_langchain_orchestrator
    from main import build_reranker, build_search_client

    allow_search = True
    reranker = None
    rerank_config: Dict[str, Any] = config.get("rerank") or {}
    if allow_search and not disable_rerank:
        try:
            reranker, rerank_config = build_reranker(config)
        except Exception as exc:
            print(f"[search_quality] reranker disabled: {exc}")
            reranker = None

    min_rerank_score = float(rerank_config.get("min_score", 0.0))
    max_per_domain = max(1, int(rerank_config.get("max_per_domain", 1)))

    search_client = build_search_client(config)
    llm = create_chat_model(config=config)

    return create_langchain_orchestrator(
        config=config,
        llm=llm,
        search_client=search_client,
        data_path=data_path,
        reranker=reranker,
        min_rerank_score=min_rerank_score,
        max_per_domain=max_per_domain,
        requested_search_sources=list(getattr(search_client, "requested_sources", []))
        if search_client
        else [],
        active_search_sources=list(getattr(search_client, "active_sources", []))
        if search_client
        else [],
        active_search_source_labels=list(getattr(search_client, "active_source_labels", []))
        if search_client
        else [],
        missing_search_sources=list(getattr(search_client, "missing_requested_sources", []))
        if search_client
        else [],
        configured_search_sources=list(getattr(search_client, "configured_sources", []))
        if search_client
        else [],
        show_timings=show_timings,
    )


def load_query_rows(args: argparse.Namespace) -> List[Dict[str, Any]]:
    """Queries to collect: ``--dataset-file`` CSV rows or ``--queries-file`` lines."""
    rows: List[Dict[str, Any]] = []
    if getattr(args, "dataset_file", None):
        for row in read_csv_records(args.dataset_file):
            query = str(row.get("query") or "").strip()
            if not query:
                continue
            rows.append(
                {
                    "qid": str(row.get("qid") or row.get("id") or "").strip() or None,
                    "query": query,
                    "category": str(row.get("category") or row.get("task_type") or row.get("intent_label") or "").strip() or None,
                }
            )
    elif getattr(args, "queries_file", None):
        rows.extend({"qid": None, "query": query, "category": None} for query in read_queries(args.queries_file))
    else:
        raise SystemExit("Provide --queries-file or --dataset-file.")
    if not rows:
        raise SystemExit("No queries found in the provided file.")
    return rows


def load_gold_doc_map(path: Optional[str]) -> Dict[str, List[str]]:
    """``normalized query -> [gold_doc_url, ...]`` from a gold_doc / gold_chunk CSV."""
    mapping: Dict[str, List[str]] = {}
    if not path:
        return mapping
    for row in read_csv_records(path):
        query = normalize_query_key(str(row.get("query") or ""))
        url = str(row.get("gold_doc_url") or row.get("url") or "").strip()
        if not query or not url:
            continue
        bucket = mapping.setdefault(query, [])
        if url not in bucket:
            bucket.append(url)
    return mapping


def project_control(control: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the loop-era control facts the evaluation reads (design appendix A)."""
    termination_policy = control.get("termination_policy") or {}
    return {
        "search_mode": control.get("search_mode"),
        "final_executor": control.get("final_executor"),
        "loop_status": control.get("loop_status"),
        "termination_reason": control.get("loop_termination_reason") or control.get("termination_reason"),
        "loop_iterations": control.get("loop_iterations"),
        "loop_forced_synthesis": control.get("loop_forced_synthesis"),
        "loop_synthesis_attempts": control.get("loop_synthesis_attempts"),
        "compactions": control.get("compactions"),
        "peak_context_ratio": control.get("peak_context_ratio"),
        "advisory_gap_count": control.get("advisory_gap_count"),
        "autonomy": control.get("autonomy"),
        "query_analysis": control.get("query_analysis") or {},
        "execution_trace": control.get("execution_trace") or {},
        "evidence_coverage": control.get("evidence_coverage") or {},
        "loop_verdicts": control.get("loop_verdicts") or [],
        "loop_fetch_outcomes": control.get("loop_fetch_outcomes") or [],
        "tool_budgets": termination_policy.get("tool_budgets") or {},
        "search_sources_active": control.get("search_sources_active") or [],
        "search_sources_requested": control.get("search_sources_requested") or [],
        "search_sources_missing": control.get("search_sources_missing") or [],
        "providers": control.get("providers") or {},
    }


def project_evidence_records(records: Any) -> List[Dict[str, Any]]:
    """Ledger records with their stable ``metadata.eid`` (needed by citation checks)."""
    projected: List[Dict[str, Any]] = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        projected.append(
            {
                "source_type": record.get("source_type"),
                "source_tier": record.get("source_tier"),
                "reference": record.get("reference"),
                "title": record.get("title"),
                "content": str(record.get("content") or "")[:2000],
                "tool_name": record.get("tool_name"),
                "iteration": record.get("iteration"),
                "metadata": {
                    key: metadata.get(key)
                    for key in (
                        "eid",
                        "retrieval_kind",
                        "provider",
                        "content_chars",
                        "truncated",
                        "retrieved_at",
                        "published_at",
                        "source_tier",
                        "source_target",
                        "source_verdict_why",
                        "canonical_reference",
                    )
                    if key in metadata
                },
            }
        )
    return projected


def prefill_gold_judgment(record: Dict[str, Any], gold_urls: List[str]) -> None:
    """Pre-fill relevant_urls with gold hits present in the results (reviewer confirms)."""
    judgment = record.setdefault("judgment", default_judgment())
    judgment["gold_doc_urls"] = list(gold_urls)
    matched = [
        hit["url"]
        for hit in record.get("search_hits") or []
        if any(gold_doc_match(hit.get("url"), gold) for gold in gold_urls)
    ]
    if matched:
        judgment["relevant_urls"] = list(dict.fromkeys(list(judgment.get("relevant_urls") or []) + matched))
        judgment["notes"] = (str(judgment.get("notes") or "") + " gold_doc 命中已预填 relevant_urls，请复核。").strip()


def collect_all_providers(
    search_client: Any,
    rows: List[Dict[str, Any]],
    *,
    num_results: int,
) -> List[Dict[str, Any]]:
    """Ask every leaf provider the same query independently (no loop, no LLM)."""
    from evidence.official_domain_resolver import flatten_search_clients

    leaves = flatten_search_clients(search_client)
    if not leaves:
        raise SystemExit("No search providers configured.")
    records: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        query = row["query"]
        print(f"[collect/all-providers] {index}/{len(rows)} {query}")
        provider_results: List[Dict[str, Any]] = []
        for client in leaves:
            source = str(getattr(client, "source_id", type(client).__name__.lower()))
            started = time.perf_counter()
            error: Optional[str] = None
            hits: List[Any] = []
            try:
                hits = list(client.search(query, num_results=num_results, per_source_limit=num_results) or [])
            except Exception as exc:  # noqa: BLE001 - provider failures are data
                error = str(exc)[:300]
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            call_records = []
            getter = getattr(client, "get_last_call_records", None)
            if callable(getter):
                call_records = [item for item in list(getter() or []) if isinstance(item, dict)]
            credits = next((item.get("credits") for item in reversed(call_records) if item.get("credits") is not None), None)
            provider_results.append(
                {
                    "provider": source,
                    "status": "error" if error else "done",
                    "error": error,
                    "duration_ms": duration_ms,
                    "result_count": len(hits),
                    "credits": credits,
                    "results": normalize_search_hits(
                        [{"title": hit.title, "url": hit.url, "snippet": hit.snippet} for hit in hits]
                    ),
                }
            )
        records.append(
            {
                "query_id": row.get("qid") or index,
                "qid": row.get("qid"),
                "query": query,
                "category": row.get("category"),
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "collect_mode": "all_providers",
                "search_hits": [],
                "provider_results": provider_results,
                "judgment": default_judgment(),
            }
        )
    return records


def collect_records(args: argparse.Namespace) -> Dict[str, Any]:
    rows = load_query_rows(args)
    gold_map = load_gold_doc_map(getattr(args, "gold_doc_file", None))

    config = load_config(args.config)
    if args.model:
        config["LLM_PROVIDER"] = args.model
    elif args.provider:
        config["LLM_PROVIDER"] = args.provider

    if getattr(args, "all_providers", False):
        from main import build_search_client

        search_client = build_search_client(config)
        records = collect_all_providers(search_client, rows, num_results=args.num_results)
        for record in records:
            gold_urls = gold_map.get(normalize_query_key(record["query"]))
            if gold_urls:
                record["judgment"]["gold_doc_urls"] = list(gold_urls)
        return {
            "meta": {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "pipeline": "search_quality_pipeline",
                "collect_mode": "all_providers",
                "num_queries": len(records),
                "num_results": args.num_results,
                "providers": [
                    str(getattr(client, "source_id", ""))
                    for client in __import__("evidence.official_domain_resolver", fromlist=["flatten_search_clients"]).flatten_search_clients(search_client)
                ],
                "note": "Per-provider raw results only; relevance comes from the annotated single-chain collect file (--relevance-annotations at evaluate time).",
            },
            "records": records,
        }

    orchestrator = build_orchestrator_for_collection(
        config,
        data_path=args.data_path,
        disable_rerank=args.disable_rerank,
        show_timings=args.show_timings,
    )

    records: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        query = row["query"]
        print(f"[collect] {index}/{len(rows)} {query}")
        try:
            result = orchestrator.answer(
                query,
                num_search_results=args.num_results,
                per_source_search_results=args.num_results,
                num_retrieved_docs=args.num_results,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                allow_search=True,
                force_search=args.force_search,
                autonomy_mode=getattr(args, "autonomy", None),
            )
        except Exception as exc:  # noqa: BLE001 - collection records failures as data
            result = {"answer": "", "llm_error": f"{type(exc).__name__}: {exc}", "control": {}}

        control = result.get("control") or {}
        record = {
            "query_id": row.get("qid") or index,
            "qid": row.get("qid"),
            "query": query,
            "category": row.get("category"),
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "search_query": result.get("search_query"),
            "search_hits": normalize_search_hits(result.get("search_hits") or []),
            "answer": str(result.get("answer") or "")[:6000],
            "llm_error": result.get("llm_error"),
            "response_times": result.get("response_times") or {},
            "search_api_calls": [
                call for call in (result.get("search_api_calls") or []) if isinstance(call, dict)
            ],
            "evidence_records": project_evidence_records(result.get("evidence_records")),
            "control": project_control(control),
            "judgment": default_judgment(),
        }
        gold_urls = gold_map.get(normalize_query_key(query))
        if gold_urls:
            prefill_gold_judgment(record, gold_urls)
        records.append(record)

    payload = {
        "meta": {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "pipeline": "search_quality_pipeline",
            "collect_mode": "single_chain",
            "num_queries": len(records),
            "num_results": args.num_results,
            "force_search": bool(args.force_search),
            "autonomy": getattr(args, "autonomy", None),
            "gold_doc_file": getattr(args, "gold_doc_file", None),
            "judgment_instructions": [
                "详细标注模式：把 judgment.annotation_complete 设为 true，保留 judgment_mode='detailed'，填写 relevant_ranks 或 relevant_urls；可选填 relevance_grades（逐 rank 0/1/2）以启用 nDCG@5。",
                "轻量标注模式：把 judgment.annotation_complete 设为 true，设 judgment_mode='top3_only'，只填写 top3_has_answer_evidence 与 core_correct(0/1/2)。",
                "如果采用 detailed 模式但当前返回结果里没有相关结果，可以保留 relevant_ranks/relevant_urls 为空数组。",
                "gold_doc 命中已预填到 relevant_urls；请复核而不是照抄；annotator / annotated_at 必填。",
            ],
        },
        "records": records,
    }
    return payload


def normalize_gold_url(url: str) -> Tuple[str, str]:
    """``(host, path)`` with lowercase host, ``www.`` stripped, no query/fragment."""
    cleaned = str(url or "").strip()
    if not cleaned:
        return "", ""
    if "://" not in cleaned:
        cleaned = "https://" + cleaned
    parsed = urlparse(cleaned)
    host = (parsed.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "/").rstrip("/") or "/"
    return host, path


def gold_doc_match(hit_url: Any, gold_url: Any) -> bool:
    """Gold match: same host (www-insensitive) and the hit path starts with the gold path."""
    hit_host, hit_path = normalize_gold_url(str(hit_url or ""))
    gold_host, gold_path = normalize_gold_url(str(gold_url or ""))
    if not hit_host or not gold_host or hit_host != gold_host:
        return False
    if gold_path == "/":
        return True
    return hit_path == gold_path or hit_path.startswith(gold_path.rstrip("/") + "/")


def registrable_domain_of(url: Any) -> str:
    from evidence.source_tiering import registrable_domain

    return registrable_domain(url)


def dcg(grades: List[float]) -> float:
    return sum(grade / math.log2(index + 1) for index, grade in enumerate(grades, start=1))


def ndcg_at_k(grades: List[float], k: int) -> Optional[float]:
    """nDCG over graded relevance (0/1/2); None when no result carries relevance."""
    if not grades:
        return None
    ranked = [max(0.0, float(grade)) for grade in grades[:k]]
    ideal = sorted((max(0.0, float(grade)) for grade in grades), reverse=True)[:k]
    if not ideal or ideal[0] <= 0:
        return 0.0
    return dcg(ranked) / dcg(ideal)


def coerce_grade(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)) and int(value) in {0, 1, 2}:
        return int(value)
    if isinstance(value, str) and value.strip() in {"0", "1", "2"}:
        return int(value.strip())
    return None


def relevance_grades_for_record(record: Dict[str, Any]) -> List[int]:
    """Per-rank 0/1/2 grades: explicit ``relevance_grades`` or binary from relevant ranks."""
    hits = record.get("search_hits") or []
    judgment = record.get("judgment") or {}
    explicit = judgment.get("relevance_grades") or []
    grades = [coerce_grade(value) for value in explicit]
    if explicit and all(grade is not None for grade in grades) and len(grades) == len(hits):
        return [int(grade) for grade in grades]
    relevant = set(extract_relevant_ranks(record))
    return [1 if rank in relevant else 0 for rank in range(1, len(hits) + 1)]


def build_tier_classifier(config: Optional[Dict[str, Any]]) -> Callable[[str, List[str]], str]:
    """Offline tier classifier: pins / config map / denylist only (resolver disabled)."""
    from evidence.official_domain_resolver import build_official_domain_resolver
    from evidence.source_verdict import classify_source

    orchestration = dict((config or {}).get("orchestration") or {})
    resolution = dict(orchestration.get("official_domain_resolution") or {})
    resolution.update({"enabled": False, "graph_probes_enabled": False, "pin_shadow_audit": False})
    orchestration["official_domain_resolution"] = resolution
    resolver = build_official_domain_resolver(orchestration)
    official_domains = orchestration.get("official_domains") or {}

    def classify(url: str, entities: List[str]) -> str:
        try:
            return classify_source(url, entities=entities, official_domains=official_domains, resolver=resolver).tier
        except Exception:  # noqa: BLE001 - tiering must never break evaluation
            return "unknown"

    return classify


def tier_metrics(hits: List[Dict[str, Any]], classify: Callable[[str, List[str]], str], entities: List[str]) -> Dict[str, Any]:
    tiers = [classify(str(hit.get("url") or ""), entities) for hit in hits]
    metrics: Dict[str, Any] = {"result_tiers": tiers}
    for k in TIER_K_VALUES:
        top = tiers[:k]
        metrics[f"authoritative_at_{k}"] = (sum(1 for tier in top if tier in {"official", "first_party"}) / len(top)) if top else None
        metrics[f"aggregator_at_{k}"] = (sum(1 for tier in top if tier == "aggregator") / len(top)) if top else None
    return metrics


def domain_diversity_at_k(hits: List[Dict[str, Any]], k: int = 5) -> Optional[float]:
    top = hits[:k]
    if not top:
        return None
    domains = {registrable_domain_of(hit.get("url")) or str(hit.get("url") or "") for hit in top}
    return len(domains) / k


def domain_diversity_at_5(hits: List[Dict[str, Any]]) -> Optional[float]:
    return domain_diversity_at_k(hits, 5)


def gold_doc_metrics(hits: List[Dict[str, Any]], gold_urls: List[str]) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {"gold_doc_urls": list(gold_urls), "gold_doc_rank": None}
    if not gold_urls:
        for k in GOLD_DOC_K_VALUES:
            metrics[f"gold_doc_recall_at_{k}"] = None
        return metrics
    rank = next(
        (index for index, hit in enumerate(hits, start=1) if any(gold_doc_match(hit.get("url"), gold) for gold in gold_urls)),
        None,
    )
    metrics["gold_doc_rank"] = rank
    for k in GOLD_DOC_K_VALUES:
        metrics[f"gold_doc_recall_at_{k}"] = bool(rank is not None and rank <= k)
    return metrics


def extract_relevant_ranks(record: Dict[str, Any]) -> List[int]:
    hits = record.get("search_hits") or []
    judgment = record.get("judgment") or {}
    relevant_indices: Set[int] = set()

    for raw_rank in judgment.get("relevant_ranks") or []:
        try:
            rank = int(raw_rank)
        except (TypeError, ValueError):
            continue
        if 1 <= rank <= len(hits):
            relevant_indices.add(rank - 1)

    normalized_urls = {
        normalize_url(str(url))
        for url in (judgment.get("relevant_urls") or [])
        if normalize_url(str(url))
    }
    if normalized_urls:
        for index, hit in enumerate(hits):
            if normalize_url(str(hit.get("url") or "")) in normalized_urls:
                relevant_indices.add(index)

    return sorted(index + 1 for index in relevant_indices)


def coerce_binary_score(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)) and int(value) in {0, 1}:
        return int(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return 1
        if normalized in {"0", "false", "no", "n"}:
            return 0
    return None


def coerce_ternary_score(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)) and int(value) in {0, 1, 2}:
        return int(value)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized in {"0", "1", "2"}:
            return int(normalized)
    return None


def evaluate_record(
    record: Dict[str, Any],
    *,
    gold_urls: Optional[List[str]] = None,
    classify: Optional[Callable[[str, List[str]], str]] = None,
) -> Dict[str, Any]:
    query = str(record.get("query") or "")
    judgment = record.get("judgment") or {}
    annotation_complete = bool(judgment.get("annotation_complete"))
    mode = str(judgment.get("judgment_mode") or DEFAULT_JUDGMENT_MODE).strip().lower()
    if mode not in SUPPORTED_JUDGMENT_MODES:
        mode = DEFAULT_JUDGMENT_MODE
    hits = record.get("search_hits") or []
    analysis = (record.get("control") or {}).get("query_analysis") or {}
    entities = list(dict.fromkeys(list(analysis.get("comparison_members") or []) + list(analysis.get("entities") or [])))

    metrics: Dict[str, Any] = {
        "query": query,
        "query_id": record.get("query_id"),
        "qid": record.get("qid"),
        "category": record.get("category"),
        "annotation_complete": annotation_complete,
        "judgment_mode": mode,
        "hit_at_3": None,
        "hit_at_5": None,
        "answer_hit_at_3": None,
        "answer_hit_at_5": None,
        "mrr": None,
        "ndcg_at_5": None,
        "unique_useful_results": None,
        "first_relevant_rank": None,
        "relevant_ranks": [],
        "route_correct": None,
        "fulltext_decision_correct": None,
        "chunk_hit_at_5": None,
        "answer_correctness": None,
        "answer_completeness": None,
        "answer_groundedness": None,
        "abstention_quality": None,
        "core_correct": coerce_ternary_score(judgment.get("core_correct")),
        "result_count": len(hits),
        "empty_result": len(hits) == 0,
        "domain_diversity_at_5": domain_diversity_at_5(hits) if hits else None,
        "loop_status": (record.get("control") or {}).get("loop_status"),
        "total_latency_ms": extract_total_latency_ms(record),
        "search_latency_ms": extract_component_latency_ms(record, "search_sources"),
        "llm_latency_ms": extract_component_latency_ms(record, "llm_calls"),
        "tool_latency_ms": extract_component_latency_ms(record, "tool_calls"),
    }
    effective_gold = list(gold_urls or judgment.get("gold_doc_urls") or [])
    metrics.update(gold_doc_metrics(hits, effective_gold))
    if classify is not None and hits:
        metrics.update(tier_metrics(hits, classify, entities))

    if not annotation_complete:
        return metrics

    if mode == TOP3_ONLY_JUDGMENT_MODE:
        evidence = judgment.get("top3_has_answer_evidence")
        metrics["hit_at_3"] = bool(evidence) if isinstance(evidence, bool) else None
        metrics["route_correct"] = coerce_binary_score(judgment.get("route_correct"))
        metrics["fulltext_decision_correct"] = coerce_binary_score(
            judgment.get("fulltext_decision_correct")
        )
        metrics["chunk_hit_at_5"] = coerce_binary_score(judgment.get("chunk_hit_at_5"))
        metrics["answer_correctness"] = coerce_ternary_score(judgment.get("answer_correctness"))
        metrics["answer_completeness"] = coerce_ternary_score(
            judgment.get("answer_completeness")
        )
        metrics["answer_groundedness"] = coerce_ternary_score(
            judgment.get("answer_groundedness")
        )
        metrics["abstention_quality"] = coerce_ternary_score(judgment.get("abstention_quality"))
        return metrics

    grades = relevance_grades_for_record(record)
    relevant_ranks = [rank for rank, grade in enumerate(grades, start=1) if grade >= 1] or extract_relevant_ranks(record)
    first_relevant_rank = relevant_ranks[0] if relevant_ranks else None
    answer_ranks = [rank for rank, grade in enumerate(grades, start=1) if grade >= 2]

    unique_useful_keys = set()
    for rank in relevant_ranks:
        if 1 <= rank <= len(hits):
            unique_useful_keys.add(make_hit_key(hits[rank - 1]))

    has_grades = bool((judgment.get("relevance_grades") or []))
    metrics.update(
        {
            "hit_at_3": any(rank <= 3 for rank in relevant_ranks),
            "hit_at_5": any(rank <= 5 for rank in relevant_ranks),
            "answer_hit_at_3": any(rank <= 3 for rank in answer_ranks) if has_grades else None,
            "answer_hit_at_5": any(rank <= 5 for rank in answer_ranks) if has_grades else None,
            "mrr": (1.0 / first_relevant_rank) if first_relevant_rank else 0.0,
            "ndcg_at_5": ndcg_at_k([float(grade) for grade in grades], 5) if hits else None,
            "relevance_grades": grades,
            "unique_useful_results": len(unique_useful_keys),
            "first_relevant_rank": first_relevant_rank,
            "relevant_ranks": relevant_ranks,
            "route_correct": coerce_binary_score(judgment.get("route_correct")),
            "fulltext_decision_correct": coerce_binary_score(
                judgment.get("fulltext_decision_correct")
            ),
            "chunk_hit_at_5": coerce_binary_score(judgment.get("chunk_hit_at_5")),
            "answer_correctness": coerce_ternary_score(judgment.get("answer_correctness")),
            "answer_completeness": coerce_ternary_score(judgment.get("answer_completeness")),
            "answer_groundedness": coerce_ternary_score(judgment.get("answer_groundedness")),
            "abstention_quality": coerce_ternary_score(judgment.get("abstention_quality")),
        }
    )
    return metrics


def average(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def coerce_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_total_latency_ms(record: Dict[str, Any]) -> Optional[float]:
    top_level_value = coerce_float(record.get("latency_ms"))
    if top_level_value is not None:
        return top_level_value

    response_times = record.get("response_times")
    if not isinstance(response_times, dict):
        return None
    return coerce_float(response_times.get("total_ms"))


def extract_component_latency_ms(record: Dict[str, Any], key: str) -> Optional[float]:
    response_times = record.get("response_times")
    if not isinstance(response_times, dict):
        return None

    entries = response_times.get(key)
    if entries is None:
        return 0.0
    if not isinstance(entries, list):
        return None

    durations: List[float] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        duration_value = coerce_float(entry.get("duration_ms"))
        if duration_value is not None:
            durations.append(duration_value)

    return sum(durations)


def percentile(values: List[float], ratio: float) -> Optional[float]:
    if not values:
        return None
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]

    index = (len(sorted_values) - 1) * ratio
    lower_index = math.floor(index)
    upper_index = math.ceil(index)
    if lower_index == upper_index:
        return sorted_values[lower_index]

    lower_value = sorted_values[lower_index]
    upper_value = sorted_values[upper_index]
    weight = index - lower_index
    return lower_value + (upper_value - lower_value) * weight


def build_metric_summary(
    values: List[Optional[float]],
    *,
    treat_bool_as_rate: bool = False,
) -> Dict[str, Any]:
    usable = [value for value in values if value is not None]
    summary = {
        "value": None,
        "denominator": len(usable),
    }
    if not usable:
        return summary

    if treat_bool_as_rate:
        numeric = [1.0 if bool(value) else 0.0 for value in usable]
        summary["value"] = sum(numeric) / len(numeric)
        summary["positives"] = int(sum(numeric))
        return summary

    numeric = [float(value) for value in usable]
    summary["value"] = sum(numeric) / len(numeric)
    return summary


def build_discrete_score_summary(
    values: List[Optional[int]],
    *,
    allowed_scores: List[int],
) -> Dict[str, Any]:
    usable = [int(value) for value in values if value is not None and int(value) in allowed_scores]
    summary: Dict[str, Any] = {
        "value": None,
        "denominator": len(usable),
        "counts": {str(score): 0 for score in allowed_scores},
    }
    if not usable:
        return summary

    for score in usable:
        summary["counts"][str(score)] += 1
    summary["value"] = sum(usable) / len(usable)
    return summary


def build_latency_summary(values: List[Optional[float]]) -> Dict[str, Any]:
    usable = [float(value) for value in values if value is not None]
    summary: Dict[str, Any] = {
        "value": None,
        "denominator": len(usable),
        "p50": None,
        "p95": None,
        "min": None,
        "max": None,
    }
    if not usable:
        return summary

    summary["value"] = sum(usable) / len(usable)
    summary["p50"] = percentile(usable, 0.50)
    summary["p95"] = percentile(usable, 0.95)
    summary["min"] = min(usable)
    summary["max"] = max(usable)
    return summary


def _error_class(reason: Any) -> str:
    text_value = str(reason or "").casefold()
    if not text_value:
        return "none"
    if "skipped" in text_value and "site:" in text_value:
        return "site_operator_skipped"
    if "timeout" in text_value or "timed out" in text_value:
        return "timeout"
    if "quota" in text_value or "429" in text_value or "rate limit" in text_value or "monthly_limit" in text_value:
        return "quota"
    if any(code in text_value for code in ("401", "403", "400", "404", "422")):
        return "4xx"
    if any(code in text_value for code in ("500", "502", "503", "504")):
        return "5xx"
    return "other"


def _retained_url_keys(record: Dict[str, Any]) -> Set[str]:
    decisions = ((record.get("control") or {}).get("evidence_coverage") or {}).get("decisions") or []
    keys: Set[str] = set()
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("decision") != "retained":
            continue
        key = normalize_url(str(decision.get("reference") or ""))
        if key:
            keys.add(key)
    return keys


def build_provider_scorecard(
    records: List[Dict[str, Any]],
    *,
    classify: Optional[Callable[[str, List[str]], str]] = None,
) -> Dict[str, Any]:
    """Per-provider availability / errors / latency / fallback share / retained contribution.

    Built from ``search_api_calls`` of single-chain collect records. Official-domain
    discovery searches (``target`` / resolver labels) are listed separately so they do
    not pollute the answer-path scorecard. ``unique_yield`` needs ``--all-providers``.
    """
    providers: Dict[str, Dict[str, Any]] = {}
    discovery: Dict[str, Dict[str, Any]] = {}

    def bucket(store: Dict[str, Dict[str, Any]], name: str) -> Dict[str, Any]:
        return store.setdefault(
            name,
            {
                "requests": 0,
                "done": 0,
                "errors": 0,
                "error_types": {},
                "empty": 0,
                "fallback": 0,
                "durations_ms": [],
                "results": 0,
                "retained": 0,
                "authoritative": 0,
                "credits_known": 0.0,
                "credits_known_requests": 0,
            },
        )

    for record in records:
        retained_keys = _retained_url_keys(record)
        analysis = (record.get("control") or {}).get("query_analysis") or {}
        entities = list(dict.fromkeys(list(analysis.get("comparison_members") or []) + list(analysis.get("entities") or [])))
        for call in record.get("search_api_calls") or []:
            if not isinstance(call, dict):
                continue
            kind = str(call.get("kind") or "")
            if kind in {"extracted_pages", "resolved_entities"}:
                continue
            name = str(call.get("provider") or call.get("source") or "unknown")
            label = str(call.get("label") or "")
            is_discovery = bool(call.get("target")) or "官方域" in label or "resolver" in label.casefold() or "discovery" in label.casefold()
            entry = bucket(discovery if is_discovery else providers, name)
            entry["requests"] += 1
            status = str(call.get("status") or "done")
            reason = call.get("reason") or call.get("error")
            if status == "error":
                entry["errors"] += 1
                error_class = _error_class(reason)
                entry["error_types"][error_class] = entry["error_types"].get(error_class, 0) + 1
            else:
                entry["done"] += 1
            if call.get("fallback"):
                entry["fallback"] += 1
            duration = coerce_float(call.get("duration_ms"))
            if duration is not None and not (status == "error" and _error_class(reason) == "site_operator_skipped"):
                entry["durations_ms"].append(duration)
            results = [item for item in (call.get("records") or call.get("results") or []) if isinstance(item, dict)]
            count = call.get("result_count")
            try:
                count_value = int(count) if count is not None else len(results)
            except (TypeError, ValueError):
                count_value = len(results)
            if status != "error" and count_value == 0:
                entry["empty"] += 1
            entry["results"] += count_value
            for item in results:
                url = str(item.get("url") or "")
                if normalize_url(url) in retained_keys:
                    entry["retained"] += 1
                if classify is not None and classify(url, entities) in {"official", "first_party"}:
                    entry["authoritative"] += 1
            credits = call.get("credits")
            if isinstance(credits, (int, float)) and not isinstance(credits, bool):
                entry["credits_known"] += float(credits)
                entry["credits_known_requests"] += 1

    def finish(store: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        table: Dict[str, Any] = {}
        for name, entry in sorted(store.items()):
            requests_count = entry["requests"]
            attempted = entry["requests"] - entry["error_types"].get("site_operator_skipped", 0)
            durations = entry.pop("durations_ms")
            table[name] = {
                "requests": requests_count,
                "attempted": attempted,
                "availability": (entry["done"] / attempted) if attempted else None,
                "error_rate_by_type": entry["error_types"],
                "empty_rate": (entry["empty"] / entry["done"]) if entry["done"] else None,
                "latency_p50_ms": percentile(durations, 0.5),
                "latency_p95_ms": percentile(durations, 0.95),
                "fallback_share": (entry["fallback"] / requests_count) if requests_count else None,
                "results_returned": entry["results"],
                "retained_contribution": (entry["retained"] / entry["results"]) if entry["results"] else None,
                "authoritative_yield": (entry["authoritative"] / entry["results"]) if (entry["results"] and classify is not None) else None,
                "credits_known": entry["credits_known"],
                "credits_known_requests": entry["credits_known_requests"],
                "cost_per_retained": (entry["credits_known"] / entry["retained"]) if (entry["retained"] and entry["credits_known_requests"]) else None,
                "unique_yield": None,
            }
        return table

    return {
        "answer_path": finish(providers),
        "official_domain_discovery": finish(discovery),
        "note": "当前链只到首个有结果的 provider，unique_yield 与公平的 retained_contribution 待 --all-providers 采集（Q3-04）。",
    }


def relevance_index_from_annotations(records: List[Dict[str, Any]]) -> Dict[str, Set[str]]:
    """``normalized query -> {normalized relevant URL}`` from annotated single-chain records."""
    index: Dict[str, Set[str]] = {}
    for record in records:
        judgment = record.get("judgment") or {}
        if not judgment.get("annotation_complete"):
            continue
        hits = record.get("search_hits") or []
        keys: Set[str] = set()
        grades = relevance_grades_for_record(record)
        for rank, grade in enumerate(grades, start=1):
            if grade >= 1 and rank <= len(hits):
                key = normalize_url(str(hits[rank - 1].get("url") or ""))
                if key:
                    keys.add(key)
        for url in judgment.get("relevant_urls") or []:
            key = normalize_url(str(url))
            if key:
                keys.add(key)
        index[normalize_query_key(str(record.get("query") or ""))] = keys
    return index


def evaluate_all_providers(
    records: List[Dict[str, Any]],
    *,
    relevance: Optional[Dict[str, Set[str]]] = None,
    gold_map: Optional[Dict[str, List[str]]] = None,
    classify: Optional[Callable[[str, List[str]], str]] = None,
) -> Dict[str, Any]:
    """Fair per-provider comparison from an ``--all-providers`` collect file.

    * ``relevant_contribution``: relevant results / returned results (relevance from
      the annotated single-chain file or gold_doc URLs).
    * ``unique_yield``: relevant results only this provider returned / its relevant results.
    * ``agreement``: mean pairwise Jaccard of returned URL sets, plus per-query consensus
      (share of providers containing the most common top-1 URL) as a drift reference.
    """
    providers: Dict[str, Dict[str, Any]] = {}
    pair_overlaps: Dict[Tuple[str, str], List[float]] = {}
    consensus: List[float] = []
    per_query: List[Dict[str, Any]] = []

    def bucket(name: str) -> Dict[str, Any]:
        return providers.setdefault(
            name,
            {"requests": 0, "done": 0, "errors": 0, "error_types": {}, "empty": 0, "durations_ms": [], "results": 0,
             "relevant": 0, "unique_relevant": 0, "gold_hits": 0, "gold_queries": 0, "authoritative": 0,
             "credits_known": 0.0, "credits_known_requests": 0},
        )

    for record in records:
        query_key = normalize_query_key(str(record.get("query") or ""))
        relevant_keys = (relevance or {}).get(query_key, set())
        gold_urls = (gold_map or {}).get(query_key) or list((record.get("judgment") or {}).get("gold_doc_urls") or [])
        url_sets: Dict[str, Set[str]] = {}
        top1: Dict[str, str] = {}
        provider_rows = [row for row in record.get("provider_results") or [] if isinstance(row, dict)]
        query_summary: Dict[str, Any] = {"query": record.get("query"), "qid": record.get("qid"), "providers": {}}
        for row in provider_rows:
            name = str(row.get("provider") or "unknown")
            entry = bucket(name)
            entry["requests"] += 1
            if row.get("status") == "error":
                entry["errors"] += 1
                error_class = _error_class(row.get("error"))
                entry["error_types"][error_class] = entry["error_types"].get(error_class, 0) + 1
            else:
                entry["done"] += 1
            duration = coerce_float(row.get("duration_ms"))
            if duration is not None:
                entry["durations_ms"].append(duration)
            results = [item for item in (row.get("results") or []) if isinstance(item, dict)]
            if row.get("status") != "error" and not results:
                entry["empty"] += 1
            keys = {normalize_url(str(item.get("url") or "")) for item in results}
            keys.discard("")
            url_sets[name] = keys
            if results:
                top1[name] = normalize_url(str(results[0].get("url") or ""))
            entry["results"] += len(results)
            relevant_here = keys & relevant_keys
            entry["relevant"] += len(relevant_here)
            if gold_urls:
                entry["gold_queries"] += 1
                if any(gold_doc_match(item.get("url"), gold) for item in results for gold in gold_urls):
                    entry["gold_hits"] += 1
            if classify is not None:
                entry["authoritative"] += sum(1 for item in results if classify(str(item.get("url") or ""), []) in {"official", "first_party"})
            credits = row.get("credits")
            if isinstance(credits, (int, float)) and not isinstance(credits, bool):
                entry["credits_known"] += float(credits)
                entry["credits_known_requests"] += 1
            query_summary["providers"][name] = {"results": len(results), "relevant": len(relevant_here), "status": row.get("status")}
        # unique relevant: relevant URLs returned by exactly one provider
        for name, keys in url_sets.items():
            others: Set[str] = set()
            for other_name, other_keys in url_sets.items():
                if other_name != name:
                    others |= other_keys
            providers[name]["unique_relevant"] += len((keys & relevant_keys) - others)
        names = sorted(url_sets)
        for index, left in enumerate(names):
            for right in names[index + 1 :]:
                union = url_sets[left] | url_sets[right]
                jaccard = (len(url_sets[left] & url_sets[right]) / len(union)) if union else None
                if jaccard is not None:
                    pair_overlaps.setdefault((left, right), []).append(jaccard)
        if top1:
            counts: Dict[str, int] = {}
            for url in top1.values():
                counts[url] = counts.get(url, 0) + 1
            consensus.append(max(counts.values()) / len(top1))
            query_summary["top1_consensus"] = consensus[-1]
        per_query.append(query_summary)

    table: Dict[str, Any] = {}
    for name, entry in sorted(providers.items()):
        durations = entry.pop("durations_ms")
        table[name] = {
            "requests": entry["requests"],
            "availability": (entry["done"] / entry["requests"]) if entry["requests"] else None,
            "error_rate_by_type": entry["error_types"],
            "empty_rate": (entry["empty"] / entry["done"]) if entry["done"] else None,
            "latency_p50_ms": percentile(durations, 0.5),
            "latency_p95_ms": percentile(durations, 0.95),
            "results_returned": entry["results"],
            "relevant_contribution": (entry["relevant"] / entry["results"]) if (entry["results"] and relevance) else None,
            "unique_yield": (entry["unique_relevant"] / entry["relevant"]) if (entry["relevant"] and relevance) else None,
            "gold_doc_hit_rate": (entry["gold_hits"] / entry["gold_queries"]) if entry["gold_queries"] else None,
            "authoritative_yield": (entry["authoritative"] / entry["results"]) if (entry["results"] and classify is not None) else None,
            "credits_known": entry["credits_known"],
            "credits_known_requests": entry["credits_known_requests"],
            "cost_per_relevant": (entry["credits_known"] / entry["relevant"]) if (entry["relevant"] and entry["credits_known_requests"]) else None,
        }
    agreement = {
        f"{left}|{right}": average(values) for (left, right), values in sorted(pair_overlaps.items())
    }
    return {
        "providers": table,
        "agreement": {"pairwise_jaccard": agreement, "top1_consensus_mean": average(consensus), "queries": len(records)},
        "per_query": per_query,
        "relevance_source": "annotations" if relevance else ("gold_doc" if gold_map else "none"),
    }


def build_category_macro(per_query: List[Dict[str, Any]], metric: str, *, rate_metric: bool) -> Dict[str, Any]:
    """Per-category means with the design §3.4 rule: < 5 samples report counts only."""
    groups: Dict[str, List[Any]] = {}
    for item in per_query:
        value = item.get(metric)
        if value is None:
            continue
        groups.setdefault(str(item.get("category") or "uncategorized"), []).append(value)
    table: Dict[str, Any] = {}
    means: List[float] = []
    for name, values in sorted(groups.items()):
        numeric = [1.0 if bool(v) else 0.0 for v in values] if rate_metric else [float(v) for v in values]
        entry: Dict[str, Any] = {"n": len(numeric)}
        if len(numeric) >= MIN_CATEGORY_SAMPLES:
            entry["value"] = sum(numeric) / len(numeric)
            means.append(entry["value"])
        else:
            entry["value"] = None
            entry["count_only"] = True
            entry["positives"] = int(sum(numeric)) if rate_metric else None
        table[name] = entry
    return {"groups": table, "macro_average": (sum(means) / len(means)) if means else None}


def evaluate_records(
    records: List[Dict[str, Any]],
    *,
    gold_map: Optional[Dict[str, List[str]]] = None,
    classify: Optional[Callable[[str, List[str]], str]] = None,
) -> Dict[str, Any]:
    gold_map = gold_map or {}
    per_query = [
        evaluate_record(
            record,
            gold_urls=gold_map.get(normalize_query_key(str(record.get("query") or ""))),
            classify=classify,
        )
        for record in records
    ]
    annotated = [item for item in per_query if item["annotation_complete"]]
    detailed = [item for item in annotated if item["judgment_mode"] == DEFAULT_JUDGMENT_MODE]
    top3_only = [item for item in annotated if item["judgment_mode"] == TOP3_ONLY_JUDGMENT_MODE]
    with_gold = [item for item in per_query if item.get("gold_doc_urls")]
    with_tiers = [item for item in per_query if item.get("result_tiers")]

    report = {
        "summary": {
            "total_queries": len(records),
            "annotated_queries": len(annotated),
            "detailed_annotations": len(detailed),
            "top3_only_annotations": len(top3_only),
            "empty_result_rate": build_metric_summary(
                [item["empty_result"] for item in per_query],
                treat_bool_as_rate=True,
            ),
            "gold_doc_queries": len(with_gold),
            "gold_doc_recall_at_3": build_metric_summary(
                [item.get("gold_doc_recall_at_3") for item in with_gold],
                treat_bool_as_rate=True,
            ),
            "gold_doc_recall_at_5": build_metric_summary(
                [item.get("gold_doc_recall_at_5") for item in with_gold],
                treat_bool_as_rate=True,
            ),
            "authoritative_at_3": build_metric_summary([item.get("authoritative_at_3") for item in with_tiers]),
            "authoritative_at_5": build_metric_summary([item.get("authoritative_at_5") for item in with_tiers]),
            "aggregator_at_3": build_metric_summary([item.get("aggregator_at_3") for item in with_tiers]),
            "aggregator_at_5": build_metric_summary([item.get("aggregator_at_5") for item in with_tiers]),
            "domain_diversity_at_5": build_metric_summary([item.get("domain_diversity_at_5") for item in per_query]),
            "ndcg_at_5": build_metric_summary(
                [item.get("ndcg_at_5") for item in detailed if item.get("relevance_grades") and item.get("judgment_mode") == DEFAULT_JUDGMENT_MODE and (item.get("relevance_grades") or []) and any(grade == 2 for grade in item.get("relevance_grades") or [])]
            ),
            "answer_hit_at_3": build_metric_summary(
                [item.get("answer_hit_at_3") for item in detailed],
                treat_bool_as_rate=True,
            ),
            "core_correct": build_discrete_score_summary(
                [item.get("core_correct") for item in annotated],
                allowed_scores=[0, 1, 2],
            ),
            "hit_at_3": build_metric_summary(
                [item["hit_at_3"] for item in annotated],
                treat_bool_as_rate=True,
            ),
            "route_correct": build_metric_summary(
                [item["route_correct"] for item in annotated],
                treat_bool_as_rate=True,
            ),
            "fulltext_decision_correct": build_metric_summary(
                [item["fulltext_decision_correct"] for item in annotated],
                treat_bool_as_rate=True,
            ),
            "hit_at_5": build_metric_summary(
                [item["hit_at_5"] for item in detailed],
                treat_bool_as_rate=True,
            ),
            "chunk_hit_at_5": build_metric_summary(
                [item["chunk_hit_at_5"] for item in annotated],
                treat_bool_as_rate=True,
            ),
            "mrr": build_metric_summary([item["mrr"] for item in detailed]),
            "avg_unique_useful_results": build_metric_summary(
                [item["unique_useful_results"] for item in detailed]
            ),
            "avg_total_latency_ms": build_latency_summary(
                [item["total_latency_ms"] for item in per_query]
            ),
            "avg_search_latency_ms": build_latency_summary(
                [item["search_latency_ms"] for item in per_query]
            ),
            "avg_llm_latency_ms": build_latency_summary(
                [item["llm_latency_ms"] for item in per_query]
            ),
            "avg_tool_latency_ms": build_latency_summary(
                [item["tool_latency_ms"] for item in per_query]
            ),
            "answer_correctness": build_discrete_score_summary(
                [item["answer_correctness"] for item in annotated],
                allowed_scores=[0, 1, 2],
            ),
            "answer_completeness": build_discrete_score_summary(
                [item["answer_completeness"] for item in annotated],
                allowed_scores=[0, 1, 2],
            ),
            "answer_groundedness": build_discrete_score_summary(
                [item["answer_groundedness"] for item in annotated],
                allowed_scores=[0, 1, 2],
            ),
            "abstention_quality": build_discrete_score_summary(
                [item["abstention_quality"] for item in annotated],
                allowed_scores=[0, 1, 2],
            ),
            "total_unique_useful_results": sum(
                int(item["unique_useful_results"] or 0) for item in detailed
            ),
        },
        "by_category": {
            "hit_at_3": build_category_macro(annotated, "hit_at_3", rate_metric=True),
            "mrr": build_category_macro(detailed, "mrr", rate_metric=False),
            "gold_doc_recall_at_5": build_category_macro(with_gold, "gold_doc_recall_at_5", rate_metric=True),
            "authoritative_at_5": build_category_macro(with_tiers, "authoritative_at_5", rate_metric=False),
            "empty_result_rate": build_category_macro(per_query, "empty_result", rate_metric=True),
            "core_correct": build_category_macro(annotated, "core_correct", rate_metric=False),
        },
        "providers": build_provider_scorecard(records, classify=classify),
        "per_query": per_query,
    }
    return report


def print_summary(report: Dict[str, Any], *, print_details: bool = False) -> None:
    summary = report["summary"]
    print("Search Quality Evaluation")
    print(f"- Total queries: {summary['total_queries']}")
    print(f"- Annotated queries: {summary['annotated_queries']}")
    print(f"- Detailed annotations: {summary['detailed_annotations']}")
    print(f"- Top3-only annotations: {summary['top3_only_annotations']}")

    for metric_name in (
        "hit_at_3",
        "answer_hit_at_3",
        "gold_doc_recall_at_3",
        "gold_doc_recall_at_5",
        "empty_result_rate",
        "route_correct",
        "fulltext_decision_correct",
        "hit_at_5",
        "chunk_hit_at_5",
        "mrr",
        "ndcg_at_5",
        "authoritative_at_3",
        "authoritative_at_5",
        "aggregator_at_5",
        "domain_diversity_at_5",
        "avg_unique_useful_results",
        "avg_total_latency_ms",
        "avg_search_latency_ms",
        "avg_llm_latency_ms",
        "avg_tool_latency_ms",
    ):
        metric = summary[metric_name]
        value = metric.get("value")
        denominator = metric.get("denominator")
        if value is None:
            print(f"- {metric_name}: N/A (denominator={denominator})")
            continue

        if "positives" in metric:
            print(f"- {metric_name}: {value:.4f} ({metric.get('positives', 0)}/{denominator})")
        elif metric_name.endswith("_correct"):
            print(f"- {metric_name}: {value:.4f} ({metric.get('positives', 0)}/{denominator})")
        elif metric_name.endswith("_latency_ms"):
            print(
                f"- {metric_name}: {value:.2f} "
                f"(p50={metric.get('p50'):.2f}, p95={metric.get('p95'):.2f}, "
                f"min={metric.get('min'):.2f}, max={metric.get('max'):.2f}, "
                f"denominator={denominator})"
            )
        else:
            print(f"- {metric_name}: {value:.4f} (denominator={denominator})")

    for metric_name in (
        "core_correct",
        "answer_correctness",
        "answer_completeness",
        "answer_groundedness",
        "abstention_quality",
    ):
        metric = summary[metric_name]
        value = metric.get("value")
        denominator = metric.get("denominator")
        counts = metric.get("counts") or {}
        if value is None:
            print(f"- {metric_name}: N/A (denominator={denominator})")
            continue
        print(
            f"- {metric_name}: {value:.4f} (denominator={denominator}, "
            f"counts={counts})"
        )

    print(f"- total_unique_useful_results: {summary['total_unique_useful_results']}")

    providers = (report.get("providers") or {}).get("answer_path") or {}
    if providers:
        print("\nProvider scorecard (answer path)")
        for name, entry in providers.items():
            print(
                f"- {name}: requests={entry['requests']} availability={entry['availability']} "
                f"empty_rate={entry['empty_rate']} p50={entry['latency_p50_ms']} p95={entry['latency_p95_ms']} "
                f"fallback_share={entry['fallback_share']} retained_contribution={entry['retained_contribution']}"
            )
    by_category = report.get("by_category") or {}
    if by_category:
        print("\nBy category (macro; n<5 counts only)")
        for metric_name, block in by_category.items():
            groups = ", ".join(
                f"{name}={entry['value']:.3f}(n={entry['n']})" if entry.get("value") is not None else f"{name}=n={entry['n']}"
                for name, entry in block["groups"].items()
            )
            print(f"- {metric_name}: macro={block['macro_average']} | {groups}")

    if not print_details:
        return

    print("\nPer-query details")
    for item in report["per_query"]:
        print(
            json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
            )
        )


def run_loop_audit(args: argparse.Namespace) -> Dict[str, Any]:
    """Run a query set through the unified LangGraph response loop."""
    queries = read_queries(args.queries_file)
    if not queries:
        raise SystemExit("No queries found in the provided file.")

    config = load_config(args.config)

    from orchestrators.react_agent_orchestrator import ReactAgentOrchestrator

    orchestrator = ReactAgentOrchestrator.create_from_config(
        config=config,
        engine="langgraph",
        max_iterations=args.max_iterations,
    )

    status_counts: Dict[str, int] = {}
    records: List[Dict[str, Any]] = []
    for index, query in enumerate(queries, start=1):
        print(f"[loop-audit] {index}/{len(queries)}: {query}")
        row: Dict[str, Any] = {"query_id": index, "query": query}
        try:
            result = orchestrator.answer(query)
            control = result.get("control") or {}
            status = control.get("loop_status") or "completed"
            row.update(
                {
                    "engine": control.get("engine"),
                    "loop_status": status,
                    "iterations": control.get("loop_iterations"),
                    "answer_chars": len(result.get("answer") or ""),
                    "llm_error": result.get("llm_error"),
                    "verdicts": control.get("loop_verdicts") or [],
                }
            )
        except Exception as exc:  # noqa: BLE001 - audit records errors as data
            row["error"] = str(exc)
            status = "error"
        status_counts[status] = status_counts.get(status, 0) + 1
        records.append(row)

    return {
        "meta": {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "num_queries": len(records),
            "max_iterations": args.max_iterations,
            "engine": "langgraph",
            "note": "All verdicts come from the unified deterministic critic and optional termination judge.",
        },
        "status_distribution": status_counts,
        "records": records,
    }


def main() -> None:
    args = parse_args()

    if args.command == "loop-audit":
        report = run_loop_audit(args)
        print(json.dumps(report["status_distribution"], ensure_ascii=False, indent=2))
        if args.output_file:
            ensure_parent_dir(args.output_file)
            with open(args.output_file, "w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
            print(f"Saved loop audit report to {args.output_file}")
        return

    if args.command == "map-external":
        handle_map_external_command(args)
        return

    if args.command == "dataset":
        handle_dataset_command(args)
        return

    if args.command == "collect":
        payload = collect_records(args)
        ensure_parent_dir(args.output_file)
        with open(args.output_file, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"Saved {len(payload['records'])} records to {args.output_file}")
        return

    with open(args.annotations_file, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    records = payload.get("records")
    if not isinstance(records, list):
        raise SystemExit("annotations file must contain a top-level 'records' list.")

    gold_map = load_gold_doc_map(args.gold_doc_file)
    classify = None
    if args.config:
        classify = build_tier_classifier(load_config(args.config))
    else:
        try:
            classify = build_tier_classifier(load_config(None))
        except (OSError, ValueError):
            classify = build_tier_classifier({})

    if (payload.get("meta") or {}).get("collect_mode") == "all_providers":
        relevance: Optional[Dict[str, Set[str]]] = None
        if args.relevance_annotations:
            with open(args.relevance_annotations, "r", encoding="utf-8") as handle:
                annotated_payload = json.load(handle)
            relevance = relevance_index_from_annotations(list(annotated_payload.get("records") or []))
        report = evaluate_all_providers(records, relevance=relevance, gold_map=gold_map, classify=classify)
        print(json.dumps({k: v for k, v in report.items() if k != "per_query"}, ensure_ascii=False, indent=2))
        if args.output_file:
            ensure_parent_dir(args.output_file)
            with open(args.output_file, "w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
            print(f"\nSaved all-providers report to {args.output_file}")
        return

    report = evaluate_records(records, gold_map=gold_map, classify=classify)
    print_summary(report, print_details=args.print_details)

    if args.output_file:
        ensure_parent_dir(args.output_file)
        with open(args.output_file, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"\nSaved evaluation report to {args.output_file}")


if __name__ == "__main__":
    main()
