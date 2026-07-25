from __future__ import annotations

from tests.search_quality_pipeline import (
    build_discrete_score_summary,
    build_latency_summary,
    evaluate_record,
    evaluate_records,
    filter_dataset_rows,
    map_external_dataset_rows,
)


def test_evaluate_record_with_detailed_relevance_labels():
    record = {
        "query": "test query",
        "search_hits": [
            {"rank": 1, "title": "A", "url": "https://example.com/a", "snippet": "A"},
            {"rank": 2, "title": "B", "url": "https://example.com/b", "snippet": "B"},
            {"rank": 3, "title": "C", "url": "https://example.com/c", "snippet": "C"},
            {"rank": 4, "title": "D", "url": "https://example.com/d", "snippet": "D"},
        ],
        "judgment": {
            "annotation_complete": True,
            "judgment_mode": "detailed",
            "relevant_ranks": [2, 4],
            "relevant_urls": [],
            "top3_has_answer_evidence": None,
        },
        "response_times": {
            "total_ms": 1200,
            "search_sources": [
                {"source": "google", "duration_ms": 200},
                {"source": "brave", "duration_ms": 150},
            ],
            "llm_calls": [
                {"label": "rewrite", "duration_ms": 300},
                {"label": "answer", "duration_ms": 400},
            ],
            "tool_calls": [{"tool": "postcheck", "duration_ms": 50}],
        },
    }

    result = evaluate_record(record)

    assert result["hit_at_3"] is True
    assert result["hit_at_5"] is True
    assert result["mrr"] == 0.5
    assert result["first_relevant_rank"] == 2
    assert result["unique_useful_results"] == 2
    assert result["total_latency_ms"] == 1200
    assert result["search_latency_ms"] == 350
    assert result["llm_latency_ms"] == 700
    assert result["tool_latency_ms"] == 50


def test_top3_only_annotation_only_contributes_to_hit_at_3():
    record = {
        "query": "test query",
        "search_hits": [
            {"rank": 1, "title": "A", "url": "https://example.com/a", "snippet": "A"},
            {"rank": 2, "title": "B", "url": "https://example.com/b", "snippet": "B"},
            {"rank": 3, "title": "C", "url": "https://example.com/c", "snippet": "C"},
        ],
        "judgment": {
            "annotation_complete": True,
            "judgment_mode": "top3_only",
            "relevant_ranks": [],
            "relevant_urls": [],
            "top3_has_answer_evidence": True,
        },
    }

    result = evaluate_record(record)

    assert result["hit_at_3"] is True
    assert result["hit_at_5"] is None
    assert result["mrr"] is None
    assert result["unique_useful_results"] is None


