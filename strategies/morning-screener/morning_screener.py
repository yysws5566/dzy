#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股早盘筛选脚本 - 早盘买入策略
执行时间：每日09:25（集合竞价结束后）
数据来源：AKShare（无需token）
筛选逻辑：6层过滤，无尾盘策略限制
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
import sys
import os
import time

warnings.filterwarnings('ignore')

# ============================================================
# 配置区（早盘版 - 比尾盘版更激进）
# ============================================================
MIN_PRICE = 5        # 最低股价
MAX_PRICE = 35       # 最高股价
MIN_TURNOVER = 3      # 最小换手率%
MAX_TURNOVER = 15     # 最大换手率%
MIN_GAIN = 0          # 早盘版：允许平开或小涨
MAX_GAIN = 8          # 早盘版：放宽至8%（买入后有全天时间发酵）
MIN_VOL_RATIO = 1.5   # 最小量比
MIN_VOLUME = 1.5e8     # 最小成交额（1.5亿，早盘流动性要求稍低）

# 输出文件路径
OUTPUT_DIR = r"C:\Users\西西家的咩咩\WorkBuddy\2026-05-10-task-14"
today_str = datetime.now().strftime("%Y%m%d")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, f"早盘候选_{today_str}.csv")
REPORT_FILE = os.path.join(OUTPUT_DIR, f"早盘候选_{today_str}.txt")


# ============================================================
# 工具函数
# ============================================================
def log(msg):
    """带时间戳的日志"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def is_trading_day():
    """判断今天是否为A股交易日"""
    today = datetime.now()
    if today.weekday() >= 5:
        return False
    return True


def get_realtime_stock_data(max_retries=3):
    """获取A股实时行情数据"""
    for attempt in range(max_retries):
        try:
            log(f"获取实时行情（第{attempt+1}次）...")
            df = ak.stock_zh_a_spot_em()
            if df is not None and len(df) > 0:
                log(f"✅ 获取到 {len(df)} 只股票实时数据")
                return df
        except Exception as e:
            log(f"⚠️ 第{attempt+1}次获取失败: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
    log("❌ 实时数据获取失败")
    return None


def calc_ma(close, windows=[5, 10, 20]):
    """批量计算均线"""
    result = {}
    for w in windows:
        result[f'MA{w}'] = close.rolling(window=w).mean()
    return result


def calc_rsi(close, period=14):
    """计算RSI"""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period-1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period-1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_macd_hist(close, fast=12, slow=26, signal=9):
    """计算MACD柱体"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd - signal_line


def get_stock_hist(stock_code, days=60):
    """获取个股历史日线（用于计算技术指标）"""
    try:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(
            symbol=stock_code,
            period="daily",
            start_date=start,
            end_date=end,
            adjust="qfq"
        )
        if df is None or len(df) < 20:
            return None
        # 统一列名
        col_map = {
            '日期': 'date', '收盘': 'close', '开盘': 'open',
            '最高': 'high', '最低': 'low', '成交量': 'volume', '成交额': 'amount'
        }
        df = df.rename(columns=col_map)
        df['date'] = pd.to_datetime(df['date'])
        return df.sort_values('date').reset_index(drop=True)
    except Exception:
        return None


# ============================================================
# 6层过滤
# ============================================================
def layer1_basic(df):
    """第1层：基础门槛"""
    log("📊 第1层：基础门槛过滤")
    orig = len(df)
    
    # 价格
    df = df[(df['最新价'] >= MIN_PRICE) & (df['最新价'] <= MAX_PRICE)]
    # 涨幅（早盘版允许平开）
    if '涨跌幅' in df.columns:
        df = df[(df['涨跌幅'] >= MIN_GAIN) & (df['涨跌幅'] <= MAX_GAIN)]
    # 换手率
    if '换手率' in df.columns:
        df = df[(df['换手率'] >= MIN_TURNOVER) & (df['换手率'] <= MAX_TURNOVER)]
    # ST过滤
    if '名称' in df.columns:
        df = df[~df['名称'].str.contains('ST|退', na=False)]
    
    log(f"   过滤: {orig} → {len(df)}")
    return df


def layer2_trend(df):
    """第2层：趋势确认（多头排列+RSI+MACD）"""
    log("📈 第2层：趋势确认（需计算技术指标，较慢）")
    results = []
    total = len(df)
    
    for i, (_, row) in enumerate(df.iterrows()):
        code = str(row['代码']).zfill(6)
        try:
            hist = get_stock_hist(code)
            if hist is None or len(hist) < 20:
                continue
            
            close = hist['close']
            ma = calc_ma(close)
            ma5, ma10, ma20 = ma['MA5'].iloc[-1], ma['MA10'].iloc[-1], ma['MA20'].iloc[-1]
            cur = close.iloc[-1]
            
            # 多头排列
            if not (ma5 > ma10 > ma20 and cur > ma20):
                continue
            
            rsi = calc_rsi(close).iloc[-1]
            if not (40 <= rsi <= 80):
                continue
            
            macd_hist = calc_macd_hist(close).iloc[-1]
            if macd_hist <= 0:
                continue
            
            results.append({
                '代码': code,
                '名称': row['名称'],
                '最新价': row['最新价'],
                '涨跌幅': row.get('涨跌幅', np.nan),
                '换手率': row.get('换手率', np.nan),
                '量比': row.get('量比', np.nan),
                '成交额': row.get('成交额', np.nan),
                'MA5': round(ma5, 2),
                'MA10': round(ma10, 2),
                'MA20': round(ma20, 2),
                'RSI': round(rsi, 2),
                'MACD柱': round(macd_hist, 4)
            })
        except Exception:
            continue
        
        # 进度提示（每20只输出一次）
        if (i + 1) % 20 == 0:
            log(f"   进度: {i+1}/{total}")
    
    result_df = pd.DataFrame(results)
    log(f"   趋势过滤: {total} → {len(result_df)}")
    return result_df


