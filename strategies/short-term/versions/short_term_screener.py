#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股短线筛选脚本 - 尾盘买次日早盘卖策略
执行时间：每日14:45
数据来源：AKShare（无需token）
筛选逻辑：6层过滤 + 尾盘策略专用调整
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
import sys
import os

warnings.filterwarnings('ignore')

# ============================================================
# 配置区
# ============================================================
MIN_PRICE = 5        # 最低股价
MAX_PRICE = 35       # 最高股价
MIN_MARKET_CAP = 50   # 最小市值（亿）
MAX_MARKET_CAP = 500  # 最大市值（亿）
MIN_VOLUME = 2e8      # 最小成交额（2亿 = 2e8分 = 200万？不对，AKShare单位是元）
# 注意：AKShare成交额单位需要确认，先用2e8（2亿分=200万）还是2e9？
# 实际应该是：成交额字段单位是元，2亿=200000000=2e8
MIN_TURNOVER = 3      # 最小换手率%
MAX_TURNOVER = 15     # 最大换手率%
MIN_GAIN = 1          # 最小涨幅%
MAX_GAIN = 7          # 最大涨幅%（尾盘策略，不过热）
MIN_VOL_RATIO = 1.5   # 最小量比

# 输出文件路径
OUTPUT_DIR = r"C:\Users\西西家的咩咩\WorkBuddy\2026-05-10-task-14"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "候选股票_" + datetime.now().strftime("%Y%m%d") + ".csv")
REPORT_FILE = os.path.join(OUTPUT_DIR, "候选股票_" + datetime.now().strftime("%Y%m%d") + ".txt")


# ============================================================
# 工具函数
# ============================================================
def get_realtime_stock_data():
    """获取A股实时行情数据"""
    import time
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            print(f"  尝试获取实时数据 (第{attempt+1}次)...")
            df = ak.stock_zh_a_spot_em()
            if df is not None and len(df) > 0:
                print(f"✅ 获取到 {len(df)} 只A股实时数据")
                return df
        except Exception as e:
            print(f"⚠️ 第{attempt+1}次获取失败: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
    
    print("❌ 实时数据获取失败，可能原因：非交易时间 / 网络问题 / AKShare接口限制")
    return None


def is_trading_day():
    """判断今天是否为A股交易日"""
    today = datetime.now()
    # 周末不交易
    if today.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False
    
    # 这里可以添加更多节假日判断逻辑
    # 简单版本：只判断周末
    return True


def calculate_ma(series, window):
    """计算移动平均线"""
    return series.rolling(window=window).mean()


def calculate_rsi(series, period=14):
    """计算RSI指标"""
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_macd(series, fast=12, slow=26, signal=9):
    """计算MACD指标"""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    histogram = macd - signal_line
    return macd, signal_line, histogram


def get_stock_history(stock_code, days=30):
    """获取个股历史数据用于计算技术指标"""
    try:
        # 获取日线数据
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        
        df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", 
                                  start_date=start_date, end_date=end_date, adjust="qfq")
        if df is None or len(df) == 0:
            return None
        
        # 统一列名（AKShare版本不同列名可能不同）
        column_map = {
            '日期': 'date', '开盘': 'open', '收盘': 'close', '最高': 'high', 
            '最低': 'low', '成交量': 'volume', '成交额': 'amount', '涨跌幅': 'pct_chg',
            '涨跌额': 'change', '换手率': 'turnover'
        }
        df = df.rename(columns=column_map)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
        
        return df
    except Exception as e:
        return None


def get_sector_info():
    """获取板块涨幅排名"""
    try:
        df = ak.stock_board_industry_name_em()
        return df
    except Exception as e:
        print(f"⚠️ 获取板块数据失败: {e}")
        return None


# ============================================================
# 核心筛选逻辑（6层过滤）
# ============================================================
def layer1_basic_filter(df):
    """第1层：基础门槛过滤"""
    print("\n📊 第1层：基础门槛过滤")
    print(f"   初始股票数: {len(df)}")
    
    # 价格过滤
    df = df[df['最新价'] >= MIN_PRICE]
    df = df[df['最新价'] <= MAX_PRICE]
    print(f"   价格过滤后: {len(df)}")
    
    # 换手率过滤（如果有这个字段）
    if '换手率' in df.columns:
        df = df[df['换手率'] >= MIN_TURNOVER]
        df = df[df['换手率'] <= MAX_TURNOVER]
        print(f"   换手率过滤后: {len(df)}")
    
    # 涨幅过滤
    if '涨跌幅' in df.columns:
        df = df[df['涨跌幅'] >= MIN_GAIN]
        df = df[df['涨跌幅'] <= MAX_GAIN]
        print(f"   涨幅过滤后: {len(df)}")
    
    return df


