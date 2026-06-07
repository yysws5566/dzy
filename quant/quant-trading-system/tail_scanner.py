"""
尾盘2:40选股扫描器
- 专为T+1尾盘买入策略设计
- 数据全部来源于TickFlow SDK
- 2:40分时点计算因子 → 尾盘集合竞价买入 → 次日卖出

核心因子（尾盘2:40可用）：
1. 尾盘放量抢筹 — 14:00-14:40量价异动 ⭐⭐⭐
2. 日内趋势质量 — 分时形态（V反、稳步推升）⭐⭐⭐
3. 量能堆积 — 今日量比 + 量价配合 ⭐⭐
4. 竞价强度 — 开盘竞价质量 ⭐⭐
5. 缺口持续性 — 跳空缺口日内回补/延续 ⭐⭐
6. 断板反包 — 昨日炸板今日反包 ⭐⭐
7. 均线位置 — 相对MA5/MA20位置 ⭐
8. 整数关口 — 心理价位博弈 ⭐
9. 板块相对强度 — 相对同板块表现 ⭐⭐
"""

import encoding_fix  # noqa: F401
import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from tickflow_client import TickFlowClient, get_client


@dataclass
class TailSignal:
    """尾盘选股信号"""
    symbol: str
    name: str
    sector: str = ""

    # 当前价格（14:40时点）
    current_price: float = 0.0
    change_pct: float = 0.0

    # 分项得分 (0-1)
    tail_volume_score: float = 0.5       # 尾盘放量
    intraday_trend_score: float = 0.5    # 日内趋势
    volume_accum_score: float = 0.5      # 量能堆积
    auction_score: float = 0.5           # 竞价强度
    gap_score: float = 0.5               # 缺口博弈
    reversal_score: float = 0.5          # 断板反包
    ma_position_score: float = 0.5       # 均线位置
    integer_psych_score: float = 0.5     # 整数关口
    sector_rel_score: float = 0.5        # 板块相对强度

    # 综合
    total_score: float = 0.0
    confidence: float = 0.0
    signal: str = "HOLD"                 # STRONG_BUY / BUY / HOLD

    # 建议
    position_pct: float = 0.0            # 建议仓位比例

    # 详情
    details: Dict[str, Any] = field(default_factory=dict)


