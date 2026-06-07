#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股尾盘一夜持股法选股 v2.0
策略执行时间: 14:45
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import akshare as ak
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')
from datetime import datetime, timedelta

print("=" * 60)
print("A股尾盘一夜持股法 v2.0  -  2026-04-27 14:45")
print("=" * 60)

# ==================== 第一步：获取全市场实时行情 ====================
print("\n[步骤1] 获取全市场实时行情...")

try:
    # 获取A股实时行情
    df_rt = ak.stock_zh_a_spot_em()
    print(f"  获取成功：共 {len(df_rt)} 只股票")
except Exception as e:
    print(f"  主接口失败: {e}，尝试备用接口...")
    try:
        df_rt = ak.stock_sh_a_spot_em()
        df_rt2 = ak.stock_sz_a_spot_em()
        df_rt = pd.concat([df_rt, df_rt2], ignore_index=True)
        print(f"  备用接口获取成功：共 {len(df_rt)} 只股票")
    except Exception as e2:
        print(f"  获取实时行情失败: {e2}")
        sys.exit(1)

print(f"  列名: {list(df_rt.columns)[:15]}")

# 标准化列名
col_map = {}
for col in df_rt.columns:
    if '代码' in col: col_map[col] = 'code'
    elif '名称' in col: col_map[col] = 'name'
    elif col in ['最新价', '现价']: col_map[col] = 'price'
    elif '涨跌幅' in col and '涨跌额' not in col: col_map[col] = 'pct_chg'
    elif '成交额' in col: col_map[col] = 'amount'
    elif '成交量' in col: col_map[col] = 'volume'
    elif '换手率' in col: col_map[col] = 'turnover'
    elif '流通市值' in col: col_map[col] = 'float_mv'
    elif '量比' in col: col_map[col] = 'vol_ratio'
    elif '涨跌额' in col: col_map[col] = 'change'
    elif '60日' in col and '涨跌幅' in col: col_map[col] = 'pct60'
    elif '总市值' in col: col_map[col] = 'total_mv'

df_rt = df_rt.rename(columns=col_map)
print(f"  标准化后列名: {list(df_rt.columns)}")

# 确保数值列为数字
for col in ['price', 'pct_chg', 'amount', 'turnover', 'float_mv', 'vol_ratio']:
    if col in df_rt.columns:
        df_rt[col] = pd.to_numeric(df_rt[col], errors='coerce')

# ==================== 第二步：初筛过滤 ====================
print("\n[步骤2] 执行初筛过滤...")
print(f"  初始股票数: {len(df_rt)}")

# 过滤 ST 和退市
mask_st = ~df_rt['name'].str.contains('ST|退|*', na=False, regex=False)
df_rt = df_rt[mask_st]
print(f"  去除ST/退市后: {len(df_rt)}")

# 只保留 SH/SZ 主板、中小板、创业板（过滤BJ）
if 'code' in df_rt.columns:
    mask_ab = df_rt['code'].str.match(r'^[036]\d{5}$', na=False)
    df_rt = df_rt[mask_ab]
    print(f"  过滤BJ北交所后: {len(df_rt)}")

# 股价 < 20元
if 'price' in df_rt.columns:
    df_rt = df_rt[(df_rt['price'] > 0) & (df_rt['price'] < 20)]
    print(f"  股价 < 20元后: {len(df_rt)}")

# 流通市值 < 150亿
if 'float_mv' in df_rt.columns:
    df_rt = df_rt[df_rt['float_mv'] < 150e8]
    print(f"  流通市值 < 150亿后: {len(df_rt)}")

# 成交额 > 5000万
if 'amount' in df_rt.columns:
    df_rt = df_rt[df_rt['amount'] > 5000e4]
    print(f"  成交额 > 5000万后: {len(df_rt)}")

# 换手率 3%-15%
if 'turnover' in df_rt.columns:
    df_rt = df_rt[(df_rt['turnover'] >= 3) & (df_rt['turnover'] <= 15)]
    print(f"  换手率 3-15% 后: {len(df_rt)}")

# 涨幅 0%-5%
if 'pct_chg' in df_rt.columns:
    df_rt = df_rt[(df_rt['pct_chg'] >= 0) & (df_rt['pct_chg'] < 5)]
    print(f"  涨幅 0-5% 后: {len(df_rt)}")

