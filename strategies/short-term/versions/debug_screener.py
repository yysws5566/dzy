# -*- coding: utf-8 -*-
"""调试选股条件——看看K线验证阶段为什么全被过滤"""
import sys, os
if sys.platform == 'win32':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except:
        pass

import requests
import pandas as pd

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://finance.sina.com.cn',
}

# 模拟一只预筛选通过的股票，手动检查K线条件
# 取一个满足流通市值+涨幅+换手率的大盘股
# 手动选 sh600000 浦发银行

url = 'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData'
params = {'symbol': 'sh600000', 'scale': 240, 'ma': 'no', 'datalen': 25}
r = requests.get(url, params=params, headers=headers, timeout=15)
kdata = r.json()
df = pd.DataFrame(kdata)

print("=== sh600000 浦发银行 最近25日K线 ===")
for col in ['day', 'open', 'high', 'low', 'close', 'volume']:
    df[col] = pd.to_numeric(df[col], errors='coerce')

print(df[['day', 'close', 'volume']].to_string())

closes = df['close'].tolist()
volumes = df['volume'].tolist()

# 条件5: 近10日跌幅检查
print("\n=== 条件5: 近10日跌幅 ===")
for j in range(1, min(11, len(closes))):
    if closes[j-1] > 0:
        chg = (closes[j] / closes[j-1] - 1) * 100
        flag = ""
        if chg <= -9.5:
            flag = " [跌停!]"
        elif chg <= -7:
            flag = " [跌>7%!]"
        if chg < -5:
            print(f"  day{j}: {chg:+.2f}%{flag}")

# 条件7: 近20日站20日线上
print("\n=== 条件7: 站20日线上方天数 ===")
ma20_list = []
for j in range(20, len(closes) + 1):
    ma20 = sum(closes[j-20:j]) / 20
    ma20_list.append(ma20)

above_count = 0
for j in range(20):
    idx_c = len(closes) - 20 + j
    idx_m = len(ma20_list) - 20 + j
    if idx_c >= 0 and idx_m >= 0:
        is_above = closes[idx_c] >= ma20_list[idx_m]
        if is_above:
            above_count += 1
        if j >= 15:  # 只打印最近5天
            print(f"  day{idx_c}: close={closes[idx_c]:.2f} ma20={ma20_list[idx_m]:.2f} {'above' if is_above else 'BELOW'}")

print(f"  站20日线上方: {above_count}/20")

# 条件2: 近20日日均成交额
avg_vol = sum(volumes[-20:]) / 20
avg_amount = avg_vol * closes[-1]
print(f"\n=== 条件2: 日均成交额 ===")
print(f"  20日均量: {avg_vol:.0f}")
print(f"  近似日均成交额: {avg_amount/1e8:.1f}亿")
