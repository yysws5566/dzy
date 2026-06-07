"""
因子5: 集合竞价
- 分析09:15-09:25集合竞价阶段的量价特征，预判开盘方向
- 核心信号：竞价量比、价格轨迹、未匹配量、试盘行为

信号逻辑：
- 竞价量>昨日同时段2倍 + 竞价尾段价格稳步推升 → 强看多
- 竞价尾段价格跳水 + 未匹配卖单堆积 → 看空
- 竞价价格先涨后跌（假抢筹）→ 看空陷阱
"""

from . import BaseFactor, FactorResult
from liquidity_filter import MarketSnapshot


class AuctionFactor(BaseFactor):
    name = "auction"
    description = "集合竞价 — 分析开盘竞价量价特征"
    category = "行为"

    def calculate(self, snapshot: MarketSnapshot) -> FactorResult:
        daily_bars = snapshot.daily_bars
        auction_data = snapshot.__dict__.get("_auction_data", {})

        if not daily_bars or len(daily_bars) < 5:
            return FactorResult(factor_name=self.name, raw_score=0.5, normalized_score=0.5, signal=0, confidence=0.1,
                                detail={"error": "数据不足"})

        latest = daily_bars[-1]
        open_price = latest.get("open", 0)
        close = latest.get("close", 0)
        prev_close = daily_bars[-2].get("close", 0) if len(daily_bars) >= 2 else open_price

        detail = {}
        has_auction = bool(auction_data)

        if has_auction:
            raw_score, detail = self._calc_from_auction_data(auction_data, open_price, prev_close, detail)
        else:
            raw_score, detail = self._calc_from_daily_bar(snapshot, detail)

        raw_score = self.normalize_score(raw_score)

        if raw_score >= 0.65:
            signal = 1
        elif raw_score <= 0.35:
            signal = -1
        else:
            signal = 0

        confidence = self.compute_confidence(
            data_quality=0.90 if has_auction else 0.50,
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

    def _calc_from_auction_data(self, auction: dict, open_price: float, prev_close: float, detail: dict):
        """基于真实集合竞价数据计算"""
        auction_vol = auction.get("volume", 0)  # 竞价成交量
        auction_amount = auction.get("amount", 0)  # 竞价成交额
        start_price = auction.get("start_indicative_price", open_price)  # 初始指示价
        end_price = auction.get("end_indicative_price", open_price)  # 最终指示价
        unmatched_sell = auction.get("unmatched_sell", 0)  # 未匹配卖单
        unmatched_buy = auction.get("unmatched_buy", 0)  # 未匹配买单

        # 竞价价格轨迹
        price_trajectory = (end_price - start_price) / max(start_price, 0.01)
        # 开盘跳空
        open_gap = (open_price - prev_close) / max(prev_close, 0.01)

        detail.update({
            "auction_vol": auction_vol,
            "price_trajectory": round(price_trajectory * 100, 3),
            "open_gap": round(open_gap * 100, 2),
        })

        # 打分
        score = 0.5

        # 竞价量越大，信号越强
        vol_score = min(0.15, auction_vol / 1e7)

        # 价格轨迹
        if price_trajectory > 0.005:  # 竞价中价格稳步推升
            score += vol_score + 0.10
            detail["trajectory_type"] = "竞价推升"
        elif price_trajectory < -0.005:
            score -= vol_score + 0.10
            detail["trajectory_type"] = "竞价打压"

        # 未匹配量
        if unmatched_buy > unmatched_sell * 2:
            score += 0.05
            detail["unmatched"] = "买盘堆积"
        elif unmatched_sell > unmatched_buy * 2:
            score -= 0.08
            detail["unmatched"] = "卖盘堆积"

        # 开盘确认
        if open_gap > 0.01 and price_trajectory > 0:
            score += 0.05
            detail["open_confirm"] = "开盘确认竞价强势"

        return score, detail

    def _calc_from_daily_bar(self, snapshot, detail: dict):
        """基于日线数据代理计算（无竞价明细时）"""
        bars = snapshot.daily_bars
        latest = bars[-1]
        open_price = latest.get("open", 0)
        close = latest.get("close", 0)
        high = latest.get("high", 0)
        low = latest.get("low", 0)
        volume = latest.get("volume", 0)
        prev_close = bars[-2].get("close", 0) if len(bars) >= 2 else open_price

        open_gap = (open_price - prev_close) / max(prev_close, 0.01)
        daily_range = (high - low) / max(open_price, 0.01)
        close_vs_open = (close - open_price) / max(open_price, 0.01)
        avg_vol_5 = snapshot.get_volume_ma(5)
        vol_ratio = volume / max(avg_vol_5, 1)

        detail.update({
            "open_gap": round(open_gap * 100, 2),
            "daily_range": round(daily_range * 100, 2),
            "close_vs_open": round(close_vs_open * 100, 2),
            "vol_ratio": round(vol_ratio, 2),
            "method": "proxy（日线推断）",
        })

        # 代理打分
        score = 0.5

        # 高开 + 放量 + 收阳 → 类似竞价强势
        if open_gap > 0.01 and vol_ratio > 1.3 and close_vs_open > 0:
            score = 0.72
            detail["proxy_pattern"] = "高开放量收阳"
        elif open_gap > 0.005 and close_vs_open > 0.01:
            score = 0.62
            detail["proxy_pattern"] = "高开收阳"
        elif open_gap < -0.01 and close_vs_open < 0:
            score = 0.30
            detail["proxy_pattern"] = "低开收阴"
        elif open_gap < -0.015:
            score = 0.22
            detail["proxy_pattern"] = "大幅低开"
        elif close_vs_open > 0.03 and vol_ratio > 1.5:
            score = 0.68
            detail["proxy_pattern"] = "盘中强势拉升"

        return score, detail
