"""
胜率优化器
- 基于历史数据回测，自动搜索最优因子权重和阈值
- 优化目标：最大化胜率（兼顾盈亏比和夏普）
- 支持：网格搜索 / 随机搜索 / 贝叶斯优化

使用方法：
  python optimizer.py              # 默认搜索
  python optimizer.py --method bayesian --trials 200  # 贝叶斯优化
"""

import encoding_fix  # noqa: F401

import datetime
import itertools
import json
import math
import os
import random
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from tickflow_client import TickFlowClient, get_client
from tail_scanner import TailScanner, TailSignal


# ================================================================
# 优化结果
# ================================================================

@dataclass
class OptimizationResult:
    """一次参数组合的回测结果"""
    weights: Dict[str, float]
    buy_threshold: float
    strong_buy_threshold: float

    # 绩效指标
    total_trades: int = 0
    win_trades: int = 0
    win_rate: float = 0.0
    avg_return: float = 0.0       # 平均每笔收益(%)
    total_return: float = 0.0     # 累计收益(%)
    profit_factor: float = 0.0    # 盈亏比
    sharpe_ratio: float = 0.0     # 夏普比率
    max_drawdown: float = 0.0     # 最大回撤(%)
    score: float = 0.0            # 综合评分

    def compute_score(self,
                      win_weight: float = 0.50,
                      pf_weight: float = 0.25,
                      sharpe_weight: float = 0.15,
                      count_weight: float = 0.10):
        """计算综合优化评分"""
        # 交易次数太少不可靠，惩罚
        count_penalty = min(1.0, self.total_trades / 30)
        self.score = (
            win_weight * self.win_rate * count_penalty +
            pf_weight * min(1.0, self.profit_factor / 3.0) * count_penalty +
            sharpe_weight * min(1.0, max(0, self.sharpe_ratio) / 3.0) +
            count_weight * count_penalty
        )
        return self.score


# ================================================================
# 模拟回测引擎（轻量级，用于优化器内部）
# ================================================================