def layer3_volume(df, realtime_df):
    """第3层：量价配合"""
    log("💰 第3层：量价配合")
    df = df.merge(
        realtime_df[['代码', '量比', '成交额']].rename(columns={'成交额': '成交额_real'}),
        on='代码', how='left'
    )
    if '量比' in df.columns:
        df = df[df['量比'] >= MIN_VOL_RATIO]
    if '成交额_real' in df.columns:
        df = df[df['成交额_real'] >= MIN_VOLUME]
    log(f"   量价过滤后: {len(df)}")
    return df


def layer4_fund_flow(df):
    """第4层：主力资金净流入"""
    log("🔍 第4层：资金面过滤")
    # 由于AKShare主力资金接口可能限流，这里做轻量处理：保留所有通过前3层的股票
    # 完整版可通过 ak.stock_individual_fund_flow_rank() 补充
    log(f"   资金面过滤后（暂跳过API调用）: {len(df)}")
    return df


def layer5_sector(df):
    """第5层：热门板块"""
    log("🎯 第5层：板块情绪过滤")
    log(f"   板块过滤（暂跳过）: {len(df)}")
    return df


def layer6_risk(df):
    """第6层：风控"""
    log("🛡️ 第6层：风控过滤")
    if '名称' in df.columns:
        df = df[~df['名称'].str.contains('ST|退', na=False)]
    log(f"   风控过滤后: {len(df)}")
    return df


# ============================================================
# 主流程
# ============================================================
def run_morning_screener():
    log("=" * 60)
    log("🚀 A股早盘筛选开始")
    log("=" * 60)
    
    if not is_trading_day():
        log("⚠️ 今天不是交易日（周末），跳过")
        return
    
    # Step 1: 获取实时数据
    rt = get_realtime_stock_data()
    if rt is None:
        log("❌ 无法获取实时数据，退出")
        return
    
    # Step 2: 第1层
    df = layer1_basic(rt.copy())
    if len(df) == 0:
        log("❌ 第1层后无候选"); return
    
    # Step 3: 第2层（最慢，计算技术指标）
    df = layer2_trend(df)
    if len(df) == 0:
        log("❌ 第2层后无候选"); return
    
    # Step 4: 第3层
    df = layer3_volume(df, rt)
    if len(df) == 0:
        log("❌ 第3层后无候选"); return
    
    # Step 5: 第4层
    df = layer4_fund_flow(df)
    if len(df) == 0:
        log("❌ 第4层后无候选"); return
    
    # Step 6: 第5层
    df = layer5_sector(df)
    if len(df) == 0:
        log("❌ 第5层后无候选"); return
    
    # Step 7: 第6层
    df = layer6_risk(df)
    if len(df) == 0:
        log("❌ 第6层后无候选"); return
    
    # 按涨幅排序
    if '涨跌幅' in df.columns:
        df = df.sort_values('涨跌幅', ascending=False).reset_index(drop=True)
    
    # ============================================================
    # 输出
    # ============================================================
    log("=" * 60)
    log(f"✅ 筛选完成！共 {len(df)} 只候选股票")
    log("=" * 60)
    
    if len(df) > 0:
        print("\n📋 早盘候选股票清单：")
        print(df.to_string(index=False))
        
        # 保存CSV
        df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')
        log(f"💾 CSV已保存: {OUTPUT_FILE}")
        
        # 生成简报
        gen_report(df)
    else:
        log("⚠️ 今日无符合条件的候选股票")


def gen_report(df):
    """生成早盘简报"""
    lines = []
    lines.append("=" * 60)
    lines.append(f"A股早盘候选股票简报 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("策略：早盘买入（09:30-10:30），择机卖出")
    lines.append("=" * 60)
    lines.append("")
    
    if len(df) == 0:
        lines.append("今日无符合条件的候选股票")
    else:
        lines.append(f"共 {len(df)} 只候选：\n")
        for i, (_, r) in enumerate(df.iterrows(), 1):
            lines.append(f"【{i}】 {r['名称']} ({r['代码']})")
            lines.append(f"    最新价: {r['最新价']} 元 | 涨幅: {r.get('涨跌幅','-')}%")
            lines.append(f"    换手: {r.get('换手率','-')}% | 量比: {r.get('量比','-')}")
            lines.append(f"    MA5/10/20: {r.get('MA5','-')}/{r.get('MA10','-')}/{r.get('MA20','-')} | RSI: {r.get('RSI','-')}")
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
    print("\n" + txt)


if __name__ == "__main__":
    run_morning_screener()
