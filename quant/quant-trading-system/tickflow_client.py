"""
TickFlow 原生 SDK 封装层
- 基于官方 tickflow SDK (v0.1.21)
- 统一 A 股数据获取接口
- 支持实时行情、历史K线、分时数据、板块分类

官方文档: https://docs.tickflow.org
API Key: 通过环境变量 TICKFLOW_API_KEY 或构造函数传入
"""

import datetime
import os
from typing import Any, Dict, List, Optional, Tuple, Union

import tickflow
import pandas as pd


class TickFlowClient:
    """TickFlow SDK 统一封装 - A股尾盘策略专用"""

    # A股符号格式: 代码.市场
    # SH = 上海, SZ = 深圳, BJ = 北交所
    CN_SUFFIX_MAP = {
        "SS": "SH",   # 旧格式 .SS → 新格式 .SH
        "SZ": "SZ",
        "BJ": "BJ",
    }

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("TICKFLOW_API_KEY", "")
        self._tf = tickflow.TickFlow(api_key=self.api_key)
        self._universe_cache: Optional[List[dict]] = None
        self._instrument_cache: Dict[str, dict] = {}

    # ================================================================
    # 实时行情
    # ================================================================

    def get_realtime_quotes(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """
        获取实时行情快照

        Args:
            symbols: ['600519.SH', '000001.SZ', ...]

        Returns:
            [{symbol, name, last_price, prev_close, open, high, low,
              volume, amount, change_pct, turnover_rate, amplitude, timestamp}, ...]
        """
        quotes = self._tf.quotes.get(symbols=symbols)
        return self._normalize_quotes(quotes)

    def get_quotes_by_universe(self, universe_id: str) -> List[Dict[str, Any]]:
        """
        按板块获取实时行情（如全A股、申万行业）

        Args:
            universe_id: 'CN_Equity' (全A股) 或 申万行业ID
        """
        quotes = self._tf.quotes.get(universes=[universe_id])
        return self._normalize_quotes(quotes)

    def _normalize_quotes(self, quotes: list) -> List[Dict[str, Any]]:
        """标准化行情数据格式"""
        result = []
        for q in quotes:
            if isinstance(q, dict):
                ext = q.get("ext", {})
                item = {
                    "symbol": q.get("symbol", ""),
                    "name": ext.get("name", "") if isinstance(ext, dict) else "",
                    "last_price": q.get("last_price", 0),
                    "prev_close": q.get("prev_close", 0),
                    "open": q.get("open", 0),
                    "high": q.get("high", 0),
                    "low": q.get("low", 0),
                    "volume": q.get("volume", 0),
                    "amount": q.get("amount", 0),
                    "change_pct": ext.get("change_pct", 0) if isinstance(ext, dict) else 0,
                    "turnover_rate": ext.get("turnover_rate", 0) if isinstance(ext, dict) else 0,
                    "amplitude": ext.get("amplitude", 0) if isinstance(ext, dict) else 0,
                    "timestamp": q.get("timestamp", 0),
                }
                result.append(item)
        return result

    # ================================================================
    # 历史K线
    # ================================================================

    def get_daily_klines(self, symbol: str, count: int = 60,
                         adjust: str = "forward") -> pd.DataFrame:
        """
        获取日线K线

        Args:
            symbol: '600519.SH'
            count: K线数量
            adjust: 复权方式 'forward'/'backward'/'none'

        Returns:
            DataFrame: [symbol, name, timestamp, open, high, low, close, volume, amount]
        """
        df = self._tf.klines.get(
            symbol=symbol, period="1d", count=count,
            adjust=adjust, as_dataframe=True,
        )
        return self._normalize_klines(df)

    def get_daily_klines_batch(self, symbols: List[str], count: int = 60,
                               adjust: str = "forward",
                               max_workers: int = 10) -> Dict[str, pd.DataFrame]:
        """
        批量获取日线K线

        Returns:
            {symbol: DataFrame}
        """
        result = self._tf.klines.batch(
            symbols=symbols, period="1d", count=count,
            adjust=adjust, as_dataframe=False,  # 返回dict便于处理
            max_workers=max_workers, batch_size=50,
        )
        normalized = {}
        for sym, data in result.items():
            if hasattr(data, "to_dataframe"):
                df = data.to_dataframe()
            else:
                df = data
            normalized[sym] = self._normalize_klines(df)
        return normalized

    def get_intraday_1m(self, symbol: str, count: int = 240) -> pd.DataFrame:
        """
        获取单只股票的1分钟分时K线（Pro套餐可用）

        Args:
            symbol: '600519.SH'
            count: 240根=全天
        """
        df = self._tf.klines.intraday(
            symbol=symbol, period="1m", count=count, as_dataframe=True,
        )
        return self._normalize_klines(df)

    def get_intraday_5m_batch(self, symbols: List[str], count: int = 48,
                               max_workers: int = 10) -> Dict[str, pd.DataFrame]:
        """
        【Pro套餐核心方法】批量获取5分钟K线

        利用 klines.batch(period='5m') 一次性获取多只股票的日内K线。
        48根5分钟K线 = 全天240分钟交易时段。

        这是尾盘分析的主力数据源！
        """
        result = self._tf.klines.batch(
            symbols=symbols, period="5m", count=count,
            as_dataframe=False, max_workers=max_workers, batch_size=100,
        )
        normalized = {}
        for sym, data in result.items():
            normalized[sym] = self._normalize_klines(data)
        return normalized

    def get_intraday_1m_parallel(self, symbols: List[str], count: int = 240,
                                  max_workers: int = 8) -> Dict[str, pd.DataFrame]:
        """
        【Pro套餐】并行获取多只股票的1分钟分时K线

        用于对通过初筛的候选池做精细尾盘分析。
        使用线程池并行请求单只intraday接口。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = {}

        def _fetch_one(sym):
            try:
                return sym, self.get_intraday_1m(sym, count=count)
            except Exception:
                return sym, pd.DataFrame()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_fetch_one, s): s for s in symbols}
            for future in as_completed(futures):
                sym, df = future.result()
                if df is not None and not df.empty:
                    results[sym] = df

        return results

    def get_intraday_batch(self, symbols: List[str], period: str = "1m",
                           count: int = 240, max_workers: int = 10) -> Dict[str, pd.DataFrame]:
        """
        批量获取分时数据（兼容旧接口）

        Pro套餐优先用5m批量 + 1m并行。
        如果intraday_batch不可用（非Expert套餐），降级为5m批量。
        """
        # 先试intraday_batch (Expert套餐)
        try:
            result = self._tf.klines.intraday_batch(
                symbols=symbols, period=period, count=count,
                as_dataframe=False, max_workers=max_workers, batch_size=30,
            )
            if result:
                normalized = {}
                for sym, data in result.items():
                    if hasattr(data, "to_dataframe"):
                        df = data.to_dataframe()
                    else:
                        df = data
                    normalized[sym] = self._normalize_klines(df)
                return normalized
        except Exception:
            pass

        # 降级：5m批量（Pro套餐可用）
        if period in ("1m", "5m"):
            return self.get_intraday_5m_batch(symbols, count=48)

        return {}

    def _normalize_klines(self, data) -> pd.DataFrame:
        """标准化K线数据（支持DataFrame或dict-of-lists）"""
        if data is None:
            return pd.DataFrame()

        # 如果是dict-of-lists格式，转为DataFrame
        if isinstance(data, dict) and not isinstance(data, pd.DataFrame):
            # TickFlow batch返回格式: {timestamp: [...], open: [...], ...}
            if "timestamp" in data and isinstance(data["timestamp"], list):
                df = pd.DataFrame(data)
            else:
                return pd.DataFrame()
        elif isinstance(data, pd.DataFrame):
            df = data.copy()
        else:
            return pd.DataFrame()

        if df.empty:
            return df

        # 统一时间列
        if "timestamp" in df.columns:
            try:
                df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                df["datetime"] = df["datetime"].dt.tz_convert("Asia/Shanghai")
            except Exception:
                # 如果时间戳格式不同，跳过转换
                pass

        if "turnover" in df.columns and "amount" not in df.columns:
            df["amount"] = df["turnover"]

        return df

    # ================================================================
    # 板块/全市场
    # ================================================================

    def list_universes(self, region: str = "CN", category: str = "equity") -> List[dict]:
        """
        列出所有可用板块

        Returns:
            [{id, name, description, symbol_count}, ...]
        """
        if self._universe_cache is None:
            ulist = self._tf.universes.list()
            self._universe_cache = [
                {"id": u["id"], "name": u["name"], "description": u.get("description", ""),
                 "region": u.get("region", ""), "category": u.get("category", ""),
                 "symbol_count": u.get("symbol_count", 0)}
                for u in ulist
            ]
        # 按地区/类别过滤
        return [
            u for u in self._universe_cache
            if (not region or u["region"] == region)
            and (not category or u["category"] == category)
        ]

    def get_universe_symbols(self, universe_id: str) -> List[str]:
        """
        获取板块内所有股票代码

        Args:
            universe_id: 'CN_Equity' (全A股) 或具体申万行业ID
        """
        detail = self._tf.universes.get(universe_id)
        symbols = detail.get("symbols", [])
        return symbols

    def get_instrument_info(self, symbols: List[str]) -> List[dict]:
        """获取标的基本信息（名称、行业、市值等）"""
        instruments = self._tf.instruments.batch(symbols)
        result = []
        for inst in instruments:
            if isinstance(inst, dict):
                result.append({
                    "symbol": inst.get("symbol", ""),
                    "name": inst.get("name", ""),
                    "exchange": inst.get("exchange", ""),
                    "industry": inst.get("industry", ""),
                    "list_date": inst.get("list_date", ""),
                })
            else:
                result.append({
                    "symbol": getattr(inst, "symbol", ""),
                    "name": getattr(inst, "name", ""),
                    "exchange": getattr(inst, "exchange", ""),
                    "industry": getattr(inst, "industry", ""),
                    "list_date": getattr(inst, "list_date", ""),
                })
        return result

    # ================================================================
    # 财务数据（辅助）
    # ================================================================

    def get_financial_metrics(self, symbols: List[str], latest: bool = True) -> Dict[str, dict]:
        """获取关键财务指标（PE、PB、ROE等）"""
        try:
            metrics = self._tf.financials.metrics(
                symbols=symbols, latest=latest, as_dataframe=False,
            )
            return metrics
        except Exception:
            return {}

    # ================================================================
    # 盘口深度
    # ================================================================

    def get_market_depth(self, symbol: str) -> dict:
        """获取买卖五档盘口"""
        try:
            depth = self._tf.depth.get(symbol)
            if isinstance(depth, dict):
                return depth
            return {"bids": [], "asks": []}
        except Exception:
            return {"bids": [], "asks": []}

    # ================================================================
    # 工具方法
    # ================================================================

    @staticmethod
    def to_tickflow_symbol(symbol: str) -> str:
        """
        转换旧格式 (.SS/.SZ) 到 TickFlow 格式 (.SH/.SZ)

        '600519.SS' → '600519.SH'
        '000001.SZ' → '000001.SZ'
        """
        if symbol.endswith(".SS"):
            return symbol.replace(".SS", ".SH")
        return symbol

    @staticmethod
    def from_tickflow_symbol(symbol: str) -> str:
        """TickFlow格式转回旧格式"""
        if symbol.endswith(".SH"):
            return symbol.replace(".SH", ".SS")
        return symbol

    def resolve_universe_name(self, name: str) -> Optional[str]:
        """模糊搜索板块ID（如搜索'白酒'→'CN_Equity_SW3_...'）"""
        universes = self.list_universes()
        for u in universes:
            if name in u["name"] or name in u.get("description", ""):
                return u["id"]
        return None


# ================================================================
# 全局单例
# ================================================================

_client: Optional[TickFlowClient] = None


def get_client(api_key: str = None) -> TickFlowClient:
    global _client
    if _client is None:
        _client = TickFlowClient(api_key=api_key)
    return _client
