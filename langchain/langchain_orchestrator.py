"""LangChain-based intelligent orchestrator with agent-style routing.

This module provides a modern implementation of the smart orchestrator using
LangChain's agent and router patterns for intelligent query handling.
"""

from __future__ import annotations

import json
import os
import sys
import time
import requests
from dataclasses import asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnableParallel
from pydantic import BaseModel, Field

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence import DomainEvidenceSource, RetrievalOptions, build_evidence_summary, source_identity_label
from evidence.official_domain_resolver import build_official_domain_resolver
from langchain.langchain_rag import LocalRAGChain, NullSearchClient, SearchRAGChain
from langchain.postcheck import PostcheckVerdict, merge_judge_verdict, screen_search_answer
from langchain.langchain_support import Document, LangChainVectorStore
from search.search import SearchClient, SearchHit, apply_search_depth_override
from search.source_selector import IntelligentSourceSelector
from utils.time_parser import TimeConstraint, parse_time_constraint
from utils.search_routing import coerce_bool, extract_json_object, is_small_talk_query, normalize_sources
from utils.timing_utils import TimingRecorder
from utils.current_time import get_current_date_str
from utils.workflow_trace import WorkflowTracer, ensure_tracer
from utils.audit_log import AuditRecorder, resolve_audit_settings
from utils.query_orchestration import (
    EvidenceLedger,
    EvidencePolicyRegistry,
    PlanController,
    PlanStepKind,
    PlanStepResult,
    QueryExecutionTrace,
    QueryPlan,
    QueryAnalysis,
    VerificationOutcome,
    VerificationStatus,
    analyze_query,
    build_query_plan,
    deterministic_query_for_plan,
    merge_optional_analysis,
    reformulate_query_for_recovery,
    verify_evidence_plan,
)


class QueryIntent(str, Enum):
    """Enum for query intent classification."""
    SEARCH = "search"
    LOCAL_RAG = "local_rag"
    DIRECT_ANSWER = "direct_answer"
    DOMAIN_API = "domain_api"
    SMALL_TALK = "small_talk"


class RouterDecision(BaseModel):
    """Schema for router decision output."""
    needs_search: bool = Field(description="Whether web search is needed")
    reason: str = Field(description="Reasoning for the decision")
    answer: Optional[str] = Field(default=None, description="Direct answer if no search needed")


class KeywordGeneration(BaseModel):
    """Schema for keyword generation output."""
    keywords: List[str] = Field(description="Generated search keywords")


