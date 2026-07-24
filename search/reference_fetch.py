"""Selected-URL extraction adapters for external evidence providers."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlparse, urlunparse

import requests

from utils.config_validation import configured_value


PARALLEL_EXTRACT_URL = "https://api.parallel.ai/v1/extract"
FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"


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


class ReferenceExtractorRouter:
    """Try configured extractors in order for each selected URL."""

    def __init__(self, extractors: Sequence[ReferenceExtractor]) -> None:
        self.extractors = list(extractors)

    def extract(
        self,
        urls: Sequence[str] | str,
        *,
        objective: Optional[str] = None,
    ) -> ReferenceExtraction:
        normalized_urls = _normalize_urls(urls, max_urls=20)
        result = ReferenceExtraction(provider="reference_router")
        for url in normalized_urls:
            for extractor in self.extractors:
                provider_result = extractor.extract([url], objective=objective)
                content = next(
                    (
                        item
                        for item in provider_result.contents
                        if item.content.strip()
                    ),
                    None,
                )
                result.attempts.append(
                    {
                        "provider": extractor.source_id,
                        "requested_url": _safe_reference_url(url),
                        "status": "success" if content else "failed",
                        "content_chars": len(content.content) if content else 0,
                    }
                )
                if content:
                    result.contents.append(content)
                    break
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


def build_reference_extractors(config: Mapping[str, Any]) -> List[ReferenceExtractor]:
    """Build selected-page extractors from compatible runtime configuration."""

    extractors: List[ReferenceExtractor] = []

    parallel_settings = _provider_settings(
        config,
        ("parellel2", "parallel2", "parallelExtract"),
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
        ("firecrawl2", "firecrawlScrape"),
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

    return extractors