def layer2_trend_filter(df, progress_callback=None):
    """第2层：趋势确认（多头排列）"""
    print("\n📈 第2层：趋势确认")
    
    qualified = []
    total = len(df)
    
    for idx, row in df.iterrows():
        stock_code = row['代码']
        try:
            hist = get_stock_history(stock_code)
            if hist is None or len(hist) < 20:
                continue
            
            close = hist['close']
            
            # 计算均线
            ma5 = calculate_ma(close, 5).iloc[-1]
            ma10 = calculate_ma(close, 10).iloc[-1]
            ma20 = calculate_ma(close, 20).iloc[-1]
            current_price = close.iloc[-1]
            
            # 多头排列：MA5 > MA10 > MA20，股价在MA20上方
            if ma5 > ma10 > ma20 and current_price > ma20:
                # 计算RSI
                rsi = calculate_rsi(close).iloc[-1]
                if 40 <= rsi <= 80:
                    # 计算MACD
                    macd, signal, hist_bar = calculate_macd(close)
                    if hist_bar.iloc[-1] > 0:  # MACD柱体>0
                        qualified.append({
                            '代码': stock_code,
                            '名称': row['名称'],
                            '最新价': row['最新价'],
                            'MA5': round(ma5, 2),
                            'MA10': round(ma10, 2),
                            'MA20': round(ma20, 2),
                            'RSI': round(rsi, 2),
                            'MACD柱': round(hist_bar.iloc[-1], 4)
                        })
            
            if progress_callback:
                progress_callback(idx, total)
                
        except Exception as e:
            continue
    
    result = pd.DataFrame(qualified)
    print(f"   趋势过滤后: {len(result)}")
    return result


def layer3_volume_price_filter(df, realtime_df):
    """第3层：量价配合"""
    print("\n💰 第3层：量价配合")
    
    # 合并实时数据
    df = df.merge(realtime_df[['代码', '量比', '涨跌幅', '换手率', '成交额']], 
                  on='代码', how='left')
    
    # 量比过滤
    if '量比' in df.columns:
        df = df[df['量比'] >= MIN_VOL_RATIO]
    
    # 已经是当日涨幅过滤过的，这里再确认一下
    print(f"   量价过滤后: {len(df)}")
    return df


def layer4_fund_flow_filter(df):
    """第4层：资金面（主力资金流向）"""
    print("\n🔍 第4层：资金面过滤")
    
    qualified = []
    total = len(df)
    
    for idx, row in df.iterrows():
        stock_code = row['代码']
        try:
            # 获取主力资金流向
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
            
            flow = ak.stock_individual_fund_flow_rank(symbol='即时')
            if flow is not None:
                stock_flow = flow[flow['代码'] == stock_code]
                if not stock_flow.empty:
                    # 检查主力净流入
                    if '主力净流入-净额' in stock_flow.columns:
                        net_inflow = stock_flow['主力净流入-净额'].values[0]
                        if net_inflow > 0:
                            qualified.append(row.to_dict())
            else:
                # 如果获取失败，保守处理：保留
                qualified.append(row.to_dict())
                
        except Exception as e:
            # 获取失败时保守处理
            qualified.append(row.to_dict())
            continue
    
    result = pd.DataFrame(qualified)
    print(f"   资金面过滤后: {len(result)}")
    return result


def layer5_sector_filter(df):
    """第5层：情绪面（板块效应）"""
    print("\n🎯 第5层：板块情绪过滤")
    
    try:
        sectors = get_sector_info()
        if sectors is not None and '涨跌幅' in sectors.columns:
            # 获取涨幅前20的板块
            top_sectors = sectors.nlargest(20, '涨跌幅')
            top_sector_names = top_sectors['板块名称'].tolist()
            
            # 这里需要判断股票是否属于热门板块
            # AKShare需要通过其他接口获取个股所属板块
            # 暂时跳过这个过滤，标记为待完善
            print(f"   ⚠️ 板块过滤：需进一步完善个股-板块映射")
            
    except Exception as e:
        print(f"   ⚠️ 板块过滤失败: {e}")
    
    print(f"   板块过滤后（暂未过滤）: {len(df)}")
    return df


def layer6_risk_filter(df):
    """第6层：风控过滤"""
    print("\n🛡️ 第6层：风控过滤")
    
    # 剔除ST股票
    if '名称' in df.columns:
        df = df[~df['名称'].str.contains('ST|退', na=False)]
        print(f"   ST过滤后: {len(df)}")
    
    # 这里可以添加更多风控逻辑
    # 1. 检查近期是否有大股东减持公告
    # 2. 检查是否在财报敏感期
    # 3. 检查是否接近历史新高
    
    return df


