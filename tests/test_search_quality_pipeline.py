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


# --- Q1-01 / Q1-02 / Q1-03 / Q1-05 additions ---------------------------------

import argparse
import math

import pytest

from tests.search_quality_pipeline import (
    build_category_macro,
    build_provider_scorecard,
    collect_records,
    evaluate_all_providers,
    gold_doc_match,
    load_gold_doc_map,
    ndcg_at_k,
    prefill_gold_judgment,
    relevance_index_from_annotations,
)


def test_ndcg_hand_computed_example():
    # DCG = 2/log2(2) + 0/log2(3) + 1/log2(4) = 2.5; IDCG = 2 + 1/log2(3)
    expected = 2.5 / (2 + 1 / math.log2(3))
    assert ndcg_at_k([2, 0, 1], 5) == pytest.approx(expected)
    assert ndcg_at_k([0, 0, 0], 5) == 0.0
    assert ndcg_at_k([], 5) is None
    assert ndcg_at_k([1, 2], 1) == pytest.approx((1 / 1) / (2 / 1))


def test_gold_doc_match_normalizes_host_query_and_path_prefix():
    gold = "https://www.britannica.com/place/France"
    assert gold_doc_match("https://britannica.com/place/France?utm=1#facts", gold)
    assert gold_doc_match("https://www.britannica.com/place/France/Government", gold)
    assert not gold_doc_match("https://www.britannica.com/place/Francesca", gold)
    assert not gold_doc_match("https://example.com/place/France", gold)
    assert gold_doc_match("https://internationalmensday.com/about", "https://internationalmensday.com/")


def test_evaluate_record_graded_relevance_gold_and_tiers():
    record = {
        "query": "Which moon is the largest?",
        "search_hits": [
            {"rank": 1, "title": "Quora", "url": "https://www.quora.com/largest-moon", "snippet": ""},
            {"rank": 2, "title": "NASA", "url": "https://solarsystem.nasa.gov/moons/jupiter-moons/ganymede/in-depth/?x=1", "snippet": ""},
            {"rank": 3, "title": "Blog", "url": "https://blog.example.org/moons", "snippet": ""},
        ],
        "control": {"query_analysis": {"entities": ["NASA"], "comparison_members": []}},
        "judgment": {
            "annotation_complete": True,
            "judgment_mode": "detailed",
            "relevant_ranks": [],
            "relevant_urls": [],
            "relevance_grades": [1, 2, 0],
            "core_correct": 2,
        },
    }
    classify = lambda url, entities: "official" if "nasa.gov" in url else ("aggregator" if "quora" in url else "unknown")
    result = evaluate_record(
        record,
        gold_urls=["https://solarsystem.nasa.gov/moons/jupiter-moons/ganymede/in-depth/"],
        classify=classify,
    )
    assert result["hit_at_3"] is True
    assert result["answer_hit_at_3"] is True
    assert result["mrr"] == 1.0  # first relevant (grade>=1) is rank 1
    assert result["ndcg_at_5"] == pytest.approx(ndcg_at_k([1, 2, 0], 5))
    assert result["gold_doc_rank"] == 2
    assert result["gold_doc_recall_at_3"] is True and result["gold_doc_recall_at_5"] is True
    assert result["authoritative_at_3"] == pytest.approx(1 / 3)
    assert result["aggregator_at_3"] == pytest.approx(1 / 3)
    assert result["domain_diversity_at_5"] == pytest.approx(3 / 5)
    assert result["core_correct"] == 2
    assert result["empty_result"] is False
    # Binary annotations still produce the historical MRR / Hit@k values.
    legacy = dict(record, judgment={"annotation_complete": True, "judgment_mode": "detailed", "relevant_ranks": [2], "relevant_urls": []})
    legacy_result = evaluate_record(legacy)
    assert legacy_result["mrr"] == 0.5 and legacy_result["hit_at_3"] is True
    assert legacy_result["answer_hit_at_3"] is None


