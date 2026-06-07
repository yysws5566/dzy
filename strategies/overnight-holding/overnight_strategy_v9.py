#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一夜持股法 v9.0 — 增强因子版 (TickFlow Pro)
===============================================
v8 → v9 核心升级:
  ① 嵌入 8 大增强因子（RSI/KDJ/威廉%R/主力资金流/VWAP/板块强度/大单/尾盘共振）
  ② 评分维度 8→12 维，覆盖超卖反弹 + 主力吸筹双引擎
  ③ 差异化于市场常见"一夜持股法"——加入超卖超买和资金流因子
  ④ 新增隔天涨跌概率预测

新增因子对胜率的贡献:
  - RSI 超卖 + KDJ 金叉 → 识别被错杀标的，隔天反弹概率高
  - 主力资金净流入 > 500万 → 主力尾盘建仓，隔天拉升概率高
  - VWAP 小幅偏离 + 大单活跃 → 资金认可当前价位
  - 三者同时满足 → 最强买入信号（预计胜率 65%+）

运行: python overnight_strategy_v9.py [--debug] [--dry-run]
环境: pip install tickflow pandas numpy
"""

import os, sys, json, time, math, argparse
from datetime import datetime, date, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd

# 导入增强因子库（在 strategies/ 目录下）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from enhanced_factors import (
    EnhancedFactorEngine,
    calc_rsi, calc_kdj, calc_williams_r,
    calc_ma, calc_vol_ratio, calc_vwap,
    calc_money_flow, calc_sector_strength,
    detect_tail_resonance, calc_atr, calc_dynamic_stop
)

# ============================================================
# v9.0 配置
# ============================================================
DEFAULT_CONFIG = {
    # ── 大盘 ──
    "index_codes": ["000001.SH", "399006.SZ"],
    "index_change_min": -1.0,       # v9 放宽大盘容忍度（因有个股超卖因子保护）

    # ── 初筛（保留 v8 的精准范围） ──
    "price_min": 5.0,
    "price_max": 50.0,
    "change_min": 2.0,
    "change_max": 4.5,              # v9 微调上限
    "turnover_min": 2.5,            # v9 放宽换手下限
    "turnover_max": 9.0,
    "amplitude_min": 2.0,
    "amplitude_max": 8.0,
    "amount_min": 6000,             # 成交额（万元）
    "close_high_min": 0.965,        # v9 微调
    "volume_ratio_min": 1.1,
    "volume_ratio_max": 3.5,        # v9 放宽量比上限
    "exclude_limit_up_pct": 0.7,

    # ── 深度分析 ──
    "kl_count": 60,
    "deep_top_n": 200,              # v9 放宽进入深度分析的数量
    "top_output": 5,

    # ── 12 维权重（v9 全新） ──
    # 传统维度
    "weight_change_quality": 0.12,
    "weight_tail_strength": 0.12,
    "weight_intraday_quality": 0.08,
    "weight_volume_health": 0.08,
    "weight_turnover_health": 0.05,
    "weight_float_cap": 0.05,
    "weight_trend": 0.04,
    "weight_amount": 0.03,
    # 增强因子维度（v9 新增，合计 0.43）
    "weight_rsi": 0.12,              # RSI 超卖反弹
    "weight_kdj": 0.10,              # KDJ 低位金叉
    "weight_money_flow": 0.12,       # 主力资金流向
    "weight_vwap": 0.06,             # VWAP 偏离度
    "weight_big_order": 0.05,        # 大单活跃度
    "weight_sector_strength": 0.04,  # 板块相对强度
    "weight_williams_r": 0.04,       # 威廉 %R

    # ── 风控 ──
    "min_score": 58,                 # v9 微调最低分
    "base_position": 0.18,
    "friday_position_ratio": 0.5,
    "profit_target_1": 0.015,
    "profit_target_2": 0.03,
    "stop_loss": -0.02,
    "gap_down_stop": -0.005,

    # ── 增强信号阈值 ──
    "rsi_oversold_threshold": 40,        # RSI 低于此值加分
    "kdj_golden_threshold": 40,          # KDJ K值低于此值且金叉加分
    "money_flow_min_score": 60,          # 资金流最低评分
    "big_order_min_ratio": 0.15,         # 大单占比最低阈值

    # ── 输出 ──
    "output_dir": os.path.join(os.path.dirname(os.path.abspath(__file__)), "results"),
    "output_json": "strategy_v9_result.json",
    "output_md": "strategy_v9_result.md",
}


def load_config() -> dict:
    cfg = DEFAULT_CONFIG.copy()
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategy_config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            cfg.update({k: v for k, v in raw.items() if not k.startswith("_")})
        except Exception:
            pass
    return cfg


# ============================================================
# TickFlow Pro 客户端（同 v8 但增加板块数据支持）
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
        self._all_quotes_cache: Optional[pd.DataFrame] = None

    def _rl(self, ep: str, rpm: int):
        now = time.time()
        last = self._rl_map.get(ep, 0)
        gap = 60.0 / rpm
        if now - last < gap:
            time.sleep(gap - (now - last) + 0.05)
        self._rl_map[ep] = time.time()
        self._cnt[ep] += 1

    def get_all_quotes(self, use_cache=True) -> pd.DataFrame:
        if use_cache and self._all_quotes_cache is not None:
            return self._all_quotes_cache
        self._rl("quotes_uni", 60)
        df = self._tf.quotes.get(universes="CN_Equity_A", as_dataframe=True)
        self._all_quotes_cache = df
        return df

    def get_index_klines(self, syms: List[str], count=60) -> dict:
        self._rl("kl_batch", 60)
        return self._tf.klines.batch(syms, period="1d", count=count,
                                     adjust="forward", as_dataframe=True)

    def get_klines_batch(self, syms: List[str], count=60) -> dict:
        result = {}
        for i in range(0, len(syms), 100):
            chunk = syms[i:i+100]
            self._rl("kl_batch", 60)
            try:
                result.update(self._tf.klines.batch(
                    chunk, period="1d", count=count,
                    adjust="forward", as_dataframe=True))
            except Exception:
                pass
        return result

    def get_intraday_batch(self, syms: List[str], period="5m", count=60) -> dict:
        try:
            self._rl("intra_batch", 30)
            return self._tf.klines.intraday_batch(
                syms, period=period, count=count, as_dataframe=True)
        except Exception:
            result = {}
            for s in syms[:30]:  # 限制逐只获取的数量
                try:
                    self._rl("intra_one", 60)
                    result[s] = self._tf.klines.intraday(
                        s, period=period, count=count, as_dataframe=True)
                except Exception:
                    pass
            return result

    def get_instruments(self, syms: List[str]) -> list:
        self._rl("inst", 60)
        try:
            return self._tf.instruments.get(syms)
        except Exception:
            return []

    def stats(self):
        print(f"\n  API调用: {dict(self._cnt)}")


# ============================================================
# v9.0 策略主类
# ============================================================
class OvernightV9:
    def __init__(self, client: TFClient, cfg: dict, debug=False):
        self.cli = client
        self.cfg = cfg
        self.debug = debug
        self.logs = []
        self.t0 = datetime.now()
        self.factor_engine = EnhancedFactorEngine()  # v9: 增强因子引擎

    def L(self, msg, lv="INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.logs.append(line)
        if self.debug or lv != "DEBUG":
            print(line)

    # ── Step 0: 交易日检测 ──
    def is_trade_day(self, d: date) -> Tuple[bool, float]:
        if d.weekday() >= 5:
            return False, 0
        cf = 1.0
        if d.weekday() == 4:
            cf = self.cfg["friday_position_ratio"]
        hol = [(1,1),(1,2),(1,3),(5,1),(5,2),(5,3),(10,1),(10,2),(10,3),(10,4),(10,5)]
        if (d.month, d.day) in hol:
            return False, 0
        return True, cf

    # ── Step 1: 大盘评估 ──
    def eval_market(self) -> Tuple[str, dict]:
        self.L("="*55)
        self.L("Step 1: 大盘多维评估 (v9 增强)")
        env = {"status": "GREEN", "details": {}, "warnings": [], "position_ratio": 0}
        ik = self.cli.get_index_klines(self.cfg["index_codes"], count=60)
        scores = []

        for code in self.cfg["index_codes"]:
            if code not in ik or len(ik[code]) < 20:
                env["warnings"].append(f"{code} 数据不足")
                scores.append(0)
                continue
            df = ik[code]; c = df["close"].astype(float)
            ma5 = c.rolling(5).mean().iloc[-1]
            ma10 = c.rolling(10).mean().iloc[-1]
            ma20 = c.rolling(20).mean().iloc[-1]
            cl = c.iloc[-1]
            above = cl > ma20; bull = ma5 > ma10

            # v9 增强: 计算大盘 RSI
            rsi_val = calc_rsi(c.values, 14)

            env["details"][code] = {
                "close": round(cl,2), "ma5": round(ma5,2),
                "ma10": round(ma10,2), "ma20": round(ma20,2),
                "above_ma20": above, "ma_bull": bull,
                "rsi": round(rsi_val, 1)
            }

            sc = (1 if above else 0) + (1 if bull else 0)
            # 大盘 RSI 过低也说明环境差
            if rsi_val < 30:
                sc -= 1
            scores.append(max(0, sc))
            ic = "OK" if sc>=2 else ("WARN" if sc>=1 else "BAD")
            self.L(f"  [{ic}] {code}: {cl:.2f} MA20:{ma20:.1f} RSI:{rsi_val:.1f} | "
                   f"{'MA20上' if above else 'MA20下'} {'多头' if bull else '死叉'}")

        avg = sum(scores)/len(scores) if scores else 0
        if avg >= 1.8:
            env["status"] = "GREEN"; env["position_ratio"] = 1.0
        elif avg >= 1.0:
            env["status"] = "YELLOW"; env["position_ratio"] = 0.6
        elif avg >= 0.5:
            env["status"] = "CAUTIOUS"; env["position_ratio"] = 0.3
        else:
            env["status"] = "RED"; env["position_ratio"] = 0
        self.L(f"  => 大盘: {env['status']} 仓位:{env['position_ratio']:.0%}")
        return env["status"], env

    # ── Step 2: 初筛 ──
    def screen(self) -> pd.DataFrame:
        self.L(f"\nStep 2: 全市场精准初筛")
        df = self.cli.get_all_quotes()
        total = len(df)
        self.L(f"  全市场 {total} 只")

        sym_col = "symbol" if "symbol" in df.columns else "code"
        name_col = next((c for c in df.columns if "name" in c.lower()), None)

        valid = pd.Series(True, index=df.index)
        if name_col:
            for kw in ["ST","*ST","退"]:
                valid &= ~df[name_col].astype(str).str.contains(kw, na=False)
        valid &= ~df[sym_col].astype(str).str.startswith("8")

        p = df["last_price"].astype(float)
        hi = df.get("high", pd.Series(0,index=df.index)).fillna(0).astype(float)
        lo = df.get("low", pd.Series(0,index=df.index)).fillna(0).astype(float)
        op = df.get("open", pd.Series(0,index=df.index)).fillna(0).astype(float)
        ch = df.get("ext.change_pct", pd.Series(0,index=df.index)).astype(float)
        to = df.get("ext.turnover_rate", pd.Series(0,index=df.index)).astype(float)
        am = df.get("ext.amplitude", pd.Series(0,index=df.index)).astype(float)
        amt = df.get("amount", pd.Series(0,index=df.index)).fillna(0).astype(float)

        vp = (p>0)&(hi>0)&(lo>0)&(op>0)
        mask = (valid & vp &
            (p>=self.cfg["price_min"]) & (p<=self.cfg["price_max"]) &
            (ch>=self.cfg["change_min"]) & (ch<=self.cfg["change_max"]) &
            (to>=self.cfg["turnover_min"]) & (to<=self.cfg["turnover_max"]) &
            (am>=self.cfg["amplitude_min"]) & (am<=self.cfg["amplitude_max"]) &
            (amt>=self.cfg["amount_min"]))

        filt = df[mask].copy()
        self.L(f"  基础条件: {len(filt)} 只")

        ch_ratio = p[mask] / hi[mask]
        ch_ok = ch_ratio >= self.cfg["close_high_min"]
        self.L(f"  尾盘强势度(>={self.cfg['close_high_min']}): {ch_ok.sum()} 只")

        nlu = pd.Series(True, index=filt.index)
        lu_cols = [c for c in df.columns if "limit_up" in c.lower()]
        if lu_cols:
            lu = df.loc[mask, lu_cols[0]].astype(float)
            nlu = p[mask] < lu * (1 - self.cfg["exclude_limit_up_pct"]/100)

        fm = ch_ok & nlu
        filt = filt.loc[fm].copy()
        self.L(f"  初筛最终: {len(filt)} 只 ({(len(filt)/total*100):.2f}%)")

        cand = []
        for idx, row in filt.iterrows():
            pp = float(row["last_price"]); hh = float(row["high"])
            ll = float(row["low"]); oo = float(row["open"])
            hl = hh - ll
            iq = (pp - oo)/hl if hl>0 else 0.5
            cand.append({
                "symbol": row[sym_col],
                "name": row.get(name_col,"") if name_col else "",
                "price": pp, "open": oo, "high": hh, "low": ll,
                "prev_close": float(row.get("prev_close",pp) or pp),
                "change_pct": float(row.get("ext.change_pct",0) or 0),
                "turnover": float(row.get("ext.turnover_rate",0) or 0),
                "amplitude": float(row.get("amplitude",0) or 0),
                "amount": float(row.get("amount",0) or 0),
                "volume": float(row.get("volume",0) or 0),
                "close_high": pp/hh if hh>0 else 0,
                "intraday_quality": iq,
                # v9 新增字段
                "volume_ratio": 0, "float_cap": 0,
                "ma5":0,"ma10":0,"ma20":0,
                "trend_bull": False, "above_ma5": False,
                "intraday_score": 0.5,
                "rsi_score": 50, "kdj_score": 50, "wr_score": 50,
                "money_flow_score": 50, "vwap_score": 50,
                "big_order_score": 50, "sector_score": 50,
                "tail_resonance_score": 50,
                "rsi_val": 50, "kdj_val": "0/0/0",
                "flow_ratio": 1.0, "net_flow": 0,
                "vwap_deviation": 0, "big_order_ratio": 0,
                "score_detail": {}, "total_score": 0,
                "composite_score": 0,          # v9: 增强因子综合分
                "probability": "",             # v9: 隔天涨跌概率
            })
        cand.sort(key=lambda x:(x["close_high"],x["change_pct"]), reverse=True)
        if len(cand) > self.cfg["deep_top_n"]:
            self.L(f"  取前 {self.cfg['deep_top_n']} 进深度分析")
            cand = cand[:self.cfg["deep_top_n"]]
        return pd.DataFrame(cand)

    # ── Step 3: 深度分析（v9 增强：加入 RSI/KDJ/威廉%R） ──
    def deep_ana(self, cand: pd.DataFrame) -> pd.DataFrame:
        n = len(cand)
        self.L(f"\nStep 3: 深度分析 + 增强因子A-C (RSI/KDJ/%R) ({n}只)")
        if n == 0: return cand

        syms = cand["symbol"].tolist()
        kd = self.cli.get_klines_batch(syms, count=self.cfg["kl_count"])

        # 获取流通市值
        im = {}
        try:
            for inst in self.cli.get_instruments(syms):
                s = inst.get("symbol",""); e = inst.get("ext",{})
                im[s] = {"fs": e.get("float_shares",0) or 0}
        except Exception:
            pass

        vi = []
        for i, row in cand.iterrows():
            s = row["symbol"]; kl = kd.get(s)
            if kl is None or len(kl) < 15:
                if self.debug: self.L(f"  {s} K线不足 跳过", "DEBUG")
                continue

            closes = kl["close"].astype(float)
            highs = kl["high"].astype(float)
            lows = kl["low"].astype(float)
            vols = kl["volume"].astype(float)

            # ── v8 原有逻辑 ──
            if len(vols) >= 6:
                avg5 = vols.iloc[-6:-1].mean()
                vr = float(vols.iloc[-1]/avg5) if avg5>0 else 1.0
            else:
                vr = 1.0
            if vr < self.cfg["volume_ratio_min"] or vr > self.cfg["volume_ratio_max"]:
                if self.debug: self.L(f"  {s} 量比={vr:.1f} 排除","DEBUG")
                continue

            cv = closes.iloc[-1]
            if cv >= highs.iloc[-60:].max() * 0.99:
                if self.debug: self.L(f"  {s} 60日新高 排除","DEBUG")
                continue

            ma5 = calc_ma(closes.values, 5)
            ma10 = calc_ma(closes.values, 10)
            ma20 = calc_ma(closes.values, 20) if len(closes)>=20 else ma10
            fs = im.get(s,{}).get("fs",0)
            fc = (fs * row["price"])/1e8 if fs>0 else 0

            # ── v9 新增: 增强因子 A-C ──
            rsi_result = self.factor_engine.factor_rsi(closes.values)
            kdj_result = self.factor_engine.factor_kdj(highs.values, lows.values, closes.values)
            wr_result = self.factor_engine.factor_williams_r(highs.values, lows.values, closes.values)

            # 写入
            cand.at[i,"volume_ratio"] = round(vr,2)
            cand.at[i,"ma5"] = round(ma5,2)
            cand.at[i,"ma10"] = round(ma10,2)
            cand.at[i,"ma20"] = round(ma20,2)
            cand.at[i,"trend_bull"] = ma5>ma10>ma20
            cand.at[i,"above_ma5"] = cv>ma5
            cand.at[i,"float_cap"] = round(fc,1)

            cand.at[i,"rsi_score"] = rsi_result["score"]
            cand.at[i,"rsi_val"] = rsi_result["rsi"]
            cand.at[i,"kdj_score"] = kdj_result["score"]
            cand.at[i,"kdj_val"] = f"{kdj_result['K']}/{kdj_result['D']}/{kdj_result['J']}"
            cand.at[i,"wr_score"] = wr_result["score"]

            # ── ATR 动态止损 ──
            atr_val = calc_atr(highs.values, lows.values, closes.values)
            stop_info = calc_dynamic_stop(row["price"], atr_val)
            cand.at[i,"atr"] = round(atr_val, 3)
            cand.at[i,"stop_loss"] = stop_info["stop_price"]
            cand.at[i,"stop_loss_pct"] = stop_info["loss_pct"]
            cand.at[i,"stop_type"] = stop_info["stop_type"]

            vi.append(i)

        res = cand.loc[vi].copy()
        self.L(f"  深度分析通过: {len(res)} 只")
        if len(res) > 0:
            # 展示增强因子亮点
            rsi_bulls = (res["rsi_score"] >= 70).sum()
            kdj_bulls = (res["kdj_score"] >= 70).sum()
            self.L(f"  其中 RSI超卖候选: {rsi_bulls} | KDJ金叉候选: {kdj_bulls}")
        return res

    # ── Step 4: 尾盘微观（v9 增强：加入资金流/VWAP/大单/共振） ──
    def micro(self, cand: pd.DataFrame) -> pd.DataFrame:
        n = len(cand)
        if n == 0: return cand
        self.L(f"\nStep 4: 尾盘微观 + 增强因子D-H (资金流/VWAP/大单/共振) ({n}只)")

        syms = cand["symbol"].tolist()
        intra = self.cli.get_intraday_batch(syms, period="5m", count=60)
        all_quotes = self.cli.get_all_quotes(use_cache=True)
        vi = []

        for i, row in cand.iterrows():
            s = row["symbol"]; idf = intra.get(s)

            # ── v8 原有尾盘微观 ──
            intra_sc = 0.5
            if idf is not None and len(idf) >= 12:
                tail = idf.iloc[-6:]; prev = idf.iloc[-12:-6]
                tc = tail["close"].astype(float); tv = tail["volume"].astype(float)
                pc = prev["close"].astype(float); pv = prev["volume"].astype(float)

                if tc.mean() < pc.mean() * 0.995:
                    if self.debug: self.L(f"  {s} 跳水 排除","DEBUG"); continue
                tp = (tc.iloc[-1]-tc.iloc[0])/tc.iloc[0]
                if tp > 0.02 and tv.mean() > pv.mean()*2:
                    if self.debug: self.L(f"  {s} 急拉 排除","DEBUG"); continue
                vt = np.polyfit(range(6), tv.values, 1)[0]
                pt = np.polyfit(range(6), tc.values, 1)[0]
                if vt > 0 and pt < -0.01:
                    if self.debug: self.L(f"  {s} 量价背离 排除","DEBUG"); continue

                if pt > 0.005: intra_sc += 0.25
                elif abs(pt) < 0.005: intra_sc += 0.15
                if vt < 0: intra_sc += 0.15
                if tv.mean() < pv.mean(): intra_sc += 0.10
                intra_sc = min(intra_sc, 1.0)

            cand.at[i,"intraday_score"] = intra_sc

            # ── v9 新增: 增强因子 D-H ──
            if idf is not None and len(idf) >= 6:
                mf = self.factor_engine.factor_money_flow(idf)
                vw = self.factor_engine.factor_vwap(idf)
                bo = self.factor_engine.factor_big_order(idf)
                tr = self.factor_engine.factor_tail_resonance(idf)
            else:
                mf = {"score": 50, "flow_ratio": 1.0, "net_flow_wan": 0}
                vw = {"score": 50, "deviation_pct": 0}
                bo = {"score": 50, "big_order_ratio": 0}
                tr = {"score": 50, "resonance": False}

            cand.at[i,"money_flow_score"] = mf["score"]
            cand.at[i,"flow_ratio"] = mf.get("flow_ratio", 1.0)
            cand.at[i,"net_flow"] = mf.get("net_flow_wan", 0)
            cand.at[i,"vwap_score"] = vw["score"]
            cand.at[i,"vwap_deviation"] = vw.get("deviation_pct", 0)
            cand.at[i,"big_order_score"] = bo["score"]
            cand.at[i,"big_order_ratio"] = bo.get("big_order_ratio", 0)
            cand.at[i,"tail_resonance_score"] = tr["score"]

            # 板块强度
            sc = self.factor_engine.factor_sector_strength(
                s, row["change_pct"], all_quotes)
            cand.at[i,"sector_score"] = int(sc.get("rank_pct", 50))

            vi.append(i)

        res = cand.loc[vi].copy()
        self.L(f"  微观通过: {len(res)} 只")
        if len(res) > 0:
            mf_bulls = (res["money_flow_score"] >= 65).sum()
            bo_bulls = (res["big_order_score"] >= 70).sum()
            self.L(f"  其中 主力资金流入: {mf_bulls} | 大单活跃: {bo_bulls}")
        return res

    # ── Step 5: 12维评分（v9 核心升级） ──
    def score(self, cand: pd.DataFrame) -> pd.DataFrame:
        n = len(cand)
        self.L(f"\nStep 5: 12维评分 (v9增强) ({n}只)")
        if n == 0: return cand

        for i, row in cand.iterrows():
            sc = {}

            # 1. 涨幅质量 12分
            chg = row["change_pct"]
            if 2.5<=chg<=3.5: s1=12
            elif 3.0<=chg<3.5: s1=11
            elif 2.0<=chg<2.5: s1=10
            elif 3.5<chg<=4.0: s1=9
            elif 4.0<chg<=4.5: s1=7
            else: s1=5
            sc["涨幅质量"]=s1

            # 2. 尾盘强势 12分
            ch = row["close_high"]
            if ch>=0.995: s2=12
            elif ch>=0.99: s2=11
            elif ch>=0.985: s2=10
            elif ch>=0.98: s2=8
            elif ch>=0.975: s2=6
            elif ch>=0.97: s2=4
            else: s2=1
            sc["尾盘强势"]=s2

            # 3. 盘中走势 8分
            iq = row["intraday_quality"]; amp = row["amplitude"]
            if iq>=0.75: s3=8
            elif iq>=0.60: s3=7
            elif iq>=0.45: s3=5
            elif iq>=0.30: s3=3
            elif iq>=0.20: s3=1
            else: s3=0
            if iq<0.25 and amp>6: s3=0
            sc["盘中走势"]=s3

            # 4. 量价配合 8分
            vr = row["volume_ratio"]; intra_sc = row.get("intraday_score",0.5)
            s4=4
            if 1.5<=vr<=2.5: s4+=3
            elif 1.2<=vr<1.5: s4+=2
            elif 2.5<vr<=3.5: s4+=1
            if intra_sc>=0.8: s4+=1
            sc["量价配合"]=min(s4,8)

            # 5. 换手健康 5分
            to = row["turnover"]
            if 4.0<=to<=7.0: s5=5
            elif 3.0<=to<4.0: s5=4
            elif 7.0<to<=9.0: s5=3
            else: s5=2
            sc["换手健康"]=s5

            # 6. 流通市值 5分
            fc = row["float_cap"]
            if 20<=fc<=80: s6=5
            elif 80<fc<=150: s6=4
            elif 10<=fc<20: s6=3
            elif 150<fc<=300: s6=2
            elif fc>0: s6=1
            else: s6=3
            sc["流通市值"]=s6

            # 7. 趋势 4分
            if row["trend_bull"] and row["above_ma5"]: s7=4
            elif row["above_ma5"]: s7=3
            elif row["trend_bull"]: s7=2
            else: s7=1
            sc["趋势强度"]=s7

            # 8. 成交额 3分
            amt = row["amount"]
            if amt>50000: s8=3
            elif amt>25000: s8=2
            elif amt>10000: s8=1
            else: s8=0
            sc["成交额"]=s8

            # ── v9 新增 4 维 ──

            # 9. RSI超卖 12分
            rs = row["rsi_score"]
            if rs>=85: s9=12
            elif rs>=70: s9=10
            elif rs>=55: s9=7
            elif rs>=45: s9=5
            elif rs>=35: s9=3
            else: s9=1  # 超买，不追
            sc["RSI超卖"]=s9

            # 10. KDJ金叉 10分
            ks = row["kdj_score"]
            if ks>=85: s10=10
            elif ks>=70: s10=8
            elif ks>=55: s10=6
            elif ks>=40: s10=4
            else: s10=2
            sc["KDJ金叉"]=s10

            # 11. 主力资金 12分
            ms = row["money_flow_score"]
            if ms>=80: s11=12
            elif ms>=65: s11=10
            elif ms>=55: s11=7
            elif ms>=45: s11=4
            else: s11=1
            sc["主力资金"]=s11

            # 12. VWAP偏离 6分
            vs = row["vwap_score"]
            if vs>=80: s12=6
            elif vs>=65: s12=5
            elif vs>=50: s12=3
            else: s12=1
            sc["VWAP"]=s12

            # ── 附加维度（权重较低，自动计入综合） ──
            # 大单活跃度 5分
            bs = row["big_order_score"]
            sb = 5 if bs>=80 else (4 if bs>=65 else (3 if bs>=50 else (2 if bs>=40 else 1)))

            # 威廉%R 4分
            ws = row["wr_score"]
            sw = 4 if ws>=80 else (3 if ws>=65 else (2 if ws>=50 else 1))

            # 板块强度 4分
            ss = row["sector_score"]
            sp = 4 if ss>=70 else (3 if ss>=55 else (2 if ss>=40 else 1))

            # 尾盘共振 3分
            ts = row["tail_resonance_score"]
            st = 3 if ts>=70 else (2 if ts>=55 else (1 if ts>=45 else 0))

            # ── 综合加权 ──
            total = (
                s1  * self.cfg["weight_change_quality"]    / 0.12 +
                s2  * self.cfg["weight_tail_strength"]     / 0.12 +
                s3  * self.cfg["weight_intraday_quality"]  / 0.08 +
                s4  * self.cfg["weight_volume_health"]     / 0.08 +
                s5  * self.cfg["weight_turnover_health"]   / 0.05 +
                s6  * self.cfg["weight_float_cap"]         / 0.05 +
                s7  * self.cfg["weight_trend"]             / 0.04 +
                s8  * self.cfg["weight_amount"]            / 0.03 +
                s9  * self.cfg["weight_rsi"]               / 0.12 +
                s10 * self.cfg["weight_kdj"]               / 0.10 +
                s11 * self.cfg["weight_money_flow"]        / 0.12 +
                s12 * self.cfg["weight_vwap"]              / 0.06 +
                sb  * self.cfg["weight_big_order"]         / 0.05 +
                sw  * self.cfg["weight_williams_r"]        / 0.04 +
                sp  * self.cfg["weight_sector_strength"]   / 0.04
            )

            cand.at[i,"score_detail"] = {
                "涨幅": s1, "尾盘": s2, "走势": s3, "量价": s4,
                "换手": s5, "市值": s6, "趋势": s7, "成交": s8,
                "RSI": s9, "KDJ": s10, "资金": s11, "VWAP": s12,
            }
            cand.at[i,"total_score"] = round(total, 1)

            # v9: 增强因子综合分（仅增强因子部分）
            enhanced_total = (s9*0.22 + s10*0.18 + s11*0.22 + s12*0.12 +
                             sb*0.10 + sw*0.07 + sp*0.05 + st*0.04)
            cand.at[i,"composite_score"] = round(enhanced_total, 1)

            # v9: 隔天涨跌概率估计
            prob, prob_label = self._estimate_probability(
                rs, ks, ms, row["close_high"], row["change_pct"],
                row["trend_bull"], row["above_ma5"])
            cand.at[i,"probability"] = prob_label

        cand = cand[cand["total_score"] >= self.cfg["min_score"]]
        cand = cand.sort_values("total_score", ascending=False)
        self.L(f"  评分完成 (>={self.cfg['min_score']}分): {len(cand)} 只")
        if len(cand)>0:
            for r,(_,rw) in enumerate(cand.head(3).iterrows(),1):
                m = ["🥇","🥈","🥉"][r-1]
                self.L(f"  [{m}] {rw['name']} - {rw['total_score']:.0f}分 "
                       f"| RSI:{rw['rsi_val']:.0f} KDJ:{rw['kdj_val']} "
                       f"| 资金:{rw['money_flow_score']:.0f} "
                       f"| {rw['probability']}")
        return cand

    def _estimate_probability(self, rsi_score, kdj_score, mf_score,
                               close_high, change_pct, trend_bull, above_ma5) -> Tuple[float, str]:
        """v9: 隔天涨跌概率估计"""
        base = 50.0

        # 增强因子贡献
        base += (rsi_score - 50) * 0.25    # RSI超卖 → +12.5 max
        base += (kdj_score - 50) * 0.20    # KDJ金叉 → +10 max
        base += (mf_score - 50) * 0.25     # 资金流入 → +12.5 max

        # 传统因子贡献
        if close_high >= 0.99: base += 5
        elif close_high >= 0.98: base += 3
        if 2.5 <= change_pct <= 3.5: base += 4
        if trend_bull: base += 3
        if above_ma5: base += 2

        prob = max(35, min(78, base))

        if prob >= 68:
            label = f"🟢 高胜率({prob:.0f}%)"
        elif prob >= 58:
            label = f"🟡 中等({prob:.0f}%)"
        elif prob >= 48:
            label = f"🟠 一般({prob:.0f}%)"
        else:
            label = f"🔴 偏低({prob:.0f}%)"

        return prob, label

    # ── 主流程 ──
    def run(self) -> Optional[pd.DataFrame]:
        print("\n" + "="*55)
        print("  一夜持股法 v9.0 — 增强因子版 TickFlow Pro")
        print("  RSI超卖 + KDJ金叉 + 主力资金 + VWAP 多引擎")
        print("="*55 + "\n")
        today = date.today()

        ok, cf = self.is_trade_day(today)
        if not ok:
            self.L(f"{today} 非交易日"); self._save(None, {}, "NOT_TRADE_DAY", today)
            return None
        if cf < 1.0: self.L(f"日历效应: 仓位x{cf:.0%}")

        ms, me = self.eval_market()
        if ms == "RED":
            self.L("\n大盘回避，不操作"); self._save(None, me, "MARKET_AVOID", today)
            return None

        cand = self.screen()
        if len(cand)==0: self.L("\n无候选"); self._save(None, me, "NO_CANDIDATE", today); return None

        cand = self.deep_ana(cand)
        if len(cand)==0: self.L("\n深度无候选"); self._save(None, me, "NO_DEEP", today); return None

        cand = self.micro(cand)
        if len(cand)==0: self.L("\n微观无候选"); self._save(None, me, "NO_MICRO", today); return None

        cand = self.score(cand)
        if len(cand)==0: self.L(f"\n评分无候选(均<{self.cfg['min_score']})"); self._save(None, me, "NO_SCORE", today); return None

        topn = min(self.cfg["top_output"], len(cand))
        top = cand.head(topn)
        self._save(top, me, "SUCCESS", today)
        self._print(top, cf)
        self.cli.stats()
        self.L(f"\n耗时: {(datetime.now()-self.t0).total_seconds():.1f}秒")
        return top

    def _print(self, top, cf):
        mdls = ["1","2","3","4","5"]
        print("\n" + "="*55)
        print(f"  🎯 TOP{len(top)} 尾盘精选 (v9 增强版)")
        print("="*55)
        for rk, (_, rw) in enumerate(top.iterrows()):
            sc = rw["total_score"]
            bar = "#"*int(sc/5) + "-"*(20-int(sc/5))
            print(f"\n  [{mdls[rk]}] {rw['symbol']} {rw['name']}")
            print(f"      {rw['price']:.2f} +{rw['change_pct']:.2f}% "
                  f"换{rw['turnover']:.1f}% 量比{rw['volume_ratio']:.1f}")
            print(f"      尾盘{rw['close_high']:.3f} 走势{rw['intraday_quality']:.2f} "
                  f"[{bar}] {sc:.0f}/100")
            dims = rw.get("score_detail",{})
            print(f"      " + " | ".join(f"{k}:{v}" for k,v in dims.items()))
            print(f"      RSI:{rw['rsi_val']:.0f} KDJ:{rw['kdj_val']} "
                  f"资金流:{rw['money_flow_score']:.0f} VWAP:{rw['vwap_deviation']:+.1f}% "
                  f"大单:{rw['big_order_ratio']:.0%}")
            print(f"      {rw['probability']} | 流通{rw['float_cap']:.0f}亿 "
                  f"| 买{rw['price']*0.997:.2f}-{rw['price']*1.003:.2f}")
            sl = rw.get("stop_loss", rw['price']*0.97)
            sl_type = rw.get("stop_type", "硬止损")
            atr_v = rw.get("atr", 0)
            print(f"      🛑 止损: {sl:.2f} ({sl_type}, ATR:{atr_v:.3f})")

        print(f"\n  [纪律] +{self.cfg['profit_target_1']*100:.0f}%卖半仓 "
              f"+{self.cfg['profit_target_2']*100:.0f}%清仓 "
              f"低开>{abs(self.cfg['gap_down_stop'])*100:.1f}%竞价止损")
        if cf < 1.0: print(f"  [日历] 仓位x{cf:.0%}")
        print("  TickFlow Pro v9 | 超卖+主力吸筹双引擎 | 仅供研究\n")

    def _save(self, top, me, status, today):
        od = self.cfg["output_dir"]; os.makedirs(od, exist_ok=True)
        res = {
            "strategy": "overnight_v9", "version": "9.0",
            "date": today.isoformat(), "time": datetime.now().strftime("%H:%M:%S"),
            "status": status, "market": me, "candidates": [],
            "config": {
                "change": f"{self.cfg['change_min']}-{self.cfg['change_max']}%",
                "close_high_min": self.cfg["close_high_min"],
                "min_score": self.cfg["min_score"],
                "enhanced_factors": "RSI+KDJ+%R+MoneyFlow+VWAP+BigOrder+Sector+TailResonance"
            }
        }
        if top is not None and len(top)>0:
            cols = ["symbol","name","price","change_pct","turnover","amplitude","amount",
                    "volume_ratio","close_high","intraday_quality","float_cap",
                    "total_score","composite_score",
                    "rsi_val","rsi_score","kdj_val","kdj_score",
                    "money_flow_score","flow_ratio","net_flow",
                    "vwap_score","vwap_deviation",
                    "big_order_score","big_order_ratio",
                    "sector_score","wr_score","tail_resonance_score",
                    "trend_bull","above_ma5","probability",
                    "ma5","ma10","ma20","score_detail"]
            res["candidates"] = top[[c for c in cols if c in top.columns]].to_dict("records")

        with open(os.path.join(od, self.cfg["output_json"]),"w",encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2, default=str)

        # MD 报告
        md = [f"# 一夜持股法 v9.0 报告\n",
              f"**{res['date']} {res['time']}** | {res['status']}\n",
              f"\n## 增强因子\n",
              f"- RSI超卖 + KDJ金叉 + 威廉%R → 超卖反弹引擎",
              f"- 主力资金流 + VWAP偏离 + 大单活跃 → 主力吸筹引擎",
              f"- 板块强度 + 尾盘共振 → 择时引擎\n",
              f"\n## 大盘\n"]
        for cd, dd in me.get("details",{}).items():
            md.append(f"- {cd}: {dd['close']:.2f} MA20:{dd['ma20']:.1f} "
                      f"RSI:{dd.get('rsi','?')} "
                      f"{'OK' if dd.get('above_ma20') else 'BAD'}MA20 "
                      f"{'多头' if dd.get('ma_bull') else '死叉'}")
        md.append("\n## 候选\n")
        if res["candidates"]:
            md.append("|#|代码|名称|现价|涨幅|评分|概率|RSI|KDJ|资金流|")
            md.append("|-|----|----|----|----|----|----|----|----|----|")
            for rk,c in enumerate(res["candidates"],1):
                md.append(f"|{rk}|{c['symbol']}|{c['name']}|{c['price']:.2f}|"
                          f"+{c['change_pct']:.2f}%|{c['total_score']:.0f}|"
                          f"{c.get('probability','?')}|{c.get('rsi_val','?')}|"
                          f"{c.get('kdj_val','?')}|{c.get('money_flow_score','?')}|")
        else:
            md.append("无候选")
        md.extend(["\n## 风险提示\n","- 仅供研究，不构成投资建议\n","- TickFlow Pro v9 增强因子版\n"])
        with open(os.path.join(od, self.cfg["output_md"]),"w",encoding="utf-8") as f:
            f.write("\n".join(md))
        self.L(f"\n结果已保存: {od}/")


# ============================================================
# 入口
# ============================================================
def main():
    p = argparse.ArgumentParser(description="一夜持股法 v9.0 增强因子版")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--config", type=str, default=None)
    a = p.parse_args()

    cfg = load_config()
    if a.config:
        with open(a.config,"r",encoding="utf-8") as f:
            cfg.update({k:v for k,v in json.load(f).items() if not k.startswith("_")})

    try:
        cli = TFClient()
    except Exception as e:
        print(f"\n初始化失败: {e}")
        print("请: pip install tickflow && setx TICKFLOW_API_KEY \"key\"")
        sys.exit(1)

    o = OvernightV9(cli, cfg, debug=a.debug)
    try:
        r = o.run()
        if r is not None and len(r) > 0:
            print("\n✓ v9.0 运行成功")
    except Exception as e:
        print(f"\n异常: {e}")
        import traceback; traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
