"""Q1-06 / Q1-07: the local corpus loads and every gold span is locatable."""
from __future__ import annotations

import pytest

from tests.quality.local_gold_check import check_gold, load_corpus_texts, span_contained, split_spans, token_overlap

pytestmark = pytest.mark.quality_offline


def test_span_containment_normalizes_whitespace_and_falls_back_to_token_overlap():
    text = "The laminate has a tensile strength of 1,850\nmegapascals and a density of 1.42\ngrams."
    assert span_contained("tensile strength of 1,850 megapascals", text) == (True, "substring")
    found, how = span_contained("tensile strength about 1,850 megapascals", text)
    assert found and how.startswith("token_overlap=")
    assert span_contained("completely unrelated wording here", text)[0] is False
    assert token_overlap("参数设定为 chunk_size", "参数 设定 为 chunk_size=1000") == 1.0
    assert split_spans("a || b ||") == ["a", "b"]


def test_corpus_loads_and_gold_spans_are_locatable():
    report = check_gold()
    assert report["files"] >= 10
    # 209 chunks when frozen (plain-text .md fallback); 190 since the ``markdown``
    # dependency landed (5af2d42) and Unstructured strips Markdown syntax.
    assert report["total_chunks"] >= 180
    assert report["questions"] >= 12
    assert report["multi_span"] >= 2
    assert report["absent"] >= 2
    assert report["cross_lingual"] >= 2
    failures = [entry for entry in report["results"] if not entry["ok"]]
    assert not failures, failures


def test_ext_corpus_loads_and_gold_spans_are_locatable():
    """2026-09-19 extension corpus: distractor-heavy docs plus 12 absent questions."""
    report = check_gold("tests/fixtures/local_corpus_ext", "dataset/local_chunk_gold_ext.csv")
    assert report["files"] >= 35
    assert report["total_chunks"] >= 350
    assert report["questions"] >= 40
    assert report["absent"] >= 10
    assert report["cross_lingual"] >= 8
    assert report["multi_span"] >= 2
    failures = [entry for entry in report["results"] if not entry["ok"]]
    assert not failures, failures


def test_gold_files_do_not_share_document_names():
    """Merged indexing relies on gold_doc_id basenames being unique across both corpora."""
    corpus1 = set(load_corpus_texts("tests/fixtures/local_corpus"))
    corpus_ext = set(load_corpus_texts("tests/fixtures/local_corpus_ext"))
    assert not (corpus1 & corpus_ext)
