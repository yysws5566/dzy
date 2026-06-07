"""
流动性筛选模块
- 剔除流动性不足的标的
- 确保候选池具备T+1操作可行性
"""

from typing import Any, Dict, List, Tuple

import config
from config import LiquidityFilter


class LiquidityScreener:
    """流动性初筛器"""

    def __init__(self, filter_config: LiquidityFilter = None):
        self.cfg = filter_config or config.DEFAULT_LIQUIDITY

    def screen(self, stocks: List[Dict[str, Any]], market_data: Dict[str, List[dict]]) -> Tuple[List[Dict], List[Dict]]:
        """
        筛选流动性合格的候选池

        Args:
            stocks: 股票基本信息列表
            market_data: {symbol: [日线数据列表]}

        Returns:
            (通过筛选的股票列表, 被剔除的股票列表及原因)
        """
        passed = []
        rejected = []

        for stock in stocks:
            symbol = stock.get("symbol", "")
            name = stock.get("name", "未知")

            bars = market_data.get(symbol, [])
            if not bars or len(bars) < 20:
                rejected.append({**stock, "reject_reason": "数据不足（需要至少20个交易日）"})
                continue

            # 逐项检查
            reject_reason = self._check_liquidity(stock, bars)
            if reject_reason:
                rejected.append({**stock, "reject_reason": reject_reason})
            else:
                passed.append(stock)

        return passed, rejected

    def _check_liquidity(self, stock: dict, bars: List[dict]) -> str:
        """
        执行流动性检查，返回拒绝原因（空字符串表示通过）

        bars 格式: [{open, high, low, close, volume, ...}, ...]
        """
        latest = bars[-1] if bars else {}
        volume = latest.get("volume", 0)
        close = latest.get("close", 0)

        # 价格检查
        if close > self.cfg.max_price:
            return f"股价{close:.2f}超过上限{self.cfg.max_price}"
        if close < self.cfg.min_price:
            return f"股价{close:.2f}低于下限{self.cfg.min_price}"

        # 成交量检查
        if volume and volume < self.cfg.min_daily_volume:
            return f"日成交量{volume:,} < {self.cfg.min_daily_volume:,}（流动性不足）"

        # 成交额检查
        turnover = latest.get("turnover", latest.get("amount", 0))
        if turnover and turnover < self.cfg.min_daily_turnover:
            return f"日成交额{turnover:,.0f} < {self.cfg.min_daily_turnover:,.0f}"

        # 20日均量检查
        if len(bars) >= 20:
            avg_vol_20 = sum(b.get("volume", 0) for b in bars[-20:]) / 20
            if avg_vol_20 < self.cfg.min_avg_volume_20d:
                return f"20日均量{avg_vol_20:,.0f} < {self.cfg.min_avg_volume_20d:,}"

        # 换手率检查
        turnover_rate = latest.get("turnover_rate", latest.get("turnoverRate", None))
        if turnover_rate is not None and turnover_rate < self.cfg.min_turnover_rate:
            return f"换手率{turnover_rate:.2f}% < {self.cfg.min_turnover_rate}%（僵尸股）"

        # ST检查
        name = stock.get("name", "")
        if self.cfg.exclude_st and ("ST" in name or "*ST" in name):
            return "ST股，自动排除"

        return ""  # 通过


class MarketSnapshot:
    """
    市场快照 - 存储候选池中每只股票的完整数据
    供因子计算使用
    """

    def __init__(self, symbol: str, name: str, sector: str = ""):
        self.symbol = symbol
        self.name = name
        self.sector = sector

        # 日线数据
        self.daily_bars: List[dict] = []
        # 分钟线数据
        self.minute_bars: List[dict] = []
        # 财务数据
        self.financials: dict = {}
        # 统计指标
        self.statistics: dict = {}

    @property
    def latest_close(self) -> float:
        if self.daily_bars:
            return self.daily_bars[-1].get("close", 0)
        return 0

    @property
    def latest_volume(self) -> int:
        if self.daily_bars:
            return self.daily_bars[-1].get("volume", 0)
        return 0

    @property
    def latest_open(self) -> float:
        if self.daily_bars:
            return self.daily_bars[-1].get("open", 0)
        return 0

    @property
    def latest_high(self) -> float:
        if self.daily_bars:
            return self.daily_bars[-1].get("high", 0)
        return 0

    @property
    def latest_low(self) -> float:
        if self.daily_bars:
            return self.daily_bars[-1].get("low", 0)
        return 0

    def get_ma(self, period: int) -> float:
        """计算移动均线"""
        if len(self.daily_bars) < period:
            return 0
        closes = [b.get("close", 0) for b in self.daily_bars[-period:]]
        return sum(closes) / period

    def get_vwap(self, days: int = 1) -> float:
        """计算成交量加权平均价"""
        bars = self.daily_bars[-days:]
        total_value = 0
        total_vol = 0
        for b in bars:
            typical = (b.get("high", 0) + b.get("low", 0) + b.get("close", 0)) / 3
            vol = b.get("volume", 1)
            total_value += typical * vol
            total_vol += vol
        return total_value / max(total_vol, 1)

    def get_volume_ma(self, period: int) -> float:
        """计算均量"""
        if len(self.daily_bars) < period:
            return 0
        vols = [b.get("volume", 0) for b in self.daily_bars[-period:]]
        return sum(vols) / period
