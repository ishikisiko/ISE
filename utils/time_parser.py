"""
时间限制解析器 - 从查询中提取时间限制并转换为搜索API参数

支持自然语言时间表达：
- "最近三天"、"这三天"、"过去3天" -> day/d3
- "最近一周"、"这周"、"过去7天" -> week/d7
- "最近一个月"、"这个月" -> month/d30
- "最近一年"、"今年" -> year/d365
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple


@dataclass
class TimeConstraint:
    """时间限制约束"""
    
    # 原始查询
    original_query: str
    
    # 清理后的查询（去除时间限制词汇）
    cleaned_query: str
    
    # 时间范围（天数）
    days: Optional[int] = None
    
    # 通用 freshness 参数值（day/week/month/year，供 Tavily 等搜索源使用）
    freshness: Optional[str] = None
    
    # Google dateRestrict 参数值
    google_date_restrict: Optional[str] = None
    
    # 检测到的时间表达
    time_expression: Optional[str] = None


class TimeParser:
    """从查询中提取时间限制"""
    
    # 时间模式定义
    TIME_PATTERNS = [
        # 天 - 匹配 "最近N天"、"这N天"、"过去N天"
        (r'(?:最近|这|过去)?\s*([一二三四五六七八九十0-9]+)\s*天', 'day'),
        
        # 周 - 匹配 "最近一周"、"这周"、"过去一周"、"最近7天"
        (r'(?:最近|这|过去)?\s*(?:一|1)?\s*(?:周|星期|礼拜)', 'week'),
        
        # 月 - 匹配 "最近一个月"、"这个月"、"过去一月"、"最近30天"
        (r'(?:最近|这|过去)?\s*(?:一|1)?\s*(?:个)?\s*月', 'month'),
        
        # 年 (带数字) - 匹配 "前三年"、"最近三年"、"过去5年" 等
        (r'(?:最近|这|过去|前|近)?\s*([一二两三四五六七八九十0-9]+)\s*年', 'year_number'),
        
        # 年 (不带数字) - 匹配 "最近一年"、"今年"、"过去一年"
        (r'(?:最近|这|过去|今)?\s*(?:一|1)?\s*年', 'year'),
        
        # 小时 - 匹配 "最近N小时"、"这N小时"
        (r'(?:最近|这|过去)?\s*([一二三四五六七八九十0-9]+)\s*(?:小时|个小时)', 'hour'),
        
        # 现在 - 匹配 "现在"、"目前"、"如今"、"当前"
        (r'(?:现在|目前|如今|当前)', 'now'),
    ]
    
    # 中文数字映射
    CHINESE_NUMBERS = {
        '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
        '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
    }
    
    def __init__(self) -> None:
        self.patterns = [
            (re.compile(pattern, re.IGNORECASE), unit)
            for pattern, unit in self.TIME_PATTERNS
        ]
    
    def _parse_chinese_number(self, text: str) -> int:
        """将中文数字转换为阿拉伯数字"""
        if text.isdigit():
            return int(text)
        
        # 简单中文数字转换
        if text in self.CHINESE_NUMBERS:
            return self.CHINESE_NUMBERS[text]
        
        # 处理"十几"的情况
        if '十' in text:
            if len(text) == 1:  # 只有"十"
                return 10
            elif text.startswith('十'):  # "十几"
                return 10 + self.CHINESE_NUMBERS.get(text[1], 0)
            else:  # "几十几"
                parts = text.split('十')
                tens = self.CHINESE_NUMBERS.get(parts[0], 1) * 10
                ones = self.CHINESE_NUMBERS.get(parts[1], 0) if len(parts) > 1 else 0
                return tens + ones
        
        return 1  # 默认返回1
    
    def _calculate_days(self, number: int, unit: str) -> int:
        """根据单位计算对应的天数"""
        if unit == 'day':
            return number
        elif unit == 'hour':
            return max(1, number // 24)  # 至少1天
        elif unit == 'week':
            return 7
        elif unit == 'month':
            return 30
        elif unit == 'year':
            return 365
        elif unit == 'year_number':
            return number * 365  # 使用提取的数字乘以365天
        elif unit == 'now':
            return 30  # "现在" 默认对应最近一个月，保证信息的新鲜度但不过于严格
        return 1
    
    def _map_to_freshness(self, days: int) -> str:
        """将天数映射到通用 freshness 参数（day/week/month/year）"""
        if days <= 1:
            return 'day'
        elif days <= 7:
            return 'week'
        elif days <= 30:
            return 'month'
        else:
            return 'year'
    
    def _map_to_google_date_restrict(self, days: int) -> str:
        """将天数映射到 Google dateRestrict 参数"""
        # Google 格式: d[number] 或 w[number] 或 m[number] 或 y[number]
        # 优先使用天数来保证精确
        if days < 365:
            return f'd{days}'
        else:
            years = days // 365
            return f'y{years}'
    
    def parse(self, query: str) -> TimeConstraint:
        """
        解析查询中的时间限制
        
        Args:
            query: 用户查询
            
        Returns:
            TimeConstraint 对象，包含解析结果
        """
        # 初始化结果
        cleaned_query = query
        days: Optional[int] = None
        time_expression: Optional[str] = None
        
        # 尝试匹配时间模式
        for pattern, unit in self.patterns:
            match = pattern.search(query)
            if match:
                time_expression = match.group(0)
                
                # 提取数字（如果有）
                number = 1  # 默认值
                if match.groups():
                    number_text = match.group(1)
                    number = self._parse_chinese_number(number_text)
                
                # 计算天数
                days = self._calculate_days(number, unit)
                
                # 从查询中移除时间表达
                cleaned_query = pattern.sub('', query).strip()
                
                # 清理多余的空格
                cleaned_query = re.sub(r'\s+', ' ', cleaned_query)
                
                break  # 只匹配第一个时间表达
        
        # 构建结果
        result = TimeConstraint(
            original_query=query,
            cleaned_query=cleaned_query if cleaned_query else query,
        )
        
        if days is not None:
            result.days = days
            result.time_expression = time_expression
            result.freshness = self._map_to_freshness(days)
            result.google_date_restrict = self._map_to_google_date_restrict(days)
        
        return result
    
    def has_time_constraint(self, query: str) -> bool:
        """检查查询是否包含时间限制"""
        for pattern, _ in self.patterns:
            if pattern.search(query):
                return True
        return False


# 全局单例实例
_time_parser: Optional[TimeParser] = None


def get_time_parser() -> TimeParser:
    """获取时间解析器单例"""
    global _time_parser
    if _time_parser is None:
        _time_parser = TimeParser()
    return _time_parser


def parse_time_constraint(query: str) -> TimeConstraint:
    """便捷函数：解析查询中的时间限制"""
    parser = get_time_parser()
    return parser.parse(query)


# 示例使用
if __name__ == "__main__":
    # 测试用例
    test_queries = [
        "最近三天的AI新闻",
        "这一周的股市行情",
        "过去30天的天气预报",
        "最近一年的科技发展",
        "过去24小时的重大新闻",
        "机器学习的基本概念",  # 无时间限制
        "这个月的热门话题",
        "今年的诺贝尔奖得主",
    ]
    
    parser = TimeParser()
    
    print("时间限制解析测试：\n")
    for query in test_queries:
        result = parser.parse(query)
        print(f"原始查询: {result.original_query}")
        print(f"清理后查询: {result.cleaned_query}")
        
        if result.days:
            print(f"时间表达: {result.time_expression}")
            print(f"天数: {result.days}")
            print(f"freshness: {result.freshness}")
            print(f"Google dateRestrict: {result.google_date_restrict}")
        else:
            print("未检测到时间限制")
        
        print("-" * 60)
