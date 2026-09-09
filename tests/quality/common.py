"""Shared helpers for the quality-evaluation scripts under ``tests/quality``.

Everything here is offline: file IO, hashing, run-directory layout, summary
statistics and a uniform loader for per-question answer records. Real runs
and judges live in their own scripts.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUALITY_RUNTIME_ROOT = ROOT / "runtime" / "quality"
QUALITY_CONFIG_PATH = ROOT / "config.quality.json"

SECRET_MARKERS = ("key", "token", "secret", "password", "authorization", "cookie")


# --------------------------------------------------------------------------- io
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def write_json(path: Path | str, payload: Any, *, exclusive: bool = False) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "x" if exclusive else "w"
    with path.open(mode, encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
        handle.write("\n")
    return path


def read_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path: Path | str) -> List[Dict[str, Any]]:
    path = Path(path)
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def write_jsonl(path: Path | str, rows: Iterable[Dict[str, Any]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return path


def read_csv_rows(path: Path | str) -> List[Dict[str, Any]]:
    """CSV reader tolerant of the blank first line several datasets carry."""
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        lines = [line for line in handle if line.strip()]
    return list(csv.DictReader(lines))


def write_csv_rows(path: Path | str, rows: Sequence[Dict[str, Any]], fieldnames: Optional[Sequence[str]] = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    names = list(fieldnames or (rows[0].keys() if rows else []))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in names})
    return path


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    config_path = path or os.environ.get("NLP_CONFIG_PATH") or str(ROOT / "config.json")
    with open(config_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def quality_config_path(path: Optional[str] = None) -> Path:
    candidate = path or os.environ.get("ISE_QUALITY_CONFIG") or QUALITY_CONFIG_PATH
    resolved = Path(candidate)
    return resolved if resolved.is_absolute() else ROOT / resolved


def load_quality_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Evaluation-framework defaults (``config.quality.json``); credentials stay in ``config.json``.

    An explicitly requested file must exist; the built-in default file may be
    absent, in which case every script keeps its own argparse defaults.
    """
    file_path = quality_config_path(path)
    if not file_path.is_file():
        if path or os.environ.get("ISE_QUALITY_CONFIG"):
            raise SystemExit(f"quality config not found: {file_path}")
        return {}
    with file_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise SystemExit(f"{file_path}: quality config must be a JSON object")
    return payload


def quality_defaults(config: Dict[str, Any], section: str) -> Dict[str, Any]:
    """One block of the quality config, minus ``_comment``-style annotations."""
    block = config.get(section)
    if not isinstance(block, dict):
        return {}
    return {key: value for key, value in block.items() if not key.startswith("_")}


def apply_config_defaults(parser: argparse.ArgumentParser, defaults: Dict[str, Any], *, source: str) -> Dict[str, Any]:
    """Feed a config block into ``parser`` as defaults; an explicit CLI flag still wins.

    Unknown keys and values the flag's own ``type``/``choices`` would reject are
    fatal, so a typo in the config file can never silently keep the built-in
    default. Reads ``parser._actions`` because argparse exposes no public map.
    """
    actions = {action.dest: action for action in parser._actions if action.dest not in {"help", argparse.SUPPRESS}}
    unknown = sorted(key for key in defaults if key not in actions)
    if unknown:
        raise SystemExit(f"{source}: unknown option(s): {', '.join(unknown)}")
    resolved: Dict[str, Any] = {}
    for key, value in defaults.items():
        action = actions[key]
        if value is not None:
            if action.nargs == 0:
                if not isinstance(value, bool):
                    raise SystemExit(f"{source}: {key} must be true or false, got {value!r}")
            elif action.type is not None:
                try:
                    value = action.type(value)
                except (TypeError, ValueError) as exc:
                    raise SystemExit(f"{source}: invalid value for {key}: {value!r} ({exc})")
            if action.choices is not None and value not in action.choices:
                raise SystemExit(f"{source}: {key} must be one of {', '.join(map(str, action.choices))}, got {value!r}")
        resolved[key] = value
    parser.set_defaults(**resolved)
    return resolved


# ---------------------------------------------------------------- provenance
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path | str) -> Optional[str]:
    path = Path(path)
    if not path.is_file():
        return None
    return sha256_bytes(path.read_bytes())


