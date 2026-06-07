#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股尾盘一夜持股法 v5.0 - 真实MA均线版
数据源:
  - 个股日线: ak.stock_zh_a_hist_tx (腾讯财经, 不限流)
  - 指数日线: ak.stock_zh_index_daily (东方财富)
  - 实时行情: ak.stock_zh_a_spot (腾讯)
  - 热点板块: ak.stock_board_industry_name_ths / concept_name_ths
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import akshare as ak
import pandas as pd
import numpy as np
import warnings
import time
warnings.filterwarnings('ignore')

# ============================================================
# 配置
# ============================================================
TODAY = "2026-04-27"
MA_START = "2026-02-01"   # MA20 需要约25个交易日
SCREENER_LIMIT = 30        # 初筛候选数量（越多越准但越慢）

print("=" * 60)
print(f"A股尾盘一夜持股法 v5.0 (真实MA版) -  {TODAY}")
print("=" * 60)

# ============================================================
# 工具函数
# ============================================================
def code_to_tx(code_str):
    """将 000001.SZ 转换为 sz000001 格式用于腾讯接口"""
    c = str(code_str).strip()
    # 处理纯数字
    c = c.replace('.SZ', '').replace('.sz', '').replace('.SH', '').replace('.sh', '')
    if c.startswith('6') or c.startswith('9'):
        return 'sh' + c.zfill(6)
    else:
        return 'sz' + c.zfill(6)

def get_stock_ma_fast(tx_symbol, start=MA_START, end=TODAY, timeout=5):
    """
    通过腾讯财经接口获取个股真实MA5/MA10/MA20
    返回: dict 或 None
    """
    try:
        df = ak.stock_zh_a_hist_tx(
            symbol=tx_symbol,
            start_date=start,
            end_date=end,
            adjust='qfq',
            timeout=timeout
        )
        if df is None or len(df) < 25:
            return None
        df = df.sort_values('date')
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna(subset=['close'])
        if len(df) < 25:
            return None
        latest = df.iloc[-1]
        ma5  = df['close'].rolling(5).mean().iloc[-1]
        ma10 = df['close'].rolling(10).mean().iloc[-1]
        ma20 = df['close'].rolling(20).mean().iloc[-1]
        return {
            'close': float(latest['close']),
            'ma5':   float(ma5),
            'ma10':  float(ma10),
            'ma20':  float(ma20),
            'date':  str(latest.get('date', ''))
        }
    except Exception:
        return None

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
# 第三步：获取实时行情初筛
# ============================================================
print("\n[获取全市场实时行情]")
df_rt = None
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

# 标准化列名
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

df_rt = df_rt[~df_rt['name'].str.contains('ST|退|\\*', na=False, regex=True)]
print(f"  ST/退市: {n0} -> {len(df_rt)}")

df_rt = df_rt[df_rt['pure_code'].str.match(r'^\d{6}$', na=False)]
df_rt = df_rt[~df_rt['code'].str.startswith('bj')]
print(f"  沪深纯6位: {len(df_rt)}")

df_rt = df_rt[(df_rt['price'] > 0) & (df_rt['price'] < 20)]
print(f"  股价<20: {len(df_rt)}")

df_rt = df_rt[df_rt['amount'] > 1e7]
print(f"  成交额>1000万: {len(df_rt)}")

df_rt = df_rt[(df_rt['pct'] >= 0) & (df_rt['pct'] < 5)]
print(f"  涨幅0-5%: {len(df_rt)}")

df_rt = df_rt[df_rt['pct'] < 4.8]
print(f"  排除涨停: {len(df_rt)}")

print(f"\n  初筛候选: {len(df_rt)} 只")

if len(df_rt) == 0:
    print("  无候选股票。")
    sys.exit(0)

# 取足够候选（实际取全量，MA获取时再筛选）
df_candidates = df_rt.head(SCREENER_LIMIT).copy()
print(f"  取TOP{SCREENER_LIMIT}获取MA均线数据...")

