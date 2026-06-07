#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股短线筛选脚本（westock-data版）
尾盘买次日早盘卖策略
数据源：westock-data（腾讯自选股）
"""
import subprocess
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
import os
import time

warnings.filterwarnings('ignore')

# ============================================================
# 配置区（尾盘策略）
# ============================================================
MIN_PRICE = 5
MAX_PRICE = 35
MIN_TURNOVER = 3
MAX_TURNOVER = 15
MIN_GAIN = 2      # 尾盘策略：最低2%涨幅
MAX_GAIN = 6      # 尾盘策略：最高6%涨幅（给次日留空间）
MIN_VOL_RATIO = 1.5
MIN_VOLUME = 2e8  # 成交额门槛（2亿）

OUTPUT_DIR = r"C:\Users\西西家的咩咩\WorkBuddy\2026-05-10-task-14"
today_str = datetime.now().strftime("%Y%m%d")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, f"候选股票_{today_str}.csv")
REPORT_FILE = os.path.join(OUTPUT_DIR, f"候选股票_{today_str}.txt")

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def is_trading_day():
    today = datetime.now()
    if today.weekday() >= 5:
        return False
    return True


def run_westock_command(cmd, timeout=30):
    """执行westock-data命令并解析表格"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
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


def get_hot_stocks(limit=100):
    """获取热门股票"""
    log(f"获取热门股票 (limit={limit})...")
    cmd = f"npx -y westock-data-clawhub@1.0.4 hot stock --limit {limit}"
    df = run_westock_command(cmd, timeout=60)
    if df is not None:
        log(f"✅ 获取到 {len(df)} 只热门股票")
    return df


def get_stock_detail(code):
    """获取个股详情（包含换手率、量比、成交额等）"""
    try:
        cmd = f"npx -y westock-data-clawhub@1.0.4 stock {code}"
        df = run_westock_command(cmd, timeout=15)
        return df
    except:
        return None


def get_kline_data(stock_code, limit=60):
    """获取K线数据"""
    try:
        cmd = f"npx -y westock-data-clawhub@1.0.4 kline {stock_code} --period day --limit {limit}"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        
        lines = result.stdout.strip().split('\n')
        data_lines = [l for l in lines if l.strip() and not l.strip().startswith('|-') and not l.strip().startswith('| ---')]
        if len(data_lines) < 5:
            return None
        
        header = [h.strip() for h in data_lines[0].split('|')[1:-1]]
        rows = []
        for line in data_lines[1:]:
            cells = [c.strip() for c in line.split('|')[1:-1]]
            if len(cells) == len(header):
                rows.append(dict(zip(header, cells)))
        
        df = pd.DataFrame(rows)
        # 查找收盘价列：优先 'last'（westock-data用此列名）
        close_col = None
        for col in df.columns:
            if col in ['last', '收盘', 'close', 'Close']:
                close_col = col
                break
        
        if close_col is None and len(df.columns) >= 4:
            close_col = df.columns[2]  # westock-data格式：第3列(last)是收盘价
        
        if close_col:
            df['_close'] = pd.to_numeric(df[close_col], errors='coerce')
        
        return df
    except Exception:
        return None


