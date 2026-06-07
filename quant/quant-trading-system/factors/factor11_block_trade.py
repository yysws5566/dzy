"""
因子11: 大宗交易
- 监测大宗交易的折溢价率、成交量和交易对手方
- 溢价大宗交易 + 成交量大 → 看多（机构吸筹）
- 折价大宗交易 + 大股东减持 → 看空
- 平价大宗 → 中性（可能是调仓）

信号逻辑：
- 溢价>3% + 大宗成交额>5000万 → 强看多
- 折价>5% + 卖出方为大股东 → 强看空
- 多笔大宗交易密集出现 → 关注方向
"""

from . import BaseFactor, FactorResult
from liquidity_filter import MarketSnapshot


class BlockTradeFactor(BaseFactor):
    name = "block_trade"
    description = "大宗交易 — 监测大宗交易折溢价和对手方"
    category = "资金"

    def calculate(self, snapshot: MarketSnapshot) -> FactorResult:
        bars = snapshot.daily_bars
        block_data = snapshot.__dict__.get("_block_trade_data", {})

        if not bars or len(bars) < 3:
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.1,
                                detail={"error": "数据不足"})

        has_block = bool(block_data and block_data.get("trades"))
        detail = {"has_block_trade": has_block}

        if not has_block:
            # 无大宗交易数据，中性
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.4,
                                detail={**detail, "reason": "无近期大宗交易"})

        # 分析大宗交易
        trades = block_data.get("trades", [])
        if not trades:
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.4,
                                detail={**detail, "reason": "无大宗交易记录"})

        # 汇总分析
        total_amount = 0
        weighted_premium = 0  # 加权溢价率
        seller_is_major = False  # 是否有大股东卖出
        buyer_is_inst = False    # 是否有机构买入

        for trade in trades:
            amount = trade.get("amount", 0)
            price = trade.get("price", 0)
            ref_price = trade.get("reference_price", bars[-1].get("close", 0))
            premium = (price - ref_price) / max(ref_price, 0.01)

            total_amount += amount
            weighted_premium += premium * amount

            if "股东" in trade.get("seller", "") or "减持" in trade.get("remark", ""):
                seller_is_major = True
            if "机构" in trade.get("buyer", "") or "基金" in trade.get("buyer", ""):
                buyer_is_inst = True

        avg_premium = weighted_premium / max(total_amount, 1) if total_amount > 0 else 0
        latest_close = bars[-1].get("close", 0)
        daily_turnover = bars[-1].get("turnover", bars[-1].get("amount", 1e9))
        trade_ratio = total_amount / max(daily_turnover, 1)

        detail.update({
            "total_amount_million": round(total_amount / 1e4, 1),
            "avg_premium_pct": round(avg_premium * 100, 2),
            "trade_count": len(trades),
            "seller_is_major": seller_is_major,
            "buyer_is_inst": buyer_is_inst,
            "trade_to_turnover_ratio": round(trade_ratio * 100, 1),
        })

        # ----- 打分 -----
        score = 0.5

        # 溢价/折价
        if avg_premium > 0.03:
            score += 0.18
            detail["premium_type"] = "高溢价大宗"
            if buyer_is_inst:
                score += 0.07
                detail["premium_type"] += "（机构溢价吸筹）"
        elif avg_premium > 0.01:
            score += 0.08
            detail["premium_type"] = "小幅溢价"
        elif avg_premium < -0.05:
            score -= 0.20
            detail["premium_type"] = "高折价大宗"
            if seller_is_major:
                score -= 0.10
                detail["premium_type"] += "（大股东折价减持）"
        elif avg_premium < -0.02:
            score -= 0.08
            detail["premium_type"] = "小幅折价"

        # 大宗成交占日成交比
        if trade_ratio > 0.5 and avg_premium > 0:
            score += 0.08
            detail["volume_impact"] = "大宗占日成交>50%（机构大额吸筹）"
        elif trade_ratio > 0.5 and avg_premium < 0:
            score -= 0.10
            detail["volume_impact"] = "大宗大额折价出货"

        # 多笔大宗
        if len(trades) >= 3 and avg_premium > 0.01:
            score += 0.05
            detail["frequency"] = "多笔溢价大宗（密集吸筹）"
        elif len(trades) >= 3 and avg_premium < -0.01:
            score -= 0.05
            detail["frequency"] = "多笔折价大宗（密集出货）"

        raw_score = self.normalize_score(score)
        signal = 1 if raw_score >= 0.65 else (-1 if raw_score <= 0.30 else 0)
        confidence = self.compute_confidence(
            data_quality=0.85,
            signal_strength=abs(raw_score - 0.5) * 2,
        )

        return FactorResult(
            factor_name=self.name, raw_score=raw_score, normalized_score=raw_score,
            signal=signal, confidence=confidence, detail=detail,
        )
