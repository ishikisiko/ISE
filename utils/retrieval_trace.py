"""Safe, provider-level retrieval audit records for workflow tracing."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from utils.workflow_trace import safe_trace_records, safe_trace_text


def _duration_ms(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def search_call_snapshot(call: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize one concrete provider search call for SSE and audit consumers."""
    raw_results = call.get("results")
    if not isinstance(raw_results, Sequence) or isinstance(raw_results, (str, bytes)):
        raw_results = call.get("records")
    records: List[Dict[str, Any]] = []
    if isinstance(raw_results, Sequence) and not isinstance(raw_results, (str, bytes)):
        for raw in raw_results:
            if not isinstance(raw, Mapping):
                continue
            records.append(
                {
                    "title": raw.get("title"),
                    "url": raw.get("url"),
                    "snippet": raw.get("snippet"),
                    "provider": call.get("source") or call.get("provider") or call.get("label"),
                    "status": call.get("status") or "done",
                }
            )

    result_count = call.get("result_count")
    if isinstance(result_count, bool):
        result_count = None
    try:
        count = max(0, int(result_count)) if result_count is not None else len(records)
    except (TypeError, ValueError):
        count = len(records)

    snapshot: Dict[str, Any] = {
        "provider": safe_trace_text(
            call.get("source") or call.get("provider") or call.get("label") or "search",
            limit=100,
        ),
        "label": safe_trace_text(
            call.get("label") or call.get("source") or call.get("provider") or "Search",
            limit=120,
        ),
        "query": safe_trace_text(call.get("query"), limit=220),
        "status": "error" if str(call.get("status") or "").lower() == "error" else "done",
        "result_count": count,
        "records": safe_trace_records(records),
    }
    duration_ms = _duration_ms(call.get("duration_ms"))
    if duration_ms is not None:
        snapshot["duration_ms"] = duration_ms
    if call.get("fallback"):
        snapshot["fallback"] = True
    if call.get("slot"):
        snapshot["slot"] = safe_trace_text(call.get("slot"), limit=40)
    if call.get("error"):
        snapshot["reason"] = safe_trace_text(call.get("error"), limit=180)
        snapshot["status"] = "error"
    return snapshot


def search_call_snapshots(calls: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize a bounded sequence of concrete search calls."""
    snapshots: List[Dict[str, Any]] = []
    for call in list(calls)[:20]:
        if isinstance(call, Mapping):
            snapshots.append(search_call_snapshot(call))
    return snapshots


def emit_search_call_step(
    tracer: Any,
    snapshot: Mapping[str, Any],
    *,
    step_id: str,
) -> None:
    """Emit one workflow node for one actual provider request."""
    label = safe_trace_text(snapshot.get("label") or snapshot.get("provider") or "Search", limit=120)
    query = safe_trace_text(snapshot.get("query"), limit=220)
    title = f"搜索 API：{label}"
    tracer.begin(step_id, title, detail=f"查询：{query}" if query else "正在调用搜索 API")

    status = "error" if snapshot.get("status") == "error" else "done"
    try:
        result_count = max(0, int(snapshot.get("result_count") or 0))
    except (TypeError, ValueError):
        result_count = 0
    items: List[Dict[str, str]] = [
        {"label": "提供方", "value": safe_trace_text(snapshot.get("provider") or label, limit=100)},
        {"label": "结果", "value": f"{result_count} 条"},
    ]
    duration_ms = _duration_ms(snapshot.get("duration_ms"))
    if duration_ms is not None:
        items.append({"label": "耗时", "value": f"{duration_ms:.0f} ms"})
    if snapshot.get("fallback"):
        items.append({"label": "路径", "value": "回退"})
    if status == "error" and snapshot.get("reason"):
        items.append({"label": "原因", "value": safe_trace_text(snapshot.get("reason"), limit=180)})

    detail = "调用失败" if status == "error" else f"返回 {result_count} 条结果"
    tracer.end(
        step_id,
        detail=detail,
        items=items,
        status=status,
        records=snapshot.get("records") if isinstance(snapshot.get("records"), list) else [],
        record_kind="search_results",
        record_label=f"搜索结果 · {result_count}",
    )


def extraction_trace_records(extraction: Any) -> List[Dict[str, Any]]:
    """Project normalized selected-page extraction output without page content."""
    request_id = getattr(extraction, "request_id", None)
    records: List[Dict[str, Any]] = []
    for item in list(getattr(extraction, "contents", []) or []):
        records.append(
            {
                "title": getattr(item, "title", ""),
                "url": getattr(item, "url", "") or getattr(item, "requested_url", ""),
                "provider": getattr(item, "provider", "") or getattr(extraction, "provider", ""),
                "status": "done",
                "content_chars": len(str(getattr(item, "content", "") or "")),
                "request_id": request_id,
            }
        )
    for item in list(getattr(extraction, "failures", []) or []):
        records.append(
            {
                "url": getattr(item, "requested_url", ""),
                "provider": getattr(item, "provider", "") or getattr(extraction, "provider", ""),
                "status": "error",
                "reason": getattr(item, "error_type", "") or "provider_error",
                "request_id": request_id,
            }
        )
    return safe_trace_records(records)


def emit_extraction_call_step(
    tracer: Any,
    extraction: Any,
    *,
    step_id: str,
    duration_ms: Optional[float] = None,
) -> None:
    """Emit one workflow node for a selected-page extraction provider call."""
    provider = safe_trace_text(getattr(extraction, "provider", "") or "reference", limit=100)
    records = extraction_trace_records(extraction)
    success_count = sum(1 for record in records if record.get("status") != "error")
    title = f"网页抽取：{provider}"
    tracer.begin(step_id, title, detail="正在读取已选择网页")
    status = "done" if success_count else "error"
    items = [
        {"label": "提供方", "value": provider},
        {"label": "网页", "value": f"{len(records)} 个"},
    ]
    if duration_ms is not None:
        items.append({"label": "耗时", "value": f"{duration_ms:.0f} ms"})
    tracer.end(
        step_id,
        detail=f"已抽取 {success_count} 个网页" if success_count else "抽取未返回内容",
        items=items,
        status=status,
        records=records,
        record_kind="extracted_pages",
        record_label=f"已抽取网页 · {len(records)}",
    )
