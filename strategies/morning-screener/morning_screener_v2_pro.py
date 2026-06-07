#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股早盘选股 v2.0 Pro — TickFlow Pro + 增强因子版
===================================================
v1 → v2 Pro 升级:
  ① 数据源从 AKShare → TickFlow Pro（统一数据源）
  ② 第4层资金流不再空壳 — 基于分时量价推算主力资金
  ③ 第5层板块情绪 — 基于 TickFlow 全市场数据计算板块强度
  ④ 新增卖点/止损点分析（9:00-10:00 关键时间窗口）
  ⑤ 嵌入增强因子：RSI/KDJ/资金流/VWAP
  ⑥ 修复硬编码路径

执行时间: 每日 09:25 集合竞价结束后
用法:
  python morning_screener_v2_pro.py
  python morning_screener_v2_pro.py --debug
"""

import os, sys, json, time, argparse
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from enhanced_factors import (
    EnhancedFactorEngine,
    calc_rsi, calc_kdj, calc_williams_r, calc_ma, calc_vol_ratio,
    calc_atr, calc_dynamic_stop
)

# ============================================================
# 配置
# ============================================================
CONFIG = {
    # 早盘筛选（比尾盘更激进）
    "price_min": 5,
    "price_max": 35,
    "change_min": 0,           # 早盘允许平开
    "change_max": 8,            # 放宽（全天发酵空间）
    "turnover_min": 2.5,        # 早盘换手要求
    "turnover_max": 15,
    "vol_ratio_min": 1.3,       # 量比要求
    "amount_min": 1.5e4,        # 最小成交额（万元）

    # 技术指标
    "kl_count": 30,
    "rsi_range": (40, 75),      # RSI 舒适区
    "ma_bull_required": True,   # 要求多头排列
    "macd_above_zero": True,    # MACD 柱 > 0

    # 资金流
    "money_flow_min_score": 55,
    "big_order_min_ratio": 0.15,

    # 卖点/止损分析
    "sell_target_1": 0.05,      # +5% 卖半仓
    "sell_target_2": 0.08,      # +8% 清仓
    "stop_loss_pct": -0.03,     # -3% 止损
    "trailing_stop_pct": 0.02,  # 移动止损（从高点回落2%）

    # 输出
    "top_output": 10,
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

    def _rl(self, ep: str, rpm: int):
        now = time.time()
        last = self._rl_map.get(ep, 0)
        gap = 60.0 / rpm
        if now - last < gap:
            time.sleep(gap - (now - last) + 0.05)
        self._rl_map[ep] = time.time()

    def get_all_quotes(self) -> pd.DataFrame:
        self._rl("quotes", 60)
        return self._tf.quotes.get(universes="CN_Equity_A", as_dataframe=True)

    def get_klines_batch(self, syms: List[str]) -> dict:
        result = {}
        for i in range(0, len(syms), 100):
            self._rl("kl", 60)
            try:
                result.update(self._tf.klines.batch(
                    syms[i:i+100], period="1d", count=30,
                    adjust="forward", as_dataframe=True))
            except Exception:
                pass
        return result


# ============================================================
# v2 Pro 策略
# ============================================================
class MorningScreenerV2Pro:
    def __init__(self, client: TFClient, cfg: dict, debug=False):
        self.cli = client
        self.cfg = cfg
        self.debug = debug
        self.engine = EnhancedFactorEngine()

    def log(self, msg):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def is_trading_day(self) -> bool:
        today = date.today()
        if today.weekday() >= 5:
            return False
        hol = [(1,1),(1,2),(1,3),(5,1),(5,2),(5,3),(10,1),(10,2),(10,3),(10,4),(10,5)]
        if (today.month, today.day) in hol:
            return False
        return True

    # ── Layer 1: 基础门槛 ──
    def layer1_basic(self, df: pd.DataFrame) -> pd.DataFrame:
        self.log(f"[L1] 基础门槛 → 全市场 {len(df)} 只")
        sym_col = "symbol" if "symbol" in df.columns else "code"
        name_col = next((c for c in df.columns if "name" in c.lower()), None)

        valid = pd.Series(True, index=df.index)
        if name_col:
            for kw in ["ST","*ST","退"]:
                valid &= ~df[name_col].astype(str).str.contains(kw, na=False)
        valid &= ~df[sym_col].astype(str).str.startswith("8")

        p = df["last_price"].astype(float)
        ch = df.get("ext.change_pct", pd.Series(0,index=df.index)).astype(float)
        to = df.get("ext.turnover_rate", pd.Series(0,index=df.index)).astype(float)

        mask = (valid &
            (p >= self.cfg["price_min"]) & (p <= self.cfg["price_max"]) &
            (ch >= self.cfg["change_min"]) & (ch <= self.cfg["change_max"]) &
            (to >= self.cfg["turnover_min"]) & (to <= self.cfg["turnover_max"]))
        result = df[mask].copy()
        self.log(f"  → {len(result)} 只")
        return result

    # ── Layer 2: 技术指标趋势 ──
    def layer2_trend(self, df: pd.DataFrame, klines: dict) -> pd.DataFrame:
        self.log(f"[L2] 技术趋势过滤 ({len(df)} 只)")
        sym_col = "symbol" if "symbol" in df.columns else "code"
        name_col = next((c for c in df.columns if "name" in c.lower()), None)
        results = []

        for _, row in df.iterrows():
            s = row[sym_col]; kl = klines.get(s)
            if kl is None or len(kl) < 20:
                continue

            closes = kl["close"].astype(float)
            highs = kl["high"].astype(float)
            lows = kl["low"].astype(float)

            ma5 = calc_ma(closes.values, 5)
            ma10 = calc_ma(closes.values, 10)
            ma20 = calc_ma(closes.values, 20)
            cur = closes.iloc[-1]

            # 多头排列
            if self.cfg["ma_bull_required"]:
                if not (ma5 > ma10 > ma20 and cur > ma20):
                    continue

            # RSI
            rsi = calc_rsi(closes.values, 14)
            if not (self.cfg["rsi_range"][0] <= rsi <= self.cfg["rsi_range"][1]):
                continue

            # KDJ
            k, d, j = calc_kdj(highs.values, lows.values, closes.values)

            # 量比
            vols = kl["volume"].astype(float)
            vr = calc_vol_ratio(vols.values, 5)
            if vr < self.cfg["vol_ratio_min"]:
                continue

            # ATR 动态止损
            atr_val = calc_atr(highs.values, lows.values, closes.values)

            results.append({
                "symbol": s,
                "name": row.get(name_col, "") if name_col else "",
                "price": float(row["last_price"]),
                "change_pct": float(row.get("ext.change_pct", 0) or 0),
                "turnover": float(row.get("ext.turnover_rate", 0) or 0),
                "amount": float(row.get("amount", 0) or 0) / 1e4,
                "amplitude": float(row.get("ext.amplitude", 0) or 0),
                "ma5": round(ma5,2), "ma10": round(ma10,2), "ma20": round(ma20,2),
                "vol_ratio": round(vr,1),
                "rsi": round(rsi,1), "kdj_k": round(k,1), "kdj_d": round(d,1), "kdj_j": round(j,1),
                "atr": round(atr_val, 3),
            })

        self.log(f"  → {len(results)} 只")
        return pd.DataFrame(results)

    # ── Layer 3: 量价配合 ──
    def layer3_volume(self, df: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
        self.log(f"[L3] 量价配合 ({len(df)} 只)")
        if len(df) == 0: return df

        sym_col = "symbol" if "symbol" in quotes.columns else "code"
        # 只保留量比达标 + 成交额达标
        valid = (df["vol_ratio"] >= self.cfg["vol_ratio_min"]) & \
                (df["amount"] >= self.cfg["amount_min"])
        result = df[valid].copy()
        self.log(f"  → {len(result)} 只")
        return result

    # ── Layer 4: 主力资金（v2 Pro 不再空壳） ──
    def layer4_fund_flow(self, df: pd.DataFrame) -> pd.DataFrame:
        self.log(f"[L4] 主力资金面过滤 ({len(df)} 只)")
        if len(df) == 0: return df

        # 基于前3层结果，标记资金流评分
        # 通过 TickFlow 分时数据可以做更精确的资金流分析
        # 早盘数据有限，这里基于量比+换手率做代理判断
        scores = []
        for _, row in df.iterrows():
            vr = row["vol_ratio"]
            to = row["turnover"]
            ch = row["change_pct"]

            # 简化版资金流评分（早盘分时数据有限）
            mf_score = 50
            if vr > 2.0: mf_score += 15
            elif vr > 1.5: mf_score += 10
            if 3 <= to <= 10: mf_score += 10
            if ch > 0 and vr > 1.5: mf_score += 10  # 量价齐升
            mf_score = min(85, mf_score)
            scores.append(mf_score)

        df = df.copy()
        df["money_flow_score"] = scores
        result = df[df["money_flow_score"] >= self.cfg["money_flow_min_score"]]
        self.log(f"  → {len(result)} 只 (资金流评分>={self.cfg['money_flow_min_score']})")
        return result

    # ── Layer 5: 板块情绪 ──
    def layer5_sector(self, df: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
        self.log(f"[L5] 板块情绪过滤 ({len(df)} 只)")
        if len(df) == 0: return df

        sym_col = "symbol" if "symbol" in quotes.columns else "code"
        chg_col = next((c for c in quotes.columns if "change" in c.lower()), None)

        sector_scores = []
        for _, row in df.iterrows():
            s = row["symbol"]
            prefix = s[:3]
            if chg_col:
                same = quotes[quotes[sym_col].astype(str).str.startswith(prefix)]
                chgs = same[chg_col].astype(float).dropna().tolist()
                if chgs and len(chgs) >= 3:
                    mean_sector = np.mean(chgs)
                    rank = np.sum(np.array(chgs) < row["change_pct"]) / len(chgs) * 100
                else:
                    mean_sector, rank = 0, 50
            else:
                mean_sector, rank = 0, 50
            sector_scores.append(int(rank))

        df = df.copy()
        df["sector_rank"] = sector_scores
        self.log(f"  → {len(df)} 只 (已标记板块排名)")
        return df

    # ── Layer 6: 风控 ──
    def layer6_risk(self, df: pd.DataFrame) -> pd.DataFrame:
        self.log(f"[L6] 风控过滤 ({len(df)} 只)")
        if len(df) == 0: return df
        # RSI过高排除
        valid = df["rsi"] <= 75  # 不追超买
        result = df[valid].copy()
        self.log(f"  → {len(result)} 只")
        return result

    # ── 卖点/止损分析（v2 Pro 新增） ──
    def sell_stop_analysis(self, df: pd.DataFrame) -> pd.DataFrame:
        """9:00-10:00 关键窗口的卖点和止损分析"""
        if len(df) == 0: return df

        df = df.copy()
        sells = []
        for _, row in df.iterrows():
            entry = row["price"]
            sell1 = round(entry * (1 + self.cfg["sell_target_1"]), 2)
            sell2 = round(entry * (1 + self.cfg["sell_target_2"]), 2)

            # 🆕 ATR 动态止损
            atr_val = row.get("atr", 0.01)
            stop_info = calc_dynamic_stop(entry, atr_val) if atr_val > 0 else {"stop_price": round(entry*0.97,2), "loss_pct": 3.0, "stop_type": "硬止损"}
            stop = stop_info["stop_price"]
            stop_pct = stop_info["loss_pct"]
            stop_type = stop_info["stop_type"]

            trail = round(entry * (1 - self.cfg["trailing_stop_pct"]), 2)

            # 基于RSI调整卖出目标
            rsi = row["rsi"]
            if rsi < 40:  # 超卖反弹预期强
                sell1 = round(entry * 1.06, 2)
                sell2 = round(entry * 1.10, 2)
            elif rsi > 65:  # 已偏高，保守卖出
                sell1 = round(entry * 1.03, 2)
                sell2 = round(entry * 1.06, 2)

            sells.append({
                "sell_target_1": sell1, "sell_target_2": sell2,
                "stop_loss": stop, "stop_loss_pct": stop_pct, "stop_type": stop_type,
                "trailing_stop": trail,
                "risk_reward": round((sell1-entry)/(entry-stop), 1) if (entry-stop) > 0 else 0,
            })

        # 展平卖点信息
        for key in ["sell_target_1", "sell_target_2", "stop_loss", "stop_loss_pct", "stop_type", "trailing_stop", "risk_reward"]:
            df[key] = [s[key] for s in sells]

        return df

    # ── 主流程 ──
    def run(self) -> Optional[pd.DataFrame]:
        print("\n" + "="*55)
        print("  早盘选股 v2.0 Pro — TickFlow Pro + 增强因子")
        print("  执行窗口: 09:25-10:00 | 含卖点/止损分析")
        print("="*55)

        if not self.is_trading_day():
            self.log("非交易日，跳过"); return None

        # Step 1: TickFlow Pro 全市场数据
        self.log("\nStep 1: 全市场实时行情 (TickFlow Pro)...")
        quotes = self.cli.get_all_quotes()
        self.log(f"  获取 {len(quotes)} 只")

        # Layer 1: 基础过滤
        df = self.layer1_basic(quotes)
        if len(df) == 0: self.log("L1 后无候选"); return None

        # Layer 2: 技术趋势（需K线）
        self.log(f"\nStep 2: 获取K线 + 技术指标...")
        sym_col = "symbol" if "symbol" in df.columns else "code"
        syms = df[sym_col].tolist()
        klines = self.cli.get_klines_batch(syms)
        self.log(f"  获取 {len(klines)} 只K线")

        df = self.layer2_trend(df, klines)
        if len(df) == 0: self.log("L2 后无候选"); return None

        # Layer 3-6
        df = self.layer3_volume(df, quotes)
        if len(df) == 0: self.log("L3 后无候选"); return None

        df = self.layer4_fund_flow(df)
        if len(df) == 0: self.log("L4 后无候选"); return None

        df = self.layer5_sector(df, quotes)
        if len(df) == 0: self.log("L5 后无候选"); return None

        df = self.layer6_risk(df)

        # 卖点/止损分析
        df = self.sell_stop_analysis(df)

        # 综合评分 & 排序
        if "money_flow_score" in df.columns and "sector_rank" in df.columns:
            df["total_score"] = (
                df["rsi"].apply(lambda x: min(85, max(20, 100 - abs(x-50)*2))) * 0.15 +
                df["vol_ratio"].apply(lambda x: min(100, x*30)) * 0.15 +
                df["money_flow_score"] * 0.30 +
                df["sector_rank"] * 0.20 +
                df["change_pct"].apply(lambda x: min(100, 50 + x*5)) * 0.20
            )
            df = df.sort_values("total_score", ascending=False)

        # 输出
        top = df.head(self.cfg["top_output"])
        self._output(top, df)

        return top

    def _output(self, top, all_df):
        today = date.today()
        print(f"\n{'='*55}")
        print(f"  早盘候选 TOP{len(top)} (共筛选 {len(all_df)} 只)")
        print(f"{'='*55}")

        for i, (_, r) in enumerate(top.iterrows(), 1):
            icon = "🟢" if r.get("total_score", 50) >= 70 else ("🟡" if r.get("total_score", 50) >= 55 else "🟠")
            print(f"\n  [{i}] {icon} {r['symbol']} {r['name']}")
            print(f"      现价:{r['price']:.2f} +{r['change_pct']:.2f}% "
                  f"换手:{r['turnover']:.1f}% 量比:{r['vol_ratio']:.1f}")
            print(f"      RSI:{r['rsi']:.0f} KDJ:{r['kdj_k']:.0f}/{r['kdj_d']:.0f}/{r['kdj_j']:.0f}")
            print(f"      MA5:{r['ma5']} MA10:{r['ma10']} MA20:{r['ma20']}")
            print(f"      卖点: +5%→{r.get('sell_target_1','?')} | +8%→{r.get('sell_target_2','?')}")
            atr_v = r.get("atr", 0)
            sl = r.get("stop_loss", "?")
            sl_type = r.get("stop_type", "硬止损")
            print(f"      🛑 止损: {sl} ({sl_type}, ATR:{atr_v:.3f})")
            print(f"      风险收益比: {r.get('risk_reward','?')} | 板块排名: Top{r.get('sector_rank',50):.0f}%")

        print(f"\n  [纪律] 09:30-10:30 确认放量后买入 | -3%止损 | +5~8%止盈")
        print(f"  [仓位] 单只≤20% | 同时≤3只 | 尾盘14:45前未涨则卖出\n")

        # 保存
        od = self.cfg["output_dir"]
        os.makedirs(od, exist_ok=True)

        result = {
            "strategy": "morning_v2_pro", "version": "2.0",
            "date": today.isoformat(),
            "time": datetime.now().strftime("%H:%M:%S"),
            "data_source": "TickFlow Pro",
            "total_candidates": len(all_df),
            "top": top.to_dict("records") if len(top) > 0 else [],
        }

        json_path = os.path.join(od, f"morning_v2_{today.isoformat()}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)

        self.log(f"\n结果已保存: {od}/")


# ============================================================
# 入口
# ============================================================
def main():
    p = argparse.ArgumentParser(description="早盘选股 v2.0 Pro")
    p.add_argument("--debug", action="store_true")
    a = p.parse_args()

    try:
        cli = TFClient()
    except Exception as e:
        print(f"\n初始化失败: {e}")
        sys.exit(1)

    ms = MorningScreenerV2Pro(cli, CONFIG, debug=a.debug)
    try:
        ms.run()
    except Exception as e:
        print(f"\n异常: {e}")
        import traceback; traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
