#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股尾盘一夜持股法 v5.1 - 2026-04-29 执行版
数据源: 腾讯财经(个股历史/实时) + 同花顺(热点板块) + 东财(指数日线)
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import akshare as ak
import pandas as pd
import numpy as np
import warnings
import time
warnings.filterwarnings('ignore')

TODAY    = "2026-04-29"
MA_START = "2026-02-01"   # 约45个交易日，确保MA20有效
TOP_N    = 80              # 初筛后取前80只做MA计算（速度与质量平衡）

print("=" * 60)
print(f"A股尾盘一夜持股法 v5.1 - {TODAY}")
print("=" * 60)

# ============================================================
# 工具函数
# ============================================================
def code_to_tx(pure_code):
    """纯6位代码 -> 腾讯格式 sh600519 / sz000001"""
    c = str(pure_code).zfill(6)
    return ('sh' if c.startswith('6') or c.startswith('9') else 'sz') + c

def get_stock_ma(tx_symbol, start=MA_START, end=TODAY, timeout=8):
    """腾讯财经接口获取个股真实MA5/MA10/MA20；返回dict或None"""
    try:
        df = ak.stock_zh_a_hist_tx(
            symbol=tx_symbol, start_date=start, end_date=end,
            adjust='qfq', timeout=timeout
        )
        if df is None or len(df) < 22:
            return None
        df = df.sort_values('date')
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna(subset=['close'])
        if len(df) < 22:
            return None
        latest = df.iloc[-1]
        # MA斜率：近3日斜率
        ma5_s  = df['close'].rolling(5).mean()
        ma10_s = df['close'].rolling(10).mean()
        ma20_s = df['close'].rolling(20).mean()
        ma5_v  = float(ma5_s.iloc[-1])
        ma10_v = float(ma10_s.iloc[-1])
        ma20_v = float(ma20_s.iloc[-1])
        # MA5近3日斜率（用于趋势确认）
        ma5_slope = float(ma5_s.iloc[-1] - ma5_s.iloc[-3]) if len(ma5_s) >= 3 else 0
        # 60日最高价（空间判断）
        hi60 = float(df['close'].tail(60).max()) if len(df) >= 60 else float(df['close'].max())
        return {
            'close': float(latest['close']),
            'ma5': ma5_v, 'ma10': ma10_v, 'ma20': ma20_v,
            'ma5_slope': ma5_slope, 'hi60': hi60,
            'date': str(latest.get('date', ''))
        }
    except Exception:
        return None

# ============================================================
# 第一步：大盘环境检查
# ============================================================
print("\n[1/5] 大盘环境检查")
idx_df = ak.stock_zh_index_spot_sina()
sh_row = idx_df[idx_df['代码'] == 'sh000001'].iloc[0]
cy_row = idx_df[idx_df['代码'] == 'sz399006'].iloc[0]
sh_close = float(sh_row['最新价'])
sh_pct   = float(sh_row['涨跌幅'])
cy_close = float(cy_row['最新价'])
cy_pct   = float(cy_row['涨跌幅'])

def get_idx_ma20(symbol):
    df = ak.stock_zh_index_daily(symbol=symbol)
    df = df.sort_values('date').tail(30)
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df['ma20'] = df['close'].rolling(20).mean()
    last = df.iloc[-1]
    return float(last['close']), float(last['ma20'])

sh_c, sh_ma20 = get_idx_ma20('sh000001')
cy_c, cy_ma20 = get_idx_ma20('sz399006')

c1 = sh_close > sh_ma20
c2 = cy_close > cy_ma20
c3 = sh_pct > -1.0

print(f"  上证:  {sh_close:.2f} | MA20={sh_ma20:.2f} | {sh_pct:+.2f}%  => {'PASS' if c1 else 'FAIL'}")
print(f"  创业板: {cy_close:.2f} | MA20={cy_ma20:.2f} | {cy_pct:+.2f}% => {'PASS' if c2 else 'FAIL'}")
print(f"  跌幅条件: {sh_pct:+.2f}% > -1.0% => {'PASS' if c3 else 'FAIL'}")

if not (c1 and c2 and c3):
    fail_reasons = []
    if not c1: fail_reasons.append(f"上证({sh_close:.2f}) <= MA20({sh_ma20:.2f})")
    if not c2: fail_reasons.append(f"创业板({cy_close:.2f}) <= MA20({cy_ma20:.2f})")
    if not c3: fail_reasons.append(f"上证跌幅{sh_pct:.2f}% <= -1%")
    print("\n大盘环境不满足，今日休息。")
    for r in fail_reasons: print(f"  - {r}")
    sys.exit(0)

