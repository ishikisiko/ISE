"""Verify that every gold span in ``dataset/local_chunk_gold.csv`` is locatable.

Loads the corpus exactly as local RAG does (``LangChainFileReader``), counts
chunks at the production split (1000 / 200) and checks each non-absent gold
span against the named document after whitespace normalisation. Multi-span
answers use ``||`` as the separator::

    python -m tests.quality.local_gold_check
    python -m tests.quality.local_gold_check --corpus tests/fixtures/local_corpus --gold dataset/local_chunk_gold.csv --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.quality.common import read_csv_rows  # noqa: E402

DEFAULT_CORPUS = "tests/fixtures/local_corpus"
DEFAULT_GOLD = "dataset/local_chunk_gold.csv"
SPAN_SEPARATOR = "||"
_WS = re.compile(r"\s+")


def normalize_text(text: Any) -> str:
    """Collapse whitespace and casefold so PDF line breaks do not break matching."""
    return _WS.sub(" ", str(text or "")).strip().casefold()


def split_spans(raw: Any) -> List[str]:
    return [part.strip() for part in str(raw or "").split(SPAN_SEPARATOR) if part.strip()]


def token_overlap(span: str, text: str) -> float:
    """Share of the span's tokens (CJK chars or word tokens) present in ``text``."""
    tokens = re.findall(r"[一-鿿]|[a-z0-9]+(?:[.,][a-z0-9]+)*", normalize_text(span))
    if not tokens:
        return 0.0
    haystack = normalize_text(text)
    return sum(1 for token in tokens if token in haystack) / len(tokens)


def span_contained(span: str, text: str, *, min_overlap: float = 0.8) -> Tuple[bool, str]:
    """Substring after normalisation, else token overlap >= ``min_overlap``."""
    if normalize_text(span) in normalize_text(text):
        return True, "substring"
    overlap = token_overlap(span, text)
    if overlap >= min_overlap:
        return True, f"token_overlap={overlap:.2f}"
    return False, f"token_overlap={overlap:.2f}"


def load_corpus_texts(corpus_dir: str) -> Dict[str, str]:
    """``basename -> full text`` for every loadable document (pages joined)."""
    from langchain.langchain_support import LangChainFileReader

    texts: Dict[str, List[str]] = {}
    for document in LangChainFileReader(corpus_dir).load():
        name = os.path.basename(str(document.source or ""))
        texts.setdefault(name, []).append(document.content or "")
    return {name: "\n".join(parts) for name, parts in texts.items()}


def count_chunks(texts: Dict[str, str], *, chunk_size: int = 1000, chunk_overlap: int = 200) -> Dict[str, int]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return {name: len(splitter.split_text(text)) for name, text in texts.items()}


def check_gold(corpus_dir: str = DEFAULT_CORPUS, gold_path: str = DEFAULT_GOLD) -> Dict[str, Any]:
    texts = load_corpus_texts(corpus_dir)
    chunks = count_chunks(texts)
    rows = read_csv_rows(gold_path)
    results: List[Dict[str, Any]] = []
    for row in rows:
        qid = row.get("qid")
        is_absent = str(row.get("is_absent") or "").strip() in {"1", "true", "True", "yes"}
        entry: Dict[str, Any] = {"qid": qid, "is_absent": is_absent, "gold_doc_id": row.get("gold_doc_id"), "spans": [], "ok": True}
        if is_absent:
            entry["ok"] = not str(row.get("gold_span") or "").strip()
            results.append(entry)
            continue
        doc_name = str(row.get("gold_doc_id") or "").strip()
        text = texts.get(doc_name)
        if text is None:
            entry.update(ok=False, error=f"gold_doc_id not in corpus: {doc_name}")
            results.append(entry)
            continue
        for span in split_spans(row.get("gold_span")):
            found, how = span_contained(span, text)
            entry["spans"].append({"span": span, "found": found, "how": how})
            entry["ok"] = entry["ok"] and found
        if not entry["spans"]:
            entry.update(ok=False, error="empty gold_span")
        results.append(entry)
    languages = {}
    for row in rows:
        lang = str(row.get("language") or "").strip() or "unknown"
        languages[lang] = languages.get(lang, 0) + 1
    return {
        "corpus_dir": corpus_dir,
        "gold_path": gold_path,
        "files": len(texts),
        "chunks_per_file": dict(sorted(chunks.items())),
        "total_chunks": sum(chunks.values()),
        "questions": len(rows),
        "multi_span": sum(1 for row in rows if len(split_spans(row.get("gold_span"))) > 1),
        "absent": sum(1 for row in rows if str(row.get("is_absent") or "").strip() in {"1", "true", "True", "yes"}),
        "cross_lingual": sum(1 for row in rows if "->" in str(row.get("language") or "")),
        "languages": languages,
        "all_ok": all(entry["ok"] for entry in results),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", default=DEFAULT_CORPUS)
    parser.add_argument("--gold", default=DEFAULT_GOLD)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = check_gold(args.corpus, args.gold)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"files={report['files']} chunks={report['total_chunks']} questions={report['questions']} "
              f"multi_span={report['multi_span']} absent={report['absent']} cross_lingual={report['cross_lingual']} all_ok={report['all_ok']}")
        for entry in report["results"]:
            if not entry["ok"]:
                print(f"- {entry['qid']}: FAIL {entry.get('error') or entry['spans']}")
    raise SystemExit(0 if report["all_ok"] else 1)


if __name__ == "__main__":
    main()
