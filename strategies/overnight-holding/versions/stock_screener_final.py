#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股尾盘一夜持股法 v4.0 - 纯实时数据版
数据源: 腾讯A股实时行情(唯一可用数据源) + 新浪指数 + THS板块
策略: 由于东财个股历史被限流，改用实时分时代理指标构建选股逻辑
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import akshare as ak
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

TODAY = "2026-04-27"
CUTOFF_TIME = "14:45"

print("=" * 60)
print(f"A股尾盘一夜持股法 v4.0  -  {TODAY} {CUTOFF_TIME}")
print("=" * 60)

# ============================================================
# 第一步：大盘环境检查
# ============================================================
print("\n[大盘环境检查]")
idx_df = ak.stock_zh_index_spot_sina()
sh_row = idx_df[idx_df['代码'] == 'sh000001'].iloc[0]
cy_row = idx_df[idx_df['代码'] == 'sz399006'].iloc[0]
sh_close = float(sh_row['最新价'])
sh_pct   = float(sh_row['涨跌幅'])
cy_close = float(cy_row['最新价'])
cy_pct   = float(cy_row['涨跌幅'])

def get_ma20(symbol):
    df = ak.stock_zh_index_daily(symbol=symbol)
    df = df.sort_values('date').tail(25)
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df['ma20'] = df['close'].rolling(20).mean()
    last = df.iloc[-1]
    return float(last['close']), float(last['ma20'])

sh_c, sh_ma20 = get_ma20('sh000001')
cy_c, cy_ma20 = get_ma20('sz399006')

print(f"  上证:  {sh_close:.2f} | MA20 {sh_ma20:.2f} | 今日 {sh_pct:+.2f}%")
print(f"  创业板: {cy_close:.2f} | MA20 {cy_ma20:.2f} | 今日 {cy_pct:+.2f}%")

c1 = sh_close > sh_ma20
c2 = cy_close > cy_ma20
c3 = sh_pct > -1.0

print(f"\n  [ {'PASS' if c1 else 'FAIL'} ] 上证>MA20  ({sh_close:.2f}>{sh_ma20:.2f})")
print(f"  [ {'PASS' if c2 else 'FAIL'} ] 创业板>MA20 ({cy_close:.2f}>{cy_ma20:.2f})")
print(f"  [ {'PASS' if c3 else 'FAIL'} ] 上证跌幅>-1% ({sh_pct:+.2f}%)")

if not (c1 and c2 and c3):
    print("\n[FAIL] 大盘环境不满足，今日休息。")
    sys.exit(0)
print("\n[PASS] 大盘环境满足，开始选股！")

# ============================================================
# 第二步：热点板块
# ============================================================
print("\n[热点板块]")
hot_sectors = []
try:
    df_ind = ak.stock_board_industry_name_ths()
    if df_ind is not None and len(df_ind) > 0:
        hot_sectors = df_ind['name'].tolist()[:20]
        print(f"  行业TOP5: {hot_sectors[:5]}")
except Exception as e:
    print(f"  行业失败: {e}")

try:
    df_con = ak.stock_board_concept_name_ths()
    if df_con is not None and len(df_con) > 0:
        con_names = df_con['板块名称'].tolist()[:10] if '板块名称' in df_con.columns else []
        hot_sectors.extend(con_names)
        print(f"  概念TOP5: {con_names[:5]}")
except Exception as e:
    print(f"  概念失败: {e}")

# ============================================================
# 第三步：获取实时行情
# ============================================================
print("\n[获取全市场实时行情]")
for src_name, fn in [('腾讯', ak.stock_zh_a_spot)]:
    try:
        df_rt = fn()
        if df_rt is not None and len(df_rt) > 1000:
            print(f"  数据源: {src_name} - {len(df_rt)} 只")
            break
    except Exception as e:
        print(f"  {src_name}失败: {str(e)[:60]}")
        continue

if df_rt is None:
    print("[FAIL] 数据获取失败")
    sys.exit(1)

# 标准化
rename_map = {}
for c in df_rt.columns:
    cn = str(c)
    if '代码' in cn: rename_map[c] = 'code'
    elif '名称' in cn: rename_map[c] = 'name'
    elif '最新' in cn: rename_map[c] = 'price'
    elif '涨跌幅' in cn and '涨跌额' not in cn: rename_map[c] = 'pct'
    elif '涨跌额' in cn: rename_map[c] = 'change'
    elif '成交' in cn and '额' in cn: rename_map[c] = 'amount'
    elif '换手' in cn: rename_map[c] = 'turnover'
    elif '流通' in cn: rename_map[c] = 'float_mv'
    elif '量比' in cn: rename_map[c] = 'vol_ratio'
    elif '昨收' in cn: rename_map[c] = 'prev_close'
    elif '今开' in cn: rename_map[c] = 'open'
    elif '最高' in cn: rename_map[c] = 'high'
    elif '最低' in cn: rename_map[c] = 'low'
    elif '成交量' in cn: rename_map[c] = 'volume'