print("\n  大盘环境满足，开始执行选股策略")

# ============================================================
# 第二步：热点板块（同花顺）
# ============================================================
print("\n[2/5] 获取热点板块")
hot_industry = []  # [(name, rank), ...]
hot_concept  = []
try:
    df_ind = ak.stock_board_industry_name_ths()
    if df_ind is not None and len(df_ind) > 0:
        # 取涨幅排序前20
        if '涨跌幅' in df_ind.columns:
            df_ind['涨跌幅'] = pd.to_numeric(df_ind['涨跌幅'], errors='coerce')
            df_ind = df_ind.sort_values('涨跌幅', ascending=False)
        hot_industry = list(df_ind['name'].head(20)) if 'name' in df_ind.columns else \
                       list(df_ind.iloc[:, 0].head(20))
        print(f"  行业TOP5: {hot_industry[:5]}")
except Exception as e:
    print(f"  行业板块失败: {e}")

try:
    df_con = ak.stock_board_concept_name_ths()
    if df_con is not None and len(df_con) > 0:
        name_col = '板块名称' if '板块名称' in df_con.columns else df_con.columns[0]
        if '涨跌幅' in df_con.columns:
            df_con['涨跌幅'] = pd.to_numeric(df_con['涨跌幅'], errors='coerce')
            df_con = df_con.sort_values('涨跌幅', ascending=False)
        hot_concept = list(df_con[name_col].head(15))
        print(f"  概念TOP5: {hot_concept[:5]}")
except Exception as e:
    print(f"  概念板块失败: {e}")

all_hot = hot_industry + hot_concept  # 合并热点列表

# ============================================================
# 第三步：获取全市场实时行情
# ============================================================
print("\n[3/5] 全市场实时行情初筛")
df_rt = ak.stock_zh_a_spot()
print(f"  获取全市场: {len(df_rt)} 只")

# 标准化列名
rename_map = {}
for col in df_rt.columns:
    cn = str(col)
    if '代码' in cn:        rename_map[col] = 'code'
    elif '名称' in cn:      rename_map[col] = 'name'
    elif '最新' in cn:      rename_map[col] = 'price'
    elif '涨跌幅' in cn and '额' not in cn: rename_map[col] = 'pct'
    elif '涨跌额' in cn:    rename_map[col] = 'change'
    elif '成交额' in cn:    rename_map[col] = 'amount'
    elif '换手' in cn:      rename_map[col] = 'turnover'
    elif '流通' in cn:      rename_map[col] = 'float_mv'
    elif '量比' in cn:      rename_map[col] = 'vol_ratio'
    elif '昨收' in cn:      rename_map[col] = 'prev_close'
    elif '今开' in cn:      rename_map[col] = 'open'
    elif '最高' in cn:      rename_map[col] = 'high'
    elif '最低' in cn:      rename_map[col] = 'low'
    elif '成交量' in cn:    rename_map[col] = 'volume'
df_rt.rename(columns=rename_map, inplace=True)

# 提取纯6位代码
df_rt['pure_code'] = df_rt['code'].astype(str).str.replace(r'^(bj|sh|sz)', '', regex=True)

# 数值转换
for c in ['price', 'pct', 'amount', 'change', 'turnover', 'float_mv',
          'vol_ratio', 'prev_close', 'open', 'high', 'low', 'volume']:
    if c in df_rt.columns:
        df_rt[c] = pd.to_numeric(df_rt[c], errors='coerce')

n0 = len(df_rt)

# ------ 硬性过滤 ------
# 1. 排除ST/退市
df_rt = df_rt[~df_rt['name'].str.contains('ST|退|\\*', na=False, regex=True)]
print(f"  去ST/退市: {n0} -> {len(df_rt)}")

# 2. 沪深主板+创业板+科创板（排除北交所bj开头）
df_rt = df_rt[df_rt['pure_code'].str.match(r'^\d{6}$', na=False)]
df_rt = df_rt[~df_rt['code'].str.startswith('bj', na=False)]
print(f"  沪深6位: {len(df_rt)}")

# 3. 股价 1-20元
df_rt = df_rt[(df_rt['price'] >= 1) & (df_rt['price'] < 20)]
print(f"  股价1-20元: {len(df_rt)}")

