"""Agent-assisted discovery of official-domain candidates.

The boundary is strict: the agent only *discovers* -- it searches, fetches
pages, and runs structured lookups, then submits candidate hosts. Every
relation edge on the returned graph is produced from deterministic tool
outputs, never from the model's prose (page bodies are injectable, and the
model's own "this is the official site" judgement is unverifiable). The
output schema therefore has no "official"/"confidence" field at all; ruling
stays with :meth:`OfficialDomainResolver.adjudicate`, a deterministic
function of the graph.

Trigger policy lives with the caller: run this only when the deterministic
path returned ``candidate``/``none`` for an authority-required entity, so
the agent never spends tokens on the common already-resolved case.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from langchain_core.messages import HumanMessage, SystemMessage

from evidence.official_domain_graph import (
    EDGE_HOMEPAGE_REPO_BACKLINK,
    EDGE_REGISTRY_HOMEPAGE,
    EDGE_SEARCH_VOTE,
    EDGE_WIKIDATA_P856,
    RelationEdge,
    RelationGraph,
    check_backlink,
)
from evidence.official_domain_resolver import HostPattern, _host_from_value
from evidence.source_tiering import normalize_entity_stem
from utils.search_routing import extract_json_object


_LOGGER = logging.getLogger(__name__)

TOOL_NAMES = (
    "search",
    "fetch_page",
    "registry_lookup",
    "wikidata_lookup",
    "check_backlink",
    "submit_candidates",
)

_MAX_TOOL_RESULT_CHARS = 1200

_SYSTEM_PROMPT = """You find candidate official domains for an entity. You may ONLY discover; \
you never judge whether a domain is official.

Reply with exactly one JSON object per turn:
- {"action": "tool", "tool": "search", "args": {"query": "..."}}
- {"action": "tool", "tool": "fetch_page", "args": {"url": "..."}}
- {"action": "tool", "tool": "registry_lookup", "args": {"name": "..."}}
- {"action": "tool", "tool": "wikidata_lookup", "args": {"name": "..."}}
- {"action": "tool", "tool": "check_backlink", "args": {"domain": "candidate.example", "target": "github.com/org/repo"}}
- {"action": "final", "candidates": ["candidate.example", ...]}

