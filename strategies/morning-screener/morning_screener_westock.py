#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股早盘筛选脚本 - 使用westockdata数据源
"""
import subprocess
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
import os

warnings.filterwarnings('ignore')

# 配置区（早盘版）
MIN_PRICE = 5
MAX_PRICE = 35
MIN_TURNOVER = 3
MAX_TURNOVER = 15
MIN_GAIN = 0
MAX_GAIN = 8
MIN_VOL_RATIO = 1.5
MIN_VOLUME = 1.5e8

OUTPUT_DIR = r"C:\Users\西西家的咩咩\WorkBuddy\2026-05-10-task-14"
today_str = datetime.now().strftime("%Y%m%d")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, f"早盘候选_{today_str}.csv")
REPORT_FILE = os.path.join(OUTPUT_DIR, f"早盘候选_{today_str}.txt")

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def is_trading_day():
    today = datetime.now()
    if today.weekday() >= 5:
        return False
    return True

def run_westock_command(cmd):
    """执行westock-data命令并返回DataFrame"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return None
        lines = result.stdout.strip().split('\n')
        if len(lines) < 3:
            return None
        data_lines = [l for l in lines if l.strip() and not l.strip().startswith('|-') and not l.strip().startswith('| ---')]
        if len(data_lines) < 2:
            return None
        header = [h.strip() for h in data_lines[0].split('|')[1:-1]]
        rows = []
        for line in data_lines[1:]:
            cells = [c.strip() for c in line.split('|')[1:-1]]
            if len(cells) == len(header):
                rows.append(dict(zip(header, cells)))
        return pd.DataFrame(rows)
    except Exception as e:
        log(f"命令执行失败: {e}")
        return None

def get_hot_stocks(limit=80):
    """获取热门股票"""
    log("获取热门股票...")
    cmd = f"npx -y westock-data-clawhub@1.0.4 hot stock --limit {limit}"
    df = run_westock_command(cmd)
    if df is not None:
        log(f"✅ 获取到 {len(df)} 只热门股票")
    return df

def calc_indicators_from_kline(stock_code):
    """从K线数据计算技术指标"""
    try:
        cmd = f"npx -y westock-data-clawhub@1.0.4 kline {stock_code} --period day --limit 60"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None, None, None, None
        
        lines = result.stdout.strip().split('\n')
        data_lines = [l for l in lines if l.strip() and not l.strip().startswith('|-') and not l.strip().startswith('| ---')]
        if len(data_lines) < 2:
            return None, None, None, None
        
        header = [h.strip() for h in data_lines[0].split('|')[1:-1]]
        rows = []
        for line in data_lines[1:]:
            cells = [c.strip() for c in line.split('|')[1:-1]]
            if len(cells) == len(header):
                rows.append(dict(zip(header, cells)))
        
        df = pd.DataFrame(rows)
        if len(df) < 20:
            return None, None, None, None
        
        close_vals = []
        for col in df.columns:
            if '收盘' in col or 'close' in col.lower():
                close_vals = pd.to_numeric(df[col], errors='coerce')
                break
        
        if not close_vals or close_vals.isna().all():
            try:
                close_vals = pd.to_numeric(df.iloc[:, 3], errors='coerce')
            except:
                return None, None, None, None
        
        if close_vals.isna().all():
            return None, None, None, None
        
        ma5 = close_vals.rolling(5).mean().iloc[-1]
        ma10 = close_vals.rolling(10).mean().iloc[-1]
        ma20 = close_vals.rolling(20).mean().iloc[-1]
        
        delta = close_vals.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.ewm(com=13, min_periods=14).mean()
        avg_loss = loss.ewm(com=13, min_periods=14).mean()
        
        if avg_loss.iloc[-1] == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs)).iloc[-1]
        
        return ma5, ma10, ma20, rsi
    except Exception as e:
        return None, None, None, None

def layer1_basic(df):
    """第1层：基础门槛"""
    log("📊 第1层：基础门槛过滤")
    orig = len(df)
    
    if 'zxj' in df.columns:
        df['zxj_num'] = pd.to_numeric(df['zxj'], errors='coerce')
        df = df[(df['zxj_num'] >= MIN_PRICE) & (df['zxj_num'] <= MAX_PRICE)]
    
    if 'zdf' in df.columns:
        df['zdf_num'] = pd.to_numeric(df['zdf'], errors='coerce')
        df = df[(df['zdf_num'] >= MIN_GAIN) & (df['zdf_num'] <= MAX_GAIN)]
    
    if 'name' in df.columns:
        df = df[~df['name'].str.contains('ST|退', na=False)]
    
    log(f"   过滤: {orig} → {len(df)}")
    return df

