import json
import requests
import os
import sys
from typing import Dict, Any, Optional, List

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config_validation import configured_value


class SportsAPI:
    """
    API-Sports 官网直连多体育 API 客户端（不依赖 RapidAPI）

    支持：
    - NBA v2 (Direct)
    - AFL v1 (Direct)
    - Baseball v1 (Direct)
    - Football v3 (Direct)
    - Basketball v1 (Direct)

    用法:
    api = SportsAPI()
    leagues = api.get_leagues('basketball_v1')
    games = api.get_games('basketball_v1', league_id=12, season='2024-2025')
    """

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            config_path = os.environ.get("NLP_CONFIG_PATH") or os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config.json",
            )

        with open(config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

        # 只需要官网 key
        self.apisports_key = configured_value(self.config.get("APISPORTS_KEY"))
        if not self.apisports_key:
            raise ValueError("APISPORTS_KEY missing in runtime configuration")

        # 官网直连通用 headers：x-apisports-key
        # docs for all sports show this header requirement
        # :contentReference[oaicite:2]{index=2}
        common_headers = {
            "x-apisports-key": self.apisports_key
        }

        # API分类配置（全部走官网直连域名）
        self.apis: Dict[str, Dict[str, Any]] = {
            "nba_v2": {
                "name": "NBA v2 (Direct)",
                "base_url": "https://v2.nba.api-sports.io",
                "headers": common_headers,
                "leagues_path": "/leagues",
                "games_path": "/games",
            },
            "afl_v1": {
                "name": "AFL v1 (Direct)",
                "base_url": "https://v1.afl.api-sports.io",
                "headers": common_headers,
                "leagues_path": "/leagues",
                "games_path": "/games",
            },
            "baseball_v1": {
                "name": "Baseball v1 (Direct)",
                "base_url": "https://v1.baseball.api-sports.io",
                "headers": common_headers,
                "leagues_path": "/leagues",
                "games_path": "/games",
            },
            "football_v3": {
                "name": "Football v3 (Direct)",
                "base_url": "https://v3.football.api-sports.io",
                "headers": common_headers,
                "leagues_path": "/leagues",
                "games_path": "/fixtures",  # Football 用 fixtures
            },
            "basketball_v1": {
                "name": "Basketball v1 (Direct)",
                "base_url": "https://v1.basketball.api-sports.io",
                "headers": common_headers,
                "leagues_path": "/leagues",
                "games_path": "/games",
                "nba_league_id": 12,
            },
        }

    def _request(
        self,
        sport_key: str,
        path: str,
        params: Dict[str, Any] = None
    ) -> Optional[Dict[str, Any]]:
        """通用请求（官网直连）"""
        if sport_key not in self.apis:
            raise ValueError(
                f"Unknown sport: {sport_key}. Available: {list(self.apis.keys())}"
            )

        api_config = self.apis[sport_key]
        url = api_config["base_url"] + path

        try:
            response = requests.get(
                url, headers=api_config["headers"], params=params, timeout=10
            )
            if response.status_code == 200:
                return response.json()
            else:
                # 常见：401 key 错 / 429 超限 / 403 未订阅
                print(
                    f"{api_config['name']} Error {response.status_code}: "
                    f"{response.text[:200]}"
                )
                return None
        except Exception as e:
            print(f"{api_config['name']} Exception: {str(e)}")
            return None

    def get_leagues(self, sport_key: str) -> Optional[List[Dict]]:
        """获取联赛列表"""
        data = self._request(sport_key, self.apis[sport_key]["leagues_path"])
        return data.get("response") if data else None

    def get_games(
        self,
        sport_key: str,
        league_id: int = None,
        season: str = None,
        **extra_params,
    ) -> Optional[List[Dict]]:
        """获取比赛，支持 league/season 过滤"""
        api_config = self.apis[sport_key]
        params = {k: v for k, v in extra_params.items()}
        if league_id is not None:
            params["league"] = league_id
        if season is not None:
            params["season"] = season

        path = api_config["games_path"]
        data = self._request(sport_key, path, params)
        return data.get("response") if data else None

    # 兼容不同 sport 里 league 字段结构的小差异
    def _get_league_name(self, league_item: Dict[str, Any]) -> str:
        if not isinstance(league_item, dict):
            return ""
        league_obj = league_item.get("league") or league_item.get("league_info") or {}
        if isinstance(league_obj, dict):
            return league_obj.get("name", "") or league_item.get("name", "")
        return league_item.get("name", "")

    def find_league(self, sport_key: str, name_contains: str) -> Optional[Dict]:
        """查找特定联赛（名字包含）"""
        leagues = self.get_leagues(sport_key)
        if not leagues:
            return None

        target = name_contains.upper()
        for l in leagues:
            name = self._get_league_name(l).upper()
            if target in name:
                return l
        return None


if __name__ == "__main__":
    api = SportsAPI()

    for sport_key in api.apis:
        print(f"\n=== {api.apis[sport_key]['name']} ===")
        leagues = api.get_leagues(sport_key)
        print(f"Leagues count: {len(leagues) if leagues else 0}")

        if sport_key == "basketball_v1":
            nba = api.find_league(sport_key, "NBA")
            print(f"NBA ID: {nba.get('league', {}).get('id') if nba else 'Not found'}")
            games = api.get_games(sport_key, league_id=12, season="2024-2025")
            print(f"NBA 2024-2025 games count: {len(games) if games else 0}")
