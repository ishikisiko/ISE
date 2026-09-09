"""Scorecard, report and regression gate for a quality run (plan Q5-02 / Q5-03).

    python -m tests.quality_report --run runtime/quality/<run>                       # scorecard.json + report.md
    python -m tests.quality_report --run <run> --answers <study round>               # answers from another source
    python -m tests.quality_report --compare <run_a> <run_b> [--output-file ...]     # paired regression gate
    python -m tests.quality_report --compare-modes <run> [--left guided --right autonomous]

Missing inputs are reported as ``未运行`` rather than zero. Index values are
means of the components that exist; the report lists which components fed
each index. The regression gate implements design §5.2: hard-gate counts may
not grow, the retrieval / evidence / answer indices may not show more losses
than wins in the paired comparison, and the cost ratio must stay <= 1.2
unless ``--accept-cost`` records an explicit acceptance.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.quality import citation_eval, cost_eval, loop_eval  # noqa: E402
from tests.quality.common import (  # noqa: E402
    ROOT,
    bootstrap_ci,
    core_correct_of,
    dataset_of,
    delivered,
    format_number,
    load_answer_records,
    macro_average,
    mean,
    paired_outcomes,
    read_json,
    utc_now,
    write_json,
)

NOT_RUN = "未运行"
INPUT_FILES = {
    "validity": "validity.json",
    "analysis": "analysis_eval.json",
    "evidence_offline": "evidence_eval_offline.json",
    "search": "search_report.json",
    "local": "local_rag_eval.json",
    "citation": "citation_eval.json",
    "evidence": "evidence_eval.json",
    "loop": "loop_eval.json",
    "cost": "cost.json",
    "fetch": "fetch_eval.json",
    "reliability": "reliability.json",
    "safety": "safety.json",
    "calibration": "calibration.json",
    "routing": "routing_eval.json",
    "preflight": "preflight_eval.json",
    "offline_results": "offline_results.json",
    "injection": "injection_eval.json",
}
COST_RATIO_LIMIT = 1.2


# ----------------------------------------------------------------- loading
def load_inputs(run_dir: Path) -> Dict[str, Any]:
    inputs: Dict[str, Any] = {}
    for key, name in INPUT_FILES.items():
        path = run_dir / name
        inputs[key] = read_json(path) if path.is_file() else None
    return inputs


def _get(payload: Any, *path: str) -> Any:
    current = payload
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _metric(payload: Any, *path: str) -> Optional[float]:
    value = _get(payload, *path)
    if isinstance(value, dict):
        value = value.get("value", value.get("rate", value.get("mean")))
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None


# ------------------------------------------------------------ per question
def per_question_rows(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for record in records:
        citation = citation_eval.evaluate_row(record)
        loop = loop_eval.evaluate_row(record)
        cost = cost_eval.evaluate_row(record)
        review = record.get("review") if isinstance(record.get("review"), dict) else {}
        judgment = review.get("judgment") if isinstance(review.get("judgment"), dict) else {}
        scores = judgment.get("scores") if isinstance(judgment.get("scores"), dict) else {}
        dims: Dict[str, Any] = {}
        for key in ("core_correct", "request_completeness", "evidence_support", "abstention"):
            if key in judgment and judgment[key] is not None:
                dims[key] = judgment[key]
        if "core_correctness" in scores:
            dims.setdefault("core_correct", scores["core_correctness"])
        for key in ("request_completeness", "evidence_support"):
            if key in scores:
                dims.setdefault(key, scores[key])
        if "grounding" in judgment and "evidence_support" not in dims:
            dims["evidence_support"] = judgment["grounding"]
        open_dims = {name: value for name, value in scores.items() if name not in {"core_correctness", "request_completeness", "evidence_support"}}
        aux = (100 * sum(scores.values()) / (2 * len(scores))) if scores else None
        checks = judgment.get("citation_checks") if isinstance(judgment.get("citation_checks"), list) else []
        precision = (sum(1 for check in checks if isinstance(check, dict) and check.get("verdict") == "supports") / len(checks)) if checks else None
        rows.append(
            {
                "qid": record.get("qid"),
                "dataset": dataset_of(record),
                "mode": record.get("mode"),
                "category": record.get("category") or dataset_of(record),
                "loop_status": loop["loop_status"],
                "outcome": record.get("outcome"),
                "delivered": loop["delivered"],
                "core_correct": core_correct_of(record),
                "request_completeness": dims.get("request_completeness"),
                "evidence_support": dims.get("evidence_support"),
                "abstention": dims.get("abstention"),
                "open_dimensions": open_dims,
                "aux_score": aux,
                "answer_complete": judgment.get("answer_complete"),
                "factual_concerns": len(judgment.get("factual_concerns") or []) if judgment else None,
                "citation_precision": precision,
                "hallucinated_citations": citation["hallucinated_citation_count"],
                "citation_recall": citation["citation_recall"],
                "authority_compliance": citation["authority_compliance"],
                "false_exhaustion": loop["false_exhaustion"],
                "premature_success": loop["premature_success"],
                "budget_hits": loop["budget_hits"],
                "iterations": loop["iterations"],
                "total_tokens": cost["total_tokens"],
                "latency_ms": cost["latency_ms"],
                "provider_credits": cost["provider_credits"],
                "review_version": review.get("review_version") or ("v3-evidence" if scores and "core_correctness" in scores else None),
            }
        )
    return rows


# ---------------------------------------------------------------- scorecard
def _component(value: Optional[float], source: str, *, transform: Optional[str] = None) -> Dict[str, Any]:
    return {"value": value, "source": source, "status": "ok" if value is not None else NOT_RUN, **({"transform": transform} if transform else {})}


def _index(components: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    values = [entry["value"] for entry in components.values() if isinstance(entry.get("value"), (int, float))]
    return {"value": (sum(values) / len(values)) if values else None, "status": "ok" if values else NOT_RUN, "components": components, "available": len(values), "total": len(components)}


def build_scorecard(run_dir: Path, inputs: Dict[str, Any], rows: List[Dict[str, Any]], *, baseline: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    validity = inputs.get("validity")
    validity_block = {"present": validity is not None, "passed": _get(validity, "passed"), "metrics": _get(validity, "metrics") or {}, "verdicts": _get(validity, "verdicts") or {}, "status": "ok" if validity else NOT_RUN}

    judged = [row for row in rows if row["core_correct"] is not None]
    safety = inputs.get("safety")
    loop = inputs.get("loop")
    hard = {
        "core_correct_zero_count": sum(1 for row in judged if row["core_correct"] == 0) if judged else None,
        "core_correct_zero_qids": [row["qid"] for row in judged if row["core_correct"] == 0],
        "hallucinated_citation_count": sum(row["hallucinated_citations"] for row in rows) if rows else None,
        "hallucinated_citation_qids": [row["qid"] for row in rows if row["hallucinated_citations"]],
        "undelivered_count": sum(1 for row in rows if not row["delivered"]) if rows else None,
        "undelivered_qids": [row["qid"] for row in rows if not row["delivered"]],
        "false_exhaustion_count": _get(loop, "summary", "hard_gate", "false_exhaustion_count") if loop else (sum(1 for row in rows if row["false_exhaustion"]) if rows else None),
        "credential_leak_count": _get(safety, "hard_gate", "credential_leak_count") if safety else None,
        "over_budget_calls": sum(len(row["budget_hits"]) for row in rows) if rows else None,
        "factual_concerns_questions": sum(1 for row in rows if row["factual_concerns"]) if any(row["factual_concerns"] is not None for row in rows) else None,
    }
    gate_fields = ("core_correct_zero_count", "hallucinated_citation_count", "undelivered_count", "false_exhaustion_count", "credential_leak_count")
    available = {name: hard[name] for name in gate_fields if hard[name] is not None}
    hard["unavailable"] = [name for name in gate_fields if hard[name] is None]
    hard["passed"] = all(value == 0 for value in available.values()) if available else None

    search = inputs.get("search")
    fetch = inputs.get("fetch")
    local = inputs.get("local")
    local_default = None
    if local:
        for item in local.get("results") or []:
            if item.get("chunk_size") == 1000 and item.get("chunk_overlap") == 200:
                local_default = item
        local_default = local_default or local.get("best")
    retrieval = _index(
        {
            "hit_at_3": _component(_metric(search, "summary", "hit_at_3"), "search_report.json"),
            "gold_doc_recall_at_5": _component(_metric(search, "summary", "gold_doc_recall_at_5"), "search_report.json"),
            "ndcg_at_5": _component(_metric(search, "summary", "ndcg_at_5"), "search_report.json"),
            "gold_span_containment": _component(_metric(fetch, "summary", "gold_span_containment_when_fetched") if fetch else None, "fetch_eval.json (when fetched)"),
            "chunk_hit_at_3": _component(_metric(local_default, "by_k", "3", "chunk_hit_at_k") if local_default else None, "local_rag_eval.json (1000/200, k=3)"),
            "mrr_chunk": _component(_metric(local_default, "by_k", "3", "mrr_chunk") if local_default else None, "local_rag_eval.json (1000/200, k=3)"),
        }
    )
    evidence_offline = inputs.get("evidence_offline")
    evidence = inputs.get("evidence")
    citation = inputs.get("citation")
    precision_values = [row["citation_precision"] for row in rows if row["citation_precision"] is not None]
    evidence_index = _index(
        {
            "tier_accuracy": _component(_metric(evidence_offline, "tiering", "tier_accuracy_authoritative_collapsed"), "evidence_eval_offline.json"),
            "official_precision": _component(_metric(evidence_offline, "tiering", "official_precision"), "evidence_eval_offline.json"),
            "member_coverage_f1": _component(_metric(evidence, "summary", "member_coverage_f1"), "evidence_eval.json"),
            "citation_recall": _component(_metric(citation, "summary", "citation_recall"), "citation_eval.json"),
            "citation_precision": _component(mean(precision_values), "reviews (citation_checks supports share)"),
            "authority_compliance": _component(_metric(citation, "summary", "authority_compliance"), "citation_eval.json"),
        }
    )
    answer_index = _index(
        {
            "core_correct": _component(mean(row["core_correct"] / 2 for row in judged) if judged else None, "reviews / annotations", transform="/2"),
            "request_completeness": _component(mean(row["request_completeness"] / 2 for row in rows if row["request_completeness"] is not None), "reviews", transform="/2"),
            "grounding": _component(mean(row["evidence_support"] / 2 for row in rows if row["evidence_support"] is not None), "reviews", transform="/2"),
            "open_task_aux": _component(mean(row["aux_score"] / 100 for row in rows if row["aux_score"] is not None and row["dataset"] == "open_task"), "reviews", transform="/100"),
            "abstention_quality": _component(mean(row["abstention"] / 2 for row in rows if row["abstention"] is not None), "reviews / annotations", transform="/2"),
        }
    )
    analysis = inputs.get("analysis")
    routing = inputs.get("routing")
    calibration = inputs.get("calibration")
    premature = _metric(loop, "summary", "premature_success_rate")
    process_index = _index(
        {
            "members_f1": _component(_metric(analysis, "summary", "comparison_members", "f1"), "analysis_eval.json"),
            "route_accuracy": _component(_metric(routing, "summary", "route_accuracy"), "routing_eval.json"),
            "critic_block_precision": _component(_metric(calibration, "loop", "critic_block_precision"), "calibration.json (human)"),
            "judge_agreement": _component(_metric(calibration, "loop", "judge_agreement"), "calibration.json (human)"),
            "not_premature": _component((1 - premature) if premature is not None else None, "loop_eval.json", transform="1 - premature_success_rate"),
        }
    )
    cost = inputs.get("cost")
    cost_block: Dict[str, Any] = {
        "status": "ok" if cost else NOT_RUN,
        "absolute": {
            "tokens_mean": _metric(cost, "summary", "total_tokens", "mean") if cost else mean(row["total_tokens"] for row in rows),
            "latency_p95_ms": _metric(cost, "summary", "latency_ms", "p95") if cost else None,
            "latency_mean_ms": _metric(cost, "summary", "latency_ms", "mean") if cost else mean(row["latency_ms"] for row in rows),
            "credits_per_query": _metric(cost, "summary", "provider_credits_per_query") if cost else None,
            "tokens_per_correct_answer": _get(cost, "summary", "cost_per_correct_answer", "tokens_per_correct_answer") if cost else None,
        },
        "ratios_vs_baseline": None,
    }
    if baseline:
        base_abs = baseline.get("indices", {}).get("cost", {}).get("absolute", {})
        ratios = {}
        for key in ("tokens_mean", "latency_mean_ms", "credits_per_query"):
            left = base_abs.get(key)
            right = cost_block["absolute"].get(key)
            ratios[key] = (right / left) if isinstance(left, (int, float)) and left and isinstance(right, (int, float)) else None
        cost_block["ratios_vs_baseline"] = ratios
    reliability = inputs.get("reliability")
    offline_results = inputs.get("offline_results")
    fault = _get(offline_results, "fault_injection", "pass_rate")
    reliability_index = _index(
        {
            "consistency_at_3": _component(_metric(reliability, "consistency_at_3"), "reliability.json"),
            "not_hard_timeout": _component((1 - _metric(reliability, "hard_timeout_rate")) if _metric(reliability, "hard_timeout_rate") is not None else None, "reliability.json", transform="1 - hard_timeout_rate"),
            "fault_injection_pass_rate": _component(float(fault) if isinstance(fault, (int, float)) else None, "offline_results.json (pytest fault injection)"),
        }
    )
    by_category = {}
    for metric in ("core_correct", "citation_recall", "delivered"):
        groups: Dict[str, List[Any]] = {}
        for row in rows:
            value = row.get(metric)
            if value is None:
                continue
            groups.setdefault(str(row["category"]), []).append(float(value) / (2 if metric == "core_correct" else 1))
        by_category[metric] = macro_average(groups)
    intervals = {
        "core_correct": bootstrap_ci([row["core_correct"] / 2 for row in judged]) if judged else None,
        "citation_recall": bootstrap_ci([row["citation_recall"] for row in rows if row["citation_recall"] is not None]),
        "total_tokens": bootstrap_ci([row["total_tokens"] for row in rows if row["total_tokens"] is not None]),
    }
    return {
        "created_at": utc_now(),
        "run_dir": str(run_dir),
        "run_meta": read_json(run_dir / "run_meta.json") if (run_dir / "run_meta.json").is_file() else None,
        "inputs_present": {key: value is not None for key, value in inputs.items()},
        "questions": len(rows),
        "judged_questions": len(judged),
        "validity": validity_block,
        "hard_gates": hard,
        "indices": {"retrieval": retrieval, "evidence": evidence_index, "answer": answer_index, "process": process_index, "cost": cost_block, "reliability": reliability_index},
        "by_category": by_category,
        "confidence_intervals": intervals,
        "per_question": rows,
    }


# ------------------------------------------------------------------ report
def _fmt(value: Any, digits: int = 3) -> str:
    return NOT_RUN if value is None else format_number(value, digits)


def _worst(rows: List[Dict[str, Any]], key: str, *, reverse: bool = True, limit: int = 3, predicate=None) -> List[Dict[str, Any]]:
    candidates = [row for row in rows if row.get(key) is not None and (predicate is None or predicate(row))]
    candidates.sort(key=lambda row: row[key], reverse=reverse)
    return candidates[:limit]


def render_report(scorecard: Dict[str, Any], inputs: Dict[str, Any], *, answers_source: Optional[str] = None) -> str:
    rows = scorecard["per_question"]
    run_dir = scorecard["run_dir"]
    meta = scorecard.get("run_meta") or {}
    lines: List[str] = []
    lines += [f"# ISE 质量评测报告（{Path(run_dir).name}）", "",
              f"生成时间 {scorecard['created_at']}。产物目录 `{run_dir}`；答案记录来源 `{answers_source or run_dir}`。本报告由 `python -m tests.quality_report` 从运行产物生成，缺失的维度标为 **{NOT_RUN}**，不会写成 0。", ""]
    lines += ["## 1. 范围与证据等级", "",
              f"- 题数 {scorecard['questions']}；有裁判/人工判定的题数 {scorecard['judged_questions']}。",
              f"- 冻结信息：commit `{meta.get('commit') or '未知'}`，工作树{'有未提交改动' if meta.get('working_tree_dirty') else '干净'}；配置摘要 `{(meta.get('config') or {}).get('digest', '未知')}`；模型 `{(meta.get('config') or {}).get('model', '未知')}`；裁判 `{(meta.get('judge') or {}).get('model') if isinstance(meta.get('judge'), dict) else ((meta.get('config') or {}).get('judge') or {}).get('model', '未知')}`；rubric `{meta.get('rubric_version', '见 reviews 目录')}`。",
              "- 证据等级：真实运行产物（answer_details / result.json）→ 确定性离线重算（citation / evidence / loop / cost / fetch）→ 模型辅助评审（reviews）→ 人工标注（dataset/annotations）。裁判分不是人工分；核心正确性单列且拥有一票否决。", ""]
    validity = scorecard["validity"]
    lines += ["## 2. D0 度量有效性", ""]
    if not validity["present"]:
        lines += [f"- validity.json {NOT_RUN}；本报告的数字未经 D0 冒烟确认，按设计 §4 D0 规则应视为**数据可信度未验证**。", ""]
    else:
        lines += [f"- 结论：{'通过' if validity['passed'] else '未通过（数据不可信）'}", "", "| 指标 | 值 | 门槛判定 |", "|---|---:|---|"]
        for name, value in validity["metrics"].items():
            if isinstance(value, (int, float)) or value is None:
                lines.append(f"| `{name}` | {_fmt(value)} | {validity['verdicts'].get(name, '—')} |")
        lines.append("")
    hard = scorecard["hard_gates"]
    lines += ["## 3. 硬门槛（任一非零即未通过）", "", "| 门槛 | 计数 | 题号 |", "|---|---:|---|"]
    for name, qkey in (("核心事实错误（core_correct=0）", "core_correct_zero"), ("幻觉引用（citation_unresolved）", "hallucinated_citation"), ("未交付（delivered=false）", "undelivered")):
        lines.append(f"| {name} | {_fmt(hard.get(qkey + '_count'), 0)} | {', '.join(hard.get(qkey + '_qids') or []) or '—'} |")
    lines.append(f"| 假性耗尽 | {_fmt(hard.get('false_exhaustion_count'), 0)} | — |")
    lines.append(f"| 凭据泄漏 | {_fmt(hard.get('credential_leak_count'), 0)} | — |")
    lines.append(f"| 触顶预算调用 | {_fmt(hard.get('over_budget_calls'), 0)} | — |")
    lines += ["", f"硬门槛结论：{'通过' if hard.get('passed') else ('未通过' if hard.get('passed') is False else NOT_RUN)}；未运行的门槛：{', '.join(hard.get('unavailable') or []) or '无'}。", ""]
    lines += ["## 4. 六个指数", "", "| 指数 | 值 | 可用组成/总组成 | 组成 |", "|---|---:|---:|---|"]
    names = {"retrieval": "检索指数", "evidence": "证据指数", "answer": "答案指数", "process": "过程指数", "reliability": "可靠指数"}
    for key, label in names.items():
        block = scorecard["indices"][key]
        components = "；".join(f"{name}={_fmt(entry['value'])}" for name, entry in block["components"].items())
        lines.append(f"| {label} | {_fmt(block['value'])} | {block['available']}/{block['total']} | {components} |")
    cost = scorecard["indices"]["cost"]
    ratios = cost.get("ratios_vs_baseline")
    lines.append(f"| 成本指数（只报比值） | {('; '.join(f'{k}={_fmt(v)}' for k, v in ratios.items()) if ratios else '无基线')} | — | tokens 均值 {_fmt(cost['absolute'].get('tokens_mean'), 0)}；P95 时延 {_fmt(cost['absolute'].get('latency_p95_ms'), 0)} ms；每正确答案 token {_fmt(cost['absolute'].get('tokens_per_correct_answer'), 0)} |")
    lines.append("")
    if scorecard["confidence_intervals"].get("core_correct"):
        ci = scorecard["confidence_intervals"]["core_correct"]
        lines.append(f"core_correct/2 的 bootstrap 95% 区间：[{ci['low']:.3f}, {ci['high']:.3f}]（n={ci['n']}）。")
    else:
        lines.append("样本 < 20，不报置信区间。")
    lines.append("")
    lines += ["## 5. 各维度要点与三条失败", ""]
    dims: List[Tuple[str, str, List[str], List[str]]] = []
    citation = inputs.get("citation")
    loop = inputs.get("loop")
    costj = inputs.get("cost")
    fetch = inputs.get("fetch")
    search = inputs.get("search")
    analysis = inputs.get("analysis")
    tiering = inputs.get("evidence_offline")
    dims.append(("D1 查询理解", "analysis_eval.json", [
        f"intent_shape 准确率 {_fmt(_metric(analysis, 'summary', 'intent_shape_accuracy'))}；对比类成员 F1 {_fmt(_metric(analysis, 'summary', 'comparison_members', 'f1'))}；noise_member_rate {_fmt(_metric(analysis, 'summary', 'comparison_members', 'noise_member_rate'))}；false_temporal_fanout_rate {_fmt(_metric(analysis, 'summary', 'false_temporal_fanout_rate'))}"
    ] if analysis else [NOT_RUN], [f"noise: {', '.join(item['members']['noise'])}（{item['qid']}）" for item in (analysis or {}).get("per_query", []) if item["members"]["noise"]][:3]))
    dims.append(("D3 网页搜索", "search_report.json", [
        f"hit_at_3 {_fmt(_metric(search, 'summary', 'hit_at_3'))}；gold_doc_recall_at_5 {_fmt(_metric(search, 'summary', 'gold_doc_recall_at_5'))}；empty_result_rate {_fmt(_metric(search, 'summary', 'empty_result_rate'))}；authoritative_at_5 {_fmt(_metric(search, 'summary', 'authoritative_at_5'))}"
    ] if search else [NOT_RUN], [f"{item.get('query_id')} hit_at_3=false" for item in (search or {}).get("per_query", []) if item.get("hit_at_3") is False][:3]))
    dims.append(("D4 抓取与抽取", "fetch_eval.json", [
        f"fetch_success_rate {_fmt(_metric(fetch, 'summary', 'fetch_success_rate'))}；attempts/success {_fmt(_metric(fetch, 'summary', 'extractor_attempts_per_success'))}；gold_span_containment(when fetched) {_fmt(_metric(fetch, 'summary', 'gold_span_containment_when_fetched'))}；truncation_rate {_fmt(_metric(fetch, 'summary', 'truncation_rate_of_fetched_records'))}"
    ] if fetch else [NOT_RUN], [f"{item['qid']} 抓取 {item['fetch_success']}/{item['fetch_attempted']}" for item in (fetch or {}).get("per_query", []) if item["fetch_attempted"] and item["fetch_success"] < item["fetch_attempted"]][:3]))
    dims.append(("D6 证据与权威", "evidence_eval*.json", [
        f"tier_accuracy(collapsed) {_fmt(_metric(tiering, 'tiering', 'tier_accuracy_authoritative_collapsed'))}；official_precision {_fmt(_metric(tiering, 'tiering', 'official_precision'))}；resolver_accuracy {_fmt(_metric(tiering, 'resolver', 'resolver_accuracy'))}；aggregator_leak_rate {_fmt(_metric(inputs.get('evidence'), 'summary', 'aggregator_leak_rate'))}"
    ] if (tiering or inputs.get("evidence")) else [NOT_RUN], [f"lookalike 被判权威：{url}" for url in (_get(tiering, "tiering", "lookalike_false_authority") or [])][:3]))
    dims.append(("D7 答案质量", "reviews + citation_eval.json", [
        f"core_correct=2 比例 {_fmt(mean((row['core_correct'] == 2) for row in rows if row['core_correct'] is not None))}；citation_recall {_fmt(_metric(citation, 'summary', 'citation_recall'))}；authority_compliance {_fmt(_metric(citation, 'summary', 'authority_compliance'))}；hallucinated_citation_count {_fmt(_get(citation, 'summary', 'hard_gate', 'hallucinated_citation_count_total'), 0)}"
    ] if (citation or rows) else [NOT_RUN], [f"{row['qid']}（{row['mode']}）core_correct={row['core_correct']}" for row in rows if row["core_correct"] == 0][:3] or [f"{row['qid']} 幻觉引用 {row['hallucinated_citations']}" for row in rows if row["hallucinated_citations"]][:3]))
    dims.append(("D8 循环与终止", "loop_eval.json", [
        f"loop_status 分布 {json.dumps(_get(loop, 'summary', 'loop_status_dist') or {}, ensure_ascii=False)}；false_exhaustion_rate {_fmt(_metric(loop, 'summary', 'false_exhaustion_rate'))}；premature_success_rate {_fmt(_metric(loop, 'summary', 'premature_success_rate'))}；judge_error_rate {_fmt(_metric(loop, 'summary', 'judge_error_rate'))}；advisory_gap_ignore_rate {_fmt(_metric(loop, 'summary', 'advisory_gap_ignore_rate'))}"
    ] if loop else [NOT_RUN], [f"{qid} 提前成功" for qid in (_get(loop, "summary", "premature_success_qids") or [])][:3]))
    dims.append(("D9 成本", "cost.json", [
        f"tokens 均值 {_fmt(_metric(costj, 'summary', 'total_tokens', 'mean'), 0)}；时延 P50/P95 {_fmt(_metric(costj, 'summary', 'latency_ms', 'p50'), 0)}/{_fmt(_metric(costj, 'summary', 'latency_ms', 'p95'), 0)} ms；预算耗尽题 token 占比 {_fmt(_get(costj, 'summary', 'budget_exhaustion_cost', 'token_share'))}；每正确答案 token {_fmt(_get(costj, 'summary', 'cost_per_correct_answer', 'tokens_per_correct_answer'), 0)}"
    ] if costj else [NOT_RUN], [f"{row['qid']}（{row['mode']}）{format_number(int(row['total_tokens'] or 0))} token / {format_number(round((row['latency_ms'] or 0) / 1000, 1))} s" for row in _worst(rows, "total_tokens")]))
    reliability = inputs.get("reliability")
    dims.append(("D10 可靠性", "reliability.json + fault injection", [
        f"consistency_at_3 {_fmt(_metric(reliability, 'consistency_at_3'))}；hard_timeout_rate {_fmt(_metric(reliability, 'hard_timeout_rate'))}"
    ] if reliability else [f"{NOT_RUN}（故障注入见 offline_results.json）"], [f"{qid} 不一致" for qid in (_get(reliability, "inconsistent_qids") or [])][:3]))
    safety = inputs.get("safety")
    dims.append(("D11 安全", "safety.json", [
        f"credential_leak_count {_fmt(_get(safety, 'credential_leak_count'), 0)}；url_secret_leak_count {_fmt(_get(safety, 'url_secret_leak_count'), 0)}；pii_in_query_redaction {_fmt(_get(safety, 'pii_in_query_redaction'))}；denylist_compliance {_fmt(_get(safety, 'denylist_compliance'))}"
    ] if safety else [NOT_RUN], [f"{item['file']}" for item in (_get(safety, "leaking_files") or [])][:3]))
    for title, source, points, failures in dims:
        lines += [f"### {title}（{source}）", ""]
        lines += [f"- {point}" for point in points]
        lines.append("- 三条失败：" + ("；".join(failures) if failures else "无（或未运行）"))
        lines.append("")
    lines += ["## 6. 逐题附表", "", "单元格：交付；core_correct /2；幻觉引用；citation_recall；loop 终态；token；秒。", "", "| 题号 | 数据集 | 模式 | 交付 | core | 幻觉引用 | citation_recall | 终态 | token | 秒 |", "|---|---|---|---|---|---:|---:|---|---:|---:|"]
    for row in sorted(rows, key=lambda item: (str(item["dataset"]), str(item["qid"]), str(item["mode"]))):
        lines.append(f"| {row['qid']} | {row['dataset']} | {row['mode'] or '—'} | {'是' if row['delivered'] else '否'} | {_fmt(row['core_correct'], 0)} | {row['hallucinated_citations']} | {_fmt(row['citation_recall'])} | {row['loop_status'] or row['outcome'] or '—'} | {format_number(int(row['total_tokens'] or 0))} | {format_number(round((row['latency_ms'] or 0) / 1000, 1))} |")
    lines += ["", "## 7. 复现命令", "", "```bash",
              "# 离线重算（不发起任何网络请求）",
              f"python -m tests.quality.citation_eval --source {answers_source or run_dir} --output-file {run_dir}/citation_eval.json",
              f"python -m tests.quality.evidence_eval --source {answers_source or run_dir} --output-file {run_dir}/evidence_eval.json",
              f"python -m tests.quality.loop_eval --source {answers_source or run_dir} --output-file {run_dir}/loop_eval.json",
              f"python -m tests.quality.cost_eval --source {answers_source or run_dir} --output-file {run_dir}/cost.json",
              f"python -m tests.quality.fetch_eval --source {answers_source or run_dir} --output-file {run_dir}/fetch_eval.json",
              f"python -m tests.quality.analysis_eval --output-file {run_dir}/analysis_eval.json",
              f"python -m tests.quality.tiering_eval --output-file {run_dir}/evidence_eval_offline.json",
              f"python -m tests.quality.safety_eval --run {run_dir} --output-file {run_dir}/safety.json",
              f"python -m tests.quality_report --run {run_dir}" + (f" --answers {answers_source}" if answers_source else ""),
              "# 采集类步骤（真实运行）见 docs/quality_evaluation_plan.md 的 [真实运行] 任务",
              "```", ""]
    lines += ["## 8. 局限", "",
              "- 裁判分是模型辅助分，未经人工校准的维度只能作参考；kappa < 0.6 的维度应降级为仅人工。",
              "- 引用核验是机械核验：只确认 `[En]` 可解析且来源等级达标，不证明来源确实支持该句。",
              "- 搜索结果随时间漂移；跨期比较需先看 `--all-providers` 的跨供应商一致性。",
              f"- {NOT_RUN} 的维度不参与指数；指数只是可用组成的均值，不做加权。", ""]
    return "\n".join(lines)


# ------------------------------------------------------------- comparison
def paired_rows(left_rows: List[Dict[str, Any]], right_rows: List[Dict[str, Any]], *, pair_key) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    left_index = {pair_key(row): row for row in left_rows}
    right_index = {pair_key(row): row for row in right_rows}
    return [(left_index[key], right_index[key]) for key in sorted(set(left_index) & set(right_index), key=str)]


def compare_rows(left_rows: List[Dict[str, Any]], right_rows: List[Dict[str, Any]], *, pair_key, accept_cost: Optional[str] = None) -> Dict[str, Any]:
    pairs = paired_rows(left_rows, right_rows, pair_key=pair_key)
    by_dataset: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
    for left, right in pairs:
        by_dataset.setdefault(str(left["dataset"]), []).append((left, right))

    def block(subset: List[Tuple[Dict[str, Any], Dict[str, Any]]]) -> Dict[str, Any]:
        def hard(rows: List[Dict[str, Any]]) -> Dict[str, int]:
            return {
                "core_correct_zero": sum(1 for row in rows if row["core_correct"] == 0),
                "hallucinated_citations": sum(row["hallucinated_citations"] for row in rows),
                "undelivered": sum(1 for row in rows if not row["delivered"]),
                "false_exhaustion": sum(1 for row in rows if row["false_exhaustion"]),
            }
        left = [pair[0] for pair in subset]
        right = [pair[1] for pair in subset]
        return {
            "pairs": len(subset),
            "core_correct": paired_outcomes((a["core_correct"], b["core_correct"]) for a, b in subset),
            "aux_score": paired_outcomes((a["aux_score"], b["aux_score"]) for a, b in subset),
            "citation_recall": paired_outcomes((a["citation_recall"], b["citation_recall"]) for a, b in subset),
            "authority_compliance": paired_outcomes((a["authority_compliance"], b["authority_compliance"]) for a, b in subset),
            "delivered": paired_outcomes((float(a["delivered"]), float(b["delivered"])) for a, b in subset),
            "tokens": paired_outcomes((a["total_tokens"], b["total_tokens"]) for a, b in subset if a["total_tokens"] is not None and b["total_tokens"] is not None),
            "latency_ms": paired_outcomes((a["latency_ms"], b["latency_ms"]) for a, b in subset if a["latency_ms"] is not None and b["latency_ms"] is not None),
            "hard_gates": {"left": hard(left), "right": hard(right)},
            "cost_ratio": {
                "tokens": (mean(b["total_tokens"] for b in right) / mean(a["total_tokens"] for a in left)) if mean(a["total_tokens"] for a in left) else None,
                "latency": (mean(b["latency_ms"] for b in right) / mean(a["latency_ms"] for a in left)) if mean(a["latency_ms"] for a in left) else None,
            },
        }

    overall = block(pairs)
    datasets = {name: block(subset) for name, subset in sorted(by_dataset.items())}
    hard_left = overall["hard_gates"]["left"]
    hard_right = overall["hard_gates"]["right"]
    hard_ok = all(hard_right[name] <= hard_left[name] for name in hard_left)
    index_checks = {
        "retrieval": None,  # needs paired search_report per_query; reported as unavailable in this comparison
        "evidence": overall["citation_recall"],
        "answer": overall["core_correct"],
    }
    no_more_losses = all(check is None or check["losses"] <= check["wins"] for check in index_checks.values())
    ratio = overall["cost_ratio"]["tokens"]
    cost_ok = ratio is None or ratio <= COST_RATIO_LIMIT or bool(accept_cost)
    return {
        "created_at": utc_now(),
        "pairs": len(pairs),
        "overall": overall,
        "by_dataset": datasets,
        "gate": {
            "hard_gates_not_increased": hard_ok,
            "no_index_with_more_losses_than_wins": no_more_losses,
            "index_checks": {name: (None if check is None else {k: check[k] for k in ("wins", "ties", "losses")}) for name, check in index_checks.items()},
            "cost_ratio_tokens": ratio,
            "cost_ratio_ok": cost_ok,
            "cost_acceptance_note": accept_cost,
            "accepted": bool(hard_ok and no_more_losses and cost_ok),
        },
        "per_pair": [
            {"key": str(pair_key(a)), "qid": a["qid"], "dataset": a["dataset"], "left": {k: a[k] for k in ("core_correct", "aux_score", "delivered", "citation_recall", "total_tokens", "latency_ms")}, "right": {k: b[k] for k in ("core_correct", "aux_score", "delivered", "citation_recall", "total_tokens", "latency_ms")}}
            for a, b in pairs
        ],
    }


def rows_for(source: Path) -> List[Dict[str, Any]]:
    return per_question_rows(load_answer_records(source))


# --------------------------------------------------------------------- main
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", default=None)
    parser.add_argument("--answers", default=None, help="Answer-record source when it differs from the run directory (e.g. a study round).")
    parser.add_argument("--baseline", default=None, help="Scorecard.json of a baseline run for cost ratios.")
    parser.add_argument("--output", default=None, help="Report markdown path (default <run>/report.md).")
    parser.add_argument("--compare", nargs=2, metavar=("RUN_A", "RUN_B"), default=None)
    parser.add_argument("--compare-modes", default=None, help="Run/round whose records are split by autonomy mode.")
    parser.add_argument("--left", default="guided")
    parser.add_argument("--right", default="autonomous")
    parser.add_argument("--accept-cost", default=None, help="Explicit acceptance note for a cost ratio above 1.2.")
    parser.add_argument("--output-file", default=None, help="JSON output for --compare / --compare-modes.")
    args = parser.parse_args()

    if args.compare or args.compare_modes:
        if args.compare_modes:
            source = Path(args.compare_modes)
            rows = rows_for(source)
            left_rows = [row for row in rows if row["mode"] == args.left]
            right_rows = [row for row in rows if row["mode"] == args.right]
            result = compare_rows(left_rows, right_rows, pair_key=lambda row: row["qid"], accept_cost=args.accept_cost)
            result.update({"source": str(source), "left": args.left, "right": args.right})
        else:
            left_rows = rows_for(Path(args.compare[0]))
            right_rows = rows_for(Path(args.compare[1]))
            multi_mode = len({row["mode"] for row in left_rows}) > 1 or len({row["mode"] for row in right_rows}) > 1
            key = (lambda row: (row["qid"], row["mode"])) if multi_mode else (lambda row: row["qid"])
            result = compare_rows(left_rows, right_rows, pair_key=key, accept_cost=args.accept_cost)
            result.update({"left": args.compare[0], "right": args.compare[1]})
        if args.output_file:
            write_json(args.output_file, result)
        print(json.dumps({key: value for key, value in result.items() if key != "per_pair"}, ensure_ascii=False, indent=2))
        return

    if not args.run:
        parser.error("--run, --compare or --compare-modes is required")
    run_dir = Path(args.run)
    answers = Path(args.answers) if args.answers else run_dir
    inputs = load_inputs(run_dir)
    rows = per_question_rows(load_answer_records(answers)) if (answers.is_file() or (answers / "runs").is_dir() or (answers / "answer_details.jsonl").is_file() or any((answers / name).is_file() for name in ("final_answer_details.jsonl", "open_task_details.jsonl"))) else []
    baseline = read_json(args.baseline) if args.baseline else None
    scorecard = build_scorecard(run_dir, inputs, rows, baseline=baseline)
    write_json(run_dir / "scorecard.json", scorecard)
    report = render_report(scorecard, inputs, answers_source=str(answers) if args.answers else None)
    output = Path(args.output) if args.output else run_dir / "report.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(json.dumps({"scorecard": str(run_dir / "scorecard.json"), "report": str(output), "hard_gates_passed": scorecard["hard_gates"]["passed"], "indices": {key: block.get("value") for key, block in scorecard["indices"].items() if key != "cost"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
