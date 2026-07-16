from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str


class SearchClient:
    """Abstract base class for search providers."""

    source_id: str = "generic"
    display_name: str = "Search"

    def __init__(self) -> None:
        self._last_timings: List[Dict[str, Any]] = []

    def _reset_timings(self) -> None:
        self._last_timings = []

    def _append_timing(self, payload: Dict[str, Any]) -> None:
        self._last_timings.append(payload)

    def get_last_timings(self) -> List[Dict[str, Any]]:
        return list(self._last_timings)

    def _is_valid_search_result(self, title: str, url: str, snippet: str) -> bool:
        """Filter out invalid or irrelevant search results."""
        # Skip if all fields are empty
        if not (title or url or snippet):
            return False
        
        # Common patterns for invalid search result pages
        invalid_patterns = [
            r"^search results?(\s+for:?|\s*\|)",  # "Search results for:", "Search results |"
            r"^untitled\s*$",  # "Untitled"
            r"^search\s*\|\s*",  # "Search | xxx"
            r"^sorry,?\s+(no|we)",  # "Sorry, no results", "Sorry, we"
            r"^no\s+results?\s+(found|available)",  # "No results found"
            r"^\d+\s+results?$",  # "10 results"
            r"^page\s+not\s+found",  # "Page not found"
            r"^error\s+\d+",  # "Error 404"
            r"^buletin$", # Specific garbage pattern observed
        ]
        
        title_lower = title.lower()
        for pattern in invalid_patterns:
            if re.match(pattern, title_lower, re.IGNORECASE):
                return False
        
        # Skip URLs that are clearly search engine pages
        invalid_url_patterns = [
            r"/search\?",  # Search query pages
            r"/results\?",  # Results pages
            r"google\.com/search",
            r"bing\.com/search",
            r"yahoo\.com/search",
        ]
        
        url_lower = url.lower()
        for pattern in invalid_url_patterns:
            if re.search(pattern, url_lower):
                return False
        
        return True

    def search(
        self,
        query: str,
        num_results: int = 5,
        *,
        per_source_limit: Optional[int] = None,
        freshness: Optional[str] = None,
        date_restrict: Optional[str] = None,
    ) -> List[SearchHit]:
        raise NotImplementedError


