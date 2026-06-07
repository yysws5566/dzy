"""
隔夜持仓策略优化器
==================
基于真实TickFlow Pro数据，优化T+1尾盘买入→次日卖出的隔夜策略。

优化维度：
1. 因子权重 — 9个尾盘因子的最优权重分配
2. 买入阈值 — 平衡胜率与交易频率
3. 卖出策略 — 次日开盘/尾盘/动态止盈
4. 仓位管理 — 固定仓位 vs 凯利公式 vs 动态调整
"""

import encoding_fix  # noqa
import datetime
import json
import math
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from tickflow_client import TickFlowClient, get_client
from tail_scanner import TailScanner


# ================================================================
# 交易记录
# ================================================================

@dataclass
class OvernightTrade:
    """单笔隔夜交易"""
    buy_date: str
    symbol: str
    name: str
    buy_price: float          # 尾盘买入价（≈收盘价）
    sell_price_open: float     # 次日开盘卖出价
    sell_price_close: float    # 次日收盘卖出价
    sell_price_best: float     # 次日最优卖出价（最高点）
    return_open: float         # 开盘卖收益率(%)
    return_close: float        # 收盘卖收益率(%)
    return_best: float         # 最优卖收益率(%)
    total_score: float         # 买入时综合得分
    signal: str                # STRONG_BUY / BUY
    factor_scores: Dict[str, float] = field(default_factory=dict)


# ================================================================
# 隔夜回测引擎
# ================================================================

