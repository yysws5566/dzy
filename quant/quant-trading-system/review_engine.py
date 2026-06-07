"""
复盘反馈自进化引擎
==================
核心创新：用每日真实交易结果替代模拟回测，持续优化因子权重。

工作流程：
1. 收盘后：加载昨日选股信号
2. 获取今日早盘数据（开盘价/最高价/最低价）
3. 计算每笔信号的实际隔夜收益
4. 分析哪些因子得分能区分盈利/亏损
5. 更新因子权重（贝叶斯更新）
6. 积累真实交易数据库

随着真实交易数据积累，算法会越来越准。
"""

import encoding_fix  # noqa
import datetime
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from tickflow_client import TickFlowClient, get_client
from merged_scanner import MergedScanner, MergedSignal
from tail_scanner import TailScanner, TailSignal


# ================================================================
# 复盘记录
# ================================================================

@dataclass
class ReviewRecord:
    """单笔复盘记录"""
    review_date: str              # 复盘日期
    signal_date: str              # 选股日期
    symbol: str
    name: str
    signal_type: str              # 'merged' / 'tail' / 'legacy'

    # 买入信息
    buy_price: float              # 尾盘买入价（≈收盘价）
    total_score: float
    signal: str

    # 次日早盘表现
    next_open: float              # 次日开盘价
    next_high: float              # 次日最高价
    next_low: float               # 次日最低价
    next_close: float             # 次日收盘价（如果有）

    # 收益
    return_open: float            # 开盘卖收益率(%)
    return_high: float            # 最高卖收益率(%)
    return_close: float           # 收盘卖收益率(%)

    is_win: bool                  # 是否盈利（开盘卖）

    # 各因子得分（用于分析哪些因子有效）
    factor_scores: Dict[str, float] = field(default_factory=dict)


# ================================================================
# 复盘数据库
# ================================================================

