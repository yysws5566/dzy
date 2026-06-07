#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强因子库 v1.0 — TickFlow Pro 专用
=====================================
覆盖 5 大缺失因子类别，所有计算基于 TickFlow Pro 日线 / 分时数据。

因子列表:
  因子 A — RSI(14) 超卖反弹识别
  因子 B — KDJ 低位金叉检测
  因子 C — 威廉 %R 超卖信号
  因子 D — 主力资金流向（分时量价推算）
  因子 E — VWAP 偏离度
  因子 F — 板块相对强度
  因子 G — 大单活跃度（分时量能分布）
  因子 H — 尾盘量价共振

用法:
  from enhanced_factors import EnhancedFactorEngine
  engine = EnhancedFactorEngine(tf_client)
  scores = engine.compute_all(symbols, daily_klines, intraday_data)
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

import numpy as np
import pandas as pd


# ============================================================
# 因子计算工具函数（纯 numpy/pandas，不依赖 API）
# ============================================================

def calc_rsi(closes: np.ndarray, period: int = 14) -> float:
    """
    计算 RSI(14) — 相对强弱指标
    返回: 0-100 之间的值
      > 70: 超买
      < 30: 超卖（看涨信号）
    """
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period+1):])
    gains = np.maximum(deltas, 0)
    losses = np.maximum(-deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def calc_kdj(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
             n: int = 9) -> Tuple[float, float, float]:
    """
    计算 KDJ 指标
    返回: (K, D, J) 三个值
      K/D < 20: 超卖区
      K 上穿 D 且处于超卖区 → 金叉买入信号
      J < 0: 极度超卖
    """
    if len(closes) < n + 1:
        return 50.0, 50.0, 50.0

    # 使用最近 n 根 K 线计算 RSV
    hh = np.max(highs[-n:])
    ll = np.min(lows[-n:])
    if hh == ll:
        rsv = 50.0
    else:
        rsv = float((closes[-1] - ll) / (hh - ll) * 100)

    # 简化为单次 RSV 的近似 KDJ（足够用于筛选）
    # 标准 KDJ 需要递推，这里用 SMA 近似
    k = float(2/3 * 50 + 1/3 * rsv)   # K = 2/3*prev_K + 1/3*RSV, prev_K 初始=50
    d = float(2/3 * 50 + 1/3 * k)     # D = 2/3*prev_D + 1/3*K
    j = float(3 * k - 2 * d)           # J = 3K - 2D

    return k, d, j


def calc_williams_r(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                    period: int = 14) -> float:
    """
    计算威廉 %R 指标
    返回: -100 ~ 0 之间的值
      > -20: 超买
      < -80: 超卖（看涨信号）
    """
    if len(closes) < period:
        return -50.0
    hh = np.max(highs[-period:])
    ll = np.min(lows[-period:])
    if hh == ll:
        return -50.0
    return float((hh - closes[-1]) / (hh - ll) * -100)


def calc_ma(closes: np.ndarray, period: int) -> float:
    """简单移动平均"""
    if len(closes) < period:
        return float(np.mean(closes))
    return float(np.mean(closes[-period:]))


def calc_vol_ratio(volumes: np.ndarray, period: int = 5) -> float:
    """量比：今日量 / 近N日均量"""
    if len(volumes) < period + 1:
        return 1.0
    avg = np.mean(volumes[-(period+1):-1])
    if avg == 0:
        return 1.0
    return float(volumes[-1] / avg)


def calc_vwap(intra_prices: np.ndarray, intra_volumes: np.ndarray) -> Tuple[float, float]:
    """
    计算 VWAP 及当前价格偏离度
    返回: (vwap_price, deviation_pct)
      deviation_pct > 0: 价格高于 VWAP（强势）
      deviation_pct < 0: 价格低于 VWAP（弱势）
    """
    if len(intra_prices) < 1 or len(intra_volumes) < 1:
        return 0.0, 0.0
    if intra_volumes.sum() == 0:
        return float(np.mean(intra_prices)), 0.0

    vwap = float(np.sum(intra_prices * intra_volumes) / intra_volumes.sum())
    current = intra_prices[-1]
    if vwap == 0:
        return vwap, 0.0
    deviation = float((current - vwap) / vwap * 100)
    return vwap, deviation


def calc_money_flow(intra_opens, intra_closes, intra_volumes,
                    intra_highs=None, intra_lows=None) -> Dict[str, float]:
    """
    从分时数据推算主力资金流向

    原理（Chaikin Money Flow 简化版）:
      - 每根 K 线的资金流 = ((close-open)/(high-low) + 0.5) * volume * price
      - 正值 = 流入，负值 = 流出
      - 大单占比通过量能集中度判断

    返回字典:
      - net_flow: 净流入金额（万元）
      - flow_ratio: 流入/流出比率
      - big_order_ratio: 大单占比估计
      - score: 0-100 综合评分
    """
    n = len(intra_closes)
    if n < 6:
        return {"net_flow": 0, "flow_ratio": 1.0, "big_order_ratio": 0, "score": 50}

    ops = np.asarray(intra_opens, dtype=float)
    cls = np.asarray(intra_closes, dtype=float)
    vols = np.asarray(intra_volumes, dtype=float)
    highs = np.asarray(intra_highs, dtype=float) if intra_highs is not None else None
    lows = np.asarray(intra_lows, dtype=float) if intra_lows is not None else None

    # 每根K线的资金流方向
    flows = []
    for i in range(n):
        o, c, v = ops[i], cls[i], vols[i]
        if highs is not None and lows is not None:
            h, l = highs[i], lows[i]
            if h > l:
                mf_multiplier = ((c - o) / (h - l)) + 0.5
            else:
                mf_multiplier = 0.5
        else:
            if o > 0:
                mf_multiplier = ((c - o) / o) + 0.5
            else:
                mf_multiplier = 0.5
        mf_multiplier = max(-1, min(2, mf_multiplier))  # 钳制
        price = c if c > 0 else o
        flows.append(mf_multiplier * v * price)

    flows = np.array(flows)
    total_flow = float(np.sum(flows))
    positive_flow = float(np.sum(flows[flows > 0])) if np.any(flows > 0) else 0
    negative_flow = float(abs(np.sum(flows[flows < 0]))) if np.any(flows < 0) else 1

    flow_ratio = positive_flow / max(negative_flow, 1)
    net_flow_wan = total_flow / 10000  # 万元

    # 大单占比：量能集中的K线占总量的比例
    if len(vols) >= 3:
        vol_threshold = np.percentile(vols, 70)
        big_bars = vols >= vol_threshold
        big_order_ratio = float(vols[big_bars].sum() / max(vols.sum(), 1))
    else:
        big_order_ratio = 0.0

    # 综合评分 0-100
    score = 50.0
    if flow_ratio > 2.0:
        score += min(25, int((flow_ratio - 1) * 15))
    elif flow_ratio > 1.2:
        score += min(15, int((flow_ratio - 1) * 20))
    elif flow_ratio < 0.6:
        score -= min(25, int((1 - flow_ratio) * 20))
    if big_order_ratio > 0.3:
        score += min(15, int((big_order_ratio - 0.2) * 30))
    if net_flow_wan > 500:
        score += min(15, int(net_flow_wan / 2000 * 10))

    return {
        "net_flow_wan": round(net_flow_wan, 1),
        "flow_ratio": round(flow_ratio, 2),
        "big_order_ratio": round(big_order_ratio, 3),
        "score": round(min(100, max(0, score)), 1)
    }


def calc_sector_strength(stock_change: float, sector_changes: List[float]) -> Dict[str, float]:
    """
    计算个股在板块中的相对强度

    Args:
      stock_change: 个股涨跌幅 %
      sector_changes: 同板块所有股票涨跌幅列表

    返回:
      - rank_pct: 在板块中的百分位排名（越高越好）
      - relative_strength: 相对强度（个股涨幅 - 板块均值）
      - sector_mean: 板块平均涨幅
    """
    if not sector_changes or len(sector_changes) < 3:
        return {"rank_pct": 50, "relative_strength": 0, "sector_mean": 0}

    mean_sector = float(np.mean(sector_changes))
    relative = stock_change - mean_sector
    rank = float(np.sum(np.array(sector_changes) < stock_change)) / len(sector_changes) * 100

    return {
        "rank_pct": round(rank, 1),
        "relative_strength": round(relative, 2),
        "sector_mean": round(mean_sector, 2)
    }


def calc_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    """
    计算 ATR(14) — 真实波幅（Average True Range）

    True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    ATR = TR 的 N 日简单移动平均

    用于动态止损：
      stop_loss = max(入场价 - ATR×1.5, 入场价 × 0.97)
      波动大 → ATR 大 → 止损宽（不被噪音扫出）
      波动小 → ATR 小 → 止损窄（保护更紧）
      硬止损 -3%（防止极端情况）
    """
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return float(np.mean(np.abs(np.diff(closes[-period:])))) if len(closes) >= period else 0.01

    trs = []
    for i in range(1, len(closes)):
        h, l, pc = highs[i], lows[i], closes[i-1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)

    if len(trs) < period:
        return float(np.mean(trs))

    return float(np.mean(trs[-period:]))


def calc_dynamic_stop(entry_price: float, atr: float,
                       atr_mult: float = 1.5, hard_stop_pct: float = 0.03) -> Dict[str, float]:
    """
    计算动态止损价

    公式: stop = max(entry - ATR × atr_mult, entry × (1 - hard_stop_pct))

    Args:
      entry_price: 入场价格
      atr: ATR(14) 值
      atr_mult: ATR 倍数（默认 1.5）
      hard_stop_pct: 硬止损百分比（默认 3%）

    Returns:
      {"stop_price": 止损价, "loss_pct": 最大亏损%, "stop_type": "ATR"|"硬止损"}
    """
    atr_stop = entry_price - atr * atr_mult
    hard_stop = entry_price * (1 - hard_stop_pct)
    stop_price = max(atr_stop, hard_stop)

    loss_pct = (entry_price - stop_price) / entry_price * 100

    if stop_price == hard_stop:
        stop_type = "硬止损(-3%)"
    else:
        stop_type = f"ATR动态(-{loss_pct:.1f}%)"

    return {
        "stop_price": round(stop_price, 2),
        "loss_pct": round(loss_pct, 2),
        "stop_type": stop_type,
        "atr_val": round(atr, 3),
    }
    """
    检测尾盘量价共振

    尾盘定义为最后 6 根 5 分钟 K 线（14:30-15:00）
    返回:
      - price_trend: 尾盘价格趋势（正=拉升，负=跳水）
      - vol_trend: 尾盘量能趋势
      - resonance: 是否量价共振（同向）
    """
    if len(tail_prices) < 4:
        return {"price_trend": 0, "vol_trend": 0, "resonance": False, "score": 0}

    tp = np.asarray(tail_prices, dtype=float)
    tv = np.asarray(tail_volumes, dtype=float)

    # 线性趋势
    x = np.arange(len(tp))
    if len(tp) > 1:
        pt = float(np.polyfit(x, tp, 1)[0])    # 价格斜率
        vt = float(np.polyfit(x, tv, 1)[0])    # 量能斜率
    else:
        pt, vt = 0.0, 0.0

    # 共振：价涨量增 或 价跌量缩
    resonance = (pt > 0 and vt > 0) or (pt < 0 and vt < 0)

    score = 0
    if resonance:
        if pt > 0 and vt > 0:
            score = min(30, int(pt * 1000 + vt * 0.001))
        else:
            score = -min(30, int(abs(pt) * 1000))

    return {
        "price_trend": round(float(pt), 4),
        "vol_trend": round(float(vt), 4),
        "resonance": resonance,
        "score": score
    }


# ============================================================
# 增强因子引擎（基于 TickFlow Pro）
# ============================================================
class EnhancedFactorEngine:
    """增强因子计算引擎 — 所有数据来自 TickFlow Pro"""

    def __init__(self, tf_client=None):
        """
        Args:
          tf_client: TickFlow 客户端实例（TFClient 或 TickFlow 原生）
        """
        self._tf = tf_client
        self._cache: Dict[str, Any] = {}
        self._sector_cache: Dict[str, List[str]] = defaultdict(list)

    # ── 因子 A: RSI 超卖反弹 ──
    def factor_rsi(self, closes: np.ndarray, period: int = 14) -> Dict[str, Any]:
        """RSI 超卖反弹因子，返回 0-100 评分"""
        rsi = calc_rsi(closes, period)
        # 超卖区（RSI < 35）→ 高分，暗示反弹
        if rsi < 25:
            score = 95
        elif rsi < 30:
            score = 85
        elif rsi < 35:
            score = 70
        elif rsi < 40:
            score = 55
        elif rsi < 50:
            score = 45
        elif rsi < 60:
            score = 35
        elif rsi < 70:
            score = 20
        else:
            score = 5  # 超买，不宜追
        return {"rsi": round(rsi, 1), "score": score, "zone": "超卖" if rsi < 35 else ("超买" if rsi > 70 else "中性")}

    # ── 因子 B: KDJ 低位金叉 ──
    def factor_kdj(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> Dict[str, Any]:
        """KDJ 低位金叉因子"""
        k, d, j = calc_kdj(highs, lows, closes)

        # 评分
        if k < 20 and j < 0:
            score = 95        # 极度超卖，最强信号
        elif k < 25 and k > d:
            score = 85        # 超卖区金叉
        elif k < 35 and k > d:
            score = 70        # 接近超卖区金叉
        elif k < 50 and k > d:
            score = 55
        elif k > d:
            score = 40
        elif k > 80:
            score = 10        # 超买区，不宜
        else:
            score = 30

        golden_cross = bool(k > d and k < 40)  # 低位金叉
        return {"K": round(k, 1), "D": round(d, 1), "J": round(j, 1),
                "golden_cross": golden_cross, "score": score}

    # ── 因子 C: 威廉 %R 超卖 ──
    def factor_williams_r(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> Dict[str, Any]:
        """威廉 %R 超卖因子"""
        wr = calc_williams_r(highs, lows, closes)

        if wr < -85:
            score = 90        # 深度超卖
        elif wr < -80:
            score = 80
        elif wr < -70:
            score = 65
        elif wr < -50:
            score = 50
        elif wr > -20:
            score = 10        # 超买
        else:
            score = 35

        return {"williams_r": round(wr, 1), "score": score,
                "zone": "深度超卖" if wr < -80 else ("超买" if wr > -20 else "中性")}

    # ── 因子 D: 主力资金流向 ──
    def factor_money_flow(self, intraday_data: pd.DataFrame) -> Dict[str, Any]:
        """主力资金流向因子（基于分时数据）"""
        if intraday_data is None or len(intraday_data) < 6:
            return {"net_flow_wan": 0, "flow_ratio": 1.0, "big_order_ratio": 0, "score": 50}

        opens = intraday_data["open"].astype(float).values
        closes = intraday_data["close"].astype(float).values
        volumes = intraday_data["volume"].astype(float).values
        highs = intraday_data.get("high")
        lows = intraday_data.get("low")
        h_arr = highs.astype(float).values if highs is not None else None
        l_arr = lows.astype(float).values if lows is not None else None

        result = calc_money_flow(opens, closes, volumes, h_arr, l_arr)
        return result

    # ── 因子 E: VWAP 偏离 ──
    def factor_vwap(self, intraday_data: pd.DataFrame) -> Dict[str, Any]:
        """VWAP 偏离度因子"""
        if intraday_data is None or len(intraday_data) < 4:
            return {"vwap": 0, "deviation_pct": 0, "score": 50}

        prices = intraday_data["close"].astype(float).values
        volumes = intraday_data["volume"].astype(float).values

        # 用 (high+low+close)/3 作为典型价格更准确
        if "high" in intraday_data.columns and "low" in intraday_data.columns:
            typical = (intraday_data["high"].astype(float) +
                       intraday_data["low"].astype(float) +
                       intraday_data["close"].astype(float)) / 3
            vwap, dev = calc_vwap(typical.values, volumes)
        else:
            vwap, dev = calc_vwap(prices, volumes)

        # 评分：小幅偏离 VWAP 上方为最佳（涨幅温和 + 资金认可）
        if 0 < dev <= 1.5:
            score = 85        # 温和强势
        elif 1.5 < dev <= 3:
            score = 70
        elif dev > 3:
            score = 45        # 偏离过大，可能追高
        elif -1 <= dev <= 0:
            score = 60        # 略低于 VWAP，可以接受
        elif -3 <= dev < -1:
            score = 40
        else:
            score = 20        # 大幅低于 VWAP，弱势

        return {"vwap": round(vwap, 2), "deviation_pct": round(dev, 2), "score": score}

    # ── 因子 F: 板块相对强度 ──
    def factor_sector_strength(self, symbol: str, stock_change: float,
                                all_quotes: pd.DataFrame) -> Dict[str, Any]:
        """板块相对强度因子"""
        # 从 symbol 推断板块（简单实现：根据代码前缀）
        # 更精确的做法是用 TickFlow 的板块分类
        sector_changes = []
        sym_col = next((c for c in all_quotes.columns if c in ("symbol", "code")), None)
        chg_col = next((c for c in all_quotes.columns if "change" in c.lower()), None)

        if sym_col and chg_col:
            # 找同板块（前3位代码相近的股票）
            prefix = symbol[:3] if len(symbol) >= 3 else ""
            same_sector = all_quotes[all_quotes[sym_col].astype(str).str.startswith(prefix)]
            sector_changes = same_sector[chg_col].astype(float).dropna().tolist()

        return calc_sector_strength(stock_change, sector_changes)

    # ── 因子 G: 大单活跃度 ──
    def factor_big_order(self, intraday_data: pd.DataFrame) -> Dict[str, Any]:
        """大单活跃度因子"""
        if intraday_data is None or len(intraday_data) < 6:
            return {"big_order_ratio": 0, "score": 50, "description": "数据不足"}

        volumes = intraday_data["volume"].astype(float).values
        prices = intraday_data["close"].astype(float).values

        # 大单 = 量能超过均值 1.5 倍标准差
        mean_v = np.mean(volumes)
        std_v = np.std(volumes) if len(volumes) > 1 else 1
        threshold = mean_v + 1.5 * std_v
        big_bars = volumes >= threshold
        big_ratio = float(volumes[big_bars].sum() / max(volumes.sum(), 1))

        # 大单的方向（成交额加权涨跌）
        big_direction = 0.0
        if big_ratio > 0 and len(prices) > 1:
            diffs = np.diff(prices) / np.maximum(prices[:-1], 0.01)
            try:
                big_direction = float(np.average(diffs, weights=volumes[1:]))
            except (TypeError, ValueError):
                big_direction = float(np.mean(diffs)) if len(diffs) > 0 else 0.0

        # 评分
        if big_ratio > 0.3 and big_direction > 0:
            score = 90        # 大单活跃且方向向上
        elif big_ratio > 0.25 and big_direction > 0:
            score = 75
        elif big_ratio > 0.2:
            score = 60
        elif big_ratio > 0.1:
            score = 45
        else:
            score = 30

        return {"big_order_ratio": round(big_ratio, 3),
                "big_direction": round(float(big_direction) * 100, 2),
                "score": score}

    # ── 因子 H: 尾盘量价共振 ──
    def factor_tail_resonance(self, intraday_data: pd.DataFrame) -> Dict[str, Any]:
        """尾盘量价共振因子"""
        if intraday_data is None or len(intraday_data) < 12:
            return {"price_trend": 0, "vol_trend": 0, "resonance": False, "score": 50}

        # 取最后 6 根 K 线（尾盘 14:30-15:00 的 5 分钟线）
        tail = intraday_data.iloc[-6:]
        prices = tail["close"].astype(float).values
        volumes = tail["volume"].astype(float).values

        result = detect_tail_resonance(prices, volumes)
        result["score"] = max(0, min(100, result["score"] + 50))
        return result

    # ── 综合计算（批量） ──
    def compute_for_stock(self, symbol: str, daily_kline: pd.DataFrame,
                          intraday_data: Optional[pd.DataFrame] = None,
                          all_quotes: Optional[pd.DataFrame] = None,
                          stock_change: float = 0.0) -> Dict[str, Any]:
        """
        对单只股票计算所有增强因子

        Args:
          symbol: 股票代码
          daily_kline: 日线 DataFrame（至少 20 根）
          intraday_data: 分时 5 分钟 DataFrame（可选，用于资金流/VWAP/大单）
          all_quotes: 全市场行情 DataFrame（可选，用于板块强度）
          stock_change: 当日涨跌幅 %

        Returns:
          包含所有因子得分的字典
        """
        closes = daily_kline["close"].astype(float).values
        highs = daily_kline["high"].astype(float).values
        lows = daily_kline["low"].astype(float).values
        volumes = daily_kline["volume"].astype(float).values

        factors = {}

        # 因子 A: RSI
        factors["rsi"] = self.factor_rsi(closes)

        # 因子 B: KDJ
        factors["kdj"] = self.factor_kdj(highs, lows, closes)

        # 因子 C: 威廉 %R
        factors["williams_r"] = self.factor_williams_r(highs, lows, closes)

        # 因子 D: 主力资金流
        if intraday_data is not None and len(intraday_data) >= 6:
            factors["money_flow"] = self.factor_money_flow(intraday_data)
        else:
            factors["money_flow"] = {"score": 50, "flow_ratio": 1.0, "note": "无分时数据"}

        # 因子 E: VWAP 偏离
        if intraday_data is not None and len(intraday_data) >= 4:
            factors["vwap"] = self.factor_vwap(intraday_data)
        else:
            factors["vwap"] = {"score": 50, "deviation_pct": 0, "note": "无分时数据"}

        # 因子 F: 板块强度
        if all_quotes is not None:
            factors["sector"] = self.factor_sector_strength(symbol, stock_change, all_quotes)
        else:
            factors["sector"] = {"rank_pct": 50, "relative_strength": 0, "sector_mean": 0}

        # 因子 G: 大单活跃度
        if intraday_data is not None and len(intraday_data) >= 6:
            factors["big_order"] = self.factor_big_order(intraday_data)
        else:
            factors["big_order"] = {"score": 50, "big_order_ratio": 0}

        # 因子 H: 尾盘共振
        if intraday_data is not None and len(intraday_data) >= 12:
            factors["tail_resonance"] = self.factor_tail_resonance(intraday_data)
        else:
            factors["tail_resonance"] = {"score": 50, "resonance": False}

        # 综合加权评分
        weights = {
            "rsi": 0.18,
            "kdj": 0.18,
            "williams_r": 0.10,
            "money_flow": 0.20,
            "vwap": 0.10,
            "sector": 0.08,
            "big_order": 0.10,
            "tail_resonance": 0.06,
        }
        total = sum(factors[k]["score"] * weights[k] for k in weights)
        factors["composite_score"] = round(total, 1)

        return factors


# ============================================================
# 快速测试
# ============================================================
if __name__ == "__main__":
    print("增强因子库 v1.0 — TickFlow Pro")
    print("=" * 40)

    # 模拟数据测试
    np.random.seed(42)
    fake_closes = np.cumsum(np.random.randn(60) * 0.5) + 10
    fake_highs = fake_closes + np.abs(np.random.randn(60) * 0.3)
    fake_lows = fake_closes - np.abs(np.random.randn(60) * 0.3)
    fake_volumes = np.abs(np.random.randn(60) * 1000) + 5000

    engine = EnhancedFactorEngine()

    print(f"\n因子 A - RSI: {engine.factor_rsi(fake_closes)}")
    print(f"\n因子 B - KDJ: {engine.factor_kdj(fake_highs, fake_lows, fake_closes)}")
    print(f"\n因子 C - 威廉%R: {engine.factor_williams_r(fake_highs, fake_lows, fake_closes)}")

    # 模拟分时数据
    intra_closes = np.cumsum(np.random.randn(48) * 0.05) + 10
    intra_volumes = np.abs(np.random.randn(48) * 500) + 2000
    intra_opens = intra_closes - np.random.randn(48) * 0.03
    fake_intra = pd.DataFrame({
        "open": intra_opens,
        "high": intra_closes + 0.1,
        "low": intra_closes - 0.1,
        "close": intra_closes,
        "volume": intra_volumes
    })

    print(f"\n因子 D - 资金流: {engine.factor_money_flow(fake_intra)}")
    print(f"\n因子 E - VWAP: {engine.factor_vwap(fake_intra)}")
    print(f"\n因子 G - 大单: {engine.factor_big_order(fake_intra)}")
    print(f"\n因子 H - 尾盘共振: {engine.factor_tail_resonance(fake_intra)}")

    print("\n" + "=" * 40)
    print("增强因子库就绪 ✓")