def test_evaluate_records_reports_empty_rate_categories_and_provider_table():
    def record(query, category, hits, calls, decisions, annotated=True):
        return {
            "query": query,
            "category": category,
            "search_hits": hits,
            "search_api_calls": calls,
            "control": {
                "query_analysis": {"entities": [], "comparison_members": []},
                "evidence_coverage": {"decisions": decisions},
            },
            "judgment": {"annotation_complete": annotated, "judgment_mode": "top3_only", "top3_has_answer_evidence": bool(hits)},
        }

    hit = {"rank": 1, "title": "A", "url": "https://a.example.com/x", "snippet": ""}
    records = [
        record(
            f"q{i}",
            "summary",
            [hit],
            [
                {"provider": "brave", "status": "error", "reason": "HTTP 429 quota", "duration_ms": 10},
                {"provider": "firecrawl", "status": "done", "result_count": 1, "duration_ms": 30, "fallback": True,
                 "records": [{"url": "https://a.example.com/x"}], "credits": 1.0},
                {"provider": "anysearch", "status": "error", "reason": "skipped: provider does not honour the site: operator"},
                {"provider": "brave", "label": "官方域解析 · brave", "target": "example", "status": "done", "result_count": 3},
            ],
            [{"reference": "https://a.example.com/x", "decision": "retained"}],
        )
        for i in range(6)
    ] + [record("empty", "fulltext", [], [], [], annotated=False)]
    report = evaluate_records(records)
    summary = report["summary"]
    assert summary["empty_result_rate"]["value"] == pytest.approx(1 / 7)
    assert report["by_category"]["hit_at_3"]["groups"]["summary"]["value"] == 1.0
    assert report["by_category"]["empty_result_rate"]["groups"]["fulltext"]["count_only"] is True
    providers = report["providers"]["answer_path"]
    assert providers["brave"]["availability"] == 0.0
    assert providers["brave"]["error_rate_by_type"] == {"quota": 6}
    assert providers["firecrawl"]["fallback_share"] == 1.0
    assert providers["firecrawl"]["retained_contribution"] == 1.0
    assert providers["firecrawl"]["credits_known"] == 6.0
    assert providers["firecrawl"]["cost_per_retained"] == 1.0
    assert providers["anysearch"]["attempted"] == 0
    assert providers["anysearch"]["availability"] is None
    assert "brave" in report["providers"]["official_domain_discovery"]
    assert "brave" in providers


def test_category_macro_applies_min_sample_rule():
    per_query = [{"category": "a", "hit_at_3": True}] * 5 + [{"category": "b", "hit_at_3": False}] * 2
    macro = build_category_macro(per_query, "hit_at_3", rate_metric=True)
    assert macro["groups"]["a"]["value"] == 1.0
    assert macro["groups"]["b"] == {"n": 2, "value": None, "count_only": True, "positives": 0}
    assert macro["macro_average"] == 1.0


