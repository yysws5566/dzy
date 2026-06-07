#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
计算指数MA20并判断大盘环境
"""

import subprocess
import pandas as pd
import io

def get_index_data(index_code, limit=60):
    """获取指数K线数据"""
    cmd = f'npx -y westock-data-clawhub@1.0.4 kline {index_code} --period day --limit {limit}'
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    
    # 解析输出（Markdown表格格式）
    lines = result.stdout.strip().split('\n')
    data_lines = [line for line in lines if line.startswith('|') and '---' not in line]
    
    data = []
    for line in data_lines:
        parts = line.split('|')
        if len(parts) >= 9:
            try:
                data.append({
                    'date': parts[1].strip(),
                    'open': float(parts[2].strip()),
                    'last': float(parts[3].strip()),
                    'high': float(parts[4].strip()),
                    'low': float(parts[5].strip()),
                    'volume': int(parts[6].strip()),
                    'amount': int(parts[7].strip()),
                    'exchange': float(parts[8].strip())
                })
            except:
                pass
    
    df = pd.DataFrame(data)
    if not df.empty:
        df = df.sort_values('date')  # 按日期升序排列
    return df

def calculate_ma20(df):
    """计算MA20"""
    df['MA20'] = df['last'].rolling(window=20).mean()
    return df

def main():
    print("=" * 60)
    print("大盘环境检查 - 计算MA20")
    print("=" * 60)
    
    # 获取指数数据
    print("\n【上证指数】")
    sh_df = get_index_data('sh000001')
    if not sh_df.empty:
        sh_df = calculate_ma20(sh_df)
        sh_current = sh_df.iloc[-1]
        sh_ma20 = sh_df['MA20'].iloc[-1]
        sh_ok = sh_current['last'] > sh_ma20
        print(f"最新收盘: {sh_current['last']:.2f}")
        print(f"MA20: {sh_ma20:.2f}")
        print(f"判断: {'✅ 上证 > MA20' if sh_ok else '❌ 上证 < MA20'}")
    else:
        print("获取数据失败")
        sh_ok = False
    
    print("\n【创业板指】")
    cyb_df = get_index_data('sz399006')
    if not cyb_df.empty:
        cyb_df = calculate_ma20(cyb_df)
        cyb_current = cyb_df.iloc[-1]
        cyb_ma20 = cyb_df['MA20'].iloc[-1]
        cyb_ok = cyb_current['last'] > cyb_ma20
        print(f"最新收盘: {cyb_current['last']:.2f}")
        print(f"MA20: {cyb_ma20:.2f}")
        print(f"判断: {'✅ 创业板 > MA20' if cyb_ok else '❌ 创业板 < MA20'}")
    else:
        print("获取数据失败")
        cyb_ok = False
    
    print("\n【沪深300】")
    hs300_df = get_index_data('sh000300')
    if not hs300_df.empty:
        hs300_df = calculate_ma20(hs300_df)
        hs300_current = hs300_df.iloc[-1]
        hs300_ma20 = hs300_df['MA20'].iloc[-1]
        hs300_ok = hs300_current['last'] > hs300_ma20
        print(f"最新收盘: {hs300_current['last']:.2f}")
        print(f"MA20: {hs300_ma20:.2f}")
        print(f"判断: {'✅ 沪深300 > MA20' if hs300_ok else '❌ 沪深300 < MA20'}")
    else:
        print("获取数据失败")
        hs300_ok = False
    
    # 市场宽度（简化版）
    print("\n【市场宽度】")
    print("⚠️  市场宽度数据获取较复杂，暂时假设为True")
    print("建议：需要获取全市场股票涨跌情况来计算市场宽度")
    width_ok = True  # 暂时假设为True
    
    # 总判断
    print("\n" + "=" * 60)
    print("大盘环境总判断:")
    all_ok = sh_ok and cyb_ok and hs300_ok and width_ok
    print(f"上证指数: {'✅' if sh_ok else '❌'}")
    print(f"创业板指: {'✅' if cyb_ok else '❌'}")
    print(f"沪深300: {'✅' if hs300_ok else '❌'}")
    print(f"市场宽度: {'✅ 假设通过' if width_ok else '❌'}")
    print(f"\n最终结果: {'✅ 大盘环境良好，可以继续选股' if all_ok else '❌ 大盘环境不满足条件，暂停选股'}")
    print("=" * 60)
    
    return all_ok

if __name__ == "__main__":
    main()
