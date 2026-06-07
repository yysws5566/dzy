#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一夜持股法 v8.0 - 高胜率优化版 (TickFlow Pro)
===============================================
基于 v3.0 + v7.0 合并优化。

核心优化 (vs 旧版):
  1. 涨幅甜点 2.0-4.0%（收窄，>5%反转风险大）
  2. close/high >= 0.97（尾盘强势度更严）
  3. 量比 1.2-3.0（新增！识别真实资金介入）
  4. 盘中走势质量（处罚尾盘急拉诱多）
  5. 八维加权评分（满分100，<60不选）
  6. 大盘多维评估

运行: python overnight_strategy_v8.py --debug
环境: pip install tickflow pandas numpy
      setx TICKFLOW_API_KEY "your_key"
"""

import os, sys, json, time, math, argparse
from datetime import datetime, date, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd

# ============================================================
# 默认配置（可被 strategy_config.json 覆盖）
# ============================================================
DEFAULT_CONFIG = {
    # 大盘
    "index_codes": ["000001.SH", "399006.SZ"],
    "index_change_min": -0.8,

    # 初筛阈值（收窄以提升胜率）
    "price_min": 5.0,
    "price_max": 50.0,
    "change_min": 2.0,
    "change_max": 4.0,
    "turnover_min": 3.0,
    "turnover_max": 8.0,
    "amplitude_min": 2.0,
    "amplitude_max": 7.0,
    "amount_min": 8000,
    "close_high_min": 0.97,
    "volume_ratio_min": 1.2,
    "volume_ratio_max": 3.0,
    "exclude_limit_up_pct": 0.8,

    # 深度分析
    "kl_count": 60,
    "deep_top_n": 150,
    "top_output": 5,

    # 八维权重
    "weight_change_quality": 0.20,
    "weight_tail_strength": 0.20,
    "weight_intraday_quality": 0.15,
    "weight_volume_health": 0.15,
    "weight_turnover_health": 0.10,
    "weight_float_cap": 0.10,
    "weight_trend": 0.06,
    "weight_amount": 0.04,

    # 风控
    "min_score": 60,
    "base_position": 0.18,
    "friday_position_ratio": 0.5,
    "profit_target_1": 0.015,
    "profit_target_2": 0.03,
    "stop_loss": -0.02,
    "gap_down_stop": -0.005,

    # 输出
    "output_dir": os.path.join(os.path.dirname(os.path.abspath(__file__)), "results"),
    "output_json": "strategy_v8_result.json",
    "output_md": "strategy_v8_result.md",
}


def load_config() -> dict:
    """加载配置"""
    cfg = DEFAULT_CONFIG.copy()
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategy_config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            cfg.update({k: v for k, v in raw.items() if not k.startswith("_")})
        except Exception as e:
            print(f"  [!] 配置读取失败: {e}")
    return cfg


# ============================================================
# TickFlow 客户端
# ============================================================
class TFClient:
    def __init__(self):
        from tickflow import TickFlow
        key = os.environ.get("TICKFLOW_API_KEY", "")
        if not key:
            raise RuntimeError(
                "\nTICKFLOW_API_KEY 未设置!\n"
                "请运行: setx TICKFLOW_API_KEY \"你的TickFlow密钥\"\n"
                "然后重新打开终端。"
            )
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
        self._rl("quotes_uni", 60)
        return self._tf.quotes.get(universes="CN_Equity_A", as_dataframe=True)

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
            except Exception as e:
                print(f"  [WARN] K线失败: {e}")
        return result

    def get_intraday_batch(self, syms: List[str], period="5m", count=60) -> dict:
        try:
            self._rl("intra_batch", 30)
            return self._tf.klines.intraday_batch(
                syms, period=period, count=count, as_dataframe=True)
        except Exception:
            result = {}
            for s in syms:
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
# v8.0 策略
# ============================================================
class OvernightV8:
    def __init__(self, client: TFClient, cfg: dict, debug=False):
        self.cli = client
        self.cfg = cfg
        self.debug = debug
        self.logs = []
        self.t0 = datetime.now()

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
        self.L("="*50)
        self.L("Step 1: 大盘多维评估")
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
            env["details"][code] = {"close": round(cl,2), "ma5": round(ma5,2),
                                    "ma10": round(ma10,2), "ma20": round(ma20,2),
                                    "above_ma20": above, "ma_bull": bull}
            sc = (1 if above else 0) + (1 if bull else 0)
            scores.append(sc)
            ic = "OK" if sc==2 else ("WARN" if sc==1 else "BAD")
            self.L(f"  [{ic}] {code}: {cl:.2f} MA20:{ma20:.1f} | "
                   f"{'MA20上' if above else 'MA20下'} {'多头' if bull else '死叉'}")

        avg = sum(scores)/len(scores) if scores else 0
        if avg >= 1.5:
            env["status"] = "GREEN"; env["position_ratio"] = 1.0
        elif avg >= 1.0:
            env["status"] = "YELLOW"; env["position_ratio"] = 0.5
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
                "volume_ratio": 0, "float_cap": 0,
                "ma5":0,"ma10":0,"ma20":0,
                "trend_bull": False, "above_ma5": False,
                "intraday_score": 0.5,
                "score_detail": {}, "total_score": 0,
            })
        cand.sort(key=lambda x:(x["close_high"],x["change_pct"]), reverse=True)
        if len(cand) > self.cfg["deep_top_n"]:
            self.L(f"  取前 {self.cfg['deep_top_n']} 进深度分析")
            cand = cand[:self.cfg["deep_top_n"]]
        return pd.DataFrame(cand)

    # ── Step 3: 深度分析 ──
    def deep_ana(self, cand: pd.DataFrame) -> pd.DataFrame:
        n = len(cand)
        self.L(f"\nStep 3: 深度分析 ({n}只)")
        if n == 0: return cand

        syms = cand["symbol"].tolist()
        kd = self.cli.get_klines_batch(syms, count=self.cfg["kl_count"])

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
            if kl is None or len(kl) < 10:
                continue
            closes = kl["close"].astype(float)
            vols = kl["volume"].astype(float)
            highs = kl["high"].astype(float)

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

            ma5 = closes.rolling(5).mean().iloc[-1]
            ma10 = closes.rolling(10).mean().iloc[-1]
            ma20 = closes.rolling(20).mean().iloc[-1] if len(closes)>=20 else ma10

            fs = im.get(s,{}).get("fs",0)
            fc = (fs * row["price"])/1e8 if fs>0 else 0

            cand.at[i,"volume_ratio"] = round(vr,2)
            cand.at[i,"ma5"] = round(ma5,2)
            cand.at[i,"ma10"] = round(ma10,2)
            cand.at[i,"ma20"] = round(ma20,2)
            cand.at[i,"trend_bull"] = ma5>ma10>ma20
            cand.at[i,"above_ma5"] = cv>ma5
            cand.at[i,"float_cap"] = round(fc,1)
            vi.append(i)

        res = cand.loc[vi].copy()
        self.L(f"  深度分析通过: {len(res)} 只")
        return res

    # ── Step 4: 尾盘微观 ──
    def micro(self, cand: pd.DataFrame) -> pd.DataFrame:
        n = len(cand)
        if n == 0: return cand
        self.L(f"\nStep 4: 尾盘微观检测 ({n}只)")

        syms = cand["symbol"].tolist()
        intra = self.cli.get_intraday_batch(syms, period="5m", count=60)
        vi = []

        for i, row in cand.iterrows():
            s = row["symbol"]; idf = intra.get(s)
            if idf is None or len(idf) < 12:
                cand.at[i,"intraday_score"] = 0.5; vi.append(i); continue

            tail, prev = idf.iloc[-6:], idf.iloc[-12:-6]
            tc = tail["close"].astype(float); tv = tail["volume"].astype(float)
            pc = prev["close"].astype(float); pv = prev["volume"].astype(float)

            if tc.mean() < pc.mean() * 0.995:  # 尾盘跳水
                if self.debug: self.L(f"  {s} 跳水 排除","DEBUG"); continue
            tp = (tc.iloc[-1]-tc.iloc[0])/tc.iloc[0]
            if tp > 0.02 and tv.mean() > pv.mean()*2:  # 放量急拉
                if self.debug: self.L(f"  {s} 急拉 排除","DEBUG"); continue
            vt = np.polyfit(range(6), tv.values, 1)[0]
            pt = np.polyfit(range(6), tc.values, 1)[0]
            if vt > 0 and pt < -0.01:  # 量价背离
                if self.debug: self.L(f"  {s} 量价背离 排除","DEBUG"); continue

            sc = 0.5
            if pt > 0.005: sc += 0.25
            elif abs(pt) < 0.005: sc += 0.15
            if vt < 0: sc += 0.15
            if tv.mean() < pv.mean(): sc += 0.10

            cand.at[i,"intraday_score"] = min(sc, 1.0)
            vi.append(i)

        res = cand.loc[vi].copy()
        self.L(f"  微观通过: {len(res)} 只")
        return res

    # ── Step 5: 八维评分 ──
    def score(self, cand: pd.DataFrame) -> pd.DataFrame:
        n = len(cand)
        self.L(f"\nStep 5: 八维评分 ({n}只)")
        if n == 0: return cand

        for i, row in cand.iterrows():
            sc = {}

            # 1.涨幅质量 20分
            chg = row["change_pct"]
            if 2.5<=chg<=3.5: s1=20
            elif 3.0<=chg<3.5: s1=18
            elif 2.0<=chg<2.5: s1=16
            elif 3.5<chg<=4.0: s1=14
            else: s1=8
            sc["涨幅质量"]=s1

            # 2.尾盘强势 20分
            ch = row["close_high"]
            if ch>=0.995: s2=20
            elif ch>=0.99: s2=18
            elif ch>=0.985: s2=16
            elif ch>=0.98: s2=13
            elif ch>=0.975: s2=10
            elif ch>=0.97: s2=6
            else: s2=2
            sc["尾盘强势"]=s2

            # 3.盘中走势 15分
            iq = row["intraday_quality"]; amp = row["amplitude"]
            if iq>=0.75: s3=15
            elif iq>=0.60: s3=13
            elif iq>=0.45: s3=10
            elif iq>=0.30: s3=6
            elif iq>=0.20: s3=2
            else: s3=0
            if iq<0.25 and amp>5: s3=0
            sc["盘中走势"]=s3

            # 4.量价配合 15分
            vr = row["volume_ratio"]; intra_sc = row.get("intraday_score",0.5)
            s4=7
            if 1.5<=vr<=2.5: s4+=5
            elif 1.2<=vr<1.5: s4+=3
            elif 2.5<vr<=3.0: s4+=1
            if intra_sc>=0.8: s4+=3
            sc["量价配合"]=min(s4,15)

            # 5.换手 10分
            to = row["turnover"]
            if 4.0<=to<=7.0: s5=10
            elif 3.0<=to<4.0: s5=8
            elif 7.0<to<=8.0: s5=6
            else: s5=4
            sc["换手健康"]=s5

            # 6.流通市值 10分
            fc = row["float_cap"]
            if 20<=fc<=80: s6=10
            elif 80<fc<=150: s6=8
            elif 10<=fc<20: s6=6
            elif 150<fc<=300: s6=5
            elif fc>0: s6=3
            else: s6=5
            sc["流通市值"]=s6

            # 7.趋势 6分
            if row["trend_bull"] and row["above_ma5"]: s7=6
            elif row["above_ma5"]: s7=4
            elif row["trend_bull"]: s7=3
            else: s7=1
            sc["趋势强度"]=s7

            # 8.成交额 4分
            amt = row["amount"]
            if amt>50000: s8=4
            elif amt>25000: s8=3
            elif amt>10000: s8=2
            else: s8=1
            sc["成交额"]=s8

            total = (
                s1*self.cfg["weight_change_quality"]/0.20 +
                s2*self.cfg["weight_tail_strength"]/0.20 +
                s3*self.cfg["weight_intraday_quality"]/0.15 +
                s4*self.cfg["weight_volume_health"]/0.15 +
                s5*self.cfg["weight_turnover_health"]/0.10 +
                s6*self.cfg["weight_float_cap"]/0.10 +
                s7*self.cfg["weight_trend"]/0.06 +
                s8*self.cfg["weight_amount"]/0.04
            )
            cand.at[i,"score_detail"] = sc
            cand.at[i,"total_score"] = round(total,1)

        cand = cand[cand["total_score"] >= self.cfg["min_score"]]
        cand = cand.sort_values("total_score", ascending=False)
        self.L(f"  评分完成 (>={self.cfg['min_score']}分): {len(cand)} 只")
        if len(cand)>0:
            for r,(_,rw) in enumerate(cand.head(3).iterrows(),1):
                m = ["1st","2nd","3rd"][r-1]
                self.L(f"  [{m}] {rw['name']} - {rw['total_score']:.0f}分")
        return cand

    # ── 主流程 ──
    def run(self) -> Optional[pd.DataFrame]:
        print("\n" + "="*55)
        print("  一夜持股法 v8.0 - TickFlow Pro")
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
        print(f"  TOP{len(top)} 尾盘精选 (14:40)")
        print("="*55)
        for rk, (_, rw) in enumerate(top.iterrows()):
            sc = rw["total_score"]; bar = "#"*int(sc/5) + "-"*(20-int(sc/5))
            print(f"\n  [{mdls[rk]}] {rw['symbol']} {rw['name']}")
            print(f"      {rw['price']:.2f} +{rw['change_pct']:.2f}% 换{rw['turnover']:.1f}% 量比{rw['volume_ratio']:.1f}")
            print(f"      尾盘{rv['close_high']:.3f} 走势{rv['intraday_quality']:.2f} [{bar}] {sc:.0f}/100")
            dims = rw.get("score_detail",{})
            print(f"      " + " | ".join(f"{k}:{v}" for k,v in dims.items()))
            cap_s = f"{rw['float_cap']:.0f}亿" if rw['float_cap']>0 else "N/A"
            tr_s = "多头" if rw['trend_bull'] else ("MA5上" if rw['above_ma5'] else "偏弱")
            print(f"      流通{cap_s} | {tr_s} | 买{rw['price']*0.997:.2f}-{rw['price']*1.003:.2f}")

        print(f"\n  [纪律] +{self.cfg['profit_target_1']*100:.0f}%卖半仓 "
              f"+{self.cfg['profit_target_2']*100:.0f}%清仓 "
              f"{self.cfg['stop_loss']*100:.0f}%止损 "
              f"低开>{abs(self.cfg['gap_down_stop'])*100:.1f}%竞价止损")
        if cf < 1.0: print(f"  [日历] 仓位x{cf:.0%}")
        print("  TickFlow Pro | 仅供研究参考\n")

    def _save(self, top, me, status, today):
        od = self.cfg["output_dir"]; os.makedirs(od, exist_ok=True)
        res = {"strategy":"overnight_v8","version":"8.0",
               "date": today.isoformat(),"time": datetime.now().strftime("%H:%M:%S"),
               "status": status,"market": me,"candidates":[],
               "config": {"change":f"{self.cfg['change_min']}-{self.cfg['change_max']}%",
                          "turnover":f"{self.cfg['turnover_min']}-{self.cfg['turnover_max']}%",
                          "close_high_min":self.cfg["close_high_min"],
                          "volume_ratio":f"{self.cfg['volume_ratio_min']}-{self.cfg['volume_ratio_max']}",
                          "min_score":self.cfg["min_score"]}}
        if top is not None and len(top)>0:
            cols = ["symbol","name","price","change_pct","turnover","amplitude","amount",
                    "volume_ratio","close_high","intraday_quality","float_cap",
                    "total_score","trend_bull","above_ma5","ma5","ma10","ma20","score_detail"]
            res["candidates"] = top[[c for c in cols if c in top.columns]].to_dict("records")

        with open(os.path.join(od, self.cfg["output_json"]),"w",encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2, default=str)

        # MD
        md = [f"# 一夜持股法 v8.0 报告\n",
              f"**{res['date']} {res['time']}** | {res['status']}\n",
              f"\n## 策略配置\n",
              f"- 涨幅{res['config']['change']} 换手{res['config']['turnover']}",
              f" close/high>={res['config']['close_high_min']} 量比{res['config']['volume_ratio']}",
              f" 最低{res['config']['min_score']}分\n",
              f"\n## 大盘\n"]
        for cd, dd in me.get("details",{}).items():
            md.append(f"- {cd}: {dd['close']:.2f} MA20:{dd['ma20']:.1f} "
                      f"{'OK' if dd.get('above_ma20') else 'BAD'}MA20 "
                      f"{'多头' if dd.get('ma_bull') else '死叉'}")
        md.append("\n## 候选\n")
        if res["candidates"]:
            md.append("|#|代码|名称|现价|涨幅|换手|量比|评分|")
            md.append("|-|----|----|----|----|----|----|----|")
            for rk,c in enumerate(res["candidates"],1):
                md.append(f"|{rk}|{c['symbol']}|{c['name']}|{c['price']:.2f}|"
                          f"+{c['change_pct']:.2f}%|{c['turnover']:.1f}%|{c['volume_ratio']:.1f}|{c['total_score']:.0f}|")
            md.append("\n### 维度")
            for c in res["candidates"]:
                dims = " | ".join(f"{k}:{v}" for k,v in c.get("score_detail",{}).items())
                md.append(f"- **{c['name']}**({c['total_score']:.0f}): {dims}")
        else:
            md.append("无候选")
        md.extend(["\n## 风险提示\n","- 仅供研究，不构成投资建议\n","- TickFlow Pro\n"])
        with open(os.path.join(od, self.cfg["output_md"]),"w",encoding="utf-8") as f:
            f.write("\n".join(md))

        self.L(f"\n结果已保存: {od}/")


# ============================================================
# 入口
# ============================================================
def main():
    p = argparse.ArgumentParser(description="一夜持股法 v8.0")
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

    o = OvernightV8(cli, cfg, debug=a.debug)
    try:
        o.run()
    except Exception as e:
        print(f"\n异常: {e}")
        import traceback; traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