class ReviewDatabase:
    """复盘数据库 — 持久化存储所有真实交易记录"""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.path.join(os.path.dirname(__file__), "reports", "review_db.json")
        self.db_path = db_path
        self.records: List[Dict] = self._load()

    def _load(self) -> List[Dict]:
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("records", [])
            except (json.JSONDecodeError, KeyError):
                return []
        return []

    def save(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump({
                "records": self.records,
                "total": len(self.records),
                "last_updated": datetime.datetime.now().isoformat(),
            }, f, ensure_ascii=False, indent=2)

    def add(self, record: ReviewRecord):
        self.records.append({
            "review_date": record.review_date,
            "signal_date": record.signal_date,
            "symbol": record.symbol,
            "name": record.name,
            "signal_type": record.signal_type,
            "buy_price": record.buy_price,
            "total_score": record.total_score,
            "signal": record.signal,
            "next_open": record.next_open,
            "next_high": record.next_high,
            "next_low": record.next_low,
            "return_open": round(record.return_open, 3),
            "return_high": round(record.return_high, 3),
            "return_close": round(record.return_close, 3),
            "is_win": record.is_win,
            "factor_scores": record.factor_scores,
        })

    def get_stats(self, signal_type: str = None) -> Dict[str, Any]:
        """查询复盘统计"""
        records = self.records
        if signal_type:
            records = [r for r in records if r.get("signal_type") == signal_type]

        if not records:
            return {"total": 0, "win_rate": 0, "avg_return": 0}

        wins = sum(1 for r in records if r.get("is_win"))
        returns = [r.get("return_open", 0) for r in records]
        avg_ret = sum(returns) / len(returns)
        win_returns = [r.get("return_open", 0) for r in records if r.get("is_win")]
        lose_returns = [abs(r.get("return_open", 0)) for r in records if not r.get("is_win")]

        return {
            "total": len(records),
            "wins": wins,
            "losses": len(records) - wins,
            "win_rate": round(wins / len(records) * 100, 1),
            "avg_return": round(avg_ret, 2),
            "total_return": round(sum(returns), 2),
            "profit_factor": round(sum(win_returns) / max(sum(lose_returns), 0.01), 2),
            "best_trade": round(max(returns), 2),
            "worst_trade": round(min(returns), 2),
        }


# ================================================================
# 复盘引擎
# ================================================================

class ReviewEngine:
    """
    复盘反馈引擎

    每日收盘后运行，分析昨日信号表现，更新权重。
    """

    FACTOR_NAMES = [
        "tail_volume", "intraday_trend", "volume_accum", "auction",
        "gap", "reversal", "ma_position", "seal_quality",
        "global_linkage", "overnight_risk",
    ]

    def __init__(self, client: TickFlowClient = None):
        self.client = client or get_client()
        self.db = ReviewDatabase()

    def review_yesterday(self, yesterday_signals: List[Dict],
                          signal_type: str = "merged") -> List[ReviewRecord]:
        """
        复盘昨日选股信号

        Args:
            yesterday_signals: 昨日保存的信号列表
                [{symbol, name, buy_price, total_score, signal, factor_scores, ...}]
            signal_type: 'merged' / 'tail' / 'legacy'

        Returns:
            复盘记录列表
        """
        if not yesterday_signals:
            print("  ⚠️ 昨日无选股信号，跳过复盘")
            return []

        today = datetime.date.today()
        symbols = [s["symbol"] for s in yesterday_signals]

        print(f"\n  📊 复盘昨日信号: {len(symbols)} 只")
        print(f"     日期: {today}")

        # 获取今日行情数据
        try:
            quotes_today = self.client.get_realtime_quotes(symbols)
            quote_map = {q["symbol"]: q for q in quotes_today}
        except Exception as e:
            print(f"     ❌ 今日行情获取失败: {e}")
            return []

        # 获取今日日K（用于开盘价）
        try:
            daily_today = self.client.get_daily_klines_batch(symbols, count=2)
        except Exception:
            daily_today = {}

        records = []

        for sig in yesterday_signals:
            sym = sig["symbol"]
            q = quote_map.get(sym)
            if not q:
                continue

            buy_price = sig.get("buy_price", sig.get("current_price", 0))
            if buy_price <= 0:
                continue

            # 今日表现
            next_open = q.get("open", q.get("last_price", buy_price))
            next_high = q.get("high", next_open)
            next_low = q.get("low", next_open)
            next_close = q.get("last_price", next_open)

            # 从日K取更准确的开盘价
            df = daily_today.get(sym)
            if df is not None and not df.empty:
                if "open" in df.columns:
                    next_open = float(df.iloc[-1].get("open", next_open))
                    next_high = float(df.iloc[-1].get("high", next_high))
                    next_low = float(df.iloc[-1].get("low", next_low))
                    next_close = float(df.iloc[-1].get("close", next_close))

            ret_open = (next_open - buy_price) / buy_price * 100
            ret_high = (next_high - buy_price) / buy_price * 100
            ret_close = (next_close - buy_price) / buy_price * 100

            record = ReviewRecord(
                review_date=today.isoformat(),
                signal_date=sig.get("date", str(today - datetime.timedelta(days=1))),
                symbol=sym,
                name=sig.get("name", ""),
                signal_type=signal_type,
                buy_price=buy_price,
                total_score=sig.get("total_score", 0),
                signal=sig.get("signal", "BUY"),
                next_open=next_open,
                next_high=next_high,
                next_low=next_low,
                next_close=next_close,
                return_open=round(ret_open, 3),
                return_high=round(ret_high, 3),
                return_close=round(ret_close, 3),
                is_win=ret_open > 0,
                factor_scores=sig.get("factor_scores", {}),
            )

            self.db.add(record)
            records.append(record)

        self.db.save()

        # 输出复盘摘要
        self._print_review_summary(records)

        return records

    def _print_review_summary(self, records: List[ReviewRecord]):
        """打印复盘摘要"""
        if not records:
            return

        wins = [r for r in records if r.is_win]
        losses = [r for r in records if not r.is_win]
        win_rate = len(wins) / len(records) * 100

        print(f"\n  {'='*50}")
        print(f"  📈 昨日复盘结果")
        print(f"  {'='*50}")
        print(f"     总信号:  {len(records)} 只")
        print(f"     盈利:    {len(wins)} 只 ({win_rate:.1f}%)")
        print(f"     亏损:    {len(losses)} 只 ({100-win_rate:.1f}%)")
        if wins:
            avg_win = np.mean([r.return_open for r in wins])
            print(f"     均盈利:  +{avg_win:.2f}%")
        if losses:
            avg_loss = np.mean([r.return_open for r in losses])
            print(f"     均亏损:  {avg_loss:.2f}%")
        if records:
            avg_all = np.mean([r.return_open for r in records])
            print(f"     均收益:  {avg_all:+.2f}%")

        # 各因子在盈利/亏损组中的均值对比
        print(f"\n  🔬 因子贡献分析 (盈利组 vs 亏损组):")
        print(f"     {'因子':<22} {'盈利组均值':<10} {'亏损组均值':<10} {'差异':<8} {'有效性':<8}")
        print(f"     {'-'*58}")

        factor_effectiveness = {}
        for fn in self.FACTOR_NAMES:
            win_scores = [r.factor_scores.get(fn, 0.5) for r in wins]
            lose_scores = [r.factor_scores.get(fn, 0.5) for r in losses]
            win_mean = np.mean(win_scores) if win_scores else 0.5
            lose_mean = np.mean(lose_scores) if lose_scores else 0.5
            diff = win_mean - lose_mean

            # 有效性 = 差异显著且方向正确（盈利组得分更高）
            if diff > 0.03:
                eff = "有效 ✓"
            elif diff < -0.03:
                eff = "反向 ✗"
            else:
                eff = "中性 -"

            factor_effectiveness[fn] = diff
            print(f"     {fn:<22} {win_mean:.3f}      {lose_mean:.3f}      {diff:+.3f}   {eff}")

        print(f"\n  📊 全局统计:")
        stats = self.db.get_stats()
        print(f"     累计交易: {stats['total']} 笔")
        print(f"     累计胜率: {stats['win_rate']}%")
        print(f"     累计收益: {stats['total_return']:.2f}%")

    def update_weights(self, current_weights: Dict[str, float],
                       learning_rate: float = 0.05) -> Dict[str, float]:
        """
        基于复盘数据更新因子权重

        策略：对"盈利组得分 > 亏损组得分"的因子增加权重，
              对"亏损组得分 > 盈利组得分"的因子减少权重。

        Args:
            current_weights: 当前权重
            learning_rate: 学习率（0.01-0.10，越大调整越激进）

        Returns:
            更新后的权重
        """
        records = self.db.records
        if len(records) < 10:
            print("  ⚠️ 复盘数据不足（<10笔），保持当前权重")
            return current_weights

        wins = [r for r in records if r.get("is_win")]
        losses = [r for r in records if not r.get("is_win")]

        if not wins or not losses:
            return current_weights

        # 计算每个因子的有效性
        adjustments = {}
        for fn in self.FACTOR_NAMES:
            win_scores = [r.get("factor_scores", {}).get(fn, 0.5) for r in wins]
            lose_scores = [r.get("factor_scores", {}).get(fn, 0.5) for r in losses]
            win_mean = np.mean(win_scores)
            lose_mean = np.mean(lose_scores)
            diff = win_mean - lose_mean
            adjustments[fn] = diff

        # 更新权重
        new_weights = current_weights.copy()
        for fn in self.FACTOR_NAMES:
            adj = adjustments.get(fn, 0) * learning_rate
            new_weights[fn] = max(0.02, min(0.35, new_weights.get(fn, 0.1) + adj))

        # 归一化
        total = sum(new_weights.values())
        new_weights = {k: round(v / total, 4) for k, v in new_weights.items()}

        print(f"\n  🔄 权重更新 (学习率={learning_rate}):")
        for fn in self.FACTOR_NAMES:
            old_w = current_weights.get(fn, 0.1)
            new_w = new_weights.get(fn, 0.1)
            change = new_w - old_w
            direction = "↑" if change > 0 else ("↓" if change < 0 else "→")
            adj_val = adjustments.get(fn, 0)
            print(f"     {fn:<22} {old_w:.3f} {direction} {new_w:.3f}  "
                  f"(有效性={adj_val:+.3f})")

        return new_weights

    def optimize_weights_from_db(self, min_records: int = 20) -> Optional[Dict[str, float]]:
        """
        从复盘数据库直接计算最优权重

        基于累积的真实交易数据，用线性回归估计每个因子的最优权重。
        数据足够多时，这比模拟回测更准确。
        """
        records = self.db.records
        if len(records) < min_records:
            print(f"  ⚠️ 需要至少{min_records}笔记录，当前{len(records)}笔")
            return None

        # 构建特征矩阵和标签
        X = []
        y = []
        for r in records:
            features = [r.get("factor_scores", {}).get(fn, 0.5) for fn in self.FACTOR_NAMES]
            X.append(features)
            y.append(1 if r.get("is_win") else 0)

        X = np.array(X)
        y = np.array(y)

        # 用每个因子的胜率差异来确定权重
        weights = {}
        for i, fn in enumerate(self.FACTOR_NAMES):
            scores = X[:, i]
            # 高分组的胜率 vs 低分组的胜率
            median = np.median(scores)
            high_mask = scores >= median
            low_mask = scores < median

            high_win_rate = np.mean(y[high_mask]) if np.any(high_mask) else 0.5
            low_win_rate = np.mean(y[low_mask]) if np.any(low_mask) else 0.5

            # 权重 = 高分组胜率优势
            advantage = max(0, high_win_rate - low_win_rate)
            weights[fn] = advantage

        # 归一化
        total = sum(weights.values())
        if total > 0:
            weights = {k: round(v / total, 4) for k, v in weights.items()}
        else:
            # 所有因子都没有区分度，回退到等权重
            weights = {fn: 1.0 / len(self.FACTOR_NAMES) for fn in self.FACTOR_NAMES}

        print(f"\n  🧬 从{len(records)}笔真实数据计算最优权重:")
        for fn in self.FACTOR_NAMES:
            w = weights.get(fn, 0)
            bar = "#" * int(w * 40) + "-" * (20 - int(w * 40))
            print(f"     {fn:<22} {w:.3f}  [{bar}]")

        return weights


# ================================================================
# 每日复盘主流程
# ================================================================

def run_daily_review(
    signal_file: str = None,
    signal_type: str = "merged",
    update_weights: bool = True,
) -> Dict[str, Any]:
    """
    每日复盘主流程

    1. 加载昨日信号文件
    2. 获取今日早盘数据
    3. 计算收益并保存
    4. 更新因子权重

    Args:
        signal_file: 昨日信号JSON文件路径
        signal_type: 'merged' / 'tail'
        update_weights: 是否自动更新权重

    Returns:
        复盘统计
    """
    client = get_client()
    engine = ReviewEngine(client)

    # 加载昨日信号
    if signal_file is None:
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        signal_file = os.path.join(
            os.path.dirname(__file__), "reports",
            f"signals_{yesterday.isoformat()}.json",
        )

    if not os.path.exists(signal_file):
        print(f"  ⚠️ 信号文件不存在: {signal_file}")
        print(f"  💡 请先运行选股扫描: python main.py --tail --save-signals")
        return {}

    with open(signal_file, "r", encoding="utf-8") as f:
        signals_data = json.load(f)

    signals = signals_data.get("signals", [])
    if not signals:
        print("  ⚠️ 昨日无信号")
        return {}

    print("=" * 60)
    print("  📊 每日复盘反馈引擎")
    print("=" * 60)

    # 复盘
    records = engine.review_yesterday(signals, signal_type=signal_type)

    # 更新权重
    if update_weights and records:
        # 加载当前权重
        weights_path = os.path.join(
            os.path.dirname(__file__), "reports", "merged_weights.json",
        )
        if os.path.exists(weights_path):
            with open(weights_path, "r") as f:
                current_weights = json.load(f).get("weights", MergedScanner.DEFAULT_WEIGHTS)
        else:
            current_weights = MergedScanner.DEFAULT_WEIGHTS.copy()

        # 更新
        new_weights = engine.update_weights(current_weights, learning_rate=0.05)

        # 也从数据库直接计算一版做参考
        db_weights = engine.optimize_weights_from_db(min_records=15)

        # 保存
        save_data = {
            "weights": new_weights,
            "updated_at": datetime.datetime.now().isoformat(),
            "total_records": len(engine.db.records),
            "db_optimized_weights": db_weights,
        }
        os.makedirs(os.path.dirname(weights_path), exist_ok=True)
        with open(weights_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        print(f"\n  💾 权重已更新: {weights_path}")

    # 打印最终统计
    stats = engine.db.get_stats(signal_type)
    print(f"\n  🏆 当前{'融合' if signal_type == 'merged' else '尾盘'}算法累计表现:")
    print(f"     交易: {stats['total']}笔 | 胜率: {stats['win_rate']}% | "
          f"均收益: {stats['avg_return']}% | 盈亏比: {stats['profit_factor']}")

    return stats


# ================================================================
# CLI
# ================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="每日复盘反馈引擎")
    parser.add_argument("--signal-file", type=str, default=None,
                        help="昨日信号JSON文件路径")
    parser.add_argument("--type", choices=["merged", "tail", "legacy"],
                        default="merged", help="算法类型")
    parser.add_argument("--no-update", action="store_true",
                        help="不自动更新权重")
    parser.add_argument("--stats", action="store_true",
                        help="仅查看累积统计")
    args = parser.parse_args()

    if args.stats:
        engine = ReviewEngine()
        for st in ["merged", "tail", "legacy"]:
            s = engine.db.get_stats(st)
            if s["total"] > 0:
                print(f"  {st}: {s['total']}笔 胜率{s['win_rate']}% 均收{s['avg_return']}%")
        if all(engine.db.get_stats(st)["total"] == 0 for st in ["merged", "tail", "legacy"]):
            print("  📭 暂无复盘数据。运行选股后次日执行复盘即可积累。")
    else:
        run_daily_review(
            signal_file=args.signal_file,
            signal_type=args.type,
            update_weights=not args.no_update,
        )
