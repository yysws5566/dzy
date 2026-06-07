#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股尾盘一夜持股法选股 v3.0
执行时间: 14:45
数据源: 腾讯A股实时行情 + 新浪指数 + THS板块
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import akshare as ak
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# 全局配置
# ============================================================
TODAY = "2026-04-27"
CUTOFF_TIME = "14:45"

print("=" * 60)
print(f"A股尾盘一夜持股法 v3.0  -  {TODAY} {CUTOFF_TIME}")
print("=" * 60)

# ============================================================
# 第一步：大盘环境检查
# ============================================================
print("\n[大盘环境检查]")

# 实时指数（新浪）
idx_df = ak.stock_zh_index_spot_sina()
sh_row = idx_df[idx_df['代码'] == 'sh000001'].iloc[0]
cy_row = idx_df[idx_df['代码'] == 'sz399006'].iloc[0]

sh_close = float(sh_row['最新价'])
sh_pct   = float(sh_row['涨跌幅'])
cy_close = float(cy_row['最新价'])
cy_pct   = float(cy_row['涨跌幅'])

# MA20
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

print(f"\n  [ {'PASS' if c1 else 'FAIL'} ] 条件1 上证 > MA20  ({sh_close:.2f} > {sh_ma20:.2f})")
print(f"  [ {'PASS' if c2 else 'FAIL'} ] 条件2 创业板 > MA20 ({cy_close:.2f} > {cy_ma20:.2f})")
print(f"  [ {'PASS' if c3 else 'FAIL'} ] 条件3 上证跌幅 > -1% ({sh_pct:+.2f}%)")

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
    if df_ind is not None:
        hot_sectors = df_ind['name'].tolist()[:15]
        print(f"  行业: {hot_sectors[:5]}")
except Exception as e:
    print(f"  行业板块失败: {e}")

try:
    df_con = ak.stock_board_concept_name_ths()
    if df_con is not None:
        con_names = df_con['板块名称'].tolist()[:10] if '板块名称' in df_con.columns else []
        print(f"  概念: {con_names[:5]}")
        hot_sectors.extend(con_names)
except Exception as e:
    print(f"  概念板块失败: {e}")

# ============================================================
# 第三步：获取全市场实时行情
# ============================================================
print("\n[获取全市场实时行情]")

# 尝试东财接口
df_rt = None
for src_name, fn in [('东财', ak.stock_zh_a_spot_em), ('腾讯', ak.stock_zh_a_spot)]:
    try:
        df_rt = fn()
        if df_rt is not None and len(df_rt) > 1000:
            print(f"  数据源: {src_name} - {len(df_rt)} 只")
            break
    except Exception as e:
        print(f"  {src_name} 失败: {str(e)[:50]}")
        continue

if df_rt is None:
    print("[FAIL] 所有数据源均失败")
    sys.exit(1)

# 标准化列名
print(f"  原始列: {list(df_rt.columns)}")

rename_map = {}
for c in df_rt.columns:
    c_str = str(c)
    if '代码' in c_str: rename_map[c] = 'code'
    elif '名称' in c_str: rename_map[c] = 'name'
    elif '最新' in c_str: rename_map[c] = 'price'
    elif '涨跌幅' in c_str and '涨跌额' not in c_str: rename_map[c] = 'pct'
    elif '涨跌额' in c_str: rename_map[c] = 'change'
    elif '成交' in c_str and '额' in c_str: rename_map[c] = 'amount'
    elif '换手' in c_str: rename_map[c] = 'turnover'
    elif '流通' in c_str: rename_map[c] = 'float_mv'
    elif '量比' in c_str: rename_map[c] = 'vol_ratio'
    elif '昨收' in c_str: rename_map[c] = 'prev_close'
    elif '今开' in c_str: rename_map[c] = 'open'

df_rt.rename(columns=rename_map, inplace=True)

# 创建纯净代码列（去掉bj/sh/sz前缀）
df_rt['pure_code'] = df_rt['code'].astype(str).str.replace(r'^(bj|sh|sz)', '', regex=True)
print(f"  标准化后: {list(df_rt.columns)}")
print(f"  示例: {df_rt[['code','pure_code','name']].head(3).to_dict('records')}")

