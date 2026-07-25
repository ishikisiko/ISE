"""Deterministic finance preflight and market-data evidence source."""

from __future__ import annotations

import math
import importlib.util
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

from evidence import EvidenceItem, RetrievalOptions
from utils.config_validation import configured_value

from skills._base import RuntimeSkillHandler
from skills.contracts import PreflightResult, SkillManifest

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - availability gate prevents registration
    yf = None


ASSET_ALIASES = {
    "hang seng": "^HSI",
    "恒生指数": "^HSI",
    "恒指": "^HSI",
    "nasdaq": "^IXIC",
    "纳斯达克": "^IXIC",
    "纳指": "^IXIC",
    "dow jones": "^DJI",
    "道琼斯": "^DJI",
    "道指": "^DJI",
    "s&p 500": "^GSPC",
    "标普500": "^GSPC",
    "标普": "^GSPC",
    "bitcoin": "BTC-USD",
    "比特币": "BTC-USD",
    "比特幣": "BTC-USD",
    "ethereum": "ETH-USD",
    "以太坊": "ETH-USD",
    "以太幣": "ETH-USD",
    "狗狗币": "DOGE-USD",
    "狗狗幣": "DOGE-USD",
    "solana": "SOL-USD",
    "索拉纳": "SOL-USD",
    "苹果公司": "AAPL",
    "苹果": "AAPL",
    "apple": "AAPL",
    "微软公司": "MSFT",
    "微软": "MSFT",
    "microsoft": "MSFT",
    "谷歌公司": "GOOGL",
    "谷歌": "GOOGL",
    "google": "GOOGL",
    "亚马逊公司": "AMZN",
    "亚马逊": "AMZN",
    "amazon": "AMZN",
    "特斯拉公司": "TSLA",
    "特斯拉": "TSLA",
    "tesla": "TSLA",
    "英伟达公司": "NVDA",
    "英伟达": "NVDA",
    "nvidia": "NVDA",
    "英特尔公司": "INTC",
    "英特尔": "INTC",
    "intel": "INTC",
    "阿里巴巴集团": "BABA",
    "阿里巴巴": "BABA",
    "alibaba": "BABA",
    "腾讯控股": "0700.HK",
    "腾讯": "0700.HK",
    "tencent": "0700.HK",
    "台积电": "TSM",
    "茅台": "600519.SS",
    "贵州茅台": "600519.SS",
    "京东": "JD",
    "百度": "BIDU",
    "小米集团": "1810.HK",
    "小米": "1810.HK",
    "美团": "3690.HK",
    "美元兑人民币": "CNY=X",
    "美元兑人民幣": "CNY=X",
    "usd/cny": "CNY=X",
    "usd to cny": "CNY=X",
}

FINANCE_INTENT_TERMS = (
    "股票",
    "股价",
    "行情",
    "财报",
    "財報",
    "市值",
    "market cap",
    "stock",
    "share price",
    "quote",
    "earnings",
    "证券",
    "證券",
    "指数",
    "指數",
    "基金",
    "汇率",
    "匯率",
    "exchange rate",
)

HISTORY_TERMS = (
    "过去",
    "過去",
    "历史",
    "歷史",
    "走势",
    "走勢",
    "趋势",
    "趨勢",
    "表现",
    "表現",
    "比较",
    "比較",
    "近",
    "最近",
    "前",
    "past",
    "history",
    "trend",
    "performance",
    "compare",
)

REASONING_TERMS = (
    "为什么",
    "原因",
    "影响",
    "影響",
    "分析",
    "why",
    "reason",
    "cause",
    "news",
    "analysis",
)

TICKER_STOPWORDS = {
    "AND", "THE", "FOR", "WHY", "USD", "HKD", "RMB", "STOCK", "PRICE",
    "PAST", "COMPARE", "WITH", "FROM", "WHAT", "WHEN", "WHERE", "HOW",
    "TODAY", "NEWS", "ANALYSIS", "TREND", "HISTORY", "VS", "PRO", "API",
    "AI", "MODEL", "DATA", "OPENAI", "CLAUDE", "FABLE", "CURRENT", "TIME",
}


