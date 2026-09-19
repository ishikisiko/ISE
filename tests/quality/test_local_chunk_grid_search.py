"""Q1-08: chunk-level grid-search metrics on an in-memory vector stub."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Tuple

import pytest

from tests.local_chunk_grid_search import evaluate_setting, load_queries, rank_results

pytestmark = pytest.mark.quality_offline


@dataclass
class _Doc:
    content: str
    source: str


class _StubStore:
    """Deterministic ranked results keyed by query text; scores are L2 distances."""

    plan = {
        "q-doc": [("gold span here", "corpus/a.md", 0.20), ("other", "corpus/b.md", 0.30), ("other", "corpus/c.md", 0.40)],
        "q-multi": [("first span", "corpus/a.md", 0.10), ("noise", "corpus/b.md", 0.15), ("second span", "corpus/a.md", 0.50)],
        "q-miss": [("noise", "corpus/b.md", 0.10), ("noise", "corpus/c.md", 0.20), ("gold span here", "corpus/a.md", 0.90)],
        "q-absent": [("noise", "corpus/b.md", 0.70), ("noise", "corpus/c.md", 0.80)],
        "q-cross": [("noise", "corpus/c.md", 0.10), ("cross span", "corpus/d.txt", 0.20)],
    }

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def index_from_directory(self, path: str) -> int:
        return 42

    def search_with_scores(self, query: str, *, k: int) -> List[Tuple[_Doc, float]]:
        return [(_Doc(content, source), score) for content, source, score in self.plan[query][:k]]


def _queries():
    return [
        {"id": "1", "query": "q-doc", "gold_doc_id": "a.md", "gold_spans": ["gold span here"], "is_absent": False, "language": "en"},
        {"id": "2", "query": "q-multi", "gold_doc_id": "a.md", "gold_spans": ["first span", "second span"], "is_absent": False, "language": "en"},
        {"id": "3", "query": "q-miss", "gold_doc_id": "a.md", "gold_spans": ["gold span here"], "is_absent": False, "language": "en"},
        {"id": "4", "query": "q-absent", "gold_doc_id": "", "gold_spans": [], "is_absent": True, "language": "en"},
        {"id": "5", "query": "q-cross", "gold_doc_id": "d.txt", "gold_spans": ["cross span"], "is_absent": False, "language": "zh->en"},
    ]


def test_metrics_hand_computed_on_stub_store():
    result = evaluate_setting(
        data_path="unused",
        queries=_queries(),
        config={},
        embedding_model=None,
        chunk_size=1000,
        chunk_overlap=200,
        top_ks=[3, 5],
        store_factory=_StubStore,
    )
    assert result["chunk_count"] == 42
    k3 = result["by_k"]["3"]
    # answerable = 4 (absent excluded). doc hits @3: q-doc(1), q-multi(1), q-miss(3), q-cross(2) -> 4/4
    assert k3["answerable_queries"] == 4
    assert k3["doc_hit_at_k"] == 1.0
    assert k3["mrr"] == pytest.approx((1 + 1 + 1 / 3 + 1 / 2) / 4, abs=1e-4)
    # chunk hits @3: q-doc rank1, q-multi rank1, q-miss rank3, q-cross rank2 -> 4/4; mrr_chunk same as doc here
    assert k3["chunk_hit_at_k"] == 1.0
    assert k3["mrr_chunk"] == pytest.approx((1 + 1 + 1 / 3 + 1 / 2) / 4, abs=1e-4)
    # context recall: 1, 1 (both spans within top3), 1, 1 -> 1.0
    assert k3["context_recall"] == 1.0
    # margins: q-doc 0.20-0.30=-0.10; q-multi 0.10-0.15=-0.05; q-miss 0.90-0.10=0.80; q-cross 0.20-0.10=0.10
    assert k3["score_margin_median"] == pytest.approx((-0.05 + 0.10) / 2)
    assert k3["score_margin_negative_share"] == 0.5
    assert k3["abstention_candidates"] == [0.70]
    assert k3["cross_lingual_hit"] == 1.0
    k5 = result["by_k"]["5"]
    assert k5["chunk_hit_at_k"] == 1.0
    per_query = {row["id"]: row for row in result["queries"]}
    assert per_query["3"]["by_k"]["3"]["chunk_rank"] == 3
    assert per_query["4"]["by_k"]["3"]["abstention_min_distance"] == 0.70
    assert result["doc_hit_at_k"] == 1.0 and result["chunk_hit_at_k"] == 1.0


def test_top_k_two_limits_hits():
    result = evaluate_setting(
        data_path="unused", queries=_queries(), config={}, embedding_model=None,
        chunk_size=500, chunk_overlap=50, top_ks=[2], store_factory=_StubStore,
    )
    block = result["by_k"]["2"]
    # q-miss gold is rank 3 -> miss at k=2; q-multi second span outside top2 -> recall 0.5
    assert block["chunk_hit_at_k"] == 0.75
    assert block["context_recall"] == pytest.approx((1 + 0.5 + 0 + 1) / 4)
    ranked = [result, dict(result, by_k={"2": dict(block, chunk_hit_at_k=1.0, mrr_chunk=1.0)}, chunk_size=800)]
    rank_results(ranked, 2)
    assert ranked[0]["chunk_size"] == 800


def test_load_queries_reads_gold_csv_and_legacy_layout(tmp_path):
    gold = tmp_path / "gold.csv"
    gold.write_text(
        "qid,query,gold_doc_id,gold_span,is_absent,language\n"
        "a,question one,doc.md,span one || span two,0,en\n"
        "b,none here,,,1,zh\n",
        encoding="utf-8",
    )
    rows = load_queries(str(gold))
    assert rows[0]["gold_spans"] == ["span one", "span two"]
    assert rows[1]["is_absent"] is True and rows[1]["gold_doc_id"] == ""
    legacy = tmp_path / "legacy.csv"
    legacy.write_text('"id","query","category","gold_doc_id"\n"Q1","x","local_rag","doc.md"\n"Q2","y","skill",""\n', encoding="utf-8")
    legacy_rows = load_queries(str(legacy), category="local_rag")
    assert [row["id"] for row in legacy_rows] == ["Q1"]
    assert legacy_rows[0]["gold_spans"] == []


# ---------------------------------------------------------------------------
# Multi-model / absent separation / rerank (2026-09-19)
# ---------------------------------------------------------------------------

from tests.local_chunk_grid_search import (  # noqa: E402
    absent_separation,
    build_model_summary,
    load_query_files,
    parse_model_spec,
    rerank_ranked,
    run_model,
)


def test_parse_model_spec_keeps_configured_provider_and_builds_hf_presets():
    base = {"embeddings": {"provider": "openai_compatible", "model": "qwen3.7-text-embedding", "base_url": "https://x", "api_key": "k"}}
    bare = parse_model_spec("qwen3.7-text-embedding-flash", base)
    assert bare["provider"] == "openai_compatible" and bare["model"] == "qwen3.7-text-embedding-flash"
    assert bare["model_name"] == "qwen3.7-text-embedding-flash"
    assert bare["config"]["embeddings"]["api_key"] == "k"  # credentials untouched
    assert base["embeddings"]["model"] == "qwen3.7-text-embedding"  # base config not mutated

    hf = parse_model_spec("huggingface:intfloat/multilingual-e5-small", base)
    assert hf["provider"] == "huggingface"
    assert hf["config"]["embeddings"] == {
        "provider": "huggingface", "model": "intfloat/multilingual-e5-small",
        "encode_kwargs": {"normalize_embeddings": True, "prompt": "passage: "},
        "query_encode_kwargs": {"normalize_embeddings": True, "prompt": "query: "},
    }
    plain = parse_model_spec("huggingface:sentence-transformers/all-MiniLM-L6-v2", base)
    assert plain["config"]["embeddings"]["encode_kwargs"] == {"normalize_embeddings": True}

    default = parse_model_spec("", base)
    assert default["model"] == "qwen3.7-text-embedding" and default["model_name"] is None


def test_absent_separation_auroc_and_reject_rate():
    # answerable distances mostly small, absent mostly large; one absent overlaps
    answerable = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2]
    absent = [1.3, 1.4, 0.55]
    result = absent_separation(answerable, absent)
    # pairs: 1.3 and 1.4 beat all 10; 0.55 beats 0.3,0.4,0.5 -> (10+10+3)/30
    assert result["auroc"] == pytest.approx(23 / 30, abs=1e-4)
    # keep 95% of 10 answerable -> ceil(9.5)=10 -> threshold is the max answerable (1.2)
    assert result["threshold"] == 1.2
    assert result["reject_at_answerable_recall"] == pytest.approx(2 / 3, abs=1e-4)
    assert absent_separation([], absent)["auroc"] is None
    assert absent_separation([0.5], [0.5])["auroc"] == 0.5


class _StubReranker:
    def __init__(self, order):
        self.order = order  # list of (index, relevance)
        self.calls = []

    def rerank_texts(self, query, texts):
        self.calls.append((query, list(texts)))
        return [{"id": str(index), "score": score} for index, score in self.order]


def test_rerank_ranked_reorders_and_flips_score_direction():
    ranked = [
        {"rank": 1, "source": "a", "content": "x", "score": 0.2, "gold_spans": []},
        {"rank": 2, "source": "b", "content": "y", "score": 0.3, "gold_spans": [0]},
        {"rank": 3, "source": "c", "content": "z", "score": 0.4, "gold_spans": []},
    ]
    reranker = _StubReranker([(1, 0.9), (2, 0.4)])  # API omits index 0
    out, applied = rerank_ranked(reranker, "q", ranked, keep=2)
    assert applied and reranker.calls[0][1] == ["x", "y", "z"]
    assert [item["source"] for item in out] == ["b", "c"]
    assert out[0]["rank"] == 1 and out[0]["score"] == pytest.approx(0.1) and out[0]["rerank_score"] == 0.9
    empty, applied = rerank_ranked(_StubReranker([]), "q", ranked, keep=2)
    assert not applied and [item["source"] for item in empty] == ["a", "b"]


def test_evaluate_setting_with_reranker_uses_candidates_and_reports_absent_metrics():
    class _Store(_StubStore):
        seen_k = []

        def search_with_scores(self, query, *, k):
            self.seen_k.append(k)
            return super().search_with_scores(query, k=k)

    reranker = _StubReranker([(0, 0.5), (1, 0.6), (2, 0.7)])  # unsorted on purpose: rerank_ranked sorts by score
    result = evaluate_setting(
        data_path="unused", queries=_queries(), config={}, embedding_model=None,
        chunk_size=800, chunk_overlap=0, top_ks=[3], store_factory=_Store, reranker=reranker, rerank_candidates=10,
    )
    assert _Store.seen_k and set(_Store.seen_k) == {10}
    assert result["rerank"] == {"candidates": 10, "applied_queries": 5}
    k3 = result["by_k"]["3"]
    # q-miss gold (index 2) is now first: chunk_rank 1
    per_query = {row["id"]: row for row in result["queries"]}
    assert per_query["3"]["by_k"]["3"]["chunk_rank"] == 1
    assert per_query["3"]["retrieved"][0]["rerank_score"] == 0.7
    assert k3["absent_queries"] == 1 and k3["answerable_queries"] == 4
    assert k3["absent_auroc"] is not None and k3["absent_reject_at_answerable_recall_95"] is not None
    assert per_query["1"]["by_k"]["3"]["min_distance"] == pytest.approx(0.3)


def test_evaluate_setting_indexes_several_directories_with_one_index_call(monkeypatch):
    calls = []

    class _MultiStore(_StubStore):
        def index(self, documents):
            calls.append(("index", len(documents)))
            return 7

        def index_from_directory(self, path):
            calls.append(("dir", path))
            return 42

    class _Reader:
        def __init__(self, path, recursive=True):
            self.path = path

        def load(self):
            return [object(), object()]

    import langchain.langchain_support as support

    monkeypatch.setattr(support, "LangChainFileReader", _Reader)
    single = evaluate_setting(data_path="one", queries=_queries(), config={}, embedding_model=None, chunk_size=800, chunk_overlap=0, top_ks=[3], store_factory=_MultiStore)
    multi = evaluate_setting(data_path=["one", "two"], queries=_queries(), config={}, embedding_model=None, chunk_size=800, chunk_overlap=0, top_ks=[3], store_factory=_MultiStore)
    assert single["chunk_count"] == 42 and multi["chunk_count"] == 7
    assert calls == [("dir", "one"), ("index", 4)]


def test_load_query_files_concatenates_and_caps(tmp_path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text("qid,query,gold_doc_id,gold_span,is_absent,language\na1,q,doc.md,s,0,en\na2,q2,doc.md,s,0,en\n", encoding="utf-8")
    b.write_text("qid,query,gold_doc_id,gold_span,is_absent,language\nb1,q,,,1,zh\n", encoding="utf-8")
    assert [row["id"] for row in load_query_files([str(a), str(b)])] == ["a1", "a2", "b1"]
    assert [row["id"] for row in load_query_files([str(a), str(b)], limit=2)] == ["a1", "a2"]


def test_run_model_and_summary_expose_matrix_and_fixed_slice():
    spec = {"spec": "huggingface:stub", "model": "stub", "provider": "huggingface", "config": {}, "model_name": "stub"}
    entry = run_model(
        model_spec=spec, data_paths=["unused"], queries=_queries(), chunk_sizes=[800, 1000], chunk_overlaps=[0, 200],
        top_ks=[3], reranker=None, rerank_candidates=10, store_factory=_StubStore,
    )
    assert [(r["chunk_size"], r["chunk_overlap"]) for r in entry["results"]].count((1000, 200)) == 1
    assert entry["default_setting_rank"] in {1, 2, 3, 4}
    assert entry["embedding_calls"] == {"chunks_embedded": 42 * 4, "queries_embedded": 5 * 4}
    summary = build_model_summary([entry], 3)
    row = summary["matrix"][0]
    assert row["model"] == "huggingface:stub" and row["settings_evaluated"] == 4
    assert row["at_800_0"]["chunk_hit_at_k"] == 1.0 and row["at_1000_200"]["chunk_hit_at_k"] == 1.0
    assert set(summary["fixed_slice"]) == {"800/0", "1000/200"}
    assert summary["fixed_slice"]["800/0"]["huggingface:stub"]["absent_auroc"] is not None