def layer2_trend(df):
    """第2层：趋势确认"""
    log("📈 第2层：趋势确认（计算技术指标）")
    results = []
    total = len(df)
    
    for i, (_, row) in enumerate(df.iterrows()):
        code = row['code']
        try:
            ma5, ma10, ma20, rsi = calc_indicators_from_kline(code)
            if ma5 is None:
                continue
            
            cur_price = pd.to_numeric(row.get('zxj', 0), errors='coerce')
            if pd.isna(cur_price) or cur_price == 0:
                continue
            
            if not (ma5 > ma10 > ma20 and cur_price > ma20):
                continue
            
            if not (40 <= rsi <= 80):
                continue
            
            results.append({
                '代码': code,
                '名称': row.get('name', ''),
                '最新价': cur_price,
                '涨跌幅': pd.to_numeric(row.get('zdf', 0), errors='coerce'),
                'MA5': round(ma5, 2),
                'MA10': round(ma10, 2),
                'MA20': round(ma20, 2),
                'RSI': round(rsi, 2)
            })
        except Exception:
            continue
        
        if (i + 1) % 10 == 0:
            log(f"   进度: {i+1}/{total}")
    
    result_df = pd.DataFrame(results)
    log(f"   趋势过滤: {total} → {len(result_df)}")
    return result_df

def layer6_risk(df):
    """第6层：风控"""
    log("🛡️ 第6层：风控过滤")
    if '名称' in df.columns:
        df = df[~df['名称'].str.contains('ST|退', na=False)]
    log(f"   风控过滤后: {len(df)}")
    return df

def gen_report(df):
    """生成简报"""
    lines = []
    lines.append("=" * 60)
    lines.append(f"A股早盘候选股票简报 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("策略：早盘买入（09:30-10:30），择机卖出")
    lines.append("数据源：westockdata (腾讯自选股)")
    lines.append("=" * 60)
    lines.append("")
    
    if len(df) == 0:
        lines.append("今日无符合条件的候选股票")
    else:
        lines.append(f"共 {len(df)} 只候选：\n")
        for i, (_, r) in enumerate(df.iterrows(), 1):
            lines.append(f"【{i}】 {r.get('名称', r['代码'])} ({r['代码']})")
            lines.append(f"    最新价: {r.get('最新价', '-')} 元 | 涨幅: {r.get('涨跌幅', '-')}%")
            lines.append(f"    MA5/10/20: {r.get('MA5', '-')}/{r.get('MA10', '-')}/{r.get('MA20', '-')} | RSI: {r.get('RSI', '-')}")
            lines.append("")
    
    lines.append("=" * 60)
    lines.append("操作建议：")
    lines.append("1. 09:30-10:30 观察分时，确认放量突破后买入")
    lines.append("2. 止损：-3% 严格执行")
    lines.append("3. 止盈：+5%~+10% 分批卖出，或尾盘(14:45)前卖出")
    lines.append("4. 仓位：单只≤20%，同时持有≤3只")
    lines.append("=" * 60)
    
    txt = "\n".join(lines)
    with open(REPORT_FILE, 'w', encoding='utf-8') as f:
        f.write(txt)
    log(f"📝 简报已保存: {REPORT_FILE}")
    return txt

def run_morning_screener():
    log("=" * 60)
    log("🚀 A股早盘筛选开始 (westockdata版)")
    log("=" * 60)
    
    if not is_trading_day():
        log("⚠️ 今天不是交易日（周末），跳过")
        return
    
    hot_df = get_hot_stocks(limit=80)
    if hot_df is None or len(hot_df) == 0:
        log("❌ 无法获取热门股票数据")
        return
    
    df = layer1_basic(hot_df.copy())
    if len(df) == 0:
        log("❌ 第1层后无候选")
        print("\n⚠️ 今日无符合条件的候选股票")
        return
    
    df = layer2_trend(df)
    if len(df) == 0:
        log("❌ 第2层后无候选")
        print("\n⚠️ 今日无符合条件的候选股票")
        return
    
    df = layer6_risk(df)
    if len(df) == 0:
        log("❌ 第6层后无候选")
        print("\n⚠️ 今日无符合条件的候选股票")
        return
    
    if '涨跌幅' in df.columns:
        df = df.sort_values('涨跌幅', ascending=False).reset_index(drop=True)
    
    log("=" * 60)
    log(f"✅ 筛选完成！共 {len(df)} 只候选股票")
    log("=" * 60)
    
    if len(df) > 0:
        print("\n📋 早盘候选股票清单：")
        print(df.to_string(index=False))
        
        df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')
        log(f"💾 CSV已保存: {OUTPUT_FILE}")
        
        txt = gen_report(df)
        print("\n" + txt)

if __name__ == "__main__":
    run_morning_screener()