df_rt.rename(columns=rename_map, inplace=True)
df_rt['pure_code'] = df_rt['code'].astype(str).str.replace(r'^(bj|sh|sz)', '', regex=True)

for c in ['price', 'pct', 'amount', 'change', 'turnover', 'float_mv', 'vol_ratio',
          'prev_close', 'open', 'high', 'low', 'volume']:
    if c in df_rt.columns:
        df_rt[c] = pd.to_numeric(df_rt[c], errors='coerce')

# ============================================================
# 第四步：初筛
# ============================================================
print("\n[初筛过滤]")
n0 = len(df_rt)

# ST/退市
df_rt = df_rt[~df_rt['name'].str.contains('ST|退|\\*', na=False, regex=True)]
print(f"  ST/退市: {n0} -> {len(df_rt)}")

# 纯6位，去北交所
df_rt = df_rt[df_rt['pure_code'].str.match(r'^\d{6}$', na=False)]
df_rt = df_rt[~df_rt['code'].str.startswith('bj')]
print(f"  沪深纯6位: {len(df_rt)}")

# 股价
df_rt = df_rt[(df_rt['price'] > 0) & (df_rt['price'] < 20)]
print(f"  股价<20: {len(df_rt)}")

# 成交额 > 1000万
df_rt = df_rt[df_rt['amount'] > 1e7]
print(f"  成交额>1000万: {len(df_rt)}")

# 涨幅 0-5%
df_rt = df_rt[(df_rt['pct'] >= 0) & (df_rt['pct'] < 5)]
print(f"  涨幅0-5%: {len(df_rt)}")

# 排除涨停价附近（涨幅>=9.8%视为接近涨停）
df_rt = df_rt[df_rt['pct'] < 4.8]
print(f"  排除涨停: {len(df_rt)}")

print(f"\n  初筛候选: {len(df_rt)} 只")

if len(df_rt) == 0:
    print("  无候选股票。")
    sys.exit(0)

# ============================================================
# 第五步：构建实时代理指标（替代历史MA）
# ============================================================
print("\n[构建实时代理指标]")

# 计算代理指标
df_rt['open_pct'] = (df_rt['price'] - df_rt['open']) / df_rt['open'] * 100  # 开盘涨幅
df_rt['ma_proxy'] = (df_rt['price'] - df_rt['prev_close']) / df_rt['prev_close'] * 100  # 当日均线代理

# 价格位置：收盘价/昨收（>1表示在昨日价格之上）
df_rt['price_vs_prev'] = df_rt['price'] / df_rt['prev_close']

# 强势特征：收盘>开盘（阳线），且涨幅>1%
df_rt['bullish'] = (df_rt['pct'] > 0.5) & (df_rt['price'] > df_rt['open'])

# 分时均价代理：当日均价 = 成交额/成交量
# 成交量单位是"手"（100股），成交额是"元"
# 均价 = 成交额(元) / (成交量(手) * 100)
df_rt['avg_price'] = np.where(
    (df_rt['volume'] > 0) & (df_rt['volume'].notna()),
    df_rt['amount'] / (df_rt['volume'] * 100),
    df_rt['price']
)
df_rt['above_avg'] = df_rt['price'] > df_rt['avg_price']  # 股价在均价上方

# 量比代理：成交额/股价/100 作为量的代理
# 简化：成交额越大，量比越高
# 设定：成交额 > 1亿为放量，>3亿为大量

# 强势度 = 收盘涨幅 * 成交额(亿)
df_rt['strength'] = df_rt['pct'] * (df_rt['amount'] / 1e8)

# 综合打分
print("\n[综合打分]")