def git_head() -> Optional[str]:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:  # noqa: BLE001 - not a git checkout
        return None


def git_dirty() -> Optional[bool]:
    try:
        output = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        return None
    return bool(output.strip())


def redact_config(value: Any) -> Any:
    """Drop credential-like keys; keep structure so a digest stays comparable."""
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, child in value.items():
            name = str(key).casefold()
            if any(marker in name for marker in SECRET_MARKERS):
                redacted[str(key)] = "[redacted]" if child not in (None, "", []) else child
            else:
                redacted[str(key)] = redact_config(child)
        return redacted
    if isinstance(value, list):
        return [redact_config(item) for item in value]
    return value


def config_summary(config: Dict[str, Any]) -> Dict[str, Any]:
    """The frozen, secret-free view of a config used by run_meta."""
    redacted = redact_config(config)
    return {
        "digest": sha256_bytes(json.dumps(redacted, sort_keys=True, ensure_ascii=False).encode("utf-8")),
        "llm_provider": config.get("LLM_PROVIDER"),
        "model": ((config.get("providers") or {}).get(config.get("LLM_PROVIDER") or "") or {}).get("model"),
        "judge": redact_config((config.get("termination") or {}).get("judge") or {}),
        "searchFallback": config.get("searchFallback"),
        "autonomy_mode": (config.get("autonomy") or {}).get("mode", "guided"),
        "termination": redact_config(
            {k: v for k, v in (config.get("termination") or {}).items() if k != "judge"}
        ),
        "localRag": config.get("localRag") or config.get("local_rag"),
        "embeddings": redact_config(config.get("embeddings") or {}),
    }


def dataset_digests(paths: Iterable[Path | str]) -> Dict[str, Optional[str]]:
    digests: Dict[str, Optional[str]] = {}
    for path in paths:
        path = Path(path)
        key = str(path.relative_to(ROOT)) if path.is_absolute() and ROOT in path.parents else str(path)
        digests[key] = sha256_file(path)
    return digests


def build_run_meta(
    *,
    tag: str,
    config: Optional[Dict[str, Any]],
    datasets: Iterable[Path | str] = (),
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "schema_version": 1,
        "tag": tag,
        "created_at": utc_now(),
        "commit": git_head(),
        "working_tree_dirty": git_dirty(),
        "python": sys.version.split()[0],
        "config": config_summary(config) if config else None,
        "datasets": dataset_digests(datasets),
    }
    if extra:
        meta.update(extra)
    return meta


def new_run_dir(tag: str, *, root: Path | str = QUALITY_RUNTIME_ROOT, date: Optional[str] = None) -> Path:
    directory = Path(root) / f"{date or today()}-{tag}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


# ---------------------------------------------------------------- statistics
def mean(values: Iterable[Optional[float]]) -> Optional[float]:
    usable = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not usable:
        return None
    return sum(usable) / len(usable)


def percentile(values: Sequence[float], ratio: float) -> Optional[float]:
    usable = sorted(float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool))
    if not usable:
        return None
    if len(usable) == 1:
        return usable[0]
    index = (len(usable) - 1) * ratio
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return usable[lower]
    weight = index - lower
    return usable[lower] + (usable[upper] - usable[lower]) * weight


def summarise(values: Iterable[Optional[float]]) -> Dict[str, Any]:
    usable = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    summary: Dict[str, Any] = {"denominator": len(usable), "mean": None, "p50": None, "p95": None, "min": None, "max": None}
    if not usable:
        return summary
    summary.update(
        mean=sum(usable) / len(usable),
        p50=percentile(usable, 0.5),
        p95=percentile(usable, 0.95),
        min=min(usable),
        max=max(usable),
    )
    return summary


def rate(values: Iterable[Optional[bool]]) -> Dict[str, Any]:
    usable = [bool(v) for v in values if v is not None]
    positives = sum(1 for v in usable if v)
    return {"denominator": len(usable), "positives": positives, "rate": (positives / len(usable)) if usable else None}