# ============================================================
# 第五步：获取真实MA均线（腾讯接口）
# ============================================================
print("\n[获取真实MA均线]")

ma_cache = {}
t0 = time.time()
ok_count = 0
fail_count = 0

for i, (_, row) in enumerate(df_candidates.iterrows()):
    code = str(row['pure_code'])
    ts_code = code  # 000001.SZ
    if not ts_code.endswith('.SZ') and not ts_code.endswith('.SH'):
        if code.startswith('6'):
            ts_code = code + '.SH'
        else:
            ts_code = code + '.SZ'
    tx_sym = code_to_tx(ts_code)
    
    ma_data = get_stock_ma_fast(tx_sym)
    if ma_data:
        ma_cache[code] = ma_data
        ok_count += 1
    else:
        fail_count += 1
    
    if (i + 1) % 5 == 0:
        elapsed = time.time() - t0
        print(f"  进度: {i+1}/{len(df_candidates)} ({elapsed:.0f}s, 成功{ok_count} 失败{fail_count})")

elapsed = time.time() - t0
print(f"  完成! 耗时 {elapsed:.1f}秒，成功 {ok_count}/{len(df_candidates)}")

if ok_count < 5:
    print(f"  [警告] MA获取成功率低({ok_count}/{len(df_candidates)})，结果参考性有限")

# ============================================================
# 第六步：计算分时指标 + 综合打分
# ============================================================
print("\n[构建分时指标]")

df_rt['avg_price'] = np.where(
    (df_rt['volume'] > 0) & (df_rt['volume'].notna()),
    df_rt['amount'] / (df_rt['volume'] * 100),
    df_rt['price']
)
df_rt['above_avg'] = df_rt['price'] > df_rt['avg_price']
df_rt['bullish'] = (df_rt['pct'] > 0.5) & (df_rt['price'] > df_rt['open'])
df_rt['strength'] = df_rt['pct'] * (df_rt['amount'] / 1e8)

