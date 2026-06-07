"""
因子15: 主力资金流向因子
========================
基于尾盘分时量价数据，推算当日主力资金流向。
资金流入/流出比率 + 大单活跃度 → 判断主力建仓/出货意图。
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "strategies"))
from enhanced_factors import calc_money_flow

from dataclasses import dataclass
from typing import Any, Dict
import numpy as np


@dataclass
class FactorResult:
    factor_name: str = "money_flow"
    raw_score: float = 0.0
    normalized_score: float = 0.0
    confidence: float = 0.5
    signal: str = "NEUTRAL"
    detail: Dict[str, Any] = None

    def __post_init__(self):
        if self.detail is None:
            self.detail = {}


class MoneyFlowFactor:
    """主力资金流向因子（基于分时量价推算）"""

    def __init__(self):
        self.name = "money_flow"

    def compute_from_intraday(self, intra_opens, intra_closes, intra_volumes,
                               intra_highs=None, intra_lows=None) -> FactorResult:
        if len(intra_closes) < 6:
            return FactorResult(detail={"error": "分时数据不足"})

        result = calc_money_flow(intra_opens, intra_closes, intra_volumes,
                                 intra_highs, intra_lows)

        score = result["score"]

        if score >= 80:
            raw = 0.90; signal = "STRONG_BUY"
        elif score >= 65:
            raw = 0.75; signal = "BUY"
        elif score >= 50:
            raw = 0.50; signal = "NEUTRAL"
        elif score >= 35:
            raw = 0.30; signal = "WEAK"
        else:
            raw = 0.10; signal = "SELL"

        return FactorResult(
            factor_name=self.name,
            raw_score=score,
            normalized_score=raw,
            confidence=0.75,
            signal=signal,
            detail=result
        )