class OvernightBacktest:
    """
    隔夜持仓回测引擎（真实TickFlow数据版）

    模拟流程：
    1. 对每个历史交易日，构建14:40快照
    2. 计算9个尾盘因子 → 打分 → 生成信号
    3. T+1次日开盘/收盘卖出
    4. 统计胜率/盈亏比/夏普
    """

    def __init__(self, client: TickFlowClient, symbols: List[str],
                 lookback_days: int = 90):
        self.client = client
        self.symbols = symbols
        self.lookback_days = lookback_days

        # 缓存
        self._daily_cache: Dict[str, pd.DataFrame] = {}
        self._daily_cache_date: Optional[str] = None

    def run(self, weights: Dict[str, float],
            buy_threshold: float = 0.58,
            exit_strategy: str = "open") -> Dict[str, Any]:
        """
        运行隔夜回测

        Args:
            weights: 9个因子权重
            buy_threshold: 买入阈值
            exit_strategy: 'open' 次日开盘 / 'close' 次日收盘 / 'best' 最优

        Returns:
            {
                trades: [OvernightTrade, ...],
                win_rate, avg_return, profit_factor, sharpe,
                total_trades, total_return, max_drawdown,
                exit_strategy,
            }
        """
        scanner = TailScanner(
            client=self.client, weights=weights,
            buy_threshold=buy_threshold,
        )

        # 获取日线数据
        print(f"     [数据] 获取{len(self.symbols)}只×{self.lookback_days}天日K...")
        daily_map = self.client.get_daily_klines_batch(
            self.symbols, count=self.lookback_days * 2,
            adjust="forward", max_workers=8,
        )

        if not daily_map:
            return self._empty_result(exit_strategy)

        # 找出所有交易日
        all_dates = set()
        for df in daily_map.values():
            if df is None or df.empty:
                continue
            if "datetime" in df.columns:
                all_dates.update(d.date() for d in df["datetime"])
            elif "timestamp" in df.columns:
                all_dates.update(
                    pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.date
                )

        trading_dates = sorted(all_dates)
        if len(trading_dates) < 22:
            return self._empty_result(exit_strategy)

        # 用后N天做回测（前20天用于计算均线等）
        test_dates = trading_dates[20:]
        print(f"     [回测] {len(test_dates)} 个交易日...")

        trades = []
        daily_returns = []

        for i, date in enumerate(test_dates[:-1]):  # 最后一天无法卖出
            day_signals = []

            for symbol in self.symbols:
                df = daily_map.get(symbol)
                if df is None or df.empty:
                    continue

                # 取该日期之前的数据
                if "datetime" in df.columns:
                    mask = df["datetime"].dt.date <= date
                else:
                    target_ts = pd.Timestamp(date, tz="Asia/Shanghai").value // 10**6
                    mask = df["timestamp"] <= target_ts

                hist = df[mask]
                if len(hist) < 20:
                    continue

                today_row = hist.iloc[-1]
                prev_row = hist.iloc[-2] if len(hist) >= 2 else today_row

                # ---- 构建14:40快照（改进版）----
                open_p = float(today_row.get("open", 0))
                high_p = float(today_row.get("high", 0))
                low_p = float(today_row.get("low", 0))
                close_p = float(today_row.get("close", 0))
                volume = float(today_row.get("volume", 0))
                amount = float(today_row.get("amount", 0))
                prev_close = float(prev_row.get("close", open_p))

                if open_p <= 0:
                    continue

                # 14:40价格估算：
                # 如果收盘价>开盘价，14:40时约完成涨幅的80%-95%
                # 如果收盘价<开盘价，14:40时约完成跌幅的80%-95%
                day_range = close_p - open_p
                est_1440_price = open_p + day_range * random.uniform(0.75, 0.95)

                # 14:40成交量估算：全天成交量的80%-90%
                vol_completion = random.uniform(0.80, 0.90)
                est_1440_vol = volume * vol_completion
                est_1440_amount = amount * vol_completion

                # 14:40时的高低价估算
                if close_p > open_p:
                    # 阳线：14:40前一般还没有最终高点
                    est_high = max(open_p, est_1440_price) * (1 + random.uniform(0, 0.005))
                    est_low = min(open_p, low_p * random.uniform(0.98, 1.0))
                else:
                    est_high = max(open_p, high_p * random.uniform(0.98, 1.0))
                    est_low = min(open_p, est_1440_price) * (1 - random.uniform(0, 0.005))

                # 模拟分时走势（改进版，基于日内OHLC的关系）
                fake_intra = self._make_intraday_smart(
                    open_p, high_p, low_p, close_p, est_1440_price, volume,
                )

                quote = {
                    "symbol": symbol,
                    "name": today_row.get("name", ""),
                    "last_price": est_1440_price,
                    "prev_close": prev_close,
                    "open": open_p,
                    "high": est_high,
                    "low": est_low,
                    "volume": est_1440_vol,
                    "amount": est_1440_amount,
                    "change_pct": (est_1440_price - prev_close) / max(prev_close, 0.01),
                    "turnover_rate": 0.02,
                    "amplitude": (est_high - est_low) / max(prev_close, 0.01),
                }

                # 计算因子得分
                signal = scanner._calculate_signal(
                    symbol, quote, hist.iloc[:-1], fake_intra, {},
                )

                if signal.total_score >= buy_threshold:
                    day_signals.append({
                        "symbol": symbol,
                        "name": quote["name"],
                        "signal": "STRONG_BUY" if signal.total_score >= scanner.strong_buy_threshold else "BUY",
                        "total_score": signal.total_score,
                        "buy_price": est_1440_price,
                        "factor_scores": {
                            "tail_volume": signal.tail_volume_score,
                            "intraday_trend": signal.intraday_trend_score,
                            "volume_accum": signal.volume_accum_score,
                            "auction": signal.auction_score,
                            "gap": signal.gap_score,
                            "reversal": signal.reversal_score,
                            "ma_position": signal.ma_position_score,
                            "integer_psych": signal.integer_psych_score,
                            "sector_rel": signal.sector_rel_score,
                        },
                    })

            # ---- T+1 卖出模拟 ----
            next_date = test_dates[i + 1] if i + 1 < len(test_dates) else None
            if next_date is None:
                continue

            for sig in day_signals[:8]:  # 每天最多8笔
                df = daily_map.get(sig["symbol"])
                if df is None or df.empty:
                    continue

                # 取次日数据
                if "datetime" in df.columns:
                    next_mask = df["datetime"].dt.date == next_date
                else:
                    next_ts = pd.Timestamp(next_date, tz="Asia/Shanghai").value // 10**6
                    next_mask = df["timestamp"] >= next_ts - 86400000

                next_data = df[next_mask]
                if next_data.empty:
                    continue

                next_open = float(next_data.iloc[0].get("open", 0))
                next_close = float(next_data.iloc[-1].get("close", 0))
                next_high = float(next_data["high"].max()) if "high" in next_data.columns else next_open
                next_low = float(next_data["low"].min()) if "low" in next_data.columns else next_open

                if next_open <= 0:
                    continue

                buy_p = sig["buy_price"]

                # 三种卖出策略的收益
                ret_open = (next_open - buy_p) / buy_p - 0.0015   # 开盘卖（扣成本）
                ret_close = (next_close - buy_p) / buy_p - 0.0015  # 收盘卖
                ret_best = (next_high - buy_p) / buy_p - 0.0015    # 最优卖

                trades.append(OvernightTrade(
                    buy_date=date.isoformat(),
                    symbol=sig["symbol"],
                    name=sig["name"],
                    buy_price=buy_p,
                    sell_price_open=next_open,
                    sell_price_close=next_close,
                    sell_price_best=next_high,
                    return_open=ret_open * 100,
                    return_close=ret_close * 100,
                    return_best=ret_best * 100,
                    total_score=sig["total_score"],
                    signal=sig["signal"],
                    factor_scores=sig.get("factor_scores", {}),
                ))

            # 进度
            if (i + 1) % 15 == 0:
                print(f"       进度: {i+1}/{len(test_dates)-1} ({len(trades)} 笔交易)")

        # ---- 统计 ----
        return self._compute_stats(trades, exit_strategy)

    def _make_intraday_smart(self, open_p: float, high_p: float, low_p: float,
                             close_p: float, est_1440: float,
                             volume: float) -> pd.DataFrame:
        """
        改进的日内分时模拟

        基于实际观察到的A股日内形态：
        - 上午通常有冲高/下探
        - 11:00-13:00 午休前后常缩量
        - 14:00后波动加大
        - 14:40后尾盘加速
        """
        n = 48  # 48根5分钟K线
        prices = []
        vols = []

        direction = 1 if close_p > open_p else -1

        for i in range(n):
            t = i / n  # 0→1 日内进度

            # ---- 价格模拟 ----
            if direction > 0:
                # 阳线：开盘→探底→回升→尾盘冲高
                if t < 0.15:
                    p = open_p + (low_p - open_p) * (t / 0.15) * random.uniform(0.3, 0.8)
                elif t < 0.35:
                    p = low_p + (est_1440 - low_p) * ((t - 0.15) / 0.2) * random.uniform(0.5, 1.0)
                elif t < 0.65:
                    p = est_1440 + random.gauss(0, 0.002) * open_p
                else:
                    p = est_1440 + (close_p - est_1440) * ((t - 0.65) / 0.35)
            else:
                # 阴线：开盘→冲高→回落→尾盘下杀
                if t < 0.15:
                    p = open_p + (high_p - open_p) * (t / 0.15) * random.uniform(0.3, 0.8)
                elif t < 0.35:
                    p = high_p + (est_1440 - high_p) * ((t - 0.15) / 0.2) * random.uniform(0.5, 1.0)
                elif t < 0.65:
                    p = est_1440 + random.gauss(0, 0.002) * open_p
                else:
                    p = est_1440 + (close_p - est_1440) * ((t - 0.65) / 0.35)

            p += random.gauss(0, 0.0015) * open_p
            p = max(low_p * 0.98, min(high_p * 1.02, p))
            prices.append(p)

            # ---- 量能模拟 ----
            if t < 0.1:
                vm = random.uniform(1.0, 2.0)    # 开盘放量
            elif 0.45 < t < 0.55:
                vm = random.uniform(0.3, 0.6)    # 午间缩量
            elif t > 0.8:
                vm = random.uniform(1.3, 2.5)    # 尾盘放量
            else:
                vm = random.uniform(0.7, 1.2)
            vols.append(volume / n * vm)

        return pd.DataFrame({
            "close": prices,
            "open": [p * random.uniform(0.998, 1.002) for p in prices],
            "high": [p * random.uniform(1.0, 1.005) for p in prices],
            "low": [p * random.uniform(0.995, 1.0) for p in prices],
            "volume": vols,
        })

    @staticmethod
    def _make_intraday_smart_static(open_p: float, high_p: float, low_p: float,
                                     close_p: float, volume: float) -> pd.DataFrame:
        """
        确定性分时模拟（用于预计算数据库）

        与 _make_intraday_smart 逻辑相同，但依赖外部 random.seed()。
        """
        n = 48
        prices = []
        vols = []
        direction = 1 if close_p > open_p else -1
        est_1440 = open_p + (close_p - open_p) * 0.85  # 固定14:40价格位置

        for i in range(n):
            t = i / n
            if direction > 0:
                if t < 0.15:
                    p = open_p + (low_p - open_p) * (t / 0.15) * 0.5
                elif t < 0.35:
                    p = low_p + (est_1440 - low_p) * ((t - 0.15) / 0.2) * 0.7
                elif t < 0.65:
                    p = est_1440 + random.gauss(0, 0.002) * open_p
                else:
                    p = est_1440 + (close_p - est_1440) * ((t - 0.65) / 0.35)
            else:
                if t < 0.15:
                    p = open_p + (high_p - open_p) * (t / 0.15) * 0.5
                elif t < 0.35:
                    p = high_p + (est_1440 - high_p) * ((t - 0.15) / 0.2) * 0.7
                elif t < 0.65:
                    p = est_1440 + random.gauss(0, 0.002) * open_p
                else:
                    p = est_1440 + (close_p - est_1440) * ((t - 0.65) / 0.35)

            p += random.gauss(0, 0.0015) * open_p
            p = max(low_p * 0.98, min(high_p * 1.02, p))
            prices.append(p)

            if t < 0.1:
                vm = 1.5
            elif 0.45 < t < 0.55:
                vm = 0.45
            elif t > 0.8:
                vm = 1.8
            else:
                vm = 1.0
            vols.append(volume / n * vm * random.uniform(0.8, 1.2))

        return pd.DataFrame({
            "close": prices,
            "open": [p * random.uniform(0.998, 1.002) for p in prices],
            "high": [p * random.uniform(1.0, 1.005) for p in prices],
            "low": [p * random.uniform(0.995, 1.0) for p in prices],
            "volume": vols,
        })

    def _compute_stats(self, trades: List[OvernightTrade],
                       exit_strategy: str) -> Dict[str, Any]:
        """计算统计指标"""
        if not trades:
            return self._empty_result(exit_strategy)

        # 根据策略选收益列
        ret_key = {
            "open": "return_open",
            "close": "return_close",
            "best": "return_best",
        }[exit_strategy]

        returns = np.array([getattr(t, ret_key) for t in trades])
        wins = returns > 0

        win_rate = float(np.mean(wins))
        avg_ret = float(np.mean(returns))
        total_ret = float(np.sum(returns))

        win_returns = returns[wins]
        lose_returns = returns[~wins]
        profit_factor = float(np.sum(np.abs(win_returns)) / max(np.sum(np.abs(lose_returns)), 0.01))

        # 夏普
        sharpe = 0.0
        if len(returns) > 1 and np.std(returns) > 0:
            sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(252))

        # 最大回撤
        cumsum = np.cumsum(returns)
        peak = np.maximum.accumulate(cumsum)
        max_dd = float(np.max(peak - cumsum))

        # 分层统计
        strong = [t for t in trades if t.signal == "STRONG_BUY"]
        normal = [t for t in trades if t.signal == "BUY"]

        def layer_stats(ts):
            if not ts:
                return {"count": 0, "win_rate": 0, "avg_ret": 0}
            rs = [getattr(t, ret_key) for t in ts]
            return {
                "count": len(ts),
                "win_rate": round(np.mean([r > 0 for r in rs]) * 100, 1),
                "avg_ret": round(np.mean(rs), 2),
            }

        # 因子IC
        factor_ic = {}
        for fn in ["tail_volume", "intraday_trend", "volume_accum", "auction",
                    "gap", "reversal", "ma_position", "integer_psych", "sector_rel"]:
            scores = []
            rets = []
            for t in trades:
                s = t.factor_scores.get(fn, 0.5)
                scores.append(s)
                rets.append(getattr(t, ret_key))
            if len(scores) > 5:
                ic = np.corrcoef(scores, rets)[0, 1]
                if not np.isnan(ic):
                    factor_ic[fn] = round(float(ic), 3)

        return {
            "trades": trades,
            "exit_strategy": exit_strategy,
            "total_trades": len(trades),
            "win_rate": round(win_rate * 100, 1),
            "avg_return": round(avg_ret, 2),
            "total_return": round(total_ret, 2),
            "profit_factor": round(profit_factor, 2),
            "sharpe": round(sharpe, 2),
            "max_drawdown": round(max_dd, 2),
            "strong_buy": layer_stats(strong),
            "normal_buy": layer_stats(normal),
            "factor_ic": factor_ic,
        }

    def _empty_result(self, exit_strategy: str) -> Dict[str, Any]:
        return {
            "trades": [], "exit_strategy": exit_strategy,
            "total_trades": 0, "win_rate": 0, "avg_return": 0,
            "total_return": 0, "profit_factor": 0, "sharpe": 0,
            "max_drawdown": 0,
            "strong_buy": {"count": 0, "win_rate": 0, "avg_ret": 0},
            "normal_buy": {"count": 0, "win_rate": 0, "avg_ret": 0},
            "factor_ic": {},
        }


