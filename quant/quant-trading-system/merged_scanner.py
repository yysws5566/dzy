"""
融合版选股算法 — 两套方法优势基因合并
=====================================

因子来源分析：
- 新版9因子：尾盘量价/日内趋势/量能/竞价/缺口/反包/均线/整数/板块
- 旧版12因子：尾盘背离/封板/缺口/北向/竞价/反包/龙虎榜/板块滞后/整数/融资/大宗/外盘

合并策略（去冗余 + 保留独立信号）：
- 尾盘量价：合并两版精华，分"尾盘放量"和"尾盘量价背离"两个子维度
- 日内趋势：保留新版（区分度0.15）
- 竞价强度：保留新版（区分度0.21，最高）
- 缺口博弈：保留新版（区分度0.21）
- 断板反包：保留（特殊事件型）
- 均线位置：保留（独立性强）
- 去掉：整数关口（区分度0.00）、板块相对强度（与量能相关0.72）
- 新增：封板质量（旧版独有，涨停板专用）
- 新增：外盘联动（旧版独有，隔夜风险因子）

最终融合版 = 10个因子，8个通用 + 2个特殊事件
"""

import encoding_fix  # noqa
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from tickflow_client import TickFlowClient, get_client


@dataclass
class MergedSignal:
    """融合版选股信号"""
    symbol: str
    name: str
    current_price: float = 0.0
    change_pct: float = 0.0

    # 10个融合因子得分
    tail_volume: float = 0.5        # 尾盘放量抢筹（两版合并）
    intraday_trend: float = 0.5     # 日内趋势质量
    volume_accum: float = 0.5       # 量能堆积
    auction: float = 0.5            # 竞价强度
    gap: float = 0.5                # 缺口博弈
    reversal: float = 0.5           # 断板反包
    ma_position: float = 0.5        # 均线位置
    seal_quality: float = 0.5       # 封板质量（旧版独有）
    global_linkage: float = 0.5     # 外盘联动（旧版独有）
    overnight_risk: float = 0.5     # 隔夜风险评估（新增）

    # 综合
    total_score: float = 0.0
    confidence: float = 0.0
    signal: str = "HOLD"
    position_pct: float = 0.0

    # 元数据
    details: Dict[str, Any] = field(default_factory=dict)