def test_evaluate_records_aggregates_detailed_and_top3_only_modes():
    records = [
        {
            "query": "query one",
            "search_hits": [
                {"rank": 1, "title": "A", "url": "https://example.com/a", "snippet": "A"},
                {"rank": 2, "title": "B", "url": "https://example.com/b", "snippet": "B"},
            ],
            "judgment": {
                "annotation_complete": True,
                "judgment_mode": "detailed",
                "relevant_ranks": [1],
                "relevant_urls": [],
                "top3_has_answer_evidence": None,
                "route_correct": 1,
                "fulltext_decision_correct": 1,
                "chunk_hit_at_5": 1,
                "answer_correctness": 2,
                "answer_completeness": 2,
                "answer_groundedness": 2,
                "abstention_quality": 1,
            },
            "response_times": {
                "total_ms": 1000,
                "search_sources": [{"source": "google", "duration_ms": 250}],
                "llm_calls": [{"label": "answer", "duration_ms": 500}],
                "tool_calls": [{"tool": "postcheck", "duration_ms": 100}],
            },
        },
        {
            "query": "query two",
            "search_hits": [
                {"rank": 1, "title": "C", "url": "https://example.com/c", "snippet": "C"},
                {"rank": 2, "title": "D", "url": "https://example.com/d", "snippet": "D"},
            ],
            "judgment": {
                "annotation_complete": True,
                "judgment_mode": "top3_only",
                "relevant_ranks": [],
                "relevant_urls": [],
                "top3_has_answer_evidence": False,
                "route_correct": 0,
                "fulltext_decision_correct": 0,
                "chunk_hit_at_5": 0,
                "answer_correctness": 1,
                "answer_completeness": 1,
                "answer_groundedness": 1,
                "abstention_quality": 2,
            },
            "response_times": {
                "total_ms": 2000,
                "search_sources": [
                    {"source": "google", "duration_ms": 300},
                    {"source": "mcp", "duration_ms": 200},
                ],
                "llm_calls": [
                    {"label": "rewrite", "duration_ms": 600},
                    {"label": "answer", "duration_ms": 400},
                ],
                "tool_calls": [],
            },
        },
    ]

    report = evaluate_records(records)
    summary = report["summary"]

    assert summary["annotated_queries"] == 2
    assert summary["detailed_annotations"] == 1
    assert summary["top3_only_annotations"] == 1
    assert summary["hit_at_3"]["value"] == 0.5
    assert summary["route_correct"]["value"] == 0.5
    assert summary["fulltext_decision_correct"]["value"] == 0.5
    assert summary["hit_at_5"]["value"] == 1.0
    assert summary["chunk_hit_at_5"]["value"] == 0.5
    assert summary["mrr"]["value"] == 1.0
    assert summary["avg_unique_useful_results"]["value"] == 1.0
    assert summary["avg_total_latency_ms"]["value"] == 1500.0
    assert summary["avg_total_latency_ms"]["p50"] == 1500.0
    assert summary["avg_total_latency_ms"]["p95"] == 1950.0
    assert summary["avg_search_latency_ms"]["value"] == 375.0
    assert summary["avg_llm_latency_ms"]["value"] == 750.0
    assert summary["avg_tool_latency_ms"]["value"] == 50.0
    assert summary["answer_correctness"]["value"] == 1.5
    assert summary["answer_completeness"]["value"] == 1.5
    assert summary["answer_groundedness"]["value"] == 1.5
    assert summary["abstention_quality"]["value"] == 1.5
    assert summary["answer_correctness"]["counts"] == {"0": 0, "1": 1, "2": 1}


def test_filter_dataset_rows_by_category():
    rows = [
        {"id": "Q001", "category": "small_talk", "query": "a", "dataset_layers": ""},
        {"id": "Q002", "category": "skill", "query": "b", "dataset_layers": ""},
        {"id": "Q003", "category": "small_talk", "query": "c", "dataset_layers": ""},
    ]

    filtered = filter_dataset_rows(rows, categories=["small_talk"])

    assert [row["id"] for row in filtered] == ["Q001", "Q003"]


def test_filter_dataset_rows_by_query_id():
    rows = [
        {"id": "Q001", "category": "small_talk", "query": "a", "dataset_layers": ""},
        {"id": "Q002", "category": "skill", "query": "b", "dataset_layers": ""},
        {"id": "Q003", "category": "small_talk", "query": "c", "dataset_layers": ""},
    ]

    filtered = filter_dataset_rows(rows, query_ids=["q002"])

    assert [row["id"] for row in filtered] == ["Q002"]


def test_filter_dataset_rows_supports_combined_filters_and_limit():
    rows = [
        {"id": "Q001", "category": "small_talk", "query": "a", "dataset_layers": ""},
        {"id": "Q002", "category": "skill", "query": "b", "dataset_layers": ""},
        {"id": "Q003", "category": "skill", "query": "c", "dataset_layers": ""},
    ]

    filtered = filter_dataset_rows(
        rows,
        categories=["skill"],
        query_ids=["Q002", "Q003"],
        limit=1,
    )

    assert [row["id"] for row in filtered] == ["Q002"]


def test_filter_dataset_rows_by_layer():
    rows = [
        {"id": "Q001", "category": "external", "query": "a", "dataset_layers": "route_intent|full_text_trigger"},
        {"id": "Q002", "category": "external", "query": "b", "dataset_layers": "gold_doc"},
        {"id": "Q003", "category": "external", "query": "c", "dataset_layers": "final_answer|gold_chunk"},
    ]

    filtered = filter_dataset_rows(rows, layers=["gold_chunk"])

    assert [row["id"] for row in filtered] == ["Q003"]


