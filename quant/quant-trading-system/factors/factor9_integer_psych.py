"""
因子9: 整数关口
- 利用投资者心理关口（整数价位、历史高/低点、均线位置）
- 突破整数关口 + 放量确认 → 看多
- 接近整数关口受阻 → 看空（心理阻力）

信号逻辑：
- 收盘价刚突破关键整数位（如10, 20, 50, 100元）+ 放量 → 强看多
- 涨停价恰好停在整数关口下方 → 看空（次日大概率回调）
- 跌破整数关口 + 放量 → 看空
"""

import math

from . import BaseFactor, FactorResult
from liquidity_filter import MarketSnapshot


class IntegerPsychFactor(BaseFactor):
    name = "integer_psych"
    description = "整数关口 — 分析关键心理价位博弈"
    category = "量价"

    # 关键整数价位列表
    KEY_LEVELS = [5, 8, 10, 12, 15, 18, 20, 25, 30, 40, 50, 60, 80, 100, 150, 200, 300, 500]

    def calculate(self, snapshot: MarketSnapshot) -> FactorResult:
        bars = snapshot.daily_bars

        if not bars or len(bars) < 5:
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.1,
                                detail={"error": "数据不足"})

        latest = bars[-1]
        close = latest.get("close", 0)
        high = latest.get("high", 0)
        low = latest.get("low", 0)
        volume = latest.get("volume", 0)
        prev_close = bars[-2].get("close", 0) if len(bars) >= 2 else close

        daily_return = (close - prev_close) / max(prev_close, 0.01)

        # 找到最近的关键整数关口
        nearest_above = None
        nearest_below = None

        for level in self.KEY_LEVELS:
            if level > close and (nearest_above is None or level < nearest_above):
                nearest_above = level
            if level < close and (nearest_below is None or level > nearest_below):
                nearest_below = level

        # 历史高低点（近60日）
        highs = [b.get("high", 0) for b in bars[-60:]]
        lows = [b.get("low", 0) for b in bars[-60:]]
        high_60 = max(highs) if highs else close
        low_60 = min(lows) if lows else close

        # 均线位置
        ma_5 = snapshot.get_ma(5)
        ma_20 = snapshot.get_ma(20)
        ma_60 = snapshot.get_ma(60)

        # 量比
        avg_vol_5 = snapshot.get_volume_ma(5)
        vol_ratio = volume / max(avg_vol_5, 1)

        detail = {
            "nearest_above": nearest_above,
            "nearest_below": nearest_below,
            "dist_to_above_pct": round((nearest_above - close) / close * 100, 2) if nearest_above else None,
            "high_60": round(high_60, 2),
            "low_60": round(low_60, 2),
        }

        # ----- 整数关口分析 -----
        score = 0.5
        patterns = []

        # 1. 突破整数关口
        if nearest_below and prev_close <= nearest_below < close:
            # 今日收盘突破整数位
            if vol_ratio > 1.3:
                score += 0.20
                patterns.append(f"放量突破{nearest_below}元关口")
            else:
                score += 0.10
                patterns.append(f"突破{nearest_below}元关口（量能不足）")

        # 2. 接近整数关口上方 → 支撑确认
        if nearest_below and 0 < (close - nearest_below) / nearest_below < 0.02:
            if daily_return > 0:
                score += 0.05
                patterns.append(f"站稳{nearest_below}元关口上方")

        # 3. 接近整数关口下方 → 受阻
        if nearest_above and 0 < (nearest_above - close) / close < 0.03:
            if high >= nearest_above * 0.99:
                # 触及但未站上 → 受阻
                score -= 0.12
                patterns.append(f"触及{nearest_above}元关口受阻")
            else:
                score -= 0.05
                patterns.append(f"接近{nearest_above}元关口")

        # 4. 跌破整数关口
        if nearest_above and prev_close >= nearest_above > close:
            score -= 0.15
            patterns.append(f"跌破{nearest_above}元关口")
            if vol_ratio > 1.3:
                score -= 0.08
                patterns.append("放量跌破（看空加强）")

        # 5. 接近60日高点/低点
        if close >= high_60 * 0.98:
            if vol_ratio > 1.5 and daily_return > 0.02:
                score += 0.12
                patterns.append("放量接近60日高点（突破预期）")
            else:
                score -= 0.03
                patterns.append("接近60日高点（关注突破）")

        if close <= low_60 * 1.03:
            score -= 0.08
            patterns.append("接近60日低点（支撑考验）")

        # 6. 均线位置
        if close > ma_20 and prev_close <= ma_20:
            score += 0.06
            patterns.append("站上20日均线")

        detail["patterns"] = patterns
        raw_score = self.normalize_score(score)

        signal = 1 if raw_score >= 0.62 else (-1 if raw_score <= 0.38 else 0)
        confidence = self.compute_confidence(
            data_quality=0.85,
            signal_strength=abs(raw_score - 0.5) * 2,
        )

        return FactorResult(
            factor_name=self.name, raw_score=raw_score, normalized_score=raw_score,
            signal=signal, confidence=confidence, detail=detail,
        )
