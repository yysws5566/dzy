"""
因子10: 融资情绪
- 监测融资余额变化，捕捉杠杆资金情绪
- 融资余额连续增加 + 股价上涨 → 趋势确认（杠杆看多）
- 融资余额骤降 + 股价稳定 → 洗盘信号（杠杆资金被清洗后利于拉升）
- 融资余额暴增 + 股价滞涨 → 警惕（杠杆拥挤，易踩踏）

数据来源: TickFlow API v1/margin/*
"""

from . import BaseFactor, FactorResult
from liquidity_filter import MarketSnapshot


class MarginSentimentFactor(BaseFactor):
    name = "margin_sentiment"
    description = "融资情绪 — 监测融资余额变化捕捉杠杆情绪"
    category = "资金"

    def calculate(self, snapshot: MarketSnapshot) -> FactorResult:
        bars = snapshot.daily_bars
        margin_data = snapshot.__dict__.get("_margin_data", {})

        if not bars or len(bars) < 10:
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.1,
                                detail={"error": "数据不足"})

        has_margin = bool(margin_data and margin_data.get("margin_balance"))

        detail = {"has_margin_data": has_margin}

        if has_margin:
            raw_score, detail = self._calc_real(margin_data, bars, detail)
        else:
            raw_score, detail = self._calc_proxy(bars, detail)

        raw_score = self.normalize_score(raw_score)
        signal = 1 if raw_score >= 0.65 else (-1 if raw_score <= 0.35 else 0)
        confidence = self.compute_confidence(
            data_quality=0.90 if has_margin else 0.40,
            signal_strength=abs(raw_score - 0.5) * 2,
        )

        return FactorResult(
            factor_name=self.name, raw_score=raw_score, normalized_score=raw_score,
            signal=signal, confidence=confidence, detail=detail,
        )

    def _calc_real(self, margin: dict, bars: list, detail: dict) -> tuple:
        """基于真实融资数据"""
        balance = margin.get("margin_balance", 0)            # 融资余额
        balance_change_5d = margin.get("balance_change_5d", 0)  # 5日变化
        balance_change_pct = margin.get("balance_change_pct_5d", 0)  # 5日变化率
        buy_amount = margin.get("margin_buy", 0)             # 融资买入额
        repay_amount = margin.get("margin_repay", 0)         # 偿还额

        closes = [b.get("close", 0) for b in bars]
        price_change_5d = (closes[-1] - closes[-6]) / max(closes[-6], 0.01) if len(closes) >= 6 else 0

        detail.update({
            "balance_change_pct_5d": round(balance_change_pct * 100, 2),
            "price_change_5d": round(price_change_5d * 100, 2),
        })

        # 分析
        score = 0.5

        if balance_change_pct > 0.03 and price_change_5d > 0.01:
            # 融资增加 + 股价上涨 → 趋势确认
            score = 0.65
            detail["pattern"] = "杠杆资金加仓追涨"
        elif balance_change_pct < -0.03 and abs(price_change_5d) < 0.02:
            # 融资减少 + 股价稳定 → 洗盘
            score = 0.60
            detail["pattern"] = "杠杆清洗（洗盘信号）"
        elif balance_change_pct > 0.05 and price_change_5d < 0:
            # 融资暴增 + 股价跌 → 杠杆接飞刀
            score = 0.25
            detail["pattern"] = "杠杆资金逆势加仓（风险）"
        elif balance_change_pct > 0.08:
            # 融资暴增 → 杠杆拥挤
            score = 0.35
            detail["pattern"] = "融资拥挤度偏高"
        elif balance_change_pct < -0.05 and price_change_5d < -0.03:
            # 融资大幅减少 + 股价大跌 → 多杀多
            score = 0.20
            detail["pattern"] = "杠杆踩踏"
        elif balance_change_pct > 0 and price_change_5d > 0:
            score = 0.55
            detail["pattern"] = "杠杆温和看多"

        return score, detail

    def _calc_proxy(self, bars: list, detail: dict) -> tuple:
        """代理计算（无融资数据时使用量价推断杠杆情绪）"""
        closes = [b.get("close", 0) for b in bars]
        volumes = [b.get("volume", 0) for b in bars]

        price_5d_change = (closes[-1] - closes[-6]) / max(closes[-6], 0.01) if len(closes) >= 6 else 0

        # 量能趋势（杠杆资金往往放大波动和量能）
        vol_5d = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0
        vol_prev_5d = sum(volumes[-10:-5]) / 5 if len(volumes) >= 10 else vol_5d
        vol_change = (vol_5d - vol_prev_5d) / max(vol_prev_5d, 1)

        # 日内波幅（杠杆放大波动）
        ranges = [(b.get("high", 0) - b.get("low", 0)) / max(b.get("close", 0), 0.01) for b in bars[-5:]]
        avg_range = sum(ranges) / len(ranges) if ranges else 0

        detail.update({
            "vol_change": round(vol_change * 100, 2),
            "avg_daily_range_pct": round(avg_range * 100, 2),
            "method": "proxy（量价波动推断）",
        })

        score = 0.5
        if vol_change > 0.3 and price_5d_change > 0.02:
            score = 0.62
            detail["proxy_pattern"] = "放量上涨（杠杆情绪偏多）"
        elif vol_change > 0.3 and price_5d_change < -0.02:
            score = 0.35
            detail["proxy_pattern"] = "放量下跌（杠杆情绪偏空）"
        elif avg_range > 0.05 and price_5d_change > 0:
            score = 0.56
            detail["proxy_pattern"] = "高波幅上涨（杠杆活跃）"
        elif vol_change < -0.3 and abs(price_5d_change) < 0.01:
            score = 0.52
            detail["proxy_pattern"] = "缩量企稳（杠杆退潮）"

        return score, detail
