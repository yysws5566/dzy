"""
因子12: 外盘联动
- 监测外盘（美股、港股、A50期货）对A股的联动影响
- 外盘大涨 + 个股滞涨 → 次日补涨预期（T+1看多）
- 外盘大跌 + 个股抗跌 → 次日可能补跌（T+1看空）
- 外盘与个股同向 → 趋势延续

核心指数: 标普500、纳斯达克、恒生指数、A50期货
"""

from . import BaseFactor, FactorResult
from liquidity_filter import MarketSnapshot


class GlobalLinkageFactor(BaseFactor):
    name = "global_linkage"
    description = "外盘联动 — 监测外盘对A股的隔夜影响"
    category = "行为"

    # 关键外盘指数映射
    GLOBAL_INDICES = {
        "SPX": "标普500",
        "NDX": "纳斯达克100",
        "HSI": "恒生指数",
        "A50": "富时A50期货",
        "DJI": "道琼斯工业",
    }

    def calculate(self, snapshot: MarketSnapshot) -> FactorResult:
        bars = snapshot.daily_bars
        global_data = snapshot.__dict__.get("_global_data", {})

        if not bars or len(bars) < 5:
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.1,
                                detail={"error": "数据不足"})

        has_global = bool(global_data and global_data.get("indices"))
        detail = {"has_global_data": has_global}

        if has_global:
            raw_score, detail = self._calc_real(global_data, bars, detail)
        else:
            raw_score, detail = self._calc_proxy(bars, detail)

        raw_score = self.normalize_score(raw_score)
        signal = 1 if raw_score >= 0.62 else (-1 if raw_score <= 0.38 else 0)
        confidence = self.compute_confidence(
            data_quality=0.85 if has_global else 0.40,
            signal_strength=abs(raw_score - 0.5) * 2,
        )

        return FactorResult(
            factor_name=self.name, raw_score=raw_score, normalized_score=raw_score,
            signal=signal, confidence=confidence, detail=detail,
        )

    def _calc_real(self, global_data: dict, bars: list, detail: dict) -> tuple:
        """基于真实外盘数据"""
        indices = global_data.get("indices", {})
        a50_change = indices.get("A50", {}).get("change_pct", 0)
        spx_change = indices.get("SPX", {}).get("change_pct", 0)
        hsi_change = indices.get("HSI", {}).get("change_pct", 0)

        closes = [b.get("close", 0) for b in bars]
        stock_change = (closes[-1] - closes[-2]) / max(closes[-2], 0.01) if len(closes) >= 2 else 0

        # 加权外盘涨跌（A50权重最高，因为直接挂钩A股）
        global_weighted = a50_change * 0.50 + spx_change * 0.25 + hsi_change * 0.25

        detail.update({
            "A50_change_pct": round(a50_change * 100, 2),
            "SPX_change_pct": round(spx_change * 100, 2),
            "HSI_change_pct": round(hsi_change * 100, 2),
            "global_weighted_pct": round(global_weighted * 100, 3),
            "stock_change_pct": round(stock_change * 100, 2),
        })

        score = 0.5

        # 外盘与个股的联动分析
        if global_weighted > 0.01 and stock_change < 0:
            # 外盘涨但个股跌 → T+1补涨预期
            divergence = min(1.0, global_weighted * 50)
            score = 0.55 + divergence * 0.25
            detail["pattern"] = "外盘强势，个股滞涨（T+1补涨预期）"
        elif global_weighted < -0.01 and stock_change > 0:
            # 外盘跌但个股涨 → T+1可能补跌
            divergence = min(1.0, abs(global_weighted) * 40)
            score = 0.50 - divergence * 0.20
            detail["pattern"] = "外盘弱势，个股抗跌（T+1补跌风险）"
        elif global_weighted > 0.01 and stock_change > 0:
            # 同向上涨 → 趋势确认
            score = 0.58
            detail["pattern"] = "内外共振上涨"
        elif global_weighted < -0.01 and stock_change < 0:
            # 同向下跌
            score = 0.40
            detail["pattern"] = "内外共振下跌"
        else:
            score = 0.50
            detail["pattern"] = "外盘平稳"

        return score, detail

    def _calc_proxy(self, bars: list, detail: dict) -> tuple:
        """代理计算（无外盘数据时使用A股大盘作为代理）"""
        closes = [b.get("close", 0) for b in bars]
        stock_change = (closes[-1] - closes[-2]) / max(closes[-2], 0.01) if len(closes) >= 2 else 0

        # 使用价格行为推断（隔夜跳空是外盘影响的主要表现形式）
        today = bars[-1]
        yesterday = bars[-2] if len(bars) >= 2 else today
        open_price = today.get("open", 0)
        prev_close = yesterday.get("close", 0)
        overnight_gap = (open_price - prev_close) / max(prev_close, 0.01)

        # 当日走势 vs 隔夜跳空（跳空后的日内走势反映A股对外盘的反应消化）
        close = today.get("close", 0)
        intraday_change = (close - open_price) / max(open_price, 0.01)

        detail.update({
            "overnight_gap_pct": round(overnight_gap * 100, 2),
            "intraday_change_pct": round(intraday_change * 100, 2),
            "method": "proxy（隔夜跳空推断外盘影响）",
        })

        score = 0.5

        # 大幅高开 + 日内回落 → 外盘利好被消化（偏空）
        if overnight_gap > 0.01 and intraday_change < -0.01:
            score = 0.38
            detail["proxy_pattern"] = "高开低走（外盘利好已消化）"
        # 大幅低开 + 日内回升 → 外盘利空被消化（偏多）
        elif overnight_gap < -0.01 and intraday_change > 0.01:
            score = 0.62
            detail["proxy_pattern"] = "低开高走（外盘利空已消化）"
        # 高开 + 日内继续走高 → 外盘利好延续
        elif overnight_gap > 0.005 and intraday_change > 0.005:
            score = 0.58
            detail["proxy_pattern"] = "高开高走（外盘利好延续）"
        # 低开 + 日内继续走低 → 外盘利空发酵
        elif overnight_gap < -0.005 and intraday_change < -0.005:
            score = 0.35
            detail["proxy_pattern"] = "低开低走（外盘利空发酵）"
        # 微小跳空（外盘影响小）
        elif abs(overnight_gap) < 0.003:
            score = 0.50
            detail["proxy_pattern"] = "隔夜平稳（外盘影响小）"

        return score, detail
