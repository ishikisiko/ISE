"""D2 skill preflight precision / recall from ``skills/*/evals/cases.jsonl`` (Q2-05).

Each case carries the human ``expect`` (``<skill>`` or ``not_<skill>``) and the
handler's observed ``preflight`` reason. ``known_gap`` cases are counted as
misses here (they are pinned, not hidden, by the pytest suite)::

    python -m tests.quality.preflight_eval --output-file runtime/quality/<run>/preflight_eval.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from skills import SkillRegistry  # noqa: E402
from tests.quality.common import ROOT, utc_now, write_json  # noqa: E402

SKILLS = ("finance", "weather", "location", "transportation", "sports")


def load_cases(skill: str) -> List[Dict[str, Any]]:
    path = ROOT / "skills" / skill / "evals" / "cases.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate_skill(skill: str, registry: SkillRegistry) -> Dict[str, Any]:
    handler = registry.get(skill)
    cases = load_cases(skill)
    tp = fp = fn = tn = 0
    reject_reasons: Dict[str, int] = {}
    accepted_positives = 0
    gaps: List[Dict[str, Any]] = []
    for case in cases:
        expect_positive = case["expect"] == skill
        handles = handler.handles_query(case["query"])
        preflight = handler.preflight({"query": case["query"]})
        if handles and expect_positive:
            tp += 1
        elif handles and not expect_positive:
            fp += 1
        elif not handles and expect_positive:
            fn += 1
        else:
            tn += 1
        if not preflight.accepted:
            reject_reasons[preflight.reason] = reject_reasons.get(preflight.reason, 0) + 1
        elif expect_positive:
            accepted_positives += 1
        if handles is not expect_positive:
            gaps.append({"query": case["query"], "expect": case["expect"], "observed_handles_query": handles, "preflight": preflight.reason, "why": case.get("why")})
    positives = tp + fn
    return {
        "cases": len(cases),
        "positives": positives,
        "negatives": fp + tn,
        "hard_negatives": sum(1 for case in cases if case["expect"] != skill and "hard negative" in str(case.get("why") or "").lower() or "困难负例" in str(case.get("why") or "")),
        "preflight_precision": (tp / (tp + fp)) if (tp + fp) else None,
        "preflight_recall": (tp / positives) if positives else None,
        "accept_rate_on_positives": (accepted_positives / positives) if positives else None,
        "reject_reason_distribution": dict(sorted(reject_reasons.items(), key=lambda item: -item[1])),
        "known_gaps": len(gaps),
        "gaps": gaps,
    }


def run() -> Dict[str, Any]:
    registry = SkillRegistry.from_config({"GOOGLE_API_KEY": "eval-key"})
    skills = {skill: evaluate_skill(skill, registry) for skill in SKILLS}
    return {"created_at": utc_now(), "skills": skills}


def render_table(report: Dict[str, Any]) -> str:
    lines = ["skill\tcases\tpos\tneg\tprecision\trecall\tgaps\ttop_reject_reasons"]
    for skill, entry in report["skills"].items():
        reasons = ", ".join(f"{name}={count}" for name, count in list(entry["reject_reason_distribution"].items())[:3])
        precision = "-" if entry["preflight_precision"] is None else f"{entry['preflight_precision']:.3f}"
        recall = "-" if entry["preflight_recall"] is None else f"{entry['preflight_recall']:.3f}"
        lines.append(f"{skill}\t{entry['cases']}\t{entry['positives']}\t{entry['negatives']}\t{precision}\t{recall}\t{entry['known_gaps']}\t{reasons}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-file", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run()
    if args.output_file:
        write_json(args.output_file, report)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_table(report))


if __name__ == "__main__":
    main()
