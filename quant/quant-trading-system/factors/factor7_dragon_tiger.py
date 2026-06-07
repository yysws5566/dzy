"""
因子7: 龙虎榜
- 分析龙虎榜上榜股票的买卖席位特征
- 核心指标：机构席位 vs 游资席位、净买入额、买一卖一比值

信号逻辑：
- 机构席位净买入 > 游资净买入 + 买一远超卖一 → 强看多
- 游资T+1席位（一日游）主导 + 净买入小 → 看空（次日抛压）
- 卖出席位集中在个别席位 → 警惕集中抛售
"""

from . import BaseFactor, FactorResult
from liquidity_filter import MarketSnapshot


class DragonTigerFactor(BaseFactor):
    name = "dragon_tiger"
    description = "龙虎榜 — 分析龙虎榜买卖席位特征"
    category = "资金"

    def calculate(self, snapshot: MarketSnapshot) -> FactorResult:
        dragon_data = snapshot.__dict__.get("_dragon_tiger_data", {})
        bars = snapshot.daily_bars

        if not bars or len(bars) < 5:
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.1,
                                detail={"error": "数据不足"})

        has_listed = bool(dragon_data and dragon_data.get("on_list"))

        detail = {"on_dragon_tiger_list": has_listed}

        if not has_listed:
            # 未上龙虎榜，中性
            # 但可以检查是否接近上榜条件（日涨幅>7%或换手率>20%等）
            latest = bars[-1]
            prev = bars[-2]
            ret = (latest.get("close", 0) - prev.get("close", 0)) / max(prev.get("close", 0), 0.01)
            if abs(ret) > 0.07:
                detail["near_list"] = "涨跌幅接近上榜阈值但未上榜"
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.4,
                                detail=detail)

        # ----- 有龙虎榜数据 -----
        buy_seats = dragon_data.get("buy_seats", [])
        sell_seats = dragon_data.get("sell_seats", [])
        total_buy = sum(s.get("amount", 0) for s in buy_seats)
        total_sell = sum(s.get("amount", 0) for s in sell_seats)
        net_buy = total_buy - total_sell

        # 机构席位识别
        inst_buy = sum(s.get("amount", 0) for s in buy_seats if "机构" in s.get("name", ""))
        inst_sell = sum(s.get("amount", 0) for s in sell_seats if "机构" in s.get("name", ""))
        inst_net = inst_buy - inst_sell

        # 买一/卖一比值
        buy1 = buy_seats[0].get("amount", 0) if buy_seats else 0
        sell1 = sell_seats[0].get("amount", 0) if sell_seats else 0
        b1_s1_ratio = buy1 / max(sell1, 1)

        # 游资占比
        retail_buy = total_buy - inst_buy
        retail_sell = total_sell - inst_sell

        detail.update({
            "net_buy_million": round(net_buy / 1e4, 1),
            "inst_net_million": round(inst_net / 1e4, 1),
            "buy1_sell1_ratio": round(b1_s1_ratio, 2),
            "buy_seats_count": len(buy_seats),
            "sell_seats_count": len(sell_seats),
        })

        # ----- 打分 -----
        score = 0.5

        # 机构 vs 游资
        if inst_net > 0 and inst_net > retail_buy * 0.5:
            score += 0.20
            detail["leader"] = "机构主导"
        elif inst_net > 0:
            score += 0.10
            detail["leader"] = "机构参与"
        else:
            detail["leader"] = "游资主导"

        # 净买入额
        if net_buy > 5e7:  # 净买超5000万
            score += 0.15
        elif net_buy > 1e7:
            score += 0.08
        elif net_buy < -3e7:
            score -= 0.15
        elif net_buy < -1e7:
            score -= 0.08

        # 买一卖一比
        if b1_s1_ratio > 3:
            score += 0.10
        elif b1_s1_ratio > 1.5:
            score += 0.05
        elif b1_s1_ratio < 0.33:
            score -= 0.12

        # 席位集中度（卖出席位过于集中=风险）
        if sell_seats and sell1 / max(total_sell, 1) > 0.5:
            score -= 0.08
            detail["sell_concentration"] = "卖出席位高度集中"

        raw_score = self.normalize_score(score)
        signal = 1 if raw_score >= 0.70 else (-1 if raw_score <= 0.30 else 0)
        confidence = self.compute_confidence(
            data_quality=0.9 if has_listed else 0.5,
            signal_strength=abs(raw_score - 0.5) * 2,
        )

        return FactorResult(
            factor_name=self.name, raw_score=raw_score, normalized_score=raw_score,
            signal=signal, confidence=confidence, detail=detail,
        )
