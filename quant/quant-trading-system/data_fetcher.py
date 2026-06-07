"""
数据获取模块
- 通过Finance API网关获取A股行情数据
- 通过TickFlow API获取北向资金、龙虎榜等高级数据
- 支持批量获取、缓存、重试机制
"""

import datetime
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

import config

# ============================================================
# HTTP 客户端
# ============================================================

class FinanceClient:
    """Finance API 网关客户端"""

    def __init__(self):
        self.base_url = config.FINANCE_GATEWAY_URL + config.FINANCE_API_PREFIX
        self.headers = config.FINANCE_HEADERS.copy()
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self._cache: Dict[str, Tuple[float, Any]] = {}  # key -> (timestamp, data)
        self._cache_ttl = 60  # 缓存60秒
        self._offline = False  # 离线模式（API不可达时自动切换）
        self._offline_checked = False

    def _check_connectivity(self) -> bool:
        """快速检测API连通性"""
        if self._offline_checked:
            return not self._offline
        try:
            resp = self.session.get(f"{self.base_url}/v1/markets/search?search=test", timeout=5)
            self._offline_checked = True
            self._offline = False
            return True
        except requests.RequestException:
            self._offline_checked = True
            self._offline = True
            return False

    def _get(self, endpoint: str, params: dict = None) -> dict:
        """发送GET请求，带缓存和离线降级"""
        # 离线模式直接返回空
        if self._offline or not self._check_connectivity():
            raise RuntimeError(f"API离线模式: {endpoint}")

        cache_key = f"{endpoint}:{json.dumps(params or {}, sort_keys=True)}"

        # 检查缓存
        if cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                return data

        url = f"{self.base_url}/{endpoint}"
        for attempt in range(2):  # 减少重试次数
            try:
                resp = self.session.get(url, params=params, timeout=8)
                resp.raise_for_status()
                data = resp.json()
                self._cache[cache_key] = (time.time(), data)
                return data
            except requests.RequestException as e:
                if attempt == 1:
                    self._offline = True
                    raise RuntimeError(f"API请求失败 [{url}]: {e}")
                time.sleep(0.5)

    def search_stocks(self, keyword: str) -> List[dict]:
        """搜索股票"""
        data = self._get("v1/markets/search", {"search": keyword})
        return data.get("data", data.get("quotes", []))

    def get_quote(self, ticker: str) -> dict:
        """获取单只股票实时行情"""
        return self._get("v1/markets/quote", {"ticker": ticker, "type": "STOCKS"})

    def get_quotes_batch(self, tickers: List[str]) -> dict:
        """批量获取快照行情（支持逗号分隔）"""
        ticker_str = ",".join(tickers[:50])  # 单次最多50只
        return self._get("v1/markets/stock/quotes", {"ticker": ticker_str})

    def get_history(self, symbol: str, interval: str = "1d", limit: int = 100) -> dict:
        """获取历史K线数据（V2接口）"""
        return self._get("v2/markets/stock/history", {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        })

    def get_all_tickers(self, page: int = 1, asset_type: str = "STOCKS") -> dict:
        """获取全市场股票列表"""
        return self._get("v2/markets/tickers", {"page": page, "type": asset_type})