class _JsonPostSearchClient(SearchClient):
    """Shared HTTP and normalization logic for JSON POST search APIs."""

    max_results = 20

    def __init__(self, api_key: str, *, base_url: str, timeout: int = 20) -> None:
        super().__init__()
        if not api_key:
            raise ValueError(f"{self.display_name} API key is required.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = max(1, int(timeout))

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        query: str,
        limit: int,
        *,
        freshness: Optional[str],
        date_restrict: Optional[str],
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def _result_entries(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def _entry_snippet(self, entry: Dict[str, Any]) -> str:
        return str(entry.get("description") or entry.get("snippet") or "").strip()

    def search(
        self,
        query: str,
        num_results: int = 5,
        *,
        per_source_limit: Optional[int] = None,
        freshness: Optional[str] = None,
        date_restrict: Optional[str] = None,
    ) -> List[SearchHit]:
        self._reset_timings()
        start = time.perf_counter()
        error_message: Optional[str] = None
        try:
            limit = max(1, min(int(per_source_limit or num_results), self.max_results))
            request_payload = self._build_payload(
                query,
                limit,
                freshness=freshness,
                date_restrict=date_restrict,
            )
            try:
                response = requests.post(
                    self.base_url,
                    headers=self._headers(),
                    json=request_payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                error_message = str(exc)
                raise RuntimeError(f"{self.display_name} search failed: {exc}") from exc

            if not isinstance(payload, dict):
                raise RuntimeError(f"{self.display_name} returned an invalid JSON payload.")
            if payload.get("success") is False:
                message = payload.get("error") or payload.get("message") or "request failed"
                raise RuntimeError(f"{self.display_name} search failed: {message}")

            hits: List[SearchHit] = []
            seen_urls: Set[str] = set()
            for entry in self._result_entries(payload):
                if not isinstance(entry, dict):
                    continue
                metadata = entry.get("metadata") or {}
                url = str(
                    entry.get("url")
                    or (metadata.get("sourceURL") if isinstance(metadata, dict) else "")
                    or ""
                ).strip()
                title = str(
                    entry.get("title")
                    or (metadata.get("title") if isinstance(metadata, dict) else "")
                    or url
                ).strip()
                snippet = self._entry_snippet(entry) or title or url
                if not url or url in seen_urls:
                    continue
                if not self._is_valid_search_result(title, url, snippet):
                    continue
                hits.append(SearchHit(title=title, url=url, snippet=snippet))
                seen_urls.add(url)
                if len(hits) >= limit:
                    break
            return hits
        except Exception as exc:
            if error_message is None:
                error_message = str(exc)
            raise
        finally:
            timing: Dict[str, Any] = {
                "source": self.source_id,
                "label": self.display_name,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            }
            if error_message:
                timing["error"] = error_message
            self._append_timing(timing)


class FirecrawlSearchClient(_JsonPostSearchClient):
    """Firecrawl v2 web search client."""

    source_id = "firecrawl"
    display_name = "Firecrawl"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.firecrawl.dev/v2/search",
        timeout: int = 30,
    ) -> None:
        super().__init__(api_key, base_url=base_url, timeout=timeout)

    def _build_payload(
        self,
        query: str,
        limit: int,
        *,
        freshness: Optional[str],
        date_restrict: Optional[str],
    ) -> Dict[str, Any]:
        _ = (freshness, date_restrict)
        return {"query": query, "limit": limit, "sources": ["web"]}

    def _result_entries(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        data = payload.get("data") or {}
        entries = data.get("web") if isinstance(data, dict) else None
        return entries if isinstance(entries, list) else []

    def _entry_snippet(self, entry: Dict[str, Any]) -> str:
        return str(
            entry.get("description")
            or entry.get("markdown")
            or entry.get("snippet")
            or ""
        ).strip()


class TavilySearchClient(_JsonPostSearchClient):
    """Tavily web search client."""

    source_id = "tavily"
    display_name = "Tavily"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.tavily.com/search",
        timeout: int = 20,
        search_depth: str = "basic",
    ) -> None:
        super().__init__(api_key, base_url=base_url, timeout=timeout)
        allowed_depths = {"advanced", "basic", "fast", "ultra-fast"}
        normalized_depth = str(search_depth or "basic").strip().lower()
        self.search_depth = normalized_depth if normalized_depth in allowed_depths else "basic"

    @staticmethod
    def _time_range(value: Optional[str]) -> Optional[str]:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return None
        if normalized in {"day", "week", "month", "year"}:
            return normalized
        return {"d": "day", "w": "week", "m": "month", "y": "year"}.get(normalized[0])

    def _build_payload(
        self,
        query: str,
        limit: int,
        *,
        freshness: Optional[str],
        date_restrict: Optional[str],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "query": query,
            "max_results": limit,
            "search_depth": self.search_depth,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        time_range = self._time_range(freshness or date_restrict)
        if time_range:
            payload["time_range"] = time_range
        return payload

    def _result_entries(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        entries = payload.get("results")
        return entries if isinstance(entries, list) else []

    def _entry_snippet(self, entry: Dict[str, Any]) -> str:
        return str(
            entry.get("content")
            or entry.get("raw_content")
            or entry.get("description")
            or ""
        ).strip()


class ParallelSearchClient(_JsonPostSearchClient):
    """Parallel Search beta API client."""

    source_id = "parallel"
    display_name = "Parallel"
    max_results = 40

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.parallel.ai/v1beta/search",
        timeout: int = 30,
        mode: str = "fast",
        max_chars_per_result: int = 1500,
    ) -> None:
        super().__init__(api_key, base_url=base_url, timeout=timeout)
        normalized_mode = str(mode or "fast").strip().lower()
        self.mode = normalized_mode if normalized_mode in {"fast", "one-shot", "agentic"} else "fast"
        self.max_chars_per_result = max(200, int(max_chars_per_result))

    def _headers(self) -> Dict[str, str]:
        return {"x-api-key": self.api_key, "Content-Type": "application/json"}

    def _build_payload(
        self,
        query: str,
        limit: int,
        *,
        freshness: Optional[str],
        date_restrict: Optional[str],
    ) -> Dict[str, Any]:
        _ = (freshness, date_restrict)
        return {
            "objective": query,
            "search_queries": [query],
            "mode": self.mode,
            "max_results": limit,
            "excerpts": {"max_chars_per_result": self.max_chars_per_result},
        }

    def _result_entries(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        entries = payload.get("results")
        return entries if isinstance(entries, list) else []

    def _entry_snippet(self, entry: Dict[str, Any]) -> str:
        excerpts = entry.get("excerpts") or []
        if isinstance(excerpts, list):
            return " ".join(str(item).strip() for item in excerpts if str(item).strip())
        return str(excerpts or entry.get("description") or "").strip()


def _strip_html(raw_html: str) -> str:
    text = re.sub(r"<script.*?>.*?</script>", " ", raw_html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style.*?>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    return re.sub(r"\s+", " ", text).strip()


def _normalize_google_result_url(raw_url: str) -> str:
    candidate = (raw_url or "").strip()
    if not candidate:
        return ""
    if candidate.startswith("/url?"):
        parsed = urlparse(candidate)
        target = parse_qs(parsed.query).get("q", [""])[0]
        return unquote(target).strip()
    if candidate.startswith("http://") or candidate.startswith("https://"):
        return candidate
    return ""


class BrightDataSERPClient(SearchClient):
    """General web search provider using Bright Data SERP proxy."""

    source_id = "brightdata"
    display_name = "Bright Data SERP"

    def __init__(
        self,
        api_token: str,
        *,
        zone: str,
        base_url: str = "https://api.brightdata.com/request",
        timeout: int = 20,
        search_url_template: str = "https://www.google.com/search?q={query}",
    ) -> None:
        super().__init__()
        if not api_token:
            raise ValueError("Bright Data API token is required.")
        if not zone:
            raise ValueError("Bright Data zone is required.")
        self.api_token = api_token
        self.zone = zone
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.search_url_template = search_url_template

    def _build_target_url(self, query: str) -> str:
        return self.search_url_template.format(query=quote_plus(query))

    def _extract_response_text(self, response: requests.Response) -> str:
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "application/json" not in content_type:
            return response.text

        try:
            payload = response.json()
        except ValueError:
            return response.text

        if isinstance(payload, dict):
            for key in ("body", "content", "html", "raw"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            nested = payload.get("result")
            if isinstance(nested, str) and nested.strip():
                return nested

        return response.text

    def _extract_hits_from_payload(self, payload: Dict[str, Any], limit: int) -> List[SearchHit]:
        hits: List[SearchHit] = []
        seen_urls: Set[str] = set()

        organic = payload.get("organic")
        if not isinstance(organic, list):
            return hits

        for entry in organic:
            if not isinstance(entry, dict):
                continue

            url = str(entry.get("link") or entry.get("url") or "").strip()
            title = str(entry.get("title") or "").strip()
            snippet = str(entry.get("description") or entry.get("snippet") or "").strip()

            if not url or url in seen_urls:
                continue
            if not snippet:
                snippet = title or url
            if not self._is_valid_search_result(title, url, snippet):
                continue

            hits.append(SearchHit(title=title or url, url=url, snippet=snippet))
            seen_urls.add(url)
            if len(hits) >= limit:
                break

        return hits

    def _extract_hits_from_html(self, raw_html: str, limit: int) -> List[SearchHit]:
        hits: List[SearchHit] = []
        seen_urls: Set[str] = set()
        anchor_pattern = re.compile(r"<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
        for href, inner_html in anchor_pattern.findall(raw_html):
            url = _normalize_google_result_url(href)
            if not url or url in seen_urls:
                continue

            title_match = re.search(r"<h3[^>]*>(.*?)</h3>", inner_html, re.IGNORECASE | re.DOTALL)
            title = _strip_html(title_match.group(1) if title_match else inner_html)
            snippet = _strip_html(inner_html)
            if not snippet:
                snippet = title

            if not self._is_valid_search_result(title, url, snippet):
                continue

            hits.append(SearchHit(title=title or url, url=url, snippet=snippet or title or url))
            seen_urls.add(url)
            if len(hits) >= limit:
                break
        return hits

    def search(
        self,
        query: str,
        num_results: int = 5,
        *,
        per_source_limit: Optional[int] = None,
        freshness: Optional[str] = None,
        date_restrict: Optional[str] = None,
    ) -> List[SearchHit]:
        self._reset_timings()
        start = time.perf_counter()
        error_message: Optional[str] = None
        try:
            _ = (freshness, date_restrict)
            limit = max(1, int(per_source_limit or num_results))
            payload = {
                "zone": self.zone,
                "url": self._build_target_url(query),
                "format": "raw",
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_token}",
            }
            try:
                response = requests.post(
                    self.base_url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
                response.raise_for_status()
            except requests.RequestException as exc:
                error_message = str(exc)
                raise RuntimeError(f"Bright Data search failed: {exc}") from exc

            if "application/json" in (response.headers.get("Content-Type") or "").lower():
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    hits = self._extract_hits_from_payload(payload, limit)
                    if hits:
                        return hits

            html_text = self._extract_response_text(response)
            return self._extract_hits_from_html(html_text, limit)
        except Exception as exc:
            if error_message is None:
                error_message = str(exc)
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            timing_payload: Dict[str, Any] = {
                "source": self.source_id,
                "label": self.display_name,
                "duration_ms": round(duration_ms, 2),
            }
            if error_message:
                timing_payload["error"] = error_message
            self._append_timing(timing_payload)


class BraveUsageRecorder:
    """Append-only backend usage log for Brave Search."""

    def __init__(self, log_path: str) -> None:
        self.log_path = log_path
        self._lock = threading.Lock()

    def record(self, payload: Dict[str, Any]) -> None:
        directory = os.path.dirname(self.log_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")


class BraveSearchClient(SearchClient):
    """Brave Search provider with primary/secondary keys and usage recording."""

    source_id = "brave"
    display_name = "Brave Search"

    def __init__(
        self,
        primary_api_key: str,
        *,
        secondary_api_key: Optional[str] = None,
        base_url: str = "https://api.search.brave.com/res/v1/web/search",
        timeout: int = 15,
        rps: float = 1.0,
        secondary_rps: Optional[float] = None,
        monthly_limit: int = 2000,
        primary_switch_limit: int = 1500,
        usage_log_path: str = "runtime/brave_search_usage.jsonl",
    ) -> None:
        super().__init__()
        if not primary_api_key:
            raise ValueError("Brave Search primary API key is required.")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.rps = max(0.1, float(rps))
        self.secondary_rps = max(
            0.1,
            float(secondary_rps if secondary_rps is not None else self.rps),
        )
        self.monthly_limit = max(1, int(monthly_limit))
        self.primary_switch_limit = max(1, int(primary_switch_limit))
        self.usage_recorder = BraveUsageRecorder(usage_log_path)
        self.usage_log_path = usage_log_path
        self.slots: Dict[str, str] = {"primary": primary_api_key}
        if secondary_api_key:
            self.slots["secondary"] = secondary_api_key
        self._slot_rps = {"primary": self.rps, "secondary": self.secondary_rps}
        self._slot_locks = {slot: threading.Lock() for slot in self.slots}
        self._last_request_at = {slot: 0.0 for slot in self.slots}
        self._last_errors: List[Dict[str, str]] = []

    def _get_monthly_usage_count(self, slot: str) -> int:
        if not self.usage_log_path or not os.path.exists(self.usage_log_path):
            return 0

        month_prefix = time.strftime("%Y-%m", time.gmtime())
        count = 0
        try:
            with open(self.usage_log_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    if payload.get("provider") != self.source_id:
                        continue
                    if payload.get("slot") != slot:
                        continue
                    timestamp = str(payload.get("timestamp") or "")
                    if not timestamp.startswith(month_prefix):
                        continue
                    count += 1
        except OSError:
            return 0

        return count

    def _ordered_slots(self) -> List[str]:
        slots = list(self.slots.keys())
        if "primary" in self.slots and "secondary" in self.slots:
            primary_count = self._get_monthly_usage_count("primary")
            if primary_count >= self.primary_switch_limit:
                return ["secondary", "primary"]
        return slots

    def _respect_rps(self, slot: str) -> None:
        min_interval = 1.0 / self._slot_rps.get(slot, self.rps)
        lock = self._slot_locks[slot]
        with lock:
            elapsed = time.perf_counter() - self._last_request_at[slot]
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            self._last_request_at[slot] = time.perf_counter()

    def _record_usage(
        self,
        *,
        slot: str,
        query: str,
        success: bool,
        status_code: Optional[int],
        result_count: int,
        fallback_used: bool,
        error: Optional[str] = None,
    ) -> None:
        query_clean = (query or "").strip()
        payload: Dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "provider": self.source_id,
            "slot": slot,
            "success": success,
            "status_code": status_code,
            "result_count": result_count,
            "fallback_used": fallback_used,
            "query_preview": query_clean[:120],
            "query_hash": hashlib.sha256(query_clean.encode("utf-8")).hexdigest() if query_clean else None,
            "monthly_limit": self.monthly_limit,
        }
        if error:
            payload["error"] = error
        self.usage_recorder.record(payload)

    def _extract_hits(self, payload: Dict[str, Any], limit: int) -> List[SearchHit]:
        web = payload.get("web") if isinstance(payload, dict) else None
        results = web.get("results") if isinstance(web, dict) else None
        hits: List[SearchHit] = []
        if not isinstance(results, list):
            return hits

        for entry in results:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "").strip()
            url = str(entry.get("url") or "").strip()
            snippet = str(entry.get("description") or entry.get("snippet") or "").strip()
            if not (title or url or snippet):
                continue
            if self._is_valid_search_result(title, url, snippet):
                hits.append(SearchHit(title=title or url, url=url, snippet=snippet or title or url))
            if len(hits) >= limit:
                break
        return hits

    def search(
        self,
        query: str,
        num_results: int = 5,
        *,
        per_source_limit: Optional[int] = None,
        freshness: Optional[str] = None,
        date_restrict: Optional[str] = None,
    ) -> List[SearchHit]:
        self._reset_timings()
        self._last_errors = []
        limit = max(1, min(int(per_source_limit or num_results), 20))
        _ = (freshness, date_restrict)

        for slot in self._ordered_slots():
            api_key = self.slots[slot]
            start = time.perf_counter()
            status_code: Optional[int] = None
            error_message: Optional[str] = None
            try:
                self._respect_rps(slot)
                headers = {
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                }
                params = {
                    "q": query,
                    "count": limit,
                }
                response = requests.get(self.base_url, params=params, headers=headers, timeout=self.timeout)
                status_code = response.status_code
                response.raise_for_status()
                payload = response.json()
                hits = self._extract_hits(payload, limit)
                self._record_usage(
                    slot=slot,
                    query=query,
                    success=True,
                    status_code=status_code,
                    result_count=len(hits),
                    fallback_used=slot != "primary",
                )
                timing_payload: Dict[str, Any] = {
                    "source": self.source_id,
                    "label": f"{self.display_name} ({slot})",
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                    "slot": slot,
                }
                if slot != "primary":
                    timing_payload["fallback"] = True
                self._append_timing(timing_payload)
                return hits
            except Exception as exc:
                error_message = str(exc)
                self._last_errors.append({"source": f"{self.display_name} ({slot})", "error": error_message})
                self._record_usage(
                    slot=slot,
                    query=query,
                    success=False,
                    status_code=status_code,
                    result_count=0,
                    fallback_used=slot != "primary",
                    error=error_message,
                )
                timing_payload = {
                    "source": self.source_id,
                    "label": f"{self.display_name} ({slot})",
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                    "slot": slot,
                    "error": error_message,
                }
                if slot != "primary":
                    timing_payload["fallback"] = True
                self._append_timing(timing_payload)
                continue

        return []

    def get_last_errors(self) -> List[Dict[str, str]]:
        return list(self._last_errors)


class FallbackSearchClient(SearchClient):
    """Fallback client for environments without a search API key."""

    def __init__(self, static_results: Optional[List[SearchHit]] = None) -> None:
        super().__init__()
        self.static_results = static_results or []

    def search(
        self,
        query: str,
        num_results: int = 5,
        *,
        per_source_limit: Optional[int] = None,
        freshness: Optional[str] = None,
        date_restrict: Optional[str] = None,
    ) -> List[SearchHit]:
        self._reset_timings()
        start = time.perf_counter()
        error_message: Optional[str] = None
        try:
            if not self.static_results:
                raise RuntimeError("No search backend configured. Provide a search API key or static results.")
            limit = max(1, int(per_source_limit or num_results))
            return self.static_results[:limit]
        except Exception as exc:
            error_message = str(exc)
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            payload = {
                "source": self.source_id,
                "label": self.display_name,
                "duration_ms": round(duration_ms, 2),
            }
            if error_message:
                payload["error"] = error_message
            self._append_timing(payload)





class GoogleSearchClient(SearchClient):
    """Client for Google Custom Search JSON API."""

    source_id = "google"
    display_name = "Google Search"

    def __init__(
        self,
        api_key: str,
        cx: str,
        *,
        base_url: str = "https://www.googleapis.com/customsearch/v1",
        timeout: int = 15,
        gl: Optional[str] = None,
        lr: Optional[str] = None,
        safe: Optional[str] = "medium",
        date_restrict: Optional[str] = None,
    ) -> None:
        super().__init__()
        if not api_key:
            raise ValueError("Google API key is required.")
        if not cx:
            raise ValueError("Google Custom Search Engine ID (cx) is required.")
        self.api_key = api_key
        self.cx = cx
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.gl = (gl or "").strip() or None  # Geolocation
        self.lr = (lr or "").strip() or None  # Language restrict
        self.safe = (safe or "").strip().lower() or None  # Safe search
        self.date_restrict = (date_restrict or "").strip() or None  # Time range filter (e.g., d3, w1, m2, y1)



    def search(
        self,
        query: str,
        num_results: int = 5,
        *,
        per_source_limit: Optional[int] = None,
        freshness: Optional[str] = None,
        date_restrict: Optional[str] = None,
    ) -> List[SearchHit]:
        self._reset_timings()
        start = time.perf_counter()
        error_message: Optional[str] = None
        try:
            limit = max(1, min(int(per_source_limit or num_results), 10))  # Google API max is 10 per request
            params = {
                "key": self.api_key,
                "cx": self.cx,
                "q": query,
                "num": limit,
            }
            if self.gl:
                params["gl"] = self.gl
            if self.lr:
                params["lr"] = self.lr
            if self.safe:
                params["safe"] = self.safe
            # Allow callers to specify freshness (e.g., "d1", "w1"); map to dateRestrict if unset
            effective_freshness = (freshness or "").strip() or None
            if effective_freshness:
                params.setdefault("dateRestrict", effective_freshness)
            # Use runtime date_restrict if provided, otherwise use instance default
            effective_date_restrict = date_restrict or self.date_restrict
            if effective_date_restrict:
                params["dateRestrict"] = effective_date_restrict

            try:
                response = requests.get(self.base_url, params=params, timeout=self.timeout)
                response.raise_for_status()
            except requests.RequestException as exc:
                error_message = str(exc)
                raise RuntimeError(f"Google Search failed: {exc}") from exc

            try:
                payload = response.json()
            except ValueError as exc:
                error_message = str(exc)
                raise RuntimeError("Google Search returned non-JSON payload") from exc

            items = payload.get("items") or []
            hits: List[SearchHit] = []
            for entry in items[:limit]:
                if not isinstance(entry, dict):
                    continue
                title = (entry.get("title") or "").strip()
                link = (entry.get("link") or "").strip()
                snippet = (entry.get("snippet") or "").strip()

                # Filter out invalid search results
                if self._is_valid_search_result(title, link, snippet):
                    hits.append(
                        SearchHit(
                            title=title,
                            url=link,
                            snippet=snippet,
                        )
                    )
            return hits
        except Exception as exc:
            if error_message is None:
                error_message = str(exc)
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            payload = {
                "source": self.source_id,
                "label": self.display_name,
                "duration_ms": round(duration_ms, 2),
            }
            if error_message:
                payload["error"] = error_message
            self._append_timing(payload)


class CombinedSearchClient(SearchClient):
    """Fan out to multiple search clients concurrently and merge unique hits."""

    def __init__(self, clients: List[SearchClient]) -> None:
        super().__init__()
        if not clients:
            raise ValueError("At least one search client is required.")
        self.clients = clients
        self._last_errors: List[Dict[str, str]] = []
        self.active_sources: List[str] = [getattr(client, "source_id", type(client).__name__.lower()) for client in clients]
        self.active_source_labels: List[str] = [getattr(client, "display_name", type(client).__name__) for client in clients]
        self.requested_sources: List[str] = []
        self.missing_requested_sources: List[str] = []
        self.configured_sources: List[str] = sorted(set(self.active_sources))

    def _label_for_client(self, client: SearchClient) -> str:
        name = getattr(client, "display_name", None)
        return name or type(client).__name__

    def search(
        self,
        query: str,
        num_results: int = 5,
        *,
        per_source_limit: Optional[int] = None,
        freshness: Optional[str] = None,
        date_restrict: Optional[str] = None,
    ) -> List[SearchHit]:
        self._reset_timings()
        hits: List[SearchHit] = []
        self._last_errors = []

        total_limit = max(1, int(num_results))
        per_source = max(1, int(per_source_limit or total_limit))

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.clients)) as pool:
            future_map: Dict[concurrent.futures.Future, SearchClient] = {}
            start_times: Dict[concurrent.futures.Future, float] = {}
            for client in self.clients:
                future = pool.submit(
                    client.search,
                    query,
                    num_results=per_source,
                    per_source_limit=per_source,
                    freshness=freshness,
                    date_restrict=date_restrict,
                )
                future_map[future] = client
                start_times[future] = time.perf_counter()

            for future in concurrent.futures.as_completed(future_map):
                client = future_map[future]
                label = self._label_for_client(client)
                duration_ms = round((time.perf_counter() - start_times.get(future, 0.0)) * 1000, 2)
                timing_payload = {
                    "source": getattr(client, "source_id", type(client).__name__.lower()),
                    "label": label,
                    "duration_ms": duration_ms,
                }
                try:
                    chunk = future.result() or []
                except Exception as exc:
                    self._last_errors.append({"source": label, "error": str(exc)})
                    timing_payload["error"] = str(exc)
                    self._append_timing(timing_payload)
                    continue

                self._append_timing(timing_payload)
                hits.extend(chunk)

        deduped: List[SearchHit] = []
        seen: Set[str] = set()
        for hit in hits:
            key = (hit.url or f"{hit.title}|{hit.snippet}").strip().lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(hit)
            if len(deduped) >= total_limit:
                break

        # 如果查询包含时间变化相关关键词，应用时间变化领域的特殊过滤和排序
        if self._is_temporal_change_query(query):
            deduped = self._filter_and_rank_temporal_change_results(deduped, query)

        return deduped

    def get_last_errors(self) -> List[Dict[str, str]]:
        return list(self._last_errors)
    
    def _is_temporal_change_query(self, query: str) -> bool:
        """检测查询是否与时间变化领域相关"""
        temporal_change_keywords = [
            # 教育排名相关
            "大学", "高校", "学院", "学校", "排名", "QS", "THE", "ARWU", "US News",
            "university", "college", "ranking", "rankings", "education", "higher education",
            "香港中文大學", "香港科技大學", "香港大學", "CUHK", "HKUST", "HKU",
            "香港中文大学", "香港科技大学", "香港大学",
            # 时间变化相关
            "最近10年", "过去10年", "10年", "十年", "历年", "历史", "变化", "趋势", "发展",
            "10 years", "decade", "historical", "trend", "development", "evolution",
            "对比", "比较", "变化趋势", "时间序列", "年度", "逐年",
            "comparison", "compare", "trend over time", "time series", "yearly", "year by year",
            # 其他可能的时间变化查询
            "增长", "下降", "波动", "变化率", "增长率", "涨跌",
            "growth", "decline", "fluctuation", "rate of change", "growth rate", "rise and fall"
        ]
        query_lower = query.lower()
        return any(keyword in query_lower for keyword in temporal_change_keywords)
    
    def _filter_and_rank_temporal_change_results(self, results: List[SearchHit], query: str) -> List[SearchHit]:
        """针对时间变化领域的搜索结果进行过滤和排序"""
        if not results:
            return results
        
        # 定义权威教育网站域名
        authoritative_domains = [
            "topuniversities.com",  # QS排名官网
            "timeshighereducation.com",  # THE排名官网
            "shanghairanking.com",  # ARWU排名官网
            "usnews.com",  # US News排名官网
            "cuHK.edu.hk",  # 香港中文大学官网
            "hkust.edu.hk",  # 香港科技大学官网
            "hku.hk",  # 香港大学官网
            "edu.hk",  # 香港教育机构域名
            "edu.cn",  # 中国教育机构域名
            "gov.hk",  # 香港政府网站
            "gov.cn",  # 中国政府网站
            "wikipedia.org",  # 维基百科
            "britannica.com"  # 大英百科全书
        ]
        
        # 定义高质量排名相关关键词
        ranking_keywords = [
            "qs world university rankings",
            "the world university rankings",
            "arwu academic ranking",
            "world university rankings",
            "global university rankings",
            "qs排名",
            "the排名",
            "arwu排名",
            "世界大学排名",
            "大学排名对比",
            "university ranking comparison"
        ]
        
        # 计算每个搜索结果的分数
        scored_results = []
        for result in results:
            score = 0
            title_lower = result.title.lower()
            snippet_lower = result.snippet.lower()
            url_lower = result.url.lower()
            
            # 1. 权威域名加分
            for domain in authoritative_domains:
                if domain in url_lower:
                    score += 10
                    break
            
            # 2. 排名关键词匹配加分
            for keyword in ranking_keywords:
                if keyword in title_lower:
                    score += 5
                if keyword in snippet_lower:
                    score += 3
            
            # 3. 查询中的特定大学名称匹配加分
            query_lower = query.lower()
            university_names = [
                "香港中文大學", "香港科技大學", "香港大學", "CUHK", "HKUST", "HKU",
                "香港中文大学", "香港科技大学", "香港大学"
            ]
            for uni in university_names:
                if uni in query_lower and uni in title_lower:
                    score += 8
                if uni in query_lower and uni in snippet_lower:
                    score += 5
            
            # 4. 时间相关性加分（最近的内容优先）
            recent_keywords = ["2024", "2023", "2025", "最新", "latest", "recent"]
            for keyword in recent_keywords:
                if keyword in title_lower or keyword in snippet_lower:
                    score += 2
                    break
            
            # 5. 数据丰富度加分（包含表格、图表等）
            data_keywords = ["table", "chart", "graph", "表格", "图表", "数据", "data"]
            for keyword in data_keywords:
                if keyword in snippet_lower:
                    score += 1
                    break
            
            scored_results.append((result, score))
        
        # 按分数降序排序
        scored_results.sort(key=lambda x: x[1], reverse=True)
        
        # 返回排序后的结果
        return [result for result, _ in scored_results]


class PrioritySearchClient(SearchClient):
    """Try providers in order and fall back only when earlier providers fail or return no hits."""

    source_id = "priority"
    display_name = "Priority Search"

    def __init__(self, clients: List[SearchClient]) -> None:
        super().__init__()
        if not clients:
            raise ValueError("At least one search client is required.")
        self.clients = clients
        self._last_errors: List[Dict[str, str]] = []
        self.active_sources: List[str] = [
            getattr(client, "source_id", type(client).__name__.lower()) for client in clients
        ]
        self.active_source_labels: List[str] = [
            getattr(client, "display_name", type(client).__name__) for client in clients
        ]
        self.requested_sources: List[str] = []
        self.missing_requested_sources: List[str] = []
        self.configured_sources: List[str] = list(self.active_sources)

    def search(
        self,
        query: str,
        num_results: int = 5,
        *,
        per_source_limit: Optional[int] = None,
        freshness: Optional[str] = None,
        date_restrict: Optional[str] = None,
    ) -> List[SearchHit]:
        self._reset_timings()
        self._last_errors = []
        total_limit = max(1, int(num_results))
        per_source = max(1, int(per_source_limit or total_limit))

        for client in self.clients:
            try:
                chunk = client.search(
                    query,
                    num_results=total_limit,
                    per_source_limit=per_source,
                    freshness=freshness,
                    date_restrict=date_restrict,
                ) or []
            except Exception as exc:
                self._last_errors.append(
                    {"source": getattr(client, "display_name", type(client).__name__), "error": str(exc)}
                )
                timings_getter = getattr(client, "get_last_timings", None)
                if callable(timings_getter):
                    self._last_timings.extend(timings_getter())
                continue

            timings_getter = getattr(client, "get_last_timings", None)
            if callable(timings_getter):
                self._last_timings.extend(timings_getter())

            error_getter = getattr(client, "get_last_errors", None)
            if callable(error_getter):
                self._last_errors.extend(error_getter() or [])

            deduped: List[SearchHit] = []
            seen: Set[str] = set()
            for hit in chunk:
                key = (hit.url or f"{hit.title}|{hit.snippet}").strip().lower()
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(hit)
                if len(deduped) >= total_limit:
                    break
            if deduped:
                return deduped

        return []

    def get_last_errors(self) -> List[Dict[str, str]]:
        return list(self._last_errors)


class YouSearchClient(SearchClient):
    """Client for the You.com Search API."""

    source_id = "you"
    display_name = "You.com Search"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.ydc-index.io/v1/search",
        contents_base_url: str = "https://api.ydc-index.io/v1/contents",
        timeout: int = 15,
        country: Optional[str] = None,
        safesearch: Optional[str] = "moderate",
        freshness: Optional[str] = None,
        include_news: bool = True,
        default_count: int = 10,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__()
        if not api_key:
            raise ValueError("You.com API key is required.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.contents_base_url = contents_base_url.rstrip("/")
        self.timeout = timeout
        self.country = (country or "").strip().upper() or None
        self.safesearch = (safesearch or "").strip().lower() or None
        self.freshness = (freshness or "").strip().lower() or None
        self.include_news = include_news
        self.default_count = max(1, min(default_count, 100))
        self.extra_params = extra_params or {}

    def fetch_contents(
        self,
        urls: List[str],
        *,
        content_format: str = "markdown",
        crawl_timeout: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        cleaned_urls = [str(url).strip() for url in urls if str(url).strip()]
        if not cleaned_urls:
            raise ValueError("At least one URL is required for You.com contents fetch.")

        payload: Dict[str, Any] = {
            "urls": cleaned_urls,
            "format": content_format,
        }
        if crawl_timeout is not None:
            payload["crawl_timeout"] = int(crawl_timeout)

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
        }
        try:
            response = requests.post(
                self.contents_base_url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"You.com contents fetch failed: {exc}") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("You.com contents API returned non-JSON payload") from exc

        if not isinstance(body, list):
            raise RuntimeError("You.com contents API returned an unexpected payload shape")
        return body

    def _build_params(self, query: str, num_results: int) -> Dict[str, Any]:
        count_value = max(1, min(max(num_results, self.default_count), 100))
        params: Dict[str, Any] = {"query": query, "count": count_value}
        if self.country:
            params["country"] = self.country
        if self.safesearch:
            params["safesearch"] = self.safesearch
        if self.freshness:
            params["freshness"] = self.freshness
        params.update(self.extra_params)
        return params

    def _extract_section(self, records: Any, limit: int) -> List[SearchHit]:
        hits: List[SearchHit] = []
        if not isinstance(records, list):
            return hits

        for entry in records:
            if not isinstance(entry, dict):
                continue
            title = (entry.get("title") or entry.get("name") or "").strip()
            url = (entry.get("url") or "").strip()
            snippets = entry.get("snippets") or []
            snippet_text = ""
            if isinstance(snippets, list) and snippets:
                snippet_parts = [str(part).strip() for part in snippets if part]
                snippet_text = " ".join(part for part in snippet_parts if part)
            if not snippet_text:
                snippet_text = str(entry.get("description") or "").strip()
            if not snippet_text and entry.get("content"):
                snippet_text = str(entry.get("content")).strip()

            if not (title or url or snippet_text):
                continue

            if self._is_valid_search_result(title, url, snippet_text):
                hits.append(SearchHit(title=title, url=url, snippet=snippet_text))
            if len(hits) >= limit:
                break

        return hits

    def search(
        self,
        query: str,
        num_results: int = 5,
        *,
        per_source_limit: Optional[int] = None,
        freshness: Optional[str] = None,
        date_restrict: Optional[str] = None,
    ) -> List[SearchHit]:
        self._reset_timings()
        start = time.perf_counter()
        error_message: Optional[str] = None
        try:
            effective_limit = max(1, int(per_source_limit or num_results))
            params = self._build_params(query, effective_limit)

            # Use runtime freshness if provided, otherwise use instance default
            effective_freshness = freshness or self.freshness
            if effective_freshness:
                params["freshness"] = effective_freshness
            # You.com API currently does not expose a direct date filter; accept the kwarg for interface parity.
            _ = date_restrict

            headers = {"X-API-Key": self.api_key}

            try:
                response = requests.get(self.base_url, params=params, headers=headers, timeout=self.timeout)
                response.raise_for_status()
            except requests.RequestException as exc:
                error_message = str(exc)
                raise RuntimeError(f"You.com search failed: {exc}") from exc

            try:
                payload = response.json()
            except ValueError as exc:
                error_message = str(exc)
                raise RuntimeError("You.com search returned non-JSON payload") from exc

            results = payload.get("results") if isinstance(payload, dict) else None
            web_hits = self._extract_section((results or {}).get("web"), effective_limit)
            if len(web_hits) >= effective_limit or not self.include_news:
                return web_hits[:effective_limit]

            remaining = effective_limit - len(web_hits)
            news_hits = self._extract_section((results or {}).get("news"), remaining)
            combined = web_hits + news_hits
            return combined[:effective_limit]
        except Exception as exc:
            if error_message is None:
                error_message = str(exc)
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            payload = {
                "source": self.source_id,
                "label": self.display_name,
                "duration_ms": round(duration_ms, 2),
            }
            if error_message:
                payload["error"] = error_message
            self._append_timing(payload)
