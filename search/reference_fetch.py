"""Selected-URL extraction adapters for external evidence providers."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urljoin, urlparse, urlunparse

import requests

from bs4 import BeautifulSoup
from utils.config_validation import configured_value


PARALLEL_EXTRACT_URL = "https://api.parallel.ai/v1/extract"
FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"

# Realistic desktop UA so plain-HTTP documentation/pricing pages do not gate
# the primary direct fetch behind a bot check. Mirrors the same approach used
# by the opencode webfetch-style "direct fetch" path.
_DEFAULT_DIRECT_FETCH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_MAX_DIRECT_FETCH_REDIRECTS = 5


@dataclass
class ReferenceContent:
    """Normalized content returned for an explicitly requested public URL."""

    provider: str
    requested_url: str
    url: str
    title: str = ""
    content: str = ""
    excerpts: List[str] = field(default_factory=list)
    published_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReferenceFailure:
    """Bounded, safe failure detail for one requested URL."""

    provider: str
    requested_url: str
    error_type: str
    status_code: Optional[int] = None


@dataclass
class ReferenceExtraction:
    """Provider response projected into stable content and failure records."""

    provider: str
    contents: List[ReferenceContent] = field(default_factory=list)
    failures: List[ReferenceFailure] = field(default_factory=list)
    request_id: Optional[str] = None
    attempts: List[Dict[str, Any]] = field(default_factory=list)

    def trace_records(self) -> List[Dict[str, Any]]:
        """Return page-level audit facts without serializing extracted content."""
        from utils.retrieval_trace import extraction_trace_records

        return extraction_trace_records(self)


def _normalize_urls(urls: Sequence[str] | str, *, max_urls: int) -> List[str]:
    candidates = [urls] if isinstance(urls, str) else list(urls)
    normalized: List[str] = []
    seen = set()
    for raw_url in candidates:
        if not isinstance(raw_url, str):
            raise ValueError("Reference URLs must be strings.")
        url = raw_url.strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Reference URLs must use an absolute http(s) URL.")
        if parsed.username or parsed.password:
            raise ValueError("Reference URLs must not contain credentials.")
        hostname = (parsed.hostname or "").rstrip(".").lower()
        if not hostname:
            raise ValueError("Reference URLs must include a hostname.")
        if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
            raise ValueError("Reference URLs must target a public hostname.")
        try:
            if not ip_address(hostname).is_global:
                raise ValueError("Reference URLs must not target a non-public IP address.")
        except ValueError as error:
            if str(error).startswith("Reference URLs must"):
                raise
            # Numeric-only and hexadecimal host forms can be interpreted as local IPs.
            if hostname.isdigit() or hostname.startswith(("0x", "0X")):
                raise ValueError("Reference URLs must target a public hostname.") from None
        if url in seen:
            continue
        seen.add(url)
        normalized.append(url)

    if not normalized:
        raise ValueError("At least one reference URL is required.")
    if len(normalized) > max_urls:
        raise ValueError(f"At most {max_urls} reference URLs are allowed per request.")
    return normalized


def _safe_reference_url(value: Any) -> str:
    """Retain a public reference identity without query or credential data."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw.split("?", 1)[0].split("#", 1)[0][:2048]
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, "", "")
    )[:2048]


def _coerce_positive_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None:
        return default
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized > 0 else default


def _coerce_nonnegative_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None:
        return default
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized >= 0 else default


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


def _safe_optional_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