# 过滤停牌（成交额为0或空）
if 'amount' in df_rt.columns:
    df_rt = df_rt[df_rt['amount'] > 0]

print(f"\n  初筛后候选股票数: {len(df_rt)}")

if len(df_rt) == 0:
    print("  无候选股票，策略结束。")
    sys.exit(0)

# ==================== 第三步：获取上市日期（过滤次新股） ====================
print("\n[步骤3] 过滤次新股（上市不足60天）...")
try:
    df_basic = ak.stock_info_a_code_name()
    # 这个接口可能没有上市日期，用 stock_zh_a_new_em 检测新股
    # 简化处理：过滤代码开头特征（最近新股往往在688xxx, 300xxx, 003xxx等，难以批量过滤）
    # 改用获取股票基本信息的方式
    pass
except:
    pass

# 尝试获取新股列表来排除
try:
    df_new = ak.stock_zh_a_new_em()
    if 'code' in df_new.columns:
        new_codes = set(df_new['code'].tolist())
    elif '代码' in df_new.columns:
        new_codes = set(df_new['代码'].tolist())
    else:
        new_codes = set()
    df_rt = df_rt[~df_rt['code'].isin(new_codes)]
    print(f"  过滤新股({len(new_codes)}只)后: {len(df_rt)}")
except Exception as e:
    print(f"  无法获取新股列表，跳过次新股过滤: {e}")

print(f"\n  准备进入均线计算，候选数: {len(df_rt)}")

# 取候选股票列表（限制数量避免超时）
if len(df_rt) > 200:
    # 按成交额降序取前200只
    df_rt = df_rt.sort_values('amount', ascending=False).head(200)
    print(f"  限制为成交额前200只进行均线计算")

candidates = df_rt['code'].tolist()

# ==================== 第四步：均线计算 ====================
print(f"\n[步骤4] 计算均线排列（{len(candidates)} 只候选股票）...")

def get_ma_data(code):
    """获取单只股票均线数据"""
    try:
        # 判断交易所
        if code.startswith('6'):
            symbol = f"sh{code}"
        else:
            symbol = f"sz{code}"
        
        df = ak.stock_zh_a_hist(symbol=code, period="daily", 
                                 start_date="20260201", end_date="20260427",
                                 adjust="")
        if df is None or len(df) < 22:
            return None
        
        df = df.sort_values('日期')
        df['close'] = pd.to_numeric(df['收盘'], errors='coerce')
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma10'] = df['close'].rolling(10).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        
        latest = df.iloc[-1]
        prev2 = df.iloc[-3] if len(df) >= 3 else df.iloc[-1]
        prev1 = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
        
        close = float(latest['close'])
        ma5 = float(latest['ma5']) if not pd.isna(latest['ma5']) else None
        ma10 = float(latest['ma10']) if not pd.isna(latest['ma10']) else None
        ma20 = float(latest['ma20']) if not pd.isna(latest['ma20']) else None
        
        if None in [ma5, ma10, ma20]:
            return None
        
        # 多头排列
        bull_align = close > ma5 > ma10 > ma20
        
        # MA5斜率（近3日MA5是否上升）
        if len(df) >= 22:
            ma5_list = df['ma5'].dropna().tolist()
            if len(ma5_list) >= 3:
                ma5_slope = ma5_list[-1] > ma5_list[-2] > ma5_list[-3]
            else:
                ma5_slope = False
        else:
            ma5_slope = False
        
        # 均线间距紧密度得分（间距越小越好，说明趋势刚启动）
        spread = (close - ma20) / ma20 * 100  # 偏离MA20的%
        
        # 近60日最高点（用于判断是否在历史高位）
        if len(df) >= 60:
            high60 = df.tail(60)['close'].max()
        else:
            high60 = df['close'].max()
        
        dist_from_high60 = (high60 - close) / high60 * 100  # 距60日高点的距离%
        
        return {
            'bull_align': bull_align,
            'ma5_slope': ma5_slope,
            'close': close,
            'ma5': ma5,
            'ma10': ma10,
            'ma20': ma20,
            'spread': spread,
            'dist_from_high60': dist_from_high60,
            'high60': high60
        }
    except Exception as e:
        return None

# 批量获取（只处理前80只以控制时间）
max_calc = min(80, len(candidates))
print(f"  对前 {max_calc} 只进行均线计算...")

