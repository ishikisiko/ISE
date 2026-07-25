from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set
from urllib.parse import urlparse

# Add project directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_JUDGMENT_MODE = "detailed"
TOP3_ONLY_JUDGMENT_MODE = "top3_only"
SUPPORTED_JUDGMENT_MODES = {DEFAULT_JUDGMENT_MODE, TOP3_ONLY_JUDGMENT_MODE}


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
        required=True,
        help="UTF-8 text file with one query per line. Blank lines and # comments are ignored.",
    )
    collect_parser.add_argument(
        "--output-file",
        required=True,
        help="Where to save the collected search result file.",
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
        "top3_has_answer_evidence": None,
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


def collect_records(args: argparse.Namespace) -> Dict[str, Any]:
    queries = read_queries(args.queries_file)
    if not queries:
        raise SystemExit("No queries found in the provided file.")

    config = load_config(args.config)
    if args.model:
        config["LLM_PROVIDER"] = args.model
    elif args.provider:
        config["LLM_PROVIDER"] = args.provider

    orchestrator = build_orchestrator_for_collection(
        config,
        data_path=args.data_path,
        disable_rerank=args.disable_rerank,
        show_timings=args.show_timings,
    )

    records: List[Dict[str, Any]] = []
    for index, query in enumerate(queries, start=1):
        print(f"[collect] {index}/{len(queries)} {query}")
        result = orchestrator.answer(
            query,
            num_search_results=args.num_results,
            per_source_search_results=args.num_results,
            num_retrieved_docs=args.num_results,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            allow_search=True,
            force_search=args.force_search,
        )

        control = result.get("control") or {}
        records.append(
            {
                "query_id": index,
                "query": query,
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "search_query": result.get("search_query"),
                "search_hits": normalize_search_hits(result.get("search_hits") or []),
                "response_times": result.get("response_times") or {},
                "control": {
                    "search_mode": control.get("search_mode"),
                    "domain": control.get("domain"),
                    "keywords": control.get("keywords") or [],
                    "selected_sources": control.get("selected_sources") or [],
                    "search_sources_active": control.get("search_sources_active") or [],
                    "search_sources_requested": control.get("search_sources_requested") or [],
                    "search_sources_missing": control.get("search_sources_missing") or [],
                },
                "judgment": default_judgment(),
            }
        )

    payload = {
        "meta": {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "pipeline": "search_quality_pipeline",
            "num_queries": len(records),
            "num_results": args.num_results,
            "force_search": bool(args.force_search),
            "judgment_instructions": [
                "详细标注模式：把 judgment.annotation_complete 设为 true，保留 judgment_mode='detailed'，填写 relevant_ranks 或 relevant_urls。",
                "轻量标注模式：把 judgment.annotation_complete 设为 true，设 judgment_mode='top3_only'，只填写 top3_has_answer_evidence。",
                "如果采用 detailed 模式但当前返回结果里没有相关结果，可以保留 relevant_ranks/relevant_urls 为空数组。",
            ],
        },
        "records": records,
    }
    return payload


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


def evaluate_record(record: Dict[str, Any]) -> Dict[str, Any]:
    query = str(record.get("query") or "")
    judgment = record.get("judgment") or {}
    annotation_complete = bool(judgment.get("annotation_complete"))
    mode = str(judgment.get("judgment_mode") or DEFAULT_JUDGMENT_MODE).strip().lower()
    if mode not in SUPPORTED_JUDGMENT_MODES:
        mode = DEFAULT_JUDGMENT_MODE

    metrics: Dict[str, Any] = {
        "query": query,
        "annotation_complete": annotation_complete,
        "judgment_mode": mode,
        "hit_at_3": None,
        "hit_at_5": None,
        "mrr": None,
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
        "total_latency_ms": extract_total_latency_ms(record),
        "search_latency_ms": extract_component_latency_ms(record, "search_sources"),
        "llm_latency_ms": extract_component_latency_ms(record, "llm_calls"),
        "tool_latency_ms": extract_component_latency_ms(record, "tool_calls"),
    }

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

    relevant_ranks = extract_relevant_ranks(record)
    first_relevant_rank = relevant_ranks[0] if relevant_ranks else None
    hits = record.get("search_hits") or []

    unique_useful_keys = set()
    for rank in relevant_ranks:
        if 1 <= rank <= len(hits):
            unique_useful_keys.add(make_hit_key(hits[rank - 1]))

    metrics.update(
        {
            "hit_at_3": any(rank <= 3 for rank in relevant_ranks),
            "hit_at_5": any(rank <= 5 for rank in relevant_ranks),
            "mrr": (1.0 / first_relevant_rank) if first_relevant_rank else 0.0,
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


def evaluate_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    per_query = [evaluate_record(record) for record in records]
    annotated = [item for item in per_query if item["annotation_complete"]]
    detailed = [item for item in annotated if item["judgment_mode"] == DEFAULT_JUDGMENT_MODE]
    top3_only = [item for item in annotated if item["judgment_mode"] == TOP3_ONLY_JUDGMENT_MODE]

    report = {
        "summary": {
            "total_queries": len(records),
            "annotated_queries": len(annotated),
            "detailed_annotations": len(detailed),
            "top3_only_annotations": len(top3_only),
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
        "route_correct",
        "fulltext_decision_correct",
        "hit_at_5",
        "chunk_hit_at_5",
        "mrr",
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

        if metric_name.startswith("hit_at_") or metric_name.startswith("chunk_hit_at_"):
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

    report = evaluate_records(records)
    print_summary(report, print_details=args.print_details)

    if args.output_file:
        ensure_parent_dir(args.output_file)
        with open(args.output_file, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"\nSaved evaluation report to {args.output_file}")


if __name__ == "__main__":
    main()