class ReferenceExtractor(ABC):
    """Common contract for provider-managed selected-URL extraction."""

    source_id = "reference"
    display_name = "Reference Extractor"

    def __init__(self, api_key: str, *, base_url: str, timeout: int) -> None:
        api_key = configured_value(api_key)
        if not api_key:
            raise ValueError(f"{self.display_name} API key is required.")
        self.api_key = api_key
        self.base_url = str(base_url or "").strip().rstrip("/")
        if not self.base_url:
            raise ValueError(f"{self.display_name} endpoint is required.")
        self.timeout = max(1, int(timeout))
        self._last_timings: List[Dict[str, Any]] = []

    def _record_timing(
        self,
        started_at: float,
        *,
        contents: int,
        failures: int,
        error_type: Optional[str] = None,
    ) -> None:
        timing: Dict[str, Any] = {
            "source": self.source_id,
            "label": self.display_name,
            "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "content_count": contents,
            "failure_count": failures,
        }
        if error_type:
            timing["error"] = error_type
        self._last_timings.append(timing)

    def get_last_timings(self) -> List[Dict[str, Any]]:
        return list(self._last_timings)

    @abstractmethod
    def extract(
        self,
        urls: Sequence[str] | str,
        *,
        objective: Optional[str] = None,
    ) -> ReferenceExtraction:
        raise NotImplementedError