class FinanceSkillHandler(RuntimeSkillHandler):
    """Finance skill mounted directly on the unified EvidenceSource contract."""

    def __init__(self, *, config: Dict[str, Any], manifest: SkillManifest) -> None:
        super().__init__(config=config, manifest=manifest)
        self.display_name = "Finance Market Data"
        self.finnhub_api_key = configured_value(
            config.get("FINNHUB_API_KEY") or os.getenv("FINNHUB_API_KEY")
        )

    def handles_query(self, query: str) -> bool:
        """Route only explicit market intent; money alone is never sufficient."""

        text = str(query or "").strip()
        lowered = text.lower()
        if not text:
            return False
        explicit_symbol = bool(
            re.search(r"(?<![A-Za-z0-9])\$[A-Za-z]{1,5}(?![A-Za-z])", text)
            or re.search(r"\([A-Z]{1,5}\)", text)
            or re.search(r"(?<!\d)\d{6}(?!\d)", text)
        )
        known_asset = any(alias in lowered for alias in ASSET_ALIASES)
        market_intent = any(term in lowered for term in FINANCE_INTENT_TERMS)
        return explicit_symbol or known_asset or market_intent

    def preflight(self, args: Dict[str, Any]) -> PreflightResult:
        query = str((args or {}).get("query") or "").strip()
        if not query:
            return PreflightResult.reject("query_required")
        if not self.handles_query(query):
            return PreflightResult.reject("not_finance_query", query=query)

        symbols = self.extract_symbols(query)
        if not symbols:
            return PreflightResult.reject("symbol_required", query=query)

        period, start, end = self._history_window(query)
        is_history = self._is_history_query(query) or bool(start)
        return PreflightResult.accept(
            query=query,
            symbols=symbols,
            mode="history" if is_history else "quote",
            period=period,
            start=start,
            end=end,
            continue_search=any(term in query.lower() for term in REASONING_TERMS),
            include_market_cap=any(
                term in query.lower() for term in ("市值", "market cap", "market capitalization")
            ),
        )

    @staticmethod
    def extract_symbols(query: str) -> List[str]:
        """Extract symbols without an LLM or network call."""

        text = str(query or "")
        lowered = text.lower()
        symbols = {
            symbol for alias, symbol in ASSET_ALIASES.items() if alias in lowered
        }
        symbols.update(
            match.upper()
            for match in re.findall(
                r"(?<![A-Za-z0-9])\$([A-Za-z]{1,5})(?![A-Za-z])", text
            )
        )
        symbols.update(re.findall(r"\(([A-Z]{1,5})\)", text))
        symbols.update(re.findall(r"(?<!\d)\d{6}(?!\d)", text))

        has_market_context = any(term in lowered for term in FINANCE_INTENT_TERMS)
        if has_market_context:
            candidates = re.findall(r"(?<![A-Za-z])[A-Z]{2,5}(?![A-Za-z])", text)
            symbols.update(candidate for candidate in candidates if candidate not in TICKER_STOPWORDS)
        return sorted(symbols)

    @staticmethod
    def _is_history_query(query: str) -> bool:
        lowered = query.lower()
        return any(term in lowered for term in HISTORY_TERMS) or bool(
            re.search(r"\d+\s*(?:天|日|周|月|年|days?|weeks?|months?|years?)", lowered)
        )

    @staticmethod
    def _history_window(query: str) -> tuple[str, Optional[str], Optional[str]]:
        lowered = query.lower()
        chinese_numbers = {
            "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
        }
        year_match = re.search(
            r"(?:前|近|过去|最近)?([一二两三四五六七八九十]+|\d+)\s*(?:年|years?)",
            lowered,
        )
        if year_match:
            raw = year_match.group(1)
            years = int(raw) if raw.isdigit() else chinese_numbers.get(raw, 3)
            years = max(1, min(years, 20))
            end = datetime.now().date().isoformat()
            start = (datetime.now() - timedelta(days=years * 365)).date().isoformat()
            period = "1y" if years <= 1 else "2y" if years <= 2 else "5y" if years <= 5 else "10y"
            return period, start, end

        day_match = re.search(r"(\d+)\s*(?:天|days?)", lowered)
        if day_match:
            days = max(1, min(int(day_match.group(1)), 3650))
            period = "5d" if days <= 5 else "1mo" if days <= 30 else "3mo" if days <= 90 else "1y"
            return period, None, None
        return "1mo" if FinanceSkillHandler._is_history_query(query) else "1d", None, None

    def retrieve(self, query: str, options: RetrievalOptions) -> List[EvidenceItem]:
        """EvidenceSource adapter used by plan execution and tests."""

        preflight = self.preflight({"query": query})
        if not preflight.accepted:
            return []
        return self.run(preflight.normalized_args, options)

    def run(self, args: Dict[str, Any], options: RetrievalOptions) -> List[EvidenceItem]:
        mode = str(args.get("mode") or "quote")
        symbols = list(args.get("symbols") or [])
        items: List[EvidenceItem] = []
        for rank, symbol in enumerate(symbols, start=1):
            if mode == "history":
                data = self._query_history(
                    symbol,
                    period=str(args.get("period") or "1mo"),
                    start=args.get("start"),
                    end=args.get("end"),
                    timing_recorder=options.timing_recorder,
                )
            else:
                data = self._query_quote(
                    symbol,
                    timing_recorder=options.timing_recorder,
                    require_market_cap=bool(args.get("include_market_cap")),
                )
            if not data or data.get("error"):
                continue
            data = dict(data)
            data["symbol"] = symbol
            answer = self.format_answer(symbol, data, mode=mode)
            provider = str(data.get("source_name") or "market_data")
            reference = str(data.get("endpoint") or provider)
            metadata = {
                "domain": "finance",
                "skill": self.manifest.name,
                "tool_name": self.manifest.tool_name,
                "provider": provider,
                "data": data,
                "continue_search": bool(args.get("continue_search")),
                "source_tier": "authoritative",
                "retrieval_kind": "skill",
                "canonical_reference": reference,
            }
            metadata.update(
                {
                    key: options.metadata[key]
                    for key in ("originating_tool_call", "covered_claims")
                    if options.metadata.get(key) is not None
                }
            )
            items.append(
                EvidenceItem(
                    source_type=self.source_type.value,
                    source_id=self.source_id,
                    title=f"{symbol} {mode}",
                    content=answer,
                    reference=reference,
                    snippet=" ".join(answer.split())[:320],
                    metadata=metadata,
                    rank=rank,
                )
            )
        return items

    def _query_quote(
        self,
        symbol: str,
        *,
        timing_recorder: Optional[Any],
        require_market_cap: bool = False,
    ) -> Dict[str, Any]:
        if require_market_cap and yf is not None:
            result = self._call_yfinance_quote(symbol, timing_recorder=timing_recorder)
            if result and not result.get("error") and result.get("marketCap") is not None:
                return result
        if self.finnhub_api_key:
            result = self._call_finnhub(symbol, timing_recorder=timing_recorder)
            if result and not result.get("error") and result.get("c") not in {None, 0}:
                return result
        if yf is not None:
            result = self._call_yfinance_quote(symbol, timing_recorder=timing_recorder)
            if result and not result.get("error"):
                return result
        if importlib.util.find_spec("yahoo_fin") is not None:
            result = self._call_yahoo_fin(symbol, timing_recorder=timing_recorder)
            if result and not result.get("error"):
                return result
        return {"error": "no_price_providers_available"}

    def _call_finnhub(self, symbol: str, *, timing_recorder: Optional[Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        success = False
        try:
            response = requests.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": symbol, "token": self.finnhub_api_key},
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return {"error": "invalid_finnhub_response"}
            payload["source_name"] = "Finnhub"
            payload["endpoint"] = "https://finnhub.io/api/v1/quote"
            success = True
            return payload
        except Exception as exc:
            return {"error": self._safe_error(exc)}
        finally:
            self._record_provider_timing(
                timing_recorder,
                "finnhub",
                "Finnhub Quote",
                started,
                success=success,
            )

    def _call_yfinance_quote(self, symbol: str, *, timing_recorder: Optional[Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        success = False
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
            fast = getattr(ticker, "fast_info", None)

            def pick(*names: str) -> Any:
                for name in names:
                    value = info.get(name)
                    if value is None and fast is not None:
                        try:
                            value = fast.get(name)
                        except Exception:
                            value = getattr(fast, name, None)
                    if value is not None:
                        return value
                return None

            current = pick("currentPrice", "regularMarketPrice", "lastPrice", "last_price")
            if current is None:
                return {"error": "no_quote_data"}
            success = True
            return {
                "c": current,
                "h": pick("dayHigh", "regularMarketDayHigh", "day_high"),
                "l": pick("dayLow", "regularMarketDayLow", "day_low"),
                "o": pick("open", "regularMarketOpen", "open"),
                "pc": pick("previousClose", "regularMarketPreviousClose", "previous_close"),
                "currency": pick("currency"),
                "marketCap": pick("marketCap", "market_cap"),
                "source_name": "Yahoo Finance (yfinance)",
                "endpoint": f"https://finance.yahoo.com/quote/{symbol}",
            }
        except Exception as exc:
            return {"error": self._safe_error(exc)}
        finally:
            self._record_provider_timing(
                timing_recorder,
                "yfinance",
                "yfinance Quote",
                started,
                success=success,
            )

    def _call_yahoo_fin(self, symbol: str, *, timing_recorder: Optional[Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        success = False
        try:
            from yahoo_fin import stock_info

            price = stock_info.get_live_price(symbol)
            if price is None:
                return {"error": "no_quote_data"}
            success = True
            return {
                "c": float(price),
                "source_name": "Yahoo Finance (yahoo_fin)",
                "endpoint": f"https://finance.yahoo.com/quote/{symbol}",
            }
        except Exception as exc:
            return {"error": self._safe_error(exc)}
        finally:
            self._record_provider_timing(
                timing_recorder,
                "yahoo_fin",
                "yahoo_fin Quote",
                started,
                success=success,
            )

    def _query_history(
        self,
        symbol: str,
        *,
        period: str,
        start: Optional[str],
        end: Optional[str],
        timing_recorder: Optional[Any],
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        success = False
        try:
            if yf is None:
                return {"error": "yfinance_not_installed"}
            ticker = yf.Ticker(symbol)
            frame = ticker.history(start=start, end=end, interval="1d") if start and end else ticker.history(period=period)
            if frame is None or frame.empty or len(frame) < 2:
                return {"error": "insufficient_history_data"}
            closes = frame["Close"].dropna()
            if len(closes) < 2:
                return {"error": "insufficient_history_data"}
            start_price = float(closes.iloc[0])
            end_price = float(closes.iloc[-1])
            daily_returns = closes.pct_change().dropna()
            volatility = float(daily_returns.std() * math.sqrt(252) * 100) if len(daily_returns) else None
            rolling_peak = closes.cummax()
            drawdown = (closes - rolling_peak) / rolling_peak
            yearly_closes = [
                {"year": int(year), "close": float(group.iloc[-1])}
                for year, group in closes.groupby(closes.index.year)
            ]
            yearly_returns = []
            for year, group in closes.groupby(closes.index.year):
                if len(group) > 1 and float(group.iloc[0]) != 0:
                    yearly_returns.append(
                        {
                            "year": int(year),
                            "return": (float(group.iloc[-1]) / float(group.iloc[0]) - 1) * 100,
                        }
                    )
            success = True
            return {
                "start_date": str(closes.index[0].date()),
                "end_date": str(closes.index[-1].date()),
                "start_price": start_price,
                "end_price": end_price,
                "pct_change": (end_price / start_price - 1) * 100 if start_price else None,
                "high": float(frame["High"].max()),
                "low": float(frame["Low"].min()),
                "volatility": volatility,
                "max_drawdown": float(drawdown.min() * 100),
                "ma_20": float(closes.tail(20).mean()) if len(closes) >= 20 else None,
                "ma_50": float(closes.tail(50).mean()) if len(closes) >= 50 else None,
                "ma_200": float(closes.tail(200).mean()) if len(closes) >= 200 else None,
                "yearly_returns": yearly_returns,
                "yearly_closes": yearly_closes,
                "source_name": "Yahoo Finance (yfinance)",
                "endpoint": f"https://finance.yahoo.com/quote/{symbol}/history",
            }
        except Exception as exc:
            return {"error": self._safe_error(exc)}
        finally:
            self._record_provider_timing(
                timing_recorder,
                "yfinance_history",
                "yfinance History",
                started,
                success=success,
            )

    @staticmethod
    def _record_provider_timing(
        timing_recorder: Optional[Any],
        source: str,
        label: str,
        started: float,
        *,
        success: bool,
    ) -> None:
        if timing_recorder:
            duration_ms = (time.perf_counter() - started) * 1000
            timing_recorder.record_search_timing(
                source=source,
                label=label,
                duration_ms=duration_ms,
            )
            timing_recorder.record_tool_call(
                tool=source,
                duration_ms=duration_ms,
                success=success,
                extra={"label": label, "kind": "skill_provider"},
            )

    @staticmethod
    def format_answer(symbol: str, data: Dict[str, Any], *, mode: str) -> str:
        """Single deterministic formatter shared by plan and loop paths."""

        if mode == "history":
            start_price = FinanceSkillHandler._number(data.get("start_price"))
            end_price = FinanceSkillHandler._number(data.get("end_price"))
            pct = FinanceSkillHandler._number(data.get("pct_change"))
            high = FinanceSkillHandler._number(data.get("high"))
            low = FinanceSkillHandler._number(data.get("low"))
            lines = [
                f"{symbol} 历史行情 ({data.get('start_date', '?')} 至 {data.get('end_date', '?')})",
                f"收盘价: {FinanceSkillHandler._fmt(start_price)} -> {FinanceSkillHandler._fmt(end_price)}",
                f"期间涨跌: {FinanceSkillHandler._signed_pct(pct)}",
                f"期间区间: {FinanceSkillHandler._fmt(low)} - {FinanceSkillHandler._fmt(high)}",
            ]
            volatility = FinanceSkillHandler._number(data.get("volatility"))
            drawdown = FinanceSkillHandler._number(data.get("max_drawdown"))
            if volatility is not None:
                lines.append(f"年化波动率: {volatility:.2f}%")
            if drawdown is not None:
                lines.append(f"最大回撤: {drawdown:.2f}%")
            yearly = data.get("yearly_returns") or []
            if yearly:
                lines.append(
                    "年度收益: "
                    + ", ".join(
                        f"{row['year']} {FinanceSkillHandler._signed_pct(FinanceSkillHandler._number(row.get('return')))}"
                        for row in yearly[-5:]
                    )
                )
        else:
            current = FinanceSkillHandler._number(data.get("c"))
            previous = FinanceSkillHandler._number(data.get("pc"))
            high = FinanceSkillHandler._number(data.get("h"))
            low = FinanceSkillHandler._number(data.get("l"))
            opened = FinanceSkillHandler._number(data.get("o"))
            currency = str(data.get("currency") or "").strip()
            lines = [f"{symbol} 当前价: {FinanceSkillHandler._fmt(current)}{f' {currency}' if currency else ''}"]
            if current is not None and previous not in {None, 0}:
                change = current - previous
                lines.append(f"较前收盘: {change:+.2f} ({change / previous * 100:+.2f}%)")
            if opened is not None:
                lines.append(f"开盘: {opened:g}")
            if high is not None or low is not None:
                lines.append(
                    f"日内区间: {FinanceSkillHandler._fmt(low)} - {FinanceSkillHandler._fmt(high)}"
                )
            market_cap = FinanceSkillHandler._number(data.get("marketCap"))
            if market_cap is not None:
                lines.append(f"市值: {market_cap:,.0f}{f' {currency}' if currency else ''}")
        lines.append(f"数据源: {data.get('source_name', 'market data')}。市场数据可能延迟。")
        return "\n".join(lines)

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _fmt(value: Optional[float]) -> str:
        return "N/A" if value is None else f"{value:g}"

    @staticmethod
    def _signed_pct(value: Optional[float]) -> str:
        return "N/A" if value is None else f"{value:+.2f}%"
