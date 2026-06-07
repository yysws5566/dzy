#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股尾盘一夜持股法选股 v2.0
执行时间: 14:45
数据源优先级: 腾讯(主) -> 新浪指数 -> NeoData
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import akshare as ak
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')
import time

def get_rt_market():
    """获取全市场实时行情（腾讯接口 + 东财备用）"""
    print("  [1] 腾讯接口...")
    try:
        df = ak.stock_zh_a_spot()
        if df is not None and len(df) > 1000:
            return df, '腾讯'
    except Exception as e:
        print(f"    腾讯失败: {str(e)[:60]}")
    
    print("  [2] 东财接口...")
    try:
        df = ak.stock_zh_a_spot_em()
        if df is not None and len(df) > 1000:
            return df, '东财'
    except Exception as e:
        print(f"    东财失败: {str(e)[:60]}")
    
    return None, None

def get_index_rt():
    """获取指数实时数据"""
    try:
        df = ak.stock_zh_index_spot_sina()
        if df is not None and len(df) > 10:
            return df
    except Exception as e:
        print(f"  指数实时获取失败: {e}")
    return None

def get_index_ma20(symbol):
    """获取指数MA20"""
    try:
        df = ak.stock_zh_index_daily(symbol=symbol)
        if df is not None and len(df) >= 20:
            df = df.sort_values('date').tail(25)
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            df['ma20'] = df['close'].rolling(20).mean()
            last = df.iloc[-1]
            return float(last['close']), float(last['ma20']), str(last['date'])
    except:
        pass
    return None, None, None

def get_stock_ma(code):
    """获取单只股票均线数据"""
    try:
        if code.startswith('6'):
            sym = f"sh{code}"
        else:
            sym = f"sz{code}"
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                 start_date="20260201", end_date="20260427", adjust="")
        if df is None or len(df) < 22:
            return None
        df = df.sort_values('日期')
        df['收盘'] = pd.to_numeric(df['收盘'], errors='coerce')
        df['ma5'] = df['收盘'].rolling(5).mean()
        df['ma10'] = df['收盘'].rolling(10).mean()
        df['ma20'] = df['收盘'].rolling(20).mean()
        
        if len(df) < 22:
            return None
        
        latest = df.iloc[-1]
        c = float(latest['收盘'])
        m5 = float(latest['ma5']) if not pd.isna(latest['ma5']) else None
        m10 = float(latest['ma10']) if not pd.isna(latest['ma10']) else None
        m20 = float(latest['ma20']) if not pd.isna(latest['ma20']) else None
        
        if None in [m5, m10, m20]:
            return None
        
        # 多头排列
        bull = c > m5 > m10 > m20
        
        # MA5斜率
        ma5s = df['ma5'].dropna().tolist()
        if len(ma5s) >= 3:
            slope = ma5s[-1] > ma5s[-2] > ma5s[-3]
        else:
            slope = False
        
        # 60日高点
        high60 = df.tail(60)['收盘'].max() if len(df) >= 60 else df['收盘'].max()
        dist_hi = (high60 - c) / high60 * 100
        
        return {
            'close': c, 'ma5': m5, 'ma10': m10, 'ma20': m20,
            'bull': bull, 'slope': slope,
            'dist_hi': dist_hi
        }
    except Exception as e:
        return None

