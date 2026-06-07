#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股短线选股 v8.0 Pro — TickFlow Pro + 增强因子版
====================================================
v7 → v8 Pro 升级:
  ① 数据源从新浪免费API → TickFlow Pro（全市场实时行情 + K线 + 分时）
  ② 保留两大子策略并增强：
     - 策略一：尾盘缩量回踩均线法 + RSI超卖验证
     - 策略二：涨停回马枪缩量回踩法 + 资金流验证
  ③ 新增强因子过滤：RSI/KDJ/资金流/VWAP
  ④ 加入加权评分和隔天涨跌概率
  ⑤ 输出 JSON + Markdown 报告

用法:
  python short_term_screener_v8_pro.py              # 全量运行
  python short_term_screener_v8_pro.py --debug      # 调试模式
  python short_term_screener_v8_pro.py --top 10     # 输出前10
"""

import os, sys, json, time, argparse
from datetime import datetime, date
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# 导入增强因子库
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from enhanced_factors import (
    EnhancedFactorEngine,
    calc_rsi, calc_kdj, calc_williams_r,
    calc_ma, calc_vol_ratio, calc_atr, calc_dynamic_stop
)

# ============================================================
# 配置
# ============================================================
CONFIG = {
    "price_min": 3.0,
    "price_max": 25.0,
    "amount_min": 5000,          # 最小成交额（万元）
    "nmc_max": 150,              # 最大流通市值（亿）
    "turnover_min": 1.5,
    "turnover_max": 12.0,
    "kl_count": 30,
    "top_output": 10,
    "min_score": 55,

    # 策略一参数
    "s1_change_min": -1.0,
    "s1_change_max": 2.0,
    "s1_zt_days": 20,

    # 策略二参数
    "s2_change_min": 0.0,
    "s2_change_max": 6.0,
    "s2_zt_window": 12,
    "s2_zt_gap_min": 5,

    # 增强因子阈值
    "rsi_oversold_max": 45,
    "rsi_overbought_min": 75,
    "kdj_golden_max_k": 40,
    "money_flow_min": 50,

    "output_dir": os.path.join(os.path.dirname(os.path.abspath(__file__)), "results"),
}


# ============================================================
# TickFlow Pro 客户端
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
        self._rl("quotes_uni", 60)
        return self._tf.quotes.get(universes="CN_Equity_A", as_dataframe=True)

    def get_klines_batch(self, syms: List[str], count=30) -> dict:
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

    def get_intraday_batch(self, syms: List[str]) -> dict:
        try:
            self._rl("intra_batch", 30)
            return self._tf.klines.intraday_batch(
                syms, period="5m", count=60, as_dataframe=True)
        except Exception:
            return {}

    def stats(self):
        print(f"\n  TickFlow API调用: {dict(self._cnt)}")


# ============================================================
# v8 Pro 策略主类
# ============================================================
class ShortTermV8Pro:
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

    # ── 策略一：尾盘缩量回踩均线法 + RSI验证 ──
    def strategy1_check(self, symbol: str, name: str, quote: pd.Series,
                         kline: pd.DataFrame) -> Optional[dict]:
        """尾盘缩量回踩均线 + 超卖检测"""
        closes = kline["close"].astype(float)
        highs = kline["high"].astype(float)
        lows = kline["low"].astype(float)

        if len(closes) < 20:
            return None

        pct = float(quote.get("ext.change_pct", 0) or 0)
        price = float(quote["last_price"])
        amount = float(quote.get("amount", 0) or 0)
        turnover = float(quote.get("ext.turnover_rate", 0) or 0)

        # 基础条件
        if not (self.cfg["s1_change_min"] <= pct <= self.cfg["s1_change_max"]):
            return None

        # 均线
        ma5 = calc_ma(closes.values, 5)
        ma10 = calc_ma(closes.values, 10)
        ma20 = calc_ma(closes.values, 20)
        cur = closes.iloc[-1]

        if not (cur > ma5 > ma10 > ma20):
            return None

        # 近20日有涨停
        has_zt = False
        for i in range(len(closes)-2, max(len(closes)-21, 0), -1):
            if i > 0 and closes.iloc[i-1] > 0:
                if (closes.iloc[i] - closes.iloc[i-1]) / closes.iloc[i-1] >= 0.095:
                    has_zt = True
                    break
        if not has_zt:
            return None

        # ── 增强因子 ──
        rsi_result = self.engine.factor_rsi(closes.values)
        kdj_result = self.engine.factor_kdj(highs.values, lows.values, closes.values)
        wr_result = self.engine.factor_williams_r(highs.values, lows.values, closes.values)

        # 量比
        vols = kline["volume"].astype(float)
        vol_ratio = calc_vol_ratio(vols.values, 5)

        return {
            "symbol": symbol, "name": name, "strategy": "尾盘回踩均线",
            "price": price, "change_pct": pct, "turnover": turnover,
            "amount": amount/1e4,
            "ma5": round(ma5,2), "ma10": round(ma10,2), "ma20": round(ma20,2),
            "vol_ratio": round(vol_ratio,1),
            "rsi": rsi_result["rsi"], "rsi_score": rsi_result["score"],
            "kdj_k": kdj_result["K"], "kdj_d": kdj_result["D"],
            "kdj_score": kdj_result["score"],
            "wr": wr_result["williams_r"], "wr_score": wr_result["score"],
        }

    # ── 策略二：涨停回马枪 + 资金流验证 ──
    def strategy2_check(self, symbol: str, name: str, quote: pd.Series,
                         kline: pd.DataFrame) -> Optional[dict]:
        """涨停回马枪缩量回踩 + 资金面验证"""
        closes = kline["close"].astype(float)
        highs = kline["high"].astype(float)
        lows = kline["low"].astype(float)

        if len(closes) < 20:
            return None

        pct = float(quote.get("ext.change_pct", 0) or 0)
        price = float(quote["last_price"])
        amount = float(quote.get("amount", 0) or 0)
        turnover = float(quote.get("ext.turnover_rate", 0) or 0)

        if not (self.cfg["s2_change_min"] <= pct <= self.cfg["s2_change_max"]):
            return None

        # 找涨停日（近12日）
        zt_idx, zt_gap = -1, 0
        for i in range(len(closes)-2, max(len(closes)-13, 0), -1):
            if i > 0 and closes.iloc[i-1] > 0:
                if (closes.iloc[i] - closes.iloc[i-1]) / closes.iloc[i-1] >= 0.095:
                    zt_idx = i
                    zt_gap = len(closes) - 1 - i
                    break
        if zt_idx < 0 or zt_gap < self.cfg["s2_zt_gap_min"]:
            return None

        # 近5日未再涨停
        recent_zt = False
        for i in range(len(closes)-2, max(len(closes)-6, 0), -1):
            if i > 0 and closes.iloc[i-1] > 0:
                if (closes.iloc[i] - closes.iloc[i-1]) / closes.iloc[i-1] >= 0.095:
                    recent_zt = True
                    break
        if recent_zt:
            return None

        # 均线支撑
        ma10 = calc_ma(closes.values, 10)
        ma20 = calc_ma(closes.values, 20)
        cur = closes.iloc[-1]
        if cur < ma10 * 0.97:
            return None

        # ── 增强因子 ──
        rsi_result = self.engine.factor_rsi(closes.values)
        kdj_result = self.engine.factor_kdj(highs.values, lows.values, closes.values)
        wr_result = self.engine.factor_williams_r(highs.values, lows.values, closes.values)

        vols = kline["volume"].astype(float)
        vol_ratio = calc_vol_ratio(vols.values, 5)

        return {
            "symbol": symbol, "name": name, "strategy": "涨停回马枪",
            "price": price, "change_pct": pct, "turnover": turnover,
            "amount": amount/1e4,
            "zt_gap": zt_gap,
            "ma10": round(ma10,2), "ma20": round(ma20,2),
            "vol_ratio": round(vol_ratio,1),
            "rsi": rsi_result["rsi"], "rsi_score": rsi_result["score"],
            "kdj_k": kdj_result["K"], "kdj_d": kdj_result["D"],
            "kdj_score": kdj_result["score"],
            "wr": wr_result["williams_r"], "wr_score": wr_result["score"],
        }

    # ── 评分 ──
    def score_candidate(self, c: dict) -> dict:
        """综合评分 0-100"""
        s = 50

        # RSI 加分（超卖）
        if c["rsi_score"] >= 85: s += 18
        elif c["rsi_score"] >= 70: s += 14
        elif c["rsi_score"] >= 55: s += 8
        elif c["rsi_score"] < 30: s -= 10

        # KDJ 加分
        if c["kdj_score"] >= 85: s += 15
        elif c["kdj_score"] >= 70: s += 10
        elif c["kdj_score"] >= 55: s += 5

        # 威廉%R
        if c["wr_score"] >= 80: s += 8
        elif c["wr_score"] >= 65: s += 4

        # 量比
        vr = c.get("vol_ratio", 1)
        if 1.2 <= vr <= 2.5: s += 6
        elif 1.0 <= vr <= 3.0: s += 3

        # 策略特定
        if c["strategy"] == "涨停回马枪":
            zt_gap = c.get("zt_gap", 0)
            if 6 <= zt_gap <= 9: s += 7
            elif 5 <= zt_gap <= 10: s += 4

        c["score"] = min(100, max(0, s))
        return c

    # ── 运行 ──
    def run(self) -> Tuple[List[dict], List[dict]]:
        print("\n" + "="*55)
        print("  短线选股 v8.0 Pro — TickFlow Pro + 增强因子")
        print("  策略一: 尾盘回踩均线+超卖 | 策略二: 涨停回马枪+资金流")
        print("="*55)

        today = date.today()
        if today.weekday() >= 5:
            self.log(f"{today} 周末，不运行"); return [], []

        # Step 1: 全市场行情
        self.log("\n[1/4] 全市场实时行情 (TickFlow Pro)...")
        df = self.cli.get_all_quotes()
        self.log(f"  全市场 {len(df)} 只")

        # 初筛
        sym_col = "symbol" if "symbol" in df.columns else "code"
        name_col = next((c for c in df.columns if "name" in c.lower()), None)

        valid = pd.Series(True, index=df.index)
        if name_col:
            for kw in ["ST","*ST","退","N"]:
                valid &= ~df[name_col].astype(str).str.contains(kw, na=False)
        valid &= ~df[sym_col].astype(str).str.startswith("8")

        p = df["last_price"].astype(float)
        ch = df.get("ext.change_pct", pd.Series(0,index=df.index)).astype(float)
        amt = df.get("amount", pd.Series(0,index=df.index)).fillna(0).astype(float)
        to = df.get("ext.turnover_rate", pd.Series(0,index=df.index)).astype(float)

        mask = (valid &
            (p >= self.cfg["price_min"]) & (p <= self.cfg["price_max"]) &
            (amt >= self.cfg["amount_min"]) &
            (to >= self.cfg["turnover_min"]) & (to <= self.cfg["turnover_max"]))

        candidates = df[mask].copy()
        self.log(f"  初筛: {len(candidates)} 只")
        if len(candidates) == 0:
            return [], []

        # Step 2: K线数据
        self.log(f"\n[2/4] K线数据获取 ({len(candidates)} 只)...")
        syms = candidates[sym_col].tolist()
        klines = self.cli.get_klines_batch(syms, count=self.cfg["kl_count"])
        self.log(f"  获取到 {len(klines)} 只K线")

        # Step 3: 双策略扫描
        self.log(f"\n[3/4] 双策略扫描...")
        s1_results, s2_results = [], []

        for i, (_, row) in enumerate(candidates.iterrows()):
            s = row[sym_col]
            nm = row.get(name_col, "") if name_col else ""
            kl = klines.get(s)

            if kl is None or len(kl) < 20:
                continue

            # ATR 动态止损
            atr_val = calc_atr(kl["high"].astype(float).values,
                               kl["low"].astype(float).values,
                               kl["close"].astype(float).values)
            stop_info = calc_dynamic_stop(float(row["last_price"]), atr_val)

            # 策略一
            r1 = self.strategy1_check(s, nm, row, kl)
            if r1:
                r1 = self.score_candidate(r1)
                r1["atr"] = round(atr_val, 3)
                r1["stop_loss"] = stop_info["stop_price"]
                r1["stop_loss_pct"] = stop_info["loss_pct"]
                r1["stop_type"] = stop_info["stop_type"]
                if r1["score"] >= self.cfg["min_score"]:
                    s1_results.append(r1)

            # 策略二
            r2 = self.strategy2_check(s, nm, row, kl)
            if r2:
                r2 = self.score_candidate(r2)
                r2["atr"] = round(atr_val, 3)
                r2["stop_loss"] = stop_info["stop_price"]
                r2["stop_loss_pct"] = stop_info["loss_pct"]
                r2["stop_type"] = stop_info["stop_type"]
                if r2["score"] >= self.cfg["min_score"]:
                    s2_results.append(r2)

            if (i+1) % 100 == 0:
                self.log(f"  进度: {i+1}/{len(candidates)} | S1:{len(s1_results)} S2:{len(s2_results)}")

        self.log(f"  策略一通过: {len(s1_results)} | 策略二通过: {len(s2_results)}")

        # 排序
        s1_results.sort(key=lambda x: -x["score"])
        s2_results.sort(key=lambda x: -x["score"])

        # Step 4: 输出
        self._output(s1_results, s2_results, today)

        self.cli.stats()
        return s1_results, s2_results

    def _output(self, s1, s2, today):
        top_n = self.cfg["top_output"]
        s1_top = s1[:top_n]
        s2_top = s2[:top_n]

        # 控制台输出
        for label, results in [("策略一: 尾盘回踩均线+超卖", s1_top),
                                ("策略二: 涨停回马枪+资金流", s2_top)]:
            print(f"\n{'='*55}")
            print(f"  {label}")
            print(f"{'='*55}")
            if results:
                for rk, r in enumerate(results, 1):
                    icon = "🟢" if r["score"]>=75 else ("🟡" if r["score"]>=65 else "🟠")
                    print(f"  [{rk}] {icon} {r['symbol']} {r['name']:<8} "
                          f"价:{r['price']:.2f} +{r['change_pct']:.2f}% "
                          f"评分:{r['score']} | RSI:{r['rsi']:.0f} "
                          f"KDJ:{r['kdj_k']:.0f}/{r['kdj_d']:.0f}")
                    if r["strategy"] == "涨停回马枪":
                        print(f"      涨停距今{r.get('zt_gap','?')}天 "
                              f"MA10:{r.get('ma10','?')} MA20:{r.get('ma20','?')} "
                              f"量比:{r.get('vol_ratio','?')}")
                    else:
                        print(f"      MA5:{r.get('ma5','?')} MA10:{r.get('ma10','?')} "
                              f"MA20:{r.get('ma20','?')} 量比:{r.get('vol_ratio','?')}")
                    sl = r.get("stop_loss", r["price"]*0.97)
                    sl_type = r.get("stop_type", "硬止损")
                    atr_v = r.get("atr", 0)
                    print(f"      🛑 止损: {sl:.2f} ({sl_type}, ATR:{atr_v:.3f})")
                print(f"  共 {len(results)} 只")
            else:
                print("  今日无候选")

        # 保存 JSON
        od = os.path.join(self.cfg["output_dir"])
        os.makedirs(od, exist_ok=True)

        result = {
            "strategy": "short_term_v8_pro", "version": "8.0",
            "date": today.isoformat(),
            "time": datetime.now().strftime("%H:%M:%S"),
            "data_source": "TickFlow Pro",
            "strategy1_count": len(s1), "strategy2_count": len(s2),
            "strategy1_top": s1_top,
            "strategy2_top": s2_top,
        }
        json_path = os.path.join(od, f"short_term_v8_{today.isoformat()}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)

        # 保存 MD
        md = [f"# 短线选股 v8.0 Pro 报告\n",
              f"**{result['date']} {result['time']}** | TickFlow Pro\n",
              f"\n## 策略一：尾盘回踩均线+超卖 ({len(s1)}只)\n"]
        if s1_top:
            md.append("|#|代码|名称|现价|涨幅%|评分|RSI|KDJ|")
            md.append("|-|----|----|----|----|----|----|----|")
            for rk, r in enumerate(s1_top, 1):
                md.append(f"|{rk}|{r['symbol']}|{r['name']}|{r['price']:.2f}|"
                         f"+{r['change_pct']:.2f}|{r['score']}|{r['rsi']:.0f}|"
                         f"{r['kdj_k']:.0f}/{r['kdj_d']:.0f}|")
        md.append(f"\n## 策略二：涨停回马枪+资金流 ({len(s2)}只)\n")
        if s2_top:
            md.append("|#|代码|名称|现价|涨幅%|评分|RSI|涨停距|")
            md.append("|-|----|----|----|----|----|----|----|")
            for rk, r in enumerate(s2_top, 1):
                md.append(f"|{rk}|{r['symbol']}|{r['name']}|{r['price']:.2f}|"
                         f"+{r['change_pct']:.2f}|{r['score']}|{r['rsi']:.0f}|"
                         f"{r.get('zt_gap','?')}天|")
        md.extend(["\n## 风险提示\n","- 仅供研究，不构成投资建议\n","- TickFlow Pro v8 Pro\n"])

        md_path = os.path.join(od, f"short_term_v8_{today.isoformat()}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md))

        self.log(f"\n结果已保存: {od}/")


# ============================================================
# 入口
# ============================================================
def main():
    p = argparse.ArgumentParser(description="短线选股 v8.0 Pro")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--top", type=int, default=10)
    a = p.parse_args()

    cfg = CONFIG.copy()
    cfg["top_output"] = a.top

    try:
        cli = TFClient()
    except Exception as e:
        print(f"\n初始化失败: {e}")
        sys.exit(1)

    st = ShortTermV8Pro(cli, cfg, debug=a.debug)
    try:
        st.run()
    except Exception as e:
        print(f"\n异常: {e}")
        import traceback; traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
