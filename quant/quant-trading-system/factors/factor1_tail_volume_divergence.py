"""
因子1: 尾盘量价背离
- 监测14:30-15:00尾盘30分钟的量价关系
- 量增价涨 → 看多（资金尾盘抢筹）
- 量增价跌 → 看空（资金尾盘出逃）
- 量缩价平 → 中性

信号逻辑：
- 尾盘成交量超过全日均量1.5倍 且 尾盘价格涨幅>0.3% → 强看多
- 尾盘成交量超过全日均量1.5倍 且 尾盘价格跌幅>0.3% → 强看空
"""

import math
from typing import Any, Dict

from . import BaseFactor, FactorResult
from liquidity_filter import MarketSnapshot


class TailVolumeDivergenceFactor(BaseFactor):
    name = "tail_volume_divergence"
    description = "尾盘量价背离 — 监测尾盘30分钟量价异动"
    category = "量价"

    def calculate(self, snapshot: MarketSnapshot) -> FactorResult:
        minute_bars = snapshot.minute_bars
        daily_bars = snapshot.daily_bars

        # 数据质量评估
        if not minute_bars or len(minute_bars) < 30:
            return FactorResult(
                factor_name=self.name,
                raw_score=0.5,
                normalized_score=0.5,
                signal=0,
                confidence=0.1,
                detail={"error": "分钟线数据不足"},
            )

        # 提取尾盘30分钟数据（最后6根5分钟K线）
        tail_bars = minute_bars[-6:]  # 14:30-15:00
        all_bars = minute_bars[-78:]  # 全天390分钟=78根5分钟K线

        if not tail_bars or not all_bars:
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.1, detail={})

        # 计算尾盘平均每根K线成交量
        tail_avg_vol = sum(b.get("volume", 0) for b in tail_bars) / max(len(tail_bars), 1)
        all_avg_vol = sum(b.get("volume", 0) for b in all_bars) / max(len(all_bars), 1)

        # 尾盘价格变化
        tail_open = tail_bars[0].get("open", 0)
        tail_close = tail_bars[-1].get("close", 0)
        tail_return = (tail_close - tail_open) / max(tail_open, 0.01)

        # 全日价格变化
        day_open = all_bars[0].get("open", 0) if all_bars else 0
        day_close = all_bars[-1].get("close", 0) if all_bars else 0
        day_return = (day_close - day_open) / max(day_open, 0.01)

        # 量比
        vol_ratio = tail_avg_vol / max(all_avg_vol, 1)

        # ----- 计算原始得分 -----
        raw_score = 0.5  # 中性起点
        detail = {
            "tail_return": round(tail_return * 100, 3),  # 百分比
            "day_return": round(day_return * 100, 3),
            "vol_ratio": round(vol_ratio, 3),
            "tail_bars_count": len(tail_bars),
        }

        if vol_ratio > 1.5:
            # 尾盘放量显著 → 加强信号
            if tail_return > 0.003:  # 尾盘涨>0.3%
                # 量价齐升 → 看多
                divergence_score = min(1.0, 0.5 + tail_return * 30 + (vol_ratio - 1.5) * 0.3)
                raw_score = divergence_score
                detail["pattern"] = "尾盘放量抢筹"
                detail["strength"] = "strong_bullish"
            elif tail_return < -0.003:  # 尾盘跌>0.3%
                # 放量下跌 → 看空
                divergence_score = max(0.0, 0.5 - abs(tail_return) * 30 - (vol_ratio - 1.5) * 0.3)
                raw_score = divergence_score
                detail["pattern"] = "尾盘放量出逃"
                detail["strength"] = "strong_bearish"
            else:
                # 放量但价格波动小 → 分歧加大，中性偏空
                raw_score = 0.45
                detail["pattern"] = "尾盘放量滞涨"
                detail["strength"] = "neutral_bearish"
        elif vol_ratio > 1.2:
            # 温和放量
            if tail_return > 0.002:
                raw_score = 0.55 + tail_return * 20
                detail["pattern"] = "尾盘温和放量上涨"
            elif tail_return < -0.002:
                raw_score = 0.45 - abs(tail_return) * 20
                detail["pattern"] = "尾盘温和放量下跌"
            else:
                raw_score = 0.50
                detail["pattern"] = "尾盘量价平稳"
        else:
            # 缩量 → 方向不明
            raw_score = 0.50
            detail["pattern"] = "尾盘缩量"

        # 信号判定
        if raw_score >= 0.65:
            signal = 1
        elif raw_score <= 0.35:
            signal = -1
        else:
            signal = 0

        normalized = self.normalize_score(raw_score)

        # 置信度
        data_quality = min(1.0, len(minute_bars) / 78)  # 数据越完整越好
        signal_strength = abs(raw_score - 0.5) * 2  # 偏离中性越远越强
        confidence = self.compute_confidence(data_quality, signal_strength)

        return FactorResult(
            factor_name=self.name,
            raw_score=raw_score,
            normalized_score=normalized,
            signal=signal,
            confidence=confidence,
            detail=detail,
        )