class LangChainOrchestrator:
    """Intelligent orchestrator using LangChain for routing and RAG.
    
    This orchestrator decides the best approach for answering queries:
    - Direct LLM response for simple questions
    - Local RAG for document-based queries
    - Web search RAG for current information
    - Domain-specific APIs for specialized queries
    """
    
    # System prompts
    DECISION_SYSTEM_PROMPT = """You are a routing assistant that decides whether a user's question needs fresh web or document search.

Respond strictly in JSON format with the following structure:
{
    "needs_search": true/false,
    "reason": "brief explanation",
    "answer": "direct answer if needs_search is false, otherwise empty string"
}

Guidelines:
- Set needs_search=true for: current events, real-time data, recent news, prices, scores, weather
- Set needs_search=false for: general knowledge, definitions, concepts, historical facts, greetings
- If needs_search=false, provide a complete answer in the "answer" field"""

    KEYWORD_SYSTEM_PROMPT = """You help generate high quality web search keywords.

Generate up to 4 bilingual (Chinese/English) search keywords or phrases for the given query.

Respond in JSON format:
{{
    "keywords": ["关键词1", "keyword 1", "关键词2", "keyword 2"]
}}

Rules:
1. Keywords should cover the core information of the query
2. Include English keywords to improve search effectiveness
3. For sports queries, add keywords like '战报 highlights', '得分统计 box score'
4. For news queries, add keywords like '最新 latest', '新闻 news'"""

    DIRECT_ANSWER_SYSTEM_PROMPT = """You are a knowledgeable assistant. Answer clearly based on your existing knowledge.
Always answer in the same language as the user's question."""

    def __init__(
        self,
        llm: BaseChatModel,
        search_client: Optional[SearchClient] = None,
        *,
        classifier_llm: Optional[BaseChatModel] = None,
        routing_llm: Optional[BaseChatModel] = None,
        postcheck_llm: Optional[BaseChatModel] = None,
        data_path: Optional[str] = None,
        reranker: Optional[Any] = None,
        min_rerank_score: float = 0.0,
        max_per_domain: int = 1,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        source_selector: Optional[IntelligentSourceSelector] = None,
        show_timings: bool = False,
        google_api_key: Optional[str] = None,
        finnhub_api_key: Optional[str] = None,
        sportsdb_api_key: Optional[str] = None,
        apisports_api_key: Optional[str] = None,
        # Search source metadata
        requested_search_sources: Optional[List[str]] = None,
        active_search_sources: Optional[List[str]] = None,
        active_search_source_labels: Optional[List[str]] = None,
        missing_search_sources: Optional[List[str]] = None,
        configured_search_sources: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.llm = llm
        self.classifier_llm = classifier_llm or llm
        self.routing_llm = routing_llm or llm
        self.config = config or {}
        self.search_client = search_client
        self.data_path = data_path
        self.reranker = reranker
        self.min_rerank_score = min_rerank_score
        self.max_per_domain = max(1, max_per_domain)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.show_timings = show_timings
        self.google_api_key = google_api_key
        self.postcheck_llm = postcheck_llm or self.llm
        self.postcheck_config = self._normalize_postcheck_config(self.config.get("postcheck") or {})
        self.orchestration_config = self._normalize_orchestration_config(
            self.config.get("orchestration") or {}
        )
        self._policy_registry = EvidencePolicyRegistry()
        self._current_analysis: Optional[QueryAnalysis] = None
        self._current_plan: Optional[QueryPlan] = None
        self._current_ledger: Optional[EvidenceLedger] = None
        self._current_execution_trace: Optional[QueryExecutionTrace] = None
        self._current_plan_controller: Optional[PlanController] = None
        self._current_verification: Optional[VerificationOutcome] = None
        self._react_fallback_orchestrator: Optional[Any] = None
        
        # Initialize source selector
        if source_selector:
            self.source_selector = source_selector
        else:
            # Create legacy wrapper for classifier LLM
            from langchain.langchain_llm import LangChainLLMWrapper
            legacy_client = LangChainLLMWrapper(self.classifier_llm)
            self.source_selector = IntelligentSourceSelector(
                llm_client=legacy_client,
                use_llm=True,
                google_api_key=google_api_key,
                finnhub_api_key=finnhub_api_key,
                sportsdb_api_key=sportsdb_api_key,
                apisports_api_key=apisports_api_key,
                config=self.config,
            )
        
        # Search source metadata
        self.requested_search_sources = self._normalize_sources(requested_search_sources)
        self.active_search_sources = self._normalize_sources(
            active_search_sources or getattr(search_client, "active_sources", [])
        )
        self.active_search_source_labels = [
            str(label).strip() for label in (active_search_source_labels or []) if str(label).strip()
        ]
        self.missing_search_sources = self._normalize_sources(missing_search_sources)
        self.configured_search_sources = self._normalize_sources(configured_search_sources)
        
        # Lazy-initialized pipelines
        self._local_rag: Optional[LocalRAGChain] = None
        self._search_rag: Optional[SearchRAGChain] = None
        self._local_signature: Optional[tuple] = None
        self._search_signature: Optional[tuple] = None
        self._primary_rag: Optional[SearchRAGChain] = None
        self._primary_signature: Optional[tuple] = None
        self._plan_resolver: Optional[Any] = None
        
        # Build decision chain
        self._decision_chain = self._build_decision_chain()
        self._keyword_chain = self._build_keyword_chain()

    @staticmethod
    def _normalize_postcheck_config(config: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize post-check configuration with safe defaults."""
        react_cfg = config.get("react_fallback") or {}
        judge_cfg = config.get("judge") or {}
        return {
            "enabled": bool(config.get("enabled", False)),
            "log_verdicts": bool(config.get("log_verdicts", False)),
            "judge": {
                "enabled": bool(judge_cfg.get("enabled", True)),
            },
            "react_fallback": {
                "enabled": bool(react_cfg.get("enabled", False)),
                "max_iterations": int(react_cfg.get("max_iterations", 4) or 4),
                "engine": str(react_cfg.get("engine") or "").strip().lower() or None,
            },
        }

    @staticmethod
    def _normalize_orchestration_config(config: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize rollout controls without making optional analysis mandatory."""
        raw_recovery_budget = config.get("recovery_budget", 1)
        try:
            recovery_budget = max(0, int(1 if raw_recovery_budget is None else raw_recovery_budget))
        except (TypeError, ValueError):
            recovery_budget = 1
        recovery_config = config.get("reformulation_recovery") or {}
        if isinstance(recovery_config, bool):
            recovery_config = {"enabled": recovery_config}
        if not isinstance(recovery_config, dict):
            recovery_config = {}
        max_attempts = recovery_config.get("max_attempts", recovery_budget)
        try:
            max_attempts = max(1, int(max_attempts))
        except (TypeError, ValueError):
            max_attempts = max(1, recovery_budget)
        return {
            "enabled": bool(config.get("enabled", True)),
            "enforce_verification": bool(config.get("enforce_verification", True)),
            "llm_analysis": bool(config.get("llm_analysis", False)),
            "query_budget": max(1, int(config.get("query_budget", 3) or 3)),
            "result_budget": max(1, int(config.get("result_budget", 8) or 8)),
            "time_budget_ms": max(1000, int(config.get("time_budget_ms", 20000) or 20000)),
            "recovery_budget": recovery_budget,
            "reformulation_recovery": {
                "enabled": bool(recovery_config.get("enabled", True)),
                "max_attempts": max_attempts,
            },
        }

    def _initialize_orchestration(
        self,
        query: str,
        *,
        allow_search: bool,
        time_constraint: TimeConstraint,
    ) -> None:
        """Create the per-turn analysis and trace before any evidence work."""
        self._current_analysis = None
        self._current_plan = None
        self._current_ledger = None
        self._current_plan_controller = None
        self._current_verification = None
        self._current_execution_trace = None
        self._recovery_terminal_reason: Optional[str] = None
        if not self.orchestration_config["enabled"]:
            return

        analysis = analyze_query(
            query,
            allow_search=allow_search,
            requested_sources=self.requested_search_sources,
            time_constraint=time_constraint,
        )
        if self.orchestration_config["llm_analysis"] and self.routing_llm is not None:
            candidate = self._optional_llm_analysis(query)
            analysis = merge_optional_analysis(analysis, candidate)
        trace = QueryExecutionTrace(
            configured=self.configured_search_sources,
            requested=self.requested_search_sources,
            eligible=self.active_search_sources,
        )
        trace.record_analysis(analysis)
        self._current_analysis = analysis
        self._current_execution_trace = trace

    def _optional_llm_analysis(self, query: str) -> Optional[Dict[str, Any]]:
        """Ask an opt-in analyzer, then let deterministic validation constrain it."""
        system_prompt = (
            "Extract only query-analysis hints as JSON with entities, claim_classes, "
            "ambiguities, and critical_ambiguity. Do not decide that search is allowed."
        )
        try:
            response = self.routing_llm.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=query)]
            )
            content = response.content if hasattr(response, "content") else str(response)
            parsed = extract_json_object(content)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    def _should_select_domain_sources(self, query: str) -> bool:
        """Avoid an unneeded domain-classifier call for a generic evidence plan.

        The legacy selector includes a broad ``temporal_change`` label, so a
        comparison alone can otherwise spend an LLM call and appear to select
        temporal sources before the plan rejects that route.  Structured-domain
        keywords retain the existing classifier path; selectors without a
        keyword inventory also retain their current behavior for compatibility.
        """
        if not self.orchestration_config["enabled"]:
            return True
        analysis = self._current_analysis
        if analysis is None or not analysis.requires_evidence:
            return True
        domain_keywords = getattr(self.source_selector, "domain_keywords", None)
        if not isinstance(domain_keywords, dict):
            return True
        query_lower = query.casefold()
        for domain in ("weather", "transportation", "finance", "sports", "location"):
            keywords = domain_keywords.get(domain) or []
            if any(str(keyword).casefold() in query_lower for keyword in keywords if keyword):
                return True
        return False

    def _get_official_resolver(self) -> Optional[Any]:
        """Reuse the RAG chain's resolver, or lazily build one for planning.

        Both instances share the same SQLite cache path, so resolutions made
        at plan time are reused during evidence tiering and vice versa.
        """
        primary = self._primary_rag
        resolver = getattr(primary, "_official_resolver", None) if primary is not None else None
        if resolver is not None:
            return resolver
        if self._plan_resolver is not None:
            return self._plan_resolver
        orchestration = self.config.get("orchestration")
        if not isinstance(orchestration, dict):
            return None
        discovery_clients = list(
            getattr(self.search_client, "clients", None)
            or ([self.search_client] if self.search_client is not None else [])
        )
        try:
            self._plan_resolver = build_official_domain_resolver(
                orchestration,
                search_clients=discovery_clients,
            )
        except Exception:  # noqa: BLE001 - planning must not fail on resolver setup
            self._plan_resolver = None
        return self._plan_resolver

    def _agent_discovery_config(self) -> Dict[str, Any]:
        orchestration = self.config.get("orchestration")
        block = orchestration.get("official_domain_resolution") if isinstance(orchestration, dict) else None
        agent_cfg = block.get("agent_discovery") if isinstance(block, dict) else None
        if isinstance(agent_cfg, bool):
            agent_cfg = {"enabled": agent_cfg}
        return agent_cfg if isinstance(agent_cfg, dict) else {}

    def _resolve_official_targets_for_plan(
        self,
        analysis: QueryAnalysis,
    ) -> Optional[Dict[str, List[str]]]:
        """Resolve comparison members to verified official domains for planning.

        This closes the gap where only statically pinned entities could
        receive an ``official_domain_recovery`` step: any entity the resolver
        rules ``official`` -- via pins, cache, structured signals, or the
        bounded discovery agent -- now generates proactive official-site
        retrieval targets. The ruling stays deterministic; the agent (when
        enabled) only fills the relation graph the resolver rules on.
        """
        if not (
            analysis.constraints.get("authority_required")
            and analysis.constraints.get("comparison_required")
            and analysis.search_allowed
        ):
            return None
        resolver = self._get_official_resolver()
        if resolver is None:
            return None
        agent_cfg = self._agent_discovery_config()
        agent_enabled = bool(agent_cfg.get("enabled", False))
        resolved: Dict[str, List[str]] = {}
        for member in analysis.comparison_members:
            label = str(member or "").strip()
            if not label:
                continue
            try:
                resolution = resolver.resolve(label)
            except Exception:  # noqa: BLE001 - a resolver failure must not break planning
                continue
            if (
                not getattr(resolution, "is_official", False)
                and agent_enabled
                and getattr(resolution, "confidence", "none") in ("candidate", "none")
            ):
                discovered = self._agent_discover_official(label, resolver, agent_cfg)
                if discovered is not None:
                    resolution = discovered
            if getattr(resolution, "is_official", False) and getattr(
                resolution, "resolved_domains", None
            ):
                resolved[label] = list(resolution.resolved_domains)
        return resolved or None

    def _agent_discover_official(
        self,
        label: str,
        resolver: Any,
        agent_cfg: Dict[str, Any],
    ) -> Optional[Any]:
        """Run the discovery agent for one unresolved entity and adjudicate.

        The agent only discovers candidates and fills the relation graph;
        :meth:`resolver.adjudicate` performs the deterministic ruling.
        """
        try:
            from orchestrators.official_domain_discovery_agent import run_discovery_agent

            llm = self.routing_llm or self.llm
            discovery_clients = list(
                getattr(self.search_client, "clients", None)
                or ([self.search_client] if self.search_client is not None else [])
            )
            graph = run_discovery_agent(
                label,
                llm=llm,
                resolver=resolver,
                search_clients=discovery_clients,
                max_iterations=max(1, int(agent_cfg.get("max_iterations", 4) or 4)),
            )
        except Exception:  # noqa: BLE001 - discovery is best-effort
            return None
        if graph is None or not graph.edges:
            return None
        try:
            return resolver.adjudicate(label, graph)
        except Exception:  # noqa: BLE001
            return None

    def _prepare_query_plan(
        self,
        *,
        needs_evidence: bool,
        has_local_docs: bool,
        domain_hint: Optional[str] = None,
        result_budget: Optional[int] = None,
    ) -> Optional[QueryPlan]:
        """Bind the shared analysis to the concrete route selected this turn."""
        if not self.orchestration_config["enabled"]:
            return None
        analysis = self._current_analysis
        if analysis is None:
            return None
        if domain_hint and str(domain_hint).lower() != "general":
            analysis.domain_hint = str(domain_hint)
        recovery_config = self.orchestration_config["reformulation_recovery"]
        recovery_budget = (
            self.orchestration_config["recovery_budget"]
            if recovery_config["enabled"]
            else 0
        )
        plan = build_query_plan(
            analysis,
            has_local_docs=has_local_docs,
            needs_evidence=needs_evidence,
            query_budget=self.orchestration_config["query_budget"],
            result_budget=result_budget or self.orchestration_config["result_budget"],
            time_budget_ms=self.orchestration_config["time_budget_ms"],
            recovery_budget=recovery_budget,
            registry=self._policy_registry,
            official_domains=(
                self.config.get("orchestration", {}).get("official_domains")
                if isinstance(self.config.get("orchestration"), dict)
                else None
            ),
            resolved_official_domains=self._resolve_official_targets_for_plan(analysis),
        )
        self._current_plan = plan
        self._current_ledger = EvidenceLedger(plan)
        trace = self._current_execution_trace
        if trace is not None:
            trace.record_plan(plan)
            self._current_plan_controller = PlanController(plan, trace)
        return plan

    def _verify_current_plan(self, result: Dict[str, Any]) -> Optional[VerificationOutcome]:
        """Run deterministic verification against the ledger built for this turn."""
        plan = self._current_plan
        ledger = self._current_ledger
        trace = self._current_execution_trace
        if plan is None or ledger is None:
            return None
        if not ledger.entries:
            ledger.ingest(result.get("evidence_items") or [])
            ledger.apply_limits(max_items=plan.result_budget)
            if trace is not None:
                trace.record_ledger(ledger)
        outcome = verify_evidence_plan(
            plan,
            ledger,
            answer=str(result.get("answer") or ""),
        )
        controller = self._current_plan_controller
        if (
            outcome.recoverable
            and controller is not None
            and controller.recoveries_used >= plan.recovery_budget
        ):
            outcome = VerificationOutcome(
                status=VerificationStatus.EVIDENCE_INSUFFICIENT,
                missing_constraints=list(outcome.missing_constraints),
                failure_types=list(outcome.failure_types) + ["recovery_budget_exhausted"],
                recoverable=False,
                next_action="return_insufficient",
                rule_hits=list(outcome.rule_hits),
            )
        if outcome.recoverable and result.get("search_error"):
            outcome = VerificationOutcome(
                status=VerificationStatus.EVIDENCE_INSUFFICIENT,
                missing_constraints=list(outcome.missing_constraints),
                failure_types=list(outcome.failure_types) + ["search_unavailable"],
                recoverable=False,
                next_action="return_insufficient",
                rule_hits=list(outcome.rule_hits),
            )
        terminal_reason = getattr(self, "_recovery_terminal_reason", None)
        if terminal_reason and outcome.status != VerificationStatus.COMPLETE:
            outcome = VerificationOutcome(
                status=VerificationStatus.EVIDENCE_INSUFFICIENT,
                missing_constraints=list(outcome.missing_constraints),
                failure_types=list(dict.fromkeys(list(outcome.failure_types) + [terminal_reason])),
                recoverable=False,
                next_action="return_insufficient",
                rule_hits=list(outcome.rule_hits),
            )
        self._current_verification = outcome
        if trace is not None:
            trace.record_verification(outcome)
        return outcome

    def _build_clarification_response(
        self,
        query: str,
        *,
        has_local_docs: bool,
    ) -> Dict[str, Any]:
        """Stop before retrieval when the deterministic plan cannot name its target."""
        analysis = self._current_analysis
        ambiguities = list(analysis.ambiguities) if analysis is not None else []
        is_chinese = any("\u4e00" <= char <= "\u9fff" for char in query)
        details = "、".join(ambiguities) if ambiguities else "关键实体或约束"
        answer = (
            f"需要先澄清 {details}，才能开始检索并比较。请补充要比较的准确对象或范围。"
            if is_chinese
            else "I need the exact entities or constraints before searching, so I do not guess the comparison target."
        )
        result = {
            "query": query,
            "answer": answer,
            "search_hits": [],
            "retrieved_docs": [],
            "evidence_items": [],
            "evidence_summary": "",
            "evidence_sources_active": [],
            "evidence_sources_used": [],
            "evidence_source_types_active": [],
            "evidence_source_types_used": [],
            "control": {
                "search_performed": False,
                "decision": {"needs_search": False, "reason": "clarification_required"},
                "search_mode": "clarification_required",
                "local_docs_present": has_local_docs,
                "search_allowed": bool(analysis.search_allowed) if analysis else True,
                "final_executor": "clarification_required",
            },
        }
        self._verify_current_plan(result)
        return result

    @staticmethod
    def _mark_evidence_insufficient(
        result: Dict[str, Any],
        control: Dict[str, Any],
        outcome: VerificationOutcome,
    ) -> Dict[str, Any]:
        """Replace an unsupported factual draft with an explicit bounded state."""
        query = str(result.get("query") or "")
        is_chinese = any("\u4e00" <= char <= "\u9fff" for char in query)
        missing = "、".join(outcome.missing_constraints[:4]) or "所需证据"
        result["answer"] = (
            f"当前检索到的证据不足以可靠回答该请求，缺少：{missing}。"
            if is_chinese
            else f"The available evidence is insufficient to answer this reliably. Missing: {missing}."
        )
        control["verification"] = outcome.to_dict()
        control["final_executor"] = "evidence_insufficient"
        control["evidence_insufficient"] = True
        result["control"] = control
        return result

    @staticmethod
    def _has_limited_evidence_answer(result: Dict[str, Any]) -> bool:
        """Keep a qualified non-empty answer distinct from an unsupported draft."""
        return bool(
            str(result.get("answer") or "").strip()
            and result.get("answer_basis") == "limited_evidence"
        )

    def _requires_target_official_coverage(self) -> bool:
        """Require a safe terminal response when configured target evidence is absent."""
        plan = self._current_plan
        return bool(
            plan
            and plan.step_for_kind(
                PlanStepKind.OFFICIAL_DOMAIN_RECOVERY,
                include_recovery=True,
            )
        )

    def _attach_orchestration_metadata(self, result: Dict[str, Any]) -> None:
        """Add bounded plan facts without altering compatibility result fields."""
        if not self.orchestration_config["enabled"]:
            return
        control = result.setdefault("control", {})
        analysis = self._current_analysis
        plan = self._current_plan
        ledger = self._current_ledger
        trace = self._current_execution_trace
        if plan is None and analysis is not None:
            decision = control.get("decision") if isinstance(control.get("decision"), dict) else {}
            plan = self._prepare_query_plan(
                needs_evidence=bool(decision.get("needs_search")),
                has_local_docs=bool(control.get("local_docs_present")),
            )
            ledger = self._current_ledger
            trace = self._current_execution_trace
        if analysis is not None:
            control.setdefault("query_analysis", analysis.to_dict())
        if plan is not None:
            control.setdefault("query_plan", plan.to_dict())
        if ledger is not None:
            if not ledger.entries:
                ledger.ingest(result.get("evidence_items") or [])
                ledger.apply_limits(max_items=plan.result_budget if plan else None)
            control.setdefault("evidence_coverage", ledger.coverage_summary())
        if self._current_verification is not None:
            control.setdefault("verification", self._current_verification.to_dict())
        if trace is not None:
            control.setdefault("execution_trace", trace.to_dict())
            control.setdefault(
                "providers",
                {
                    "configured": list(trace.configured),
                    "requested": list(trace.requested),
                    "eligible": list(trace.eligible),
                    "executed": list(trace.executed),
                },
            )

    def _build_decision_chain(self):
        """Build the routing decision chain."""
        prompt = ChatPromptTemplate.from_messages([
            ("system", self.DECISION_SYSTEM_PROMPT),
            ("human", "{query}"),
        ])
        
        return prompt | self.routing_llm

    def _build_keyword_chain(self):
        """Build the keyword generation chain."""
        prompt = ChatPromptTemplate.from_messages([
            ("system", self.KEYWORD_SYSTEM_PROMPT),
            ("human", "{query}"),
        ])
        
        return prompt | self.routing_llm

    @staticmethod
    def _normalize_sources(sources: Optional[List[str]]) -> List[str]:
        """Normalize source list."""
        return normalize_sources(sources)

    def _is_small_talk(self, query: str) -> bool:
        """Check if query is small talk."""
        return is_small_talk_query(query)

    def _make_routing_decision(
        self,
        query: str,
        timing_recorder: Optional[TimingRecorder] = None,
    ) -> Dict[str, Any]:
        """Make routing decision using LLM."""
        start = time.perf_counter()
        try:
            response = self._decision_chain.invoke({"query": query})
            content = response.content if hasattr(response, 'content') else str(response)
            
            # Parse JSON response
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = extract_json_object(content) or {"needs_search": True, "reason": "parse_error"}
            
            return {
                "needs_search": coerce_bool(parsed.get("needs_search"), True),
                "reason": parsed.get("reason", ""),
                "direct_answer": parsed.get("answer", ""),
                "raw_text": content[:100] if content else None,
            }
        except Exception as exc:
            return {
                "needs_search": True,
                "reason": f"decision_error: {exc}",
                "direct_answer": None,
                "raw_text": None,
            }
        finally:
            if timing_recorder:
                duration_ms = (time.perf_counter() - start) * 1000
                timing_recorder.record_llm_call(
                    label="search_decision",
                    duration_ms=duration_ms,
                    provider=getattr(self.routing_llm, "provider", None),
                    model=getattr(self.routing_llm, "model_name", None),
                )

    def _generate_keywords(
        self,
        query: str,
        timing_recorder: Optional[TimingRecorder] = None,
    ) -> Dict[str, Any]:
        """Generate search keywords using LLM."""
        start = time.perf_counter()
        try:
            response = self._keyword_chain.invoke({"query": query})
            content = response.content if hasattr(response, 'content') else str(response)
            
            # Parse JSON response
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = extract_json_object(content) or {"keywords": []}
            
            keywords = parsed.get("keywords", [])
            if isinstance(keywords, str):
                keywords = [k.strip() for k in keywords.split(";") if k.strip()]
            
            return {
                "keywords": keywords[:10],
                "raw_text": content[:200] if content else None,
            }
        except Exception as exc:
            return {
                "keywords": [],
                "raw_text": None,
                "error": str(exc),
            }
        finally:
            if timing_recorder:
                duration_ms = (time.perf_counter() - start) * 1000
                timing_recorder.record_llm_call(
                    label="keyword_generation",
                    duration_ms=duration_ms,
                    provider=getattr(self.routing_llm, "provider", None),
                    model=getattr(self.routing_llm, "model_name", None),
                )

    def _get_local_rag(self, snapshot: Optional[tuple]) -> Optional[LocalRAGChain]:
        """Get or create local RAG pipeline."""
        if not self.data_path or snapshot is None:
            return None
        
        if self._local_rag is None or self._local_signature != snapshot:
            try:
                self._local_rag = LocalRAGChain(
                    llm=self.llm,
                    data_path=self.data_path,
                    config=self.config,
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                )
                self._local_signature = snapshot
            except Exception as exc:
                print(f"Failed to initialize local RAG: {exc}")
                self._local_rag = None
                self._local_signature = None
        
        return self._local_rag

    def _get_search_rag(self, snapshot: Optional[tuple]) -> Optional[SearchRAGChain]:
        """Get or create search RAG pipeline."""
        if not self.search_client:
            return None
        return self._get_primary_rag(snapshot, require_search_client=True)

    def _get_primary_rag(
        self,
        snapshot: Optional[tuple],
        *,
        require_search_client: bool = False,
        tracer: Optional[Any] = None,
    ) -> Optional[SearchRAGChain]:
        """Get or create the unified primary RAG pipeline."""
        if require_search_client and not self.search_client:
            return None

        search_client = self.search_client or NullSearchClient()
        search_signature = (id(search_client), snapshot)

        if self._primary_rag is None or self._primary_signature != search_signature:
            self._primary_rag = SearchRAGChain(
                llm=self.llm,
                search_client=search_client,
                data_path=self.data_path,
                config=self.config,
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
                reranker=self.reranker,
                min_rerank_score=self.min_rerank_score,
                max_per_domain=self.max_per_domain,
                source_selector=self.source_selector,
                tracer=tracer,
            )
            self._primary_signature = search_signature

        return self._primary_rag

    def _snapshot_local_docs(self) -> Optional[tuple]:
        """Create a snapshot of local documents for cache invalidation."""
        if not self.data_path or not os.path.isdir(self.data_path):
            return None
        
        records = []
        for root, _, files in os.walk(self.data_path):
            for name in files:
                if name.lower().endswith((".txt", ".md", ".pdf")):
                    full_path = os.path.join(root, name)
                    try:
                        records.append((full_path, os.path.getmtime(full_path)))
                    except OSError:
                        continue
        return tuple(sorted(records))

    def _direct_answer(
        self,
        query: str,
        timing_recorder: Optional[TimingRecorder] = None,
        images: Optional[List[Dict[str, str]]] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate direct answer without search."""
        messages = [
            SystemMessage(content=system_prompt or self.DIRECT_ANSWER_SYSTEM_PROMPT),
        ]
        
        if images:
            content_list = [{"type": "text", "text": query}]
            for img in images:
                b64 = img.get("base64", "")
                if "," in b64:
                    b64 = b64.split(",")[1]
                mime = img.get("mime_type", "image/jpeg")
                content_list.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"}
                })
            messages.append(HumanMessage(content=content_list))
        else:
            messages.append(HumanMessage(content=query))
        
        start = time.perf_counter()
        try:
            response = self.llm.invoke(messages)
            content = response.content if hasattr(response, 'content') else str(response)
            return {
                "content": content,
                "raw": response.response_metadata if hasattr(response, "response_metadata") else None,
            }
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            if timing_recorder:
                duration_ms = (time.perf_counter() - start) * 1000
                timing_recorder.record_llm_call(
                    label="direct_answer",
                    duration_ms=duration_ms,
                    provider=getattr(self.llm, "provider", None),
                    model=getattr(self.llm, "model_name", None),
                )

    def _run_primary_rag(
        self,
        *,
        query: str,
        snapshot: Optional[tuple],
        num_retrieved_docs: int,
        max_tokens: int,
        temperature: float,
        timing_recorder: Optional[TimingRecorder],
        enable_search: bool,
        num_search_results: int = 0,
        per_source_limit: Optional[int] = None,
        reference_limit: Optional[int] = None,
        search_query: Optional[str] = None,
        freshness: Optional[str] = None,
        date_restrict: Optional[str] = None,
        extra_context: Optional[str] = None,
        domain: Optional[str] = None,
        domain_result: Optional[Dict[str, Any]] = None,
        enable_domain: bool = False,
        tracer: Optional[Any] = None,
        query_plan: Optional[QueryPlan] = None,
        evidence_ledger: Optional[EvidenceLedger] = None,
        execution_trace: Optional[QueryExecutionTrace] = None,
        plan_controller: Optional[PlanController] = None,
        enable_local_docs: Optional[bool] = None,
        web_step_kind: PlanStepKind = PlanStepKind.WEB_SEARCH,
        enable_temporal_recovery: bool = True,
    ) -> Optional[Dict[str, Any]]:
        pipeline = self._get_primary_rag(snapshot, tracer=tracer)
        if not pipeline:
            return None

        if self.orchestration_config["enabled"] and query_plan is None:
            query_plan = self._current_plan
            evidence_ledger = evidence_ledger or self._current_ledger
            execution_trace = execution_trace or self._current_execution_trace
            plan_controller = plan_controller or self._current_plan_controller

        return pipeline.answer(
            query,
            search_query=search_query,
            num_search_results=max(1, int(num_search_results or 1)),
            per_source_limit=per_source_limit,
            num_retrieved_docs=num_retrieved_docs,
            max_tokens=max_tokens,
            temperature=temperature,
            enable_search=enable_search,
            enable_local_docs=bool(snapshot) if enable_local_docs is None else bool(enable_local_docs),
            reference_limit=reference_limit,
            freshness=freshness,
            date_restrict=date_restrict,
            timing_recorder=timing_recorder,
            extra_context=extra_context,
            domain=domain,
            domain_result=domain_result,
            enable_domain=enable_domain,
            tracer=tracer,
            query_plan=query_plan,
            evidence_ledger=evidence_ledger,
            execution_trace=execution_trace,
            plan_controller=plan_controller,
            web_step_kind=web_step_kind,
            enable_temporal_recovery=enable_temporal_recovery,
        )

    def _terminalize_recovery_outcome(
        self,
        outcome: VerificationOutcome,
        reason: str,
    ) -> VerificationOutcome:
        """Turn a blocked recovery action into the deterministic final state."""
        terminal = VerificationOutcome(
            status=VerificationStatus.EVIDENCE_INSUFFICIENT,
            missing_constraints=list(outcome.missing_constraints),
            failure_types=list(dict.fromkeys(list(outcome.failure_types) + [reason])),
            recoverable=False,
            next_action="return_insufficient",
            rule_hits=list(outcome.rule_hits),
        )
        self._recovery_terminal_reason = reason
        self._current_verification = terminal
        if self._current_execution_trace is not None:
            self._current_execution_trace.record_verification(terminal)
        return terminal

    def _run_reformulation_recovery(
        self,
        *,
        result: Dict[str, Any],
        query: str,
        snapshot: Optional[tuple],
        num_retrieved_docs: int,
        max_tokens: int,
        temperature: float,
        timing_recorder: Optional[TimingRecorder],
        num_search_results: int,
        per_source_limit: Optional[int],
        reference_limit: Optional[int],
        freshness: Optional[str],
        date_restrict: Optional[str],
        tracer: Optional[Any],
    ) -> Dict[str, Any]:
        """Consume typed recover actions through one bounded web re-search loop."""
        plan = self._current_plan
        ledger = self._current_ledger
        controller = self._current_plan_controller
        trace = self._current_execution_trace
        recovery_config = self.orchestration_config["reformulation_recovery"]
        if not (plan and ledger and controller and recovery_config["enabled"]):
            return result

        recovery_step = plan.step_for_kind(
            PlanStepKind.OFFICIAL_DOMAIN_RECOVERY,
            include_recovery=True,
        ) or plan.step_for_kind(
            PlanStepKind.QUERY_REFORMULATION,
            include_recovery=True,
        )
        if recovery_step is None:
            return result
        official_domain_recovery = (
            recovery_step.kind == PlanStepKind.OFFICIAL_DOMAIN_RECOVERY
        )
        recovery_executor = (
            "official_domain_recovery"
            if official_domain_recovery
            else "query_reformulation"
        )

        outcome = self._verify_current_plan(result)
        attempt = 0
        max_attempts = min(plan.recovery_budget, recovery_config["max_attempts"])
        while outcome is not None and outcome.next_action == "recover" and outcome.recoverable:
            if attempt >= max_attempts:
                if trace is not None:
                    trace.record_recovery(
                        executor=recovery_executor,
                        status="skipped",
                        reason="recovery_budget_exhausted",
                    )
                self._terminalize_recovery_outcome(outcome, "recovery_budget_exhausted")
                break

            blocked = controller.can_run(recovery_step)
            if blocked:
                if trace is not None:
                    trace.record_recovery(
                        executor=recovery_executor,
                        status="skipped",
                        reason=blocked,
                    )
                self._terminalize_recovery_outcome(outcome, blocked)
                break

            recovery_query = (
                "；".join(
                    str(target.get("query") or "").strip()
                    for target in recovery_step.metadata.get("targets") or []
                    if isinstance(target, dict)
                )
                if official_domain_recovery
                else reformulate_query_for_recovery(
                    plan.analysis,
                    outcome.missing_constraints,
                )
            )
            if not recovery_query:
                if trace is not None:
                    trace.record_recovery(
                        executor=recovery_executor,
                        status="skipped",
                        reason=(
                            "empty_official_domain_targets"
                            if official_domain_recovery
                            else "empty_reformulation_query"
                        ),
                    )
                self._terminalize_recovery_outcome(
                    outcome,
                    (
                        "empty_official_domain_targets"
                        if official_domain_recovery
                        else "empty_reformulation_query"
                    ),
                )
                break

            attempt += 1
            recovery_step.query = recovery_query
            trace_step_id = f"recovery_{attempt}"
            active_tracer = ensure_tracer(tracer)
            active_tracer.begin(
                trace_step_id,
                "官方域名检索恢复" if official_domain_recovery else "改写检索恢复",
                detail=recovery_query,
            )
            if trace is not None:
                trace.record_recovery(
                    executor=recovery_executor,
                    status="active",
                    query=recovery_query,
                )
            recovered = self._run_primary_rag(
                query=query,
                snapshot=snapshot,
                num_retrieved_docs=num_retrieved_docs,
                max_tokens=max_tokens,
                temperature=temperature,
                timing_recorder=timing_recorder,
                enable_search=True,
                num_search_results=num_search_results,
                per_source_limit=per_source_limit,
                reference_limit=reference_limit,
                search_query=recovery_query,
                freshness=freshness,
                date_restrict=date_restrict,
                tracer=active_tracer,
                query_plan=plan,
                evidence_ledger=ledger,
                execution_trace=trace,
                plan_controller=controller,
                enable_local_docs=False,
                web_step_kind=recovery_step.kind,
                enable_temporal_recovery=False,
            )
            if not recovered:
                active_tracer.end(trace_step_id, detail="检索不可用", status="error")
                if trace is not None:
                    trace.record_recovery(
                        executor=recovery_executor,
                        status="error",
                        query=recovery_query,
                        reason="search_unavailable",
                    )
                self._terminalize_recovery_outcome(outcome, "search_unavailable")
                break

            result = recovered
            outcome = self._verify_current_plan(result)
            status = "complete" if outcome and outcome.status == VerificationStatus.COMPLETE else "continued"
            active_tracer.end(trace_step_id, detail=status)
            if trace is not None:
                trace.record_recovery(
                    executor=recovery_executor,
                    status=status,
                    query=recovery_query,
                    reason=outcome.status.value if outcome is not None else None,
                )

        return result

    def answer(
        self,
        query: str,
        *,
        num_search_results: int = 10,
        per_source_search_results: Optional[int] = None,
        num_retrieved_docs: int = 5,
        max_tokens: int = 8000,
        temperature: float = 0.3,
        allow_search: bool = True,
        reference_limit: Optional[int] = None,
        force_search: bool = False,
        images: Optional[List[Dict[str, str]]] = None,
        conversation_id: Optional[str] = None,
        tracer: Optional[Any] = None,
        audit_mode: Optional[str] = None,
        search_depth: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Answer a query using intelligent routing.
        
        This method determines the best approach for answering:
        1. Small talk detection
        2. Domain-specific API routing
        3. Direct LLM answer for simple questions
        4. Web search RAG for current information
        5. Local RAG for document-based queries

        When ``conversation_id`` is supplied and the turn is a follow-up on the
        previous answer, the request resumes the ReAct loop on the checkpointed
        conversation state instead of starting from scratch.
        """
        audit_settings = resolve_audit_settings(self.config)
        audit_override = (audit_mode or "").strip().lower()
        if audit_override == "off":
            audit_active = False
            audit_external = False
        elif audit_override == "external":
            audit_active = True
            audit_external = True
        else:
            audit_active = bool(audit_settings.get("enabled"))
            audit_external = False
        self._audit_settings = audit_settings if audit_active else None
        self._audit_external = audit_external

        # JSON web requests do not provide the SSE tracer. Create one only when
        # audit is enabled so their persisted records still contain workflow steps.
        if audit_active and tracer is None:
            tracer = WorkflowTracer()
        else:
            tracer = ensure_tracer(tracer)
        self._current_tracer = tracer

        timing_recorder = TimingRecorder(enabled=self.show_timings or audit_active)
        timing_recorder.start()

        # Per-request search_depth override (Tavily + Firecrawl).
        applied_search_depth = apply_search_depth_override(
            self.search_client, search_depth
        )

        # Conversation bookkeeping (per-request instance; orchestrator is built fresh)
        self._conversation_id = conversation_id
        self._conversation_query = query
        self._conversation_allow_search = allow_search
        self._current_time_constraint = None
        self._topic_reset = False

        total_limit = max(1, int(num_search_results))
        per_source_limit = max(1, int(per_source_search_results or total_limit))
        force_search = bool(force_search and allow_search)

        # Parse time constraints
        time_constraint = parse_time_constraint(query)
        self._current_time_constraint = time_constraint if time_constraint.days else None
        effective_query = time_constraint.cleaned_query if time_constraint.days else query

        if time_constraint.days:
            current_date = get_current_date_str()
            effective_query = f"{effective_query} (Current Date: {current_date})"

        self._initialize_orchestration(
            query,
            allow_search=allow_search,
            time_constraint=time_constraint,
        )

        # Conversation resume: a follow-up turn continues on the checkpointed state
        resume_result = self._maybe_resume_conversation(
            query=query,
            conversation_id=conversation_id,
            time_constraint=time_constraint,
            num_search_results=total_limit,
            per_source_limit=per_source_limit,
            num_retrieved_docs=num_retrieved_docs,
            max_tokens=max_tokens,
            temperature=temperature,
            allow_search=allow_search,
            reference_limit=reference_limit,
            force_search=force_search,
            timing_recorder=timing_recorder,
            tracer=tracer,
        )
        if resume_result is not None:
            return resume_result

        snapshot = self._snapshot_local_docs()
        has_docs = bool(snapshot)
        
        # Handle images (visual retrieval)
        if images:
            tracer.begin("visual", "图片理解", detail="正在解析图片内容")
            visual_response = self._handle_visual_query(
                query, images, max_tokens, temperature,
                has_docs, allow_search, timing_recorder
            )
            tracer.end("visual", detail="已生成回答")
            return self._finalize_response(visual_response, timing_recorder)

        # Small talk detection
        if self._is_small_talk(effective_query):
            tracer.begin("intent", "意图理解")
            tracer.end("intent", detail="识别为闲聊")
            tracer.begin("generate", "生成回答")
            response = self._handle_small_talk(
                query, max_tokens, temperature,
                has_docs, allow_search, timing_recorder
            )
            tracer.end("generate", detail="模型直答")
            return self._finalize_response(response, timing_recorder)

        # Search disabled
        if not allow_search:
            tracer.begin("intent", "意图理解")
            tracer.end("intent", detail="联网已关闭，使用本地知识")
            self._prepare_query_plan(
                needs_evidence=False,
                has_local_docs=has_docs,
                result_budget=total_limit,
            )
            response = self._handle_local_only(
                query, snapshot, has_docs, num_retrieved_docs,
                max_tokens, temperature, timing_recorder,
                tracer=tracer,
            )
            return self._finalize_response(response, timing_recorder)

        # Domain classification and API handling
        tracer.begin("intent", "意图理解")
        if self._should_select_domain_sources(effective_query):
            domain, sources = self.source_selector.select_sources(
                effective_query, timing_recorder=timing_recorder
            )
        else:
            domain, sources = "general", []
        domain_label = domain if domain and str(domain).lower() != "general" else None

        finance_keywords = ["股价", "stock", "股票", "市值", "market cap", "收益", "revenue",
                           "英伟达", "nvidia", "nvda", "英特尔", "intel", "intc", "amd",
                           "苹果", "apple", "aapl", "微软", "microsoft", "msft"]
        has_finance_keywords = any(kw in query.lower() for kw in finance_keywords)
        structured_domains = {"weather", "transportation", "finance", "sports", "location"}
        domain_api_hint = domain_label if domain_label in structured_domains else None
        if domain_label == "temporal_change" and has_finance_keywords:
            domain_api_hint = "finance"
        analysis = self._current_analysis
        if (
            domain_label == "temporal_change"
            and not has_finance_keywords
            and analysis is not None
            and not analysis.constraints.get("temporal_required")
        ):
            # A source classifier's broad comparison label cannot create a
            # temporal route when shared analysis found no explicit time scope.
            domain = "general"
            domain_label = None
            sources = []
        tracer.end("intent", detail=f"识别领域：{domain_label}" if domain_label else "通用问题")
        enhanced_query = (
            self.source_selector.generate_domain_specific_query(effective_query, domain_api_hint)
            if domain_api_hint
            else effective_query
        )
        
        # Domain selection is only a source hint. The plan keeps it from
        # becoming an untracked side channel and gates critical ambiguity before
        # any provider receives a guessed entity.
        analysis_requires_evidence = bool(
            self._current_analysis and self._current_analysis.requires_evidence
        )
        plan = self._prepare_query_plan(
            needs_evidence=analysis_requires_evidence or bool(domain_api_hint),
            has_local_docs=has_docs,
            domain_hint=domain_api_hint,
            result_budget=total_limit,
        )
        if plan and plan.clarification_required:
            return self._finalize_response(
                self._build_clarification_response(query, has_local_docs=has_docs),
                timing_recorder,
            )

        # For finance queries preserve time expressions that the structured
        # source selector understands. The call itself remains a plan step.
        finance_query = query if has_finance_keywords else effective_query
        domain_api_domain = domain_api_hint or domain
        domain_step = plan.step_for_kind(PlanStepKind.DOMAIN_API) if plan else None
        domain_api_result: Optional[Dict[str, Any]] = None
        tracer.begin("domain_api", "领域数据查询", detail=domain_label or "通用")
        if domain_step is not None and self._current_plan_controller is not None:
            def fetch_domain(step: Any) -> PlanStepResult:
                payload = self.source_selector.fetch_domain_data(
                    finance_query,
                    domain_api_domain,
                    timing_recorder=timing_recorder,
                )
                return PlanStepResult(
                    payload=payload,
                    providers=[f"domain:{domain_api_hint or domain_label or domain or 'general'}"],
                    attempts=[{"provider": f"domain:{domain_api_hint or domain_label or domain or 'general'}", "status": "done"}],
                )

            domain_step_result = self._current_plan_controller.run_step(domain_step, fetch_domain)
            if isinstance(domain_step_result.payload, dict):
                domain_api_result = domain_step_result.payload
            elif domain_step_result.status not in {"skipped", "done"}:
                tracer.error("domain_api", detail=domain_step_result.reason or domain_step_result.status)
        elif not self.orchestration_config["enabled"]:
            domain_api_result = self.source_selector.fetch_domain_data(
                finance_query, domain_api_domain, timing_recorder=timing_recorder
            )
        else:
            tracer.skip("domain_api", "领域数据查询", detail="计划未启用领域数据")

        if domain_api_result and domain_api_result.get("handled"):
            tracer.end("domain_api", detail="已获取领域数据")
        elif domain_step is not None:
            tracer.end("domain_api", detail="无需领域数据", status="skipped")

        should_continue = domain_api_result.get("continue_search", False) if domain_api_result else False

        if domain_api_result and domain_api_result.get("handled") and domain_api_result.get("answer") and not should_continue:
            tracer.begin("domain_answer", "组织领域回答")
            response = self._handle_domain_api(
                query, domain_api_domain, sources, enhanced_query,
                domain_api_result, has_docs, allow_search, force_search, timing_recorder
            )
            if self._current_ledger is not None:
                self._current_ledger.ingest(
                    response.get("evidence_items") or [],
                    default_step_id=domain_step.step_id if domain_step else None,
                )
                self._current_ledger.apply_limits(max_items=plan.result_budget if plan else total_limit)
                response["evidence_items"] = [
                    item for item in self._current_ledger.retained_items() if isinstance(item, dict)
                ]
                if self._current_execution_trace is not None:
                    self._current_execution_trace.record_ledger(self._current_ledger)
            tracer.end("domain_answer", detail=domain_label or str(domain))
            return self._finalize_response(response, timing_recorder)
        
        # Routing decision
        if not force_search:
            tracer.begin("route", "路由决策")
            decision = self._make_routing_decision(effective_query, timing_recorder)
            if (
                not decision.get("needs_search")
                and self._current_analysis is not None
                and self._current_analysis.requires_evidence
            ):
                decision = dict(decision)
                decision["needs_search"] = True
                decision["reason"] = "plan_requires_evidence"
                decision["direct_answer"] = None
            tracer.end(
                "route",
                detail="需要联网检索" if decision["needs_search"] else "无需检索，直接回答",
            )

            if not decision["needs_search"] and decision.get("direct_answer"):
                response = self._build_direct_response(
                    query, decision["direct_answer"], decision,
                    has_docs, allow_search
                )
                return self._finalize_response(response, timing_recorder)

            if not decision["needs_search"]:
                tracer.begin("generate", "生成回答")
                direct = self._direct_answer(query, timing_recorder)
                tracer.end("generate", detail="模型直答")
                response = self._build_direct_response(
                    query, direct.get("content", ""), decision,
                    has_docs, allow_search,
                    llm_raw=direct.get("raw"),
                    llm_error=direct.get("error"),
                )
                return self._finalize_response(response, timing_recorder)
        else:
            tracer.skip("route", "路由决策", detail="已跳过：强制联网")

        # Search is needed
        if (
            self._current_plan is None
            or self._current_plan.step_for_kind(PlanStepKind.WEB_SEARCH) is None
        ):
            plan = self._prepare_query_plan(
                needs_evidence=True,
                has_local_docs=has_docs,
                domain_hint=domain_label,
                result_budget=total_limit,
            )
            if plan and plan.clarification_required:
                return self._finalize_response(
                    self._build_clarification_response(query, has_local_docs=has_docs),
                    timing_recorder,
                )
        if not self.search_client:
            tracer.skip("search", "联网检索", detail="搜索不可用，回退本地模式")
            response = self._handle_search_unavailable(
                query, snapshot, has_docs, num_retrieved_docs,
                max_tokens, temperature, timing_recorder,
                tracer=tracer,
            )
            if force_search:
                response.setdefault("control", {})["force_search_enabled"] = True
            return self._finalize_response(response, timing_recorder)

        # Generate keywords
        tracer.begin("keywords", "生成检索词")
        keyword_info = self._generate_keywords(effective_query, timing_recorder)
        keywords = [str(value).strip() for value in (keyword_info.get("keywords") or []) if str(value).strip()]
        plan_analysis = (
            self._current_plan.analysis
            if self._current_plan is not None
            else self._current_analysis
        )
        deterministic_fallback = (
            deterministic_query_for_plan(plan_analysis)
            if plan_analysis is not None
            else effective_query
        )
        if not keywords:
            fallback_query = deterministic_fallback
            keywords = [fallback_query]
            keyword_info = dict(keyword_info)
            keyword_info["fallback_used"] = True
            keyword_info["fallback_query"] = fallback_query
        search_query = " ".join(keywords).strip() or deterministic_fallback
        if self._current_plan is not None:
            web_step = self._current_plan.step_for_kind(PlanStepKind.WEB_SEARCH)
            if web_step is not None:
                web_step.query = search_query
        if keyword_info.get("fallback_used"):
            error = " ".join(str(keyword_info.get("error") or "").split())[:160]
            detail = f"fallback_used: {fallback_query}"
            if error:
                detail += f" | error: {error}"
            tracer.end("keywords", detail=detail)
        else:
            tracer.end("keywords", detail="、".join(str(k) for k in keywords[:4]))
        
        # Execute search RAG
        result = self._run_primary_rag(
            query=query,
            snapshot=snapshot,
            num_retrieved_docs=num_retrieved_docs,
            max_tokens=max_tokens,
            temperature=temperature,
            timing_recorder=timing_recorder,
            enable_search=True,
            num_search_results=total_limit,
            per_source_limit=per_source_limit,
            reference_limit=reference_limit,
            search_query=search_query,
            freshness=time_constraint.freshness if time_constraint.days else None,
            date_restrict=time_constraint.google_date_restrict if time_constraint.days else None,
            extra_context=domain_api_result.get("answer") if domain_api_result and should_continue else None,
            domain=domain_api_domain,
            domain_result=domain_api_result,
            enable_domain=bool(domain_api_result and should_continue),
            tracer=tracer,
        )
        if not result:
            response = self._handle_search_unavailable(
                query, snapshot, has_docs, num_retrieved_docs,
                max_tokens, temperature, timing_recorder,
                tracer=tracer,
            )
            return self._finalize_response(response, timing_recorder)

        result = self._run_reformulation_recovery(
            result=result,
            query=query,
            snapshot=snapshot,
            num_retrieved_docs=num_retrieved_docs,
            max_tokens=max_tokens,
            temperature=temperature,
            timing_recorder=timing_recorder,
            num_search_results=total_limit,
            per_source_limit=per_source_limit,
            reference_limit=reference_limit,
            freshness=time_constraint.freshness if time_constraint.days else None,
            date_restrict=time_constraint.google_date_restrict if time_constraint.days else None,
            tracer=tracer,
        )
        
        # Add control metadata
        control = {
            "search_performed": True,
            "decision": {"needs_search": True, "reason": "search_required"},
            "search_mode": "search",
            "keywords": keywords,
            "keyword_generation": keyword_info,
            "hybrid_mode": True,
            "local_docs_present": has_docs,
            "search_allowed": True,
            "domain": domain,
            "domain_api": domain_api_hint,
            "selected_sources": sources,
            "enhanced_query": enhanced_query,
            "search_total_limit": total_limit,
            "search_per_source_limit": per_source_limit,
            "search_depth": applied_search_depth,
            "force_search_enabled": force_search,
            "answer_basis": result.get("answer_basis"),
            "limited_evidence_fallback": bool(result.get("limited_evidence_fallback")),
        }
        
        if time_constraint.days:
            control["time_constraint"] = {
                "original_query": time_constraint.original_query,
                "cleaned_query": time_constraint.cleaned_query,
                "time_expression": time_constraint.time_expression,
                "days": time_constraint.days,
            }
        
        result["control"] = control
        result["search_query"] = search_query

        result = self._apply_postcheck(
            query=query,
            result=result,
            control=control,
            time_constraint=time_constraint,
            domain_api_result=domain_api_result,
            num_search_results=total_limit,
            per_source_limit=per_source_limit,
            num_retrieved_docs=num_retrieved_docs,
            max_tokens=max_tokens,
            temperature=temperature,
            reference_limit=reference_limit,
            force_search=force_search,
            timing_recorder=timing_recorder,
            tracer=tracer,
        )

        return self._finalize_response(result, timing_recorder)

    def _handle_visual_query(
        self,
        query: str,
        images: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
        has_docs: bool,
        allow_search: bool,
        timing_recorder: Optional[TimingRecorder],
    ) -> Dict[str, Any]:
        """Handle queries with images."""
        # Check if LLM supports vision
        vision_keywords = ["grok", "gpt-4", "claude", "gemini", "glm-4v", "glm-4.5v", "claude-4.5-haiku", "vision", "minimax"]
        is_vision_model = any(k in self.llm.model_name.lower() if hasattr(self.llm, 'model_name') else '' for k in vision_keywords)
        
        # Try to get visual metadata from Google Vision API if available
        visual_context = ""
        if self.google_api_key:
            try:
                visual_info = self._perform_visual_retrieval(images, timing_recorder)
                if visual_info:
                    labels = [label.get("label") for label in visual_info.get("bestGuessLabels", [])]
                    entities = [entity.get("description") for entity in visual_info.get("webEntities", []) if entity.get("description")]
                    
                    visual_context = (
                        "我通过搜索引擎找到了关于这张图片的以下线索（元数据）：\n\n"
                        f"最佳猜测标签：[{', '.join(labels)}]\n\n"
                        f"关联实体：[{', '.join(entities)}]\n\n"
                        "请结合图片内容和上述线索回答用户的问题。如果线索不足，请诚实告知。请始终使用与用户提问相同的语言回答。"
                    )
            except Exception as e:
                print(f"Visual retrieval failed: {e}")
        
        if is_vision_model:
            system_prompt = "你是一个智能视觉助手。用户上传了一张图片。"
            if visual_context:
                system_prompt += "\n\n" + visual_context
            else:
                system_prompt += "\n请结合图片内容回答用户的问题。请始终使用与用户提问相同的语言回答。"
        else:
            system_prompt = "你是一个智能助手。用户上传了图片，但你无法查看图片内容。"
            if visual_context:
                system_prompt += "\n\n" + visual_context
                system_prompt += "\n\n虽然你无法直接查看图片，但可以根据上述元数据信息尝试回答用户的问题。请始终使用与用户提问相同的语言回答。"
            else:
                system_prompt += "\n请明确告知用户你无法查看图片，并询问他们是否可以描述图片内容或提供其他相关信息。请始终使用与用户提问相同的语言回答。"
        
        direct = self._direct_answer(
            query, timing_recorder, images=images, system_prompt=system_prompt
        )
        
        return {
            "query": query,
            "answer": direct.get("content", ""),
            "search_hits": [],
            "llm_raw": direct.get("raw"),
            "llm_error": direct.get("error"),
            "control": {
                "search_performed": False,
                "decision": {"needs_search": False, "reason": "image_content_present"},
                "search_mode": "image_content_present",
                "local_docs_present": has_docs,
                "search_allowed": allow_search,
            },
        }

    def _perform_visual_retrieval(self, images: List[Dict[str, str]], timing_recorder: Optional[TimingRecorder]) -> Optional[Dict[str, Any]]:
        if not self.google_api_key or not images:
            return None
        
        start = time.perf_counter()
        try:
            # Use the first image for visual retrieval
            img = images[0]
            b64_content = img.get("base64", "")
            if "," in b64_content:
                b64_content = b64_content.split(",")[1]
            
            url = f"https://vision.googleapis.com/v1/images:annotate?key={self.google_api_key}"
            payload = {
                "requests": [
                    {
                        "image": {
                            "content": b64_content
                        },
                        "features": [
                            {
                                "type": "WEB_DETECTION"
                            }
                        ]
                    }
                ]
            }
            
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            result = response.json()
            
            responses = result.get("responses", [])
            if responses:
                web_detection = responses[0].get("webDetection", {})
                return web_detection
            return None
            
        except Exception as e:
            print(f"Google Cloud Vision API error: {e}")
            return None
        finally:
            if timing_recorder:
                duration_ms = (time.perf_counter() - start) * 1000
                timing_recorder.record_tool_call(
                    tool="google_vision",
                    duration_ms=duration_ms,
                    success=True # Simplified
                )

    def _handle_small_talk(
        self,
        query: str,
        max_tokens: int,
        temperature: float,
        has_docs: bool,
        allow_search: bool,
        timing_recorder: Optional[TimingRecorder],
    ) -> Dict[str, Any]:
        """Handle small talk queries."""
        direct = self._direct_answer(query, timing_recorder)
        
        return {
            "query": query,
            "answer": direct.get("content", ""),
            "search_hits": [],
            "llm_raw": direct.get("raw"),
            "llm_error": direct.get("error"),
            "control": {
                "search_performed": False,
                "decision": {"needs_search": False, "reason": "small_talk_heuristic"},
                "search_mode": "small_talk",
                "local_docs_present": has_docs,
                "search_allowed": allow_search,
            },
        }

    def _handle_local_only(
        self,
        query: str,
        snapshot: Optional[tuple],
        has_docs: bool,
        num_retrieved_docs: int,
        max_tokens: int,
        temperature: float,
        timing_recorder: Optional[TimingRecorder],
        tracer: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Handle local-only queries (search disabled)."""
        if not has_docs:
            pipeline_result = self._run_primary_rag(
                query=query,
                snapshot=snapshot,
                num_retrieved_docs=num_retrieved_docs,
                max_tokens=max_tokens,
                temperature=temperature,
                timing_recorder=timing_recorder,
                enable_search=False,
                tracer=tracer,
            )
            if pipeline_result:
                pipeline_result["control"] = {
                    "search_performed": False,
                    "decision": {"needs_search": False, "reason": "search_disabled"},
                    "search_mode": "local_rag",
                    "local_docs_present": False,
                    "search_allowed": False,
                }
                return pipeline_result

        pipeline_result = self._run_primary_rag(
            query=query,
            snapshot=snapshot,
            num_retrieved_docs=num_retrieved_docs,
            max_tokens=max_tokens,
            temperature=temperature,
            timing_recorder=timing_recorder,
            enable_search=False,
            tracer=tracer,
        )
        if not pipeline_result:
            direct = self._direct_answer(query, timing_recorder)
            return {
                "query": query,
                "answer": direct.get("content", ""),
                "search_hits": [],
                "llm_raw": direct.get("raw"),
                "llm_error": direct.get("error"),
                "control": {
                    "search_performed": False,
                    "decision": {"needs_search": False, "reason": "search_disabled"},
                    "search_mode": "direct_llm",
                    "local_docs_present": True,
                    "search_allowed": False,
                },
            }

        pipeline_result["control"] = {
            "search_performed": False,
            "decision": {"needs_search": False, "reason": "search_disabled"},
            "search_mode": "local_rag",
            "local_docs_present": has_docs,
            "search_allowed": False,
        }

        return pipeline_result

    def _handle_domain_api(
        self,
        query: str,
        domain: str,
        sources: List[Dict[str, Any]],
        enhanced_query: str,
        domain_api_result: Dict[str, Any],
        has_docs: bool,
        allow_search: bool,
        force_search: bool,
        timing_recorder: Optional[TimingRecorder],
    ) -> Dict[str, Any]:
        """Handle domain-specific API responses."""
        answer = domain_api_result.get("answer", "")
        domain_source = DomainEvidenceSource(self.source_selector)
        domain_step = (
            self._current_plan.step_for_kind(PlanStepKind.DOMAIN_API)
            if self._current_plan is not None
            else None
        )
        domain_items = domain_source.retrieve(
            query,
            RetrievalOptions(
                metadata={
                    "domain": domain,
                    "domain_result": domain_api_result,
                    "originating_plan_step": domain_step.step_id if domain_step else None,
                    "source_tier": "authoritative",
                    "retrieval_kind": "domain_api",
                }
            ),
        )
        
        # Enhance with LLM if data available
        domain_data = domain_api_result.get("data")
        if domain_data:
            enhanced = self._enhance_domain_answer(
                query, domain, domain_data, timing_recorder
            )
            if enhanced.get("content"):
                answer = enhanced["content"]
        
        return {
            "query": query,
            "answer": answer,
            "search_hits": [],
            "domain_data": domain_data,
            "llm_raw": None,
            "evidence_items": [item.to_dict() for item in domain_items],
            "evidence_summary": build_evidence_summary(domain_items),
            "evidence_sources_active": [domain_source.describe_with_domain(domain)],
            "evidence_sources_used": [
                {
                    "source_type": "domain",
                    "source_id": f"domain:{domain}",
                    "reference": domain_api_result.get("endpoint") or domain_api_result.get("provider") or f"domain:{domain}",
                }
            ] if domain_items else [],
            "evidence_source_types_active": ["domain"],
            "evidence_source_types_used": ["domain"] if domain_items else [],
            "control": {
                "search_performed": False,
                "decision": {"needs_search": False, "reason": f"domain_api_{domain}"},
                "search_mode": "domain_api",
                "domain": domain,
                "selected_sources": sources,
                "enhanced_query": enhanced_query,
                "local_docs_present": has_docs,
                "search_allowed": allow_search,
                "force_search_enabled": force_search,
            },
        }

    def _enhance_domain_answer(
        self,
        query: str,
        domain: str,
        domain_data: Any,  # Can be Dict or List for finance
        timing_recorder: Optional[TimingRecorder],
    ) -> Dict[str, Any]:
        """Enhance domain API answer with LLM."""
        prompts = {}
        
        # Handle weather domain - only if domain_data is a dict
        if domain == "weather" and isinstance(domain_data, dict):
            prompts["weather"] = (
                "你是天气助手。根据实时数据，给出自然、丰富的回复，包括当前状况、穿衣/出行建议。",
                f"位置：{domain_data.get('location', {}).get('formatted_address', '未知')}\n"
                f"概况：{domain_data.get('weatherCondition', {}).get('description', {}).get('text', '未知')}\n"
                f"温度：{domain_data.get('temperature', {}).get('degrees', '未知')}°C"
            )
        elif domain == "weather":
            # Fallback for weather when data is not a dict
            prompts["weather"] = (
                "你是天气助手。根据提供的天气数据，给出自然、丰富的回复。",
                f"天气数据：{json.dumps(domain_data, ensure_ascii=False, indent=2)}"
            )
        
        # Handle finance domain - works with both dict and list
        if domain == "finance":
            prompts["finance"] = (
                "你是金融助手。根据提供的股票数据，分析价格走势和表现。如果包含多只股票，请进行对比。",
                json.dumps(domain_data, ensure_ascii=False, indent=2)
            )
        
        if domain not in prompts:
            return {"content": ""}
        
        system_prompt, data_summary = prompts[domain]
        user_prompt = f"查询：{query}\n数据：\n{data_summary}\n生成回复："
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        
        start = time.perf_counter()
        try:
            response = self.llm.invoke(messages, max_tokens=300, temperature=0.3)
            return {"content": response.content if hasattr(response, 'content') else str(response)}
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            if timing_recorder:
                duration_ms = (time.perf_counter() - start) * 1000
                timing_recorder.record_llm_call(
                    label=f"domain_enhance_{domain}",
                    duration_ms=duration_ms,
                    provider=getattr(self.llm, "provider", None),
                    model=getattr(self.llm, "model_name", None),
                )

    def _handle_search_unavailable(
        self,
        query: str,
        snapshot: Optional[tuple],
        has_docs: bool,
        num_retrieved_docs: int,
        max_tokens: int,
        temperature: float,
        timing_recorder: Optional[TimingRecorder],
        tracer: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Handle case where search is requested but unavailable."""
        result = self._run_primary_rag(
            query=query,
            snapshot=snapshot,
            num_retrieved_docs=num_retrieved_docs,
            max_tokens=max_tokens,
            temperature=temperature,
            timing_recorder=timing_recorder,
            enable_search=False,
            tracer=tracer,
        )
        if result is None:
            result = self._handle_local_only(
                query, snapshot, has_docs, num_retrieved_docs,
                max_tokens, temperature, timing_recorder,
                tracer=tracer,
            )
        control = result.setdefault("control", {})
        control["search_mode"] = "search_unavailable"
        control["search_allowed"] = True
        return result

    def _build_direct_response(
        self,
        query: str,
        answer: str,
        decision: Dict[str, Any],
        has_docs: bool,
        allow_search: bool,
        llm_raw: Any = None,
        llm_error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build response for direct answer."""
        return {
            "query": query,
            "answer": answer,
            "search_hits": [],
            "llm_raw": llm_raw,
            "llm_error": llm_error,
            "control": {
                "search_performed": False,
                "decision": decision,
                "search_mode": "direct_llm",
                "local_docs_present": has_docs,
                "search_allowed": allow_search,
            },
        }

    def _finalize_response(
        self,
        result: Dict[str, Any],
        timing_recorder: TimingRecorder,
    ) -> Dict[str, Any]:
        """Finalize response with timing and metadata."""
        control = result.get("control", {})
        if "postcheck" not in control:
            control["postcheck"] = {
                "eligible": False,
                "skipped_reason": "non_search_path",
                "rule_hits": [],
                "judge_used": False,
                "judge_error": None,
                "passes_postcheck": True,
                "should_fallback_to_react": False,
                "recoverable": False,
                "failure_types": [],
                "missing_constraints": [],
                "evidence_sufficiency": "unknown",
                "reason": "postcheck_not_applicable",
            }
        control.setdefault("fallback_triggered", False)
        control.setdefault("final_executor", "default_pipeline")
        
        # Add search source metadata
        control.setdefault("search_sources_requested", self.requested_search_sources)
        control.setdefault("search_sources_active", self.active_search_sources)
        control.setdefault("search_sources_configured", self.configured_search_sources)
        if self.missing_search_sources:
            control.setdefault("search_sources_missing", self.missing_search_sources)
        control.setdefault("evidence_sources_active", result.get("evidence_sources_active") or [])
        control.setdefault("evidence_sources_used", result.get("evidence_sources_used") or [])
        control.setdefault("evidence_source_types_active", result.get("evidence_source_types_active") or [])
        control.setdefault("evidence_source_types_used", result.get("evidence_source_types_used") or [])
        
        result["control"] = control

        plan = self._current_plan
        if (
            plan is not None
            and plan.analysis.requires_evidence
            and self._current_verification is None
        ):
            self._verify_current_plan(result)
        if (
            self.orchestration_config["enforce_verification"]
            and self._current_verification is not None
            and self._current_verification.status == VerificationStatus.EVIDENCE_INSUFFICIENT
            and bool((result.get("control") or {}).get("search_performed"))
            and (
                not self._has_limited_evidence_answer(result)
                or self._requires_target_official_coverage()
            )
        ):
            result = self._mark_evidence_insufficient(
                result,
                result.setdefault("control", {}),
                self._current_verification,
            )
        self._attach_orchestration_metadata(result)
        
        # Add timing information
        if timing_recorder.enabled:
            timing_recorder.stop()
            timing_payload = timing_recorder.to_dict()
            if timing_payload:
                domain = control.get("domain", "")
                timing_payload["领域智能类型"] = domain if domain and domain.lower() != "general" else "无"
                result["response_times"] = timing_payload

        self._record_conversation_turn(result)
        self._record_audit_turn(result)
        return result

    # ------------------------------------------------------------------
    # Conversation resume
    # ------------------------------------------------------------------
    def _record_conversation_turn(self, result: Dict[str, Any]) -> None:
        """Persist this turn to the conversation record (all execution paths).

        The full ``result`` is stored so the sidebar can fully restore the turn
        (workflow metadata, sources, retrieved docs, timings and warnings).
        """
        cid = getattr(self, "_conversation_id", None)
        if not cid:
            return
        try:
            from orchestrators.conversation_store import get_conversation_manager

            mgr = get_conversation_manager()
            if not mgr.enabled:
                return
            mgr.record_turn(
                cid,
                getattr(self, "_conversation_query", ""),
                result.get("answer", "") or "",
                getattr(self, "_current_time_constraint", None),
                topic_reset=getattr(self, "_topic_reset", False),
                result=result if isinstance(result, dict) else None,
            )
        except Exception as exc:  # noqa: BLE001 - recording must never break a response
            print(f"[conversation] record_turn failed: {exc}")

    def _record_audit_turn(self, result: Dict[str, Any]) -> None:
        """Persist this turn's process audit when enabled (all execution paths)."""
        settings = getattr(self, "_audit_settings", None)
        if not settings or getattr(self, "_audit_external", False):
            return
        try:
            tracer = getattr(self, "_current_tracer", None)
            events = list(getattr(tracer, "events", None) or [])
            recorder = AuditRecorder(
                settings.get("dir"),
                include_answer=bool(settings.get("include_answer", True)),
                include_full_result=bool(settings.get("include_full_result", False)),
                max_files=int(settings.get("max_files", 200)),
                max_bytes_per_record=int(settings.get("max_bytes_per_record", 65536)),
            )
            recorder.record_turn(
                conversation_id=getattr(self, "_conversation_id", None),
                query=getattr(self, "_conversation_query", "") or "",
                allow_search=bool(getattr(self, "_conversation_allow_search", True)),
                events=events,
                result=result,
            )
        except Exception as exc:  # noqa: BLE001 - audit must never break a response
            print(f"[audit] record failed: {exc}")

    def _classify_followup_intent(self, query: str, recent_turns: List[Dict[str, Any]]) -> str:
        """Classify a follow-up turn as ``continuation`` or ``new_topic``.

        Defaults to ``continuation`` on any failure so context is retained.
        """
        if not recent_turns:
            return "new_topic"
        history_lines: List[str] = []
        for turn in recent_turns[-4:]:
            history_lines.append(f"Q: {turn.get('query', '')}")
            answer = turn.get("answer") or ""
            if answer:
                history_lines.append(f"A: {answer[:200]}")
        history = "\n".join(history_lines)
        system_prompt = (
            "你是对话意图判别器。根据对话历史判断新问题属于「continuation」（延续上一轮主题、"
            "修改/补充/追问上一轮回答）还是「new_topic」（全新话题）。"
            "只输出一个词：continuation 或 new_topic。"
        )
        user_prompt = f"对话历史：\n{history}\n\n新问题：{query}"
        try:
            response = self.routing_llm.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
            )
            content = (response.content if hasattr(response, "content") else str(response)).strip().lower()
            if "new" in content and "topic" in content:
                return "new_topic"
            return "continuation"
        except Exception:  # noqa: BLE001 - default to continuation
            return "continuation"

    def _maybe_resume_conversation(
        self,
        *,
        query: str,
        conversation_id: Optional[str],
        time_constraint: TimeConstraint,
        num_search_results: int,
        per_source_limit: int,
        num_retrieved_docs: int,
        max_tokens: int,
        temperature: float,
        allow_search: bool,
        reference_limit: Optional[int],
        force_search: bool,
        timing_recorder: TimingRecorder,
        tracer: Optional[Any],
    ) -> Optional[Dict[str, Any]]:
        """Route a follow-up turn to the ReAct loop, resuming state if present.

        Returns ``None`` when the turn is not a conversation follow-up so normal
        routing proceeds.
        """
        if not conversation_id:
            return None
        try:
            from orchestrators.conversation_store import get_conversation_manager

            mgr = get_conversation_manager()
        except Exception as exc:  # noqa: BLE001 - degrade to normal routing
            print(f"[conversation] manager unavailable: {exc}")
            return None
        if not mgr.enabled:
            return None

        last_turn = mgr.get_last_turn(conversation_id)
        if not last_turn:
            return None  # first turn of this conversation -> normal routing

        intent = self._classify_followup_intent(query, mgr.get_recent_turns(conversation_id))
        if intent == "new_topic":
            # Fresh topic: discard the stale agent state, keep the dialogue log.
            mgr.delete_checkpoint(conversation_id)
            self._topic_reset = True
            return None

        # Continuation: build follow-up context from the conversation record.
        previous_answer = str(last_turn.get("answer") or "").strip()
        inherited_constraint = None
        if not time_constraint.days:
            inherited = mgr.get_inherited_time_constraint(conversation_id)
            if inherited:
                inherited_constraint = (
                    f"{inherited.get('time_expression') or ''} "
                    f"(约 {inherited.get('days')} 天内)".strip()
                )
                self._current_time_constraint = inherited

        fallback_context: Dict[str, Any] = {
            "previous_answer": previous_answer,
            "user_feedback": query,
            "missing_constraints": [],
            "failure_types": [],
            "evidence_summary": "",
            "recovery_goal": f"根据用户反馈调整上一轮回答：{query}",
            "inherited_time_constraint": inherited_constraint,
            "judge_source": "human_feedback",
        }

        react_orchestrator = self._get_react_fallback_orchestrator()
        if react_orchestrator is None:
            return None  # no react path available -> fall back to normal routing

        tracer.begin("conversation_resume", "会话续跑", detail="基于上轮调整")
        response = react_orchestrator.answer(
            query,
            num_search_results=num_search_results,
            per_source_search_results=per_source_limit,
            num_retrieved_docs=num_retrieved_docs,
            max_tokens=max_tokens,
            temperature=temperature,
            allow_search=allow_search,
            reference_limit=reference_limit,
            force_search=force_search,
            fallback_context=fallback_context,
            conversation_id=conversation_id,
            tracer=tracer,
        )
        tracer.end("conversation_resume", detail="续跑完成")

        # Ensure control exposes conversation metadata and record the turn.
        control = response.setdefault("control", {})
        control.setdefault("conversation_id", conversation_id)
        control["conversation_resumed"] = bool(control.get("conversation_resumed"))
        control.setdefault("judge_source", "human_feedback")
        response["control"] = control
        self._record_conversation_turn(response.get("answer", ""))
        response = self._attach_timing(response, timing_recorder)
        self._attach_orchestration_metadata(response)
        self._record_audit_turn(response)
        return response

    def _attach_timing(self, result: Dict[str, Any], timing_recorder: TimingRecorder) -> Dict[str, Any]:
        """Attach timing payload to a delegated result without double-counting."""
        if timing_recorder.enabled:
            timing_recorder.stop()
            payload = timing_recorder.to_dict()
            if payload:
                payload["领域智能类型"] = "会话续跑"
                result["response_times"] = payload
        return result

    def _format_evidence_summary(
        self,
        search_hits: List[Dict[str, Any]],
        retrieved_docs: List[Dict[str, Any]],
        domain_answer: Optional[str],
        evidence_items: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Build a concise evidence summary for ReAct fallback input."""
        if evidence_items:
            lines: List[str] = []
            for index, item in enumerate(evidence_items[:8], start=1):
                lines.append(
                    f"{index}. [{source_identity_label(item.get('source_type'), item.get('source_id'))}] "
                    f"{item.get('title') or item.get('reference') or 'Evidence'} | "
                    f"{item.get('reference') or 'N/A'} | "
                    f"{item.get('snippet') or str(item.get('content') or '')[:180]}"
                )
            summary = "\n".join(lines).strip()
            if summary:
                return summary

        lines: List[str] = []
        if domain_answer:
            lines.append(f"Domain Context:\n{domain_answer}")
        if search_hits:
            lines.append("Search Hits:")
            for index, hit in enumerate(search_hits[:5], start=1):
                lines.append(
                    f"{index}. {hit.get('title') or f'Result {index}'} | "
                    f"{hit.get('url') or 'N/A'} | {hit.get('snippet') or ''}"
                )
        if retrieved_docs:
            lines.append("Local Documents:")
            for index, doc in enumerate(retrieved_docs[:3], start=1):
                source = doc.get("source") or f"Document {index}"
                content_preview = str(doc.get("content") or "")[:200]
                lines.append(f"{index}. {source} | {content_preview}")
        return "\n".join(lines).strip()

    def _build_recovery_goal(self, verdict: PostcheckVerdict) -> str:
        """Translate verdict into a concise recovery objective for ReAct."""
        parts: List[str] = []
        if verdict.failure_types:
            parts.append(f"Address these failure types: {', '.join(verdict.failure_types)}.")
        if verdict.missing_constraints:
            parts.append(f"Explicitly cover these missing constraints: {', '.join(verdict.missing_constraints)}.")
        if verdict.evidence_sufficiency == "insufficient":
            parts.append("Gather more evidence before answering.")
        parts.append("Return a corrected final answer grounded in available evidence.")
        return " ".join(parts)

    def _coerce_bool(self, value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "y", "on"}:
                return True
            if lowered in {"false", "0", "no", "n", "off"}:
                return False
        return default

    def _parse_json_payload(self, content: str) -> Optional[Dict[str, Any]]:
        content = (content or "").strip()
        if not content:
            return None
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            start_idx = content.find("{")
            end_idx = content.rfind("}") + 1
            if start_idx != -1 and end_idx > start_idx:
                try:
                    parsed = json.loads(content[start_idx:end_idx])
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    return None
        return None

    def _run_postcheck_judge(
        self,
        *,
        query: str,
        answer: str,
        verdict: PostcheckVerdict,
        result: Dict[str, Any],
        timing_recorder: Optional[TimingRecorder],
    ) -> Optional[Dict[str, Any]]:
        """Ask an LLM judge whether the search answer should escalate to ReAct."""
        if not self.postcheck_config["judge"]["enabled"]:
            return None

        search_hits = result.get("search_hits") or []
        retrieved_docs = result.get("retrieved_docs") or []
        search_hit_preview = json.dumps(search_hits[:4], ensure_ascii=False)
        retrieved_doc_preview = json.dumps(retrieved_docs[:3], ensure_ascii=False)
        rule_hits_preview = json.dumps(verdict.rule_hits, ensure_ascii=False)

        system_prompt = (
            "You are a strict post-check judge for a search pipeline. "
            "Return JSON only with keys: "
            "passes_postcheck, should_fallback_to_react, recoverable, "
            "failure_types, missing_constraints, evidence_sufficiency, reason."
        )
        user_prompt = (
            f"Query:\n{query}\n\n"
            f"Answer:\n{answer}\n\n"
            f"Rule Screen Hits:\n{rule_hits_preview}\n\n"
            f"Search Hits:\n{search_hit_preview}\n\n"
            f"Retrieved Docs:\n{retrieved_doc_preview}\n\n"
            "Judge whether the answer already satisfies the user's request. "
            "Only set should_fallback_to_react=true when the failure is recoverable through multi-step tool use. "
            "Set recoverable=false for unavailable data, unavailable services, or when the answer already honestly says evidence is insufficient."
        )

        start = time.perf_counter()
        try:
            response = self.postcheck_llm.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
            )
            content = response.content if hasattr(response, "content") else str(response)
            return self._parse_json_payload(content)
        except Exception as exc:
            verdict.judge_error = str(exc)
            return None
        finally:
            if timing_recorder:
                duration_ms = (time.perf_counter() - start) * 1000
                timing_recorder.record_llm_call(
                    label="postcheck_judge",
                    duration_ms=duration_ms,
                    provider=getattr(self.postcheck_llm, "provider", None),
                    model=getattr(self.postcheck_llm, "model_name", None),
                )

    def _get_react_fallback_orchestrator(self) -> Optional[Any]:
        """Lazily create the ReAct fallback orchestrator."""
        if self._react_fallback_orchestrator is not None:
            return self._react_fallback_orchestrator
        if not self.search_client:
            return None
        from orchestrators.react_agent_orchestrator import ReactAgentOrchestrator

        self._react_fallback_orchestrator = ReactAgentOrchestrator.create_from_config(
            config=self.config,
            llm=self.llm,
            search_client=self.search_client,
            data_path=self.data_path,
            max_iterations=self.postcheck_config["react_fallback"]["max_iterations"],
            show_timings=self.show_timings,
            engine=self.postcheck_config["react_fallback"].get("engine"),
            judge_llm=self.postcheck_llm,
        )
        return self._react_fallback_orchestrator

    def _apply_postcheck(
        self,
        *,
        query: str,
        result: Dict[str, Any],
        control: Dict[str, Any],
        time_constraint: TimeConstraint,
        domain_api_result: Optional[Dict[str, Any]],
        num_search_results: int,
        per_source_limit: int,
        num_retrieved_docs: int,
        max_tokens: int,
        temperature: float,
        reference_limit: Optional[int],
        force_search: bool,
        timing_recorder: Optional[TimingRecorder],
        tracer: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Run post-check and optionally escalate to ReAct fallback."""
        tracer = ensure_tracer(tracer)
        plan_outcome = self._verify_current_plan(result)
        limited_evidence_answer = (
            self._has_limited_evidence_answer(result)
            and not self._requires_target_official_coverage()
        )
        if result.get("answer_basis"):
            control["answer_basis"] = result["answer_basis"]
        if plan_outcome is not None:
            control["verification"] = plan_outcome.to_dict()
            if plan_outcome.status == VerificationStatus.CLARIFICATION_REQUIRED:
                return self._build_clarification_response(
                    query,
                    has_local_docs=bool(control.get("local_docs_present")),
                )
        postcheck_meta: Dict[str, Any]
        if not self.postcheck_config["enabled"]:
            tracer.skip("postcheck", "质量校验", detail="未启用")
            postcheck_meta = {
                "eligible": False,
                "skipped_reason": "disabled",
                "rule_hits": [],
                "judge_used": False,
                "judge_error": None,
                "passes_postcheck": True,
                "should_fallback_to_react": False,
                "recoverable": False,
                "failure_types": [],
                "missing_constraints": [],
                "evidence_sufficiency": "unknown",
                "reason": "postcheck_disabled",
            }
            control["postcheck"] = postcheck_meta
            control["fallback_triggered"] = False
            control["final_executor"] = "default_pipeline"
            if (
                self.orchestration_config["enforce_verification"]
                and plan_outcome is not None
                and plan_outcome.status == VerificationStatus.EVIDENCE_INSUFFICIENT
                and not limited_evidence_answer
            ):
                return self._mark_evidence_insufficient(result, control, plan_outcome)
            return result

        verdict = screen_search_answer(
            query=query,
            answer=str(result.get("answer") or ""),
            search_hits=result.get("search_hits") or [],
            retrieved_docs=result.get("retrieved_docs") or [],
            time_constraint=time_constraint,
            search_error=result.get("search_error"),
        )

        if verdict.rule_hits and verdict.recoverable:
            judge_payload = self._run_postcheck_judge(
                query=query,
                answer=str(result.get("answer") or ""),
                verdict=verdict,
                result=result,
                timing_recorder=timing_recorder,
            )
            if judge_payload:
                verdict = merge_judge_verdict(verdict, judge_payload)
            elif verdict.judge_error:
                verdict.reason = "judge_error"

        postcheck_meta = verdict.to_dict()
        if self.postcheck_config["log_verdicts"]:
            print(f"[postcheck] {json.dumps(postcheck_meta, ensure_ascii=False)}")
        control["postcheck"] = postcheck_meta

        fallback_enabled = self.postcheck_config["react_fallback"]["enabled"]
        verification_blocks_react = bool(
            plan_outcome is not None
            and plan_outcome.status in {
                VerificationStatus.CLARIFICATION_REQUIRED,
                VerificationStatus.EVIDENCE_INSUFFICIENT,
            }
        )
        if (
            not verdict.should_fallback_to_react
            or not verdict.recoverable
            or not fallback_enabled
            or verification_blocks_react
        ):
            passes = getattr(verdict, "passes_postcheck", True)
            tracer.end("postcheck", detail="通过" if passes else "未通过，保持原答案")
            control["fallback_triggered"] = False
            control["final_executor"] = "default_pipeline"
            if verdict.should_fallback_to_react and not fallback_enabled:
                control["fallback_reason"] = "react_fallback_disabled"
            if verification_blocks_react:
                control["fallback_reason"] = "plan_verification_nonrecoverable"
            if (
                self.orchestration_config["enforce_verification"]
                and plan_outcome is not None
                and plan_outcome.status == VerificationStatus.EVIDENCE_INSUFFICIENT
                and not limited_evidence_answer
            ):
                return self._mark_evidence_insufficient(result, control, plan_outcome)
            return result

        fallback_orchestrator = self._get_react_fallback_orchestrator()
        if fallback_orchestrator is None:
            tracer.end("postcheck", detail="未通过，但回退不可用")
            control["fallback_triggered"] = False
            control["fallback_reason"] = "react_fallback_unavailable"
            control["final_executor"] = "default_pipeline"
            if (
                self.orchestration_config["enforce_verification"]
                and plan_outcome is not None
                and plan_outcome.status == VerificationStatus.EVIDENCE_INSUFFICIENT
                and not limited_evidence_answer
            ):
                return self._mark_evidence_insufficient(result, control, plan_outcome)
            return result

        tracer.end("postcheck", detail="未通过，转入深度检索")
        tracer.begin("react", "深度检索恢复", detail=verdict.reason or "postcheck_fallback")

        evidence_summary = self._format_evidence_summary(
            result.get("search_hits") or [],
            result.get("retrieved_docs") or [],
            (domain_api_result or {}).get("answer"),
            result.get("evidence_items") or [],
        )
        fallback_context = {
            "previous_answer": result.get("answer"),
            "failure_types": verdict.failure_types,
            "missing_constraints": verdict.missing_constraints,
            "evidence_summary": evidence_summary,
            "recovery_goal": self._build_recovery_goal(verdict),
            "search_hits": result.get("search_hits") or [],
            "evidence_items": result.get("evidence_items") or [],
            "evidence_sources_active": result.get("evidence_sources_active") or [],
            "evidence_sources_used": result.get("evidence_sources_used") or [],
            "evidence_source_types_active": result.get("evidence_source_types_active") or [],
            "evidence_source_types_used": result.get("evidence_source_types_used") or [],
        }
        fallback_result = fallback_orchestrator.answer(
            query,
            num_search_results=num_search_results,
            per_source_search_results=per_source_limit,
            num_retrieved_docs=num_retrieved_docs,
            max_tokens=max_tokens,
            temperature=temperature,
            allow_search=True,
            reference_limit=reference_limit,
            force_search=force_search,
            fallback_context=fallback_context,
            tracer=tracer,
        )
        max_iterations = self.postcheck_config["react_fallback"]["max_iterations"]
        tracer.end("react", detail=f"最多 {max_iterations} 轮迭代")
        if self._current_execution_trace is not None:
            self._current_execution_trace.record_recovery(
                executor="react_fallback",
                status="done",
                reason=verdict.reason,
            )
        fallback_control = fallback_result.setdefault("control", {})
        fallback_control["postcheck"] = postcheck_meta
        fallback_control["fallback_triggered"] = True
        fallback_control["fallback_reason"] = verdict.reason
        fallback_control["final_executor"] = "react_fallback"
        return fallback_result

    @staticmethod
    def create_react_agent(
        llm: BaseChatModel,
        tools: List[Any],
        max_iterations: int = 5,
        react_prompt: Optional[str] = None,
    ) -> Any:
        """Create a LangChain ReAct agent.

        Args:
            llm: LangChain chat model
            tools: List of LangChain BaseTool instances
            max_iterations: Maximum number of agent iterations
            react_prompt: Optional custom ReAct prompt (Chinese-optimized)

        Returns:
            AgentExecutor instance ready to run
        """
        try:
            from langchain.agents import create_react_agent, AgentExecutor
        except ModuleNotFoundError:
            from langchain_classic.agents import create_react_agent, AgentExecutor

        # Default ReAct prompt (English)
        default_react_prompt = """You are a helpful assistant.

You have access to the following tools:

{tools}

To use a tool, respond in the following format:

```
Thought: the assistant thinks about what to do
Action: the name of the tool to use (only one of: {tool_names})
Action Input: the input to the tool
Observation: the result of the tool
```

When you have a response for the user, respond in this format instead:

```
Thought: I have gathered enough information to answer the user's question.
Final Answer: [your response here]
```

Begin!"""

        # Chinese-optimized ReAct prompt
        chinese_react_prompt = """你是一个智能助手。

你可以使用以下工具：

{tools}

使用工具的格式：

```
Thought: 思考应该做什么
Action: 工具名称（只能使用以下工具之一: {tool_names}）
Action Input: 工具的输入
Observation: 工具的返回结果
```

当你收集到足够的信息时，用以下格式回答：

```
Thought: 我已经收集到足够的信息来回答用户的问题。
Final Answer: [你的回答]
```

开始！"""

        prompt_template = react_prompt or chinese_react_prompt

        if isinstance(prompt_template, str):
            from langchain_core.prompts import PromptTemplate

            template_text = prompt_template
            if "{input}" not in template_text:
                template_text += "\n\nQuestion: {input}"
            if "{agent_scratchpad}" not in template_text:
                template_text += "\n{agent_scratchpad}"
            prompt_template = PromptTemplate(
                template=template_text,
                input_variables=["input", "agent_scratchpad", "tools", "tool_names"],
            )

        agent = create_react_agent(llm, tools, prompt_template)
        return AgentExecutor.from_agent_and_tools(
            agent,
            tools,
            max_iterations=max_iterations,
            verbose=True,
            handle_parsing_errors=True,
        )


# Factory function
def create_langchain_orchestrator(
    config: Optional[Dict[str, Any]] = None,
    llm: Optional[BaseChatModel] = None,
    search_client: Optional[SearchClient] = None,
    **kwargs: Any,
) -> LangChainOrchestrator:
    """Create a LangChain orchestrator from configuration.
    
    Args:
        config: Configuration dictionary (loaded from config.json if not provided)
        llm: LangChain chat model (created from config if not provided)
        search_client: Search client for web search
        **kwargs: Additional arguments passed to LangChainOrchestrator
    
    Returns:
        Configured LangChainOrchestrator instance
    """
    if config is None:
        import json
        import os
        config_path = os.getenv("NLP_CONFIG_PATH", "config.json")
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        else:
            config = {}
    
    if llm is None:
        from langchain.langchain_llm import create_chat_model
        llm = create_chat_model(config=config)
    
    # Create classifier LLM if configured
    classifier_llm = None
    classifier_cfg = config.get("domainClassifier", {})
    if classifier_cfg.get("enabled", True):
        provider = classifier_cfg.get("provider") or classifier_cfg.get("model")
        if provider:
            from langchain.langchain_llm import create_role_chat_model
            classifier_llm = create_role_chat_model(config, classifier_cfg)
    
    # Create routing LLM if configured
    routing_llm = None
    routing_cfg = config.get("routingAndKeywords", {})
    if routing_cfg.get("enabled", True):
        provider = routing_cfg.get("provider") or routing_cfg.get("model")
        if provider:
            from langchain.langchain_llm import create_role_chat_model
            routing_llm = create_role_chat_model(config, routing_cfg)

    # Create post-check judge LLM if configured
    postcheck_llm = None
    postcheck_cfg = config.get("postcheck", {})
    judge_cfg = postcheck_cfg.get("judge", {})
    if postcheck_cfg.get("enabled", False) and judge_cfg.get("enabled", True):
        provider = judge_cfg.get("provider") or judge_cfg.get("model")
        if provider:
            from langchain.langchain_llm import create_role_chat_model

            postcheck_llm = create_role_chat_model(config, judge_cfg)
    
    return LangChainOrchestrator(
        llm=llm,
        search_client=search_client,
        classifier_llm=classifier_llm,
        routing_llm=routing_llm,
        postcheck_llm=postcheck_llm,
        google_api_key=config.get("googleSearch", {}).get("api_key") or config.get("GOOGLE_API_KEY"),
        sportsdb_api_key=config.get("SPORTSDB_API_KEY"),
        apisports_api_key=config.get("APISPORTS_KEY"),
        config=config,
        show_timings=kwargs.pop("show_timings", False),
        **kwargs,
    )