class ParallelExtractClient(ReferenceExtractor):
    """Adapter for Parallel's selected URL Extract API."""

    source_id = "parallel_extract"
    display_name = "Parallel Extract"
    max_urls = 20

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = PARALLEL_EXTRACT_URL,
        timeout: int = 45,
        full_content: bool = False,
        max_chars_total: Optional[int] = None,
        max_age_seconds: Optional[int] = None,
    ) -> None:
        super().__init__(api_key, base_url=base_url, timeout=timeout)
        self.full_content = bool(full_content)
        self.max_chars_total = _coerce_positive_int(max_chars_total)
        self.max_age_seconds = _coerce_positive_int(max_age_seconds)

    def extract(
        self,
        urls: Sequence[str] | str,
        *,
        objective: Optional[str] = None,
    ) -> ReferenceExtraction:
        normalized_urls = _normalize_urls(urls, max_urls=self.max_urls)
        self._last_timings = []
        started_at = time.perf_counter()
        payload: Dict[str, Any] = {"urls": normalized_urls}
        normalized_objective = _safe_optional_text(objective)
        if normalized_objective:
            payload["objective"] = normalized_objective
        if self.max_chars_total is not None:
            payload["max_chars_total"] = self.max_chars_total

        advanced_settings: Dict[str, Any] = {}
        if self.full_content:
            advanced_settings["full_content"] = True
        if self.max_age_seconds is not None:
            advanced_settings["fetch_policy"] = {
                "max_age_seconds": max(600, self.max_age_seconds),
            }
        if advanced_settings:
            payload["advanced_settings"] = advanced_settings

        try:
            response = requests.post(
                self.base_url,
                headers={
                    "x-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError):
            extraction = ReferenceExtraction(
                provider=self.source_id,
                failures=[
                    ReferenceFailure(
                        provider=self.source_id,
                        requested_url=url,
                        error_type="request_failed",
                    )
                    for url in normalized_urls
                ],
            )
            self._record_timing(
                started_at,
                contents=0,
                failures=len(extraction.failures),
                error_type="request_failed",
            )
            return extraction

        if not isinstance(body, dict):
            extraction = ReferenceExtraction(
                provider=self.source_id,
                failures=[
                    ReferenceFailure(
                        provider=self.source_id,
                        requested_url=url,
                        error_type="invalid_response",
                    )
                    for url in normalized_urls
                ],
            )
            self._record_timing(
                started_at,
                contents=0,
                failures=len(extraction.failures),
                error_type="invalid_response",
            )
            return extraction

        extraction = ReferenceExtraction(
            provider=self.source_id,
            request_id=_safe_optional_text(body.get("extract_id")),
        )
        for entry in body.get("results") or []:
            if not isinstance(entry, dict):
                continue
            requested_url = str(entry.get("url") or "").strip()
            excerpts = [
                str(item).strip()
                for item in (entry.get("excerpts") or [])
                if str(item).strip()
            ]
            full_content = str(entry.get("full_content") or "").strip()
            content = full_content or "\n\n".join(excerpts)
            if not requested_url or not content:
                continue
            extraction.contents.append(
                ReferenceContent(
                    provider=self.source_id,
                    requested_url=_safe_reference_url(requested_url),
                    url=_safe_reference_url(requested_url),
                    title=str(entry.get("title") or "").strip(),
                    content=content,
                    excerpts=excerpts,
                    published_at=_safe_optional_text(entry.get("publish_date")),
                    metadata={
                        "extract_id": extraction.request_id,
                    },
                )
            )

        for entry in body.get("errors") or []:
            if not isinstance(entry, dict):
                continue
            requested_url = str(entry.get("url") or "").strip()
            if not requested_url:
                continue
            status_code = entry.get("http_status_code")
            extraction.failures.append(
                ReferenceFailure(
                    provider=self.source_id,
                    requested_url=_safe_reference_url(requested_url),
                    error_type=str(entry.get("error_type") or "provider_error"),
                    status_code=status_code if isinstance(status_code, int) else None,
                )
            )

        successful_urls = {item.requested_url for item in extraction.contents}
        failed_urls = {item.requested_url for item in extraction.failures}
        for url in normalized_urls:
            safe_url = _safe_reference_url(url)
            if safe_url not in successful_urls and safe_url not in failed_urls:
                extraction.failures.append(
                    ReferenceFailure(
                        provider=self.source_id,
                        requested_url=safe_url,
                        error_type="empty_content",
                    )
                )

        self._record_timing(
            started_at,
            contents=len(extraction.contents),
            failures=len(extraction.failures),
            error_type="empty_content" if not extraction.contents else None,
        )
        return extraction


class FirecrawlScrapeClient(ReferenceExtractor):
    """Adapter for Firecrawl's selected URL Scrape API."""

    source_id = "firecrawl_scrape"
    display_name = "Firecrawl Scrape"
    max_urls = 20

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = FIRECRAWL_SCRAPE_URL,
        timeout: int = 45,
        only_main_content: bool = True,
        only_clean_content: bool = False,
        max_age_ms: Optional[int] = None,
    ) -> None:
        super().__init__(api_key, base_url=base_url, timeout=timeout)
        self.only_main_content = bool(only_main_content)
        self.only_clean_content = bool(only_clean_content)
        self.max_age_ms = _coerce_nonnegative_int(max_age_ms)

    def extract(
        self,
        urls: Sequence[str] | str,
        *,
        objective: Optional[str] = None,
    ) -> ReferenceExtraction:
        _ = objective
        normalized_urls = _normalize_urls(urls, max_urls=self.max_urls)
        self._last_timings = []
        started_at = time.perf_counter()
        extraction = ReferenceExtraction(provider=self.source_id)
        request_timeout_ms = min(max(self.timeout * 1000, 1000), 300000)

        for url in normalized_urls:
            payload: Dict[str, Any] = {
                "url": url,
                "formats": ["markdown"],
                "onlyMainContent": self.only_main_content,
                "onlyCleanContent": self.only_clean_content,
                "removeBase64Images": True,
                "blockAds": True,
                "skipTlsVerification": False,
                "timeout": request_timeout_ms,
            }
            if self.max_age_ms is not None:
                payload["maxAge"] = self.max_age_ms

            try:
                response = requests.post(
                    self.base_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                body = response.json()
            except (requests.RequestException, ValueError):
                extraction.failures.append(
                    ReferenceFailure(
                        provider=self.source_id,
                        requested_url=_safe_reference_url(url),
                        error_type="request_failed",
                    )
                )
                continue

            if not isinstance(body, dict) or body.get("success") is False:
                extraction.failures.append(
                    ReferenceFailure(
                        provider=self.source_id,
                        requested_url=_safe_reference_url(url),
                        error_type="provider_error" if isinstance(body, dict) else "invalid_response",
                    )
                )
                continue

            data = body.get("data") or {}
            if not isinstance(data, dict):
                extraction.failures.append(
                    ReferenceFailure(
                        provider=self.source_id,
                        requested_url=_safe_reference_url(url),
                        error_type="invalid_response",
                    )
                )
                continue

            content = str(data.get("markdown") or "").strip()
            if not content:
                extraction.failures.append(
                    ReferenceFailure(
                        provider=self.source_id,
                        requested_url=_safe_reference_url(url),
                        error_type="empty_content",
                    )
                )
                continue

            raw_metadata = data.get("metadata") or {}
            metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
            if extraction.request_id is None:
                extraction.request_id = _safe_optional_text(metadata.get("scrapeId"))
            final_url = str(
                metadata.get("sourceURL")
                or metadata.get("sourceUrl")
                or metadata.get("url")
                or url
            ).strip()
            extraction.contents.append(
                ReferenceContent(
                    provider=self.source_id,
                    requested_url=_safe_reference_url(url),
                    url=_safe_reference_url(final_url or url),
                    title=str(metadata.get("title") or "").strip(),
                    content=content,
                    metadata={
                        key: _safe_reference_url(metadata[key])
                        if key in {"sourceURL", "sourceUrl", "url"}
                        else metadata[key]
                        for key in (
                            "sourceURL",
                            "sourceUrl",
                            "url",
                            "title",
                            "statusCode",
                            "scrapeId",
                        )
                        if metadata.get(key) is not None
                    },
                )
            )

        self._record_timing(
            started_at,
            contents=len(extraction.contents),
            failures=len(extraction.failures),
            error_type="request_failed" if not extraction.contents else None,
        )
        return extraction


class TavilyExtractClient(ReferenceExtractor):
    """Adapter for Tavily's selected URL Extract API."""

    source_id = "tavily_extract"
    display_name = "Tavily Extract"
    max_urls = 20

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = TAVILY_EXTRACT_URL,
        timeout: int = 45,
        extract_depth: str = "basic",
        format: str = "markdown",
        chunks_per_source: Optional[int] = None,
    ) -> None:
        super().__init__(api_key, base_url=base_url, timeout=timeout)
        normalized_depth = str(extract_depth or "basic").strip().lower()
        self.extract_depth = (
            normalized_depth if normalized_depth in {"basic", "advanced"} else "basic"
        )
        normalized_format = str(format or "markdown").strip().lower()
        self.format = (
            normalized_format if normalized_format in {"markdown", "text"} else "markdown"
        )
        self.chunks_per_source = _coerce_positive_int(chunks_per_source)

    def extract(
        self,
        urls: Sequence[str] | str,
        *,
        objective: Optional[str] = None,
    ) -> ReferenceExtraction:
        normalized_urls = _normalize_urls(urls, max_urls=self.max_urls)
        self._last_timings = []
        started_at = time.perf_counter()
        payload: Dict[str, Any] = {
            "urls": normalized_urls,
            "extract_depth": self.extract_depth,
            "format": self.format,
            "timeout": min(max(self.timeout, 1), 60),
        }
        normalized_objective = _safe_optional_text(objective)
        if normalized_objective:
            payload["query"] = normalized_objective
            chunks = self.chunks_per_source or 3
            payload["chunks_per_source"] = min(max(chunks, 1), 5)

        try:
            response = requests.post(
                self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError):
            extraction = ReferenceExtraction(
                provider=self.source_id,
                failures=[
                    ReferenceFailure(
                        provider=self.source_id,
                        requested_url=url,
                        error_type="request_failed",
                    )
                    for url in normalized_urls
                ],
            )
            self._record_timing(
                started_at,
                contents=0,
                failures=len(extraction.failures),
                error_type="request_failed",
            )
            return extraction

        if not isinstance(body, dict):
            extraction = ReferenceExtraction(
                provider=self.source_id,
                failures=[
                    ReferenceFailure(
                        provider=self.source_id,
                        requested_url=url,
                        error_type="invalid_response",
                    )
                    for url in normalized_urls
                ],
            )
            self._record_timing(
                started_at,
                contents=0,
                failures=len(extraction.failures),
                error_type="invalid_response",
            )
            return extraction

        extraction = ReferenceExtraction(
            provider=self.source_id,
            request_id=_safe_optional_text(body.get("request_id")),
        )
        for entry in body.get("results") or []:
            if not isinstance(entry, dict):
                continue
            requested_url = str(entry.get("url") or "").strip()
            content = str(entry.get("raw_content") or "").strip()
            if not requested_url or not content:
                continue
            extraction.contents.append(
                ReferenceContent(
                    provider=self.source_id,
                    requested_url=_safe_reference_url(requested_url),
                    url=_safe_reference_url(requested_url),
                    content=content,
                    metadata={
                        "request_id": extraction.request_id,
                    },
                )
            )

        for entry in body.get("failed_results") or []:
            if not isinstance(entry, dict):
                continue
            requested_url = str(entry.get("url") or "").strip()
            if not requested_url:
                continue
            error_message = str(entry.get("error") or "").strip()
            extraction.failures.append(
                ReferenceFailure(
                    provider=self.source_id,
                    requested_url=_safe_reference_url(requested_url),
                    error_type=(error_message or "provider_error")[:200],
                )
            )

        successful_urls = {item.requested_url for item in extraction.contents}
        failed_urls = {item.requested_url for item in extraction.failures}
        for url in normalized_urls:
            safe_url = _safe_reference_url(url)
            if safe_url not in successful_urls and safe_url not in failed_urls:
                extraction.failures.append(
                    ReferenceFailure(
                        provider=self.source_id,
                        requested_url=safe_url,
                        error_type="empty_content",
                    )
                )

        self._record_timing(
            started_at,
            contents=len(extraction.contents),
            failures=len(extraction.failures),
            error_type="empty_content" if not extraction.contents else None,
        )
        return extraction


class DirectFetchClient(ReferenceExtractor):
    """Zero-config primary extractor: plain HTTP GET + main-text extraction.

    No API key, no JavaScript rendering, no proxy. Best for static official
    documentation / pricing pages. When it cannot return usable content (such
    as a bot check, JavaScript-only page, or non-HTML response), the router
    falls through to configured provider-managed extract APIs.
    """

    source_id = "direct_fetch"
    display_name = "Direct Fetch"
    max_urls = 20

    def __init__(
        self,
        *,
        timeout: int = 30,
        max_chars: Optional[int] = None,
        user_agent: Optional[str] = None,
        enabled: bool = True,
    ) -> None:
        # Bypass ReferenceExtractor.__init__ on purpose: this adapter needs no
        # API key. We still satisfy the timing/identity contract used by the
        # router and the base class helpers.
        self.api_key = ""
        self.base_url = ""
        self.timeout = max(5, int(timeout))
        self.max_chars = _coerce_positive_int(max_chars)
        self.enabled = bool(enabled)
        self.user_agent = (
            str(user_agent or "").strip() or _DEFAULT_DIRECT_FETCH_UA
        )
        self._last_timings: List[Dict[str, Any]] = []

    def extract(
        self,
        urls: Sequence[str] | str,
        *,
        objective: Optional[str] = None,
    ) -> ReferenceExtraction:
        _ = objective
        normalized_urls = _normalize_urls(urls, max_urls=self.max_urls)
        self._last_timings = []
        extraction = ReferenceExtraction(provider=self.source_id)
        if not self.enabled:
            return extraction
        started_at = time.perf_counter()
        for url in normalized_urls:
            content_text, title, error_type = self._fetch_and_extract(url)
            safe_url = _safe_reference_url(url)
            if error_type or not content_text:
                extraction.failures.append(
                    ReferenceFailure(
                        provider=self.source_id,
                        requested_url=safe_url,
                        error_type=error_type or "empty_content",
                    )
                )
                continue
            extraction.contents.append(
                ReferenceContent(
                    provider=self.source_id,
                    requested_url=safe_url,
                    url=safe_url,
                    title=title or "",
                    content=content_text,
                )
            )
        self._record_timing(
            started_at,
            contents=len(extraction.contents),
            failures=len(extraction.failures),
            error_type=None if extraction.contents else "empty_content",
        )
        return extraction

    def _fetch_and_extract(
        self,
        url: str,
    ) -> tuple[str, str, Optional[str]]:
        """Return (text, title, error_type). error_type is None on success."""
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en,zh-CN;q=0.9,zh;q=0.8",
        }
        request_url = url
        response = None
        for _ in range(_MAX_DIRECT_FETCH_REDIRECTS + 1):
            try:
                response = requests.get(
                    request_url,
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=False,
                )
            except requests.RequestException:
                return "", "", "request_failed"

            if not 300 <= response.status_code < 400:
                break

            location = response.headers.get("Location")
            if not location:
                return "", "", f"http_{response.status_code}"
            redirect_url = urljoin(request_url, location)
            try:
                _normalize_urls(redirect_url, max_urls=1)
            except ValueError:
                return "", "", "unsafe_redirect"
            request_url = redirect_url
        else:
            return "", "", "too_many_redirects"

        if response is None:
            return "", "", "request_failed"
        if response.status_code >= 400:
            return "", "", f"http_{response.status_code}"
        content_type = (response.headers.get("Content-Type") or "").lower()
        if content_type and "html" not in content_type and "xml" not in content_type:
            # PDFs / images / JSON feeds are out of scope for this static extractor.
            return "", "", "non_html"
        try:
            soup = BeautifulSoup(response.content, "lxml")
        except Exception:  # noqa: BLE001 - malformed markup should not crash the chain
            return "", "", "parse_failed"
        title = self._extract_title(soup)
        text = self._extract_main_text(soup)
        if not text:
            return "", title or "", "empty_content"
        if self.max_chars is not None and len(text) > self.max_chars:
            text = text[: self.max_chars]
        return text, title, None

    @staticmethod
    def _extract_title(soup: "BeautifulSoup") -> str:
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        first_heading = soup.find(["h1", "h2"])
        if first_heading:
            return first_heading.get_text(strip=True)
        return ""

    @staticmethod
    def _extract_main_text(soup: "BeautifulSoup") -> str:
        for tag in soup(
            ["script", "style", "noscript", "nav", "footer", "header", "aside",
             "form", "svg", "button", "iframe", "template"]
        ):
            tag.decompose()
        container = soup.find("main") or soup.find("article") or soup
        raw_text = container.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in raw_text.splitlines()]
        # Drop boilerplate whitespace and overly long runs of nav-like noise.
        kept = [line for line in lines if line and len(line) > 1]
        return "\n".join(kept)


