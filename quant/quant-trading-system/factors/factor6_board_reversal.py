"""
因子6: 断板反包
- 识别连板股断板后的反包形态
- 核心：昨日涨停失败（触板回落/炸板），今日是否反包成功

信号逻辑：
- 昨日触板回落 + 今日放量反包昨日高点 → 强看多（弱转强）
- 昨日炸板 + 今日缩量企稳 → 轻度看多（筹码沉淀）
- 昨日炸板 + 今日继续放量下跌 → 看空
"""

from . import BaseFactor, FactorResult
from liquidity_filter import MarketSnapshot


class BoardReversalFactor(BaseFactor):
    name = "board_reversal"
    description = "断板反包 — 识别涨停断板后的反包机会"
    category = "行为"

    def calculate(self, snapshot: MarketSnapshot) -> FactorResult:
        bars = snapshot.daily_bars

        if not bars or len(bars) < 5:
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.1,
                                detail={"error": "数据不足"})

        today = bars[-1]
        yesterday = bars[-2]

        t_close = today.get("close", 0)
        t_open = today.get("open", 0)
        t_high = today.get("high", 0)
        t_low = today.get("low", 0)
        t_vol = today.get("volume", 0)

        y_close = yesterday.get("close", 0)
        y_open = yesterday.get("open", 0)
        y_high = yesterday.get("high", 0)
        y_low = yesterday.get("low", 0)
        y_vol = yesterday.get("volume", 0)
        y_prev_close = bars[-3].get("close", 0) if len(bars) >= 3 else y_open

        # 昨日涨幅
        y_return = (y_close - y_prev_close) / max(y_prev_close, 0.01)
        # 昨日上影线（触板回落特征）
        y_upper_shadow = (y_high - y_close) / max(y_close, 0.01)
        # 今日涨幅
        t_return = (t_close - t_open) / max(t_open, 0.01)

        avg_vol_5 = snapshot.get_volume_ma(5)
        t_vol_ratio = t_vol / max(avg_vol_5, 1)
        y_vol_ratio = y_vol / max(avg_vol_5, 1)

        detail = {
            "yesterday_return": round(y_return * 100, 2),
            "yesterday_upper_shadow": round(y_upper_shadow * 100, 2),
            "today_return": round(t_return * 100, 2),
            "today_vol_ratio": round(t_vol_ratio, 2),
        }

        # 昨日是否"断板"（冲高回落）
        was_board_broken = (y_return > 0.07 and y_upper_shadow > 0.03)  # 涨7%+但上影线>3%
        was_seal_failed = (y_high >= y_prev_close * 1.095 and y_close < y_prev_close * 1.05)  # 触涨停但没收住

        if not was_board_broken and not was_seal_failed:
            detail["pattern"] = "昨日未断板"
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.3,
                                detail=detail)

        # ----- 断板反包分析 -----
        if t_close > y_high:
            # 今日收盘突破昨日高点 → 强反包
            if t_vol_ratio > 1.2:
                raw_score = 0.88
                detail["pattern"] = "放量强反包（突破昨日高点）"
            else:
                raw_score = 0.70
                detail["pattern"] = "缩量反包（突破但量能不足）"
        elif t_close > y_close and t_return > 0.02:
            # 收复昨日收盘价 但未创新高
            raw_score = 0.62
            detail["pattern"] = "弱反包（收复失地但未创新高）"
        elif t_low > y_low and t_close >= y_close * 0.98:
            # 企稳不跌
            raw_score = 0.52
            detail["pattern"] = "断板后企稳"
        elif t_close < y_low:
            # 继续下跌破昨日低点
            raw_score = 0.15
            detail["pattern"] = "断板后加速下跌"
        else:
            raw_score = 0.40
            detail["pattern"] = "断板后弱势震荡"

        raw_score = self.normalize_score(raw_score)

        signal = 1 if raw_score >= 0.65 else (-1 if raw_score <= 0.35 else 0)
        confidence = self.compute_confidence(
            data_quality=0.8,
            signal_strength=abs(raw_score - 0.5) * 2,
        )

        return FactorResult(
            factor_name=self.name, raw_score=raw_score, normalized_score=raw_score,
            signal=signal, confidence=confidence, detail=detail,
        )
