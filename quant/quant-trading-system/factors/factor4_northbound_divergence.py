"""
因子4: 北向资金背离
- 监测北向资金（沪股通/深股通）流向与股价走势的背离
- 股价下跌但北向净买入 → 看多（聪明钱抄底）
- 股价上涨但北向净卖出 → 看空（聪明钱出货）
- 股价与北向同向 → 趋势确认

数据来源: TickFlow API v1/northbound/*
"""

from . import BaseFactor, FactorResult
from liquidity_filter import MarketSnapshot


class NorthboundDivergenceFactor(BaseFactor):
    name = "northbound_divergence"
    description = "北向资金背离 — 监测北向资金与股价的背离信号"
    category = "资金"

    def calculate(self, snapshot: MarketSnapshot) -> FactorResult:
        bars = snapshot.daily_bars
        northbound_data = snapshot.__dict__.get("_northbound_data", {})

        if not bars or len(bars) < 10:
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.1,
                                detail={"error": "数据不足"})

        # ----- 如果没有北向数据，使用替代指标估算 -----
        # 北向资金通常青睐大市值、ROE高的标的
        # 通过日内资金流向推断（大单净买入作为代理变量）
        has_real_data = bool(northbound_data and northbound_data.get("flow_data"))

        detail = {"has_northbound_data": has_real_data}

        if has_real_data:
            raw_score, detail = self._calc_from_tickflow(snapshot, northbound_data, detail)
        else:
            raw_score, detail = self._calc_from_proxy(snapshot, detail)

        raw_score = self.normalize_score(raw_score)

        if raw_score >= 0.65:
            signal = 1
        elif raw_score <= 0.35:
            signal = -1
        else:
            signal = 0

        confidence = self.compute_confidence(
            data_quality=0.85 if has_real_data else 0.45,
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

    def _calc_from_tickflow(self, snapshot, data: dict, detail: dict) -> tuple:
        """基于TickFlow真实北向数据计算"""
        flow_data = data.get("flow_data", {})
        # 近5日北向净买入
        net_buy_5d = flow_data.get("net_buy_5d", 0)
        # 近20日北向净买入
        net_buy_20d = flow_data.get("net_buy_20d", 0)
        # 北向持仓占比变化
        holding_change = flow_data.get("holding_change_pct", 0)

        # 股价近5日涨跌
        bars = snapshot.daily_bars
        if len(bars) >= 6:
            close_now = bars[-1].get("close", 0)
            close_5d = bars[-6].get("close", 0)
            price_change_5d = (close_now - close_5d) / max(close_5d, 0.01)
        else:
            price_change_5d = 0

        detail.update({
            "net_buy_5d": net_buy_5d,
            "net_buy_20d": net_buy_20d,
            "holding_change": round(holding_change * 100, 3),
            "price_change_5d": round(price_change_5d * 100, 2),
        })

        # 背离检测
        if price_change_5d < -0.02 and net_buy_5d > 0:
            # 股价跌但北向买 → 背离看多
            strength = min(1.0, abs(price_change_5d) * 10 + net_buy_5d / 1e8)
            return 0.5 + strength * 0.4, {**detail, "pattern": "北向抄底背离"}
        elif price_change_5d > 0.03 and net_buy_5d < 0:
            # 股价涨但北向卖 → 背离看空
            strength = min(1.0, price_change_5d * 8 + abs(net_buy_5d) / 1e8)
            return 0.5 - strength * 0.35, {**detail, "pattern": "北向出货背离"}
        elif net_buy_5d > 0 and price_change_5d > 0:
            # 同向看多，但力度弱于背离
            return 0.55 + min(0.15, net_buy_5d / 5e8), {**detail, "pattern": "北向与股价同向看多"}
        elif net_buy_5d < 0 and price_change_5d < 0:
            return 0.40, {**detail, "pattern": "北向与股价同向看空"}
        else:
            return 0.50, {**detail, "pattern": "北向无明确信号"}

    def _calc_from_proxy(self, snapshot, detail: dict) -> tuple:
        """
        代理计算（无真实北向数据时）
        通过大单净流入、尾盘量价关系等推断
        """
        bars = snapshot.daily_bars
        if len(bars) < 10:
            return 0.5, detail

        # 近5日价格变化
        closes = [b.get("close", 0) for b in bars[-10:]]
        price_5d_change = (closes[-1] - closes[-6]) / max(closes[-6], 0.01) if len(closes) >= 6 else 0
        price_10d_change = (closes[-1] - closes[0]) / max(closes[0], 0.01)

        # 量能趋势（放量上涨=资金流入，放量下跌=资金流出）
        volumes = [b.get("volume", 0) for b in bars[-5:]]
        avg_vol_5d = sum(volumes) / max(len(volumes), 1)
        avg_vol_prev = sum(b.get("volume", 0) for b in bars[-10:-5]) / max(len(bars[-10:-5]), 1)
        vol_trend = avg_vol_5d / max(avg_vol_prev, 1)

        detail.update({
            "price_change_5d": round(price_5d_change * 100, 2),
            "vol_trend": round(vol_trend, 2),
            "method": "proxy（量价推断）",
        })

        # 基于量价背离代理判断
        if price_5d_change < -0.03 and vol_trend > 1.3:
            # 跌但放量（可能是资金进场）→ 轻度看多
            return 0.60, {**detail, "proxy_pattern": "放量下跌（潜在资金进场）"}
        elif price_5d_change > 0.05 and vol_trend < 0.7:
            # 涨但缩量（动能不足）→ 轻度看空
            return 0.40, {**detail, "proxy_pattern": "缩量上涨（动能衰减）"}
        elif price_5d_change > 0 and vol_trend > 1.2:
            return 0.55, {**detail, "proxy_pattern": "放量上涨（资金流入）"}
        elif price_5d_change < 0 and vol_trend < 0.8:
            return 0.48, {**detail, "proxy_pattern": "缩量下跌（抛压减轻）"}
        else:
            return 0.50, {**detail, "proxy_pattern": "无显著背离"}