class ReferenceExtractorRouter:
    """Try configured extractors in order for each selected URL.

    Per-URL extractor exhaustion is tracked across calls within the router's
    lifetime: once an extractor returned insufficient content or failed for a
    URL, it is skipped on subsequent calls for the same URL. This avoids
    re-running a doomed direct fetch when a heavier extraction API is
    configured behind it. Call :meth:`reset` to clear the accounting (the
    fetch tool does this at the start of every query).
    """

    def __init__(
        self,
        extractors: Sequence[ReferenceExtractor],
        *,
        min_content_chars: int = 1,
    ) -> None:
        self.extractors = list(extractors)
        self.min_content_chars = max(1, int(min_content_chars))
        self._exhausted: Dict[str, set] = {}

    def reset(self) -> None:
        """Clear per-URL extractor exhaustion accounting."""
        self._exhausted.clear()

    def is_url_exhausted(self, url: Any) -> bool:
        """Return True when every configured extractor has failed for ``url``."""
        key = _safe_reference_url(url)
        exhausted = self._exhausted.get(key)
        if not exhausted:
            return False
        return all(
            extractor.source_id in exhausted for extractor in self.extractors
        )

    def extract(
        self,
        urls: Sequence[str] | str,
        *,
        objective: Optional[str] = None,
        accept_content: Optional[Callable[[str], Any]] = None,
        tracer: Optional[Any] = None,
        trace_step_prefix: str = "extract_api",
    ) -> ReferenceExtraction:
        normalized_urls = _normalize_urls(urls, max_urls=20)
        result = ReferenceExtraction(provider="reference_router")
        trace_position = 0
        for url in normalized_urls:
            url_key = _safe_reference_url(url)
            skip_ids = set(self._exhausted.get(url_key, ()))
            attempted_count = 0
            for extractor in self.extractors:
                if extractor.source_id in skip_ids:
                    continue
                attempted_count += 1
                try:
                    provider_result = extractor.extract([url], objective=objective)
                except Exception:  # noqa: BLE001 - a failed provider must not block fallback
                    provider_result = ReferenceExtraction(
                        provider=extractor.source_id,
                        failures=[
                            ReferenceFailure(
                                provider=extractor.source_id,
                                requested_url=_safe_reference_url(url),
                                error_type="extractor_error",
                            )
                        ],
                    )
                raw_content = next(
                    (
                        item
                        for item in provider_result.contents
                        if item.content.strip()
                    ),
                    None,
                )
                content = (
                    raw_content
                    if raw_content is not None
                    and len(raw_content.content.strip()) >= self.min_content_chars
                    else None
                )
                insufficient_content = raw_content is not None and content is None
                if insufficient_content:
                    provider_result.contents = []
                    provider_result.failures.append(
                        ReferenceFailure(
                            provider=extractor.source_id,
                            requested_url=_safe_reference_url(url),
                            error_type="insufficient_content",
                        )
                    )
                objective_incomplete = False
                objective_reason = ""
                if content is not None and accept_content is not None:
                    try:
                        acceptance = accept_content(content.content)
                        if isinstance(acceptance, tuple):
                            accepted = bool(acceptance[0])
                            objective_reason = str(
                                acceptance[1] if len(acceptance) > 1 else ""
                            )
                        else:
                            accepted = bool(acceptance)
                    except Exception:  # noqa: BLE001 - semantic rejection should fall back
                        accepted = False
                        objective_reason = "validator_error"
                    if not accepted:
                        objective_incomplete = True
                        content = None
                        provider_result.contents = []
                        provider_result.failures.append(
                            ReferenceFailure(
                                provider=extractor.source_id,
                                requested_url=_safe_reference_url(url),
                                error_type="objective_incomplete",
                            )
                        )
                if tracer is not None:
                    from utils.retrieval_trace import emit_extraction_call_step

                    timings_getter = getattr(extractor, "get_last_timings", None)
                    timings = list(timings_getter() or []) if callable(timings_getter) else []
                    duration_ms = next(
                        (
                            entry.get("duration_ms")
                            for entry in reversed(timings)
                            if isinstance(entry, dict) and entry.get("duration_ms") is not None
                        ),
                        None,
                    )
                    trace_position += 1
                    emit_extraction_call_step(
                        tracer,
                        provider_result,
                        step_id=f"{trace_step_prefix}_{trace_position}",
                        duration_ms=duration_ms,
                    )
                attempt = {
                    "provider": extractor.source_id,
                    "requested_url": _safe_reference_url(url),
                    "status": "success" if content else "failed",
                    "content_chars": (
                        len(content.content)
                        if content
                        else len(raw_content.content)
                        if raw_content
                        else 0
                    ),
                }
                if insufficient_content:
                    attempt["reason"] = "insufficient_content"
                elif objective_incomplete:
                    attempt["reason"] = "objective_incomplete"
                    if objective_reason:
                        attempt["detail"] = objective_reason[:160]
                result.attempts.append(attempt)
                if content:
                    result.contents.append(content)
                    break
                self._exhausted.setdefault(url_key, set()).add(extractor.source_id)
                result.failures.extend(provider_result.failures)
            else:
                if not self.extractors:
                    result.failures.append(
                        ReferenceFailure(
                            provider="reference_router",
                            requested_url=url,
                            error_type="no_configured_extractor",
                        )
                    )
                elif attempted_count == 0:
                    result.failures.append(
                        ReferenceFailure(
                            provider="reference_router",
                            requested_url=url,
                            error_type="url_exhausted",
                        )
                    )
                    result.attempts.append(
                        {
                            "provider": "reference_router",
                            "requested_url": _safe_reference_url(url),
                            "status": "skipped",
                            "content_chars": 0,
                            "reason": "url_exhausted",
                        }
                    )
        return result


