"""
因子2: 封板质量
- 分析涨停板的封板质量，预判次日溢价概率
- 核心指标：封板时间、封单量、开板次数、尾盘是否炸板

信号逻辑：
- 早盘封板(10:30前) + 封单量>流通市值3% + 零开板 → 强看多（次日高溢价）
- 尾盘封板(14:30后) + 封单不足 → 弱看多（次日大概率低开）
- 盘中炸板 ≥ 2次 → 看空（筹码松动）
"""

from . import BaseFactor, FactorResult
from liquidity_filter import MarketSnapshot


class SealQualityFactor(BaseFactor):
    name = "seal_quality"
    description = "封板质量 — 评估涨停板次日溢价概率"
    category = "量价"

    def calculate(self, snapshot: MarketSnapshot) -> FactorResult:
        bars = snapshot.daily_bars
        minute_bars = snapshot.minute_bars

        if not bars or len(bars) < 5:
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.1,
                                detail={"error": "数据不足"})

        latest = bars[-1]
        prev = bars[-2] if len(bars) >= 2 else latest

        close = latest.get("close", 0)
        high = latest.get("high", 0)
        low = latest.get("low", 0)
        prev_close = prev.get("close", 0)

        daily_return = (close - prev_close) / max(prev_close, 0.01)
        is_limit_up = daily_return >= 0.098  # 10cm涨停

        detail = {
            "daily_return": round(daily_return * 100, 2),
            "is_limit_up": is_limit_up,
        }

        if not is_limit_up:
            # 非涨停，直接中性
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.5,
                                detail={**detail, "reason": "非涨停板，不适用封板质量因子"})

        # ----- 涨停板分析 -----

        # 1. 封板时间估计（通过分钟线K线形态）
        seal_time_score = self._estimate_seal_time(minute_bars, high, prev_close)

        # 2. 封单量评估（通过涨停时的成交量推断）
        seal_order_score = self._evaluate_seal_volume(latest, bars)

        # 3. 开板检测
        open_count = self._count_openings(minute_bars, prev_close)

        # 4. 尾盘炸板风险
        tail_break_risk = self._check_tail_break_risk(minute_bars, close, prev_close)

        detail.update({
            "seal_time_score": round(seal_time_score, 3),
            "seal_order_score": round(seal_order_score, 3),
            "open_count": open_count,
            "tail_break_risk": tail_break_risk,
        })

        # ----- 综合得分 -----
        # 权重：封板时间30%，封单量30%，开板次数25%，尾盘风险15%
        raw = (
            seal_time_score * 0.30 +
            seal_order_score * 0.30 +
            max(0, 1.0 - open_count * 0.25) * 0.25 +
            (1.0 - tail_break_risk) * 0.15
        )

        raw_score = self.normalize_score(raw)

        # 信号判定
        if raw_score >= 0.75:
            signal = 1
        elif raw_score <= 0.35:
            signal = -1
        else:
            signal = 0

        confidence = self.compute_confidence(
            data_quality=0.7 if minute_bars and len(minute_bars) > 50 else 0.3,
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

    def _estimate_seal_time(self, minute_bars: list, day_high: float, prev_close: float) -> float:
        """估计封板时间，返回0-1得分（越早越高）"""
        if not minute_bars:
            return 0.5

        limit_price = prev_close * 1.10
        # 找到第一根接近涨停价的K线
        for i, bar in enumerate(minute_bars):
            if bar.get("close", 0) >= limit_price * 0.995:  # 涨9.5%以上
                # 按位置给分：越早越高
                position = i / max(len(minute_bars), 1)
                if position < 0.15:  # 开盘30分钟内
                    return 0.95
                elif position < 0.33:  # 上午
                    return 0.80
                elif position < 0.55:  # 午后早段
                    return 0.60
                elif position < 0.75:  # 午后
                    return 0.40
                else:  # 尾盘
                    return 0.25

        return 0.5  # 没找到明确封板时间

    def _evaluate_seal_volume(self, latest: dict, bars: list) -> float:
        """评估封单量（通过涨停日换手率推断）"""
        volume = latest.get("volume", 0)
        if len(bars) >= 5:
            avg_vol = sum(b.get("volume", 0) for b in bars[-5:-1]) / max(len(bars[-5:-1]), 1)
        else:
            avg_vol = volume

        vol_ratio = volume / max(avg_vol, 1)

        # 缩量涨停 → 封单意愿强
        if vol_ratio < 0.5:
            return 0.90
        elif vol_ratio < 0.8:
            return 0.75
        elif vol_ratio < 1.2:
            return 0.55
        elif vol_ratio < 2.0:
            return 0.35
        else:
            return 0.15

    def _count_openings(self, minute_bars: list, prev_close: float) -> int:
        """检测盘中开板次数"""
        if not minute_bars:
            return 0

        limit_price = prev_close * 1.10
        openings = 0
        was_sealed = False

        for bar in minute_bars:
            close = bar.get("close", 0)
            is_at_limit = close >= limit_price * 0.995

            if was_sealed and not is_at_limit:
                openings += 1
            was_sealed = is_at_limit or (was_sealed and not is_at_limit and close >= limit_price * 0.95)

        return openings

    def _check_tail_break_risk(self, minute_bars: list, close: float, prev_close: float) -> float:
        """检测尾盘炸板风险 (0=安全, 1=高风险)"""
        if not minute_bars or len(minute_bars) < 6:
            return 0.5

        limit_price = prev_close * 1.10
        tail = minute_bars[-6:]  # 最后30分钟
        tail_opens_below = sum(1 for b in tail if b.get("close", 0) < limit_price * 0.98)
        risk = tail_opens_below / len(tail)
        return risk