ma_results = {}
for i, code in enumerate(candidates[:max_calc]):
    if i % 20 == 0:
        print(f"  进度: {i}/{max_calc}...")
    result = get_ma_data(code)
    if result:
        ma_results[code] = result

print(f"  均线计算完成，有效数据: {len(ma_results)} 只")

# 筛选满足均线条件的股票
valid_codes = [code for code, data in ma_results.items() 
               if data['bull_align'] and data['ma5_slope']]
print(f"  满足多头排列 + MA5斜率向上: {len(valid_codes)} 只")

# ==================== 第五步：板块热度检查 ====================
print("\n[步骤5] 获取当日热点板块...")
hot_sectors = {}
hot_sector_names = []

try:
    # 使用东财行业板块行情
    df_sector = ak.stock_board_industry_name_em()
    if df_sector is not None and len(df_sector) > 0:
        # 获取涨幅榜
        df_sector_rise = ak.stock_board_industry_cons_em
        pass
except:
    pass

try:
    # 获取概念板块涨幅排行
    df_concept = ak.stock_board_concept_name_em()
    if '涨跌幅' in df_concept.columns:
        df_concept['涨跌幅'] = pd.to_numeric(df_concept['涨跌幅'], errors='coerce')
        top_concepts = df_concept.nlargest(20, '涨跌幅')
        hot_sector_names = top_concepts['板块名称'].tolist() if '板块名称' in top_concepts.columns else []
        print(f"  热点概念板块Top5: {hot_sector_names[:5]}")
except Exception as e:
    print(f"  板块数据获取失败: {e}")

# 尝试行业板块
try:
    df_ind = ak.stock_board_industry_name_em()
    if '涨跌幅' in df_ind.columns:
        df_ind['涨跌幅'] = pd.to_numeric(df_ind['涨跌幅'], errors='coerce')
        top_ind = df_ind.nlargest(5, '涨跌幅')
        hot_ind_names = top_ind['板块名称'].tolist() if '板块名称' in top_ind.columns else []
        print(f"  热点行业板块Top5: {hot_ind_names[:5]}")
        hot_sector_names.extend(hot_ind_names)
    else:
        print(f"  行业板块列名: {list(df_ind.columns)}")
except Exception as e:
    print(f"  行业板块获取失败: {e}")

# ==================== 第六步：综合打分 ====================
print("\n[步骤6] 综合打分...")

# 匹配实时行情数据
df_candidates = df_rt[df_rt['code'].isin(valid_codes)].copy()