class TailScanner:
    """尾盘2:40选股扫描器"""

    # 默认因子权重（可被优化器覆盖）
    DEFAULT_WEIGHTS = {
        "tail_volume": 0.22,       # 尾盘放量抢筹（最重要）
        "intraday_trend": 0.18,    # 日内趋势质量
        "volume_accum": 0.13,      # 量能堆积
        "auction": 0.10,           # 竞价强度
        "gap": 0.08,               # 缺口持续性
        "reversal": 0.10,          # 断板反包
        "ma_position": 0.05,       # 均线位置
        "integer_psych": 0.04,     # 整数关口
        "sector_rel": 0.10,        # 板块相对强度
    }

    def __init__(self, client: TickFlowClient = None,
                 weights: Dict[str, float] = None,
                 buy_threshold: float = 0.58,
                 strong_buy_threshold: float = 0.75):
        self.client = client or get_client()
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()
        self.buy_threshold = buy_threshold
        self.strong_buy_threshold = strong_buy_threshold

        # 校验权重
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            print(f"[警告] 因子权重合计={total:.3f}，建议归一化")

    # ================================================================
    # 主扫描流程
    # ================================================================

    def scan(self, symbols: List[str], refine_top: int = 20) -> List[TailSignal]:
        """
        尾盘2:40扫描主流程（Pro套餐优化版）

        两阶段分析：
        Pass 1: 全量5分钟K线扫描 → 粗筛候选
        Pass 2: Top N候选用1分钟K线精筛 → 提升精度

        Args:
            symbols: TickFlow格式股票列表
            refine_top: 粗筛后取前N只做1分钟精细分析（0=跳过精筛）
        """
        print(f"  🔍 尾盘扫描: {len(symbols)} 只标的...")

        # 1. 获取实时行情（14:40快照，一次性）
        print(f"     [数据] 获取实时行情...")
        quotes = self._fetch_quotes(symbols)
        if not quotes:
            print("  ❌ 无法获取实时行情")
            return []

        # 2. 批量获取日K线
        print(f"     [数据] 获取日K线...")
        kline_map = self._fetch_daily_klines(symbols)

        # 3. 获取板块数据
        sector_map = self._fetch_sector_data(symbols)

        # ==== Pass 1: 5分钟K线粗筛 ====
        print(f"     [Pass1] 5分钟K线批量粗筛...")
        intraday_5m = self._fetch_intraday_5m(symbols)
        if intraday_5m:
            print(f"       5m数据: {len(intraday_5m)} 只")
        else:
            print(f"       5m数据不可用，使用日线代理模式")

        quote_map = {q["symbol"]: q for q in quotes}

        # Pass 1: 对所有标的计算粗筛得分
        pass1_signals = []
        for i, symbol in enumerate(symbols):
            q = quote_map.get(symbol)
            if q is None:
                continue
            if not self._liquidity_filter(q):
                continue

            daily = kline_map.get(symbol, pd.DataFrame())
            intra_5m = intraday_5m.get(symbol, pd.DataFrame())
            sector = sector_map.get(symbol, {})

            signal = self._calculate_signal(symbol, q, daily, intra_5m, sector)
            if signal.total_score >= self.buy_threshold * 0.85:  # 粗筛阈值略低
                signal.signal = "STRONG_BUY" if signal.total_score >= self.strong_buy_threshold else "BUY"
                signal.position_pct = self._calc_position(signal)
                pass1_signals.append(signal)

            if (i + 1) % 50 == 0:
                print(f"       进度: {i+1}/{len(symbols)}")

        pass1_signals.sort(key=lambda s: s.total_score, reverse=True)
        print(f"     Pass1 粗筛: {len(pass1_signals)} 只候选")

        # ==== Pass 2: 1分钟K线精筛（Top N候选） ====
        if refine_top > 0 and pass1_signals and intraday_5m:
            refine_symbols = [s.symbol for s in pass1_signals[:refine_top]]
            print(f"     [Pass2] 1分钟K线精筛 Top {len(refine_symbols)}...")

            intraday_1m = self._fetch_intraday_1m_parallel(refine_symbols)
            if intraday_1m:
                print(f"       1m数据: {len(intraday_1m)} 只")

                # 用1分钟数据重新计算精筛得分
                refined_signals = []
                for s in pass1_signals:
                    intra_1m = intraday_1m.get(s.symbol)
                    if intra_1m is not None and not intra_1m.empty:
                        daily = kline_map.get(s.symbol, pd.DataFrame())
                        sector = sector_map.get(s.symbol, {})
                        q = quote_map.get(s.symbol, {})
                        # 用1分钟数据重新计算
                        refined = self._calculate_signal(s.symbol, q, daily, intra_1m, sector)
                        if refined.total_score >= self.buy_threshold:
                            refined.signal = "STRONG_BUY" if refined.total_score >= self.strong_buy_threshold else "BUY"
                            refined.position_pct = self._calc_position(refined)
                            refined_signals.append(refined)
                    else:
                        # 没有1m数据，保留5m结果
                        if s.total_score >= self.buy_threshold:
                            refined_signals.append(s)

                refined_signals.sort(key=lambda x: x.total_score, reverse=True)
                pass1_signals = refined_signals
            else:
                print(f"       1m数据不可用，保留5m结果")

        # 只返回超过正式阈值的信号
        final_signals = [s for s in pass1_signals if s.total_score >= self.buy_threshold]
        print(f"  ✅ 扫描完成: {len(final_signals)} 只买入信号 (粗筛{len(pass1_signals)}只→精筛{len(final_signals)}只)")
        return final_signals

    # ================================================================
    # 数据获取
    # ================================================================

    def _fetch_quotes(self, symbols: List[str]) -> List[dict]:
        """获取实时行情"""
        try:
            return self.client.get_realtime_quotes(symbols)
        except Exception as e:
            print(f"  [警告] 实时行情获取失败: {e}")
            return []

    def _fetch_daily_klines(self, symbols: List[str]) -> Dict[str, pd.DataFrame]:
        """批量获取日K线"""
        try:
            return self.client.get_daily_klines_batch(symbols, count=60)
        except Exception as e:
            print(f"  [警告] K线获取失败: {e}")
            return {}

    def _fetch_intraday_5m(self, symbols: List[str]) -> Dict[str, pd.DataFrame]:
        """
        【Pro套餐】批量获取5分钟K线（主力日内数据源）

        利用 klines.batch(period='5m') 一次性拉取所有标的的日内K线。
        48根5分钟K线覆盖全天240分钟。
        """
        try:
            return self.client.get_intraday_5m_batch(symbols, count=48)
        except Exception:
            return {}

    def _fetch_intraday_1m_parallel(self, symbols: List[str]) -> Dict[str, pd.DataFrame]:
        """
        【Pro套餐】并行获取1分钟K线（精筛用）

        对粗筛后的Top N候选做精细分析。
        使用线程池并行请求单只intraday接口。
        """
        if not symbols:
            return {}
        try:
            return self.client.get_intraday_1m_parallel(symbols, count=240, max_workers=6)
        except Exception:
            return {}

    def _fetch_intraday(self, symbols: List[str]) -> Dict[str, pd.DataFrame]:
        """
        【Pro套餐优化】分层获取分时数据

        策略：
        1. 主力: klines.batch(period='5m', count=48) — 批量获取5分钟K线
        2. 补充: 1m intraday 并行 — 对流动性筛选后的候选做精细分析
        3. 降级: 返回空 → 因子用日线代理
        """
        # 第一层：5分钟K线批量（Pro套餐核心能力）
        try:
            result_5m = self.client.get_intraday_5m_batch(symbols, count=48)
            if result_5m:
                # 对其中成交活跃的标的，补1分钟精细数据（可选）
                return result_5m
        except Exception as e:
            pass

        # 降级：返回空
        return {}

    def _fetch_sector_data(self, symbols: List[str]) -> Dict[str, dict]:
        """获取板块归属数据"""
        try:
            instruments = self.client.get_instrument_info(symbols)
            return {i["symbol"]: i for i in instruments}
        except Exception:
            return {}

    def _liquidity_filter(self, quote: dict) -> bool:
        """尾盘流动性快速筛选"""
        price = quote.get("last_price", 0)
        amount = quote.get("amount", 0)
        turnover_rate = quote.get("turnover_rate", 0)
        change_pct = quote.get("change_pct", 0)

        # 价格过滤（3-200元）
        if price < 3 or price > 200:
            return False
        # 成交额过滤（>2000万）
        if amount < 20_000_000:
            return False
        # 换手率（>0.3%，排除僵尸股）
        if turnover_rate < 0.003:
            return False
        # 涨跌停过滤（已涨停的T+1没空间，跌停的风险大）
        if abs(change_pct) >= 0.095:
            return False

        return True

    # ================================================================
    # 因子计算
    # ================================================================

    def _calculate_signal(self, symbol: str, quote: dict,
                          daily: pd.DataFrame, intra: pd.DataFrame,
                          sector_info: dict) -> TailSignal:
        """计算单只标的的全部因子"""

        name = quote.get("name", "")
        sector = sector_info.get("industry", "")
        price = quote.get("last_price", 0)
        change_pct = quote.get("change_pct", 0)

        signal = TailSignal(
            symbol=symbol, name=name, sector=sector,
            current_price=price, change_pct=change_pct,
        )

        # 因子1: 尾盘放量抢筹（核心因子，权重最高）
        signal.tail_volume_score, signal.details["tail_volume"] = \
            self._factor_tail_volume(intra, quote)

        # 因子2: 日内趋势质量
        signal.intraday_trend_score, signal.details["intraday_trend"] = \
            self._factor_intraday_trend(intra, quote)

        # 因子3: 量能堆积
        signal.volume_accum_score, signal.details["volume_accum"] = \
            self._factor_volume_accum(quote, daily)

        # 因子4: 竞价强度
        signal.auction_score, signal.details["auction"] = \
            self._factor_auction(quote, daily)

        # 因子5: 缺口博弈
        signal.gap_score, signal.details["gap"] = \
            self._factor_gap(quote, daily, intra)

        # 因子6: 断板反包
        signal.reversal_score, signal.details["reversal"] = \
            self._factor_reversal(quote, daily)

        # 因子7: 均线位置
        signal.ma_position_score, signal.details["ma_position"] = \
            self._factor_ma_position(price, daily)

        # 因子8: 整数关口
        signal.integer_psych_score, signal.details["integer_psych"] = \
            self._factor_integer_psych(price, daily)

        # 因子9: 板块相对强度
        signal.sector_rel_score, signal.details["sector_rel"] = \
            self._factor_sector_rel(quote, daily, sector_info)

        # 加权综合
        w = self.weights
        signal.total_score = (
            w["tail_volume"] * signal.tail_volume_score +
            w["intraday_trend"] * signal.intraday_trend_score +
            w["volume_accum"] * signal.volume_accum_score +
            w["auction"] * signal.auction_score +
            w["gap"] * signal.gap_score +
            w["reversal"] * signal.reversal_score +
            w["ma_position"] * signal.ma_position_score +
            w["integer_psych"] * signal.integer_psych_score +
            w["sector_rel"] * signal.sector_rel_score
        )

        # 置信度 = 各因子偏离中性的平均强度
        scores = [
            signal.tail_volume_score, signal.intraday_trend_score,
            signal.volume_accum_score, signal.auction_score,
            signal.gap_score, signal.reversal_score,
            signal.ma_position_score, signal.integer_psych_score,
            signal.sector_rel_score,
        ]
        deviations = [abs(s - 0.5) * 2 for s in scores]
        signal.confidence = sum(deviations) / len(deviations)

        return signal

    def _calc_position(self, signal: TailSignal) -> float:
        """动态仓位计算"""
        base = 0.10  # 基础10%
        if signal.signal == "STRONG_BUY":
            base = 0.18
        # 置信度调节
        conf_adj = 0.5 + signal.confidence * 0.5
        return min(0.25, base * conf_adj)

    # ================================================================
    # 因子1: 尾盘放量抢筹 ⭐⭐⭐
    # ================================================================

    def _factor_tail_volume(self, intra: pd.DataFrame, quote: dict) -> Tuple[float, dict]:
        """尾盘14:00-14:40放量抢筹检测"""
        detail = {"pattern": "尾盘正常", "has_intraday": False}

        if intra is not None and not intra.empty and len(intra) >= 30:
            return self._factor_tail_volume_intraday(intra, detail)

        return self._factor_tail_volume_proxy(quote, detail)

    def _factor_tail_volume_intraday(self, df: pd.DataFrame, detail: dict) -> Tuple[float, dict]:
        """有分时数据时的精确计算"""
        detail["has_intraday"] = True

        if "datetime" in df.columns:
            df = df.sort_values("datetime")
        elif "timestamp" in df.columns:
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df["datetime"] = df["datetime"].dt.tz_convert("Asia/Shanghai")
            df = df.sort_values("datetime")

        n = len(df)
        tail_start = max(0, int(n * 0.8))
        early_end = int(n * 0.7)
        tail_bars = df.iloc[tail_start:]
        early_bars = df.iloc[:early_end] if early_end > 0 else df.iloc[:1]

        if len(tail_bars) < 5:
            return 0.50, detail

        tail_avg_vol = tail_bars["volume"].mean() if "volume" in tail_bars.columns else 0
        early_avg_vol = early_bars["volume"].mean() if "volume" in early_bars.columns else 1
        tail_first = tail_bars.iloc[0]["close"] if "close" in tail_bars.columns else tail_bars.iloc[0].get("open", 0)
        tail_last = tail_bars.iloc[-1]["close"] if "close" in tail_bars.columns else tail_bars.iloc[-1].get("close", 0)
        tail_return = (tail_last - tail_first) / max(tail_first, 0.01)
        vol_ratio = tail_avg_vol / max(early_avg_vol, 1)

        detail["vol_ratio"] = round(vol_ratio, 2)
        detail["tail_return"] = round(tail_return * 100, 3)

        return self._score_tail_volume(vol_ratio, tail_return, detail)

    def _factor_tail_volume_proxy(self, quote: dict, detail: dict) -> Tuple[float, dict]:
        """
        无分时数据时的代理计算
        用实时行情推断尾盘行为：
        - 涨幅>2%且接近日内高点 → 尾盘可能抢筹
        - 涨幅>1%且当前价>开盘价 → 温和看多
        - 跌幅>2%且接近日内低点 → 尾盘可能出逃
        """
        change_pct = quote.get("change_pct", 0)
        open_price = quote.get("open", 0)
        high = quote.get("high", 0)
        low = quote.get("low", 0)
        current = quote.get("last_price", 0)

        if open_price <= 0:
            return 0.50, detail

        # 当前价格在日内位置
        day_range = high - low
        if day_range > 0:
            position_in_range = (current - low) / day_range  # 0=低点, 1=高点
        else:
            position_in_range = 0.5

        # 量比（14:40成交量预估全天量能）
        vol = quote.get("volume", 0)
        detail["position_in_range"] = round(position_in_range, 2)
        detail["change_pct"] = round(change_pct * 100, 2)

        # 模拟尾盘量比（高价股和活跃股尾盘通常放量）
        if position_in_range > 0.7 and change_pct > 0.01:
            vol_ratio = 1.5 + position_in_range * 0.5  # 模拟量比
            tail_return = change_pct * 0.3  # 尾盘贡献约30%的涨幅
        elif position_in_range < 0.3 and change_pct < -0.01:
            vol_ratio = 1.3 + (1 - position_in_range) * 0.5
            tail_return = change_pct * 0.4
        else:
            vol_ratio = 1.0
            tail_return = change_pct * 0.15

        detail["vol_ratio"] = round(vol_ratio, 2)
        detail["tail_return"] = round(tail_return * 100, 3)
        detail["method"] = "proxy"

        return self._score_tail_volume(vol_ratio, tail_return, detail)

    def _score_tail_volume(self, vol_ratio: float, tail_return: float,
                           detail: dict) -> Tuple[float, dict]:
        """尾盘量价统一打分逻辑"""
        score = 0.50

        if vol_ratio > 2.0 and tail_return > 0.002:
            score = 0.75 + min(0.25, tail_return * 50 + (vol_ratio - 2.0) * 0.1)
            detail["pattern"] = "尾盘强抢筹"
        elif vol_ratio > 1.5 and tail_return > 0.001:
            score = 0.62 + min(0.20, tail_return * 30 + (vol_ratio - 1.5) * 0.1)
            detail["pattern"] = "尾盘温和抢筹"
        elif vol_ratio > 1.5 and tail_return < -0.002:
            score = 0.35 - min(0.15, abs(tail_return) * 20)
            detail["pattern"] = "尾盘放量出逃"
        elif vol_ratio < 0.5:
            if tail_return > 0.001:
                score = 0.55
                detail["pattern"] = "尾盘缩量微涨"
            else:
                score = 0.48
                detail["pattern"] = "尾盘缩量横盘"
        elif tail_return > 0.003:
            score = 0.58
            detail["pattern"] = "尾盘温和上涨"

        return max(0.0, min(1.0, score)), detail

    # ================================================================
    # 因子2: 日内趋势质量 ⭐⭐⭐
    # ================================================================

    def _factor_intraday_trend(self, intra: pd.DataFrame, quote: dict) -> Tuple[float, dict]:
        """
        日内分时趋势质量

        有分时数据时：检测V型反转、稳步推升等形态
        无分时数据时：用开盘价vs当前价+高低点位置推断日内走势
        """
        detail = {"pattern": "日内震荡", "has_intraday": False}

        if intra is not None and not intra.empty and len(intra) >= 30:
            return self._factor_intraday_trend_real(intra, quote, detail)

        return self._factor_intraday_trend_proxy(quote, detail)

    def _factor_intraday_trend_real(self, df: pd.DataFrame, quote: dict,
                                     detail: dict) -> Tuple[float, dict]:
        """有分时数据时的精确日内趋势检测"""
        detail["has_intraday"] = True
        if "close" not in df.columns:
            return 0.50, detail

        prices = df["close"].values
        n = len(prices)

        open_price = quote.get("open", prices[0])
        current_price = quote.get("last_price", prices[-1])
        high_price = quote.get("high", max(prices))
        low_price = quote.get("low", min(prices))

        day_return = (current_price - open_price) / max(open_price, 0.01)
        first_half = prices[:n//2]
        second_half = prices[n//2:]
        first_trend = (first_half[-1] - first_half[0]) / max(first_half[0], 0.01)
        second_trend = (second_half[-1] - second_half[0]) / max(second_half[0], 0.01)
        high_position = np.argmax(prices) / n
        low_position = np.argmin(prices) / n

        detail["day_return"] = round(day_return * 100, 2)
        detail["high_pos"] = round(high_position, 2)
        detail["low_pos"] = round(low_position, 2)

        score = 0.50
        if low_position < 0.45 and high_position > 0.6 and day_return > 0.005:
            score = 0.78; detail["pattern"] = "日内V型反转"
        elif first_trend > 0 and second_trend > 0 and second_trend > first_trend:
            score = 0.72; detail["pattern"] = "稳步推升"
        elif open_price < quote.get("prev_close", open_price) * 0.995 and day_return > 0.01:
            score = 0.68; detail["pattern"] = "低开高走"
        elif high_position < 0.5 and day_return < -0.005:
            score = 0.30; detail["pattern"] = "冲高回落"
        elif first_trend < -0.01 and second_trend < -0.005:
            score = 0.22; detail["pattern"] = "单边下跌"
        elif abs(day_return) < 0.005:
            score = 0.52; detail["pattern"] = "窄幅震荡"

        return max(0.0, min(1.0, score)), detail

    def _factor_intraday_trend_proxy(self, quote: dict, detail: dict) -> Tuple[float, dict]:
        """
        无分时数据时的日内趋势代理推断
        使用开盘价、最高价、最低价、当前价推断日内走势形态
        """
        open_p = quote.get("open", 0)
        high = quote.get("high", 0)
        low = quote.get("low", 0)
        current = quote.get("last_price", 0)
        prev_close = quote.get("prev_close", 0)

        if open_p <= 0:
            return 0.50, detail

        day_return = (current - open_p) / open_p
        detail["day_return"] = round(day_return * 100, 2)
        detail["method"] = "proxy"

        # 推断日内走势
        if high > open_p and low < open_p:
            # 有上有下，看收盘位置
            range_total = max(high - low, 0.01)
            position = (current - low) / range_total
            if position > 0.7 and day_return > 0.005:
                score = 0.68; detail["pattern"] = "日内低开/探底回升"
            elif position < 0.3 and day_return < -0.005:
                score = 0.32; detail["pattern"] = "日内冲高回落"
            else:
                score = 0.50; detail["pattern"] = "日内震荡"
        elif current > open_p and low >= open_p * 0.995:
            # 全天在开盘价上方
            if day_return > 0.02:
                score = 0.72; detail["pattern"] = "单边上涨"
            elif day_return > 0.01:
                score = 0.62; detail["pattern"] = "稳步上行"
            else:
                score = 0.55; detail["pattern"] = "小幅上涨"
        elif current < open_p and high <= open_p * 1.005:
            # 全天在开盘价下方
            if day_return < -0.02:
                score = 0.22; detail["pattern"] = "单边下跌"
            elif day_return < -0.01:
                score = 0.35; detail["pattern"] = "持续走弱"
            else:
                score = 0.48; detail["pattern"] = "窄幅走低"
        else:
            # 高低开情况
            if day_return > 0.01:
                score = 0.62; detail["pattern"] = "日内走强"
            elif day_return < -0.01:
                score = 0.38; detail["pattern"] = "日内走弱"
            else:
                score = 0.50; detail["pattern"] = "日内震荡"

        return max(0.0, min(1.0, score)), detail

    # ================================================================
    # 因子3: 量能堆积 ⭐⭐
    # ================================================================

    def _factor_volume_accum(self, quote: dict, daily: pd.DataFrame) -> Tuple[float, dict]:
        """
        量能堆积检测

        - 今日预估量 vs 5日均量（量比）
        - 量价配合度（放量上涨=好，放量下跌=差）
        """
        detail = {}
        change_pct = quote.get("change_pct", 0)
        cur_volume = quote.get("volume", 0)
        cur_amount = quote.get("amount", 0)

        if daily.empty or len(daily) < 10:
            return 0.50, detail

        # 预估全日量（14:40约占全天85%）
        est_full_volume = cur_volume / 0.85
        est_full_amount = cur_amount / 0.85

        # 5日均量
        if "volume" in daily.columns:
            avg_vol_5 = daily["volume"].tail(5).mean()
            avg_amount_5 = daily["amount"].tail(5).mean() if "amount" in daily.columns else avg_vol_5 * quote.get("last_price", 10)
        else:
            avg_vol_5 = 1e8
            avg_amount_5 = 1e9

        vol_ratio = est_full_volume / max(avg_vol_5, 1)
        amount_ratio = est_full_amount / max(avg_amount_5, 1)

        detail["vol_ratio"] = round(vol_ratio, 2)
        detail["amount_ratio"] = round(amount_ratio, 2)

        score = 0.50

        # 放量上涨 → 看多
        if vol_ratio > 1.5 and change_pct > 0.01:
            score = 0.70 + min(0.20, (vol_ratio - 1.5) * 0.2)
            detail["pattern"] = "放量上涨"
        elif vol_ratio > 1.2 and change_pct > 0.005:
            score = 0.60 + min(0.10, (vol_ratio - 1.2) * 0.15)
            detail["pattern"] = "温和放量上涨"
        elif vol_ratio > 1.5 and change_pct < -0.01:
            score = 0.30 - min(0.10, abs(change_pct) * 3)
            detail["pattern"] = "放量下跌"
        elif vol_ratio < 0.5 and abs(change_pct) < 0.01:
            score = 0.55
            detail["pattern"] = "缩量整固"
        elif vol_ratio > 2.0 and change_pct > 0.02:
            score = 0.85
            detail["pattern"] = "巨量上涨"

        return max(0.0, min(1.0, score)), detail

    # ================================================================
    # 因子4: 竞价强度 ⭐⭐
    # ================================================================

    def _factor_auction(self, quote: dict, daily: pd.DataFrame) -> Tuple[float, dict]:
        """
        开盘竞价质量（基于开盘价推断）

        - 开盘跳空方向
        - 开盘后30分钟走势（确认/否定竞价方向）
        """
        detail = {}
        open_price = quote.get("open", 0)
        prev_close = quote.get("prev_close", 0)
        current_price = quote.get("last_price", 0)

        if prev_close <= 0:
            return 0.50, detail

        open_gap = (open_price - prev_close) / prev_close
        # 开盘后走势（到14:40）
        session_return = (current_price - open_price) / max(open_price, 0.01)

        detail["open_gap"] = round(open_gap * 100, 2)
        detail["session_return"] = round(session_return * 100, 2)

        score = 0.50

        # 高开 + 日内继续涨 = 竞价强势被确认
        if open_gap > 0.005 and session_return > 0.01:
            score = 0.75
            detail["pattern"] = "高开高走（竞价强势确认）"
        elif open_gap > 0.01 and session_return > 0:
            score = 0.68
            detail["pattern"] = "高开维持"
        # 低开 + 日内拉回 = 竞价弱势被否定（弱转强）
        elif open_gap < -0.005 and session_return > 0.015:
            score = 0.72
            detail["pattern"] = "低开高走（弱转强）"
        # 高开 + 日内走弱 = 竞价陷阱
        elif open_gap > 0.01 and session_return < -0.01:
            score = 0.28
            detail["pattern"] = "高开低走（竞价陷阱）"
        # 低开 + 继续跌 = 竞价弱势确认
        elif open_gap < -0.01 and session_return < -0.01:
            score = 0.22
            detail["pattern"] = "低开低走"

        return max(0.0, min(1.0, score)), detail

    # ================================================================
    # 因子5: 缺口博弈 ⭐⭐
    # ================================================================

    def _factor_gap(self, quote: dict, daily: pd.DataFrame,
                    intra: pd.DataFrame) -> Tuple[float, dict]:
        """
        缺口博弈

        - 昨日收盘到今日开盘的缺口
        - 日内是否回补
        - 缺口的量和意义
        """
        detail = {}
        open_price = quote.get("open", 0)
        prev_close = quote.get("prev_close", 0)
        current_price = quote.get("last_price", 0)
        high_price = quote.get("high", 0)
        low_price = quote.get("low", 0)

        if prev_close <= 0 or daily.empty:
            return 0.50, detail

        # 昨日高低点
        yesterday_high = prev_close  # fallback
        yesterday_low = prev_close
        if len(daily) >= 2:
            if "high" in daily.columns:
                yesterday_high = daily["high"].iloc[-2]
                yesterday_low = daily["low"].iloc[-2]

        # 缺口检测
        gap_up = low_price > yesterday_high  # 全天最低 > 昨日最高
        gap_down = high_price < yesterday_low  # 全天最高 < 昨日最低
        open_gap_up = open_price > yesterday_high + yesterday_high * 0.002
        open_gap_down = open_price < yesterday_low - yesterday_low * 0.002

        detail["gap_direction"] = "up" if open_gap_up else ("down" if open_gap_down else "none")

        if not open_gap_up and not open_gap_down:
            return 0.50, detail

        score = 0.50

        if open_gap_up:
            gap_size = (open_price - yesterday_high) / yesterday_high
            # 缺口是否回补
            filled = low_price <= yesterday_high
            if not filled and current_price > open_price:
                # 未回补 + 继续上涨 = 强缺口
                score = 0.78
                detail["pattern"] = "向上缺口未回补（强势）"
            elif not filled:
                score = 0.60
                detail["pattern"] = "向上缺口维持"
            else:
                score = 0.45
                detail["pattern"] = "向上缺口已回补（弱势）"
        else:
            gap_size = (yesterday_low - open_price) / yesterday_low
            filled = high_price >= yesterday_low
            if filled and current_price > open_price:
                # 回补 + 上涨 = 利空出尽
                score = 0.65
                detail["pattern"] = "向下缺口回补并转涨"
            elif filled:
                score = 0.50
                detail["pattern"] = "向下缺口已回补"
            elif not filled:
                score = 0.25
                detail["pattern"] = "向下缺口未回补（弱势）"

        return max(0.0, min(1.0, score)), detail

    # ================================================================
    # 因子6: 断板反包 ⭐⭐
    # ================================================================

    def _factor_reversal(self, quote: dict, daily: pd.DataFrame) -> Tuple[float, dict]:
        """
        断板反包检测

        - 昨日涨停/接近涨停后回落
        - 今日是否反包
        """
        detail = {}
        if daily.empty or len(daily) < 3:
            return 0.50, detail

        if "close" not in daily.columns or "open" not in daily.columns:
            return 0.50, detail

        y_close = daily["close"].iloc[-2]
        y_open = daily["open"].iloc[-2]
        y_high = daily["high"].iloc[-2]
        y2_close = daily["close"].iloc[-3]
        t_close = quote.get("last_price", 0)
        t_open = quote.get("open", 0)
        t_high = quote.get("high", 0)

        # 昨日涨幅
        y_return = (y_close - y2_close) / max(y2_close, 0.01)
        # 昨日上影线比例
        y_upper_shadow = (y_high - y_close) / max(y_close, 0.01)
        # 今日涨幅（至14:40）
        t_return = (t_close - t_open) / max(t_open, 0.01)

        detail["y_return"] = round(y_return * 100, 2)
        detail["y_upper_shadow"] = round(y_upper_shadow * 100, 2)

        # 判断昨日是否"断板"（冲高7%+但长上影）
        was_break = y_return > 0.07 and y_upper_shadow > 0.03
        was_touch_limit = y_high >= y2_close * 1.09 and y_close < y2_close * 1.05

        if not was_break and not was_touch_limit:
            return 0.50, {**detail, "pattern": "昨日正常"}

        # 今日反包判断
        score = 0.50  # 默认中性
        if t_close > y_high:
            score = 0.85
            detail["pattern"] = "强反包（突破昨日高点）"
        elif t_close > y_close and t_return > 0.02:
            score = 0.68
            detail["pattern"] = "弱反包（收复失地）"
        elif t_close > y_close * 0.98:
            score = 0.52
            detail["pattern"] = "断板后企稳"
        elif t_close < y_close * 0.97:
            score = 0.20
            detail["pattern"] = "断板后继续下跌"

        return max(0.0, min(1.0, score)), detail

    # ================================================================
    # 因子7: 均线位置 ⭐
    # ================================================================

    def _factor_ma_position(self, price: float, daily: pd.DataFrame) -> Tuple[float, dict]:
        """均线位置评估"""
        detail = {}
        if daily.empty or "close" not in daily.columns or len(daily) < 20:
            return 0.50, detail

        closes = daily["close"].values

        ma5 = closes[-5:].mean() if len(closes) >= 5 else price
        ma10 = closes[-10:].mean() if len(closes) >= 10 else price
        ma20 = closes[-20:].mean() if len(closes) >= 20 else price

        detail["ma5"] = round(ma5, 2)
        detail["ma20"] = round(ma20, 2)
        detail["vs_ma5"] = round((price - ma5) / ma5 * 100, 2)
        detail["vs_ma20"] = round((price - ma20) / ma20 * 100, 2)

        score = 0.50
        above_all = price > ma5 and price > ma20
        above_ma5 = price > ma5
        near_ma20 = abs(price - ma20) / ma20 < 0.02

        if above_all and ma5 > ma20:
            # 多头排列 + 站上所有均线
            score = 0.72
            detail["pattern"] = "多头排列"
        elif above_ma5 and near_ma20:
            # 刚站上MA20附近
            score = 0.65
            detail["pattern"] = "站上均线支撑"
        elif not above_ma5 and price > ma20:
            score = 0.52
            detail["pattern"] = "短期回调中"
        elif price < ma20 and price > ma20 * 0.95:
            score = 0.40
            detail["pattern"] = "跌破MA20"
        elif price < ma5 and price < ma20:
            score = 0.30
            detail["pattern"] = "空头排列"

        return max(0.0, min(1.0, score)), detail

    # ================================================================
    # 因子8: 整数关口 ⭐
    # ================================================================

    def _factor_integer_psych(self, price: float, daily: pd.DataFrame) -> Tuple[float, dict]:
        """整数关口心理博弈"""
        detail = {}
        if price <= 0:
            return 0.50, detail

        # 找最近的整数关口
        key_levels = [5, 8, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 150, 200, 300, 500]
        nearest_above = min([l for l in key_levels if l > price], default=None)
        nearest_below = max([l for l in key_levels if l < price], default=None)

        detail["nearest_level"] = nearest_below or nearest_above

        score = 0.50

        # 刚突破整数关口（在关口上方1-3%）
        if nearest_below and 0 < (price - nearest_below) / nearest_below < 0.03:
            score = 0.68
            detail["pattern"] = f"刚突破{nearest_below}元关口"
        # 接近整数关口上方（支撑确认）
        elif nearest_below and (price - nearest_below) / nearest_below < 0.01:
            score = 0.55
        # 接近整数关口下方（受阻）
        elif nearest_above and (nearest_above - price) / price < 0.02:
            score = 0.40
            detail["pattern"] = f"受阻于{nearest_above}元关口"

        return max(0.0, min(1.0, score)), detail

    # ================================================================
    # 因子9: 板块相对强度 ⭐⭐
    # ================================================================

    def _factor_sector_rel(self, quote: dict, daily: pd.DataFrame,
                           sector_info: dict) -> Tuple[float, dict]:
        """
        板块相对强度（简化版：基于个股自身相对大盘的走势）

        在全板块数据不可用时，通过以下代理判断：
        - 相对自身历史波动的位置
        - 近期相对强势程度
        """
        detail = {}
        change_pct = quote.get("change_pct", 0)

        if daily.empty or "close" not in daily.columns or len(daily) < 20:
            return 0.50, detail

        closes = daily["close"].values
        ret_5d = (closes[-1] - closes[-6]) / closes[-6] if len(closes) >= 6 else 0
        ret_10d = (closes[-1] - closes[-11]) / closes[-11] if len(closes) >= 11 else 0
        ret_20d = (closes[-1] - closes[-21]) / closes[-21] if len(closes) >= 21 else 0

        detail["ret_5d"] = round(ret_5d * 100, 2)
        detail["ret_20d"] = round(ret_20d * 100, 2)

        score = 0.50

        # 中期上行趋势 + 今日强势 → 板块领涨特征
        if ret_20d > 0.05 and change_pct > 0.02:
            score = 0.70
            detail["pattern"] = "中期强势+今日领涨"
        elif ret_10d > 0.03 and change_pct > 0.01:
            score = 0.62
            detail["pattern"] = "短期强势延续"
        elif ret_20d < -0.05 and change_pct > 0.03:
            # 超跌后强势反弹 → 可能反转
            score = 0.68
            detail["pattern"] = "超跌强势反弹"
        elif ret_5d < -0.03 and change_pct > 0.02:
            score = 0.60
            detail["pattern"] = "短线修复"
        elif ret_20d > 0.15 and change_pct < 0:
            score = 0.35
            detail["pattern"] = "高位回调"
        elif change_pct > 0.015:
            score = 0.55
            detail["pattern"] = "今日偏强"

        return max(0.0, min(1.0, score)), detail