def calc_score_v5(row, hot_secs, ma_cache):
    pct   = float(row.get('pct', 0))
    price = float(row.get('price', 0))
    name  = str(row.get('name', ''))
    code  = str(row.get('pure_code', ''))
    amt_b = float(row.get('amount', 0)) / 1e8
    above_avg = bool(row.get('above_avg', False))
    bullish = bool(row.get('bullish', False))
    open_p = float(row.get('open', 0))
    high_p = float(row.get('high', 0))
    low_p  = float(row.get('low', 0))

    if pct <= 0 or pct >= 4.8 or price <= 0 or price >= 20 or amt_b <= 0.1:
        return None

    # 获取真实MA数据
    ma_data = ma_cache.get(code)
    ma_ok = ma_data is not None
    close_px = price
    ma5_v = ma_data['ma5']  if ma_ok else price
    ma10_v = ma_data['ma10'] if ma_ok else price
    ma20_v = ma_data['ma20'] if ma_ok else price
    real_close = ma_data['close'] if ma_ok else price

    score = 0

    # 1. 真实均线多头排列 20%（核心升级！）
    if ma_ok:
        # 判断：价格 > MA20 > MA10 > MA5 为标准多头
        if close_px > ma20_v and ma20_v > ma10_v and ma10_v > ma5_v:
            ms = 100  # 完美多头
        elif close_px > ma20_v and close_px > ma10_v:
            ms = 85   # 价格在MA10上方
        elif close_px > ma20_v:
            ms = 70   # 价格在MA20上方
        elif close_px > ma5_v:
            ms = 50   # 仅在MA5上方
        else:
            ms = 30   # 跌破均线
    else:
        # fallback: 用昨收代理
        prev_close = float(row.get('prev_close', 0))
        if prev_close > 0:
            spread_pct = (close_px - prev_close) / prev_close * 100
            if spread_pct < 2: ms = 80
            elif spread_pct < 4: ms = 60
            elif spread_pct < 6: ms = 40
            else: ms = 20
        else:
            ms = 40
    score += ms * 0.20

    # 2. 分时强度 20%
    if above_avg and bullish: ts = 90
    elif above_avg: ts = 75
    elif bullish: ts = 65
    else: ts = 40
    score += ts * 0.20

    # 3. 量价配合 20%
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
    upper_shadow = 0; lower_shadow = 0
    if high_p > 0 and low_p > 0:
        upper_shadow = (high_p - max(price, open_p)) / high_p * 100
        lower_shadow = (min(price, open_p) - low_p) / low_p * 100 if low_p > 0 else 0

    # 均线回踩不破：价格接近MA5/MA10（偏离<2%）+ 阳线 = 经典买点
    if ma_ok:
        dev_ma5 = abs(close_px - ma5_v) / ma5_v * 100
        dev_ma10 = abs(close_px - ma10_v) / ma10_v * 100
        if dev_ma5 < 2 and dev_ma10 < 3 and bullish:
            pat = "均线回踩"; ps = 95
        elif close_px > ma20_v and ma20_v > ma10_v and ma10_v > ma5_v and bullish:
            pat = "均线多头"; ps = 90
        elif dev_ma5 < 3 and bullish:
            pat = "MA5蓄势"; ps = 75
        elif 2 <= pct <= 4 and amt_b >= 1.0 and upper_shadow < 2:
            pat = "平台突破"; ps = 80
        elif bullish and pct > 0:
            pat = "阳线稳健"; ps = 55
    else:
        if 2 <= pct <= 4 and amt_b >= 1.0 and upper_shadow < 2:
            pat = "平台突破"; ps = 75
        elif bullish and pct > 0:
            pat = "阳线稳健"; ps = 50

    if upper_shadow > 4:
        return None
    score += ps * 0.10

    # 6. 空间 10%
    if 2 <= pct <= 3.5: ss2 = 100
    elif 1.5 <= pct < 2: ss2 = 80
    elif 1 <= pct < 1.5: ss2 = 60
    elif 0.5 <= pct < 1: ss2 = 45
    else: ss2 = 30
    score += ss2 * 0.10

    return {
        'code': code, 'name': name,
        'price': close_px, 'pct': pct,
        'amount_b': amt_b,
        'above_avg': above_avg,
        'bullish': bullish,
        'ma_ok': ma_ok,
        'ma5': round(ma5_v, 3) if ma_ok else None,
        'ma10': round(ma10_v, 3) if ma_ok else None,
        'ma20': round(ma20_v, 3) if ma_ok else None,
        'close_px': real_close if ma_ok else None,
        'sector': sec, 'pattern': pat,
        'score': round(score, 1)
    }

results = []
for _, row in df_rt.iterrows():
    sc = calc_score_v5(row, hot_sectors, ma_cache)
    if sc:
        results.append(sc)

df_res = pd.DataFrame(results)
if len(df_res) > 0:
    df_res = df_res.sort_values('score', ascending=False).head(10)

# ============================================================
# 第七步：输出
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
        ma_info = ""
        if row['ma_ok']:
            ma_info = f" | MA5={row['ma5']:.2f} MA10={row['ma10']:.2f} MA20={row['ma20']:.2f}"
        else:
            ma_info = " | [昨收代理]"

        print(f"\n【{i+1}】[{row['code']}] {row['name']}")
        print(f"    价格: {row['price']:.2f}元 | 涨幅: {row['pct']:.2f}% | 成交额: {row['amount_b']:.2f}亿")
        print(f"    均线: {ma_info[3:]}")
        print(f"    分时均价上方: {'是' if row['above_avg'] else '否'} | 阳线: {'是' if row['bullish'] else '否'}")
        print(f"    板块: {row['sector']} | 形态: {row['pattern']} | 综合得分: {row['score']}")

print("\n" + "=" * 60)
print("操作提醒")
print("=" * 60)
print("策略已过滤，请于尾盘(14:55-15:00)结合分时图")
print("决策，并严格执行次日早盘止盈止损纪律。")
print(f"\n--- 运行完成: {TODAY} (v5.0真实MA版) ---")
