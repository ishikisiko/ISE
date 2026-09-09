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
