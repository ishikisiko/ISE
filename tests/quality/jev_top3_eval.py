"""Jev (TypeSafe AI System One) experiments for three ISE judgement points.

  routing   skill routing Choice vs skills/*/evals/cases.jsonl (161) + dataset/route_intent_dataset.csv (57)
  analysis  claim_classes / critical_ambiguity / existence / time_scope / comparison-member de-noising
            vs dataset/query_analysis_gold.csv (65); baseline = deterministic analyze_query
  smalltalk small-talk Noul vs utils.search_routing.is_small_talk_query on dataset/small_talk_cases.csv (40)

Needs ``TYPESAFE_API_KEY`` (real network calls). Report of the 2026-09-18 runs:
``docs/reports/jev_evaluation_20260918/report.md``. Routing and smalltalk question
wording was written once and not tuned; the analysis wording is the second revision.

    TYPESAFE_API_KEY=... python -m tests.quality.jev_top3_eval [routing|analysis|smalltalk|all] [--output-dir DIR]
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "runtime" / "quality" / "jev" / "top3"

from typesafe_sdk import Choice, Noul, RetryPolicy, TypeSafeClient  # noqa: E402

CLIENT = TypeSafeClient(retry=RetryPolicy(max_retries=2, timeout=20.0))
CONCURRENCY = 4


def run_all(rows: List[Dict[str, Any]], fn) -> Tuple[List[Dict[str, Any]], List[float]]:
    lat: List[float] = []

    def wrapped(row):
        t0 = time.perf_counter()
        try:
            out = fn(row)
        except Exception as exc:  # noqa: BLE001
            out = {**row, "error": f"{type(exc).__name__}: {exc}"}
        lat.append((time.perf_counter() - t0) * 1000)
        return out

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        return list(pool.map(wrapped, rows)), lat


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return round(p, 3), round(r, 3), round(f, 3)


def lat_line(lat: List[float]) -> str:
    lat = sorted(lat)
    return f"latency ms p50 {statistics.median(lat):.0f} p95 {lat[int(len(lat) * 0.95) - 1]:.0f} max {lat[-1]:.0f} (concurrency {CONCURRENCY})"


# ---------------------------------------------------------------- routing
SKILL_CRITERIA = {
    "weather": (
        "Current weather, forecast, or air quality for an explicitly named place. Not physics of temperature, "
        "sensors or devices, creative writing, product pricing, or company names that contain weather words."
    ),
    "finance": (
        "Current quote or historical market performance of an explicit stock, index, fund, cryptocurrency, or "
        "currency exchange rate. Not product prices, subscriptions, promotional credits, macro statistics, retail "
        "banking products, exams, inventory, math, or company news."
    ),
    "sports": (
        "Recent or upcoming games, scores, fixtures, or schedules for an explicit team or a named sporting event. "
        "Not rules, history, nutrition, ticket prices, league finance, or non-sport uses of game, match, score."
    ),
    "location": (
        "Named place types or businesses near an explicit reference location such as a landmark, campus, or address. "
        "Not when the only reference is the user's own current position ('near me'), and not geography facts, "
        "biography, museums, algorithms, or idioms."
    ),
    "transportation": (
        "Directions or a route between an explicit origin and an explicit destination (driving, transit, walking, "
        "cycling). Not train or flight timetables, ticket or refund policies, licences, laws, or metaphorical uses "
        "of route, train, drive, from/to."
    ),
    "none": "None of the above: general knowledge, news, product pricing, how-to, code, chat, calculation, or anything else.",
}
ROUTING_QUESTIONS = {
    "skill": Choice(
        instructions="Which specialised tool, if any, should handle user_query? Choose none unless the query clearly fits a tool.",
        criteria=SKILL_CRITERIA,
    )
}
ROUTE_MAP = {"weather_api": "weather", "finance_api": "finance", "sports_api": "sports",
             "transportation_api": "transportation", "location_api": "location"}


def routing() -> str:
    from skills.registry import SkillRegistry
    from tests.quality.preflight_eval import load_cases  # noqa: F401  (format reference)

    per_skill_cases: Dict[str, List[Dict[str, Any]]] = {}
    queries: Dict[str, Dict[str, Any]] = {}
    for skill in SKILL_CRITERIA:
        if skill == "none":
            continue
        cases = [json.loads(l) for l in (ROOT / "skills" / skill / "evals" / "cases.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        per_skill_cases[skill] = cases
        for c in cases:
            queries.setdefault(c["query"], {"query": c["query"]})
    route_rows = list(csv.DictReader((ROOT / "dataset" / "route_intent_dataset.csv").open(encoding="utf-8")))
    for r in route_rows:
        queries.setdefault(r["query"], {"query": r["query"]})

    def judge(row):
        resp = CLIENT.system_one({"user_query": row["query"]}, ROUTING_QUESTIONS)
        a = resp.choices["skill"]
        return {**row, "pred": a.choice, "conf": round(a.confidence, 3),
                "probs": {k: round(v, 3) for k, v in a.probabilities.items()}}

    results, lat = run_all(list(queries.values()), judge)
    by_q = {r["query"]: r for r in results}
    (OUT / "routing_rows.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n", encoding="utf-8")
    errors = [r for r in results if "error" in r]

    # current baseline via handlers (same as preflight_eval)
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        cfg = {}
    registry = SkillRegistry.from_config(cfg)
    lines = [f"# routing  ({len(results)} unique queries, errors {len(errors)})", lat_line(lat), ""]
    lines.append("## per-skill cases.jsonl  (baseline = handles_query word lists)")
    lines.append("| skill | n | base P | base R | jev P | jev R | jev F1 | jev P@conf>=0.5 | jev R@conf>=0.5 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    all_gaps = []
    for skill, cases in per_skill_cases.items():
        handler = registry.get(skill)
        b = Counter(); j = Counter(); g = Counter()
        for c in cases:
            pos = c["expect"] == skill
            r = by_q[c["query"]]
            if "error" in r:
                continue
            base = bool(handler.handles_query(c["query"])) if handler else False
            jev = r["pred"] == skill
            gated = jev and r["conf"] >= 0.5
            for cnt, pred in ((b, base), (j, jev), (g, gated)):
                cnt["tp" if pred and pos else "fp" if pred else "fn" if pos else "tn"] += 1
            if jev != pos:
                all_gaps.append((skill, c["expect"], r["pred"], r["conf"], c["query"], c.get("why", "")))
        bp, br, _ = prf(b["tp"], b["fp"], b["fn"])
        jp, jr, jf = prf(j["tp"], j["fp"], j["fn"])
        gp, gr, _ = prf(g["tp"], g["fp"], g["fn"])
        lines.append(f"| {skill} | {len(cases)} | {bp} | {br} | {jp} | {jr} | {jf} | {gp} | {gr} |")
    lines.append("\njev disagreements with cases.jsonl:")
    for skill, expect, pred, conf, q, why in all_gaps:
        lines.append(f"- [{skill} file] expect={expect} pred={pred} conf={conf}: {q}  ({why})")

    # route_intent_dataset: 6-way accuracy
    ok = 0; conf_ok = []; rows_out = []
    for r in route_rows:
        gold = ROUTE_MAP.get(r["expected_route"], "none")
        j = by_q[r["query"]]
        if "error" in j:
            continue
        hit = j["pred"] == gold
        ok += hit
        if not hit:
            rows_out.append(f"- {r['qid']} gold={gold} pred={j['pred']} conf={j['conf']}: {r['query']}")
    lines.append(f"\n## route_intent_dataset  6-way accuracy {ok}/{len(route_rows)} = {ok / len(route_rows):.3f}")
    lines.extend(rows_out)
    return "\n".join(lines)


# ---------------------------------------------------------------- analysis
CLAIM_QUESTIONS = {
    "comparison": Noul(instructions="Does user_query ask to compare, contrast, or choose between two or more named things?"),
    "numeric": Noul(instructions="Does user_query ask for a specific quantity such as a price, count, size, rate, percentage, or numeric limit?"),
    "pricing": Noul(instructions="Does user_query ask about the price, cost, fees, free quota, credits, or plan limits of a product or service?"),
    "current": Noul(instructions="Does user_query ask for the current, latest, present-day, or today's state of something (words like latest, current, now, today, 最新, 当前, 现在, 今天)?"),
    "compliance": Noul(instructions="Does user_query concern regulation, compliance, legal terms, policies, or privacy law?"),
    "temporal": Noul(instructions="Does user_query state an explicit time window or date range, such as a year, 'past week', 'last three years', 'yesterday', or 'from 2020 to 2024'?"),
    "historical": Noul(instructions="Does user_query ask for a history, trend, or year-by-year record spanning multiple years (for example 历年, 近五年, from 2017 through 2021)?"),
    "critical_ambiguity": Noul(instructions=(
        "Does user_query refer to a target it does not name, so the target cannot be identified from the query alone? "
        "For example pronouns such as it, this, that, the former, these two, 它, 这个, 前者, or a comparison whose items are not named explicitly."
    )),
    "existence": Noul(instructions="Does user_query ask which things currently exist or are available, that is for an inventory of items (有哪些, which ones), and not for what changed or was updated, not for a historical or year-by-year list, and not for a single value or an explanation?"),
    "local_context": Noul(instructions="Does user_query refer to the user's own uploaded files, documents, or this project?"),
    "time_scope": Choice(
        instructions="What time scope does user_query state?",
        criteria={
            "none": "No time scope stated.",
            "recent": "Asks about the latest, current, recent, today, yesterday, or the past few days or weeks.",
            "window": "States a bounded range within roughly one year, such as a specific month, quarter, or year.",
            "historical": "Asks for coverage across multiple years, such as 历年, 近五年, 最近三年, or 2017 through 2021.",
        },
    ),
}


def analysis() -> str:
    from tests.quality.analysis_eval import match_members, parse_aliases, predicted_time_scope, split_list
    from utils.query_orchestration import analyze_query, prepare_analysis

    rows = list(csv.DictReader((ROOT / "dataset" / "query_analysis_gold.csv").open(encoding="utf-8")))

    def judge(row):
        q = row["query"]
        det = prepare_analysis(analyze_query(q, allow_search=True), query=q, reconcile_enabled=True, llm_invoke=None).to_dict()
        candidates = list(dict.fromkeys((det.get("comparison_members") or []) + (det.get("entities") or [])))[:12]
        questions = dict(CLAIM_QUESTIONS)
        for i, cand in enumerate(candidates):
            questions[f"member_{i}"] = Noul(instructions=f"Is candidate_{i} one of the things that user_query asks to compare?")
            questions[f"clean_{i}"] = Noul(instructions=(
                f"Is candidate_{i} just the name of a product, company, technology, model, or thing, with no extra words "
                "such as qualifiers, verbs, particles, or trailing phrases attached?"
            ))
        state = {"user_query": q, **{f"candidate_{i}": c for i, c in enumerate(candidates)}}
        resp = CLIENT.system_one(state, questions)
        out = {"qid": row["qid"], "query": q, "det": det, "candidates": candidates,
               "time_scope_pred": resp.choices["time_scope"].choice,
               "time_scope_conf": round(resp.choices["time_scope"].confidence, 3)}
        for k in CLAIM_QUESTIONS:
            if k != "time_scope":
                out[k] = round(resp.nouls[k].noul, 3)
        out["member_p"] = {c: round(resp.nouls[f"member_{i}"].noul, 3) for i, c in enumerate(candidates)}
        out["clean_p"] = {c: round(resp.nouls[f"clean_{i}"].noul, 3) for i, c in enumerate(candidates)}
        return out

    results, lat = run_all(rows, judge)
    (OUT / "analysis_rows.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n", encoding="utf-8")
    gold_by = {r["qid"]: r for r in rows}
    ok = [r for r in results if "error" not in r]
    lines = [f"# analysis  ({len(ok)}/{len(rows)} ok)", lat_line(lat), ""]

    # claim classes micro P/R/F1 at t=0.5, baseline from det
    claims = ("comparison", "numeric", "pricing", "current", "compliance", "temporal", "historical")
    for label, pred_fn in (("baseline", lambda r, c: c in set(r["det"].get("claim_classes") or [])),
                           ("jev t=0.5", lambda r, c: r[c] >= 0.5),
                           ("jev t=0.7", lambda r, c: r[c] >= 0.7)):
        tp = fp = fn = 0
        per = defaultdict(Counter)
        for r in ok:
            gold = set(split_list(gold_by[r["qid"]]["gold_claim_classes"]))
            for c in claims:
                p, g = pred_fn(r, c), c in gold
                key = "tp" if p and g else "fp" if p else "fn" if g else "tn"
                per[c][key] += 1
                if key != "tn":
                    tp += key == "tp"; fp += key == "fp"; fn += key == "fn"
        P, R, F = prf(tp, fp, fn)
        lines.append(f"## claim_classes micro {label}: P {P} R {R} F1 {F}   per-class F1: " + ", ".join(
            f"{c} {prf(per[c]['tp'], per[c]['fp'], per[c]['fn'])[2]}" for c in claims))
    lines.append("jev t=0.5 claim errors:")
    for r in ok:
        gold = set(split_list(gold_by[r["qid"]]["gold_claim_classes"]))
        pred = {c for c in claims if r[c] >= 0.5}
        if pred != gold:
            lines.append(f"- {r['qid']} gold={sorted(gold)} jev={sorted(pred)} base={sorted(r['det'].get('claim_classes') or [])}: {r['query'][:60]}")

    # binary fields
    for field, gold_col, det_key in (("critical_ambiguity", "gold_critical_ambiguity", "critical_ambiguity"),
                                     ("existence", "gold_existence_query", "existence_query")):
        for label, pred_fn in (("baseline", lambda r: bool(r["det"].get(det_key))), ("jev t=0.5", lambda r: r[field] >= 0.5),
                               ("jev t=0.5 & not local_context", lambda r: r[field] >= 0.5 and r["local_context"] < 0.5)):
            c = Counter()
            errs = []
            for r in ok:
                g = gold_by[r["qid"]][gold_col].strip() == "1"
                p = pred_fn(r)
                c["tp" if p and g else "fp" if p else "fn" if g else "tn"] += 1
                if p != g and label.startswith("jev t=0.5 &") or (p != g and label == "jev t=0.5" and field == "existence"):
                    errs.append(f"  - {r['qid']} gold={int(g)} p={r[field]}: {r['query'][:60]}")
            P, R, F = prf(c["tp"], c["fp"], c["fn"])
            lines.append(f"## {field} {label}: P {P} R {R} F1 {F}  (tp {c['tp']} fp {c['fp']} fn {c['fn']})")
            lines.extend(errs)

    # time scope
    base_ok = jev_ok = 0
    errs = []
    for r in ok:
        g = (gold_by[r["qid"]]["gold_time_scope"] or "none").strip() or "none"
        base_ok += predicted_time_scope(r["det"]) == g
        jev_ok += r["time_scope_pred"] == g
        if r["time_scope_pred"] != g:
            errs.append(f"  - {r['qid']} gold={g} jev={r['time_scope_pred']} conf={r['time_scope_conf']} base={predicted_time_scope(r['det'])}: {r['query'][:60]}")
    lines.append(f"## time_scope accuracy baseline {base_ok / len(ok):.3f}  jev {jev_ok / len(ok):.3f}")
    lines.extend(errs)

    # members: baseline det vs jev-filtered candidates (comparison category, like analysis_eval)
    def jev_members(r, t):
        import re as _re
        keep = [c for c in r["member_p"] if r["member_p"][c] >= t and r["clean_p"][c] >= 0.5]
        key = lambda x: _re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", x.casefold())
        # drop a candidate whose compact key is contained in another kept candidate's key (keep the longer name)
        out = [c for c in keep if not any(o != c and key(c) and key(c) in key(o) for o in keep)]
        return out

    for label, member_fn in (("baseline", lambda r: r["det"].get("comparison_members") or []),
                             ("jev-filtered t=0.5", lambda r: [c for c, p in r["member_p"].items() if p >= 0.5]),
                             ("jev-filtered+clean+dedupe t=0.5", lambda r: jev_members(r, 0.5)),
                             ("jev-filtered+clean+dedupe t=0.7", lambda r: jev_members(r, 0.7))):
        tp = pred_n = gold_n = noise = 0
        errs = []
        for r in ok:
            grow = gold_by[r["qid"]]
            if grow["category"] != "comparison":
                continue
            gold_members = split_list(grow["gold_members"])
            aliases = parse_aliases(grow["gold_member_aliases"])
            m_tp, m_pred, m_gold, m_noise = match_members(member_fn(r), gold_members, aliases)
            tp += m_tp; pred_n += m_pred; gold_n += m_gold; noise += len(m_noise)
            if m_noise or m_tp < m_gold:
                errs.append(f"  - {r['qid']} gold={gold_members} pred={member_fn(r)} noise={m_noise}")
        P, R, F = prf(tp, pred_n - tp, gold_n - tp)
        lines.append(f"## comparison members {label}: P {P} R {R} F1 {F} noise_member_rate {noise / pred_n if pred_n else 0:.3f} ({noise}/{pred_n})")
        if label != "baseline":
            lines.extend(errs)
    lines.append("\ncandidate probabilities compared/clean (comparison rows):")
    for r in ok:
        if gold_by[r["qid"]]["category"] == "comparison":
            lines.append(f"- {r['qid']} " + ", ".join(f"{c}={r['member_p'][c]}/{r['clean_p'][c]}" for c in r["member_p"]))
    return "\n".join(lines)


# ---------------------------------------------------------------- small talk
def load_small_talk_cases() -> List[Tuple[str, int]]:
    with (ROOT / "dataset" / "small_talk_cases.csv").open(encoding="utf-8") as fh:
        return [(r["query"], int(r["gold_small_talk"])) for r in csv.DictReader(fh)]


def smalltalk() -> str:
    from utils.search_routing import is_small_talk_query

    q = Noul(instructions=(
        "Is user_query only a greeting, thanks, farewell, acknowledgement, or small talk about the assistant, "
        "with no information request, task, or question about anything else?"
    ))

    def judge(row):
        resp = CLIENT.system_one({"user_query": row["query"]}, {"st": q})
        return {**row, "p": round(resp.nouls["st"].noul, 3)}

    rows = [{"query": x, "gold": g} for x, g in load_small_talk_cases()]
    results, lat = run_all(rows, judge)
    (OUT / "smalltalk_rows.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n", encoding="utf-8")
    ok = [r for r in results if "error" not in r]
    lines = [f"# smalltalk  ({len(ok)} agent-authored cases from dataset/small_talk_cases.csv)", lat_line(lat), ""]
    lines.append("| method | P | R | F1 | acc | FP | FN |")
    lines.append("|---|---|---|---|---|---|---|")
    for label, fn in (("baseline is_small_talk_query", lambda r: is_small_talk_query(r["query"])),
                      ("jev t=0.5", lambda r: r["p"] >= 0.5), ("jev t=0.7", lambda r: r["p"] >= 0.7)):
        c = Counter()
        for r in ok:
            p, g = bool(fn(r)), bool(r["gold"])
            c["tp" if p and g else "fp" if p else "fn" if g else "tn"] += 1
        P, R, F = prf(c["tp"], c["fp"], c["fn"])
        lines.append(f"| {label} | {P} | {R} | {F} | {(c['tp'] + c['tn']) / len(ok):.3f} | {c['fp']} | {c['fn']} |")
    lines.append("\nper-case (gold, baseline, jev p):")
    for r in ok:
        b = int(is_small_talk_query(r["query"]))
        flag = "" if (r["p"] >= 0.5) == bool(r["gold"]) else "  <-- jev wrong"
        bflag = "" if b == r["gold"] else "  (baseline wrong)"
        lines.append(f"- {r['gold']} base={b} jev={r['p']}: {r['query']}{flag}{bflag}")
    return "\n".join(lines)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    which = args[0] if args else "all"
    if "--output-dir" in sys.argv:
        OUT = Path(sys.argv[sys.argv.index("--output-dir") + 1])
    OUT.mkdir(parents=True, exist_ok=True)
    parts = []
    for name, fn in (("routing", routing), ("analysis", analysis), ("smalltalk", smalltalk)):
        if which in (name, "all"):
            report = fn()
            (OUT / f"{name}_report.md").write_text(report, encoding="utf-8")
            parts.append(report)
    print("\n\n".join(parts))
