#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')
import akshare as ak
import pandas as pd

print('[大盘环境检查] 2026-04-29')
idx_df = ak.stock_zh_index_spot_sina()
sh_row = idx_df[idx_df['代码'] == 'sh000001'].iloc[0]
cy_row = idx_df[idx_df['代码'] == 'sz399006'].iloc[0]
sh_close = float(sh_row['最新价'])
sh_pct   = float(sh_row['涨跌幅'])
cy_close = float(cy_row['最新价'])
cy_pct   = float(cy_row['涨跌幅'])
print(f'上证实时: {sh_close}  涨跌幅: {sh_pct}%')
print(f'创业板实时: {cy_close}  涨跌幅: {cy_pct}%')

def get_ma20(symbol):
    df = ak.stock_zh_index_daily(symbol=symbol)
    df = df.sort_values('date').tail(30)
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df['ma20'] = df['close'].rolling(20).mean()
    last = df.iloc[-1]
    return float(last['close']), float(last['ma20'])

sh_c, sh_ma20 = get_ma20('sh000001')
cy_c, cy_ma20 = get_ma20('sz399006')
print(f'上证: close={sh_c:.2f} MA20={sh_ma20:.2f}')
print(f'创业板: close={cy_c:.2f} MA20={cy_ma20:.2f}')

c1 = sh_close > sh_ma20
c2 = cy_close > cy_ma20
c3 = sh_pct > -1.0
print(f'C1 上证>MA20: {c1}')
print(f'C2 创业板>MA20: {c2}')
print(f'C3 上证>-1%: {c3}')
env_pass = c1 and c2 and c3
print(f'大盘环境: {"PASS" if env_pass else "FAIL"}')
