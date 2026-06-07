"""
因子13: RSI 超卖反弹因子
=======================
基于 14 日 RSI 指标，识别超卖（<35）反弹机会。
隔夜策略中 RSI 超卖的股票隔天反弹概率显著高于随机。
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "strategies"))
from enhanced_factors import calc_rsi

from dataclasses import dataclass
from typing import Any, Dict, Optional
import numpy as np


@dataclass
class FactorResult:
    factor_name: str = "rsi_oversold"
    raw_score: float = 0.0
    normalized_score: float = 0.0
    confidence: float = 0.5
    signal: str = "NEUTRAL"
    detail: Dict[str, Any] = None

    def __post_init__(self):
        if self.detail is None:
            self.detail = {}


class RsiOversoldFactor:
    """RSI 超卖反弹因子"""

    def __init__(self):
        self.name = "rsi_oversold"
        self.period = 14

    def compute(self, closes: np.ndarray) -> FactorResult:
        if len(closes) < self.period + 1:
            return FactorResult(detail={"error": "数据不足"})

        rsi = calc_rsi(closes, self.period)

        # RSI越低，反弹概率越高
        if rsi < 25:
            raw = 0.95; signal = "STRONG_BUY"
        elif rsi < 30:
            raw = 0.85; signal = "BUY"
        elif rsi < 35:
            raw = 0.70; signal = "BUY"
        elif rsi < 40:
            raw = 0.55; signal = "WATCH"
        elif rsi < 50:
            raw = 0.45; signal = "NEUTRAL"
        elif rsi < 60:
            raw = 0.35; signal = "NEUTRAL"
        elif rsi < 70:
            raw = 0.20; signal = "WEAK"
        else:
            raw = 0.08; signal = "SELL"

        return FactorResult(
            factor_name=self.name,
            raw_score=round(rsi, 1),
            normalized_score=raw,
            confidence=0.85,
            signal=signal,
            detail={"rsi": round(rsi, 1), "zone": "超卖" if rsi < 35 else ("超买" if rsi > 70 else "中性")}
        )