def _provider_settings(
    config: Mapping[str, Any],
    keys: Sequence[str],
) -> Dict[str, Any]:
    for key in keys:
        value = config.get(key)
        if isinstance(value, Mapping):
            if value.get("enabled") is False:
                return {}
            return dict(value)
        if isinstance(value, str):
            return {"api_key": value}
    return {}


def _direct_fetch_settings(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Read the primary direct-fetch settings from its nested runtime block.

    ``directFetch`` lives under ``orchestration`` in the documented config.
    The top-level lookup remains for compatibility with early adopters of the
    adapter, and an explicit nested ``enabled: false`` must win over that
    legacy setting.
    """
    orchestration = config.get("orchestration")
    sources: List[Mapping[str, Any]] = []
    if isinstance(orchestration, Mapping):
        sources.append(orchestration)
    sources.append(config)

    for source in sources:
        for key in ("directFetch", "direct_fetch"):
            value = source.get(key)
            if isinstance(value, Mapping):
                return dict(value)
            if isinstance(value, bool):
                return {"enabled": value}
    return {}


def build_reference_extractors(config: Mapping[str, Any]) -> List[ReferenceExtractor]:
    """Build selected-page extractors from compatible runtime configuration."""

    extractors: List[ReferenceExtractor] = []

    direct_settings = _direct_fetch_settings(config)
    direct_enabled = _coerce_bool(direct_settings.get("enabled", True), True)
    if direct_enabled:
        # Prefer a local, zero-key HTTP fetch for static pages. The router tries
        # configured extraction APIs only when this client cannot produce text.
        extractors.append(
            DirectFetchClient(
                timeout=_coerce_positive_int(direct_settings.get("timeout"), 30) or 30,
                max_chars=direct_settings.get("max_chars_per_page"),
                user_agent=direct_settings.get("user_agent"),
                enabled=True,
            )
        )

    parallel_settings = _provider_settings(
        config,
        ("parellel2", "parallel2", "parallelExtract", "parallelSearch"),
    )
    parallel_key = configured_value(parallel_settings.get("api_key"))
    if parallel_key:
        extractors.append(
            ParallelExtractClient(
                api_key=parallel_key,
                base_url=(
                    parallel_settings.get("base_url")
                    or parallel_settings.get("endpoint")
                    or PARALLEL_EXTRACT_URL
                ),
                timeout=_coerce_positive_int(parallel_settings.get("timeout"), 45) or 45,
                full_content=_coerce_bool(parallel_settings.get("full_content"), False),
                max_chars_total=parallel_settings.get("max_chars_total"),
                max_age_seconds=parallel_settings.get("max_age_seconds"),
            )
        )

    firecrawl_settings = _provider_settings(
        config,
        ("firecrawl2", "firecrawlScrape", "firecrawlSearch"),
    )
    firecrawl_key = configured_value(firecrawl_settings.get("api_key"))
    if firecrawl_key:
        extractors.append(
            FirecrawlScrapeClient(
                api_key=firecrawl_key,
                base_url=(
                    firecrawl_settings.get("base_url")
                    or firecrawl_settings.get("endpoint")
                    or FIRECRAWL_SCRAPE_URL
                ),
                timeout=_coerce_positive_int(firecrawl_settings.get("timeout"), 45) or 45,
                only_main_content=_coerce_bool(
                    firecrawl_settings.get("only_main_content"),
                    True,
                ),
                only_clean_content=_coerce_bool(
                    firecrawl_settings.get("only_clean_content"),
                    False,
                ),
                max_age_ms=firecrawl_settings.get("max_age_ms"),
            )
        )

    tavily_settings = _provider_settings(
        config,
        ("tavily2", "tavilyExtract", "tavilySearch"),
    )
    tavily_key = configured_value(tavily_settings.get("api_key"))
    if tavily_key:
        extractors.append(
            TavilyExtractClient(
                api_key=tavily_key,
                base_url=(
                    tavily_settings.get("base_url")
                    or tavily_settings.get("endpoint")
                    or TAVILY_EXTRACT_URL
                ),
                timeout=_coerce_positive_int(tavily_settings.get("timeout"), 45) or 45,
                extract_depth=str(tavily_settings.get("extract_depth") or "basic"),
                format=str(tavily_settings.get("format") or "markdown"),
                chunks_per_source=tavily_settings.get("chunks_per_source"),
            )
        )

    return extractors
