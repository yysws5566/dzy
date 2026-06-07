#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查大盘环境 + 今日实时数据"""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')

import akshare as ak
import pandas as pd

def get_index_data(ak_symbol, ts_code, name):
    """获取指数历史日线并计算MA20，同时获取今日实时数据"""
    try:
        # 获取历史日线（用于MA20计算）
        df = ak.stock_zh_index_daily(symbol=ak_symbol)
        df = df.sort_values('date')
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df['open'] = pd.to_numeric(df['open'], errors='coerce')
        
        # 取最近30条计算MA20
        df_recent = df.tail(30).copy()
        df_recent['ma20'] = df_recent['close'].rolling(20).mean()
        
        last = df_recent.iloc[-1]
        prev = df_recent.iloc[-2]
        
        hist_close = float(last['close'])
        hist_ma20 = float(last['ma20'])
        hist_date = str(last['date'])
        hist_pct = (hist_close - float(prev['close'])) / float(prev['close']) * 100
        
        return hist_date, hist_close, hist_ma20, hist_pct
    except Exception as e:
        print(f"  获取{name}历史数据失败: {e}", file=sys.stderr)
        return None, None, None, None

def get_realtime_index():
    """获取今日实时指数行情"""
    try:
        df = ak.stock_zh_index_spot_sina()
        return df
    except Exception as e:
        print(f"  获取实时指数失败: {e}", file=sys.stderr)
        return None

# 获取今日实时数据
rt_df = get_realtime_index()

sh_rt_close, sh_rt_pct = None, None
cy_rt_close, cy_rt_pct = None, None

if rt_df is not None:
    sh_row = rt_df[rt_df['代码'] == 'sh000001']
    cy_row = rt_df[rt_df['代码'] == 'sz399006']
    if not sh_row.empty:
        sh_rt_close = float(sh_row.iloc[0]['最新价'])
        sh_rt_pct = float(sh_row.iloc[0]['涨跌幅'])
    if not cy_row.empty:
        cy_rt_close = float(cy_row.iloc[0]['最新价'])
        cy_rt_pct = float(cy_row.iloc[0]['涨跌幅'])

# 获取历史MA20
sh_date, sh_hist_close, sh_ma20, sh_hist_pct = get_index_data('sh000001', '000001.SH', '上证指数')
cy_date, cy_hist_close, cy_ma20, cy_hist_pct = get_index_data('sz399006', '399006.SZ', '创业板指')

# 用今日实时数据覆盖（如果能获取到）
sh_close = sh_rt_close if sh_rt_close else sh_hist_close
sh_pct = sh_rt_pct if sh_rt_pct is not None else sh_hist_pct

cy_close = cy_rt_close if cy_rt_close else cy_hist_close
cy_pct = cy_rt_pct if cy_rt_pct is not None else cy_hist_pct

print("=" * 50)
print("大盘环境检查 - 2026-04-27 (14:45)")
print("=" * 50)

print(f"\n上证指数（000001.SH）")
print(f"  当前价: {sh_close:.2f} 点")
print(f"  MA20:  {sh_ma20:.2f} 点 (基于{sh_date}历史数据)")
print(f"  当日涨跌幅: {sh_pct:+.2f}%")
print(f"  当前价 > MA20: {'[OK]' if sh_close > sh_ma20 else '[FAIL]'}")

print(f"\n创业板指（399006.SZ）")
print(f"  当前价: {cy_close:.2f} 点")
print(f"  MA20:  {cy_ma20:.2f} 点 (基于{cy_date}历史数据)")
print(f"  当日涨跌幅: {cy_pct:+.2f}%")
print(f"  当前价 > MA20: {'[OK]' if cy_close > cy_ma20 else '[FAIL]'}")

# 综合判断
cond1 = sh_close > sh_ma20  # 上证 > MA20
cond2 = cy_close > cy_ma20  # 创业板 > MA20
cond3 = sh_pct > -1.0       # 上证跌幅不超1%

print("\n===== 综合判断 =====")
print(f"  条件1 - 上证 > MA20: {'PASS' if cond1 else 'FAIL'}")
print(f"  条件2 - 创业板 > MA20: {'PASS' if cond2 else 'FAIL'}")
print(f"  条件3 - 上证跌幅 > -1%: {'PASS' if cond3 else 'FAIL'} (当前{sh_pct:+.2f}%)")

all_pass = cond1 and cond2 and cond3
if all_pass:
    print("\n[PASS] 大盘环境满足，开始执行选股策略")
else:
    failed = []
    if not cond1: failed.append(f"上证指数({sh_close:.2f}) <= MA20({sh_ma20:.2f})")
    if not cond2: failed.append(f"创业板指({cy_close:.2f}) <= MA20({cy_ma20:.2f})")
    if not cond3: failed.append(f"上证当日跌幅{sh_pct:+.2f}% <= -1%")
    print("\n[FAIL] 大盘环境不满足，今日休息。")
    print("不满足条件：" + "；".join(failed))

# 输出结构化数据供后续使用
print("\n---RESULT---")
print(f"MARKET_PASS={all_pass}")
print(f"SH_CLOSE={sh_close:.2f}")
print(f"SH_MA20={sh_ma20:.2f}")
print(f"SH_PCT={sh_pct:.2f}")
print(f"CY_CLOSE={cy_close:.2f}")
print(f"CY_MA20={cy_ma20:.2f}")
print(f"CY_PCT={cy_pct:.2f}")
