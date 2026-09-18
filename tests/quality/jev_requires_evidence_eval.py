"""Jev (TypeSafe AI System One) experiment: can it decide whether a query needs retrieved evidence?

Runs the decomposed judgement (task type + risk nouls + wants_sources + intent shape)
once per query against ``dataset/query_analysis_gold.csv`` plus the agent-authored
hard cases in ``dataset/jev_requires_evidence_hard.csv``, compares against gold and
writes metrics + per-query rows. Needs ``TYPESAFE_API_KEY`` (real network calls,
about 0.04 USD per 1000 queries). Report of the 2026-09-18 runs:
``docs/reports/jev_evaluation_20260918/report.md``.

    TYPESAFE_API_KEY=... python -m tests.quality.jev_requires_evidence_eval [--limit N] [--concurrency 4] [--output-dir DIR]

The ``revised`` rules were designed after seeing run 1, so their numbers on this
set are optimistic; validate on held-out queries before relying on them.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

from typesafe_sdk import Choice, Noul, RetryPolicy, TypeSafeClient

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "dataset" / "query_analysis_gold.csv"
HARD = ROOT / "dataset" / "jev_requires_evidence_hard.csv"
OUT_DIR = ROOT / "runtime" / "quality" / "jev" / "requires_evidence"

TASK_TYPES = {
    "chit_chat": "Greeting, thanks, small talk, or a question about how the assistant is doing.",
    "about_assistant": "Asks who or what the assistant is, or about its own abilities.",
    "creative_or_opinion": "Asks for a joke, a motivational line, a poem, advice, or a personal opinion.",
    "computation_or_code": "Arithmetic, unit conversion, or writing/explaining code.",
    "translation": "Asks to translate text between languages.",
    "concept_explanation": "Asks what a well-known concept, technique, or term means or how it works in general.",
    "local_documents": "Asks about the user's uploaded files, documents, or 'this project'.",
    "factual_lookup": "Asks for a specific fact about a named real-world entity, product, person, or event.",
    "current_state": "Asks about the latest, current, recent, or today's state of something.",
}

QUESTIONS = {
    "task_type": Choice(
        instructions="What kind of request is user_query?",
        criteria=TASK_TYPES,
    ),
    "time_varying": Noul(
        instructions=(
            "Does a correct answer to user_query depend on facts that change over time, "
            "such as prices, versions, plans, office holders, rankings, schedules, or counts?"
        )
    ),
    "exact_figure": Noul(
        instructions=(
            "Does user_query ask for a specific number, date, list, or name that must be exactly right "
            "to be useful, rather than a general explanation?"
        )
    ),
    "recent": Noul(
        instructions="Does user_query ask about something new, recent, latest, current, or happening now?"
    ),
    "long_tail": Noul(
        instructions=(
            "Does user_query concern a niche, obscure, or little-known entity, product, parameter, or company, "
            "as opposed to encyclopedic common knowledge?"
        )
    ),
    "local_context": Noul(
        instructions="Does user_query refer to the user's own uploaded files, documents, or this project?"
    ),
    "wants_sources": Noul(
        instructions=(
            "Does user_query explicitly ask for sources, citations, official documentation, benchmarks, "
            "or first-hand evidence, or explicitly reject second-hand or promotional material?"
        )
    ),
    "intent_shape": Choice(
        instructions="Is user_query asking to compare two or more things, or asking for information about one thing?",
        criteria={
            "comparison": "Compares, contrasts, or asks which of several things is better/cheaper/faster/different.",
            "information_request": "Asks about a single subject without comparing alternatives.",
        },
    ),
}

SAFE_TYPES = {
    "chit_chat", "about_assistant", "creative_or_opinion", "computation_or_code",
    "translation", "concept_explanation", "local_documents",
}
RISK_KEYS = ("time_varying", "exact_figure", "recent", "long_tail")


def load_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with GOLD.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append({
                "qid": r["qid"], "query": r["query"], "source": "gold",
                "gold": int(r["gold_requires_evidence"]),
                "gold_intent": r["gold_intent_shape"], "subgroup": r["category"],
            })
    with HARD.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append({
                "qid": r["qid"], "query": r["query"], "source": "hard",
                "gold": int(r["gold_requires_evidence"]),
                "gold_intent": r["gold_intent_shape"], "subgroup": r["subgroup"],
            })
    return rows


def judge(client: TypeSafeClient, row: Dict[str, Any]) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        resp = client.system_one({"user_query": row["query"]}, QUESTIONS)
    except Exception as exc:  # noqa: BLE001
        return {**row, "error": f"{type(exc).__name__}: {exc}", "latency_ms": (time.perf_counter() - started) * 1000}
    latency = (time.perf_counter() - started) * 1000
    tt = resp.choices["task_type"]
    it = resp.choices["intent_shape"]
    out = {
        **row,
        "latency_ms": round(latency, 1),
        "input_tokens": resp.usage.input_tokens,
        "task_type": tt.choice,
        "task_type_conf": round(tt.confidence, 3),
        "task_type_probs": {k: round(v, 3) for k, v in tt.probabilities.items()},
        "intent_pred": it.choice,
        "intent_conf": round(it.confidence, 3),
        "p_comparison": round(it.probabilities.get("comparison", 0.0), 3),
    }
    for key in (*RISK_KEYS, "local_context", "wants_sources"):
        out[key] = round(resp.nouls[key].noul, 3)
    return out


def decide(r: Dict[str, Any], rule: str, t: float) -> int:
    if "error" in r:
        return 1
    risk = max(r[k] for k in RISK_KEYS)
    p_safe = sum(v for k, v in r["task_type_probs"].items() if k in SAFE_TYPES)
    if rule == "type_only":
        return int(r["task_type"] not in SAFE_TYPES)
    if rule == "risk_only":
        return int(risk >= t)
    if rule == "type_or_risk":
        return int(r["task_type"] not in SAFE_TYPES or risk >= t)
    if rule == "conservative":  # skip evidence only when clearly safe
        return int(not (p_safe >= 0.9 and risk < t))
    lookup = r["task_type"] in {"factual_lookup", "current_state"}
    ws = r.get("wants_sources", 0.0)
    if rule == "revised":  # post-hoc: exact_figure is not a trigger by itself
        return int(max(r["time_varying"], r["recent"]) >= t or (lookup and r["long_tail"] >= 0.5) or ws >= 0.5)
    if rule == "revised+comparison":  # plus ISE's existing deterministic gate
        return int(decide(r, "revised", t) or r["intent_pred"] == "comparison")
    raise ValueError(rule)


def prf(rows: List[Dict[str, Any]], rule: str, t: float) -> Dict[str, Any]:
    tp = fp = fn = tn = 0
    errors = []
    for r in rows:
        pred, gold = decide(r, rule, t), r["gold"]
        if pred and gold:
            tp += 1
        elif pred and not gold:
            fp += 1
            errors.append((r["qid"], "FP(over-search)", r["query"]))
        elif not pred and gold:
            fn += 1
            errors.append((r["qid"], "FN(missed-evidence)", r["query"]))
        else:
            tn += 1
    p = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * rc / (p + rc) if p + rc else 0.0
    acc = (tp + tn) / len(rows) if rows else 0.0
    return {"rule": rule, "t": t, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(p, 3), "recall": round(rc, 3), "f1": round(f1, 3),
            "accuracy": round(acc, 3), "errors": errors}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--output-dir", default=str(OUT_DIR))
    args = ap.parse_args()
    out_dir = Path(args.output_dir)
    if not os.environ.get("TYPESAFE_API_KEY"):
        print("TYPESAFE_API_KEY not set", file=sys.stderr)
        return 2
    rows = load_rows()
    if args.limit:
        rows = rows[: args.limit]
    client = TypeSafeClient(retry=RetryPolicy(max_retries=2, timeout=20.0))

    wall = time.perf_counter()
    if args.concurrency > 1:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(pool.map(lambda r: judge(client, r), rows))
    else:
        results = [judge(client, r) for r in rows]
    wall = time.perf_counter() - wall

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rows.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n", encoding="utf-8"
    )
    ok = [r for r in results if "error" not in r]
    errs = [r for r in results if "error" in r]
    lat = sorted(r["latency_ms"] for r in ok)

    lines = []
    lines.append(f"# Jev requires_evidence experiment  ({time.strftime('%Y-%m-%d %H:%M')})")
    lines.append(f"queries: {len(results)}  ok: {len(ok)}  errors: {len(errs)}  wall: {wall:.1f}s  concurrency: {args.concurrency}")
    if lat:
        lines.append(f"latency ms  p50 {statistics.median(lat):.0f}  p95 {lat[int(len(lat)*0.95)-1]:.0f}  min {lat[0]:.0f}  max {lat[-1]:.0f}")
        lines.append(f"input tokens/query mean {statistics.mean(r['input_tokens'] or 0 for r in ok):.0f}")
    for e in errs:
        lines.append(f"ERROR {e['qid']}: {e['error']}")

    lines.append("\n## requires_evidence  (all rows: gold 65 + hard 30)")
    lines.append("| rule | t | P | R | F1 | acc | FP | FN |")
    lines.append("|---|---|---|---|---|---|---|---|")
    best = None
    for rule in ("type_only", "risk_only", "type_or_risk", "conservative", "revised", "revised+comparison"):
        for t in ((0.0,) if rule == "type_only" else (0.3, 0.5, 0.7)):
            m = prf(ok, rule, t)
            lines.append(f"| {rule} | {t} | {m['precision']} | {m['recall']} | {m['f1']} | {m['accuracy']} | {m['fp']} | {m['fn']} |")
            if best is None or (m["fn"], -m["f1"]) < (best["fn"], -best["f1"]):
                best = m
    lines.append(f"\nfewest-missed rule: {best['rule']} t={best['t']}  errors:")
    for qid, kind, q in best["errors"]:
        lines.append(f"- {qid} {kind}: {q}")

    debatable = {"qa027", "h18", "h22", "h29", "h30"}
    for sub_name, pred in (
        ("gold only", lambda r: r["source"] == "gold"),
        ("hard only", lambda r: r["source"] == "hard"),
        ("all minus debatable historical/obscure-stable", lambda r: r["qid"] not in debatable),
    ):
        sub = [r for r in ok if pred(r)]
        m = prf(sub, "revised", 0.3)
        lines.append(f"\n## {sub_name}  revised t=0.3: P {m['precision']} R {m['recall']} F1 {m['f1']} acc {m['accuracy']}")
        for qid, kind, q in m["errors"]:
            lines.append(f"- {qid} {kind}: {q}")

    lines.append("\n## intent_shape (bonus)")
    hit = sum(r["intent_pred"] == r["gold_intent"] for r in ok)
    lines.append(f"accuracy {hit}/{len(ok)} = {hit/len(ok):.3f}")
    for r in ok:
        if r["intent_pred"] != r["gold_intent"]:
            lines.append(f"- {r['qid']} gold={r['gold_intent']} pred={r['intent_pred']} p_cmp={r['p_comparison']}: {r['query']}")

    lines.append("\n## per-query")
    lines.append("| qid | gold | type | conf | tv | ex | rc | lt | loc | ws | ms | query |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in ok:
        lines.append(
            f"| {r['qid']} | {r['gold']} | {r['task_type']} | {r['task_type_conf']} | {r['time_varying']} | "
            f"{r['exact_figure']} | {r['recent']} | {r['long_tail']} | {r['local_context']} | {r['wants_sources']} | {r['latency_ms']:.0f} | {r['query'][:60]} |"
        )
    report = "\n".join(lines)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