# ================================================================
# 隔夜策略优化器
# ================================================================

class OvernightOptimizer:
    """隔夜策略全维度优化器"""

    WEIGHT_BOUNDS = {
        "tail_volume": (0.08, 0.30),
        "intraday_trend": (0.10, 0.30),
        "volume_accum": (0.03, 0.18),
        "auction": (0.05, 0.25),
        "gap": (0.03, 0.15),
        "reversal": (0.03, 0.20),
        "ma_position": (0.02, 0.10),
        "integer_psych": (0.02, 0.12),
        "sector_rel": (0.03, 0.18),
    }

    def __init__(self, client: TickFlowClient = None,
                 symbols: List[str] = None):
        self.client = client or get_client()
        self.symbols = symbols or self._default_symbols()
        self.best_result: Optional[Dict] = None

    def _default_symbols(self) -> List[str]:
        return [
            "600519.SH", "600036.SH", "600030.SH", "600887.SH",
            "601012.SH", "601318.SH", "600900.SH", "601899.SH",
            "600809.SH", "601398.SH", "600031.SH", "601088.SH",
            "000858.SZ", "000333.SZ", "000001.SZ", "000651.SZ",
            "002594.SZ", "002415.SZ", "000568.SZ", "002475.SZ",
            "000725.SZ", "002714.SZ", "300750.SZ", "300059.SZ",
            "300274.SZ", "300124.SZ", "300760.SZ", "300015.SZ",
            "300014.SZ", "688981.SH", "688111.SH", "688036.SH",
        ]

    def optimize_all(self, trials: int = 150, lookback_days: int = 90) -> Dict[str, Any]:
        """
        全维度优化（数据只拉一次，大幅提速）

        1. 先获取全部日线数据
        2. 预计算每日快照元数据（OHLCV）
        3. 每组权重/阈值直接用预计算数据回测
        """
        print("=" * 60)
        print("  隔夜持仓策略全维度优化")
        print(f"  标的: {len(self.symbols)}只 | 回测: {lookback_days}天 | 试验: {trials}组")
        print("=" * 60)

        # 获取数据（只一次）
        print(f"  [数据] 获取{len(self.symbols)}只×{lookback_days}天日K...")
        daily_map = self.client.get_daily_klines_batch(
            self.symbols, count=lookback_days * 2,
            adjust="forward", max_workers=10,
        )

        if not daily_map:
            print("  ❌ 数据获取失败")
            return {}

        # 建立每日快照数据库
        print(f"  [预处理] 构建每日快照...")
        snapshots_db = self._build_snapshot_db(daily_map)
        print(f"     {len(snapshots_db)} 个交易日, {sum(len(v) for v in snapshots_db.values())} 条快照")

        all_results = []

        for trial in range(trials):
            # 随机采样权重
            raw = {fn: random.uniform(lo, hi) for fn, (lo, hi) in self.WEIGHT_BOUNDS.items()}
            total = sum(raw.values())
            weights = {k: v / total for k, v in raw.items()}
            bt = random.uniform(0.52, 0.66)

            # 每种卖出策略
            for exit_s in ["open", "close"]:
                result = self._fast_backtest(
                    snapshots_db, daily_map, weights, bt, exit_s,
                )
                result["weights"] = weights
                result["buy_threshold"] = bt
                result["trial"] = trial
                all_results.append(result)

            if (trial + 1) % 15 == 0:
                valid = [r for r in all_results if r["total_trades"] > 0]
                if valid:
                    best = max(valid, key=lambda r: r["win_rate"] * 0.6 + r["profit_factor"] * 0.4)
                    print(f"  [{trial+1}/{trials}] 最佳: 胜率={best['win_rate']:.1f}% "
                          f"盈亏={best['profit_factor']:.2f} "
                          f"策略={best['exit_strategy']} 交易={best['total_trades']}笔")

        # 找出各组最优
        self._summarize(all_results)
        return self.best_result

    def _build_snapshot_db(self, daily_map: Dict[str, pd.DataFrame]) -> Dict[str, List[Dict]]:
        """
        预计算每日快照数据库

        对每个交易日、每只股票，预计算14:40快照和因子所需的基础数据。
        这样每组权重/阈值可以极快地回测（无需重复拉API）。

        Returns:
            {date_str: [{symbol, name, quote_dict, hist_df, fake_intra_df}, ...]}
        """
        all_dates = set()
        for df in daily_map.values():
            if df is not None and not df.empty:
                if "datetime" in df.columns:
                    all_dates.update(d.date() for d in df["datetime"])

        dates = sorted(all_dates)
        if len(dates) < 22:
            return {}

        test_dates = dates[20:-1]  # 前20天用于均线，最后一天无法卖出
        db = {}

        for date in test_dates:
            date_str = date.isoformat()
            day_snapshots = []

            # 预计算次日数据（用于卖出模拟）
            next_date = dates[dates.index(date) + 1] if date in dates else None

            for symbol in self.symbols:
                df = daily_map.get(symbol)
                if df is None or df.empty:
                    continue

                # 取该日期之前的数据
                if "datetime" in df.columns:
                    mask = df["datetime"].dt.date <= date
                else:
                    target_ts = pd.Timestamp(date, tz="Asia/Shanghai").value // 10**6
                    mask = df["timestamp"] <= target_ts

                hist = df[mask]
                if len(hist) < 20:
                    continue

                today_row = hist.iloc[-1]
                prev_row = hist.iloc[-2] if len(hist) >= 2 else today_row

                open_p = float(today_row.get("open", 0))
                high_p = float(today_row.get("high", 0))
                low_p = float(today_row.get("low", 0))
                close_p = float(today_row.get("close", 0))
                volume = float(today_row.get("volume", 0))
                amount = float(today_row.get("amount", 0))
                prev_close = float(prev_row.get("close", open_p))

                if open_p <= 0:
                    continue

                # 预计算次日数据（卖出用）
                next_open = close_p  # fallback
                next_close = close_p
                next_high = close_p
                if next_date:
                    if "datetime" in df.columns:
                        next_mask = df["datetime"].dt.date == next_date
                    else:
                        next_ts = pd.Timestamp(next_date, tz="Asia/Shanghai").value // 10**6
                        next_mask = df["timestamp"] >= next_ts - 86400000
                    next_data = df[next_mask]
                    if not next_data.empty:
                        next_open = float(next_data.iloc[0].get("open", close_p))
                        next_close = float(next_data.iloc[-1].get("close", close_p))
                        next_high = float(next_data["high"].max()) if "high" in next_data.columns else next_open

                # 生成固定的分时模拟（同一个交易日对同一只股票使用相同的模拟）
                random.seed(hash(f"{date_str}_{symbol}") % (2**31))
                fake_intra = OvernightBacktest._make_intraday_smart_static(
                    open_p, high_p, low_p, close_p, volume,
                )
                random.seed()

                day_snapshots.append({
                    "symbol": symbol,
                    "name": today_row.get("name", ""),
                    "quote": {
                        "symbol": symbol,
                        "name": today_row.get("name", ""),
                        "last_price": close_p * random.uniform(0.98, 1.00),  # 14:40估算
                        "prev_close": prev_close,
                        "open": open_p,
                        "high": high_p,
                        "low": low_p,
                        "volume": volume * 0.85,
                        "amount": amount * 0.85,
                        "change_pct": (close_p - prev_close) / max(prev_close, 0.01) * random.uniform(0.75, 0.95),
                        "turnover_rate": 0.02,
                        "amplitude": (high_p - low_p) / max(prev_close, 0.01),
                    },
                    "hist": hist.iloc[:-1],
                    "fake_intra": fake_intra,
                    "next_open": next_open,
                    "next_close": next_close,
                    "next_high": next_high,
                })

            if day_snapshots:
                db[date_str] = day_snapshots

        return db

    def _fast_backtest(self, snapshots_db: Dict, daily_map: Dict,
                       weights: Dict, buy_threshold: float,
                       exit_strategy: str) -> Dict[str, Any]:
        """
        快速回测（使用预计算快照数据库）

        对于每组权重/阈值，只需重新计算因子得分和信号，无需重拉数据。
        """
        scanner = TailScanner(
            client=self.client, weights=weights,
            buy_threshold=buy_threshold,
            strong_buy_threshold=buy_threshold + 0.15,
        )

        all_trades = []

        for date_str, snapshots in snapshots_db.items():
            day_signals = []

            for snap in snapshots:
                signal = scanner._calculate_signal(
                    snap["symbol"], snap["quote"],
                    snap["hist"], snap["fake_intra"], {},
                )

                if signal.total_score >= buy_threshold:
                    day_signals.append({
                        "symbol": snap["symbol"],
                        "name": snap["name"],
                        "signal": "STRONG_BUY" if signal.total_score >= scanner.strong_buy_threshold else "BUY",
                        "total_score": signal.total_score,
                        "buy_price": snap["quote"]["last_price"],
                        "next_open": snap["next_open"],
                        "next_close": snap["next_close"],
                        "next_high": snap["next_high"],
                        "factor_scores": {
                            "tail_volume": signal.tail_volume_score,
                            "intraday_trend": signal.intraday_trend_score,
                            "volume_accum": signal.volume_accum_score,
                            "auction": signal.auction_score,
                            "gap": signal.gap_score,
                            "reversal": signal.reversal_score,
                            "ma_position": signal.ma_position_score,
                            "integer_psych": signal.integer_psych_score,
                            "sector_rel": signal.sector_rel_score,
                        },
                    })

            # T+1卖出
            for sig in day_signals[:8]:
                buy_p = sig["buy_price"]
                ret_open = (sig["next_open"] - buy_p) / buy_p - 0.0015
                ret_close = (sig["next_close"] - buy_p) / buy_p - 0.0015
                ret_best = (sig["next_high"] - buy_p) / buy_p - 0.0015

                all_trades.append(OvernightTrade(
                    buy_date=date_str,
                    symbol=sig["symbol"],
                    name=sig["name"],
                    buy_price=buy_p,
                    sell_price_open=sig["next_open"],
                    sell_price_close=sig["next_close"],
                    sell_price_best=sig["next_high"],
                    return_open=ret_open * 100,
                    return_close=ret_close * 100,
                    return_best=ret_best * 100,
                    total_score=sig["total_score"],
                    signal=sig["signal"],
                    factor_scores=sig.get("factor_scores", {}),
                ))

        # 统计
        ret_key = {"open": "return_open", "close": "return_close", "best": "return_best"}[exit_strategy]
        returns = np.array([getattr(t, ret_key) for t in all_trades]) if all_trades else np.array([])
        wins = returns > 0

        strong = [t for t in all_trades if t.signal == "STRONG_BUY"]
        normal = [t for t in all_trades if t.signal == "BUY"]

        factor_ic = {}
        for fn in ["tail_volume", "intraday_trend", "volume_accum", "auction",
                    "gap", "reversal", "ma_position", "integer_psych", "sector_rel"]:
            scores = [t.factor_scores.get(fn, 0.5) for t in all_trades]
            rets = [getattr(t, ret_key) for t in all_trades]
            if len(scores) > 5:
                ic = np.corrcoef(scores, rets)[0, 1]
                if not np.isnan(ic):
                    factor_ic[fn] = round(float(ic), 3)

        def layer(ts):
            if not ts:
                return {"count": 0, "win_rate": 0, "avg_ret": 0}
            rs = [getattr(t, ret_key) for t in ts]
            return {"count": len(ts), "win_rate": round(np.mean([r > 0 for r in rs]) * 100, 1),
                    "avg_ret": round(np.mean(rs), 2)}

        return {
            "trades": all_trades, "exit_strategy": exit_strategy,
            "total_trades": len(all_trades),
            "win_rate": round(float(np.mean(wins)) * 100, 1) if len(wins) > 0 else 0,
            "avg_return": round(float(np.mean(returns)), 2) if len(returns) > 0 else 0,
            "total_return": round(float(np.sum(returns)), 2) if len(returns) > 0 else 0,
            "profit_factor": round(self._calc_pf(returns), 2),
            "sharpe": round(self._calc_sharpe(returns), 2),
            "max_drawdown": round(float(np.max(np.maximum.accumulate(np.cumsum(returns)) - np.cumsum(returns))), 2) if len(returns) > 0 else 0,
            "strong_buy": layer(strong),
            "normal_buy": layer(normal),
            "factor_ic": factor_ic,
        }

    @staticmethod
    def _calc_pf(returns: np.ndarray) -> float:
        if len(returns) == 0:
            return 0
        wins = returns[returns > 0]
        losses = np.abs(returns[returns <= 0])
        return float(np.sum(wins) / max(np.sum(losses), 0.01))

    @staticmethod
    def _calc_sharpe(returns: np.ndarray) -> float:
        if len(returns) < 2:
            return 0
        std = np.std(returns)
        if std == 0:
            return 0
        return float(np.mean(returns) / std * np.sqrt(252))

    def _summarize(self, all_results: List[Dict]):
        """汇总优化结果"""
        if not all_results:
            return

        # 按综合评分排序（胜率为主，盈亏比为辅）
        for r in all_results:
            r["score"] = (
                r["win_rate"] / 100 * 0.55 +
                min(1.0, r["profit_factor"] / 2.5) * 0.30 +
                min(1.0, r["total_trades"] / 50) * 0.15
            )

        all_results.sort(key=lambda r: r["score"], reverse=True)
        best = all_results[0]
        self.best_result = best

        # ---- 输出 ----
        print("\n" + "=" * 60)
        print("  🏆 隔夜策略最优参数")
        print("=" * 60)

        print(f"\n  📊 全局最优:")
        print(f"     卖出策略:   {best['exit_strategy']} (次日{'开盘' if best['exit_strategy']=='open' else '收盘'}卖出)")
        print(f"     买入阈值:   {best['buy_threshold']:.3f}")
        print(f"     胜率:       {best['win_rate']:.1f}%")
        print(f"     盈亏比:     {best['profit_factor']:.2f}")
        print(f"     平均收益:   {best['avg_return']:.2f}%")
        print(f"     夏普比率:   {best['sharpe']:.2f}")
        print(f"     交易次数:   {best['total_trades']}")
        print(f"     综合评分:   {best['score']:.3f}")

        print(f"\n  ⚖️ 最优权重:")
        weights = best.get("weights", {})
        for fn in ["tail_volume", "intraday_trend", "volume_accum", "auction",
                    "gap", "reversal", "ma_position", "integer_psych", "sector_rel"]:
            w = weights.get(fn, 0)
            bar = "#" * int(w * 40) + "-" * (20 - int(w * 40))
            print(f"     {fn:<22} {w:.3f}  [{bar}]")

        # 三种策略对比
        print(f"\n  📈 卖出策略对比:")
        print(f"     {'策略':<12} {'胜率':<8} {'均收益':<10} {'盈亏比':<8} {'交易':<6}")
        for es in ["open", "close", "best"]:
            es_results = [r for r in all_results if r["exit_strategy"] == es]
            if es_results:
                top = max(es_results, key=lambda r: r["score"])
                print(f"     {es:<12} {top['win_rate']:>5.1f}%  {top['avg_return']:>8.2f}%  "
                      f"{top['profit_factor']:>7.2f}  {top['total_trades']:>5}")

        # 阈值敏感度
        print(f"\n  🎯 阈值-胜率关系:")
        thresholds = sorted(set(r["buy_threshold"] for r in all_results))
        for bt in [0.54, 0.58, 0.62, 0.66, 0.70]:
            bt_results = [r for r in all_results
                          if abs(r["buy_threshold"] - bt) < 0.02 and r["exit_strategy"] == best["exit_strategy"]]
            if bt_results:
                avg_wr = np.mean([r["win_rate"] for r in bt_results])
                avg_trades = np.mean([r["total_trades"] for r in bt_results])
                print(f"     阈值={bt:.2f} → 胜率≈{avg_wr:.1f}%  交易≈{avg_trades:.0f}笔")

        # 因子有效性排名
        if best.get("factor_ic"):
            print(f"\n  🔬 因子有效性排名 (IC值):")
            sorted_factors = sorted(best["factor_ic"].items(), key=lambda x: abs(x[1]), reverse=True)
            for fn, ic in sorted_factors:
                direction = "+" if ic > 0 else "-"
                bar = "#" * int(abs(ic) * 30) + "-" * (15 - int(abs(ic) * 30))
                print(f"     {fn:<22} {direction}{ic:.3f}  [{bar}]")

    def save_best(self, filepath: str = None) -> str:
        """保存最优参数"""
        if not self.best_result:
            return ""

        if filepath is None:
            filepath = os.path.join(os.path.dirname(__file__), "reports",
                                     "overnight_optimal.json")

        data = {
            "weights": self.best_result.get("weights", {}),
            "buy_threshold": self.best_result.get("buy_threshold", 0.58),
            "exit_strategy": self.best_result.get("exit_strategy", "open"),
            "predicted_win_rate": self.best_result.get("win_rate", 0),
            "predicted_profit_factor": self.best_result.get("profit_factor", 0),
            "predicted_sharpe": self.best_result.get("sharpe", 0),
            "avg_return": self.best_result.get("avg_return", 0),
            "factor_ic": self.best_result.get("factor_ic", {}),
            "optimized_at": datetime.datetime.now().isoformat(),
            "total_trials": len(self.best_result.get("trades", [])),
        }

        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return filepath


# ================================================================
# CLI
# ================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="隔夜持仓策略优化器")
    parser.add_argument("--trials", type=int, default=150, help="搜索次数")
    parser.add_argument("--days", type=int, default=90, help="回测天数")
    parser.add_argument("--save", action="store_true", default=True, help="保存结果")
    args = parser.parse_args()

    client = get_client()
    opt = OvernightOptimizer(client=client)
    opt.optimize_all(trials=args.trials, lookback_days=args.days)

    if args.save:
        path = opt.save_best()
        print(f"\n  💾 最优参数已保存: {path}")