def score_stock(row, ma_data, hot_sectors, vol_ratio_override=None):
    """综合打分"""
    if ma_data is None:
        return -1
    
    pct = float(row.get('pct', 0))
    turn = float(row.get('turnover', 0)) if pd.notna(row.get('turnover')) else 5.0
    name = str(row.get('name', ''))
    code = str(row.get('code', ''))
    float_mv_val = float(row.get('float_mv', 0)) if pd.notna(row.get('float_mv')) else 0
    
    # vol_ratio 可能在腾讯接口中缺失，尝试从成交额估算
    vol_r = vol_ratio_override if vol_ratio_override else 1.5
    
    # 排除
    if ma_data['dist_hi'] < 3:
        return -1
    if pct >= 4.8:
        return -1
    if not ma_data['bull'] or not ma_data['slope']:
        return -1
    
    score = 0
    
    # 1. 均线紧密度(20%)
    spread = (ma_data['close'] - ma_data['ma20']) / ma_data['ma20'] * 100
    if spread < 3: ma_s = 100
    elif spread < 5: ma_s = 80
    elif spread < 8: ma_s = 60
    elif spread < 12: ma_s = 40
    else: ma_s = 20
    score += ma_s * 0.20
    
    # 2. 分时强度(20%) - 换手率可能在腾讯接口缺失，用涨幅估算
    if 2 <= pct <= 4: ts = 85
    elif 1 <= pct < 2: ts = 70
    elif 0 <= pct < 1: ts = 50
    elif pct < 0: ts = 30
    else: ts = 40
    score += ts * 0.20
    
    # 3. 量价配合(20%) - 估算
    if vol_r >= 1.5: vs = 80
    elif vol_r >= 1.0: vs = 60
    else: vs = 40
    score += vs * 0.20
    
    # 4. 板块热度(20%)
    ss = 20
    sec = "未知"
    for sname in hot_sectors[:10]:
        kw = sname.split()[0] if sname else ""
        if kw and kw in name:
            ss = 90
            sec = sname
            break
    score += ss * 0.20
    
    # 5. 形态(10%)
    pat = "无特殊形态"
    ps = 0
    if 2 <= pct <= 4 and vol_r >= 1.5:
        pat = "平台突破"
        ps = 80
    elif pct > 0 and vol_r >= 1.2:
        pat = "温和上涨"
        ps = 40
    score += ps * 0.10
    
    # 6. 空间(10%)
    dist = ma_data['dist_hi']
    if 15 <= dist <= 30: ss2 = 100
    elif 10 <= dist < 15: ss2 = 80
    elif 8 <= dist < 10: ss2 = 60
    elif 5 <= dist < 8: ss2 = 40
    else: ss2 = 20
    score += ss2 * 0.10
    
    return {
        'code': code, 'name': name,
        'price': float(row.get('price', 0)),
        'pct': pct, 'vol_ratio': vol_r,
        'turnover': turn,
        'float_mv': float_mv_val / 1e8 if float_mv_val > 0 else 0,
        'ma5': ma_data['ma5'], 'ma10': ma_data['ma10'], 'ma20': ma_data['ma20'],
        'sector': sec, 'pattern': pat,
        'score': round(score, 1),
        'dist_hi': dist
    }