def distribution(values: Iterable[Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        key = str(value if value is not None else "none")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def bootstrap_ci(
    values: Sequence[float],
    *,
    iterations: int = 1000,
    alpha: float = 0.05,
    min_n: int = 20,
    seed: int = 20260909,
    statistic: Callable[[Sequence[float]], float] = lambda xs: sum(xs) / len(xs),
) -> Optional[Dict[str, float]]:
    """Percentile bootstrap CI of ``statistic``; None below ``min_n`` samples."""
    usable = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if len(usable) < min_n:
        return None
    rng = random.Random(seed)
    stats: List[float] = []
    for _ in range(iterations):
        sample = [usable[rng.randrange(len(usable))] for _ in usable]
        stats.append(statistic(sample))
    stats.sort()
    lower = stats[int(math.floor(alpha / 2 * (len(stats) - 1)))]
    upper = stats[int(math.ceil((1 - alpha / 2) * (len(stats) - 1)))]
    return {"low": lower, "high": upper, "n": len(usable), "iterations": iterations}


def macro_average(groups: Dict[str, Sequence[Optional[float]]], *, min_n: int = 5) -> Dict[str, Any]:
    """Per-category means plus their macro average.

    Categories with fewer than ``min_n`` usable samples are reported as counts
    only (design §3.4) and excluded from the macro average.
    """
    per_group: Dict[str, Any] = {}
    means: List[float] = []
    for name, values in sorted(groups.items()):
        usable = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        entry: Dict[str, Any] = {"n": len(usable)}
        if len(usable) >= min_n:
            entry["mean"] = sum(usable) / len(usable)
            means.append(entry["mean"])
        else:
            entry["mean"] = None
            entry["count_only"] = True
            entry["positives"] = sum(1 for v in usable if v >= 1.0)
        per_group[name] = entry
    return {"groups": per_group, "macro_mean": (sum(means) / len(means)) if means else None, "min_n": min_n}


def paired_outcomes(pairs: Iterable[tuple[Optional[float], Optional[float]]], *, higher_is_better: bool = True) -> Dict[str, Any]:
    """Win / tie / loss of ``right`` against ``left`` plus the mean delta."""
    wins = ties = losses = 0
    deltas: List[float] = []
    for left, right in pairs:
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            continue
        delta = float(right) - float(left)
        if not higher_is_better:
            delta = -delta
        deltas.append(float(right) - float(left))
        if delta > 0:
            wins += 1
        elif delta < 0:
            losses += 1
        else:
            ties += 1
    return {"wins": wins, "ties": ties, "losses": losses, "n": len(deltas), "mean_delta": mean(deltas)}


# ------------------------------------------------------------ answer records
def normalize_answer_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Project a raw study/baseline row onto the ``answer_details.jsonl`` shape.

    Accepts either a study ``result.json`` (``{"qid", "dataset", "requested_mode",
    "query", "outcome", "metrics", "result": {...}}``) or an already-normalized
    row. The normalized row keeps the full ``control`` so every evaluator can
    recompute from the same evidence.
    """
    if "result" in record and isinstance(record.get("result"), dict):
        result = record["result"]
        metrics = record.get("metrics") or {}
        control = result.get("control") or {}
        return {
            "qid": record.get("qid"),
            "dataset": record.get("dataset"),
            "mode": record.get("requested_mode") or (control.get("autonomy") or {}).get("mode"),
            "query": record.get("query") or result.get("query"),
            "answer": result.get("answer") or "",
            "outcome": record.get("outcome"),
            "llm_error": result.get("llm_error") or metrics.get("llm_error"),
            "control": control,
            "response_times": result.get("response_times") or {},
            "evidence_records": result.get("evidence_records") or [],
            "search_hits": result.get("search_hits") or [],
            "metrics": metrics,
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
            "review": record.get("review"),
            "human": record.get("human"),
            "run_id": record.get("run_id"),
        }
    row = dict(record)
    row.setdefault("control", {})
    row.setdefault("response_times", {})
    row.setdefault("evidence_records", [])
    row.setdefault("metrics", {})
    row.setdefault("answer", "")
    row.setdefault("mode", (row["control"].get("autonomy") or {}).get("mode"))
    return row


def load_study_reviews(round_dir: Path | str, *, version: str = "v3-evidence") -> Dict[str, Dict[str, Any]]:
    """``result_digest -> review`` for the blinded model-assisted reviews of a study round."""
    reviews: Dict[str, Dict[str, Any]] = {}
    directory = Path(round_dir) / f"reviews-{version}"
    if not directory.is_dir():
        return reviews
    for path in directory.glob("*.json"):
        review = read_json(path)
        if isinstance(review, dict) and review.get("result_digest") and review.get("judgment"):
            reviews[str(review["result_digest"])] = review
    return reviews


def iter_study_results(round_dir: Path | str, *, review_version: str = "v3-evidence") -> Iterator[Dict[str, Any]]:
    """Yield normalized rows from ``<round>/runs/*/result.json`` (autonomy study layout).

    Reviews bound to the result digest are attached as ``row["review"]`` so
    downstream evaluators can read a judged ``core_correct`` without re-judging.
    """
    runs = Path(round_dir) / "runs"
    if not runs.is_dir():
        return
    reviews = load_study_reviews(round_dir, version=review_version)
    for path in sorted(runs.glob("*/result.json")):
        blob = path.read_bytes()
        try:
            record = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        row = normalize_answer_record(record)
        row["run_id"] = path.parent.name
        row["source_path"] = str(path)
        row["result_digest"] = sha256_bytes(blob)
        row["review"] = reviews.get(row["result_digest"])
        yield row


def core_correct_of(row: Dict[str, Any]) -> Optional[int]:
    """Judged core correctness 0/1/2: human annotation first, then the model review."""
    human = row.get("human") if isinstance(row.get("human"), dict) else {}
    value = human.get("core_correct")
    if value is None:
        review = row.get("review") if isinstance(row.get("review"), dict) else {}
        judgment = review.get("judgment") if isinstance(review.get("judgment"), dict) else {}
        value = judgment.get("core_correct")
        if value is None:
            scores = judgment.get("scores") if isinstance(judgment.get("scores"), dict) else {}
            value = scores.get("core_correctness")
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed in (0, 1, 2) else None


DELIVERY_TEMPLATE_RE = re.compile(
    r"^(?:\s*(?:agent execution failed|search unavailable|迭代用尽|预算(?:已)?耗尽|抱歉，?(?:我)?无法|"
    r"i (?:will|'ll) (?:now )?(?:fetch|search|look|gather|retrieve)|我(?:将|会|先)(?:去)?(?:获取|搜索|查找|检索))"
    r"|\s*$)",
    re.IGNORECASE,
)


def delivered(row: Dict[str, Any]) -> bool:
    """Non-empty, non-error, non-template, non-plan answer (design D7 ``delivered``)."""
    if row.get("llm_error"):
        return False
    answer = str(row.get("answer") or "").strip()
    if len(answer) < 2:
        return False
    # Length is deliberately not a criterion: "木星 (Jupiter)" is a delivered answer.
    return not DELIVERY_TEMPLATE_RE.match(answer)


def load_answer_records(source: Path | str) -> List[Dict[str, Any]]:
    """Load answer rows from a run directory, a jsonl file or a study round."""
    source = Path(source)
    if source.is_file():
        return [normalize_answer_record(row) for row in read_jsonl(source)]
    if (source / "answer_details.jsonl").is_file():
        return [normalize_answer_record(row) for row in read_jsonl(source / "answer_details.jsonl")]
    if (source / "runs").is_dir():
        return list(iter_study_results(source))
    rows: List[Dict[str, Any]] = []
    for name in ("final_answer_details.jsonl", "open_task_details.jsonl", "route_intent_details.jsonl"):
        for row in read_jsonl(source / name):
            row = normalize_answer_record(row)
            row.setdefault("dataset", name.replace("_details.jsonl", ""))
            rows.append(row)
    return rows


def dataset_of(row: Dict[str, Any]) -> str:
    dataset = str(row.get("dataset") or "")
    if dataset:
        return dataset
    qid = str(row.get("qid") or "")
    if qid.startswith("final"):
        return "final_answer"
    if qid.startswith("open"):
        return "open_task"
    if qid.startswith("route"):
        return "route_intent"
    return "unknown"


def category_of(row: Dict[str, Any]) -> str:
    """Grouping key for macro averages: task_type / intent_label / dataset."""
    for key in ("category", "task_type", "intent_label"):
        value = row.get(key)
        if value:
            return str(value)
    return dataset_of(row)


def format_number(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)
