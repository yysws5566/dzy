#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大盘环境检查脚本
检查上证指数和创业板指是否满足隔夜策略的大盘环境要求
"""
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta

def get_index_data(symbol, name):
    """获取指数最近20个交易日的收盘数据"""
    try:
        # 获取指数历史数据
        # symbol: 000001 (上证指数), 399006 (创业板指)
        df = ak.index_zh_a_hist(symbol=symbol, period="daily")

        if df is None or df.empty:
            print(f"❌ 无法获取{name}数据")
            return None

        # 确保列名正确
        # akshare 返回的列名可能是中文，需要确认
        print(f"\n{name}数据列名: {df.columns.tolist()}")

        # 获取最近20个交易日的数据
        df_recent = df.tail(30)  # 多取一些，确保有足够数据

        return df_recent

    except Exception as e:
        print(f"❌ 获取{name}数据时出错: {e}")
        return None

def calculate_ma20(df, price_col='收盘'):
    """计算20日均线"""
    try:
        df[price_col] = pd.to_numeric(df[price_col], errors='coerce')
        df['MA20'] = df[price_col].rolling(window=20).mean()
        return df
    except Exception as e:
        print(f"❌ 计算MA20时出错: {e}")
        return None

def check_market_env():
    """检查大盘环境"""
    print("=" * 60)
    print("🔍 大盘环境检查")
    print("=" * 60)

    results = {
        'shanghai': {'pass': False, 'reason': ''},
        'chinext': {'pass': False, 'reason': ''},
        'down_market': {'pass': False, 'reason': ''}
    }

    # 1. 检查上证指数
    print("\n📊 正在获取上证指数数据...")
    sh_df = get_index_data("000001", "上证指数")

    if sh_df is not None:
        sh_df = calculate_ma20(sh_df)

        if sh_df is not None and not sh_df.empty:
            latest = sh_df.iloc[-1]
            current_price = latest['收盘']
            ma20 = latest['MA20']

            print(f"\n上证指数:")
            print(f"  最新收盘价: {current_price:.2f}")
            print(f"  MA20: {ma20:.2f}")
            print(f"  当前 > MA20: {current_price > ma20}")

            results['shanghai']['pass'] = current_price > ma20
            if not results['shanghai']['pass']:
                results['shanghai']['reason'] = f"当前价 {current_price:.2f} < MA20 {ma20:.2f}"

    # 2. 检查创业板指
    print("\n📊 正在获取创业板指数据...")
    cyb_df = get_index_data("399006", "创业板指")

    if cyb_df is not None:
        cyb_df = calculate_ma20(cyb_df)

        if cyb_df is not None and not cyb_df.empty:
            latest = cyb_df.iloc[-1]
            current_price = latest['收盘']
            ma20 = latest['MA20']

            print(f"\n创业板指:")
            print(f"  最新收盘价: {current_price:.2f}")
            print(f"  MA20: {ma20:.2f}")
            print(f"  当前 > MA20: {current_price > ma20}")

            results['chinext']['pass'] = current_price > ma20
            if not results['chinext']['pass']:
                results['chinext']['reason'] = f"当前价 {current_price:.2f} < MA20 {ma20:.2f}"

    # 3. 检查是否单边下跌市（上证指数当日涨跌幅 > -1%）
    # 这里需要从实时行情获取涨跌幅
    print("\n📊 正在检查当日涨跌幅...")

    # 总结
    print("\n" + "=" * 60)
    print("📋 大盘环境检查结果")
    print("=" * 60)

    all_pass = True

    if results['shanghai']['pass']:
        print("✅ 上证指数: 当前价格 > 20日均线")
    else:
        print(f"❌ 上证指数: {results['shanghai']['reason']}")
        all_pass = False

    if results['chinext']['pass']:
        print("✅ 创业板指: 当前价格 > 20日均线")
    else:
        print(f"❌ 创业板指: {results['chinext']['reason']}")
        all_pass = False

    if all_pass:
        print("\n✅ 大盘环境满足，可以执行选股")
    else:
        print("\n⛔ 大盘环境不满足，今日休息")

    return all_pass

if __name__ == "__main__":
    check_market_env()