# 4. 成交额 > 5000万（单位：元）
df_rt = df_rt[df_rt['amount'] > 5e7]
print(f"  成交额>5000万: {len(df_rt)}")

# 5. 涨幅 0.3% ~ 4.8%（排除涨停和微涨/跌）
df_rt = df_rt[(df_rt['pct'] >= 0.3) & (df_rt['pct'] < 4.8)]
print(f"  涨幅0.3-4.8%: {len(df_rt)}")

# 6. 换手率 2%-15%（若有数据）
if 'turnover' in df_rt.columns:
    has_turn = df_rt['turnover'].notna() & (df_rt['turnover'] > 0)
    df_turn = df_rt[has_turn]
    df_turn = df_turn[(df_turn['turnover'] >= 2) & (df_turn['turnover'] <= 15)]
    df_no_turn = df_rt[~has_turn]
    df_rt = pd.concat([df_turn, df_no_turn])
    print(f"  换手率2-15%(有数据才过滤): {len(df_rt)}")

# 7. 流通市值 < 150亿（若有数据）
if 'float_mv' in df_rt.columns:
    has_mv = df_rt['float_mv'].notna() & (df_rt['float_mv'] > 0)
    df_mv = df_rt[has_mv]
    df_mv = df_mv[df_mv['float_mv'] < 15000000000]
    df_no_mv = df_rt[~has_mv]
    df_rt = pd.concat([df_mv, df_no_mv])
    print(f"  流通市值<150亿(有数据才过滤): {len(df_rt)}")

print(f"\n  初筛候选: {len(df_rt)} 只")
print(f"  取前 {TOP_N} 只进行MA均线计算...")

# 按成交额降序取TOP_N（优先量大活跃的）
if 'amount' in df_rt.columns:
    df_rt = df_rt.sort_values('amount', ascending=False)
df_cands = df_rt.head(TOP_N).copy()

# ============================================================
# 第四步：真实MA均线计算
# ============================================================
print("\n[4/5] 获取真实MA均线（腾讯财经）")
ma_cache = {}
t0 = time.time()
ok, fail = 0, 0

for i, (_, row) in enumerate(df_cands.iterrows()):
    code = str(row['pure_code'])
    tx_sym = code_to_tx(code)
    ma_data = get_stock_ma(tx_sym)
    if ma_data:
        ma_cache[code] = ma_data
        ok += 1
    else:
        fail += 1
    if (i + 1) % 20 == 0:
        elapsed = time.time() - t0
        print(f"  进度: {i+1}/{len(df_cands)} | 成功{ok} 失败{fail} | 耗时{elapsed:.0f}s")

elapsed = time.time() - t0
print(f"  完成! 耗时 {elapsed:.1f}s，成功 {ok}/{len(df_cands)}")

# ============================================================
# 第五步：综合打分
# ============================================================
print("\n[5/5] 综合打分...")

def match_sector(name, hot_list):
    """匹配热点板块，返回(板块名, 排名)"""
    for idx, sname in enumerate(hot_list[:20]):
        # 简单关键词匹配
        kws = sname.replace('板块', '').replace('行业', '').strip()
        if len(kws) >= 2 and kws in name:
            return sname, idx + 1
    return "未知", 99