def calc_score_v4(row, hot_secs):
    pct   = float(row.get('pct', 0))
    price = float(row.get('price', 0))
    name  = str(row.get('name', ''))
    code  = str(row.get('pure_code', ''))
    amt   = float(row.get('amount', 0))  # 元
    amt_b = amt / 1e8  # 亿
    above_avg = bool(row.get('above_avg', False))
    vol_proxy = float(row.get('amount', 0)) / 1e7  # 成交额/1000万作为量代理
    prev_close = float(row.get('prev_close', 0))
    open_p = float(row.get('open', 0))
    high_p = float(row.get('high', 0))
    low_p  = float(row.get('low', 0))
    bullish = bool(row.get('bullish', False))
    
    if pct <= 0 or pct >= 4.8:
        return None
    if price <= 0 or price >= 20:
        return None
    if amt_b <= 0.1:
        return None
    
    score = 0
    
    # 1. 均线紧密度代理 20%：用"股价/昨收"判断是否在均线之上
    # 昨收 ≈ MA5/MA10代理；若股价 > 昨收 * 1.02，说明在均线之上
    if prev_close > 0:
        above_ma_proxy = price > prev_close * 1.01  # 略高于昨收
        spread_pct = (price - prev_close) / prev_close * 100
        if spread_pct < 2: ms = 100
        elif spread_pct < 4: ms = 80
        elif spread_pct < 6: ms = 60
        elif spread_pct < 9: ms = 40
        else: ms = 20
    else:
        ms = 50
    score += ms * 0.20
    
    # 2. 分时强度 20%：收盘>均价=强势；阳线+涨幅适中
    if above_avg and bullish: ts = 90
    elif above_avg: ts = 75
    elif bullish: ts = 65
    else: ts = 40
    score += ts * 0.20
    
    # 3. 量价配合 20%：成交额代理
    if amt_b >= 3.0: vs = 90
    elif amt_b >= 1.5: vs = 75
    elif amt_b >= 0.8: vs = 60
    elif amt_b >= 0.4: vs = 45
    else: vs = 30
    score += vs * 0.20
    
    # 4. 板块热度 20%
    ss = 20; sec = "未知"
    for sname in hot_secs[:15]:
        kw = sname.split()[0] if sname else ""
        if kw and kw in name:
            ss = 90; sec = sname; break
    score += ss * 0.20
    
    # 5. 形态 10%
    pat = "无特殊形态"; ps = 0
    # 上影线判断
    if high_p > 0 and low_p > 0:
        upper_shadow = (high_p - max(price, open_p)) / high_p * 100
        lower_shadow = (min(price, open_p) - low_p) / low_p * 100 if low_p > 0 else 0
    else:
        upper_shadow = 0; lower_shadow = 0
    
    # 平台突破代理：涨幅2-4%，成交额放量
    if 2 <= pct <= 4 and amt_b >= 1.0 and upper_shadow < 2:
        pat = "平台突破"; ps = 80
    # 阳线稳健
    elif bullish and pct > 0:
        pat = "阳线稳健"; ps = 50
    # 上影线过长（>3%）回避
    if upper_shadow > 4:
        return None
    score += ps * 0.10
    
    # 6. 空间 10%：涨幅适中则空间足（涨幅过高的票上涨空间有限）
    if 2 <= pct <= 3.5: ss2 = 100
    elif 1.5 <= pct < 2: ss2 = 80
    elif 1 <= pct < 1.5: ss2 = 60
    elif 0.5 <= pct < 1: ss2 = 45
    else: ss2 = 30
    score += ss2 * 0.10
    
    return {
        'code': code, 'name': name,
        'price': price, 'pct': pct,
        'amount_b': amt_b,
        'above_avg': above_avg,
        'bullish': bullish,
        'sector': sec, 'pattern': pat,
        'score': round(score, 1)
    }

results = []
for _, row in df_rt.iterrows():
    sc = calc_score_v4(row, hot_sectors)
    if sc:
        results.append(sc)

df_res = pd.DataFrame(results)
if len(df_res) > 0:
    df_res = df_res.sort_values('score', ascending=False).head(10)

# ============================================================
# 第六步：输出
# ============================================================
print("\n" + "=" * 60)
print("大盘环境状态")
print("=" * 60)
print(f"上证指数:  {sh_close:.2f} | MA20 {sh_ma20:.2f} | 今日 {sh_pct:+.2f}%  [PASS]")
print(f"创业板指:  {cy_close:.2f} | MA20 {cy_ma20:.2f} | 今日 {cy_pct:+.2f}%  [PASS]")
print(f"大盘结论:  满足选股条件，执行策略")

print(f"\n选股结果 TOP{min(10, len(df_res))}（按综合得分降序）")
print("=" * 60)

if len(df_res) == 0:
    print("今日无符合条件的股票，建议观望。")
    print("注意：当前东财个股历史接口被限流，无法计算均线条件")
    print("     建议明日14:45再次运行策略")
else:
    for i, row in df_res.reset_index(drop=True).iterrows():
        print(f"\n【{i+1}】[{row['code']}] {row['name']}")
        print(f"    价格: {row['price']:.2f}元 | 涨幅: {row['pct']:.2f}% | 成交额: {row['amount_b']:.2f}亿")
        print(f"    分时均价上方: {'是' if row['above_avg'] else '否'} | 阳线: {'是' if row['bullish'] else '否'}")
        print(f"    板块: {row['sector']} | 形态: {row['pattern']} | 综合得分: {row['score']}")
        print(f"    注: 均线条件由昨收代理（历史数据接口被限流）")

print("\n" + "=" * 60)
print("操作提醒")
print("=" * 60)
print("策略已过滤，请于尾盘(14:55-15:00)结合分时图")
print("(确认回踩均线不破)决策，并严格执行次日早盘止盈止损纪律。")
print(f"\n--- 运行完成: {TODAY} {CUTOFF_TIME} ---")
