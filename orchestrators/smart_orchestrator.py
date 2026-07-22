from __future__ import annotations

import json
import os
import sys
import time
import requests
import base64
from typing import Any, Dict, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.api import LLMClient
from utils.temperature_config import get_temperature_for_task
from rag.local_rag import LocalRAG
from rag.search_rag import SearchRAG
from search.rerank import BaseReranker
from search.search import SearchClient
from search.source_selector import IntelligentSourceSelector
from utils.search_routing import (
    build_decision_prompt,
    build_keyword_prompt,
    coerce_bool,
    extract_json_object,
    is_small_talk_query,
    normalize_sources,
)
from utils.time_parser import parse_time_constraint, TimeConstraint
from utils.timing_utils import TimingRecorder
from utils.current_time import get_current_date_str


class SmartSearchOrchestrator:
    """Coordinator that decides whether search is necessary before answering."""

    DECISION_SYSTEM_PROMPT = (
        "You are a routing assistant that decides whether a user's question needs fresh "
        "web or document search. Respond strictly in minified JSON."
    )
    KEYWORD_SYSTEM_PROMPT = (
        "You help generate high quality web search keywords. Respond strictly in JSON."
    )
    DIRECT_ANSWER_SYSTEM_PROMPT = (
        "You are a knowledgeable assistant. Answer clearly based on your existing knowledge. "
        "Always answer in the same language as the user's question."
    )
    SEARCH_SOURCE_LABELS = {
        "brave": "Brave Search",
        "brightdata": "Bright Data SERP",
        "google": "Google Search",
    }

    def __init__(
        self,
        llm_client: LLMClient,
        search_client: Optional[SearchClient],
        *,
        classifier_llm_client: Optional[LLMClient] = None,
        routing_llm_client: Optional[LLMClient] = None,
        data_path: Optional[str] = None,
        reranker: Optional[BaseReranker] = None,
        min_rerank_score: float = 0.0,
        max_per_domain: int = 1,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        requested_search_sources: Optional[List[str]] = None,
        active_search_sources: Optional[List[str]] = None,
        active_search_source_labels: Optional[List[str]] = None,
        missing_search_sources: Optional[List[str]] = None,
        configured_search_sources: Optional[List[str]] = None,
        show_timings: bool = False,
        google_api_key: Optional[str] = None,
        finnhub_api_key: Optional[str] = None,
        sportsdb_api_key: Optional[str] = None,
        apisports_api_key: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.llm_client = llm_client
        self.classifier_llm_client = classifier_llm_client
        self.routing_llm_client = routing_llm_client or llm_client
        self.search_client = search_client
        self.data_path = data_path
        self.reranker = reranker
        self.min_rerank_score = min_rerank_score
        self.max_per_domain = max(1, max_per_domain)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._local_pipeline: Optional[LocalRAG] = None
        self._search_rag_pipeline: Optional[SearchRAG] = None
        self._local_signature: Optional[tuple] = None
        self._search_rag_signature: Optional[tuple] = None
        self.google_api_key = google_api_key
        self.config = config or {}
        
        # Get provider name for temperature settings
        self.provider = getattr(llm_client, 'provider', 'zai')
        selector_client = classifier_llm_client or self.llm_client
        self.source_selector = IntelligentSourceSelector(
            llm_client=selector_client,
            use_llm=selector_client is not None,
            google_api_key=google_api_key,
            finnhub_api_key=finnhub_api_key,
            sportsdb_api_key=sportsdb_api_key,
            apisports_api_key=apisports_api_key,
            config=self.config,
        )
        self.requested_search_sources = normalize_sources(requested_search_sources)
        raw_active_sources = active_search_sources or getattr(search_client, "active_sources", [])
        self.active_search_sources = normalize_sources(raw_active_sources)
        raw_labels = active_search_source_labels or getattr(search_client, "active_source_labels", [])
        self.active_search_source_labels = [str(label).strip() for label in raw_labels if str(label).strip()]
        missing_sources = missing_search_sources or getattr(search_client, "missing_requested_sources", [])
        self.missing_search_sources = normalize_sources(missing_sources)
        configured_sources = configured_search_sources or getattr(search_client, "configured_sources", [])
        self.configured_search_sources = normalize_sources(configured_sources)
        self.show_timings = bool(show_timings)

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
    ) -> Dict[str, Any]:
        try:
            total_limit = max(1, int(num_search_results))
        except (TypeError, ValueError):
            total_limit = 10
        try:
            per_source_limit = (
                max(1, int(per_source_search_results))
                if per_source_search_results is not None
                else total_limit
            )
        except (TypeError, ValueError):
            per_source_limit = total_limit

        force_search = bool(force_search and allow_search)

        timing_recorder = TimingRecorder(enabled=self.show_timings)
        timing_recorder.start()

        # 解析查询中的时间限制
        time_constraint = parse_time_constraint(query)
        
        # 如果正则没有检测到时间限制，尝试使用LLM检测隐含的时间限制
        if not time_constraint.days and self.llm_client:
             llm_time_constraint = self._detect_time_constraint_with_llm(query, timing_recorder)
             if llm_time_constraint:
                 time_constraint = llm_time_constraint
                 print(f"LLM detected time constraint: {time_constraint.days} days ({time_constraint.freshness})")

        effective_query = time_constraint.cleaned_query if time_constraint.days else query
        
        # 如果存在时间限制，将当前日期注入到查询中以提供上下文
        if time_constraint.days:
            current_date = get_current_date_str()
            effective_query = f"{effective_query} (Current Date: {current_date})"
            print(f"Injecting current date into query: {effective_query}")

        snapshot = self._snapshot_local_docs()
        has_docs = bool(snapshot)

        if images:
            # Visual Retrieval Pipeline
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
            
            # Check if the LLM supports vision
            vision_keywords = ["grok", "gpt-4", "claude", "gemini", "glm-4v", "glm-4.5v", "claude-4.5-haiku", "vision", "minimax"]
            is_vision_model = any(k in self.llm_client.model_id.lower() for k in vision_keywords)
            
            if is_vision_model:
                system_prompt = "你是一个智能视觉助手。用户上传了一张图片。"
                if visual_context:
                    system_prompt += "\n\n" + visual_context
                else:
                    system_prompt += "\n请结合图片内容回答用户的问题。注意：未能获取图片的外部元数据（如Google Vision结果），请完全依赖你的视觉能力进行识别。请始终使用与用户提问相同的语言回答。"
            else:
                # Non-vision model prompt
                system_prompt = "你是一个智能助手。用户上传了图片，但你无法查看图片内容。"
                if visual_context:
                    system_prompt += "\n\n" + visual_context
                    system_prompt += "\n\n虽然你无法直接查看图片，但可以根据上述元数据信息尝试回答用户的问题。请始终使用与用户提问相同的语言回答。"
                else:
                    system_prompt += "\n请明确告知用户你无法查看图片，并询问他们是否可以描述图片内容或提供其他相关信息。请始终使用与用户提问相同的语言回答。"

            return self._finalize_with_timings(
                self._direct_answer_via_llm(
                    query=query,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    has_docs=has_docs,
                    allow_search=allow_search,
                    reason="image_content_present",
                    decision_meta={
                        "needs_search": False,
                        "reason": "image_content_present",
                        "raw_text": None,
                        "llm_warning": None,
                        "llm_error": None,
                    },
                    timing_recorder=timing_recorder,
                    images=images,
                    system_prompt_override=system_prompt
                ),
                timing_recorder,
            )

        if self._looks_like_small_talk(effective_query):
            return self._finalize_with_timings(
                self._respond_small_talk(
                    query=query,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    allow_search=allow_search,
                    has_docs=has_docs,
                    timing_recorder=timing_recorder,
                ),
                timing_recorder,
            )

        if not allow_search:
            return self._finalize_with_timings(
                self._answer_local_mode(
                    query=query,
                    snapshot=snapshot,
                    has_docs=has_docs,
                    num_retrieved_docs=num_retrieved_docs,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    search_allowed=False,
                    decision_reason="search_disabled",
                    timing_recorder=timing_recorder,
                    images=images,
                ),
                timing_recorder,
            )
        
        # 在决策之前先进行领域分类（使用清理后的查询）
        domain, sources = self.source_selector.select_sources(effective_query, timing_recorder=timing_recorder)
        enhanced_query = self.source_selector.generate_domain_specific_query(effective_query, domain)

        domain_api_result = self.source_selector.fetch_domain_data(
            effective_query,
            domain,
            timing_recorder=timing_recorder,
        )
        
        should_continue = False
        if domain_api_result:
            should_continue = domain_api_result.get("continue_search", False)

        if domain_api_result and domain_api_result.get("handled") and domain_api_result.get("answer") and not should_continue:
            return self._finalize_with_timings(
                self._domain_api_answer(
                    query=query,
                    domain=domain,
                    sources=sources,
                    enhanced_query=enhanced_query,
                    domain_api_result=domain_api_result,
                    has_docs=has_docs,
                    allow_search=allow_search,
                    force_search_enabled=force_search,
                ),
                timing_recorder,
            )
        
        # If we continue search, inject the domain data into the query context
        if should_continue and domain_api_result.get("answer"):
             print(f"Injecting domain data into context: {domain_api_result['answer'][:50]}...")
             effective_query += f"\n\n[已知背景信息/Context Data]:\n{domain_api_result['answer']}\n(请结合上述数据和接下来的搜索结果回答)"

        decision_meta: Dict[str, Any]
        decision_raw: Optional[Dict[str, Any]] = None

        if not force_search:
            decision = self._decide(effective_query, timing_recorder=timing_recorder)
            decision_meta = {
                "needs_search": decision.get("needs_search", True),
                "reason": decision.get("reason"),
                "raw_text": decision.get("raw_text"),
                "llm_warning": decision.get("llm_warning"),
                "llm_error": decision.get("llm_error"),
            }
            decision_raw = decision.get("llm_raw")

            if not decision_meta["needs_search"] and decision.get("direct_answer"):
                return self._finalize_with_timings(
                    self._direct_answer_from_decision(
                        query=query,
                        answer=decision["direct_answer"],
                        decision_meta=decision_meta,
                        decision_raw=decision_raw,
                        has_docs=has_docs,
                        allow_search=True,
                    ),
                    timing_recorder,
                )

            if not decision_meta["needs_search"]:
                return self._finalize_with_timings(
                    self._direct_answer_via_llm(
                        query=query,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        has_docs=has_docs,
                        allow_search=True,
                        reason="direct_llm_fallback",
                        decision_meta=decision_meta,
                        timing_recorder=timing_recorder,
                        images=images,
                    ),
                    timing_recorder,
                )
        else:
            decision_meta = {
                "needs_search": True,
                "reason": "force_search_override",
                "raw_text": None,
                "llm_warning": None,
                "llm_error": None,
            }

        if not self.search_client:
            response = self._search_unavailable_response(
                query=query,
                snapshot=snapshot,
                has_docs=has_docs,
                decision_meta=decision_meta,
                max_tokens=max_tokens,
                temperature=temperature,
                num_retrieved_docs=num_retrieved_docs,
                timing_recorder=timing_recorder,
            )
            if force_search:
                self._merge_control(response, {"force_search_enabled": True})
            return self._finalize_with_timings(response, timing_recorder)

        keyword_info = self._generate_keywords(effective_query, timing_recorder=timing_recorder)
        keywords = keyword_info.get("keywords") or [effective_query]
        search_query = " ".join(keywords).strip() or effective_query

        pipeline = self._build_pipeline(
            allow_search=True,
            has_docs=has_docs,
            snapshot=snapshot,
        )
        if pipeline is None:
            response = self._search_unavailable_response(
                query=query,
                snapshot=snapshot,
                has_docs=has_docs,
                decision_meta=decision_meta,
                max_tokens=max_tokens,
                temperature=temperature,
                num_retrieved_docs=num_retrieved_docs,
                timing_recorder=timing_recorder,
            )
            if force_search:
                self._merge_control(response, {"force_search_enabled": True})
            return self._finalize_with_timings(response, timing_recorder)

        pipeline_kwargs: Dict[str, Any] = {
            "search_query": search_query,
            "num_search_results": total_limit,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "timing_recorder": timing_recorder,
            "images": images,
        }
        if isinstance(pipeline, SearchRAG):
            pipeline_kwargs.update(
                {
                    "num_retrieved_docs": num_retrieved_docs,
                    "enable_search": True,
                }
            )
            pipeline_kwargs["per_source_limit"] = per_source_limit
            # 添加时间限制参数
            if time_constraint.freshness:
                pipeline_kwargs["freshness"] = time_constraint.freshness
            if time_constraint.google_date_restrict:
                pipeline_kwargs["date_restrict"] = time_constraint.google_date_restrict
            if reference_limit is not None:
                pipeline_kwargs["reference_limit"] = reference_limit
            
            # 传递领域API数据作为额外上下文（如yfinance股票数据）
            if domain_api_result and domain_api_result.get("answer"):
                pipeline_kwargs["extra_context"] = domain_api_result["answer"]

        result = pipeline.answer(query, **pipeline_kwargs)
        control_payload = {
            "search_performed": True,
            "decision": decision_meta,
            "search_mode": "search",
            "keywords": keywords,
            "keyword_generation": {
                "raw_text": keyword_info.get("raw_text"),
                "llm_warning": keyword_info.get("llm_warning"),
                "llm_error": keyword_info.get("llm_error"),
            },
            "hybrid_mode": isinstance(pipeline, SearchRAG),
            "local_docs_present": has_docs,
            "search_allowed": True,
            "domain": domain,
            "selected_sources": sources,
            "enhanced_query": enhanced_query,
            "search_total_limit": total_limit,
            "search_per_source_limit": per_source_limit,
            "force_search_enabled": force_search,
        }
        if domain_api_result:
            control_payload["domain_api"] = self._summarize_domain_api(domain_api_result)
            if domain_api_result.get("error") and not domain_api_result.get("skipped"):
                domain_warning = f"领域API调用失败：{domain_api_result['error']}"
                existing_warnings = result.get("search_warnings")
                if isinstance(existing_warnings, list):
                    warnings = existing_warnings
                elif existing_warnings:
                    warnings = [existing_warnings]
                else:
                    warnings = []
                if domain_warning not in warnings:
                    warnings.append(domain_warning)
                result["search_warnings"] = warnings
        
        if reference_limit is not None:
            control_payload["search_reference_limit"] = reference_limit

        # 添加时间约束信息到返回结果
        if time_constraint.days:
            control_payload["time_constraint"] = {
                "original_query": time_constraint.original_query,
                "cleaned_query": time_constraint.cleaned_query,
                "time_expression": time_constraint.time_expression,
                "days": time_constraint.days,
                "freshness": time_constraint.freshness,
                "google_date_restrict": time_constraint.google_date_restrict,
            }

        if result.get("search_warnings"):
            control_payload["search_warnings"] = result["search_warnings"]

        self._merge_control(result, control_payload)
        result.setdefault("search_query", search_query)
        return self._finalize_with_timings(result, timing_recorder)

    def _respond_small_talk(
        self,
        *,
        query: str,
        max_tokens: int,
        temperature: float,
        allow_search: bool,
        has_docs: bool,
        timing_recorder: Optional[TimingRecorder],
    ) -> Dict[str, Any]:
        direct = self._chat_with_timing(
            self.llm_client,
            label="small_talk_answer",
            timing_recorder=timing_recorder,
            system_prompt=self.DIRECT_ANSWER_SYSTEM_PROMPT,
            user_prompt=query,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        control = {
            "search_performed": False,
            "decision": {
                "needs_search": False,
                "reason": "small_talk_heuristic",
                "raw_text": None,
                "llm_warning": None,
                "llm_error": None,
            },
            "search_mode": "small_talk",
            "keywords": [],
            "hybrid_mode": False,
            "local_docs_present": has_docs,
            "search_allowed": allow_search,
        }
        return {
            "query": query,
            "answer": direct.get("content") or "",
            "search_hits": [],
            "llm_raw": direct.get("raw"),
            "llm_warning": direct.get("warning"),
            "llm_error": direct.get("error"),
            "control": control,
            "search_query": None,
        }

    def _answer_local_mode(
        self,
        *,
        query: str,
        snapshot: Optional[tuple],
        has_docs: bool,
        num_retrieved_docs: int,
        max_tokens: int,
        temperature: float,
        search_allowed: bool,
        decision_reason: str,
        timing_recorder: Optional[TimingRecorder],
        images: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        decision_meta = {
            "needs_search": False,
            "reason": decision_reason,
            "raw_text": None,
            "llm_warning": None,
            "llm_error": None,
        }

        if not has_docs:
            return self._direct_answer_via_llm(
                query=query,
                max_tokens=max_tokens,
                temperature=temperature,
                has_docs=False,
                allow_search=search_allowed,
                reason=decision_reason,
                decision_meta=decision_meta,
                timing_recorder=timing_recorder,
                images=images,
            )

        pipeline = self._ensure_local_pipeline(snapshot)
        if pipeline is None:
            return self._direct_answer_via_llm(
                query=query,
                max_tokens=max_tokens,
                temperature=temperature,
                has_docs=False,
                allow_search=search_allowed,
                reason=decision_reason,
                decision_meta=decision_meta,
                timing_recorder=timing_recorder,
                images=images,
            )

        # LocalRAG pipeline answer method needs to support images if we want to use it there
        # For now, let's assume LocalRAG doesn't support images yet, or we update it.
        # If images are present, maybe we should skip LocalRAG or update it?
        # Let's update LocalRAG later. For now, pass it if possible, or just ignore for LocalRAG.
        # But wait, if user uploads image, they expect it to be used.
        # If I don't update LocalRAG, I should probably use direct answer if images are present?
        # Or just update LocalRAG.
        
        # Let's update LocalRAG.answer signature later.
        # For now, I will pass images to pipeline.answer via kwargs if I update it.
        
        result = pipeline.answer(
            query,
            num_retrieved_docs=num_retrieved_docs,
            max_tokens=max_tokens,
            temperature=temperature,
            timing_recorder=timing_recorder,
            # images=images, # TODO: Update LocalRAG
        )
        result.setdefault("search_hits", [])
        result["search_query"] = None

        control_payload = {
            "search_performed": False,
            "decision": decision_meta,
            "search_mode": "local_rag",
            "keywords": [],
            "hybrid_mode": False,
            "local_docs_present": True,
            "search_allowed": search_allowed,
        }
        self._merge_control(result, control_payload)
        return result

    def _domain_api_answer(
        self,
        *,
        query: str,
        domain: str,
        sources: List[Dict[str, Any]],
        enhanced_query: str,
        domain_api_result: Dict[str, Any],
        has_docs: bool,
        allow_search: bool,
        force_search_enabled: bool,
        timing_recorder: Optional[TimingRecorder] = None,
    ) -> Dict[str, Any]:
        decision_meta = {
            "needs_search": False,
            "reason": f"domain_api_{domain or 'unknown'}",
            "raw_text": None,
            "llm_warning": None,
            "llm_error": None,
        }
        control_payload = {
            "search_performed": False,
            "decision": decision_meta,
            "search_mode": "domain_api",
            "keywords": [],
            "hybrid_mode": False,
            "local_docs_present": has_docs,
            "search_allowed": allow_search,
            "domain": domain,
            "selected_sources": sources,
            "enhanced_query": enhanced_query,
            "domain_api": self._summarize_domain_api(domain_api_result),
            "force_search_enabled": force_search_enabled,
        }
        
        # LLM增强回复
        domain_data = domain_api_result.get("data")
        basic_answer = domain_api_result.get("answer") or ""
        if domain_data and self.llm_client:
            enhanced = self._enhance_domain_answer(query, domain, domain_data, timing_recorder)
            answer = enhanced.get("content") or basic_answer
            llm_raw = enhanced.get("raw")
            llm_warning = enhanced.get("warning")
            llm_error = enhanced.get("error")
        else:
            answer = basic_answer
            llm_raw = None
            llm_warning = None
            llm_error = None
        
        result = {
            "query": query,
            "answer": answer,
            "search_hits": [],
            "llm_raw": llm_raw,
            "llm_warning": llm_warning,
            "llm_error": llm_error,
            "search_query": None,
            "domain_data": domain_data,
        }
        self._merge_control(result, control_payload)
        return result

    def _enhance_domain_answer(
        self,
        query: str,
        domain: str,
        domain_data: Dict[str, Any],
        timing_recorder: Optional[TimingRecorder] = None,
    ) -> Dict[str, Any]:
        if domain == "weather":
            system_prompt = (
                "你是天气助手。根据实时数据，给出自然、丰富的回复，包括当前状况、穿衣/出行建议。"
                "保持简洁、专业，并使用与用户提问相同的语言。"
            )
            data_summary = (
                f"位置：{domain_data.get('location', {}).get('formatted_address', '未知')}\n"
                f"概况：{domain_data.get('weatherCondition', {}).get('description', {}).get('text', '未知')}\n"
                f"温度：{domain_data.get('temperature', {}).get('degrees', '未知')}°C\n"
                f"湿度：{domain_data.get('relativeHumidity', '未知')}%\n"
                f"风速：{domain_data.get('wind', {}).get('speed', {}).get('value', '未知')} km/h"
            )
            user_prompt = f"查询：{query}\n实时数据：\n{data_summary}\n生成回复："
        elif domain == "transportation":
            system_prompt = (
                "你是交通助手。根据路线数据，给出详细建议，包括时间、拥堵、备选。"
                "保持简洁、专业，并使用与用户提问相同的语言。"
            )
            data_summary = json.dumps(domain_data, ensure_ascii=False, indent=2)
            user_prompt = f"查询：{query}\n路线数据：{data_summary}\n生成回复："
        elif domain == "finance":
            system_prompt = (
                "你是金融助手。根据提供的股票数据，分析价格走势和表现。"
                "如果包含多只股票，请进行对比。保持客观、专业，并使用与用户提问相同的语言。"
            )
            # domain_data is a list of dicts for finance
            data_summary = json.dumps(domain_data, ensure_ascii=False, indent=2)
            user_prompt = f"查询：{query}\n金融数据：{data_summary}\n生成回复："
        else:
            return {"content": "", "raw": None}
        
        return self._chat_with_timing(
            self.llm_client,
            label=f"domain_enhance_{domain}",
            timing_recorder=timing_recorder,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=300,
            temperature=0.3,
        )

    def _direct_answer_from_decision(
        self,
        *,
        query: str,
        answer: str,
        decision_meta: Dict[str, Any],
        decision_raw: Optional[Dict[str, Any]],
        has_docs: bool,
        allow_search: bool,
    ) -> Dict[str, Any]:
        payload = {
            "query": query,
            "answer": answer,
            "search_hits": [],
            "llm_raw": decision_raw,
            "llm_warning": decision_meta.get("llm_warning"),
            "llm_error": decision_meta.get("llm_error"),
            "search_query": None,
        }
        control_payload = {
            "search_performed": False,
            "decision": decision_meta,
            "search_mode": "direct_llm",
            "keywords": [],
            "hybrid_mode": False,
            "local_docs_present": has_docs,
            "search_allowed": allow_search,
        }
        self._merge_control(payload, control_payload)
        return payload

    def _direct_answer_via_llm(
        self,
        *,
        query: str,
        max_tokens: int,
        temperature: float,
        has_docs: bool,
        allow_search: bool,
        reason: str,
        decision_meta: Optional[Dict[str, Any]] = None,
        timing_recorder: Optional[TimingRecorder],
        images: Optional[List[Dict[str, str]]] = None,
        system_prompt_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Use task-specific temperature if configured
        task_temp = get_temperature_for_task(self.config, "direct_answer", self.provider, temperature)
        
        fallback = self._chat_with_timing(
            self.llm_client,
            label="direct_answer",
            timing_recorder=timing_recorder,
            system_prompt=system_prompt_override or self.DIRECT_ANSWER_SYSTEM_PROMPT,
            user_prompt=query,
            max_tokens=max_tokens,
            temperature=task_temp,
            extra={"stage": reason},
            images=images,
        )
        meta = (
            dict(decision_meta)
            if decision_meta is not None
            else {
                "needs_search": False,
                "reason": reason,
                "raw_text": None,
                "llm_warning": None,
                "llm_error": None,
            }
        )
        if fallback.get("warning") is not None:
            meta["llm_warning"] = fallback.get("warning")
        if fallback.get("error") is not None:
            meta["llm_error"] = fallback.get("error")

        payload = {
            "query": query,
            "answer": fallback.get("content") or "",
            "search_hits": [],
            "llm_raw": fallback.get("raw"),
            "llm_warning": fallback.get("warning"),
            "llm_error": fallback.get("error"),
            "search_query": None,
        }
        control_payload = {
            "search_performed": False,
            "decision": meta,
            "search_mode": reason,
            "keywords": [],
            "hybrid_mode": False,
            "local_docs_present": has_docs,
            "search_allowed": allow_search,
        }
        self._merge_control(payload, control_payload)
        return payload

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

    def _search_unavailable_response(
        self,
        *,
        query: str,
        snapshot: Optional[tuple],
        has_docs: bool,
        decision_meta: Dict[str, Any],
        max_tokens: int,
        temperature: float,
        num_retrieved_docs: int,
        timing_recorder: Optional[TimingRecorder],
    ) -> Dict[str, Any]:
        result = self._answer_local_mode(
            query=query,
            snapshot=snapshot,
            has_docs=has_docs,
            num_retrieved_docs=num_retrieved_docs,
            max_tokens=max_tokens,
            temperature=temperature,
            search_allowed=True,
            decision_reason="search_unavailable",
            timing_recorder=timing_recorder,
        )
        self._merge_control(
            result,
            {
                "decision": decision_meta,
                "search_mode": "search_unavailable",
                "search_allowed": True,
            },
        )
        return result

    def _build_pipeline(
        self,
        *,
        allow_search: bool,
        has_docs: bool,
        snapshot: Optional[tuple],
    ) -> Optional[SearchRAG | LocalRAG]:
        if not allow_search:
            return self._ensure_local_pipeline(snapshot)

        # Use SearchRAG for all search cases, with optional local documents
        return self._ensure_search_rag_pipeline(snapshot)

    def _ensure_local_pipeline(self, snapshot: Optional[tuple]) -> Optional[LocalRAG]:
        if not self.data_path or snapshot is None:
            self._local_pipeline = None
            self._local_signature = None
            return None

        if self._local_pipeline is None or self._local_signature != snapshot:
            try:
                self._local_pipeline = LocalRAG(
                    llm_client=self.llm_client,
                    data_path=self.data_path,
                    config=self.config,
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                )
                self._local_signature = snapshot
            except ValueError:
                self._local_pipeline = None
                self._local_signature = None
        return self._local_pipeline

    def _ensure_search_rag_pipeline(self, snapshot: Optional[tuple]) -> Optional[SearchRAG]:
        if not self.search_client:
            return None

        # Create a signature that includes both search client and local docs
        search_signature = (id(self.search_client), snapshot)
        
        if self._search_rag_pipeline is None or self._search_rag_signature != search_signature:
            self._search_rag_pipeline = SearchRAG(
                llm_client=self.llm_client,
                search_client=self.search_client,
                data_path=self.data_path,  # Can be None for search-only mode
                config=self.config,
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
                reranker=self.reranker,
                min_rerank_score=self.min_rerank_score,
                max_per_domain=self.max_per_domain,
            )
            self._search_rag_signature = search_signature
        return self._search_rag_pipeline

    def _snapshot_local_docs(self) -> Optional[tuple]:
        if not self.data_path or not os.path.isdir(self.data_path):
            return None

        records = []
        for root, _, files in os.walk(self.data_path):
            for name in files:
                lowered = name.lower()
                if lowered.endswith((".txt", ".md", ".pdf")):
                    full_path = os.path.join(root, name)
                    try:
                        records.append((full_path, os.path.getmtime(full_path)))
                    except OSError:
                        continue
        return tuple(sorted(records))

    @staticmethod
    def _merge_control(target: Dict[str, Any], payload: Dict[str, Any]) -> None:
        if "control" in target and isinstance(target["control"], dict):
            target["control"].update(payload)
        else:
            target["control"] = payload

    @staticmethod
    def _summarize_domain_api(domain_api_result: Dict[str, Any]) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {}
        for key in ("provider", "endpoint", "error", "mode"):
            value = domain_api_result.get(key)
            if value is not None:
                snapshot[key] = value
        for key in ("location", "origin", "destination"):
            value = domain_api_result.get(key)
            if value:
                snapshot[key] = value
        if domain_api_result.get("data"):
            snapshot["data"] = domain_api_result["data"]
        return snapshot

    @staticmethod
    def _normalize_sources(sources: Optional[List[str]]) -> List[str]:
        return normalize_sources(sources)

    def _attach_search_source_metadata(self, control: Dict[str, Any]) -> None:
        if "search_sources_requested" not in control:
            control["search_sources_requested"] = list(self.requested_search_sources)
        if "search_sources_active" not in control:
            control["search_sources_active"] = list(self.active_search_sources)
        if self.active_search_source_labels and "search_sources_active_labels" not in control:
            control["search_sources_active_labels"] = list(self.active_search_source_labels)
        if self.configured_search_sources and "search_sources_configured" not in control:
            control["search_sources_configured"] = list(self.configured_search_sources)
        if self.missing_search_sources and "search_sources_missing" not in control:
            control["search_sources_missing"] = list(self.missing_search_sources)

    def _apply_search_source_warnings(self, result: Dict[str, Any]) -> None:
        if not self.missing_search_sources:
            return
        labels = [self.SEARCH_SOURCE_LABELS.get(src, src) for src in self.missing_search_sources]
        warning = "部分搜索源不可用: " + ", ".join(labels)
        existing = result.get("search_warnings")
        if existing is None:
            result["search_warnings"] = [warning]
            return
        if isinstance(existing, list):
            if warning not in existing:
                existing.append(warning)
            return
        if existing != warning:
            result["search_warnings"] = [existing, warning]

    def _finalize(self, result: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(result, dict):
            return result
        control = result.get("control")
        if not isinstance(control, dict):
            control = {}
            result["control"] = control
        self._attach_search_source_metadata(control)
        self._apply_search_source_warnings(result)
        return result

    def _decide(self, query: str, timing_recorder: Optional[TimingRecorder]) -> Dict[str, Any]:
        try:
            response = self._chat_with_timing(
                self.routing_llm_client,
                label="search_decision",
                timing_recorder=timing_recorder,
                system_prompt=self.DECISION_SYSTEM_PROMPT,
                user_prompt=self._decision_prompt(query),
                max_tokens=400,
                temperature=0.0,
                extra={"stage": "decision"},
            )

            content = response.get("content") or ""
            # Ensure content is a string
            if not isinstance(content, str):
                content = str(content) if content is not None else ""
            
            payload = {
                "needs_search": True,
                "reason": None,
                "direct_answer": None,
                "raw_text": content[:100] if content else None,
                "llm_raw": response.get("raw"),
                "llm_warning": str(response.get("warning")) if response.get("warning") else None,
                "llm_error": str(response.get("error")) if response.get("error") else None,
            }

            if response.get("error"):
                payload["reason"] = "decision_llm_error"
                return payload

            parsed = extract_json_object(content)
            if not parsed:
                payload["reason"] = "decision_parse_error"
                return payload

            needs_search = coerce_bool(parsed.get("needs_search"))
            payload["needs_search"] = needs_search
            reason = parsed.get("reason")
            payload["reason"] = str(reason)[:100] if reason else None
            direct_answer = parsed.get("answer") if not needs_search else None
            if direct_answer:
                payload["direct_answer"] = str(direct_answer).strip()[:1000]
            return payload
            
        except Exception as e:
            return {
                "needs_search": True,
                "reason": f"Error in _decide: {str(e)[:100]}",
                "direct_answer": None,
                "raw_text": None,
                "llm_raw": None,
                "llm_warning": None,
                "llm_error": str(e)[:100],
            }

    @staticmethod
    def _looks_like_small_talk(query: str) -> bool:
        return is_small_talk_query(query)

    def _generate_keywords(self, query: str, timing_recorder: Optional[TimingRecorder]) -> Dict[str, Any]:
        try:
            response = self._chat_with_timing(
                self.routing_llm_client,
                label="keyword_generation",
                timing_recorder=timing_recorder,
                system_prompt=self.KEYWORD_SYSTEM_PROMPT,
                user_prompt=self._keyword_prompt(query),
                max_tokens=300,
                temperature=0.2,
                extra={"stage": "keywords"},
            )

            content = response.get("content") or ""
            # Ensure content is a string
            if not isinstance(content, str):
                content = str(content) if content is not None else ""
            
            payload: Dict[str, Any] = {
                "keywords": [],
                "raw_text": content[:200] if content else None,
                "llm_warning": str(response.get("warning")) if response.get("warning") else None,
                "llm_error": str(response.get("error")) if response.get("error") else None,
            }

            if response.get("error"):
                return payload

            parsed = extract_json_object(content)
            if not parsed:
                return payload

            keywords: List[str] = []
            raw_keywords = parsed.get("keywords")

            if isinstance(raw_keywords, list):
                for item in raw_keywords:
                    if isinstance(item, str):
                        cleaned = item.strip()
                        if cleaned:
                            keywords.append(str(cleaned)[:50])
            elif isinstance(raw_keywords, str):
                cleaned = raw_keywords.strip()
                if cleaned:
                    keywords.extend([str(part.strip())[:50] for part in cleaned.split(";") if part.strip()])

            payload["keywords"] = [str(k)[:50] for k in keywords[:10]]
            return payload
            
        except Exception as e:
            return {
                "keywords": [],
                "raw_text": None,
                "llm_warning": None,
                "llm_error": f"Error in _generate_keywords: {str(e)[:100]}",
            }

    @staticmethod
    def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
        return extract_json_object(text)

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        return coerce_bool(value)

    @classmethod
    def _decision_prompt(cls, query: str) -> str:
        return build_decision_prompt(query)

    @classmethod
    def _keyword_prompt(cls, query: str) -> str:
        return build_keyword_prompt(query)

    def _finalize_with_timings(
        self,
        result: Dict[str, Any],
        timing_recorder: TimingRecorder,
    ) -> Dict[str, Any]:
        finalized = self._finalize(result)
        if timing_recorder.enabled:
            timing_recorder.stop()
            payload = timing_recorder.to_dict()
            if payload:
                # Determine domain label
                domain_label = "无"
                control = finalized.get("control")
                if isinstance(control, dict):
                    domain = control.get("domain")
                    if domain and str(domain).lower() != "general":
                        domain_label = str(domain)
                
                # Add explicit field
                payload["领域智能类型"] = domain_label
                
                finalized["response_times"] = payload
        return finalized

    def _chat_with_timing(
        self,
        client: LLMClient,
        *,
        label: str,
        timing_recorder: Optional[TimingRecorder],
        extra: Optional[Dict[str, Any]] = None,
        **chat_kwargs: Any,
    ) -> Dict[str, Any]:
        start = time.perf_counter()
        try:
            return client.chat(**chat_kwargs)
        finally:
            if timing_recorder:
                duration_ms = (time.perf_counter() - start) * 1000
                timing_recorder.record_llm_call(
                    label=label,
                    duration_ms=duration_ms,
                    provider=getattr(client, "provider", None),
                    model=getattr(client, "model_id", None),
                    extra=extra,
                )

    def _detect_time_constraint_with_llm(
        self,
        query: str,
        timing_recorder: Optional[TimingRecorder]
    ) -> Optional[TimeConstraint]:
        """Use LLM to detect implicit time constraints in the query."""
        system_prompt = (
            "You are a query analysis assistant. Your task is to determine if a user's query implies a need for "
            "recent or time-sensitive information, even if no explicit time keywords are used.\n"
            "Examples:\n"
            "- 'How old is Trump' -> Implies 'now' -> Return 'month' or 'year'\n"
            "- 'Latest iPhone features' -> Implies 'now' -> Return 'month'\n"
            "- 'History of Rome' -> No time constraint -> Return null\n"
            "- 'Python list methods' -> No time constraint -> Return null\n\n"
            "Respond strictly in JSON format with the following structure:\n"
            "{\n"
            "  \"has_time_constraint\": boolean,\n"
            "  \"time_range\": \"day\" | \"week\" | \"month\" | \"year\" | null,\n"
            "  \"reason\": string\n"
            "}"
        )
        
        # Use task-specific temperature for classification
        task_temp = get_temperature_for_task(self.config, "classification", self.provider, 0.1)
        
        response = self._chat_with_timing(
            self.llm_client,
            label="time_detection",
            timing_recorder=timing_recorder,
            system_prompt=system_prompt,
            user_prompt=f"Query: {query}",
            max_tokens=100,
            temperature=task_temp,
        )
        
        try:
            content = response.get("content", "{}")
            if not content:
                return None
                
            # Handle potential markdown code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
                
            data = json.loads(content)
            
            if data.get("has_time_constraint") and data.get("time_range"):
                range_map = {
                    "day": 1,
                    "week": 7,
                    "month": 30,
                    "year": 365
                }
                days = range_map.get(data["time_range"])
                if days:
                    # Create a TimeConstraint object
                    # We don't change the cleaned_query because there was no explicit keyword to remove
                    tc = TimeConstraint(
                        original_query=query,
                        cleaned_query=query,
                        days=days,
                        time_expression="LLM_Inferred"
                    )
                    
                    # Use the parser instance to help
                    from time_parser import get_time_parser
                    parser = get_time_parser()
                    tc.freshness = parser._map_to_freshness(days)
                    tc.google_date_restrict = parser._map_to_google_date_restrict(days)
                    return tc
                    
        except Exception as e:
            print(f"Error parsing LLM time detection response: {e}")
            
        return None