# 转换为数值
for c in ['price', 'pct', 'amount', 'turnover', 'float_mv', 'vol_ratio', 'change']:
    if c in df_rt.columns:
        df_rt[c] = pd.to_numeric(df_rt[c], errors='coerce')

# ============================================================
# 第四步：初筛
# ============================================================
print("\n[初筛过滤]")
n0 = len(df_rt)

# 去除ST/退市
df_rt = df_rt[~df_rt['name'].str.contains('ST|退|\\*', na=False, regex=True)]
print(f"  ST/退市: {n0} -> {len(df_rt)}")

# 沪深主板+创业板（纯6位数字，去除北交所bj前缀）
df_rt = df_rt[df_rt['pure_code'].astype(str).str.match(r'^\d{6}$', na=False)]
# 排除北交所
df_rt = df_rt[~df_rt['code'].astype(str).str.startswith('bj')]
print(f"  纯6位代码(排除北交所): {len(df_rt)}")

# 股价 < 20元
if 'price' in df_rt.columns:
    df_rt = df_rt[(df_rt['price'] > 0) & (df_rt['price'] < 20)]
    print(f"  股价<20: {len(df_rt)}")

# 成交额 > 1000万（宽松阈值，保留成交额适中的标的）
if 'amount' in df_rt.columns:
    df_rt = df_rt[df_rt['amount'] > 1e7]  # 1000万=1e7元
    print(f"  成交额>1000万: {len(df_rt)}")

# 涨幅 0%-5%
if 'pct' in df_rt.columns:
    df_rt = df_rt[(df_rt['pct'] >= 0) & (df_rt['pct'] < 5)]
    print(f"  涨幅0-5%: {len(df_rt)}")

candidates = df_rt['pure_code'].astype(str).tolist()
print(f"\n  初筛候选: {len(candidates)} 只")

if len(candidates) == 0:
    print("  无候选股票。")
    sys.exit(0)

# ============================================================
# 第五步：获取历史数据计算均线
# ============================================================
# 按成交额排序，取前60只
df_rt['amount'] = df_rt['amount'].fillna(0)
df_rt = df_rt.sort_values('amount', ascending=False)

# 由于东财接口被限流，使用腾讯股票历史接口
# 腾讯接口: sh6xxxxx / sz0xxxxx / sz3xxxxx
def get_tencent_hist(code):
    """腾讯股票历史行情"""
    try:
        if code.startswith('6'):
            sym = f"sh{code}"
        elif code.startswith(('0', '2', '3')):
            sym = f"sz{code}"
        else:
            return None
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                  start_date="20260201", end_date="20260427", adjust="")
        if df is None or len(df) < 22:
            return None
        df = df.sort_values('日期')
        df['收盘'] = pd.to_numeric(df['收盘'], errors='coerce')
        df['ma5']  = df['收盘'].rolling(5).mean()
        df['ma10'] = df['收盘'].rolling(10).mean()
        df['ma20'] = df['收盘'].rolling(20).mean()
        latest = df.iloc[-1]
        c  = float(latest['收盘'])
        m5 = float(latest['ma5'])  if not pd.isna(latest['ma5'])  else None
        m10= float(latest['ma10']) if not pd.isna(latest['ma10']) else None
        m20= float(latest['ma20']) if not pd.isna(latest['ma20']) else None
        if None in [m5, m10, m20]:
            return None
        bull = c > m5 > m10 > m20
        ma5s = df['ma5'].dropna().tolist()
        slope = len(ma5s) >= 3 and ma5s[-1] > ma5s[-2] > ma5s[-3]
        high60 = df.tail(60)['收盘'].max() if len(df) >= 60 else df['收盘'].max()
        dist_hi = (high60 - c) / high60 * 100
        return {'close': c, 'ma5': m5, 'ma10': m10, 'ma20': m20,
                'bull': bull, 'slope': slope, 'dist_hi': dist_hi}
    except Exception as e:
        return None

print(f"\n[计算均线排列]")
to_calc = df_rt.head(60)
ma_results = {}
ok_count = 0
for i, (_, row) in enumerate(to_calc.iterrows()):
    if i % 15 == 0:
        print(f"  进度: {i}/{min(60, len(to_calc))}... (已有有效数据: {ok_count})")
    pure_code = str(row['pure_code'])  # 纯净6位代码
    ma = get_tencent_hist(pure_code)
    if ma:
        ma_results[pure_code] = ma
        ok_count += 1

