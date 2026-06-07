#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
尾盘异动猎手 v1.0 — Tail Anomaly Hunter (TickFlow Pro)
=========================================================
专为小资金 T+1 高胜率设计，核心思路：避开量化拥挤因子，聚焦尾盘微结构。

为什么这套因子不容易被量化挖烂？

  F1 尾盘脉冲质量      — 14:30-14:55 1分钟K线斜率+R²拟合度
  F2 集合竞价异常      — 14:57-15:00 竞价量/日内均量比（多数扫描器跳过的窗口）
  F3 连续碎步小阳      — 3-5天缩量小阳线（Wyckoff吸筹形态，标准量化难编码）
  F4 日内V型反转度    — Open→Low→Close 路径形状（非 OHLCV 简单计算）
  F5 分时均线缠绕度    — 价格绕VWAP的震荡密度（平衡→即将方向选择）
  F6 盘中试盘信号      — 小脉冲→迅速回落→横盘（测试抛压）
  F7 振幅压缩爆发      — 日内振幅收缩至 N 日低点（弹簧效应）
  F8 隔夜跳空方向偏差  — 历史同期跳空方向的统计偏向

机构量化的盲区：
  - 多数使用5分钟K线 → 我们看1分钟 + 集合竞价
  - 多数用日频因子 → 我们看尾盘30分钟微结构
  - 多数资金量大，尾盘建仓冲击成本高 → 小资金无此限制
  - 多数模型要求全市场扫描 → 我们精选形态匹配的标的

运行时间: 每日 14:50（收盘前10分钟，集合竞价前）
用法:
  python tail_anomaly_hunter_v1.py              # 正常运行
  python tail_anomaly_hunter_v1.py --debug      # 调试模式
  python tail_anomaly_hunter_v1.py --top 10     # 输出前N