def test_evaluate_record_includes_extended_judgment_scores():
    record = {
        "query": "extended metrics query",
        "search_hits": [
            {"rank": 1, "title": "A", "url": "https://example.com/a", "snippet": "A"},
            {"rank": 2, "title": "B", "url": "https://example.com/b", "snippet": "B"},
        ],
        "judgment": {
            "annotation_complete": True,
            "judgment_mode": "detailed",
            "relevant_ranks": [2],
            "relevant_urls": [],
            "top3_has_answer_evidence": None,
            "route_correct": 1,
            "fulltext_decision_correct": 0,
            "chunk_hit_at_5": 1,
            "answer_correctness": 2,
            "answer_completeness": 1,
            "answer_groundedness": 2,
            "abstention_quality": 0,
        },
    }

    result = evaluate_record(record)

    assert result["route_correct"] == 1
    assert result["fulltext_decision_correct"] == 0
    assert result["chunk_hit_at_5"] == 1
    assert result["answer_correctness"] == 2
    assert result["answer_completeness"] == 1
    assert result["answer_groundedness"] == 2
    assert result["abstention_quality"] == 0


def test_build_discrete_score_summary_tracks_average_and_counts():
    summary = build_discrete_score_summary([2, 1, 1, 0, None], allowed_scores=[0, 1, 2])

    assert summary["denominator"] == 4
    assert summary["value"] == 1.0
    assert summary["counts"] == {"0": 1, "1": 2, "2": 1}


def test_build_latency_summary_tracks_average_and_percentiles():
    summary = build_latency_summary([100.0, 200.0, None, 400.0])

    assert summary["denominator"] == 3
    assert summary["value"] == 700.0 / 3.0
    assert summary["p50"] == 200.0
    assert summary["p95"] == 380.0
    assert summary["min"] == 100.0
    assert summary["max"] == 400.0


def test_map_external_dataset_rows_merges_layers_by_query(tmp_path):
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()

    (dataset_dir / "route_intent_dataset.csv").write_text(
        "\nqid,query,intent_label,expected_route\n"
        "route001,What is the speed of light?,general_knowledge,general_web\n",
        encoding="utf-8",
    )
    (dataset_dir / "full_text_trigger_dataset.csv").write_text(
        "\nqid,query,need_search,need_fulltext,reason\n"
        "trigger001,What is the speed of light?,True,False,summary is enough\n",
        encoding="utf-8",
    )
    (dataset_dir / "gold_doc_dataset.csv").write_text(
        "\nqid,query,gold_doc_url\n"
        "gdoc001,What is the speed of light?,https://example.com/doc\n",
        encoding="utf-8",
    )
    (dataset_dir / "gold_chunk_dataset.csv").write_text(
        "\nqid,query,gold_doc_url,gold_span,reference_answer\n"
        "gchunk001,What is the speed of light?,https://example.com/doc,exact value,299792458 m/s\n",
        encoding="utf-8",
    )
    (dataset_dir / "final_answer_dataset.csv").write_text(
        "\nqid,query,reference_answer,must_include_facts,allowed_sources,time_sensitive\n"
        "final001,What is the speed of light?,299792458 m/s,value 299792458 m/s,example.com,False\n",
        encoding="utf-8",
    )

    records, summary = map_external_dataset_rows(str(dataset_dir))

    assert len(records) == 1
    record = records[0]
    assert record["id"] == "final001"
    assert record["dataset_layers"] == "final_answer|full_text_trigger|gold_chunk|gold_doc|route_intent"
    assert record["category"] == "final_answer"
    assert record["ideal_route"] == "web_search_summary"
    assert record["gold_doc_url"] == "https://example.com/doc"
    assert record["must_include_facts"] == "value 299792458 m/s"
    assert summary["merged_queries"] == 1
