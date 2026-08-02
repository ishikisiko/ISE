from __future__ import annotations

import os
import sys
import re
import time
import logging
from dataclasses import asdict
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.api import LLMClient
from langchain.langchain_support import Document, FileReader, LangChainVectorStore
from search.rerank import BaseReranker
from search.search import SearchClient, SearchHit, GoogleSearchClient
from utils.timing_utils import TimingRecorder

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


DEFAULT_SYSTEM_PROMPT = (
    "You are an information assistant. "
    "Answer user questions concisely using ONLY the provided search results and local documents. "
    "CRITICAL: Do NOT fabricate, invent, or guess any specific data (such as scores, numbers, statistics, dates, or names) "
    "that is not EXPLICITLY stated in the search results or local documents. "
    "If specific information is not found in the search results or local documents, clearly state '未在搜索结果和本地文档中找到具体数据' or 'specific data not found in search results and local documents'. "
    "When unsure, acknowledge the uncertainty instead of guessing. "
    "Always answer in the same language as the user's question."
)


class SearchRAG:
    """A unified RAG pipeline that combines web search with optional local document retrieval."""

    def __init__(
        self,
        llm_client: LLMClient,
        search_client: SearchClient,
        data_path: Optional[str] = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        *,
        config: Optional[Dict[str, object]] = None,
        reranker: Optional[BaseReranker] = None,
        min_rerank_score: float = 0.0,
        max_per_domain: int = 1,
        embedding_model: Optional[str] = None,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ) -> None:
        self.llm_client = llm_client
        self.search_client = search_client
        self.system_prompt = system_prompt
        self.config = config or {}
        self.reranker = reranker
        self.min_rerank_score = min_rerank_score
        self.max_per_domain = max(1, max_per_domain)

        # Initialize local document retrieval if data_path is provided
        self.vector_store = None
        if data_path:
            print("Loading and indexing local documents...")
            try:
                reader = FileReader(data_path)
                documents = reader.load()
                self.vector_store = LangChainVectorStore(
                    model_name=embedding_model,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    config=self.config,
                )
                chunk_count = self.vector_store.index(documents)
                print(f"Indexed {chunk_count} chunks from local documents.")
            except Exception as e:
                print(f"Failed to load local documents: {e}")
                self.vector_store = None

    def _format_search_hits(self, hits: List[SearchHit]) -> str:
        if not hits:
            return "No search results were returned."

        formatted_rows = []
        for idx, hit in enumerate(hits, start=1):
            snippet = hit.snippet or "No snippet available."
            url = hit.url or "No URL available."
            title = hit.title or f"Result {idx}"
            formatted_rows.append(
                f"{idx}. {title}\n"
                f"   URL: {url}\n"
                f"   Snippet: {snippet}"
            )
        return "\n".join(formatted_rows)
    
    def _format_local_docs(self, docs: List[Document]) -> str:
        if not docs:
            return "No local documents were retrieved."

        formatted_rows = []
        for idx, doc in enumerate(docs, start=1):
            source = doc.source or f"Document {idx}"
            content = doc.content[:500]  # Limit content length
            if len(doc.content) > 500:
                content += "..."
            formatted_rows.append(
                f"{idx}. {source}\n"
                f"   Content: {content}"
            )
        return "\n".join(formatted_rows)
    
    def _is_temporal_change_query(self, query: str) -> bool:
        """Legacy-only temporal fallback requires an explicit time request."""
        return bool(
            re.search(
                r"(?:过去|最近|近)\s*(?:\d+|一|两|三|四|五|六|七|八|九|十)+\s*(?:年|个月|月|天|周)"
                r"|(?:历年|历史|趋势|逐年|时间序列)"
                r"|\b(?:last|past|over\s+the\s+last)\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:years?|months?|weeks?|days?)\b"
                r"|\b(?:historical|history|trend|time\s+series)\b",
                query,
                re.IGNORECASE,
            )
        )

    def _check_google_client_availability(self) -> Optional[GoogleSearchClient]:
        """
        Enhanced Google search client availability check with comprehensive error handling
        """
        logger.info("🔍 Checking Google search client availability...")

        # Check if search_client is CombinedSearchClient with clients
        if hasattr(self.search_client, "clients"):
            logger.debug(f"   Found clients attribute, client count: {len(self.search_client.clients)}")

            for i, client in enumerate(self.search_client.clients):
                logger.debug(f"   Client {i}: {type(client)}")
                if hasattr(client, "source_id"):
                    logger.debug(f"      source_id: {client.source_id}")
                    if client.source_id == "google":
                        logger.info("   ✅ Found Google search client!")
                        return client
        else:
            logger.debug("   ❌ search_client has no clients attribute")

        # Check if search_client itself is a GoogleSearchClient
        if isinstance(self.search_client, GoogleSearchClient):
            logger.info("   ✅ search_client is a GoogleSearchClient!")
            return self.search_client

        logger.warning("⚠️ No Google search client found")
        return None

    def _should_fallback_to_granular_search(self, query: str, hits: List[SearchHit]) -> bool:
        """判断是否应该进行颗粒化搜索fallback"""
        if not hits:
            return True
            
        # 使用LLM判断搜索结果是否满足查询需求
        query_lower = query.lower()
        
        # 检查是否包含时间变化相关的关键词
        time_keywords = ["最近10年", "过去10年", "10年", "十年", "10 years", "decade", "历年", "历史", "变化", "趋势"]
        is_time_query = any(kw in query_lower for kw in time_keywords)
        
        if not is_time_query:
            return False
            
        # 检查搜索结果中是否包含足够的时间变化数据
        combined_snippets = " ".join(hit.snippet.lower() for hit in hits if hit.snippet)
        
        # 检查是否包含年份+排名的模式
        year_rank_pattern = r'\b(20\d{2})\b.*?(?:rank|排名|position|#\d+|top\s*\d+)'
        has_year_rank_data = bool(re.search(year_rank_pattern, combined_snippets))
        
        # 检查是否包含多个年份的数据
        year_pattern = r'\b(20\d{2})\b'
        years_found = re.findall(year_pattern, combined_snippets)
        unique_years = set(years_found)
        has_multiple_years = len(unique_years) >= 3  # 至少3个不同年份的数据
        
        # 如果是时间查询，必须要有多个年份的数据，或者虽然年份不多但有明确的排名趋势描述
        # 之前的逻辑是 (has_year_rank_data or has_multiple_years)，这意味着只要有一个年份的排名数据就跳过颗粒化搜索
        # 这对于"近十年变化"的查询是不够的
        
        if is_time_query:
            # 对于时间查询，如果没有足够多的年份数据，就应该进行颗粒化搜索
            if not has_multiple_years:
                logger.info(f"Time query detected but only found {len(unique_years)} years. Triggering granular search.")
                return True
        
        # 如果没有足够的时间变化数据，则需要fallback
        return not (has_year_rank_data or has_multiple_years)
    
    def _perform_granular_search_fallback(
        self, 
        original_query: str, 
        effective_query: str, 
        num_search_results: int, 
        per_source_cap: int,
        freshness: Optional[str],
        date_restrict: Optional[str],
        timing_recorder: Optional[TimingRecorder]
    ) -> List[SearchHit]:
        """执行颗粒化搜索fallback"""
        import json

        # 第一步：使用LLM生成更宽泛的搜索查询
        broad_search_prompt = (
            f"原始查询：{original_query}\n\n"
            "这是一个关于时间变化的查询，但初始搜索结果没有提供足够的历史数据。"
            "请生成一个更宽泛的搜索查询，用于获取相关的历史数据。\n\n"
            "生成规则：\n"
            "1. 如果查询涉及大学排名，生成包含'历史排名'、'历年排名'或'历年变化'的查询\n"
            "2. 如果查询涉及10年变化，生成包含2016-2025年份范围的查询\n"
            "3. 如果查询涉及其他时间变化，生成包含'历史趋势'、'历年数据'或'时间序列'的查询\n"
            "4. 宽泛查询应该更通用，但仍然保持与原始查询的相关性\n\n"
            "只返回一个JSON对象，格式如下：\n"
            '{\n'
            '  "broad_query": "更宽泛的搜索查询",\n'
            '  "years": ["2016", "2017", "2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025"]\n'
            '}'
        )
        
        try:
            response = self.llm_client.chat(
                system_prompt="你是搜索查询优化专家，擅长生成更宽泛但相关的搜索查询。",
                user_prompt=broad_search_prompt,
                max_tokens=200,
                temperature=0.3,
            )
            
            content = response.get("content", "")
            # 尝试解析JSON
            start = content.find("{")
            end = content.rfind("}") + 1
            if start != -1 and end > start:
                json_str = content[start:end]
                llm_result = json.loads(json_str)
                broad_query = llm_result.get("broad_query", effective_query)
                years = llm_result.get("years", [])
            else:
                broad_query = effective_query
                years = []
        except Exception as e:
            print(f"LLM生成宽泛查询失败: {e}")
            broad_query = effective_query
            years = []
        
        # 第二步：执行宽泛搜索
        print(f"🔍 执行宽泛搜索: {broad_query}")
        broad_hits = self.search_client.search(
            broad_query,
            num_results=num_search_results * 2,  # 获取更多结果以便筛选
            per_source_limit=per_source_cap * 2,
            freshness=freshness,
            date_restrict=date_restrict,
        )
        
        # 分析宽泛搜索结果中的时间变化数据
        if broad_hits:
            combined_snippets = " ".join(hit.snippet.lower() for hit in broad_hits if hit.snippet)
            import re
            
            # 统计找到的年份数量
            year_pattern = r'\b(20\d{2})\b'
            years_found = re.findall(year_pattern, combined_snippets)
            unique_years = set(years_found)
            
            # 检查是否包含排名/位置信息
            rank_indicators = ['rank', '排名', 'position', '#', 'top', 'ranking']
            has_rank_data = any(indicator in combined_snippets for indicator in rank_indicators)
            
            print(f"📊 宽泛搜索分析：找到 {len(unique_years)} 个不同年份的数据，包含排名信息: {has_rank_data}")
            
            # 对于时间变化查询，总是执行颗粒化搜索以获取更完整的数据
            print(f"📊 宽泛搜索分析：找到 {len(unique_years)} 个不同年份的数据，包含排名信息: {has_rank_data}")
            print("🔄 继续执行颗粒化搜索以获取更完整的历史数据...")
        
        # 第三步：从宽泛搜索结果中提取年份信息
        if not years:
            # 如果LLM没有提供年份，尝试从查询中提取
            import re
            year_range_match = re.search(r'(20\d{2})\s*[-至到]\s*(20\d{2})', original_query)
            if year_range_match:
                start_year = int(year_range_match.group(1))
                end_year = int(year_range_match.group(2))
                years = [str(year) for year in range(start_year, end_year + 1)]
            else:
                # 默认使用最近10年
                current_year = 2025
                years = [str(year) for year in range(current_year - 9, current_year + 1)]
        
        # 第四步：执行颗粒化搜索（对于时间变化查询总是执行）
        if years:  # 移除_should_fallback_to_granular_search检查，确保总是执行颗粒化搜索
            logger.info("🔍 Starting granular search...")
            granular_search_start = time.perf_counter()
            granular_hits = []

            # 尝试获取Google客户端，但如果不可用，使用通用搜索客户端
            google_client = self._check_google_client_availability()
            active_client = google_client if google_client else self.search_client
            client_name = "Google" if google_client else "Generic"
            
            logger.info(f"Using {client_name} search client for granular search")

            # Add performance monitoring for granular search
            if timing_recorder:
                timing_recorder.start_operation("granular_search")
            
            # 优化颗粒化查询：智能选择关键年份
            selected_years = years
            if len(years) > 6:
                # 智能选择策略：选择开始年份、结束年份和中间的几个关键年份
                # 对于10年查询，选择第1年、第3年、第5年、第7年、第10年
                if len(years) == 10:
                    selected_years = [years[0], years[2], years[4], years[6], years[-1]]
                else:
                    # 对于其他长度的年份列表，均匀分布选择
                    step = max(1, len(years) // 5)
                    selected_years = [years[i] for i in range(0, len(years), step)]
                    if years[-1] not in selected_years:
                        selected_years.append(years[-1])
                
                print(f"📅 优化颗粒化搜索，选择关键年份: {selected_years}")
            else:
                print(f"📅 执行颗粒化搜索，年份: {selected_years}")
            
            # 为每个选定的年份生成查询
            for year in selected_years:
                # 智能生成更精确的年份查询
                query_lower = original_query.lower()
                
                # 提取查询中的关键实体（大学名称等）
                import re
                # 提取大学名称
                universities = []
                if "香港中文大學" in original_query or "香港中文大学" in original_query:
                    universities.append("Chinese University of Hong Kong")
                    universities.append("CUHK")
                if "香港科技大學" in original_query or "香港科技大学" in original_query:
                    universities.append("Hong Kong University of Science and Technology")
                    universities.append("HKUST")
                
                # 根据查询类型生成不同的年份查询
                if "qs" in query_lower and ("排名" in original_query or "ranking" in query_lower):
                    # QS排名查询 - 生成更简洁的查询
                    if universities:
                        # 如果有具体的大学，查询这些大学的QS排名
                        for uni in universities:
                            year_query = f"QS world university rankings {year} {uni}"
                            print(f"🔍 搜索年份 {year}: {year_query}")
                            try:
                                # 构建搜索参数
                                search_kwargs = {
                                    "num_results": max(2, num_search_results // (len(selected_years) * len(universities))),
                                    "freshness": freshness,
                                }
                                # 只有Google客户端支持date_restrict
                                if google_client:
                                    search_kwargs["date_restrict"] = f"{year}-01-01..{year}-12-31"
                                
                                year_hits = active_client.search(year_query, **search_kwargs)
                                granular_hits.extend(year_hits)
                            except Exception as e:
                                print(f"年份 {year} 搜索失败: {e}")
                        
                        # 额外查询：香港大学QS排名（作为参考）
                        hk_query = f"QS world university rankings {year} Hong Kong universities ranking"
                        print(f"🔍 搜索年份 {year} (香港大学排名): {hk_query}")
                        try:
                            search_kwargs = {
                                "num_results": 2,
                                "freshness": freshness,
                            }
                            if google_client:
                                search_kwargs["date_restrict"] = f"{year}-01-01..{year}-12-31"
                                
                            hk_hits = active_client.search(hk_query, **search_kwargs)
                            granular_hits.extend(hk_hits)
                        except Exception as e:
                            print(f"年份 {year} 香港大学排名搜索失败: {e}")
                    else:
                        # 如果没有具体大学，查询QS排名总体情况
                        year_query = f"QS world university rankings {year}"
                        print(f"🔍 搜索年份 {year}: {year_query}")
                        try:
                            search_kwargs = {
                                "num_results": max(3, num_search_results // len(selected_years)),
                                "freshness": freshness,
                            }
                            if google_client:
                                search_kwargs["date_restrict"] = f"{year}-01-01..{year}-12-31"
                                
                            year_hits = active_client.search(year_query, **search_kwargs)
                            granular_hits.extend(year_hits)
                        except Exception as e:
                            print(f"年份 {year} 搜索失败: {e}")
                    continue  # 跳过后续的通用查询逻辑
                elif "the" in query_lower and ("排名" in original_query or "ranking" in query_lower):
                    # THE排名查询
                    year_query = f"THE world university rankings {year}"
                elif "arwu" in query_lower or "软科" in original_query:
                    # ARWU排名查询
                    year_query = f"ARWU academic ranking of world universities {year}"
                elif "排名" in original_query or "ranking" in query_lower:
                    # 通用排名查询
                    year_query = f"university rankings {year}"
                elif "大学" in original_query or "university" in query_lower:
                    # 大学相关查询
                    year_query = f"university {year}"
                else:
                    # 通用查询
                    year_query = f"{original_query} {year}"
                
                print(f"🔍 搜索年份 {year}: {year_query}")
                
                try:
                    search_kwargs = {
                        "num_results": max(3, num_search_results // len(selected_years)),
                        "freshness": freshness,
                    }
                    if google_client:
                        search_kwargs["date_restrict"] = f"{year}-01-01..{year}-12-31"
                        
                    year_hits = active_client.search(year_query, **search_kwargs)
                    granular_hits.extend(year_hits)
                    logger.debug(f"✅ Successfully searched year {year}, got {len(year_hits)} results")
                except Exception as e:
                    logger.error(f"❌ Year {year} search failed: {e}")
                    logger.debug(f"   Query: {year_query}")
                    if timing_recorder:
                        timing_recorder.record_error(f"granular_search_year_{year}", str(e))
            
            # 合并宽泛搜索和颗粒化搜索结果，优先保留颗粒化搜索结果
            all_hits = granular_hits + broad_hits
            
            # 智能去重：保留更相关的结果
            seen_urls = set()
            deduped_hits = []
            for hit in all_hits:
                url_key = hit.url or ""
                if url_key not in seen_urls:
                    seen_urls.add(url_key)
                    deduped_hits.append(hit)
            
            # 按相关性排序：优先包含年份和排名信息的结果
            def hit_relevance_score(hit):
                score = 0
                if hit.snippet:
                    snippet = hit.snippet.lower()
                    # 检查是否包含年份
                    import re
                    years_in_snippet = re.findall(r'\b(20\d{2})\b', snippet)
                    score += len(years_in_snippet) * 2
                    # 检查是否包含排名信息
                    rank_keywords = ['rank', '排名', 'position', '#', 'top']
                    score += sum(1 for kw in rank_keywords if kw in snippet)
                return score
            
            deduped_hits.sort(key=hit_relevance_score, reverse=True)

            # Record granular search performance
            granular_search_duration = (time.perf_counter() - granular_search_start) * 1000
            logger.info(f"✅ Granular search completed in {granular_search_duration:.2f}ms, obtained {len(deduped_hits)} results")

            if timing_recorder:
                timing_recorder.end_operation("granular_search", {
                    "duration_ms": granular_search_duration,
                    "results_count": len(deduped_hits),
                    "years_queried": len(selected_years),
                    "google_client_available": google_client is not None
                })

            return deduped_hits[:num_search_results * 2]  # 返回更多结果以便筛选
        else:
            logger.info("No years specified for granular search")
            return broad_hits

    def build_prompt(self, query: str, hits: List[SearchHit], local_docs: List[Document] = None, ranking_info: str = "") -> str:
        search_context = self._format_search_hits(hits)
        local_context = self._format_local_docs(local_docs) if local_docs else ""
        
        # 检查是否是排名查询
        is_ranking_query = any(keyword in query.lower() for keyword in ['排名', 'ranking', 'rank'])
        
        if is_ranking_query:
            special_instructions = (
                "对于排名查询，请特别注意：\n"
                "1. 如果搜索结果中有具体的排名数据，请使用这些数据\n"
                "2. 如果没有找到具体的排名数字，请明确说明'未找到具体排名数据'\n"
                "3. 不要猜测或编造排名数字\n"
                "4. 如果有多个年份的排名数据，请按时间顺序呈现\n"
                "5. 如果有多个排名系统（QS、THE、ARWU等），请分别说明\n\n"
            )
        else:
            special_instructions = ""
        
        # 构建提示
        prompt_parts = [
            f"用户问题：{query}\n\n",
        ]
        
        # 添加搜索结果上下文
        if search_context:
            prompt_parts.append("网络搜索结果：\n")
            prompt_parts.append(search_context)
            prompt_parts.append("\n\n")
        
        # 添加本地文档上下文
        if local_context:
            prompt_parts.append("本地文档：\n")
            prompt_parts.append(local_context)
            prompt_parts.append("\n\n")
        
        # 添加排名信息（如果有）
        if ranking_info:
            prompt_parts.append(ranking_info)
            prompt_parts.append("\n\n")
        
        # 添加特殊指令
        if special_instructions:
            prompt_parts.append(special_instructions)
        
        # 添加最终指令
        prompt_parts.append(
            "请基于以上信息回答用户问题。"
            "如果信息不足，请明确说明。"
            "请用与用户问题相同的语言回答。"
        )
        
        return "".join(prompt_parts)

    def _extract_ranking_data(self, hits: List[SearchHit]) -> Dict[str, Dict[str, int]]:
        """从搜索结果中提取排名数据 - 优化版本，增强正则表达式覆盖范围和匹配能力"""
        logger.info("🔍 Starting ranking data extraction from search results...")

        cuhk_rankings = {}
        hkust_rankings = {}

        # Enhanced regex patterns for university names and ranking indicators
        university_patterns = {
            "cuhk": [
                r"chinese university of hong kong",
                r"cuhk",
                r"香港中文大學",
                r"香港中文大学",
                r"港中大",
                r"中文大學",
                r"中文大学"
            ],
            "hkust": [
                r"hong kong university of science and technology",
                r"hkust",
                r"香港科技大學",
                r"香港科技大学",
                r"港科大",
                r"科技大學",
                r"科技大学"
            ]
        }

        # Enhanced ranking patterns with broader coverage
        ranking_patterns = [
            r"ranked? #?(\d+)",
            r"排名.*?第?(\d+)",
            r"position #?(\d+)",
            r"top\s*(\d+)",
            r"#(\d+)",
            r"(\d+)\s*(?:st|nd|rd|th)",
            r"(\d+)\s*位",
            r"(\d+)\s*名"
        ]

        # Enhanced year patterns with broader coverage
        year_patterns = [
            r"\b(20\d{2})\b",  # 4-digit years starting with 20
            r"(\d{4})年",      # Chinese format: 2023年
            r"(\d{4})年度",    # Chinese format: 2023年度
            r"\b(19\d{2})\b"   # 4-digit years starting with 19 (for historical data)
        ]

        for hit_idx, hit in enumerate(hits):
            if not hit.snippet:
                continue

            snippet = hit.snippet.lower()
            logger.debug(f"Processing hit {hit_idx + 1}: {hit.title[:50]}...")

            # Extract year information first
            extracted_years = []
            for year_pattern in year_patterns:
                year_matches = re.findall(year_pattern, snippet)
                for match in year_matches:
                    # Handle different match formats from regex
                    if isinstance(match, tuple):
                        year_str = match[0]
                    else:
                        year_str = match

                    # Clean up year string
                    year_str = str(year_str).strip()

                    # Convert 2-digit years to 4-digit (assuming 2000s)
                    if len(year_str) == 2:
                        year = f"20{year_str}"
                        if 2000 <= int(year) <= 2099:
                            extracted_years.append(year)
                    elif len(year_str) == 4 and year_str.startswith(('20', '19')):
                        extracted_years.append(year_str)
                    elif '年' in year_str:
                        # Extract year from Chinese format like "2022年"
                        year_match = re.search(r'(\d{4})年', year_str)
                        if year_match:
                            extracted_years.append(year_match.group(1))

            # Remove duplicates and sort
            unique_years = sorted(list(set(extracted_years)), reverse=True)
            logger.debug(f"   Extracted years: {unique_years}")

            # Extract CUHK rankings with enhanced patterns
            for year in unique_years:
                # Look for year followed immediately by ranking information
                # Use multiple specific patterns to catch different formats
                ranking_patterns = [
                    rf"{year}.*?(?:ranked?)\s+#(\d+)",  # "2022 ranked #45"
                    rf"{year}.*?(?:ranked?)\s+(\d+)(?:st|nd|rd|th)",  # "2022 ranked 45th"
                    rf"{year}.*?#(\d+)",  # "2022 #45"
                    rf"{year}.*?第(\d+)",  # "2022 第45"
                    rf"{year}.*?(\d+)(?:st|nd|rd|th)",  # "2022 45th"
                    rf"{year}.*?(\d+)\s*(?:位|名)",  # "2022 45位"
                    rf"{year}.*?and\s+(\d+)(?:st|nd|rd|th)",  # "2022 and 45th"
                    rf"{year}.*?in\s+(\d+)"  # "2022 in 45"
                ]

                for ranking_pattern in ranking_patterns:
                    ranking_matches = re.findall(ranking_pattern, snippet, re.IGNORECASE)
                    if ranking_matches:
                        for match in ranking_matches:
                            try:
                                if isinstance(match, tuple):
                                    rank_str = match[-1]  # Get the last group (the ranking number)
                                else:
                                    rank_str = match

                                rank = int(rank_str)
                                if 1 <= rank <= 1000:  # Reasonable ranking range
                                    # Check if CUHK is mentioned in the snippet
                                    cuhk_found = False
                                    for cuhk_pattern in university_patterns["cuhk"]:
                                        if re.search(cuhk_pattern, snippet, re.IGNORECASE):
                                            cuhk_found = True
                                            break

                                    if cuhk_found:
                                        cuhk_rankings[year] = rank
                                        logger.debug(f"   CUHK {year}: #{rank} via pattern: {ranking_pattern}")
                                        break
                            except (ValueError, IndexError) as e:
                                logger.debug(f"   Failed to extract CUHK {year} rank from '{match}': {e}")
                                continue
                        if year in cuhk_rankings:  # Found a ranking for this year
                            break

            # Extract HKUST rankings with enhanced patterns
            for year in unique_years:
                # Look for year followed immediately by ranking information
                # Use multiple patterns to catch different formats
                ranking_patterns = [
                    rf"{year}.*?(?:ranked?)\s+#?(\d+)",  # "2022 ranked #45"
                    rf"{year}.*?(?:ranked?)\s+(\d+)(?:st|nd|rd|th)",  # "2022 ranked 45th"
                    rf"{year}.*?#(\d+)",  # "2022 #45"
                    rf"{year}.*?第(\d+)",  # "2022 第45"
                    rf"{year}.*?(\d+)(?:st|nd|rd|th)",  # "2022 45th"
                    rf"{year}.*?(\d+)\s*(?:位|名)"  # "2022 45位"
                ]

                for ranking_pattern in ranking_patterns:
                    ranking_matches = re.findall(ranking_pattern, snippet, re.IGNORECASE)
                    if ranking_matches:
                        for match in ranking_matches:
                            try:
                                if isinstance(match, tuple):
                                    rank_str = match[-1]  # Get the last group (the ranking number)
                                else:
                                    rank_str = match

                                rank = int(rank_str)
                                if 1 <= rank <= 1000:  # Reasonable ranking range
                                    # Check if HKUST is mentioned in the snippet
                                    hkust_found = False
                                    for hkust_pattern in university_patterns["hkust"]:
                                        if re.search(hkust_pattern, snippet, re.IGNORECASE):
                                            hkust_found = True
                                            break

                                    if hkust_found:
                                        hkust_rankings[year] = rank
                                        logger.debug(f"   HKUST {year}: #{rank} via pattern: {ranking_pattern}")
                                        break
                            except (ValueError, IndexError) as e:
                                logger.debug(f"   Failed to extract HKUST {year} rank from '{match}': {e}")
                                continue
                        if year in hkust_rankings:  # Found a ranking for this year
                            break

        logger.info(f"✅ Ranking data extraction completed. CUHK: {len(cuhk_rankings)} entries, HKUST: {len(hkust_rankings)} entries")
        return {
            "cuhk_rankings": cuhk_rankings,
            "hkust_rankings": hkust_rankings
        }

    def _validate_and_integrate_ranking_data(self, ranking_data: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, int]]:
        """
        Validate and integrate ranking data with comprehensive data quality checks
        """
        logger.info("🔍 Validating and integrating ranking data...")

        validated_data = {
            "cuhk_rankings": {},
            "hkust_rankings": {}
        }

        # Validate CUHK rankings
        for year, rank in ranking_data.get("cuhk_rankings", {}).items():
            try:
                # Convert year to string if it's not already
                year_str = str(year)

                # Validate year format and range
                if not year_str.startswith("20") or len(year_str) != 4:
                    logger.warning(f"Invalid CUHK year format: {year_str}")
                    continue

                year_int = int(year_str)
                if year_int < 2000 or year_int > 2099:
                    logger.warning(f"CUHK year out of reasonable range: {year_int}")
                    continue

                # Validate rank format and range
                rank_int = int(rank)
                if rank_int < 1 or rank_int > 1000:
                    logger.warning(f"CUHK rank out of reasonable range: {rank_int}")
                    continue

                # Store validated data
                validated_data["cuhk_rankings"][year_str] = rank_int
                logger.debug(f"✅ Validated CUHK ranking: {year_str} -> #{rank_int}")

            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid CUHK ranking data - year: {year}, rank: {rank}, error: {e}")
                continue

        # Validate HKUST rankings
        for year, rank in ranking_data.get("hkust_rankings", {}).items():
            try:
                # Convert year to string if it's not already
                year_str = str(year)

                # Validate year format and range
                if not year_str.startswith("20") or len(year_str) != 4:
                    logger.warning(f"Invalid HKUST year format: {year_str}")
                    continue

                year_int = int(year_str)
                if year_int < 2000 or year_int > 2099:
                    logger.warning(f"HKUST year out of reasonable range: {year_int}")
                    continue

                # Validate rank format and range
                rank_int = int(rank)
                if rank_int < 1 or rank_int > 1000:
                    logger.warning(f"HKUST rank out of reasonable range: {rank_int}")
                    continue

                # Store validated data
                validated_data["hkust_rankings"][year_str] = rank_int
                logger.debug(f"✅ Validated HKUST ranking: {year_str} -> #{rank_int}")

            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid HKUST ranking data - year: {year}, rank: {rank}, error: {e}")
                continue

        # Sort rankings by year for consistent output
        validated_data["cuhk_rankings"] = dict(sorted(validated_data["cuhk_rankings"].items()))
        validated_data["hkust_rankings"] = dict(sorted(validated_data["hkust_rankings"].items()))

        logger.info(f"✅ Data validation completed. Valid CUHK: {len(validated_data['cuhk_rankings'])} entries, Valid HKUST: {len(validated_data['hkust_rankings'])} entries")
        return validated_data

    def _apply_rerank(
        self,
        query: str,
        hits: List[SearchHit],
        *,
        limit: Optional[int] = None,
    ) -> Tuple[List[SearchHit], List[Dict[str, object]]]:
        if not self.reranker or not hits:
            return hits, []

        try:
            reranked = self.reranker.rerank(query, hits)
        except Exception as exc:  # pragma: no cover - best effort resilience
            return hits, [{"error": str(exc)}]

        filtered: List[SearchHit] = []
        metadata: List[Dict[str, object]] = []
        domain_counts: Dict[str, int] = {}
        max_results = limit or len(reranked)

        for item in reranked:
            if item.score < self.min_rerank_score:
                metadata.append(
                    {
                        "url": item.hit.url,
                        "score": item.score,
                        "dropped": "below_min_score",
                    }
                )
                continue

            domain = self._extract_domain(item.hit.url)
            if domain and domain_counts.get(domain, 0) >= self.max_per_domain:
                metadata.append(
                    {
                        "url": item.hit.url,
                        "score": item.score,
                        "dropped": "per_domain_limit",
                    }
                )
                continue

            filtered.append(item.hit)
            metadata.append(
                {
                    "url": item.hit.url,
                    "score": item.score,
                    "kept": True,
                }
            )

            if domain:
                domain_counts[domain] = domain_counts.get(domain, 0) + 1

            if len(filtered) >= max_results:
                break

        if not filtered:
            return hits, metadata

        return filtered, metadata

    @staticmethod
    def _extract_domain(url: str) -> Optional[str]:
        if not url:
            return None
        return urlparse(url).netloc or None

    def answer(
        self,
        query: str,
        *,
        search_query: Optional[str] = None,
        num_search_results: int = 5,
        per_source_limit: Optional[int] = None,
        num_retrieved_docs: int = 5,
        max_tokens: int = 5000,
        temperature: float = 0.3,
        enable_search: bool = True,
        enable_local_docs: bool = True,
        reference_limit: Optional[int] = None,
        freshness: Optional[str] = None,
        date_restrict: Optional[str] = None,
        timing_recorder: Optional[TimingRecorder] = None,
        images: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, object]:
        """Answer a query using the SearchRAG pipeline."""
        hits: List[SearchHit] = []
        effective_query = search_query.strip() if search_query else query
        search_error: Optional[str] = None

        search_warnings: List[str] = []
        raw_hits: List[SearchHit] = []

        # 执行搜索
        if enable_search:
            try:
                per_source_cap = per_source_limit if per_source_limit is not None else num_search_results
                
                # Calculate fetch limit to ensure we get enough candidates for reranking
                # If reranker is enabled, we want to fetch (per_source_cap * num_sources) results
                # CombinedSearchClient will aggregate them.
                fetch_limit = num_search_results
                if self.reranker:
                    num_sources = 1
                    if hasattr(self.search_client, "clients"):
                        num_sources = len(self.search_client.clients)
                    fetch_limit = per_source_cap * num_sources

                raw_hits = self.search_client.search(
                    effective_query,
                    num_results=fetch_limit,
                    per_source_limit=per_source_cap,
                    freshness=freshness,
                    date_restrict=date_restrict,
                )
                hits = list(raw_hits)
            except Exception as exc:
                # Surface search errors while still letting the LLM see local docs
                hits = []
                search_error = str(exc)
            finally:
                if timing_recorder:
                    timings_getter = getattr(self.search_client, "get_last_timings", None)
                    if callable(timings_getter):
                        timing_recorder.extend_search_timings(timings_getter())

        if raw_hits:
            get_last_errors = getattr(self.search_client, "get_last_errors", None)
            if callable(get_last_errors):
                errors = get_last_errors() or []
                if errors:
                    for item in errors:
                        source = str(item.get("source") or "搜索服务")
                        detail = str(item.get("error") or "未知错误")
                        if source.lower().startswith("mcp"):
                            search_warnings.append(f"{source} 未正常工作，已使用其他搜索结果。原因：{detail}")
                        else:
                            search_warnings.append(f"{source} 出现异常：{detail}")

        # 应用重排序
        hits, rerank_meta = self._apply_rerank(query, hits, limit=num_search_results)

        # 检查是否需要LLM fallback（针对时间变化类查询）
        if enable_search and self._is_temporal_change_query(query):
            # 对于时间变化查询，总是尝试执行颗粒化搜索以获取更全面的历史数据
            print("🔄 检测到时间变化查询，启动LLM fallback机制以获取历史数据...")
            fallback_hits = self._perform_granular_search_fallback(query, effective_query, num_search_results, per_source_cap, freshness, date_restrict, timing_recorder)
            if fallback_hits:
                hits = fallback_hits
                print(f"✅ Fallback搜索完成，获得{len(fallback_hits)}条结果")
        
        # 检查是否是排名查询，如果是则提取排名数据
        is_ranking_query = any(keyword in query.lower() for keyword in ['排名', 'ranking', 'rank'])
        ranking_data = None
        if is_ranking_query:
            logger.info("🔍 检测到排名查询，提取排名数据...")
            ranking_data = self._extract_ranking_data(hits)

            # Validate and integrate the extracted ranking data
            validated_ranking_data = self._validate_and_integrate_ranking_data(ranking_data)

            logger.info(f"✅ 提取到CUHK排名数据: {validated_ranking_data['cuhk_rankings']}")
            logger.info(f"✅ 提取到HKUST排名数据: {validated_ranking_data['hkust_rankings']}")

            # Use validated data instead of raw extracted data
            ranking_data = validated_ranking_data
        
        # 检索本地文档
        retrieved_docs: List[Document] = []
        if enable_local_docs and self.vector_store:
            retrieved_docs = self.vector_store.search(query, k=num_retrieved_docs)

        # 构建提示
        if is_ranking_query and ranking_data:
            ranking_info = "\n\n提取的排名数据:\n"
            if ranking_data['cuhk_rankings']:
                ranking_info += "CUHK排名:\n"
                for year, rank in sorted(ranking_data['cuhk_rankings'].items()):
                    ranking_info += f"- {year}年: #{rank}\n"
            
            if ranking_data['hkust_rankings']:
                ranking_info += "HKUST排名:\n"
                for year, rank in sorted(ranking_data['hkust_rankings'].items()):
                    ranking_info += f"- {year}年: #{rank}\n"
            
            user_prompt = self.build_prompt(query, hits, retrieved_docs, ranking_info)
        else:
            user_prompt = self.build_prompt(query, hits, retrieved_docs)

        # 调用LLM生成答案
        response_start = time.perf_counter()
        try:
            response = self.llm_client.chat(
                system_prompt=self.system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                images=images,
            )
        finally:
            if timing_recorder:
                duration_ms = (time.perf_counter() - response_start) * 1000
                timing_recorder.record_llm_call(
                    label="search_rag_answer",
                    duration_ms=duration_ms,
                    provider=getattr(self.llm_client, "provider", None),
                    model=getattr(self.llm_client, "model_id", None),
                )

        # 构建答案
        answer = response.get("content")
        reference_hits = hits if reference_limit is None else hits[:reference_limit]
        
        if answer:
            # 添加网络来源引用
            if reference_hits:
                answer += "\n\n**网络来源：**\n"
                for idx, hit in enumerate(reference_hits, start=1):
                    title = hit.title or f"Result {idx}"
                    url = hit.url or ""
                    bullet = f"{idx}. [{title}]({url})" if url else f"{idx}. {title}"
                    answer += f"{bullet}\n"
            
            # 添加本地文档引用
            if retrieved_docs:
                answer += "\n\n**本地文档来源：**\n"
                for idx, doc in enumerate(retrieved_docs, start=1):
                    source = doc.source or f"文档 {idx}"
                    answer += f"{idx}. {source}\n"

        # 构建返回结果
        payload: Dict[str, object] = {
            "query": query,
            "answer": answer,
            "search_hits": [asdict(hit) for hit in hits],
            "retrieved_docs": [asdict(doc) for doc in retrieved_docs],
            "llm_raw": response.get("raw"),
            "llm_warning": response.get("warning"),
            "llm_error": response.get("error"),
            "rerank": rerank_meta or None,
        }
        
        if search_error:
            payload["search_error"] = search_error
        if search_warnings:
            payload["search_warnings"] = search_warnings
        if ranking_data:
            payload["ranking_data"] = ranking_data

        return payload

    def answer_stream(
        self,
        query: str,
        *,
        search_query: Optional[str] = None,
        num_search_results: int = 5,
        per_source_limit: Optional[int] = None,
        num_retrieved_docs: int = 5,
        max_tokens: int = 5000,
        temperature: float = 0.3,
        enable_search: bool = True,
        enable_local_docs: bool = True,
        reference_limit: Optional[int] = None,
        freshness: Optional[str] = None,
        date_restrict: Optional[str] = None,
        timing_recorder: Optional[TimingRecorder] = None,
    ):
        import json
        
        # Prefer keyword-focused query generated upstream when available.
        effective_query = search_query.strip() if search_query else query

        per_source_cap = per_source_limit if per_source_limit is not None else num_search_results
        hits = []
        search_warnings: List[str] = []
        
        # 执行搜索
        if enable_search:
            hits = self.search_client.search(
                effective_query,
                num_results=num_search_results,
                per_source_limit=per_source_cap,
                freshness=freshness,
                date_restrict=date_restrict,
            )
            
            if timing_recorder:
                timings_getter = getattr(self.search_client, "get_last_timings", None)
                if callable(timings_getter):
                    timing_recorder.extend_search_timings(timings_getter())
            
            get_last_errors = getattr(self.search_client, "get_last_errors", None)
            if callable(get_last_errors):
                errors = get_last_errors() or []
                if hits and errors:
                    for item in errors:
                        source = str(item.get("source") or "搜索服务")
                        detail = str(item.get("error") or "未知错误")
                        if source.lower().startswith("mcp"):
                            search_warnings.append(f"{source} 未正常工作，已使用其他搜索结果。原因：{detail}")
                        else:
                            search_warnings.append(f"{source} 出现异常：{detail}")
        
        # 应用重排序
        hits, rerank_meta = self._apply_rerank(query, hits, limit=num_search_results)

        # 检查是否需要LLM fallback（针对时间变化类查询）
        if enable_search and self._is_temporal_change_query(query):
            # 对于时间变化查询，总是尝试执行颗粒化搜索以获取更全面的历史数据
            print("🔄 检测到时间变化查询，启动LLM fallback机制以获取历史数据...")
            fallback_hits = self._perform_granular_search_fallback(query, effective_query, num_search_results, per_source_cap, freshness, date_restrict, timing_recorder)
            if fallback_hits:
                hits = fallback_hits
                print(f"✅ Fallback搜索完成，获得{len(fallback_hits)}条结果")

        # 检索本地文档
        retrieved_docs: List[Document] = []
        if enable_local_docs and self.vector_store:
            retrieved_docs = self.vector_store.search(query, k=num_retrieved_docs)

        # First, yield preliminary data
        preliminary_data = {
            "query": query,
            "search_hits": [asdict(hit) for hit in hits],
            "retrieved_docs": [asdict(doc) for doc in retrieved_docs],
            "rerank": rerank_meta or None,
            "search_query": effective_query,
        }
        if search_warnings:
            preliminary_data["search_warnings"] = search_warnings
        
        yield json.dumps({"type": "preliminary", "data": preliminary_data})

        user_prompt = self.build_prompt(query, hits, retrieved_docs)
        response_start = time.perf_counter()
        
        # Stream the response
        full_answer = ""
        try:
            stream = self.llm_client.chat_stream(
                system_prompt=self.system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            for chunk in stream:
                if chunk.startswith("Error:"):
                    yield json.dumps({"type": "error", "data": chunk})
                    return
                full_answer += chunk
                yield json.dumps({"type": "content", "data": chunk})

        finally:
            if timing_recorder:
                duration_ms = (time.perf_counter() - response_start) * 1000
                timing_recorder.record_llm_call(
                    label="search_rag_answer_stream",
                    duration_ms=duration_ms,
                    provider=getattr(self.llm_client, "provider", None),
                    model=getattr(self.llm_client, "model_id", None),
                )

        # Finally, yield the references
        reference_hits = hits if reference_limit is None else hits[:reference_limit]
        if full_answer:
            # 添加网络来源引用
            if reference_hits:
                reference_text = "\n\n**网络来源：**\n"
                for idx, hit in enumerate(reference_hits, start=1):
                    title = hit.title or f"Result {idx}"
                    url = hit.url or ""
                    bullet = f"{idx}. [{title}]({url})" if url else f"{idx}. {title}"
                    reference_text += f"{bullet}\n"
                yield json.dumps({"type": "references", "data": reference_text})
            
            # 添加本地文档引用
            if retrieved_docs:
                reference_text = "\n\n**本地文档来源：**\n"
                for idx, doc in enumerate(retrieved_docs, start=1):
                    source = doc.source or f"文档 {idx}"
                    reference_text += f"{idx}. {source}\n"
                yield json.dumps({"type": "local_references", "data": reference_text})