def test_collect_records_projects_loop_control_and_prefills_gold(tmp_path, monkeypatch):
    class FakeOrchestrator:
        def answer(self, query, **kwargs):
            self.kwargs = kwargs
            return {
                "answer": "Paris [E1].",
                "search_hits": [{"title": "France", "url": "https://www.britannica.com/place/France", "snippet": "Paris"}],
                "search_api_calls": [{"provider": "brave", "status": "done", "result_count": 1}],
                "evidence_records": [
                    {"source_type": "web", "source_tier": "unknown", "reference": "https://www.britannica.com/place/France",
                     "title": "France", "content": "Paris", "metadata": {"eid": 1, "retrieval_kind": "general_search", "api_key": "leak"}}
                ],
                "response_times": {"total_ms": 10},
                "control": {
                    "search_mode": "agentic_loop",
                    "keywords": [],
                    "loop_status": "succeeded",
                    "loop_termination_reason": "succeeded",
                    "loop_iterations": 2,
                    "query_analysis": {"intent_shape": "information_request", "entities": ["France"]},
                    "execution_trace": {"events": [], "executed": ["web_search"]},
                    "evidence_coverage": {"entries": 1, "retained": 1, "decisions": []},
                    "loop_verdicts": [{"iteration": 1, "reason": "constraints_satisfied"}],
                    "termination_policy": {"tool_budgets": {"web_search": {"limit": 3, "used": 1}}},
                    "autonomy": {"mode": "guided", "source": "request"},
                },
            }

    fake = FakeOrchestrator()
    monkeypatch.setattr("tests.search_quality_pipeline.build_orchestrator_for_collection", lambda *a, **k: fake)
    monkeypatch.setattr("tests.search_quality_pipeline.load_config", lambda path: {})
    dataset = tmp_path / "queries.csv"
    dataset.write_text("qid,query,category\ngdoc003,What is the capital of France?,gold_doc\n", encoding="utf-8")
    gold = tmp_path / "gold.csv"
    gold.write_text("qid,query,gold_doc_url\ngdoc003,What is the capital of France?,https://www.britannica.com/place/France\n", encoding="utf-8")
    args = argparse.Namespace(
        queries_file=None, dataset_file=str(dataset), gold_doc_file=str(gold), all_providers=False, autonomy="guided",
        config=None, model=None, provider=None, data_path=str(tmp_path), disable_rerank=True, show_timings=True,
        num_results=5, max_tokens=512, temperature=0.1, force_search=True,
    )
    payload = collect_records(args)
    record = payload["records"][0]
    assert fake.kwargs["autonomy_mode"] == "guided"
    assert record["qid"] == "gdoc003" and record["category"] == "gold_doc"
    control = record["control"]
    assert control["query_analysis"]["entities"] == ["France"]
    assert control["loop_verdicts"][0]["reason"] == "constraints_satisfied"
    assert control["tool_budgets"]["web_search"] == {"limit": 3, "used": 1}
    assert control["termination_reason"] == "succeeded"
    assert "selected_sources" not in control and "keywords" not in control
    assert record["search_api_calls"][0]["provider"] == "brave"
    assert record["evidence_records"][0]["metadata"]["eid"] == 1
    assert "api_key" not in record["evidence_records"][0]["metadata"]
    assert record["judgment"]["gold_doc_urls"] == ["https://www.britannica.com/place/France"]
    assert record["judgment"]["relevant_urls"] == ["https://www.britannica.com/place/France"]
    assert payload["meta"]["collect_mode"] == "single_chain"
    # The pre-filled judgment is not complete until a reviewer confirms it.
    assert record["judgment"]["annotation_complete"] is False
    assert load_gold_doc_map(str(gold)) == {"what is the capital of france?": ["https://www.britannica.com/place/France"]}


def test_evaluate_all_providers_unique_yield_and_agreement():
    annotated = [
        {
            "query": "q1",
            "search_hits": [{"url": "https://a.example/1"}, {"url": "https://b.example/2"}],
            "judgment": {"annotation_complete": True, "judgment_mode": "detailed", "relevant_ranks": [1, 2], "relevant_urls": ["https://c.example/3"]},
        }
    ]
    relevance = relevance_index_from_annotations(annotated)
    assert relevance == {"q1": {"a.example/1", "b.example/2", "c.example/3"}}
    records = [
        {
            "query": "q1",
            "provider_results": [
                {"provider": "brave", "status": "done", "duration_ms": 10, "results": [{"url": "https://a.example/1"}, {"url": "https://x.example/9"}]},
                {"provider": "tavily", "status": "done", "duration_ms": 20, "results": [{"url": "https://a.example/1"}, {"url": "https://c.example/3"}], "credits": 1},
                {"provider": "parallel", "status": "error", "error": "timeout", "duration_ms": 5, "results": []},
            ],
        }
    ]
    report = evaluate_all_providers(records, relevance=relevance)
    providers = report["providers"]
    assert providers["brave"]["relevant_contribution"] == 0.5
    assert providers["brave"]["unique_yield"] == 0.0
    assert providers["tavily"]["relevant_contribution"] == 1.0
    assert providers["tavily"]["unique_yield"] == 0.5
    assert providers["tavily"]["cost_per_relevant"] == 0.5
    assert providers["parallel"]["availability"] == 0.0
    assert providers["parallel"]["error_rate_by_type"] == {"timeout": 1}
    assert report["agreement"]["pairwise_jaccard"]["brave|tavily"] == pytest.approx(1 / 3)
    assert report["agreement"]["top1_consensus_mean"] == 1.0
    assert report["relevance_source"] == "annotations"