class SimulatedBacktest:
    """
    轻量级模拟回测

    在优化器的每次迭代中运行，评估一组参数的表现。
    使用TickFlow历史数据模拟每日14:40扫描。
    """

    def __init__(self, client: TickFlowClient, symbols: List[str],
                 lookback_days: int = 60):
        self.client = client
        self.symbols = symbols
        self.lookback_days = lookback_days

    def run(self, weights: Dict[str, float],
            buy_threshold: float = 0.62,
            strong_buy_threshold: float = 0.78) -> OptimizationResult:
        """
        运行模拟回测

        注意：完整的逐日回测需要大量API调用，这里做一个简化：
        - 使用最近N天的日线数据
        - 对每天模拟尾盘快照，计算因子得分
        - 买入信号次日以开盘价买入，再次日开盘卖出
        """
        scanner = TailScanner(
            client=self.client,
            weights=weights,
            buy_threshold=buy_threshold,
            strong_buy_threshold=strong_buy_threshold,
        )

        # 获取历史日线数据
        try:
            daily_map = self.client.get_daily_klines_batch(
                self.symbols, count=self.lookback_days * 2, max_workers=8,
            )
        except Exception as e:
            print(f"    [警告] K线获取失败: {e}")
            return OptimizationResult(
                weights=weights, buy_threshold=buy_threshold,
                strong_buy_threshold=strong_buy_threshold,
            )

        if not daily_map:
            return OptimizationResult(
                weights=weights, buy_threshold=buy_threshold,
                strong_buy_threshold=strong_buy_threshold,
            )

        # 找出所有交易日
        all_dates = set()
        for symbol, df in daily_map.items():
            if df is not None and not df.empty:
                if "datetime" in df.columns:
                    dates = df["datetime"].dt.date
                elif "timestamp" in df.columns:
                    dates = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.date
                else:
                    continue
                all_dates.update(dates)

        trading_dates = sorted(all_dates)
        if len(trading_dates) < 10:
            return OptimizationResult(
                weights=weights, buy_threshold=buy_threshold,
                strong_buy_threshold=strong_buy_threshold,
            )

        # 留出至少20天用于计算均线等指标
        test_dates = trading_dates[20:]
        trades = []

        for i, date in enumerate(test_dates[:-1]):  # 最后一天无法卖出
            # 对每天构建"14:40快照"
            day_signals = []
            for symbol in self.symbols:
                df = daily_map.get(symbol)
                if df is None or df.empty:
                    continue

                # 获取该日期及之前的数据
                if "datetime" in df.columns:
                    mask = df["datetime"].dt.date <= date
                elif "timestamp" in df.columns:
                    target_ts = pd.Timestamp(date).tz_localize("Asia/Shanghai").value // 10**6
                    mask = df["timestamp"] <= target_ts
                else:
                    continue

                hist = df[mask]
                if len(hist) < 20:
                    continue

                # 构建模拟quote（用当日K线模拟14:40快照）
                # 真实quote需要实时数据，这里用日线OHLCV做近似
                today_data = hist.iloc[-1] if len(hist) > 0 else None
                if today_data is None:
                    continue

                # 模拟14:40快照：价格约为当日close的98%（因为14:40还没收盘）
                est_price = float(today_data.get("close", 0))
                open_price = float(today_data.get("open", 0))
                high_price = float(today_data.get("high", 0))
                low_price = float(today_data.get("low", 0))
                volume = float(today_data.get("volume", 0))
                amount = float(today_data.get("amount", 0))
                prev_close = float(hist.iloc[-2].get("close", open_price)) if len(hist) >= 2 else open_price

                # 模拟分时数据（简化：基于日内波幅构造一个虚拟走势）
                fake_intraday = self._make_fake_intraday(open_price, high_price, low_price, est_price, volume)

                quote = {
                    "symbol": symbol,
                    "name": today_data.get("name", ""),
                    "last_price": est_price,
                    "prev_close": prev_close,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "volume": volume * 0.85,  # 14:40约完成85%交易量
                    "amount": amount * 0.85,
                    "change_pct": (est_price - prev_close) / max(prev_close, 0.01),
                    "turnover_rate": 0.02,
                    "amplitude": (high_price - low_price) / max(prev_close, 0.01),
                }

                # 计算因子
                signal = scanner._calculate_signal(
                    symbol, quote, hist.iloc[:-1], fake_intraday, {},
                )

                if signal.total_score >= buy_threshold:
                    day_signals.append({
                        "symbol": symbol,
                        "name": quote["name"],
                        "signal": "STRONG_BUY" if signal.total_score >= strong_buy_threshold else "BUY",
                        "total_score": signal.total_score,
                        "buy_price": est_price,
                        "date": date,
                    })

            # T+1模拟：买入后次日卖出
            for sig in day_signals[:5]:  # 最多5笔/天
                next_date_idx = i + 1
                if next_date_idx >= len(test_dates):
                    continue

                # 找次日价格
                next_date = test_dates[next_date_idx]
                sell_price = None
                df = daily_map.get(sig["symbol"])
                if df is not None and not df.empty:
                    if "datetime" in df.columns:
                        next_mask = df["datetime"].dt.date == next_date
                    else:
                        next_ts = pd.Timestamp(next_date).tz_localize("Asia/Shanghai").value // 10**6
                        next_mask = df["timestamp"] == next_ts
                    next_data = df[next_mask]
                    if not next_data.empty:
                        sell_price = float(next_data.iloc[0].get("open", 0))  # 次日开盘卖

                if sell_price and sell_price > 0:
                    ret = (sell_price - sig["buy_price"]) / sig["buy_price"]
                    # 扣除成本（佣金+印花税≈0.15%）
                    net_ret = ret - 0.0015
                    trades.append({
                        "date": sig["date"],
                        "symbol": sig["symbol"],
                        "name": sig["name"],
                        "buy_price": sig["buy_price"],
                        "sell_price": sell_price,
                        "return_pct": net_ret * 100,
                        "is_win": net_ret > 0,
                        "total_score": sig["total_score"],
                        "signal": sig["signal"],
                    })

        # 汇总统计
        result = OptimizationResult(
            weights=weights, buy_threshold=buy_threshold,
            strong_buy_threshold=strong_buy_threshold,
        )
        result.total_trades = len(trades)
        result.win_trades = sum(1 for t in trades if t["is_win"])
        result.win_rate = result.win_trades / max(result.total_trades, 1)

        if trades:
            returns = [t["return_pct"] for t in trades]
            result.avg_return = sum(returns) / len(returns)
            result.total_return = sum(returns)

            wins = [t["return_pct"] for t in trades if t["is_win"]]
            losses = [abs(t["return_pct"]) for t in trades if not t["is_win"]]
            result.profit_factor = sum(wins) / max(sum(losses), 0.01)

            # 夏普（简化）
            if len(returns) > 1:
                avg = np.mean(returns)
                std = np.std(returns)
                result.sharpe_ratio = (avg / std * (252 ** 0.5)) if std > 0 else 0

            # 最大回撤
            cumsum = np.cumsum(returns)
            peak = np.maximum.accumulate(cumsum)
            dd = peak - cumsum
            result.max_drawdown = float(np.max(dd)) if len(dd) > 0 else 0

        result.compute_score()
        return result

    def _make_fake_intraday(self, open_p: float, high_p: float, low_p: float,
                            close_p: float, volume: float) -> pd.DataFrame:
        """构造虚拟分时数据用于因子计算"""
        n = 48  # 48根5分钟K线
        prices = []
        vols = []
        trend = 1 if close_p > open_p else -1

        for i in range(n):
            t = i / n
            # V型或A型走势
            if trend > 0:
                if t < 0.3:
                    p = open_p + (low_p - open_p) * (t / 0.3) + random.gauss(0, 0.003) * open_p
                elif t > 0.7:
                    p = low_p + (close_p - low_p) * ((t - 0.7) / 0.3) + random.gauss(0, 0.003) * open_p
                else:
                    p = low_p + random.gauss(0, 0.005) * open_p
            else:
                if t < 0.3:
                    p = open_p + (high_p - open_p) * (t / 0.3) + random.gauss(0, 0.003) * open_p
                elif t > 0.7:
                    p = high_p + (close_p - high_p) * ((t - 0.7) / 0.3) + random.gauss(0, 0.003) * open_p
                else:
                    p = high_p + random.gauss(0, 0.005) * open_p

            p = max(low_p * 0.98, min(high_p * 1.02, p))
            prices.append(p)
            # 尾盘放量
            vol_mult = random.uniform(1.3, 2.5) if i >= n - 6 else random.uniform(0.6, 1.2)
            vols.append(volume / n * vol_mult)

        return pd.DataFrame({
            "close": prices,
            "open": [p * random.uniform(0.998, 1.002) for p in prices],
            "high": [p * random.uniform(1.0, 1.008) for p in prices],
            "low": [p * random.uniform(0.992, 1.0) for p in prices],
            "volume": vols,
        })


