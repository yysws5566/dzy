"""
因子3: 缺口博弈
- 识别向上/向下跳空缺口，评估次日回补概率
- 突破缺口（放量跳空向上）→ 看多（趋势加速）
- 衰竭缺口（高位缩量跳空）→ 看空（动能耗尽）
- 普通缺口 → 中性偏回补

缺口类型分类：
1. 突破缺口：低位放量突破关键均线 → 强看多
2. 持续缺口：趋势中途放量跳空 → 偏看多
3. 衰竭缺口：高位缩量跳空 → 看空
4. 普通缺口：无特殊量价配合 → 中性
"""

from . import BaseFactor, FactorResult
from liquidity_filter import MarketSnapshot


class GapGambitFactor(BaseFactor):
    name = "gap_gambit"
    description = "缺口博弈 — 识别跳空缺口类型与回补概率"
    category = "量价"

    def calculate(self, snapshot: MarketSnapshot) -> FactorResult:
        bars = snapshot.daily_bars

        if not bars or len(bars) < 30:
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.1,
                                detail={"error": "数据不足（需要30日线）"})

        latest = bars[-1]
        prev = bars[-2]

        open_price = latest.get("open", 0)
        prev_close = prev.get("close", 0)
        prev_high = prev.get("high", 0)
        prev_low = prev.get("low", 0)

        detail = {}

        # ----- 检测缺口 -----
        gap_up = open_price > prev_high  # 向上跳空
        gap_down = open_price < prev_low  # 向下跳空

        gap_pct = 0
        if gap_up:
            gap_pct = (open_price - prev_high) / prev_high
        elif gap_down:
            gap_pct = (prev_low - open_price) / prev_low

        detail["gap_type"] = "up" if gap_up else ("down" if gap_down else "none")
        detail["gap_pct"] = round(gap_pct * 100, 3)

        if not gap_up and not gap_down:
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.3,
                                detail={**detail, "reason": "无跳空缺口"})

        # ----- 缺口分析 -----
        close = latest.get("close", 0)
        volume = latest.get("volume", 0)
        avg_vol_20 = snapshot.get_volume_ma(20)
        vol_ratio = volume / max(avg_vol_20, 1)
        ma_20 = snapshot.get_ma(20)
        ma_60 = snapshot.get_ma(60)
        daily_return = (close - open_price) / max(open_price, 0.01)

        # 判断当前位置（相对均线）
        above_ma20 = close > ma_20
        above_ma60 = close > ma_60
        # 近20日涨幅（判断高低位）
        if len(bars) >= 20:
            price_20d_ago = bars[-21].get("close", bars[-20].get("close", close))
            ret_20d = (close - price_20d_ago) / max(price_20d_ago, 0.01)
        else:
            ret_20d = 0

        is_high_position = ret_20d > 0.30  # 20日涨超30%视为高位
        is_low_position = ret_20d < -0.15   # 20日跌超15%视为低位

        detail.update({
            "vol_ratio": round(vol_ratio, 2),
            "ret_20d": round(ret_20d * 100, 1),
            "position": "high" if is_high_position else ("low" if is_low_position else "mid"),
        })

        # ----- 分类打分 -----
        if gap_up:
            # 向上跳空
            if is_low_position and vol_ratio > 1.5:
                # 低位放量突破 → 突破缺口，强看多
                raw_score = 0.85
                detail["gap_category"] = "突破缺口"
            elif vol_ratio > 1.2 and not is_high_position:
                # 趋势中途放量 → 持续缺口
                raw_score = 0.70
                detail["gap_category"] = "持续缺口"
            elif is_high_position and vol_ratio < 0.8:
                # 高位缩量跳空 → 衰竭缺口
                raw_score = 0.25
                detail["gap_category"] = "衰竭缺口"
            else:
                # 普通缺口
                raw_score = 0.55
                detail["gap_category"] = "普通向上缺口"
        else:
            # 向下跳空
            if is_high_position and vol_ratio > 1.5:
                # 高位放量跳空向下 → 强看空
                raw_score = 0.10
                detail["gap_category"] = "高位出逃缺口"
            elif is_low_position and vol_ratio < 0.6:
                # 低位缩量跳空 → 衰竭缺口，看反转
                raw_score = 0.60
                detail["gap_category"] = "低位衰竭缺口（潜在反转）"
            else:
                raw_score = 0.30
                detail["gap_category"] = "普通向下缺口"

        raw_score = self.normalize_score(raw_score)

        # 信号
        if raw_score >= 0.65:
            signal = 1
        elif raw_score <= 0.35:
            signal = -1
        else:
            signal = 0

        confidence = self.compute_confidence(
            data_quality=0.9 if len(bars) >= 60 else 0.6,
            signal_strength=abs(raw_score - 0.5) * 2,
        )

        return FactorResult(
            factor_name=self.name,
            raw_score=raw_score,
            normalized_score=raw_score,
            signal=signal,
            confidence=confidence,
            detail=detail,
        )