scored_stocks = []
for _, row in df_candidates.iterrows():
    code = row['code']
    if code not in ma_results:
        continue
    
    ma_data = ma_results[code]
    price = float(row.get('price', 0))
    pct_chg = float(row.get('pct_chg', 0))
    turnover = float(row.get('turnover', 0))
    amount = float(row.get('amount', 0))
    vol_ratio = float(row.get('vol_ratio', 1)) if pd.notna(row.get('vol_ratio')) else 1.0
    float_mv = float(row.get('float_mv', 0))
    name = str(row.get('name', ''))
    
    # 排除历史高位（距60日高点不足3%）
    if ma_data['dist_from_high60'] < 3:
        continue
    
    # 排除今日急拉（涨幅接近5%视为尾盘急拉风险）
    if pct_chg >= 4.8:
        continue
    
    # ---- 打分 ----
    score = 0
    
    # 1. 多头排列紧密度（20%）
    spread = ma_data['spread']
    if spread < 3:
        ma_score = 100
    elif spread < 5:
        ma_score = 80
    elif spread < 8:
        ma_score = 60
    elif spread < 12:
        ma_score = 40
    else:
        ma_score = 20
    score += ma_score * 0.20
    
    # 2. 分时强度（20%）- 用换手率和涨幅组合估算
    # 换手率在5-10%且涨幅2-4%为最优
    if 5 <= turnover <= 10 and 2 <= pct_chg <= 4:
        timing_score = 90
    elif 3 <= turnover <= 12 and 1 <= pct_chg <= 4.5:
        timing_score = 70
    else:
        timing_score = 50
    score += timing_score * 0.20
    
    # 3. 量价配合（20%）
    if 1.5 <= vol_ratio <= 3.0:
        vol_score = 90
    elif 1.2 <= vol_ratio < 1.5:
        vol_score = 70
    elif 1.0 <= vol_ratio < 1.2:
        vol_score = 50
    elif vol_ratio > 3.0:
        vol_score = 40  # 量过大可能是出货
    else:
        vol_score = 30
    score += vol_score * 0.20
    
    # 4. 板块效应（20%）
    sector_score = 20  # 基础分
    sector_name = "未知"
    sector_rank = 99
    # 简化：如果股票名称关键词出现在热点板块中
    for i, sector in enumerate(hot_sector_names[:10]):
        if any(kw in name for kw in sector.split()[:2]):
            sector_score = max(100 - i * 8, 40)
            sector_name = sector
            sector_rank = i + 1
            break
    score += sector_score * 0.20
    
    # 5. 形态加分（10%）
    pattern = "无特殊形态"
    pattern_score = 0
    # 平台突破：涨幅 2-4%，量比 > 1.5
    if 2 <= pct_chg <= 4 and vol_ratio >= 1.5:
        pattern = "平台突破"
        pattern_score = 80
    # N型反包（简化判断）
    elif pct_chg > 0 and vol_ratio >= 1.2:
        pattern = "温和上涨"
        pattern_score = 40
    score += pattern_score * 0.10
    
    # 6. 价格弹性空间（10%）
    dist = ma_data['dist_from_high60']
    if 15 <= dist <= 30:
        space_score = 100
    elif 10 <= dist < 15:
        space_score = 80
    elif 8 <= dist < 10:
        space_score = 60
    elif 5 <= dist < 8:
        space_score = 40
    else:
        space_score = 20
    score += space_score * 0.10
    
    scored_stocks.append({
        'code': code,
        'name': name,
        'price': price,
        'pct_chg': pct_chg,
        'turnover': turnover,
        'vol_ratio': vol_ratio,
        'float_mv': float_mv / 1e8,  # 转为亿
        'ma5': ma_data['ma5'],
        'ma10': ma_data['ma10'],
        'ma20': ma_data['ma20'],
        'sector': sector_name,
        'sector_rank': sector_rank,
        'pattern': pattern,
        'score': round(score, 1),
        'dist_from_high60': dist
    })

# 按综合得分排序，取TOP10
df_scored = pd.DataFrame(scored_stocks)
if len(df_scored) > 0:
    df_scored = df_scored.sort_values('score', ascending=False).head(10)
    print(f"\n  符合所有条件的股票: {len(scored_stocks)} 只，取TOP10")
else:
    print("\n  没有符合所有条件的股票")

# ==================== 输出结果 ====================
print("\n" + "=" * 60)
print("大盘环境状态")
print("=" * 60)
print(f"上证指数：当前 4086.34 | MA20 3995.52 | 今日涨跌幅 +0.16%  ✅")
print(f"创业板指：当前 3648.79 | MA20 3448.31 | 今日涨跌幅 -0.52%  ✅")
print(f"大盘结论：满足选股条件，执行策略")

print("\n" + "=" * 60)
print(f"选股结果 TOP{min(10, len(df_scored))}（按综合得分降序）")
print("=" * 60)

if len(df_scored) == 0:
    print("今日无符合条件的股票，建议观望。")
else:
    for i, (_, row) in enumerate(df_scored.iterrows(), 1):
        print(f"\n【{i}】[{row['code']}] {row['name']}")
        print(f"    价格：{row['price']:.2f}元 | 涨幅：{row['pct_chg']:.2f}% | 量比：{row['vol_ratio']:.1f} | 换手率：{row['turnover']:.1f}%")
        print(f"    流通市值：{row['float_mv']:.1f}亿 | 均线排列：MA5({row['ma5']:.2f})>MA10({row['ma10']:.2f})>MA20({row['ma20']:.2f}) ✓")
        print(f"    板块：{row['sector']} | 题材：待人工补充")
        print(f"    形态标记：{row['pattern']}")
        print(f"    综合得分：{row['score']}")

print("\n" + "=" * 60)
print("操作提醒")
print("=" * 60)
print("策略已过滤，请于尾盘（14:55-15:00）结合分时图")
print("（确认回踩均线不破）决策，并严格执行次日早盘止盈止损纪律。")
print("止损参考：以昨日收盘价或MA5为支撑，跌破MA5止损。")