Strategy: look the entity up in registries/Wikidata; when a registry names a \
homepage and a repository, verify with check_backlink that the homepage links \
back to the same repository. Finish with submit via the "final" action listing \
every plausible candidate host (no paths). Never claim any domain is official."""


class DiscoverySession:
    """Executes the whitelisted tools and accumulates graph edges from outputs."""

    def __init__(
        self,
        label: str,
        *,
        resolver: Any = None,
        search_clients: Optional[Sequence[Any]] = None,
        fetch_client: Optional[Any] = None,
        backlink_checker: Callable[..., Any] = check_backlink,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.label = str(label or "").strip()
        stem = normalize_entity_stem(self.label)
        self.graph = RelationGraph(stem=stem, generated_by="agent")
        self.resolver = resolver
        self._search_clients = [c for c in (search_clients or []) if c is not None]
        self._fetch_client = fetch_client
        self._backlink_checker = backlink_checker
        self._clock = clock
        self.candidates: List[str] = []
        self.finished = False
        self.calls = 0

    # -- tool dispatch -----------------------------------------------------

    def call(self, tool: str, args: Mapping[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        handler = getattr(self, f"_tool_{tool}", None)
        if handler is None:
            return {"error": f"unknown tool: {tool}"}
        try:
            return handler(args or {})
        except Exception as exc:  # noqa: BLE001 - tool failures are observations
            _LOGGER.debug("discovery tool %s failed", tool, exc_info=True)
            return {"error": f"{type(exc).__name__}: {exc}"}

    # -- tools ---------------------------------------------------------------

    def _tool_search(self, args: Mapping[str, Any]) -> Dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query required"}
        results: List[Dict[str, str]] = []
        for client in self._search_clients:
            try:
                hits = client.search(query, num_results=5)
            except Exception:  # noqa: BLE001 - a failed provider is skipped
                continue
            source_id = str(getattr(client, "source_id", "search"))
            seen_hosts = set()
            for hit in hits or []:
                url = getattr(hit, "url", "") or (hit.get("url") if isinstance(hit, Mapping) else "")
                host = _host_from_value(url)
                if not host or host in seen_hosts:
                    continue
                seen_hosts.add(host)
                title = getattr(hit, "title", "") or (hit.get("title") if isinstance(hit, Mapping) else "")
                results.append({"provider": source_id, "host": host, "url": url, "title": str(title)[:120]})
                self.graph.add(
                    RelationEdge(
                        kind=EDGE_SEARCH_VOTE,
                        subject=source_id,
                        object=host,
                        detail=f"agent search: {query[:80]}",
                        observed_at=self._clock(),
                    )
                )
        return {"results": results[:10]}

    def _tool_fetch_page(self, args: Mapping[str, Any]) -> Dict[str, Any]:
        """Orientation only: final URL, title, outbound link hosts. No edges --
        page-derived facts enter the graph exclusively via check_backlink."""
        url = str(args.get("url") or "").strip()
        if not url:
            return {"error": "url required"}
        if "://" not in url:
            url = f"https://{url}"
        from evidence.official_domain_graph import extract_href_targets

        client = self._fetch_client or self._build_fetch_client()
        if client is not None:
            extraction = client.extract([url])
            content = next(iter(extraction.contents or []), None)
            if content is not None:
                links = extract_href_targets(getattr(content, "raw_html", "") or "")
                return {
                    "final_url": content.url or url,
                    "title": (content.title or "")[:200],
                    "link_targets": links[:30],
                }
        # Raw fallback: plain HTTP fetch for the link structure.
        import requests

        resp = requests.get(
            url,
            timeout=8,
            headers={"User-Agent": "ISE-official-domain-discovery/1.0"},
        )
        resp.raise_for_status()
        html = resp.text or ""
        title = ""
        lowered = html.lower()
        start = lowered.find("<title>")
        end = lowered.find("</title>")
        if start != -1 and end != -1:
            title = html[start + 7 : end].strip()[:200]
        return {
            "final_url": str(resp.url or url),
            "title": title,
            "link_targets": extract_href_targets(html)[:30],
        }

    def _tool_registry_lookup(self, args: Mapping[str, Any]) -> Dict[str, Any]:
        name = str(args.get("name") or "").strip() or self.label
        if self.resolver is None:
            return {"error": "registry lookups unavailable"}
        facts: List[Dict[str, str]] = []
        for source in ("pypi", "npm", "github"):
            probe = self.resolver._probe_structured(source, normalize_entity_stem(name), name)
            if probe is None:
                continue
            signal, repo_url = probe
            host = _host_from_value(signal.domain)
            facts.append(
                {
                    "registry": source,
                    "homepage": signal.detail.split("->")[-1].strip()[:200],
                    "host": host,
                    "repo": repo_url,
                }
            )
            if host:
                kind = (
                    EDGE_WIKIDATA_P856
                    if signal.kind == "wikidata_p856"
                    else EDGE_REGISTRY_HOMEPAGE
                )
                self.graph.add(
                    RelationEdge(
                        kind=kind,
                        subject=source,
                        object=host,
                        detail=signal.detail,
                        observed_at=self._clock(),
                    )
                )
        return {"facts": facts}

    def _tool_wikidata_lookup(self, args: Mapping[str, Any]) -> Dict[str, Any]:
        name = str(args.get("name") or "").strip() or self.label
        if self.resolver is None:
            return {"error": "wikidata lookup unavailable"}
        probe = self.resolver._probe_wikidata(normalize_entity_stem(name), name)
        if probe is None:
            return {"facts": []}
        signal, _repo = probe
        host = _host_from_value(signal.domain)
        if host:
            self.graph.add(
                RelationEdge(
                    kind=EDGE_WIKIDATA_P856,
                    subject="wikidata",
                    object=host,
                    detail=signal.detail,
                    observed_at=self._clock(),
                )
            )
        return {"facts": [{"registry": "wikidata", "host": host, "detail": signal.detail[:200]}]}

    def _tool_check_backlink(self, args: Mapping[str, Any]) -> Dict[str, Any]:
        domain = str(args.get("domain") or "").strip()
        target = str(args.get("target") or "").strip()
        if not domain or not target:
            return {"error": "domain and target required"}
        pattern = HostPattern.parse(domain)
        if pattern is None:
            return {"error": f"unparseable domain: {domain}"}
        found, matched = self._backlink_checker(f"https://{pattern.host}/", target)
        if found is None:
            return {"domain": domain, "target": target, "result": "inconclusive"}
        host = _host_from_value(domain)
        self.graph.add(
            RelationEdge(
                kind=EDGE_HOMEPAGE_REPO_BACKLINK,
                subject=_host_from_value(target, include_path=True) or target,
                object=host,
                detail=f"backlink -> {matched}" if found else "no backlink to repo found",
                verified=bool(found),
                observed_at=self._clock(),
            )
        )
        return {
            "domain": domain,
            "target": target,
            "result": "backlink_found" if found else "backlink_absent",
        }

    def _tool_submit_candidates(self, args: Mapping[str, Any]) -> Dict[str, Any]:
        raw = args.get("candidates") or args.get("domains") or []
        if isinstance(raw, str):
            raw = [raw]
        accepted: List[str] = []
        for value in raw if isinstance(raw, Sequence) else []:
            host = _host_from_value(value)
            # Candidates must be plausible registrable hosts: no whitespace,
            # at least one label boundary (docs.acme.com, acme.com, ...).
            if (
                host
                and not any(ch.isspace() for ch in host)
                and "." in host
                and host not in accepted
            ):
                accepted.append(host)
        self.candidates = accepted
        self.finished = True
        return {"accepted": accepted}

    def _build_fetch_client(self) -> Any:
        try:
            from search.reference_fetch import DirectFetchClient

            self._fetch_client = DirectFetchClient(timeout=8)
            return self._fetch_client
        except Exception:  # noqa: BLE001
            return None


def run_discovery_agent(
    label: str,
    *,
    llm: Any,
    resolver: Any = None,
    search_clients: Optional[Sequence[Any]] = None,
    fetch_client: Optional[Any] = None,
    backlink_checker: Callable[..., Any] = check_backlink,
    max_iterations: int = 4,
    clock: Callable[[], float] = time.time,
) -> RelationGraph:
    """Run the bounded discovery loop and return the tool-derived graph.

    The loop never asks the model for a verdict; edges accumulate solely from
    deterministic tool outputs. On any structural failure (no LLM, repeated
    unparseable turns, budget exhaustion) the partial graph is returned --
    whatever deterministic facts were gathered remain usable.
    """
    session = DiscoverySession(
        label,
        resolver=resolver,
        search_clients=search_clients,
        fetch_client=fetch_client,
        backlink_checker=backlink_checker,
        clock=clock,
    )
    if llm is None or not session.graph.stem:
        return session.graph

    transcript: List[str] = [
        f"Entity: {label}. Find candidate official domains, then submit them."
    ]
    for _ in range(max(1, int(max_iterations))):
        prompt = "\n\n".join(transcript)
        try:
            response = llm.invoke(
                [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=prompt)]
            )
        except Exception:  # noqa: BLE001
            break
        content = response.content if hasattr(response, "content") else str(response)
        parsed = extract_json_object(str(content))
        if not isinstance(parsed, dict):
            transcript.append(f"Model: {str(content)[:400]}")
            transcript.append("Observer: unparseable; reply with exactly one JSON object.")
            continue
        action = str(parsed.get("action") or "").strip().lower()
        if action == "final":
            session._tool_submit_candidates(parsed)
            break
        if action != "tool":
            transcript.append("Observer: action must be \"tool\" or \"final\".")
            continue
        tool = str(parsed.get("tool") or "").strip()
        if tool not in TOOL_NAMES:
            transcript.append(f"Observer: tool {tool!r} is not available.")
            continue
        args = parsed.get("args") if isinstance(parsed.get("args"), Mapping) else parsed
        result = session.call(tool, args)
        transcript.append(f"Model: {json.dumps(parsed, ensure_ascii=False)[:400]}")
        transcript.append(
            f"Observer [{tool}]: {json.dumps(result, ensure_ascii=False)[:_MAX_TOOL_RESULT_CHARS]}"
        )
        if session.finished:
            break
    return session.graph
