"""Recompute study comparisons only from immutable runs and bound reviews."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from collections import Counter

from tests.autonomy_study import ROOT, digest, write_new
from tests.baseline_runner import summarise


def load_round(study: Path, name: str) -> tuple[list[dict], list[str]]:
    root = study / name
    manifest = json.loads((root / "manifest.json").read_text())
    review_manifest_path = study / "review-manifest-v3-evidence.json"
    review_manifest_digest = (
        digest(json.dumps(json.loads(review_manifest_path.read_text()), sort_keys=True).encode())
        if review_manifest_path.exists() else None
    )
    reviews = {}
    for path in (root / "reviews-v3-evidence").glob("*.json"):
        review = json.loads(path.read_text())
        if "result_digest" not in review:
            continue
        if review_manifest_digest is None or review.get("review_manifest_digest") != review_manifest_digest:
            raise ValueError(f"Review protocol binding mismatch: {path}")
        if review["result_digest"] in reviews:
            raise ValueError("Duplicate review for one result")
        reviews[review["result_digest"]] = review
    records = []
    missing = []
    for run in manifest["runs"]:
        path = root / "runs" / run["id"] / "result.json"
        if not path.exists():
            missing.append(run["id"])
            continue
        blob = path.read_bytes()
        record = json.loads(blob)
        if (record["qid"] != run["row"]["qid"] or record["requested_mode"] != run["mode"]
                or record["dataset"] != run["row"]["dataset"] or record["query"] != run["row"]["query"]):
            raise ValueError(f"Run identity mismatch: {path}")
        actual_mode = ((record.get("result") or {}).get("control") or {}).get("autonomy", {}).get("mode")
        if actual_mode is not None and actual_mode != record["requested_mode"]:
            raise ValueError(f"Effective autonomy mismatch: {path}")
        from tests.autonomy_review import review_input
        result = record.get("result") or {}
        cited = {f"E{value}" for value in re.findall(r"\[E(\d{1,4})\]", str(result.get("answer") or ""))}
        ledger_ids = {f"E{(entry.get('metadata') or {}).get('eid')}"
                      for entry in result.get("evidence_records") or [] if isinstance(entry, dict)}
        supplied_ids = {entry.get("citation_id") for entry in review_input(record, run["row"])["retrieved_evidence"]}
        record["metrics"]["review_known_citations_omitted"] = sorted((cited & ledger_ids) - supplied_ids)
        record["metrics"]["unresolved_citation_ids"] = sorted(cited - ledger_ids)
        review = reviews.get(digest(blob))
        if (record["outcome"] == "returned" and record["metrics"].get("transport_requests") == 0
                and record["metrics"].get("llm_call_count") == 0 and not record["metrics"].get("llm_error")):
            # A deterministic pre-loop clarification made no model request;
            # this is a known zero within the observed scope, not lost usage.
            for key in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens"):
                record["metrics"]["transport_" + key] = 0
            record["metrics"]["transport_usage_complete"] = True
        if record["outcome"] in ("harness_timeout", "process_error"):
            # A killed child cannot serialize its final metrics, but the
            # observer persisted every completed HTTP response separately.
            # Recover this lower bound without altering the immutable result.
            from tests.study_transport import TransportObserver
            transport_path = path.parent / "transport.jsonl"
            observer = TransportObserver(transport_path)
            if transport_path.exists():
                observer.records = [json.loads(line) for line in transport_path.read_text().splitlines() if line.strip()]
                record["metrics"].update(observer.summary())
                record["metrics"]["transport_usage_complete"] = False
                record["metrics"]["transport_incomplete_reason"] = "Process ended with a potentially outstanding request; completed-response lower bound only."
        if record["metrics"].get("transport_http_errors", 0):
            # A failed request may have consumed provider work before its
            # response was lost. Successful-response coverage is not complete
            # accounting for the whole run; keep the raw result unchanged.
            record["metrics"]["transport_usage_complete"] = False
            record["metrics"].setdefault("transport_incomplete_reason",
                                         "Some requests failed without reporting complete usage; lower bound only.")
        records.append({**record, "review": review, "result_digest": digest(blob)})
    return records, missing


def aggregate(records: list[dict]) -> dict:
    groups = {}
    for dataset in ("final_answer", "open_task"):
        for mode in ("guided", "autonomous"):
            rows = [r for r in records if r["dataset"] == dataset and r["requested_mode"] == mode]
            judgments = [r["review"]["judgment"] for r in rows
                         if r.get("review") and r["review"].get("judgment")]
            metrics = [r["metrics"] for r in rows]
            review_usage = [attempt["usage"] for r in rows if r.get("review")
                            for attempt in r["review"].get("attempts", []) if attempt.get("usage")]
            dimensions = sorted({k for j in judgments for k in j["scores"]})
            completed_answers = sum(j["answer_complete"] for j in judgments)
            observed_tokens = sum(m.get("transport_total_tokens") or 0 for m in metrics)
            groups[f"{dataset}/{mode}"] = {
                "rows": len(rows), "reviewed": len(judgments),
                "review_failed": sum(bool(r.get("review")) and not r["review"].get("judgment") for r in rows),
                "rows_with_known_citations_outside_review_view": sum(
                    bool(r["metrics"].get("review_known_citations_omitted")) for r in rows),
                "succeeded_but_review_incomplete": sum(
                    r["metrics"].get("loop_status") == "succeeded"
                    and not r["review"]["judgment"]["answer_complete"]
                    for r in rows if r.get("review") and r["review"].get("judgment")),
                "completed_but_loop_not_succeeded": sum(
                    r["metrics"].get("loop_status") != "succeeded"
                    and r["review"]["judgment"]["answer_complete"]
                    for r in rows if r.get("review") and r["review"].get("judgment")),
                "runtime_errors": sum(bool(r["metrics"].get("llm_error")) or r["outcome"] in
                                      ("exception", "harness_timeout", "process_error") for r in rows),
                "deadline_cancelled": sum(r["outcome"] == "deadline_cancelled" for r in rows),
                "loop_states": dict(Counter(r["metrics"].get("loop_status") or r["outcome"] for r in rows)),
                "completed_by_review": completed_answers,
                "full_core_correctness": sum(j["scores"].get("core_correctness") == 2 for j in judgments)
                                         if dataset == "final_answer" else None,
                "auxiliary_quality_percent": summarise([100 * sum(j["scores"].values()) / (2 * len(j["scores"]))
                                                        for j in judgments]),
                "dimension_scores": {d: summarise([j["scores"][d] for j in judgments if d in j["scores"]])
                                     for d in dimensions},
                "grounding": summarise([j["grounding"] for j in judgments]),
                "review_confidence": summarise([j["confidence"] for j in judgments]),
                "transport_usage_complete_rows": sum(m.get("transport_usage_complete") is True for m in metrics),
                "application_usage_complete_rows": sum(m.get("token_capture_complete") is True for m in metrics),
                "application_usage_eligible_rows": sum((m.get("llm_call_count") or 0) > 0 for m in metrics),
                "application_transport_usage_gap_rows": sum(
                    isinstance(m.get("total_tokens"), (int, float))
                    and isinstance(m.get("transport_total_tokens"), (int, float))
                    and m["total_tokens"] != m["transport_total_tokens"] for m in metrics),
                "rows_without_observed_llm_requests": sum(m.get("transport_requests") == 0 for m in metrics),
                "review_tokens": sum(u.get("total_tokens", 0) for u in review_usage),
                "review_completed_usage_records": len(review_usage),
                "transport_total_tokens_sum": observed_tokens,
                "known_tokens_per_completed_answer": observed_tokens / completed_answers if completed_answers else None,
                "transport_requests_sum": sum(m.get("transport_requests") or 0 for m in metrics),
                "usd": None,
                **{key: summarise([m.get(key) for m in metrics]) for key in (
                    "latency_ms", "wall_ms", "total_tokens", "transport_total_tokens",
                    "transport_input_tokens", "transport_output_tokens", "transport_cached_input_tokens",
                    "llm_call_count", "transport_requests", "transport_http_errors", "external_api_calls",
                    "loop_iterations", "compactions", "peak_context_ratio", "advisory_gap_count", "fact_coverage",
                )},
            }
    return groups


def paired_comparison(left: list[dict], right: list[dict]) -> dict:
    """Compare common questions only; positive deltas mean right minus left.

    Scores are rubric-normalized per question, not pooled across unrelated
    open-task dimensions. One observation per side is descriptive, not a test
    of statistical significance.
    """
    left_by_id = {row["qid"]: row for row in left}
    right_by_id = {row["qid"]: row for row in right}
    if len(left_by_id) != len(left) or len(right_by_id) != len(right):
        raise ValueError("A paired side must contain only one record per question")
    output = {}
    for dataset in ("final_answer", "open_task"):
        pairs = [(left_by_id[qid], right_by_id[qid]) for qid in sorted(left_by_id.keys() & right_by_id.keys())
                 if left_by_id[qid]["dataset"] == dataset]
        quality_deltas = []
        cases = []
        core_cases = []
        for before, after in pairs:
            if before["query"] != after["query"] or before["dataset"] != after["dataset"]:
                raise ValueError("Paired question mismatch")
            judgments = [(r.get("review") or {}).get("judgment") for r in (before, after)]
            if all(judgments):
                if set(judgments[0]["scores"]) != set(judgments[1]["scores"]):
                    raise ValueError("Paired rubric mismatch")
                scores = [100 * sum(j["scores"].values()) / (2 * len(j["scores"])) for j in judgments]
                delta = scores[1] - scores[0]
                quality_deltas.append(delta)
                cases.append({"qid": before["qid"], "left": scores[0], "right": scores[1], "delta": delta})
                if dataset == "final_answer":
                    core_scores = [j["scores"]["core_correctness"] for j in judgments]
                    core_cases.append({"qid": before["qid"], "left": core_scores[0], "right": core_scores[1],
                                       "delta": core_scores[1] - core_scores[0]})
        output[dataset] = {
            "pairs": len(pairs), "reviewed_pairs": len(quality_deltas),
            "quality_delta_points": summarise(quality_deltas),
            "right_better": sum(d > 0 for d in quality_deltas),
            "equal": sum(d == 0 for d in quality_deltas),
            "right_worse": sum(d < 0 for d in quality_deltas),
            "cases": cases,
            "core_correctness_regressions": [case for case in core_cases if case["delta"] < 0],
            "core_correctness_improvements": [case for case in core_cases if case["delta"] > 0],
            **{key + "_delta": summarise([
                b["metrics"][key] - a["metrics"][key] for a, b in pairs
                if isinstance(a["metrics"].get(key), (int, float))
                and isinstance(b["metrics"].get(key), (int, float))
            ]) for key in ("wall_ms", "transport_total_tokens", "transport_requests")},
        }
    return output


def render_cases(report: dict) -> str:
    """Render all planned questions, keeping absent results visibly absent."""
    columns = ("r1/guided", "r1/autonomous", "r2/guided", "r2/autonomous")
    lines = ["# 自主度评测逐题附表", "",
             "由摘要绑定校验后的机器结果生成；辅助分不等于事实正确率，完整交付不等于循环 succeeded。",
             "成本为观测到的 LLM HTTP total token；标记 ≥ 表示用量仅为下界。", ""]
    for title, prefix in (("事实题", "final"), ("开放题", "open")):
        selected = [(qid, case) for qid, case in sorted(report["cases"].items()) if qid.startswith(prefix)]
        lines.extend([f"## {title}：质量与交付", "",
                      "单元格：辅助分 /100；完整交付；核心正确性 /2（仅事实题）；循环终态。", "",
                      "| 题号 | " + " | ".join(columns) + " |", "|---|---|---|---|---|"])
        for qid, case in selected:
            cells = []
            for column in columns:
                row = case.get(column)
                if not row:
                    cells.append("未完成")
                    continue
                judgment = row.get("judgment")
                status = row["metrics"].get("loop_status") or row["outcome"]
                if not judgment:
                    cells.append(f"未评审；{status}")
                    continue
                scores = judgment["scores"]
                quality = 100 * sum(scores.values()) / (2 * len(scores))
                complete = "是" if judgment["answer_complete"] else "否"
                core = f"；{scores['core_correctness']}/2" if "core_correctness" in scores else ""
                cells.append(f"{quality:.2f}；{complete}{core}；{status}")
            lines.append(f"| {qid} | " + " | ".join(cells) + " |")
        lines.extend(["", f"## {title}：成本与耗时", "", "单元格：已知 token；墙钟秒。", "",
                      "| 题号 | " + " | ".join(columns) + " |", "|---|---|---|---|---|"])
        for qid, case in selected:
            cells = []
            for column in columns:
                row = case.get(column)
                if not row:
                    cells.append("未完成")
                    continue
                metrics = row["metrics"]
                tokens = metrics.get("transport_total_tokens")
                lower = "" if metrics.get("transport_usage_complete") else "≥"
                token_text = f"{lower}{tokens:,}" if tokens is not None else "未知"
                seconds = metrics.get("wall_ms")
                time_text = f"{seconds / 1000:.1f}" if seconds is not None else "未知"
                cells.append(f"{token_text}；{time_text}")
            lines.append(f"| {qid} | " + " | ".join(cells) + " |")
        lines.extend(["", f"## {title}：问题索引", ""])
        for qid, case in selected:
            query = str(case["query"]).replace("\n", " ").replace("\r", " ")
            lines.append(f"- `{qid}`：{query}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", required=True)
    parser.add_argument("--out")
    parser.add_argument("--cases-out", help="Create a new Markdown appendix; refuses overwrite")
    args = parser.parse_args()
    study = ROOT / "runtime/baseline" / args.study
    result = {"study": args.study, "human_review": "not_performed", "rounds": {}, "cases": {}}
    round_records = {}
    manifests = {}
    for name in ("r1", "r2"):
        if not (study / name / "manifest.json").exists():
            continue
        records, missing = load_round(study, name)
        manifests[name] = json.loads((study / name / "manifest.json").read_text())
        round_records[name] = records
        result["rounds"][name] = {"completed": len(records), "missing": missing, "groups": aggregate(records)}
        result["rounds"][name]["autonomous_minus_guided"] = paired_comparison(
            [r for r in records if r["requested_mode"] == "guided"],
            [r for r in records if r["requested_mode"] == "autonomous"],
        )
        for row in records:
            result["cases"].setdefault(row["qid"], {"query": row["query"]})[f"{name}/{row['requested_mode']}"] = {
                "outcome": row["outcome"], "metrics": row["metrics"],
                "judgment": (row.get("review") or {}).get("judgment"), "result_digest": row["result_digest"],
            }
    if set(round_records) == {"r1", "r2"}:
        for field in ("config_digest", "dataset_digests", "protocol_digest", "workers", "soft_timeout", "hard_timeout", "parameters"):
            if manifests["r1"][field] != manifests["r2"][field]:
                raise ValueError(f"Round comparison input changed: {field}")
        sources = [manifests[name]["source_files"] for name in ("r1", "r2")]
        result["changed_source_files"] = sorted(
            key for key in sources[0].keys() | sources[1].keys() if sources[0].get(key) != sources[1].get(key))
        result["r2_minus_r1"] = {mode: paired_comparison(
            [r for r in round_records["r1"] if r["requested_mode"] == mode],
            [r for r in round_records["r2"] if r["requested_mode"] == mode],
        ) for mode in ("guided", "autonomous")}
    result["provenance"] = {name: {
        **{key: manifest.get(key) for key in (
            "created_at", "head", "provider", "model", "config_digest", "dataset_digests",
            "protocol_digest", "workers", "soft_timeout", "hard_timeout", "parameters")},
        "source_file_count": len(manifest["source_files"]),
        "source_manifest_digest": digest(json.dumps(manifest["source_files"], sort_keys=True).encode()),
        "source_archive_digest": digest((study / name / "source.zip").read_bytes()),
    } for name, manifest in manifests.items()}
    result["review_overhead"] = {}
    for version, directory in (("v1", "reviews"), ("v2-low", "reviews-v2-low"), ("v3-evidence", "reviews-v3-evidence")):
        reviews = [json.loads(path.read_text()) for name in manifests
                   for path in (study / name / directory).glob("*.json")]
        attempts = [attempt for review in reviews for attempt in review.get("attempts", [])]
        result["review_overhead"][version] = {
            "records": len(reviews), "attempts": len(attempts),
            "known_total_tokens": sum((a.get("usage") or {}).get("total_tokens", 0) for a in attempts),
            "attempts_with_usage": sum(bool(a.get("usage")) for a in attempts),
            "used_for_final_comparison": version == "v3-evidence",
            "interrupted_request_usage_unknown": version in ("v1", "v2-low"),
            "usd": None,
        }
    if args.out:
        write_new(Path(args.out), result)
    else:
        print(json.dumps({k: v for k, v in result.items() if k != "cases"}, ensure_ascii=False, indent=2))
    if args.cases_out:
        with Path(args.cases_out).open("x", encoding="utf-8") as handle:
            handle.write(render_cases(result))


if __name__ == "__main__":
    main()