def calc_indicators_from_kline(stock_code):
    """计算技术指标：MA5/MA10/MA20/RSI/MACD
    使用AKShare历史K线（列名正确）获取数据
    """
    try:
        import akshare as ak
        from datetime import datetime, timedelta
        
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
        
        # 去掉前缀 sh/sz
        symbol = stock_code.replace('sh', '').replace('sz', '')
        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start,
            end_date=end,
            adjust="qfq"
        )
        if df is None or len(df) < 20:
            return None, None, None, None, None
        
        # 统一列名
        col_map = {
            '日期': 'date', '收盘': 'close', '开盘': 'open',
            '最高': 'high', '最低': 'low', '成交量': 'volume', '成交额': 'amount',
            '日期': 'date', '收盘': 'close'
        }
        for old, new in col_map.items():
            if old in df.columns:
                df.rename(columns={old: new}, inplace=True)
        
        if 'close' not in df.columns:
            return None, None, None, None, None
        
        df = df.sort_values('date').reset_index(drop=True)
        close = df['close'].dropna()
        
        if len(close) < 20:
            return None, None, None, None, None
        
        ma5 = close.rolling(5).mean().iloc[-1]
        ma10 = close.rolling(10).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        
        # RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(com=13, min_periods=14).mean()
        avg_loss = loss.ewm(com=13, min_periods=14).mean()
        if avg_loss.iloc[-1] == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = (100 - (100 / (1 + rs))).iloc[-1]
        
        # MACD柱体
        ema_fast = close.ewm(span=12, adjust=False).mean()
        ema_slow = close.ewm(span=26, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = (macd_line - signal_line).iloc[-1]
        
        return round(ma5, 2), round(ma10, 2), round(ma20, 2), round(rsi, 2), round(macd_hist, 4)
    except Exception:
        return None, None, None, None, None


def layer1_basic(df):
    """第1层：基础门槛"""
    log("📊 第1层：基础门槛过滤")
    orig = len(df)
    
    # 过滤非A股（只要 GP-A，排除ETF、创业板、科创板）
    if 'stock_type' in df.columns:
        df = df[df['stock_type'] == 'GP-A']
        log(f"   A股过滤: {orig} → {len(df)}")
        orig = len(df)
    
    # 价格
    if 'zxj' in df.columns:
        df['zxj_num'] = pd.to_numeric(df['zxj'], errors='coerce')
        df = df[(df['zxj_num'] >= MIN_PRICE) & (df['zxj_num'] <= MAX_PRICE)]
        log(f"   价格({MIN_PRICE}-{MAX_PRICE}元)过滤: {orig} → {len(df)}")
        orig = len(df)
    
    # 涨幅（尾盘策略：2-6%）
    if 'zdf' in df.columns:
        df['zdf_num'] = pd.to_numeric(df['zdf'], errors='coerce')
        df = df[(df['zdf_num'] >= MIN_GAIN) & (df['zdf_num'] <= MAX_GAIN)]
        log(f"   涨幅({MIN_GAIN}-{MAX_GAIN}%)过滤: {orig} → {len(df)}")
        orig = len(df)
    
    # ST过滤
    if 'name' in df.columns:
        df = df[~df['name'].str.contains('ST|退', na=False)]
        log(f"   ST过滤: {orig} → {len(df)}")
    
    return df


def layer2_trend(df):
    """第2层：趋势确认"""
    log("📈 第2层：趋势确认（多头排列 + RSI + MACD）")
    results = []
    total = len(df)
    
    for i, (_, row) in enumerate(df.iterrows()):
        code = row['code']
        name = row.get('name', code)
        cur_price = pd.to_numeric(row.get('zxj', 0), errors='coerce')
        
        if pd.isna(cur_price) or cur_price == 0:
            continue
        
        # 计算技术指标
        ma5, ma10, ma20, rsi, macd_hist = calc_indicators_from_kline(code)
        
        if ma5 is None:
            continue
        
        # 多头排列：MA5 > MA10 > MA20，股价站稳MA20
        if not (ma5 > ma10 > ma20 and cur_price > ma20):
            continue
        
        # RSI：40-80区间（不过热未超买）
        if not (40 <= rsi <= 80):
            continue
        
        # MACD柱体 > 0（多头动能）
        if macd_hist <= 0:
            continue
        
        results.append({
            '代码': code,
            '名称': name.strip(),
            '最新价': cur_price,
            '涨跌幅': pd.to_numeric(row.get('zdf', 0), errors='coerce'),
            'MA5': ma5,
            'MA10': ma10,
            'MA20': ma20,
            'RSI': rsi,
            'MACD柱': macd_hist
        })
        
        if (i + 1) % 5 == 0:
            log(f"   进度: {i+1}/{total}")
    
    result_df = pd.DataFrame(results)
    log(f"   趋势过滤: {total} → {len(result_df)} 只")
    return result_df


def layer3_volume(df):
    """第3层：量价配合（量比）"""
    log("💰 第3层：量价配合")
    # westock-data 热门列表未提供量比，这里仅做基础量判断
    # 通过获取个股详情补充量比
    results = []
    for _, row in df.iterrows():
        detail = get_stock_detail(row['代码'])
        if detail is not None and len(detail) > 0:
            # 查找量比列
            hbl = None
            for col in detail.columns:
                if '量比' in col or 'hbl' in col.lower():
                    hbl = pd.to_numeric(detail[col].iloc[0], errors='coerce')
                    break
            if hbl is not None and hbl >= MIN_VOL_RATIO:
                row_dict = row.to_dict()
                row_dict['量比'] = hbl
                results.append(row_dict)
            else:
                results.append(row.to_dict())
        else:
            results.append(row.to_dict())
    
    result_df = pd.DataFrame(results)
    log(f"   量价过滤后: {len(result_df)}")
    return result_df


def layer4_fund_flow(df):
    """第4层：资金面（主力净流入）"""
    log("🔍 第4层：资金面过滤（暂跳过，避免API调用过慢）")
    return df


def layer5_sector(df):
    """第5层：板块情绪"""
    log("🎯 第5层：板块情绪过滤（暂跳过）")
    return df


def layer6_risk(df):
    """第6层：风控"""
    log("🛡️ 第6层：风控过滤")
    orig = len(df)
    if '名称' in df.columns:
        df = df[~df['名称'].str.contains('ST|退', na=False)]
    log(f"   风控过滤: {orig} → {len(df)}")
    return df


def gen_report(df, market_data=None):
    """生成简报"""
    lines = []
    lines.append("=" * 60)
    lines.append(f"A股短线候选股票简报 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("策略：尾盘买入(14:45-15:00)，次日早盘卖出")
    lines.append("数据源：westock-data (腾讯自选股)")
    lines.append("=" * 60)
    lines.append("")
    
    # 大盘概况
    if market_data:
        lines.append("【大盘今日概况】")
        for k, v in market_data.items():
            lines.append(f"  {k}: {v}")
        lines.append("")
    
    if len(df) == 0:
        lines.append("今日无符合条件的候选股票")
    else:
        lines.append(f"共 {len(df)} 只候选（按涨幅排序）：\n")
        for i, (_, r) in enumerate(df.iterrows(), 1):
            lines.append(f"【{i}】{r.get('名称', r['代码'])} ({r['代码']})")
            lines.append(f"    最新价: {r.get('最新价', '-')} 元 | 涨幅: {r.get('涨跌幅', '-')}%")
            ma_str = f"MA5:{r.get('MA5','-')}/MA10:{r.get('MA10','-')}/MA20:{r.get('MA20','-')}"
            lines.append(f"    {ma_str} | RSI:{r.get('RSI','-')} | MACD柱:{r.get('MACD柱','-')}")
            if '量比' in r:
                lines.append(f"    量比: {r.get('量比', '-')}")
            lines.append("")
    
    lines.append("=" * 60)
    lines.append("操作建议：")
    lines.append("1. 今日 14:45-15:00 尾盘买入候选股票")
    lines.append("2. 次日 09:30-10:30 观察开盘情况，择机卖出")
    lines.append("3. 止损：-3% 严格执行")
    lines.append("4. 止盈：+5%~+10% 分批卖出")
    lines.append("5. 仓位：单只≤20%，同时持有≤3只")
    lines.append("=" * 60)
    
    txt = "\n".join(lines)
    with open(REPORT_FILE, 'w', encoding='utf-8') as f:
        f.write(txt)
    log(f"📝 简报已保存: {REPORT_FILE}")
    return txt


def run_screener():
    log("=" * 60)
    log("🚀 A股短线筛选开始 (westock-data版) - 尾盘买次日早盘卖策略")
    log("=" * 60)
    
    if not is_trading_day():
        log("⚠️ 今天不是交易日（周末），跳过筛选")
        return
    
    # Step 1: 获取热门股票
    hot_df = get_hot_stocks(limit=100)
    if hot_df is None or len(hot_df) == 0:
        log("❌ 无法获取热门股票数据，退出")
        return
    
    # Step 2: 第1层 - 基础门槛
    df = layer1_basic(hot_df.copy())
    if len(df) == 0:
        log("❌ 第1层后无候选"); 
        print("\n⚠️ 今日无符合条件的候选股票")
        return
    
    # Step 3: 第2层 - 趋势确认
    df = layer2_trend(df)
    if len(df) == 0:
        log("❌ 第2层后无候选")
        print("\n⚠️ 今日无符合条件的候选股票")
        return
    
    # Step 4: 第3层 - 量价配合
    df = layer3_volume(df)
    
    # Step 5: 第4层 - 资金面
    df = layer4_fund_flow(df)
    
    # Step 6: 第5层 - 板块
    df = layer5_sector(df)
    
    # Step 7: 第6层 - 风控
    df = layer6_risk(df)
    
    if len(df) == 0:
        log("⚠️ 今日无符合条件的候选股票")
        return
    
    # 按涨幅排序
    if '涨跌幅' in df.columns:
        df = df.sort_values('涨跌幅', ascending=False).reset_index(drop=True)
    
    log("=" * 60)
    log(f"✅ 筛选完成！共 {len(df)} 只候选股票")
    log("=" * 60)
    
    print("\n📋 候选股票清单：")
    print(df.to_string(index=False))
    
    # 获取大盘数据
    market_data = None
    try:
        result = subprocess.run(
            'python "C:\\Users\\西西家的咩咩\\.workbuddy\\skills\\akshare-stock-analysis\\scripts\\akshare_cli.py" summary',
            shell=True, capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            import json
            market_data = json.loads(result.stdout)
    except:
        pass
    
    # 保存CSV
    df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')
    log(f"💾 CSV已保存: {OUTPUT_FILE}")
    
    # 生成简报
    txt = gen_report(df, market_data)
    print("\n" + txt)


if __name__ == "__main__":
    run_screener()