print(f"  均线计算完成: {len(ma_results)} 只有效")

# 多头排列 + MA5斜率向上
valid = [c for c, m in ma_results.items() if m['bull'] and m['slope']]
print(f"  多头排列+MA5斜率向上: {len(valid)} 只")

if len(valid) == 0:
    print("  无满足均线条件的股票。")
    sys.exit(0)

# ============================================================
# 第六步：综合打分
# ============================================================
print("\n[综合打分]")

def calc_score(row, ma, hot_secs):
    pct   = float(row.get('pct', 0))
    price = float(row.get('price', 0))
    name  = str(row.get('name', ''))
    code  = str(row.get('pure_code', ''))  # 纯净代码
    
    if ma['dist_hi'] < 3: return None
    if pct >= 4.8: return None
    if not ma['bull'] or not ma['slope']: return None
    
    score = 0
    
    # 均线紧密度 20%
    spread = (ma['close'] - ma['ma20']) / ma['ma20'] * 100
    if spread < 3: ms = 100
    elif spread < 5: ms = 80
    elif spread < 8: ms = 60
    elif spread < 12: ms = 40
    else: ms = 20
    score += ms * 0.20
    
    # 分时强度 20%
    if 2 <= pct <= 4: ts = 85
    elif 1 <= pct < 2: ts = 70
    elif 0 <= pct < 1: ts = 55
    else: ts = 40
    score += ts * 0.20
    
    # 量价配合 20%（用成交额估算）
    amt = float(row.get('amount', 0))
    if amt > 1e8: vs = 80  # 成交额大，活跃
    elif amt > 5e7: vs = 65
    else: vs = 45
    score += vs * 0.20
    
    # 板块热度 20%
    ss = 20
    sec = "未知"
    for sname in hot_secs[:10]:
        kw = sname.split()[0] if sname else ""
        if kw and kw in name:
            ss = 90; sec = sname; break
    score += ss * 0.20
    
    # 形态 10%
    pat = "无特殊形态"
    ps = 0
    if 2 <= pct <= 4 and amt > 8e7:
        pat = "平台突破"; ps = 80
    elif pct > 0:
        pat = "温和上涨"; ps = 40
    score += ps * 0.10
    
    # 空间 10%
    dist = ma['dist_hi']
    if 15 <= dist <= 30: ss2 = 100
    elif 10 <= dist < 15: ss2 = 80
    elif 8 <= dist < 10: ss2 = 60
    elif 5 <= dist < 8: ss2 = 40
    else: ss2 = 20
    score += ss2 * 0.10
    
    return {
        'code': code, 'name': name,
        'price': price, 'pct': pct,
        'amount': amt / 1e8,
        'ma5': ma['ma5'], 'ma10': ma['ma10'], 'ma20': ma['ma20'],
        'sector': sec, 'pattern': pat,
        'score': round(score, 1),
        'dist_hi': dist
    }

results = []
for _, row in df_rt[df_rt['pure_code'].astype(str).isin(valid)].iterrows():
    pure_code = str(row['pure_code'])
    sc = calc_score(row, ma_results.get(pure_code), hot_sectors)
    if sc:
        results.append(sc)

df_res = pd.DataFrame(results)
if len(df_res) > 0:
    df_res = df_res.sort_values('score', ascending=False).head(10)

# ============================================================
# 第七步：输出结果
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
else:
    for i, row in df_res.reset_index(drop=True).iterrows():
        print(f"\n【{i+1}】[{row['code']}] {row['name']}")
        print(f"    价格: {row['price']:.2f}元 | 涨幅: {row['pct']:.2f}% | 成交额: {row['amount']:.2f}亿")
        print(f"    均线: MA5({row['ma5']:.2f})>MA10({row['ma10']:.2f})>MA20({row['ma20']:.2f}) [多头排列]")
        print(f"    板块: {row['sector']} | 形态: {row['pattern']} | 综合得分: {row['score']}")

print("\n" + "=" * 60)
print("操作提醒")
print("=" * 60)
print("策略已过滤，请于尾盘(14:55-15:00)结合分时图")
print("(确认回踩均线不破)决策，并严格执行次日早盘止盈止损纪律。")
print(f"\n--- 运行完成: {TODAY} {CUTOFF_TIME} ---")
