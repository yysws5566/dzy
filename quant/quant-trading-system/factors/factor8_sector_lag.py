"""
因子8: 板块滞后
- 识别板块内滞涨标的的补涨机会
- 板块整体上涨，但某标的涨幅显著落后 → 潜在补涨

信号逻辑：
- 板块近5日涨>3% + 个股近5日涨<板块涨幅的50% → 补涨看多
- 板块领涨但个股下跌 → 需判断是否有利空（无利空时轻仓埋伏）
- 板块下跌 + 个股抗跌 → 潜在转强信号（但需等板块企稳）
"""

from . import BaseFactor, FactorResult
from liquidity_filter import MarketSnapshot


class SectorLagFactor(BaseFactor):
    name = "sector_lag"
    description = "板块滞后 — 识别板块内补涨机会"
    category = "行为"

    def calculate(self, snapshot: MarketSnapshot) -> FactorResult:
        bars = snapshot.daily_bars
        sector_data = snapshot.__dict__.get("_sector_data", {})

        if not bars or len(bars) < 10:
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.1,
                                detail={"error": "数据不足"})

        has_sector = bool(sector_data and sector_data.get("sector_return"))

        detail = {"has_sector_data": has_sector}

        if has_sector:
            raw_score, detail = self._calc_with_sector(snapshot, sector_data, detail)
        else:
            raw_score, detail = self._calc_proxy(snapshot, detail)

        raw_score = self.normalize_score(raw_score)
        signal = 1 if raw_score >= 0.65 else (-1 if raw_score <= 0.35 else 0)
        confidence = self.compute_confidence(
            data_quality=0.85 if has_sector else 0.45,
            signal_strength=abs(raw_score - 0.5) * 2,
        )

        return FactorResult(
            factor_name=self.name, raw_score=raw_score, normalized_score=raw_score,
            signal=signal, confidence=confidence, detail=detail,
        )

    def _calc_with_sector(self, snapshot, sector_data: dict, detail: dict) -> tuple:
        """基于真实板块数据计算"""
        sector_ret_5d = sector_data.get("sector_return_5d", 0)
        sector_ret_1d = sector_data.get("sector_return", 0)

        bars = snapshot.daily_bars
        closes = [b.get("close", 0) for b in bars]
        stock_ret_5d = (closes[-1] - closes[-6]) / max(closes[-6], 0.01) if len(closes) >= 6 else 0

        lag_ratio = stock_ret_5d / max(sector_ret_5d, 0.001)

        detail.update({
            "sector_return_5d": round(sector_ret_5d * 100, 2),
            "stock_return_5d": round(stock_ret_5d * 100, 2),
            "lag_ratio": round(lag_ratio, 2),
        })

        if sector_ret_5d > 0.03 and lag_ratio < 0.5:
            # 板块大涨但个股严重滞后 → 补涨信号
            lag_degree = 1.0 - min(1.0, lag_ratio)
            raw_score = 0.55 + lag_degree * 0.35
            detail["pattern"] = "板块滞涨（补涨机会）"
        elif sector_ret_5d > 0.02 and lag_ratio < 0.3:
            raw_score = 0.65
            detail["pattern"] = "板块严重滞涨"
        elif sector_ret_5d < -0.02 and stock_ret_5d > 0:
            # 板块跌但个股涨 → 独立强势
            raw_score = 0.58
            detail["pattern"] = "逆板块走强"
        elif sector_ret_5d < -0.03 and stock_ret_5d > sector_ret_5d:
            # 板块大跌但个股抗跌
            raw_score = 0.52
            detail["pattern"] = "板块内抗跌"
        else:
            raw_score = 0.50
            detail["pattern"] = "与板块同步"

        return raw_score, detail

    def _calc_proxy(self, snapshot, detail: dict) -> tuple:
        """代理计算（无板块数据时，基于市值风格推断）"""
        bars = snapshot.daily_bars
        closes = [b.get("close", 0) for b in bars]

        stock_ret_5d = (closes[-1] - closes[-6]) / max(closes[-6], 0.01) if len(closes) >= 6 else 0
        stock_ret_20d = (closes[-1] - closes[-20]) / max(closes[-20], 0.01) if len(closes) >= 20 else 0

        detail.update({
            "stock_return_5d": round(stock_ret_5d * 100, 2),
            "stock_return_20d": round(stock_ret_20d * 100, 2),
            "method": "proxy（相对强度推断）",
        })

        # 基于相对强度的代理判断
        if stock_ret_20d > 0.15 and stock_ret_5d < 0.01:
            # 中期强势但短期滞涨 → 可能蓄力
            return 0.60, {**detail, "proxy_pattern": "中期强势短期蓄力"}
        elif stock_ret_20d < -0.10 and stock_ret_5d > 0.02:
            # 中期弱势但短期回暖 → 可能反转
            return 0.55, {**detail, "proxy_pattern": "超跌反弹苗头"}
        elif stock_ret_5d < -0.03 and stock_ret_20d < 0.05:
            # 短期跌但中期未大涨 → 正常回调
            return 0.48, {**detail, "proxy_pattern": "短期回调中"}
        else:
            return 0.50, {**detail, "proxy_pattern": "无显著滞后特征"}
