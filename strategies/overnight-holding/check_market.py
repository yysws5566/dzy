#!/usr/bin/env python3
"""检查大盘环境：上证指数 和 创业板指 是否满足均线条件"""
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta

def check_index(ts_code, name):
    """获取指数日线数据并计算MA20"""
    symbol = ts_code.replace('.SH','').replace('.SZ','')
    try:
        df = ak.stock_zh_index_daily(symbol=f"sh{symbol}" if ts_code.endswith('.SH') else f"sz{symbol}")
        df = df.sort_values('date').tail(30)
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df['ma20'] = df['close'].rolling(20).mean()
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        pct_chg = (latest['close'] - prev['close']) / prev['close'] * 100
        ma20 = latest['ma20']
        close = latest['close']
        date = latest['date']
        above_ma20 = close > ma20
        print(f"\n{name}（{ts_code}）")
        print(f"  日期: {date}")
        print(f"  收盘价: {close:.2f}")
        print(f"  MA20: {ma20:.2f}")
        print(f"  当日涨跌幅: {pct_chg:.2f}%")
        print(f"  收盘 > MA20: {'✅ 是' if above_ma20 else '❌ 否'}")
        return close, ma20, pct_chg, above_ma20
    except Exception as e:
        print(f"  获取{name}数据失败: {e}")
        return None, None, None, None

# 检查上证指数
sh_close, sh_ma20, sh_pct, sh_ok = check_index('000001.SH', '上证指数')

# 检查创业板指
cy_close, cy_ma20, cy_pct, cy_ok = check_index('399006.SZ', '创业板指')

print("\n===== 大盘环境综合判断 =====")
conditions = []
if sh_ok is not None:
    conditions.append(('上证指数 > MA20', sh_ok))
    conditions.append(('上证当日跌幅不超过1%', sh_pct is not None and sh_pct > -1))
if cy_ok is not None:
    conditions.append(('创业板指 > MA20', cy_ok))

all_pass = all(v for _, v in conditions)
for name, val in conditions:
    print(f"  {'✅' if val else '❌'} {name}")

if all_pass:
    print("\n✅ 大盘环境满足，开始执行选股策略")
else:
    print("\n⛔ 大盘环境不满足，今日休息。")