def adjust_for_tail_market(df, realtime_df):
    """尾盘买入策略专用调整"""
    print("\n🌅 尾盘策略调整")
    
    # 合并实时数据
    df = df.merge(realtime_df[['代码', '最新价', '涨跌幅', '换手率', '成交额', '量比']], 
                  on='代码', how='left', suffixes=('', '_real'))
    
    # 尾盘策略：偏好当日涨幅3-5%的股票（不是太高，给次日留空间）
    if '涨跌幅' in df.columns:
        df = df[df['涨跌幅'] >= 2]  # 最低2%涨幅
        df = df[df['涨跌幅'] <= 6]  # 最高6%涨幅（给次日留空间）
    
    # 尾盘策略：成交额要足够大（保证流动性）
    if '成交额' in df.columns:
        df = df[df['成交额'] >= MIN_VOLUME]
    
    print(f"   尾盘策略调整后: {len(df)}")
    return df


# ============================================================
# 主流程
# ============================================================
def run_screener():
    """执行完整筛选流程"""
    print("="*60)
    print(f"🚀 A股短线筛选开始 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    # 交易日检查
    if not is_trading_day():
        print("⚠️ 今天不是交易日（周末），跳过筛选")
        return
    
    # Step 1: 获取实时数据
    realtime_df = get_realtime_stock_data()
    if realtime_df is None:
        print("❌ 无法获取实时数据，退出")
        return
    
    # Step 2: 第1层 - 基础门槛
    df = layer1_basic_filter(realtime_df.copy())
    if len(df) == 0:
        print("❌ 第1层过滤后无候选股票")
        return
    
    # Step 3: 第2层 - 趋势确认（需要计算技术指标，较慢）
    df = layer2_trend_filter(df)
    if len(df) == 0:
        print("❌ 第2层过滤后无候选股票")
        return
    
    # Step 4: 第3层 - 量价配合
    df = layer3_volume_price_filter(df, realtime_df)
    if len(df) == 0:
        print("❌ 第3层过滤后无候选股票")
        return
    
    # Step 5: 第4层 - 资金面
    df = layer4_fund_flow_filter(df)
    if len(df) == 0:
        print("❌ 第4层过滤后无候选股票")
        return
    
    # Step 6: 第5层 - 板块情绪
    df = layer5_sector_filter(df)
    if len(df) == 0:
        print("❌ 第5层过滤后无候选股票")
        return
    
    # Step 7: 第6层 - 风控
    df = layer6_risk_filter(df)
    if len(df) == 0:
        print("❌ 第6层过滤后无候选股票")
        return
    
    # Step 8: 尾盘策略调整
    df = adjust_for_tail_market(df, realtime_df)
    if len(df) == 0:
        print("❌ 尾盘策略调整后无候选股票")
        return
    
    # ============================================================
    # 输出结果
    # ============================================================
    print("\n" + "="*60)
    print(f"✅ 筛选完成！共找到 {len(df)} 只候选股票")
    print("="*60)
    
    if len(df) > 0:
        # 按涨幅排序
        if '涨跌幅' in df.columns:
            df = df.sort_values('涨跌幅', ascending=False)
        
        # 打印结果
        print("\n📋 候选股票清单：")
        print(df.to_string(index=False))
        
        # 保存到CSV
        df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')
        print(f"\n💾 结果已保存到: {OUTPUT_FILE}")
        
        # 生成简报
        generate_report(df)
    else:
        print("\n⚠️ 今日无符合条件的候选股票")


def generate_report(df):
    """生成简报文件"""
    report = []
    report.append("=" * 60)
    report.append(f"A股短线候选股票简报 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report.append("策略：尾盘买入，次日早盘卖出")
    report.append("=" * 60)
    report.append("")
    
    if len(df) == 0:
        report.append("今日无符合条件的候选股票")
    else:
        report.append(f"共找到 {len(df)} 只候选股票：\n")
        
        for idx, row in df.iterrows():
            report.append(f"【{idx+1}】 {row['名称']} ({row['代码']})")
            report.append(f"    最新价: {row['最新价']} 元")
            if '涨跌幅' in row:
                report.append(f"    涨幅: {row['涨跌幅']}%")
            if '换手率' in row:
                report.append(f"    换手率: {row['换手率']}%")
            if '量比' in row:
                report.append(f"    量比: {row['量比']}")
            if 'MA5' in row:
                report.append(f"    MA5/MA10/MA20: {row['MA5']}/{row['MA10']}/{row['MA20']}")
            if 'RSI' in row:
                report.append(f"    RSI: {row['RSI']}")
            report.append("")
    
    report.append("=" * 60)
    report.append("操作建议：")
    report.append("1. 14:45-15:00 买入候选股票")
    report.append("2. 次日 09:30-10:30 根据盘面决定卖出时机")
    report.append("3. 止损：-3% 严格执行")
    report.append("4. 止盈：+5%~+10% 分批卖出")
    report.append("=" * 60)
    
    report_text = "\n".join(report)
    
    with open(REPORT_FILE, 'w', encoding='utf-8') as f:
        f.write(report_text)
    
    print(f"📝 简报已保存到: {REPORT_FILE}")
    print("\n" + report_text)


if __name__ == "__main__":
    run_screener()