def calc_score(row, all_hot, ma_cache):
    pct    = float(row.get('pct', 0) or 0)
    price  = float(row.get('price', 0) or 0)
    name   = str(row.get('name', ''))
    code   = str(row.get('pure_code', ''))
    amt    = float(row.get('amount', 0) or 0)
    amt_b  = amt / 1e8
    open_p = float(row.get('open', price) or price)
    high_p = float(row.get('high', price) or price)
    low_p  = float(row.get('low', price) or price)
    volume = float(row.get('volume', 0) or 0)
    vol_r  = float(row.get('vol_ratio', 1) or 1)
    turn   = float(row.get('turnover', 0) or 0)

    # 均价（分时均价线代理）
    avg_price = amt / (volume * 100) if volume > 0 else price
    above_avg = price > avg_price
    bullish   = pct > 0.3 and price >= open_p

    # 基础拦截
    if pct <= 0 or pct >= 4.8 or price <= 0:
        return None
    # 拦截极长上影线（(最高-收盘)/收盘 > 5%）
    if high_p > 0:
        upper_shadow_pct = (high_p - price) / price * 100
        if upper_shadow_pct > 5:
            return None

    ma_data = ma_cache.get(code)
    ma_ok = ma_data is not None

    if ma_ok:
        ma5  = ma_data['ma5']
        ma10 = ma_data['ma10']
        ma20 = ma_data['ma20']
        ma5_slope = ma_data['ma5_slope']
        hi60 = ma_data['hi60']
        close = ma_data['close']
    else:
        # fallback: 用当前价代理
        ma5 = ma10 = ma20 = price
        ma5_slope = 0
        hi60 = price
        close = price

    score = 0

    # ---- 1. 均线多头排列 20% ----
    if ma_ok:
        if close > ma5 > ma10 > ma20 and ma5_slope > 0:
            ms = 100   # 完美多头 + MA5斜率向上
        elif close > ma5 > ma10 > ma20:
            ms = 90    # 多头排列（斜率待确认）
        elif close > ma10 > ma20:
            ms = 75    # 在MA10上方
        elif close > ma20:
            ms = 55    # 仅在MA20上方
        else:
            return None   # 跌破MA20，直接排除
    else:
        ms = 50   # 无均线数据，中性分
    score += ms * 0.20

    # ---- 2. 分时强度 20% ----
    if above_avg and bullish:  ts = 95
    elif above_avg:            ts = 75
    elif bullish:              ts = 60
    else:                      ts = 30
    score += ts * 0.20

    # ---- 3. 量价配合 20% ----
    if vol_r >= 2.0 and 1.5 <= pct <= 4:   vs = 100  # 量比高 + 合理涨幅
    elif vol_r >= 1.5:                       vs = 85
    elif vol_r >= 1.0:                       vs = 65
    elif vol_r >= 0.7:                       vs = 45
    else:                                    vs = 25
    # 成交额补偿
    if amt_b >= 2.0:   vs = min(100, vs + 10)
    elif amt_b >= 1.0: vs = min(100, vs + 5)
    score += vs * 0.20

    # ---- 4. 板块热度 20% ----
    sec_name, sec_rank = match_sector(name, all_hot)
    if   sec_rank <= 3:  ss = 100
    elif sec_rank <= 7:  ss = 85
    elif sec_rank <= 12: ss = 70
    elif sec_rank <= 20: ss = 55
    else:                ss = 20
    score += ss * 0.20

    # ---- 5. 形态 10% ----
    pat = "阳线稳健"; ps = 50
    if ma_ok:
        dev_ma5  = (close - ma5)  / ma5  * 100 if ma5 > 0 else 0
        dev_ma10 = (close - ma10) / ma10 * 100 if ma10 > 0 else 0
        if 0 <= dev_ma5 <= 2 and bullish and ma5_slope > 0:
            pat = "均线回踩买点"; ps = 100
        elif close > ma5 > ma10 > ma20 and 1 <= pct <= 3 and bullish:
            pat = "均线多头"; ps = 90
        elif 2 <= pct <= 4 and amt_b >= 0.8 and upper_shadow_pct < 2:
            pat = "平台突破"; ps = 85
        elif 0 <= dev_ma10 <= 3 and bullish:
            pat = "MA10支撑"; ps = 80
        elif bullish and pct >= 1:
            pat = "阳线稳健"; ps = 60
    else:
        if 2 <= pct <= 4 and amt_b >= 0.8:
            pat = "平台突破(估)"; ps = 70

    score += ps * 0.10

    # ---- 6. 弹性空间 10% ----
    if ma_ok and hi60 > 0:
        dist60 = (hi60 - close) / hi60 * 100
        if   dist60 >= 20: ss2 = 100
        elif dist60 >= 12: ss2 = 85
        elif dist60 >= 6:  ss2 = 70
        elif dist60 >= 3:  ss2 = 50
        else:              ss2 = 20   # 接近60日高点，空间不足
    else:
        if 1.5 <= pct <= 3: ss2 = 75
        elif 1 <= pct < 1.5: ss2 = 60
        else: ss2 = 40
    score += ss2 * 0.10

    # 流通市值（亿元）
    float_mv_b = float(row.get('float_mv', 0) or 0) / 1e8
    if float_mv_b <= 0:
        float_mv_b = None

    return {
        'code': code, 'name': name,
        'price': price, 'pct': pct,
        'amount_b': round(amt_b, 2),
        'vol_ratio': round(vol_r, 2),
        'turnover': round(turn, 2),
        'float_mv_b': round(float_mv_b, 1) if float_mv_b else None,
        'above_avg': above_avg, 'bullish': bullish,
        'ma_ok': ma_ok,
        'ma5':  round(ma5, 3) if ma_ok else None,
        'ma10': round(ma10, 3) if ma_ok else None,
        'ma20': round(ma20, 3) if ma_ok else None,
        'ma5_slope': round(ma5_slope, 4) if ma_ok else None,
        'hi60': round(hi60, 2) if ma_ok else None,
        'sector': sec_name, 'sector_rank': sec_rank,
        'pattern': pat, 'score': round(score, 1)
    }