# ================================================================
# 优化器
# ================================================================

class WeightOptimizer:
    """因子权重优化器"""

    # 搜索空间定义
    WEIGHT_BOUNDS = {
        "tail_volume": (0.10, 0.35),      # 尾盘放量（核心）
        "intraday_trend": (0.08, 0.30),   # 日内趋势
        "volume_accum": (0.05, 0.20),     # 量能堆积
        "auction": (0.03, 0.18),          # 竞价强度
        "gap": (0.03, 0.15),              # 缺口博弈
        "reversal": (0.03, 0.18),         # 断板反包
        "ma_position": (0.02, 0.10),      # 均线位置
        "integer_psych": (0.01, 0.08),    # 整数关口
        "sector_rel": (0.03, 0.18),       # 板块相对强度
    }

    def __init__(self, client: TickFlowClient = None,
                 symbols: List[str] = None):
        self.client = client or get_client()
        self.symbols = symbols or self._get_default_symbols()
        self.results: List[OptimizationResult] = []

    def _get_default_symbols(self) -> List[str]:
        """获取默认优化股票池"""
        # 各行业代表 + 历史活跃标的
        samples = [
            # 上海主板
            "600519.SH", "600036.SH", "600030.SH", "600887.SH",
            "601012.SH", "601318.SH", "600900.SH", "601899.SH",
            "600809.SH", "601398.SH", "600031.SH", "601088.SH",
            # 深圳主板
            "000858.SZ", "000333.SZ", "000001.SZ", "000651.SZ",
            "002594.SZ", "002415.SZ", "000568.SZ", "002475.SZ",
            "000725.SZ", "002714.SZ",
            # 创业板
            "300750.SZ", "300059.SZ", "300274.SZ", "300124.SZ",
            "300760.SZ", "300015.SZ", "300014.SZ",
            # 科创板
            "688981.SH", "688111.SH", "688036.SH",
        ]
        return samples

    # ================================================================
    # 网格搜索
    # ================================================================

    def grid_search(self, backtest_days: int = 60,
                    steps_per_factor: int = 3) -> List[OptimizationResult]:
        """
        网格搜索最优权重

        对每个因子在[min, max]范围内均匀采样，组合搜索。
        由于全组合爆炸，使用分层策略：
        1. 先用大步长粗搜索
        2. 找到最优区域后用细步长精搜索
        """
        print(f"  🔬 网格搜索优化 (回测{days}天, 每因子{steps_per_factor}步)")

        backtest = SimulatedBacktest(self.client, self.symbols, lookback_days=backtest_days)
        results = []

        # 生成权重候选（分层：核心因子更多组合，次要因子少些）
        core_factors = ["tail_volume", "intraday_trend"]
        mid_factors = ["volume_accum", "auction", "reversal", "sector_rel"]
        minor_factors = ["gap", "ma_position", "integer_psych"]

        # 对核心因子生成候选值
        tail_candidates = np.linspace(0.15, 0.35, steps_per_factor + 1)
        trend_candidates = np.linspace(0.10, 0.28, steps_per_factor + 1)

        total_combos = len(tail_candidates) * len(trend_candidates) * 10  # 随机10组次要因子
        print(f"     预计搜索 {total_combos} 组参数...")
        count = 0

        for tw in tail_candidates:
            for iw in trend_candidates:
                remaining = 1.0 - tw - iw
                if remaining < 0.35:  # 不够分配其他因子
                    continue

                # 随机采样次要因子10次
                for _ in range(10):
                    # 分配剩余权重
                    mid_weights = self._random_split(remaining * 0.6, len(mid_factors))
                    minor_weights = self._random_split(remaining * 0.4, len(minor_factors))

                    weights = {"tail_volume": tw, "intraday_trend": iw}
                    for fn, w in zip(mid_factors, mid_weights):
                        weights[fn] = w
                    for fn, w in zip(minor_factors, minor_weights):
                        weights[fn] = w

                    # 归一化
                    total = sum(weights.values())
                    weights = {k: v / total for k, v in weights.items()}

                    # 对多个阈值组合测试
                    for bt in [0.58, 0.62, 0.65]:
                        result = backtest.run(
                            weights=weights,
                            buy_threshold=bt,
                            strong_buy_threshold=bt + 0.15,
                        )
                        results.append(result)
                        count += 1

                        if count % 20 == 0:
                            best = max(results, key=lambda r: r.score) if results else None
                            best_score = f"{best.score:.3f}" if best else "-"
                            print(f"     [{count}] 最佳评分={best_score}")

        self.results = results
        return sorted(results, key=lambda r: r.score, reverse=True)

    # ================================================================
    # 随机搜索
    # ================================================================

    def random_search(self, trials: int = 500, backtest_days: int = 60) -> List[OptimizationResult]:
        """
        随机搜索最优权重
        在大搜索空间中随机采样，效率高于网格搜索
        """
        print(f"  🎲 随机搜索优化 ({trials}次试验, 回测{backtest_days}天)")

        backtest = SimulatedBacktest(self.client, self.symbols, lookback_days=backtest_days)
        results = []

        for i in range(trials):
            # 从搜索空间随机采样权重
            raw_weights = {}
            for fn, (lo, hi) in self.WEIGHT_BOUNDS.items():
                raw_weights[fn] = random.uniform(lo, hi)

            # 归一化
            total = sum(raw_weights.values())
            weights = {k: v / total for k, v in raw_weights.items()}

            # 随机阈值
            buy_threshold = random.uniform(0.55, 0.68)

            result = backtest.run(
                weights=weights,
                buy_threshold=buy_threshold,
                strong_buy_threshold=buy_threshold + 0.15,
            )
            results.append(result)

            if (i + 1) % 50 == 0:
                best = max(results, key=lambda r: r.score)
                print(f"     [{i+1}/{trials}] 最佳: 胜率={best.win_rate*100:.1f}% "
                      f"评分={best.score:.3f} 交易={best.total_trades}")

        self.results = results
        return sorted(results, key=lambda r: r.score, reverse=True)

    def _random_split(self, total: float, n: int) -> List[float]:
        """将total随机分成n份"""
        if n <= 1:
            return [total]
        points = sorted([random.random() for _ in range(n - 1)])
        parts = []
        prev = 0
        for p in points + [1.0]:
            parts.append(total * (p - prev))
            prev = p
        return parts

    # ================================================================
    # 贝叶斯优化（简化版：基于历史结果的外推）
    # ================================================================

    def bayesian_optimize(self, trials: int = 100, backtest_days: int = 60) -> List[OptimizationResult]:
        """
        简化贝叶斯优化

        策略：
        1. 先随机搜索30%的试验
        2. 找出top20%的参数
        3. 在top区域附近密集采样
        4. 重复直到用完预算
        """
        print(f"  🧠 贝叶斯优化 ({trials}次试验)")

        backtest = SimulatedBacktest(self.client, self.symbols, lookback_days=backtest_days)

        # 阶段1: 探索（40%预算随机搜索）
        explore_trials = int(trials * 0.4)
        print(f"     [阶段1] 探索 {explore_trials} 次...")
        explore_results = []
        for _ in range(explore_trials):
            raw = {fn: random.uniform(lo, hi) for fn, (lo, hi) in self.WEIGHT_BOUNDS.items()}
            total = sum(raw.values())
            weights = {k: v / total for k, v in raw.items()}
            bt = random.uniform(0.55, 0.68)
            result = backtest.run(weights=weights, buy_threshold=bt, strong_buy_threshold=bt + 0.15)
            explore_results.append(result)

        # 找top 20%
        explore_results.sort(key=lambda r: r.score, reverse=True)
        top_n = max(5, int(len(explore_results) * 0.2))
        top_results = explore_results[:top_n]

        # 阶段2: 利用（60%预算在top区域附近密集搜索）
        exploit_trials = trials - explore_trials
        print(f"     [阶段2] 利用 {exploit_trials} 次（在{top_n}个最优区域附近）...")
        all_results = explore_results.copy()

        for _ in range(exploit_trials):
            # 选一个top结果作为基准
            base = random.choice(top_results)

            # 在基准附近扰动
            raw = {}
            for fn in self.WEIGHT_BOUNDS:
                noise = random.gauss(0, 0.03)  # 标准差3%
                raw[fn] = max(0.01, base.weights.get(fn, 0.1) + noise)

            total = sum(raw.values())
            weights = {k: v / total for k, v in raw.items()}

            # 阈值也在基准附近扰动
            bt = max(0.50, min(0.72, base.buy_threshold + random.gauss(0, 0.02)))

            result = backtest.run(weights=weights, buy_threshold=bt, strong_buy_threshold=bt + 0.15)
            all_results.append(result)

            # 动态更新top区域
            all_results.sort(key=lambda r: r.score, reverse=True)
            top_results = all_results[:max(5, int(len(all_results) * 0.15))]

            if len(all_results) % 30 == 0:
                best = all_results[0]
                print(f"     [{len(all_results)}/{trials}] 最佳: 胜率={best.win_rate*100:.1f}% "
                      f"评分={best.score:.3f} 盈亏比={best.profit_factor:.2f}")

        self.results = all_results
        return sorted(all_results, key=lambda r: r.score, reverse=True)

    # ================================================================
    # 结果输出
    # ================================================================

    def print_best(self, top_n: int = 10):
        """打印最优结果"""
        if not self.results:
            print("  ⚠️ 无优化结果")
            return

        sorted_results = sorted(self.results, key=lambda r: r.score, reverse=True)
        best = sorted_results[0]

        print("\n" + "=" * 70)
        print("  🏆 最优参数组合")
        print("=" * 70)
        print(f"\n  📊 绩效预测:")
        print(f"     胜率:       {best.win_rate*100:.1f}%")
        print(f"     盈亏比:     {best.profit_factor:.2f}")
        print(f"     平均收益:   {best.avg_return:.2f}%")
        print(f"     夏普比率:   {best.sharpe_ratio:.2f}")
        print(f"     交易次数:   {best.total_trades}")
        print(f"     综合评分:   {best.score:.3f}")

        print(f"\n  ⚖️ 最优权重:")
        for fn in ["tail_volume", "intraday_trend", "volume_accum", "auction",
                    "gap", "reversal", "ma_position", "integer_psych", "sector_rel"]:
            w = best.weights.get(fn, 0)
            bar = "█" * int(w * 50) + "░" * (15 - int(w * 50))
            print(f"     {fn:<22} {w:.3f}  {bar}")

        print(f"\n  🎯 最优阈值:")
        print(f"     买入阈值:   {best.buy_threshold:.3f}")
        print(f"     强买入:     {best.strong_buy_threshold:.3f}")

        if len(sorted_results) > 1:
            print(f"\n  📋 Top {min(top_n, len(sorted_results))} 参数组合:")
            print(f"     {'排名':<5} {'胜率':<8} {'盈亏比':<8} {'交易':<6} {'评分':<7} {'阈值':<6}")
            for i, r in enumerate(sorted_results[:top_n]):
                print(f"     {i+1:<5} {r.win_rate*100:>6.1f}% {r.profit_factor:>7.2f} "
                      f"{r.total_trades:>5}  {r.score:>6.3f} {r.buy_threshold:>5.2f}")

    def save_best(self, filepath: str = None) -> str:
        """保存最优参数到JSON"""
        if not self.results:
            return ""

        best = max(self.results, key=lambda r: r.score)
        if filepath is None:
            filepath = os.path.join(os.path.dirname(__file__), "reports", "optimal_weights.json")

        data = {
            "weights": best.weights,
            "buy_threshold": best.buy_threshold,
            "strong_buy_threshold": best.strong_buy_threshold,
            "predicted_win_rate": best.win_rate,
            "predicted_profit_factor": best.profit_factor,
            "predicted_sharpe": best.sharpe_ratio,
            "optimized_at": datetime.datetime.now().isoformat(),
            "search_method": "bayesian",
            "total_trials": len(self.results),
        }

        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return filepath


# ================================================================
# CLI入口
# ================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="因子权重胜率优化器")
    parser.add_argument("--method", choices=["grid", "random", "bayesian"],
                        default="random", help="优化方法")
    parser.add_argument("--trials", type=int, default=200, help="搜索次数")
    parser.add_argument("--days", type=int, default=60, help="回测天数")
    parser.add_argument("--save", action="store_true", help="保存最优参数")
    args = parser.parse_args()

    client = get_client()
    opt = WeightOptimizer(client=client)

    if args.method == "grid":
        results = opt.grid_search(backtest_days=args.days)
    elif args.method == "random":
        results = opt.random_search(trials=args.trials, backtest_days=args.days)
    else:
        results = opt.bayesian_optimize(trials=args.trials, backtest_days=args.days)

    opt.print_best(top_n=10)

    if args.save:
        path = opt.save_best()
        print(f"\n  💾 最优参数已保存: {path}")