class TickFlowClient:
    """TickFlow API 客户端 - 用于高级因子数据（北向资金、龙虎榜等）"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or config.TICKFLOW_API_KEY
        self.base_url = config.TICKFLOW_BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._cache_ttl = 120

    def _get(self, endpoint: str, params: dict = None) -> dict:
        """GET请求，带缓存"""
        cache_key = f"{endpoint}:{json.dumps(params or {}, sort_keys=True)}"
        if cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                return data

        url = f"{self.base_url}/{endpoint}"
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, timeout=20)
                if resp.status_code == 401:
                    raise RuntimeError("TickFlow API密钥无效或已过期")
                resp.raise_for_status()
                data = resp.json()
                self._cache[cache_key] = (time.time(), data)
                return data
            except requests.RequestException as e:
                if attempt == 2:
                    # 降级处理：返回空数据而不是崩溃
                    print(f"[警告] TickFlow API请求失败 [{endpoint}]: {e}")
                    return {}
                time.sleep(1.5 * (attempt + 1))

    def get_northbound_flow(self, symbol: str, days: int = 20) -> dict:
        """获取北向资金流向数据"""
        return self._get("v1/northbound/flow", {
            "symbol": symbol,
            "days": days,
        })

    def get_northbound_top(self, date: str = None, top_n: int = 50) -> dict:
        """获取北向资金成交TOP榜"""
        return self._get("v1/northbound/top", {
            "date": date or datetime.date.today().isoformat(),
            "top_n": top_n,
        })

    def get_dragon_tiger(self, symbol: str, days: int = 10) -> dict:
        """获取龙虎榜数据"""
        return self._get("v1/dragon-tiger/stock", {
            "symbol": symbol,
            "days": days,
        })

    def get_dragon_tiger_list(self, date: str = None) -> dict:
        """获取当日龙虎榜列表"""
        return self._get("v1/dragon-tiger/list", {
            "date": date or datetime.date.today().isoformat(),
        })

    def get_margin_data(self, symbol: str, days: int = 20) -> dict:
        """获取融资融券数据"""
        return self._get("v1/margin/stock", {
            "symbol": symbol,
            "days": days,
        })

    def get_block_trades(self, symbol: str, days: int = 20) -> dict:
        """获取大宗交易数据"""
        return self._get("v1/block-trade/stock", {
            "symbol": symbol,
            "days": days,
        })

    def get_auction_data(self, symbol: str) -> dict:
        """获取集合竞价数据"""
        return self._get("v1/auction/stock", {
            "symbol": symbol,
        })

    def get_sector_data(self, sector_code: str) -> dict:
        """获取板块数据"""
        return self._get("v1/sector/info", {
            "code": sector_code,
        })

    def get_global_index(self, index_name: str = "SPX") -> dict:
        """获取外盘指数数据"""
        return self._get("v1/global/index", {
            "index": index_name,
        })


# ============================================================
# 数据获取协调器
# ============================================================

class DataFetcher:
    """数据获取协调层 - 统一管理多数据源"""

    def __init__(self):
        self.finance = FinanceClient()
        self.tickflow = TickFlowClient()

    def get_a_share_universe(self, sample_mode: bool = True) -> List[Dict[str, Any]]:
        """
        获取A股全市场股票列表

        Args:
            sample_mode: True时返回代表性样本（开发/回测用），False时尝试获取全量

        Returns:
            股票基本信息列表 [{symbol, name, exchange, sector, ...}, ...]
        """
        # A股代表性样本（涵盖各板块主要标的）
        a_share_samples = [
            # 上证主板
            {"symbol": "600519.SS", "name": "贵州茅台", "exchange": "SSE", "sector": "白酒"},
            {"symbol": "600036.SS", "name": "招商银行", "exchange": "SSE", "sector": "银行"},
            {"symbol": "600276.SS", "name": "恒瑞医药", "exchange": "SSE", "sector": "医药"},
            {"symbol": "600030.SS", "name": "中信证券", "exchange": "SSE", "sector": "证券"},
            {"symbol": "600887.SS", "name": "伊利股份", "exchange": "SSE", "sector": "食品饮料"},
            {"symbol": "601012.SS", "name": "隆基绿能", "exchange": "SSE", "sector": "光伏"},
            {"symbol": "601318.SS", "name": "中国平安", "exchange": "SSE", "sector": "保险"},
            {"symbol": "600900.SS", "name": "长江电力", "exchange": "SSE", "sector": "电力"},
            {"symbol": "601899.SS", "name": "紫金矿业", "exchange": "SSE", "sector": "有色"},
            {"symbol": "600809.SS", "name": "山西汾酒", "exchange": "SSE", "sector": "白酒"},
            {"symbol": "601857.SS", "name": "中国石油", "exchange": "SSE", "sector": "石油"},
            {"symbol": "600585.SS", "name": "海螺水泥", "exchange": "SSE", "sector": "建材"},
            {"symbol": "601398.SS", "name": "工商银行", "exchange": "SSE", "sector": "银行"},
            {"symbol": "600031.SS", "name": "三一重工", "exchange": "SSE", "sector": "机械"},
            {"symbol": "601088.SS", "name": "中国神华", "exchange": "SSE", "sector": "煤炭"},
            # 深证主板
            {"symbol": "000858.SZ", "name": "五粮液", "exchange": "SZSE", "sector": "白酒"},
            {"symbol": "000333.SZ", "name": "美的集团", "exchange": "SZSE", "sector": "家电"},
            {"symbol": "000001.SZ", "name": "平安银行", "exchange": "SZSE", "sector": "银行"},
            {"symbol": "000651.SZ", "name": "格力电器", "exchange": "SZSE", "sector": "家电"},
            {"symbol": "002594.SZ", "name": "比亚迪", "exchange": "SZSE", "sector": "汽车"},
            {"symbol": "002415.SZ", "name": "海康威视", "exchange": "SZSE", "sector": "安防"},
            {"symbol": "000568.SZ", "name": "泸州老窖", "exchange": "SZSE", "sector": "白酒"},
            {"symbol": "002475.SZ", "name": "立讯精密", "exchange": "SZSE", "sector": "电子"},
            {"symbol": "000725.SZ", "name": "京东方A", "exchange": "SZSE", "sector": "面板"},
            {"symbol": "002714.SZ", "name": "牧原股份", "exchange": "SZSE", "sector": "农牧"},
            # 创业板
            {"symbol": "300750.SZ", "name": "宁德时代", "exchange": "SZSE", "sector": "电池"},
            {"symbol": "300059.SZ", "name": "东方财富", "exchange": "SZSE", "sector": "证券"},
            {"symbol": "300274.SZ", "name": "阳光电源", "exchange": "SZSE", "sector": "光伏"},
            {"symbol": "300124.SZ", "name": "汇川技术", "exchange": "SZSE", "sector": "工控"},
            {"symbol": "300760.SZ", "name": "迈瑞医疗", "exchange": "SZSE", "sector": "医疗"},
            {"symbol": "300015.SZ", "name": "爱尔眼科", "exchange": "SZSE", "sector": "医疗"},
            {"symbol": "300014.SZ", "name": "亿纬锂能", "exchange": "SZSE", "sector": "电池"},
            {"symbol": "300498.SZ", "name": "温氏股份", "exchange": "SZSE", "sector": "农牧"},
            # 科创板
            {"symbol": "688981.SS", "name": "中芯国际", "exchange": "SSE", "sector": "半导体"},
            {"symbol": "688111.SS", "name": "金山办公", "exchange": "SSE", "sector": "软件"},
            {"symbol": "688036.SS", "name": "传音控股", "exchange": "SSE", "sector": "手机"},
            {"symbol": "688005.SS", "name": "容百科技", "exchange": "SSE", "sector": "电池材料"},
        ]

        if sample_mode:
            return a_share_samples

        # 尝试从API获取全量列表
        try:
            all_stocks = []
            for page in range(1, 6):  # 尝试获取前5页
                data = self.finance.get_all_tickers(page=page, asset_type="STOCKS")
                stocks = data.get("data", data.get("stocks", []))
                if not stocks:
                    break
                # 过滤A股（.SS 上海, .SZ 深圳）
                a_stocks = [s for s in stocks if ".SS" in s.get("symbol", "") or ".SZ" in s.get("symbol", "")]
                all_stocks.extend(a_stocks)
            if all_stocks:
                return all_stocks
        except Exception as e:
            print(f"[提示] 全量获取失败，使用样本模式: {e}")

        return a_share_samples

    def get_daily_bars(self, symbols: List[str], days: int = 60) -> Dict[str, List[dict]]:
        """
        批量获取日线数据

        Returns:
            {symbol: [{open, high, low, close, volume, turnover, ...}, ...]}
        """
        result = {}
        for symbol in symbols:
            try:
                data = self.finance.get_history(symbol=symbol, interval="1d", limit=days)
                candles = data.get("data", data.get("candles", data.get("quotes", [])))
                result[symbol] = candles
            except Exception as e:
                print(f"[警告] 获取{symbol}日线失败: {e}")
                result[symbol] = []
        return result

    def get_minute_bars(self, symbols: List[str], minutes: int = 390) -> Dict[str, List[dict]]:
        """
        获取分钟线数据（用于尾盘分析等）

        Returns:
            {symbol: [{open, high, low, close, volume, timestamp, ...}, ...]}
        """
        result = {}
        # 每天390分钟（09:30-11:30, 13:00-15:00），取最近2天
        for symbol in symbols:
            try:
                data = self.finance.get_history(symbol=symbol, interval="5m", limit=min(390, minutes))
                candles = data.get("data", data.get("candles", data.get("quotes", [])))
                result[symbol] = candles
            except Exception:
                result[symbol] = []
        return result

    def get_financial_data(self, symbol: str) -> dict:
        """获取财务数据"""
        try:
            return self.finance._get("v1/markets/stock/modules", {
                "ticker": symbol,
                "module": "financial-data",
            })
        except Exception:
            return {}

    def get_statistics(self, symbol: str) -> dict:
        """获取统计指标（PE、市值等）"""
        try:
            return self.finance._get("v1/markets/stock/modules", {
                "ticker": symbol,
                "module": "statistics",
            })
        except Exception:
            return {}