"""

import os, sys, json, time, argparse, math
from datetime import datetime, date, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# 导入增强因子库中的工具函数
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from enhanced_factors import (
    EnhancedFactorEngine, calc_rsi, calc_kdj, calc_ma, calc_vol_ratio,
    calc_atr, calc_dynamic_stop
)

# ============================================================
# 配置
# ============================================================
CONFIG = {
    # ── 标的筛选 ──
    "price_min": 5,
    "price_max": 60,
    "float_cap_min": 10,       # 最小流通市值（亿）
    "float_cap_max": 200,      # 最大流通市值（亿）—— 避开大盘股
    "turnover_min": 1.5,
    "turnover_max": 12,
    "amount_min": 3000,        # 最小成交额（万元）

    # ── 尾盘微结构参数 ──
    "tail_bars": 25,           # 尾盘分析K线数（14:30-14:55的25根1分钟线）
    "tail_pulse_min_r2": 0.75, # 尾盘脉冲线性拟合度R²最小阈值
    "tail_slope_min": 0.0003,  # 尾盘价格斜率最小阈值

    # ── 集合竞价参数 ──
    "auction_vol_ratio_min": 1.5,  # 竞价量/最后5分钟均量比
    "auction_price_impact": 0.003, # 竞价价格影响阈值

    # ── 碎步小阳参数 ──
    "tiny_yang_days": 3,        # 连续碎步小阳天数
    "tiny_yang_max_pct": 1.5,   # 单日最大涨幅%
    "tiny_yang_vol_shrink": 0.8, # 量缩比例（当日量/前日均量）

    # ── 日内V型反转 ──
    "v_shape_min_recovery": 0.6,  # 从最低点恢复的比例

    # ── 振幅压缩 ──
    "amp_compress_days": 5,       # 比较天数
    "amp_compress_ratio": 0.6,    # 当前振幅/N日均振幅比

    # ── 评分权重 ──
    "weight_pulse": 0.22,
    "weight_auction": 0.18,
    "weight_tiny_yang": 0.18,
    "weight_v_shape": 0.12,
    "weight_vwap_tight": 0.10,
    "weight_test_sell": 0.08,
    "weight_amp_squeeze": 0.07,
    "weight_gap_bias": 0.05,

    # ── 输出 ──
    "min_score": 55,
    "top_output": 8,
    "output_dir": os.path.join(os.path.dirname(os.path.abspath(__file__)), "tail_hunter_results"),
}


# ============================================================
# TickFlow Pro 客户端（支持1分钟K线）
# ============================================================
class TFClient:
    def __init__(self):
        from tickflow import TickFlow
        key = os.environ.get("TICKFLOW_API_KEY", "")
        if not key:
            raise RuntimeError("TICKFLOW_API_KEY 未设置!")
        self._tf = TickFlow(api_key=key)
        self._rl_map = {}
        self._cnt = defaultdict(int)

    def _rl(self, ep: str, rpm: int):
        now = time.time()
        last = self._rl_map.get(ep, 0)
        gap = 60.0 / rpm
        if now - last < gap:
            time.sleep(gap - (now - last) + 0.05)
        self._rl_map[ep] = time.time()
        self._cnt[ep] += 1

    def get_all_quotes(self) -> pd.DataFrame:
        self._rl("quotes", 60)
        return self._tf.quotes.get(universes="CN_Equity_A", as_dataframe=True)

    def get_klines_batch(self, syms: List[str], count=30) -> dict:
        result = {}
        for i in range(0, len(syms), 100):
            self._rl("kl", 60)
            try:
                result.update(self._tf.klines.batch(
                    syms[i:i+100], period="1d", count=count,
                    adjust="forward", as_dataframe=True))
            except Exception:
                pass
        return result

    def get_intraday_1m(self, syms: List[str], count=80) -> dict:
        """获取1分钟分时K线（尾盘微结构分析用）"""
        result = {}
        for s in syms[:50]:  # 限制数量（1分钟K线数据量大）
            try:
                self._rl("intra_1m", 30)
                result[s] = self._tf.klines.intraday(
                    s, period="1m", count=count, as_dataframe=True)
            except Exception:
                pass
        return result

    def stats(self):
        print(f"\n  TickFlow API: {dict(self._cnt)}")


# ============================================================
# F1: 尾盘脉冲质量（14:30-14:55 1分钟K线）
# ============================================================
def factor_tail_pulse(intra_1m: pd.DataFrame) -> Dict:
    """
    分析14:30-14:55尾盘的脉冲质量和方向。

    为什么不是拥挤因子：
    - 大多数量化用5分钟K线，1分钟颗粒度太细被忽略
    - 我们不只是看涨跌，而是看"涨的质量"（R²拟合度）
    - 低R²=随机波动，高R²=有意图的拉升
    """
    if intra_1m is None or len(intra_1m) < 20:
        return {"score": 50, "slope": 0, "r2": 0, "direction": "数据不足"}

    # 取最后25根1分钟K线（14:30-14:55）
    tail = intra_1m.iloc[-25:] if len(intra_1m) >= 25 else intra_1m
    closes = tail["close"].astype(float).values
    volumes = tail["volume"].astype(float).values

    if len(closes) < 10:
        return {"score": 50, "slope": 0, "r2": 0, "direction": "数据不足"}

    # 线性回归拟合
    x = np.arange(len(closes))
    slope, intercept = np.polyfit(x, closes, 1)
    y_pred = slope * x + intercept
    ss_res = np.sum((closes - y_pred) ** 2)
    ss_tot = np.sum((closes - np.mean(closes)) ** 2)
    r2 = 1 - ss_res / max(ss_tot, 1e-10)

    # 量能配合度
    vol_slope = np.polyfit(x, volumes, 1)[0]

    # 评分逻辑
    score = 50
    direction = "横盘"

    if slope > 0.0005 and r2 > 0.8:        # 稳健拉升
        score = 90; direction = "稳健拉升"
    elif slope > 0.0003 and r2 > 0.7:
        score = 80; direction = "温和拉升"
    elif slope > 0.0001 and r2 > 0.6:
        score = 65; direction = "微幅上行"
    elif slope < -0.0005 and r2 > 0.8:     # 稳健下跌（可能是低吸机会）
        score = 60; direction = "尾盘打压(低吸)"
    elif slope < -0.0002 and r2 > 0.6:
        score = 45; direction = "尾盘走弱"
    elif abs(slope) < 0.0001:
        score = 55; direction = "横盘整理"
    else:
        score = 40; direction = "方向不确定"

    # 量价共振加分
    if slope > 0 and vol_slope > 0:  # 价涨量增
        score = min(95, score + 5)
    elif slope < 0 and vol_slope < 0:  # 价跌量缩（正常回调）
        score = min(70, score + 3)

    return {
        "score": round(score, 1),
        "slope": round(float(slope), 6),
        "r2": round(float(r2), 3),
        "vol_trend": "放量" if vol_slope > 0 else "缩量",
        "direction": direction,
    }


# ============================================================
# F2: 集合竞价异常检测
# ============================================================
def factor_auction_anomaly(intra_1m: pd.DataFrame, daily_vol: float) -> Dict:
    """
    检测14:57-15:00集合竞价阶段的异常放量。

    为什么不是拥挤因子：
    - 绝大多数扫描器使用日内5分钟K线，集合竞价是"盲区"
    - 最后3分钟的竞价行为反映机构隔夜持仓的真实意图
    - 个人小资金可以在此窗口无冲击成本地跟进
    """
    if intra_1m is None or len(intra_1m) < 10:
        return {"score": 50, "ratio": 1.0, "signal": "数据不足"}

    # 最后3根1分钟K线（14:57-15:00）
    last_bars = intra_1m.iloc[-3:]
    auction_vol = last_bars["volume"].astype(float).sum()

    # 前5根K线的均量（14:52-14:57）
    prev_bars = intra_1m.iloc[-8:-3] if len(intra_1m) >= 8 else intra_1m.iloc[:-3]
    avg_vol = prev_bars["volume"].astype(float).mean() if len(prev_bars) > 0 else auction_vol

    if avg_vol == 0:
        return {"score": 50, "ratio": 1.0, "signal": "无数据"}

    ratio = auction_vol / (avg_vol * 3)  # 3分钟的量和前面5分钟均量比较

    # 最后1分钟的价格方向
    last_close = last_bars["close"].astype(float)
    price_change = (last_close.iloc[-1] - last_close.iloc[0]) / max(last_close.iloc[0], 0.01) if len(last_close) > 1 else 0

    # 评分：竞价放量 + 价格上涨 = 强信号
    score = 50
    signal = "正常"

    if ratio > 2.5 and price_change > 0.002:
        score = 95; signal = "竞价抢筹"
    elif ratio > 2.0 and price_change > 0.001:
        score = 85; signal = "竞价活跃偏多"
    elif ratio > 1.8 and price_change > 0:
        score = 75; signal = "竞价偏多"
    elif ratio > 1.5 and price_change > -0.001:
        score = 65; signal = "竞价略多"
    elif ratio > 1.5 and price_change < -0.002:
        score = 35; signal = "竞价偏空"
    elif ratio < 1.0:
        score = 45; signal = "竞价冷清"
    else:
        score = 50; signal = "竞价正常"

    return {
        "score": round(score, 1),
        "ratio": round(float(ratio), 2),
        "price_impact": round(float(price_change), 4),
        "signal": signal,
    }


# ============================================================
# F3: 连续碎步小阳（Wyckoff 吸筹形态）
# ============================================================
def factor_tiny_yang_steps(daily_kline: pd.DataFrame) -> Dict:
    """
    检测连续3-5天的缩量小阳线——典型的机构暗中介入形态。

    为什么不是拥挤因子：
    - 标准量化模型关注"大阳线"、"放量"，忽略小阳碎步
    - 这种形态需要模式识别（连续N天满足3个条件），不是简单阈值
    - Wyckoff吸筹是经典技术分析概念，但很少被编码为量化因子
    """
    if daily_kline is None or len(daily_kline) < 10:
        return {"score": 50, "count": 0, "pattern": "数据不足"}

    closes = daily_kline["close"].astype(float)
    opens = daily_kline["open"].astype(float)
    volumes = daily_kline["volume"].astype(float)

    # 检查最近5天
    consecutive = 0
    max_consecutive = 0
    vol_shrink_total = 1.0

    for i in range(len(closes) - 6, len(closes)):
        if i <= 0:
            continue
        day_chg = (closes.iloc[i] - closes.iloc[i-1]) / closes.iloc[i-1]
        is_yang = closes.iloc[i] > opens.iloc[i]
        is_tiny = 0 < day_chg <= 0.015  # 0~1.5% 小阳
        # 是否缩量
        if i >= 2:
            prev_vol = volumes.iloc[i-2:i].mean() if volumes.iloc[i-2:i].mean() > 0 else volumes.iloc[i]
            vol_shrink = volumes.iloc[i] / prev_vol
        else:
            vol_shrink = 0.8

        if is_yang and is_tiny and vol_shrink < 0.85:
            consecutive += 1
            max_consecutive = max(max_consecutive, consecutive)
            vol_shrink_total *= vol_shrink
        else:
            consecutive = 0

    # 评分
    score = 50
    pattern = "无形态"

    if max_consecutive >= 5:
        score = 92; pattern = f"连续{max_consecutive}日碎步吸筹"
    elif max_consecutive >= 4:
        score = 82; pattern = f"连续{max_consecutive}日碎步吸筹"
    elif max_consecutive >= 3:
        score = 72; pattern = f"连续{max_consecutive}日碎步小阳"
    elif max_consecutive >= 2:
        score = 58; pattern = f"连续{max_consecutive}日小阳"
    else:
        score = 42

    return {
        "score": round(score, 1),
        "count": max_consecutive,
        "pattern": pattern,
    }


# ============================================================
# F4: 日内V型反转度
# ============================================================
def factor_v_shape_reversal(intra_1m: pd.DataFrame) -> Dict:
    """
    检测日内V型反转的完成度和质量。
    V型反转 = 开盘后下跌 → 日内低点 → 尾盘回升

    为什么不是拥挤因子：
    - 标准指标只看 "下影线长度"，我们看整个日内路径
    - V型+尾盘放量 = 更可靠的底部信号
    """
    if intra_1m is None or len(intra_1m) < 20:
        return {"score": 50, "recovery": 0, "shape": "数据不足"}

    closes = intra_1m["close"].astype(float).values
    volumes = intra_1m["volume"].astype(float).values
    opens = intra_1m["open"].astype(float).values

    day_open = opens[0]
    day_low = closes.min()
    day_low_idx = closes.argmin()
    current = closes[-1]

    # 开盘到最低点的跌幅
    drop_pct = (day_open - day_low) / max(day_open, 0.01)

    # 从最低点到当前价格的恢复比例
    if drop_pct > 0.005:  # 有显著下跌
        recovery = (current - day_low) / max(day_open - day_low, 0.01)
    else:
        recovery = 0.5  # 无明显V型

    # 尾盘量能（最低点之后的量 vs 之前的量）
    post_low_vol = volumes[day_low_idx:].mean() if day_low_idx < len(volumes) else 0
    pre_low_vol = volumes[:day_low_idx].mean() if day_low_idx > 0 else post_low_vol
    vol_ratio = post_low_vol / max(pre_low_vol, 1)

    # 评分
    score = 50; shape = "无V型"
    if drop_pct > 0.02 and recovery > 0.8 and vol_ratio > 1.2:
        score = 88; shape = "深V反转+放量"
    elif drop_pct > 0.015 and recovery > 0.7:
        score = 78; shape = "V型反转"
    elif drop_pct > 0.01 and recovery > 0.6:
        score = 65; shape = "浅V型"
    elif recovery > 1.0:  # 突破开盘价
        score = 72; shape = "V型突破"

    return {
        "score": round(score, 1),
        "recovery": round(float(recovery), 2),
        "drop_pct": round(float(drop_pct) * 100, 2),
        "shape": shape,
    }


# ============================================================
# F5: 分时均线缠绕度（VWAP Tightness）
# ============================================================
def factor_vwap_tightness(intra_1m: pd.DataFrame) -> Dict:
    """
    衡量价格绕 VWAP 的震荡密度。
    缠绕越紧密 → 平衡越充分 → 方向选择越临近。

    为什么不是拥挤因子：
    - 多数人用布林带宽度，我们用的是分时级别的VWAP缠绕度
    - 1分钟级别的VWAP缠绕在尾盘被打破时，方向更可靠
    """
    if intra_1m is None or len(intra_1m) < 20:
        return {"score": 50, "tightness": 0, "signal": "数据不足"}

    closes = intra_1m["close"].astype(float).values
    volumes = intra_1m["volume"].astype(float).values

    # 计算 VWAP
    vwap = np.sum(closes * volumes) / max(np.sum(volumes), 1)

    # 最后30分钟价格绕VWAP的偏离度标准差
    tail_closes = closes[-25:] if len(closes) >= 25 else closes
    deviations = np.abs(tail_closes - vwap) / max(vwap, 0.01)
    tightness = 1 - min(float(np.std(deviations) * 100), 0.99)  # 越接近1越紧密

    # 当前价格与VWAP的关系
    current = closes[-1]
    vwap_dev = (current - vwap) / max(vwap, 0.01)

    score = 50
    if tightness > 0.9 and vwap_dev > 0:  # 紧密度高+价格在VWAP上方
        score = 85
    elif tightness > 0.85:
        score = 72
    elif tightness > 0.75:
        score = 60
    elif tightness > 0.6:
        score = 48
    else:
        score = 35

    return {
        "score": round(score, 1),
        "tightness": round(float(tightness), 3),
        "vwap_dev_pct": round(float(vwap_dev) * 100, 2),
        "vwap": round(float(vwap), 2),
    }


# ============================================================
# F6: 盘中试盘信号（测试抛压）
# ============================================================
def factor_test_sell_pressure(intra_1m: pd.DataFrame) -> Dict:
    """
    检测"小脉冲 → 迅速回落 → 持续横盘"的试盘模式。
    这是主力测试上方抛压的经典手法：如果回落后缩量横盘，说明抛压已被消化。

    为什么不是拥挤因子：
    - 需要在1分钟K线上识别特定的3阶段形态（脉冲→回落→横盘）
    - 不是简单的价格或量阈值，而是时间序列形态识别
    """
    if intra_1m is None or len(intra_1m) < 20:
        return {"score": 50, "count": 0, "signal": "数据不足"}

    closes = intra_1m["close"].astype(float).values
    volumes = intra_1m["volume"].astype(float).values

    # 扫描脉冲：1-2根K线快速拉升 > 0.5%
    test_signals = 0
    for i in range(5, len(closes) - 5):
        # 脉冲检测：单根或两根K线涨 > 0.5%
        chg_2bar = (closes[i] - closes[i-2]) / max(closes[i-2], 0.01)
        vol_spike = volumes[i] > volumes[i-3:i].mean() * 1.5

        if chg_2bar > 0.005 and vol_spike:
            # 回落检测：接下来3根K线跌回
            post_retrace = (closes[i] - closes[i+3]) / max(closes[i], 0.01)
            post_vol = volumes[i+1:i+4].mean()

            if post_retrace > 0.003 and post_vol < volumes[i] * 0.6:
                # 回落缩量 → 抛压不大
                test_signals += 1

    score = 50
    if test_signals >= 3:
        score = 85; signal = f"多次试盘({test_signals}次)"
    elif test_signals >= 2:
        score = 72; signal = f"试盘({test_signals}次)"
    elif test_signals >= 1:
        score = 60; signal = "单次试盘"
    else:
        score = 45; signal = "无试盘"

    return {
        "score": round(score, 1),
        "count": test_signals,
        "signal": signal,
    }


# ============================================================
# F7: 振幅压缩爆发（弹簧效应）
# ============================================================
def factor_amplitude_squeeze(daily_kline: pd.DataFrame, today_amp: float) -> Dict:
    """
    检测日内振幅收缩至N日低点。
    振幅压缩→能量积蓄→方向选择→爆发

    为什么不是拥挤因子：
    - 不同于布林带收窄（那是价格波动率），我们用的是日内振幅
    - 结合尾盘方向判断，可以做方向性预测
    """
    if daily_kline is None or len(daily_kline) < 10:
        return {"score": 50, "ratio": 1.0, "signal": "数据不足"}

    highs = daily_kline["high"].astype(float)
    lows = daily_kline["low"].astype(float)

    # 计算每日振幅
    amps = []
    for i in range(len(highs)):
        if highs.iloc[i] > 0 and lows.iloc[i] > 0:
            amps.append((highs.iloc[i] - lows.iloc[i]) / ((highs.iloc[i] + lows.iloc[i]) / 2))

    if len(amps) < 5:
        return {"score": 50, "ratio": 1.0, "signal": "数据不足"}

    avg_amp_5d = np.mean(amps[-6:-1]) if len(amps) >= 6 else np.mean(amps[:-1])
    if avg_amp_5d == 0:
        return {"score": 50, "ratio": 1.0, "signal": "无法计算"}

    today_amp_pct = today_amp / 100  # 转换为小数
    ratio = today_amp_pct / avg_amp_5d

    score = 50
    if ratio < 0.4:       # 振幅极度压缩
        score = 88; signal = "极度压缩(弹簧蓄力)"
    elif ratio < 0.6:
        score = 75; signal = "明显压缩"
    elif ratio < 0.8:
        score = 62; signal = "轻微压缩"
    elif ratio > 2.0:     # 振幅异常放大
        score = 40; signal = "振幅已释放"
    else:
        score = 50; signal = "正常"

    return {
        "score": round(score, 1),
        "ratio": round(float(ratio), 2),
        "avg_amp": round(float(avg_amp_5d) * 100, 2),
        "today_amp": round(float(today_amp_pct) * 100, 2),
        "signal": signal,
    }


# ============================================================
# F8: 隔夜跳空方向统计偏差
# ============================================================
def factor_overnight_gap_bias(daily_kline: pd.DataFrame, today_change: float) -> Dict:
    """
    统计该股票历史上"当日涨→次日高开"的概率。
    结合当前涨幅和尾盘行为，估计隔夜跳空方向。

    为什么不是拥挤因子：
    - 个股级别的隔夜跳空统计需要逐一计算，全市场扫描很少做
    - 我们关注的是统计偏差，不是单次的跳空预测
    """
    if daily_kline is None or len(daily_kline) < 20:
        return {"score": 50, "bias": 0, "signal": "历史数据不足"}

    closes = daily_kline["close"].astype(float)
    opens = daily_kline["open"].astype(float)

    # 统计：当日收涨(>0.5%)后，次日高开的概率
    up_then_gap_up = 0
    up_days = 0
    for i in range(1, len(closes) - 1):
        if closes.iloc[i] > closes.iloc[i-1] * 1.005:  # 当日涨>0.5%
            up_days += 1
            if opens.iloc[i+1] > closes.iloc[i]:  # 次日高开
                up_then_gap_up += 1

    gap_up_rate = up_then_gap_up / max(up_days, 1)
    bias = gap_up_rate - 0.5  # 正偏向=倾向于高开

    score = 50
    if bias > 0.2:
        score = 78; signal = "强高开偏向"
    elif bias > 0.1:
        score = 65; signal = "高开偏向"
    elif bias > 0:
        score = 55; signal = "微高开偏向"
    elif bias < -0.1:
        score = 35; signal = "低开偏向"
    elif bias < -0.2:
        score = 20; signal = "强低开偏向"
    else:
        score = 48; signal = "无明显偏向"

    return {
        "score": round(score, 1),
        "bias": round(float(bias), 3),
        "gap_up_rate": round(float(gap_up_rate), 3),
        "sample": up_days,
        "signal": signal,
    }


# ============================================================
# 尾盘异动猎手 策略主类
# ============================================================
class TailAnomalyHunter:
    def __init__(self, client: TFClient, cfg: dict, debug=False):
        self.cli = client
        self.cfg = cfg
        self.debug = debug
        self.engine = EnhancedFactorEngine()

    def log(self, msg, lv="INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        if self.debug or lv != "DEBUG":
            print(line)

    # ── 主流程 ──
    def run(self) -> Optional[pd.DataFrame]:
        print("\n" + "=" * 60)
        print("  尾盘异动猎手 v1.0 — Tail Anomaly Hunter")
        print("  8因子反拥挤体系 | 14:50 执行 | T+1 高胜率")
        print("=" * 60)

        today = date.today()
        if today.weekday() >= 5:
            self.log("周末不运行"); return None

        # Step 1: 全市场初筛
        self.log("\n[1/6] TickFlow Pro 全市场行情...")
        quotes = self.cli.get_all_quotes()
        self.log(f"  全市场 {len(quotes)} 只")

        candidates = self._prefilter(quotes)
        if len(candidates) == 0:
            self.log("初筛无候选"); return None
        self.log(f"  初筛: {len(candidates)} 只")

        # Step 2: 日线K线
        self.log(f"\n[2/6] 获取日线K线...")
        sym_col = "symbol" if "symbol" in candidates.columns else "code"
        syms = candidates[sym_col].tolist()
        daily_kl = self.cli.get_klines_batch(syms, count=30)
        self.log(f"  获取 {len(daily_kl)} 只K线")

        # Step 3: 1分钟分时（核心！）
        self.log(f"\n[3/6] 获取1分钟分时K线（尾盘微结构分析）...")
        intra_1m = self.cli.get_intraday_1m(syms, count=80)
        self.log(f"  获取 {len(intra_1m)} 只分时")

        # Step 4: 8因子计算
        self.log(f"\n[4/6] 8因子计算（尾盘微结构+反拥挤）...")
        results = []
        name_col = next((c for c in candidates.columns if "name" in c.lower()), None)

        for i, (_, row) in enumerate(candidates.iterrows()):
            s = row[sym_col]
            nm = row.get(name_col, "") if name_col else ""
            kl = daily_kl.get(s)
            idf = intra_1m.get(s)

            if kl is None or len(kl) < 15:
                continue

            today_change = float(row.get("ext.change_pct", 0) or 0)
            today_amp = float(row.get("ext.amplitude", 0) or 0)
            price = float(row["last_price"])
            turnover = float(row.get("ext.turnover_rate", 0) or 0)
            amount = float(row.get("amount", 0) or 0)

            # 计算8因子
            f1 = factor_tail_pulse(idf)
            f2 = factor_auction_anomaly(idf, float(kl["volume"].astype(float).iloc[-1]) if kl is not None else 0)
            f3 = factor_tiny_yang_steps(kl)
            f4 = factor_v_shape_reversal(idf)
            f5 = factor_vwap_tightness(idf)
            f6 = factor_test_sell_pressure(idf)
            f7 = factor_amplitude_squeeze(kl, today_amp)
            f8 = factor_overnight_gap_bias(kl, today_change)

            # 加权综合评分
            w = self.cfg
            total = (
                f1["score"] * w["weight_pulse"] +
                f2["score"] * w["weight_auction"] +
                f3["score"] * w["weight_tiny_yang"] +
                f4["score"] * w["weight_v_shape"] +
                f5["score"] * w["weight_vwap_tight"] +
                f6["score"] * w["weight_test_sell"] +
                f7["score"] * w["weight_amp_squeeze"] +
                f8["score"] * w["weight_gap_bias"]
            )

            # ── ATR 动态止损 ──
            atr_val = calc_atr(kl["high"].astype(float).values,
                               kl["low"].astype(float).values,
                               kl["close"].astype(float).values)
            stop_info = calc_dynamic_stop(price, atr_val)

            if total < w["min_score"]:
                continue

            results.append({
                "symbol": s,
                "name": nm,
                "price": price,
                "change_pct": today_change,
                "turnover": turnover,
                "amount": amount / 1e4,
                "amplitude": today_amp,
                "total_score": round(total, 1),
                "atr": round(atr_val, 3),
                "stop_loss": stop_info["stop_price"],
                "stop_loss_pct": stop_info["loss_pct"],
                "stop_type": stop_info["stop_type"],
                **{f"f{i+1}_{k}": v for i, (k, v) in enumerate([
                    ("pulse", f1), ("auction", f2), ("tiny_yang", f3),
                    ("v_shape", f4), ("vwap_tight", f5), ("test_sell", f6),
                    ("amp_sq", f7), ("gap_bias", f8)
                ])},
            })

            if (i+1) % 50 == 0:
                self.log(f"  进度: {i+1}/{len(candidates)} | 入选: {len(results)}", "DEBUG")

        self.log(f"  8因子评分完成: {len(results)} 只入选 (>{w['min_score']}分)")

        if len(results) == 0:
            self.log("无候选"); return None

        # 排序
        results.sort(key=lambda x: -x["total_score"])
        top = results[:w["top_output"]]

        # Step 5: 生成推荐理由
        self.log(f"\n[5/6] 生成推荐理由...")
        top_with_reasons = [self._generate_reason(r) for r in top]

        # Step 6: 输出
        self._output(top_with_reasons, today)
        self.cli.stats()

        return pd.DataFrame(top_with_reasons)

    def _prefilter(self, df: pd.DataFrame) -> pd.DataFrame:
        """初筛"""
        sym_col = "symbol" if "symbol" in df.columns else "code"
        name_col = next((c for c in df.columns if "name" in c.lower()), None)

        valid = pd.Series(True, index=df.index)
        if name_col:
            for kw in ["ST","*ST","退"]:
                valid &= ~df[name_col].astype(str).str.contains(kw, na=False)
        valid &= ~df[sym_col].astype(str).str.startswith("8")

        p = df["last_price"].astype(float)
        to = df.get("ext.turnover_rate", pd.Series(0,index=df.index)).astype(float)
        am = df.get("amount", pd.Series(0,index=df.index)).fillna(0).astype(float)

        mask = (valid &
            (p >= self.cfg["price_min"]) & (p <= self.cfg["price_max"]) &
            (to >= self.cfg["turnover_min"]) & (to <= self.cfg["turnover_max"]) &
            (am >= self.cfg["amount_min"]))
        return df[mask].copy()

    def _generate_reason(self, r: dict) -> dict:
        """为每只入选股票生成推荐理由"""
        reasons = []
        signals = []

        f1 = r["f1_pulse"]
        if f1["score"] >= 80:
            reasons.append(f"尾盘{f1['direction']}(斜率{f1['slope']:.5f}, R²={f1['r2']})")
            signals.append("尾盘稳健")

        f2 = r["f2_auction"]
        if f2["score"] >= 70:
            reasons.append(f"集合竞价{f2['signal']}(量比{f2['ratio']}x)")
            signals.append("竞价抢筹")

        f3 = r["f3_tiny_yang"]
        if f3["score"] >= 70:
            reasons.append(f"{f3['pattern']}—机构暗中介入形态")
            signals.append("吸筹形态")

        f4 = r["f4_v_shape"]
        if f4["score"] >= 70:
            reasons.append(f"日内{f4['shape']}(恢复{f4['recovery']:.0%})")
            signals.append("V型反转")

        f5 = r["f5_vwap_tight"]
        if f5["score"] >= 70:
            reasons.append(f"VWAP缠绕度{f5['tightness']:.2f}(紧密度高→方向选择临近)")
            signals.append("蓄力突破")

        f6 = r["f6_test_sell"]
        if f6["score"] >= 70:
            reasons.append(f"盘中{f6['signal']}信号(抛压已消化)")
            signals.append("试盘确认")

        f7 = r["f7_amp_sq"]
        if f7["score"] >= 70:
            reasons.append(f"振幅{f7['signal']}({f7['ratio']:.1f}x均幅→弹簧蓄力)")
            signals.append("弹簧效应")

        f8 = r["f8_gap_bias"]
        if f8["score"] >= 65:
            reasons.append(f"历史隔夜{f8['signal']}(高开率{f8['gap_up_rate']:.0%})")
            signals.append("高开偏向")

        # 买入建议
        buy_time = "14:55-14:57"  # 集合竞价前3分钟
        sell_time = "次日09:30-10:00"
        if f2["score"] >= 80:
            buy_time = "14:57"  # 竞价确认后跟
        if f7["score"] >= 80:
            sell_time = "次日10:00-10:30"  # 振幅释放需要更多时间

        r["reasons"] = reasons
        r["signals"] = signals[:3]
        r["recommendation"] = {
            "buy_time": buy_time,
            "sell_time": sell_time,
            "hold_period": "T+1 隔夜",
            "stop_loss": r.get("stop_loss", round(r["price"] * 0.97, 2)),
            "stop_loss_pct": r.get("stop_loss_pct", 3.0),
            "stop_type": r.get("stop_type", "硬止损"),
            "atr": r.get("atr", 0),
            "target_1": round(r["price"] * 1.03, 2),
            "target_2": round(r["price"] * 1.06, 2),
            "confidence": "高" if r["total_score"] >= 80 else ("中高" if r["total_score"] >= 70 else "中"),
        }
        return r

    def _output(self, results, today):
        top_n = min(len(results), self.cfg["top_output"])
        results = results[:top_n]

        print(f"\n{'=' * 60}")
        print(f"  🎯 尾盘异动猎手 TOP{top_n} | {today.isoformat()}")
        print(f"  执行窗口: 14:50 | 持仓: T+1 隔夜")
        print(f"{'=' * 60}")

        for i, r in enumerate(results, 1):
            icon = "🔥" if r["total_score"] >= 80 else ("⭐" if r["total_score"] >= 70 else ("💡" if r["total_score"] >= 60 else "📌"))
            rec = r["recommendation"]
            print(f"\n{'─' * 50}")
            print(f"  [{i}] {icon} {r['symbol']} {r['name']}  —  {r['total_score']:.0f}分 {rec['confidence']}信心")
            print(f"      现价: {r['price']:.2f} | +{r['change_pct']:.2f}% | 换{r['turnover']:.1f}% | 额{r['amount']:.0f}万")
            print(f"      信号: {' | '.join(r['signals'][:4])}")

            print(f"\n      📊 推荐理由:")
            for reason in r.get("reasons", [])[:4]:
                print(f"        • {reason}")

            print(f"\n      ⏰ 操作建议:")
            print(f"        买入: {rec['buy_time']} | 卖出: {rec['sell_time']}")
            print(f"        🛑 止损: {rec['stop_loss']:.2f} ({rec['stop_type']}) | ATR: {rec['atr']:.3f}")
            print(f"        🎯 目标①: {rec['target_1']:.2f} (+3%) | 目标②: {rec['target_2']:.2f} (+6%)")

            # 因子详情
            print(f"\n      📈 8因子得分:")
            f_scores = [
                (f"尾盘脉冲", r["f1_pulse"]["score"], r["f1_pulse"]["direction"]),
                (f"竞价异常", r["f2_auction"]["score"], r["f2_auction"]["signal"]),
                (f"碎步小阳", r["f3_tiny_yang"]["score"], r["f3_tiny_yang"]["pattern"]),
                (f"V型反转", r["f4_v_shape"]["score"], r["f4_v_shape"]["shape"]),
                (f"VWAP缠绕", r["f5_vwap_tight"]["score"], f"紧密度{r['f5_vwap_tight']['tightness']:.2f}"),
                (f"试盘信号", r["f6_test_sell"]["score"], r["f6_test_sell"]["signal"]),
                (f"振幅压缩", r["f7_amp_sq"]["score"], r["f7_amp_sq"]["signal"]),
                (f"隔夜偏向", r["f8_gap_bias"]["score"], r["f8_gap_bias"]["signal"]),
            ]
            for j, (name, sc, detail) in enumerate(f_scores):
                bar = "█" * int(sc/10) + "░" * (10 - int(sc/10))
                print(f"        {name:<10} [{bar}] {sc:.0f}  {detail}")

        print(f"\n{'=' * 60}")
        print(f"  策略特点: 8因子均为非传统微结构因子")
        print(f"  避开拥挤: RSI/KDJ/MA/MACD/涨停板等已被量化挖烂的信号")
        print(f"  小资金优势: 尾盘30分钟建仓无冲击成本")
        print(f"{'=' * 60}\n")

        # 保存 JSON
        od = self.cfg["output_dir"]
        os.makedirs(od, exist_ok=True)
        output = {
            "strategy": "tail_anomaly_hunter_v1",
            "version": "1.0",
            "date": today.isoformat(),
            "time": datetime.now().strftime("%H:%M:%S"),
            "data_source": "TickFlow Pro",
            "philosophy": "8反拥挤因子 | 尾盘微结构 | T+1高胜率 | 小资金专属",
            "candidates": [{k: v for k, v in r.items() if not k.startswith("f")} for r in results],
            "factor_details": [{k: v for k, v in r.items() if k.startswith("f")} for r in results],
        }
        json_path = os.path.join(od, f"tail_hunter_{today.isoformat()}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2, default=str)
        self.log(f"结果已保存: {json_path}")


# ============================================================
# 入口
# ============================================================
def main():
    p = argparse.ArgumentParser(description="尾盘异动猎手 v1.0")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--top", type=int, default=CONFIG["top_output"])
    a = p.parse_args()

    cfg = CONFIG.copy()
    cfg["top_output"] = a.top

    try:
        cli = TFClient()
    except Exception as e:
        print(f"\n初始化失败: {e}")
        print("请: pip install tickflow && setx TICKFLOW_API_KEY \"key\"")
        sys.exit(1)

    hunter = TailAnomalyHunter(cli, cfg, debug=a.debug)
    try:
        result = hunter.run()
        if result is not None and len(result) > 0:
            print("\n✓ 尾盘异动猎手运行成功")
        else:
            print("\n今日无符合条件的候选")
    except Exception as e:
        print(f"\n异常: {e}")
        import traceback; traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