class MergedScanner:
    """
    融合版扫描器

    10个因子 = 8个通用（两版合并去重）+ 2个增强（旧版独有+新增）
    """

    # 默认权重（等权起步，由复盘引擎逐步优化）
    DEFAULT_WEIGHTS = {
        "tail_volume": 0.15,
        "intraday_trend": 0.13,
        "volume_accum": 0.10,
        "auction": 0.12,
        "gap": 0.10,
        "reversal": 0.08,
        "ma_position": 0.07,
        "seal_quality": 0.10,
        "global_linkage": 0.07,
        "overnight_risk": 0.08,
    }

    def __init__(self, client: TickFlowClient = None,
                 weights: Dict[str, float] = None,
                 buy_threshold: float = 0.55,
                 strong_buy_threshold: float = 0.72):
        self.client = client or get_client()
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()
        self.buy_threshold = buy_threshold
        self.strong_buy_threshold = strong_buy_threshold

    def scan(self, symbols: List[str], refine_top: int = 20) -> List[MergedSignal]:
        """
        融合版扫描（复用TickFlow Pro两阶段数据获取）
        """
        from tail_scanner import TailScanner
        # 复用tail_scanner的数据获取基础设施
        ts = TailScanner(client=self.client, buy_threshold=self.buy_threshold)

        print(f"  [融合算法] 扫描 {len(symbols)} 只标的...")
        quotes = ts._fetch_quotes(symbols)
        if not quotes:
            return []

        kline_map = ts._fetch_daily_klines(symbols)
        sector_map = ts._fetch_sector_data(symbols)
        intraday_5m = ts._fetch_intraday_5m(symbols)
        quote_map = {q["symbol"]: q for q in quotes}

        # Pass 1: 5m粗筛
        candidates = []
        for sym in symbols:
            q = quote_map.get(sym)
            if not q: continue
            if not ts._liquidity_filter(q): continue

            daily = kline_map.get(sym, pd.DataFrame())
            intra = intraday_5m.get(sym, pd.DataFrame())
            sector = sector_map.get(sym, {})

            signal = self._calculate(sym, q, daily, intra, sector)
            if signal.total_score >= self.buy_threshold * 0.85:
                candidates.append(signal)

        candidates.sort(key=lambda s: s.total_score, reverse=True)

        # Pass 2: 1m精筛Top N
        if refine_top > 0 and len(candidates) > refine_top:
            refine_syms = [s.symbol for s in candidates[:refine_top]]
            intra_1m = ts._fetch_intraday_1m_parallel(refine_syms)
            if intra_1m:
                refined = []
                for sig in candidates:
                    m1 = intra_1m.get(sig.symbol)
                    if m1 is not None and not m1.empty:
                        q = quote_map.get(sig.symbol, {})
                        daily = kline_map.get(sig.symbol, pd.DataFrame())
                        sector = sector_map.get(sig.symbol, {})
                        new_sig = self._calculate(sig.symbol, q, daily, m1, sector)
                        if new_sig.total_score >= self.buy_threshold:
                            new_sig.signal = "STRONG_BUY" if new_sig.total_score >= self.strong_buy_threshold else "BUY"
                            new_sig.position_pct = self._position(new_sig)
                            refined.append(new_sig)
                    elif sig.total_score >= self.buy_threshold:
                        sig.signal = "STRONG_BUY" if sig.total_score >= self.strong_buy_threshold else "BUY"
                        sig.position_pct = self._position(sig)
                        refined.append(sig)
                refined.sort(key=lambda s: s.total_score, reverse=True)
                candidates = refined

        # 只返回超阈值的
        final = [s for s in candidates if s.total_score >= self.buy_threshold]
        for s in final:
            s.signal = "STRONG_BUY" if s.total_score >= self.strong_buy_threshold else "BUY"
            s.position_pct = self._position(s)

        print(f"  [融合算法] {len(final)} 只信号 (粗筛{len(candidates)}→精筛{len(final)})")
        return final

    def _calculate(self, symbol: str, quote: dict, daily: pd.DataFrame,
                   intra: pd.DataFrame, sector: dict) -> MergedSignal:
        """计算10个融合因子"""
        name = quote.get("name", "")
        price = quote.get("last_price", 0)
        change = quote.get("change_pct", 0)

        sig = MergedSignal(symbol=symbol, name=name, current_price=price, change_pct=change)

        # 复用tail_scanner的8个通用因子
        from tail_scanner import TailScanner
        ts = TailScanner(client=self.client)

        # 因子1-7：复用新版
        sig.tail_volume, sig.details["tail_volume"] = ts._factor_tail_volume(intra, quote)
        sig.intraday_trend, sig.details["intraday_trend"] = ts._factor_intraday_trend(intra, quote)
        sig.volume_accum, sig.details["volume_accum"] = ts._factor_volume_accum(quote, daily)
        sig.auction, sig.details["auction"] = ts._factor_auction(quote, daily)
        sig.gap, sig.details["gap"] = ts._factor_gap(quote, daily, intra)
        sig.reversal, sig.details["reversal"] = ts._factor_reversal(quote, daily)
        sig.ma_position, sig.details["ma_position"] = ts._factor_ma_position(price, daily)

        # 因子8：封板质量（旧版独有 — 检测涨停板次日溢价概率）
        sig.seal_quality, sig.details["seal_quality"] = self._factor_seal_quality(quote, daily)

        # 因子9：外盘联动（旧版独有 — 隔夜外盘影响）
        sig.global_linkage, sig.details["global_linkage"] = self._factor_global_linkage(quote, daily)

        # 因子10：隔夜风险评估（新增 — 综合评估隔夜跳空风险）
        sig.overnight_risk, sig.details["overnight_risk"] = self._factor_overnight_risk(quote, daily, intra)

        # 加权综合
        w = self.weights
        sig.total_score = (
            w["tail_volume"] * sig.tail_volume +
            w["intraday_trend"] * sig.intraday_trend +
            w["volume_accum"] * sig.volume_accum +
            w["auction"] * sig.auction +
            w["gap"] * sig.gap +
            w["reversal"] * sig.reversal +
            w["ma_position"] * sig.ma_position +
            w["seal_quality"] * sig.seal_quality +
            w["global_linkage"] * sig.global_linkage +
            w["overnight_risk"] * sig.overnight_risk
        )

        scores = [sig.tail_volume, sig.intraday_trend, sig.volume_accum,
                   sig.auction, sig.gap, sig.reversal, sig.ma_position,
                   sig.seal_quality, sig.global_linkage, sig.overnight_risk]
        sig.confidence = sum(abs(s - 0.5) * 2 for s in scores) / len(scores)

        return sig

    def _position(self, sig: MergedSignal) -> float:
        base = 0.12 if sig.signal == "STRONG_BUY" else 0.08
        return min(0.25, base * (0.5 + sig.confidence * 0.5))

    # ================================================================
    # 因子8: 封板质量（从旧版移植）
    # ================================================================

    def _factor_seal_quality(self, quote: dict, daily: pd.DataFrame) -> Tuple[float, dict]:
        """
        封板质量 — 评估涨停板的次日溢价概率

        从旧版factor2移植，适配TickFlow数据格式。
        核心逻辑：涨停时间越早、封单越大、开板次数越少→次日溢价越高
        """
        detail = {"pattern": "非涨停", "is_limit_up": False}

        if daily.empty or len(daily) < 3:
            return 0.50, detail

        close = quote.get("last_price", 0)
        prev_close = quote.get("prev_close", 0)

        if prev_close <= 0:
            return 0.50, detail

        daily_return = (close - prev_close) / prev_close
        is_limit_up = daily_return >= 0.095  # 接近涨停

        detail["daily_return"] = round(daily_return * 100, 2)
        detail["is_limit_up"] = is_limit_up

        if not is_limit_up:
            return 0.50, detail

        # 涨停板质量分析
        high = quote.get("high", close)
        open_price = quote.get("open", 0)
        volume = quote.get("volume", 0)
        amount = quote.get("amount", 0)

        # 估算封板时间 — 如果开盘就接近涨停=早封板
        if open_price >= prev_close * 1.05:
            seal_time_score = 0.90  # 开盘封板
        elif open_price >= prev_close * 1.03:
            seal_time_score = 0.70
        elif quote.get("amplitude", 0) < 0.03:
            seal_time_score = 0.80  # 振幅小=一字板或早封板
        else:
            seal_time_score = 0.40  # 尾盘封板

        # 封单量评估 — 缩量涨停=封单意愿强
        if len(daily) >= 5 and "volume" in daily.columns:
            avg_vol_5 = daily["volume"].tail(5).mean()
            vol_ratio = volume / max(avg_vol_5, 1)
            if vol_ratio < 0.5:
                seal_order_score = 0.85
            elif vol_ratio < 0.8:
                seal_order_score = 0.70
            elif vol_ratio < 1.2:
                seal_order_score = 0.55
            else:
                seal_order_score = 0.30
        else:
            seal_order_score = 0.50

        # 尾盘风险 — 收盘价接近最高价=封板牢固
        if high > 0 and close >= high * 0.995:
            tail_risk = 0.0
        elif close >= high * 0.98:
            tail_risk = 0.2
        else:
            tail_risk = 0.5
            detail["pattern"] = "涨停板松动（尾盘回落）"

        score = seal_time_score * 0.40 + seal_order_score * 0.35 + (1 - tail_risk) * 0.25
        detail["seal_time"] = round(seal_time_score, 2)
        detail["seal_order"] = round(seal_order_score, 2)

        if score >= 0.75:
            detail["pattern"] = "高质量封板"
        elif score >= 0.60:
            detail["pattern"] = "中等封板"

        return max(0.0, min(1.0, score)), detail

    # ================================================================
    # 因子9: 外盘联动（从旧版移植）
    # ================================================================

    def _factor_global_linkage(self, quote: dict, daily: pd.DataFrame) -> Tuple[float, dict]:
        """
        外盘联动 — 隔夜外盘对A股的联动影响

        通过开盘跳空幅度和日内消化程度推断外盘影响。
        大幅低开+日内拉回 = 外盘利空被消化 → 看多
        大幅高开+日内回落 = 外盘利好已兑现 → 看空
        """
        detail = {}
        open_price = quote.get("open", 0)
        prev_close = quote.get("prev_close", 0)
        current = quote.get("last_price", 0)

        if prev_close <= 0 or open_price <= 0:
            return 0.50, detail

        overnight_gap = (open_price - prev_close) / prev_close
        intraday_change = (current - open_price) / max(open_price, 0.01)

        detail["overnight_gap"] = round(overnight_gap * 100, 2)
        detail["intraday_change"] = round(intraday_change * 100, 2)

        score = 0.50

        if overnight_gap < -0.01 and intraday_change > 0.015:
            # 大幅低开 + 日内强势拉回 → 利空出尽
            score = 0.75
            detail["pattern"] = "低开高走（外盘利空消化）"
        elif overnight_gap < -0.005 and intraday_change > 0.01:
            score = 0.65
            detail["pattern"] = "小幅低开后回升"
        elif overnight_gap > 0.015 and intraday_change < -0.005:
            # 大幅高开 + 日内回落 → 利好兑现
            score = 0.30
            detail["pattern"] = "高开低走（利好兑现风险）"
        elif overnight_gap > 0.01 and intraday_change > 0.01:
            # 高开继续涨 → 强势延续
            score = 0.62
            detail["pattern"] = "高开高走（强势延续）"
        elif overnight_gap < -0.015 and intraday_change < -0.005:
            score = 0.22
            detail["pattern"] = "低开低走（外盘利空发酵）"
        elif abs(overnight_gap) < 0.003:
            score = 0.50
            detail["pattern"] = "外盘平稳"

        return max(0.0, min(1.0, score)), detail

    # ================================================================
    # 因子10: 隔夜风险评估（新增）
    # ================================================================

    def _factor_overnight_risk(self, quote: dict, daily: pd.DataFrame,
                                intra: pd.DataFrame) -> Tuple[float, dict]:
        """
        隔夜风险评估 — 综合评估T+1隔夜持仓风险

        风险来源：
        1. 日内波动率过高 → 次日可能大幅低开
        2. 尾盘急拉 → 次日容易低开回落
        3. 连续大涨后 → 获利盘抛压
        4. 尾盘放量但价格不涨 → 出货嫌疑

        得分越高 = 隔夜风险越低 = 越适合隔夜持仓
        """
        detail = {"risk_factors": []}
        risk_score = 1.0  # 从满分开始扣

        open_price = quote.get("open", 0)
        high = quote.get("high", 0)
        low = quote.get("low", 0)
        current = quote.get("last_price", 0)
        prev_close = quote.get("prev_close", 0)
        amplitude = quote.get("amplitude", 0)

        if prev_close <= 0:
            return 0.50, detail

        # 1. 日内振幅过大（>5%）= 高风险
        if amplitude > 0.05:
            risk_score -= 0.15
            detail["risk_factors"].append("日内振幅>5%")
        elif amplitude > 0.03:
            risk_score -= 0.05

        # 2. 连续大涨后风险（近5日涨超15%）
        if not daily.empty and len(daily) >= 6 and "close" in daily.columns:
            close_5d_ago = daily["close"].iloc[-6]
            ret_5d = (current - close_5d_ago) / max(close_5d_ago, 0.01)
            if ret_5d > 0.15:
                risk_score -= 0.15
                detail["risk_factors"].append("近5日涨超15%")
            elif ret_5d > 0.10:
                risk_score -= 0.08
            elif ret_5d < -0.10:
                risk_score += 0.05  # 超跌反而风险低
                detail["risk_factors"].append("超跌低风险")

        # 3. 尾盘急拉风险（14:00-14:40涨幅>2%）
        if intra is not None and not intra.empty and len(intra) >= 30:
            n = len(intra)
            tail_start = int(n * 0.8)
            tail_bars = intra.iloc[tail_start:]
            if len(tail_bars) >= 3:
                tail_first = tail_bars.iloc[0].get("close", tail_bars.iloc[0].get("open", current))
                tail_last = tail_bars.iloc[-1].get("close", tail_bars.iloc[-1].get("close", current))
                tail_ret = (tail_last - tail_first) / max(tail_first, 0.01)
                if tail_ret > 0.02:
                    risk_score -= 0.10
                    detail["risk_factors"].append("尾盘急拉（次日低开风险）")
                elif tail_ret < -0.01:
                    risk_score += 0.05  # 尾盘下跌释放风险

        # 4. 量价背离风险（放量但涨幅小）
        change_pct = quote.get("change_pct", 0)
        if abs(change_pct) < 0.01 and amplitude > 0.03:
            risk_score -= 0.08
            detail["risk_factors"].append("放量滞涨")

        detail["risk_score"] = round(risk_score, 2)
        score = max(0.0, min(1.0, risk_score))

        if score >= 0.80:
            detail["pattern"] = "低隔夜风险"
        elif score >= 0.60:
            detail["pattern"] = "中等隔夜风险"
        elif score >= 0.40:
            detail["pattern"] = "偏高隔夜风险"
        else:
            detail["pattern"] = "高隔夜风险"

        return score, detail