results = []
for _, row in df_rt.iterrows():
    sc = calc_score(row, all_hot, ma_cache)
    if sc:
        results.append(sc)

df_res = pd.DataFrame(results) if results else pd.DataFrame()
if len(df_res) > 0:
    df_res = df_res.sort_values('score', ascending=False).head(10)

# ============================================================
# 第六步：输出
# ============================================================
print("\n" + "=" * 60)
print(f"大盘环境状态 [{TODAY}]")
print("=" * 60)
print(f"上证指数:  {sh_close:.2f} | MA20={sh_ma20:.2f} | 今日{sh_pct:+.2f}%  [PASS]")
print(f"创业板指:  {cy_close:.2f} | MA20={cy_ma20:.2f} | 今日{cy_pct:+.2f}% [PASS]")
print(f"大盘结论:  满足选股条件，执行策略")

print(f"\n选股结果 TOP{min(10, len(df_res))}（按综合得分降序）")
print("=" * 60)

if len(df_res) == 0:
    print("今日无符合条件的股票，建议观望。")
else:
    for i, row in df_res.reset_index(drop=True).iterrows():
        ma_info = ""
        if row['ma_ok']:
            arr_mark = "✓" if row['ma5'] and row['ma10'] and row['ma20'] and row['ma5'] > row['ma10'] > row['ma20'] else "~"
            ma_info  = f"MA5={row['ma5']:.2f} MA10={row['ma10']:.2f} MA20={row['ma20']:.2f} {arr_mark}"
        else:
            ma_info  = "[均线不可用]"

        mv_str = f"{row['float_mv_b']:.1f}亿" if row.get('float_mv_b') else "未知"
        turn_str = f"{row['turnover']:.1f}%" if row.get('turnover') else "未知"

        print(f"\n【{i+1}】[{row['code']}] {row['name']}")
        print(f"    价格: {row['price']:.2f}元 | 涨幅: {row['pct']:.2f}% | 量比: {row['vol_ratio']} | 换手率: {turn_str}")
        print(f"    流通市值: {mv_str} | 成交额: {row['amount_b']:.2f}亿")
        print(f"    均线排列: {ma_info}")
        print(f"    分时均价上方: {'是' if row['above_avg'] else '否'} | 阳线: {'是' if row['bullish'] else '否'}")
        print(f"    板块: {row['sector']}(排名第{row['sector_rank']}) | 形态: {row['pattern']}")
        print(f"    综合得分: {row['score']}")

print("\n" + "=" * 60)
print("操作提醒")
print("=" * 60)
print("策略已过滤，请于尾盘(14:55-15:00)结合分时图")
print("确认回踩均线不破后决策，次日早盘严格执行止盈止损纪律。")
print(f"\n--- 运行完成: {TODAY} (v5.1) ---")

# 保存原始结果到变量（供后续Markdown生成使用）
import json
if len(df_res) > 0:
    df_res.to_csv('screener_v5_result.csv', index=False, encoding='utf-8-sig')
    print(f"\n结果已保存到 screener_v5_result.csv ({len(df_res)} 条)")

# 输出JSON方便解析
result_data = {
    'date': TODAY,
    'market': {
        'sh_close': sh_close, 'sh_ma20': sh_ma20, 'sh_pct': sh_pct,
        'cy_close': cy_close, 'cy_ma20': cy_ma20, 'cy_pct': cy_pct,
        'env_pass': True
    },
    'hot_industry': hot_industry[:10],
    'hot_concept': hot_concept[:10],
    'results': df_res.to_dict('records') if len(df_res) > 0 else []
}
with open('screener_v5_result.json', 'w', encoding='utf-8') as f:
    json.dump(result_data, f, ensure_ascii=False, indent=2, default=str)
print("结果已保存到 screener_v5_result.json")