def main():
    print("=" * 60)
    print("A股尾盘一夜持股法 v2.0  -  2026-04-27 14:45")
    print("=" * 60)
    
    # ===== 大盘环境检查 =====
    print("\n[大盘环境检查]")
    
    # 实时指数
    idx_df = get_index_rt()
    sh_close = sh_pct = None
    cy_close = cy_pct = None
    
    if idx_df is not None:
        sh_row = idx_df[idx_df['代码'] == 'sh000001']
        cy_row = idx_df[idx_df['代码'] == 'sz399006']
        if not sh_row.empty:
            sh_close = float(sh_row.iloc[0]['最新价'])
            sh_pct = float(sh_row.iloc[0]['涨跌幅'])
        if not cy_row.empty:
            cy_close = float(cy_row.iloc[0]['最新价'])
            cy_pct = float(cy_row.iloc[0]['涨跌幅'])
    
    # MA20
    sh_c, sh_ma20, sh_dt = get_index_ma20('sh000001')
    cy_c, cy_ma20, cy_dt = get_index_ma20('sz399006')
    
    if sh_close is None: sh_close = sh_c
    if cy_close is None: cy_close = cy_c
    
    print(f"  上证: {sh_close:.2f} | MA20: {sh_ma20:.2f} | 今日: {sh_pct:+.2f}%")
    print(f"  创业板: {cy_close:.2f} | MA20: {cy_ma20:.2f} | 今日: {cy_pct:+.2f}%")
    
    c1 = sh_close > sh_ma20
    c2 = cy_close > cy_ma20
    c3 = sh_pct > -1.0
    
    print(f"\n  条件1 上证>MA20: {'PASS' if c1 else 'FAIL'}")
    print(f"  条件2 创业板>MA20: {'PASS' if c2 else 'FAIL'}")
    print(f"  条件3 上证跌幅>-1%: {'PASS' if c3 else 'FAIL'} ({sh_pct:+.2f}%)")
    
    if not (c1 and c2 and c3):
        print("\n[FAIL] 大盘环境不满足，今日休息。")
        return
    
    print("\n[PASS] 大盘环境满足，开始选股！")
    
    # ===== 获取热点板块 =====
    print("\n[获取热点板块]")
    hot_sectors = []
    try:
        df_ind = ak.stock_board_industry_name_ths()
        if df_ind is not None and len(df_ind) > 0:
            hot_sectors = df_ind['name'].tolist()[:20]
            print(f"  行业板块TOP5: {hot_sectors[:5]}")
    except Exception as e:
        print(f"  行业板块失败: {e}")
    
    try:
        df_con = ak.stock_board_concept_name_ths()
        if df_con is not None and len(df_con) > 0:
            hot_con = df_con['板块名称'].tolist()[:10] if '板块名称' in df_con.columns else df_con['name'].tolist()[:10]
            print(f"  概念板块TOP5: {hot_con[:5]}")
            hot_sectors.extend(hot_con)
    except Exception as e:
        print(f"  概念板块失败: {e}")
    
    # ===== 获取实时行情 =====
    print("\n[获取全市场实时行情]")
    df_rt, src = get_rt_market()
    
    if df_rt is None:
        print("  全市场数据获取失败，尝试备用...")
        print("  [FAIL] 数据源不可用")
        return
    
    print(f"  数据源: {src}，共 {len(df_rt)} 只")
    
    # 标准化列
    col_map = {}
    for c in df_rt.columns:
        cl = c.lower()
        if 'code' in cl or '代码' in c: col_map[c] = 'code'
        elif 'name' in cl or '名称' in c: col_map[c] = 'name'
        elif '最新' in c or 'price' in cl: col_map[c] = 'price'
        elif '涨跌幅' in c and '涨跌额' not in c: col_map[c] = 'pct'
        elif '成交' in c and '额' in c: col_map[c] = 'amount'
        elif '换手' in c: col_map[c] = 'turnover'
        elif '流通' in c and ('市' in c or 'mktcap' in cl): col_map[c] = 'float_mv'
        elif '量比' in c: col_map[c] = 'vol_ratio'
        elif '总市' in c: col_map[c] = 'total_mv'
        elif 'sub' in cl or '板块' in c: col_map[c] = 'sector'
    
    df_rt.rename(columns=col_map, inplace=True)
    
    for c in ['price', 'pct', 'amount', 'turnover', 'float_mv', 'vol_ratio']:
        if c in df_rt.columns:
            df_rt[c] = pd.to_numeric(df_rt[c], errors='coerce')
    
    print(f"  列名: {list(df_rt.columns)}")
    
    # ===== 初筛 =====
    print("\n[初筛过滤]")
    n0 = len(df_rt)
    
    # 过滤ST
    mask = ~df_rt['name'].str.contains('ST|退|\\*', na=False, regex=True)
    df_rt = df_rt[mask]
    print(f"  去除ST: {n0} -> {len(df_rt)}")
    
    # 主板过滤（沪深主板+中小板+创业板，6位数字）
    if 'code' in df_rt.columns:
        # code 是 6 位纯数字字符串
        df_rt = df_rt[df_rt['code'].str.match(r'^\d{6}$', na=False)]
        print(f"  主板过滤: {len(df_rt)}")
    
    # 股价
    if 'price' in df_rt.columns:
        df_rt = df_rt[(df_rt['price'] > 0) & (df_rt['price'] < 20)]
        print(f"  股价<20: {len(df_rt)}")
    
    # 成交额（单位：元，换算为万需要除以10000；腾讯接口单位可能是万元）
    if 'amount' in df_rt.columns:
        # 尝试判断单位：若 amount > 1e8 则已是亿元级别，需转换为元
        median_amt = df_rt['amount'].median() if len(df_rt) > 0 else 0
        if median_amt > 1e8:
            # 亿元，换算为元
            df_rt['amount'] = df_rt['amount'] * 1e8
        elif median_amt > 1e4:
            # 万元，换算为元
            df_rt['amount'] = df_rt['amount'] * 1e4
        df_rt = df_rt[df_rt['amount'] > 5000e4]
        print(f"  成交额>5000万: {len(df_rt)}")
    
    # 换手率（注意：腾讯接口可能没有此字段，跳过）
    if 'turnover' in df_rt.columns:
        df_rt = df_rt[(df_rt['turnover'] >= 3) & (df_rt['turnover'] <= 15)]
        print(f"  换手3-15%: {len(df_rt)}")
    else:
        print(f"  换手率字段缺失，跳过")
    
    # 涨幅
    if 'pct' in df_rt.columns:
        df_rt = df_rt[(df_rt['pct'] >= 0) & (df_rt['pct'] < 5)]
        print(f"  涨幅0-5%: {len(df_rt)}")
    
    candidates = df_rt['code'].tolist()
    print(f"\n  初筛候选: {len(candidates)} 只")
    
    if len(candidates) == 0:
        print("  无候选股票，策略结束。")
        return
    
    # 按成交额排序，取前60只做均线计算
    df_rt = df_rt.sort_values('amount', ascending=False)
    to_calc = df_rt.head(60)
    
    # ===== 均线计算 =====
    print(f"\n[计算均线排列({len(to_calc)}只)]")
    ma_results = {}
    for i, (_, row) in enumerate(to_calc.iterrows()):
        if i % 15 == 0:
            print(f"  进度: {i}/{len(to_calc)}...")
        code = str(row['code'])
        ma = get_stock_ma(code)
        if ma:
            ma_results[code] = ma
    
    print(f"  均线计算完成: {len(ma_results)}只有效数据")
    
    # 筛选满足条件的
    valid = [c for c, m in ma_results.items() if m['bull'] and m['slope']]
    print(f"  多头排列+MA5斜率向上: {len(valid)} 只")
    
    if len(valid) == 0:
        print("  无满足均线条件的股票，策略结束。")
        return
    
    # ===== 批量打分 =====
    print("\n[综合打分]")
    results = []
    for _, row in df_rt[df_rt['code'].isin(valid)].iterrows():
        code = str(row['code'])
        sc = score_stock(row, ma_results.get(code), hot_sectors)
        if isinstance(sc, dict) and sc['score'] > 0:
            results.append(sc)
    
    df_results = pd.DataFrame(results)
    if len(df_results) > 0:
        df_results = df_results.sort_values('score', ascending=False).head(10)
    
    # ===== 输出 =====
    print("\n" + "=" * 60)
    print("大盘环境状态")
    print("=" * 60)
    print(f"上证指数：当前 {sh_close:.2f} | MA20 {sh_ma20:.2f} | 今日 {sh_pct:+.2f}%  [PASS]")
    print(f"创业板指：当前 {cy_close:.2f} | MA20 {cy_ma20:.2f} | 今日 {cy_pct:+.2f}%  [PASS]")
    
    print(f"\n选股结果 TOP{min(10, len(df_results))}（按综合得分降序）")
    print("=" * 60)
    
    if len(df_results) == 0:
        print("今日无符合条件的股票，建议观望。")
    else:
        for i, row in df_results.iterrows():
            print(f"\n【{list(df_results.index).index(i)+1}】[{row['code']}] {row['name']}")
            print(f"    价格: {row['price']:.2f}元 | 涨幅: {row['pct']:.2f}% | 量比: {row['vol_ratio']:.1f} | 换手率: {row['turnover']:.1f}%")
            print(f"    流通市值: {row['float_mv']:.1f}亿 | 均线: MA5({row['ma5']:.2f})>MA10({row['ma10']:.2f})>MA20({row['ma20']:.2f})")
            print(f"    板块: {row['sector']} | 形态: {row['pattern']} | 综合得分: {row['score']}")
    
    print("\n" + "=" * 60)
    print("操作提醒")
    print("=" * 60)
    print("策略已过滤，请于尾盘(14:55-15:00)结合分时图")
    print("(确认回踩均线不破)决策，并严格执行次日早盘止盈止损纪律。")

if __name__ == "__main__":
    main()
