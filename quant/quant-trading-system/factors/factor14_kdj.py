"""
因子14: KDJ 低位金叉因子
========================
基于 9 日 KDJ 指标，识别超卖区金叉信号。
K<25 且 K 上穿 D → 短线买入信号。
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "strategies"))
from enhanced_factors import calc_kdj

from dataclasses import dataclass
from typing import Any, Dict
import numpy as np


@dataclass
class FactorResult:
    factor_name: str = "kdj_golden_cross"
    raw_score: float = 0.0
    normalized_score: float = 0.0
    confidence: float = 0.5
    signal: str = "NEUTRAL"
    detail: Dict[str, Any] = None

    def __post_init__(self):
        if self.detail is None:
            self.detail = {}


class KdjGoldenCrossFactor:
    """KDJ 低位金叉因子"""

    def __init__(self):
        self.name = "kdj_golden_cross"

    def compute(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> FactorResult:
        if len(closes) < 10:
            return FactorResult(detail={"error": "数据不足"})

        k, d, j = calc_kdj(highs, lows, closes)

        golden = k > d and k < 40

        if k < 20 and j < 0:
            raw = 0.95; signal = "STRONG_BUY"
        elif k < 25 and golden:
            raw = 0.85; signal = "BUY"
        elif k < 35 and golden:
            raw = 0.70; signal = "BUY"
        elif golden:
            raw = 0.55; signal = "WATCH"
        elif k > 80:
            raw = 0.10; signal = "SELL"
        else:
            raw = 0.40; signal = "NEUTRAL"

        return FactorResult(
            factor_name=self.name,
            raw_score=raw,
            normalized_score=raw,
            confidence=0.80,
            signal=signal,
            detail={"K": round(k,1), "D": round(d,1), "J": round(j,1),
                    "golden_cross": golden}
        )
