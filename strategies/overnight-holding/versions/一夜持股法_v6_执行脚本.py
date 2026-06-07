#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一夜持股法 v6.0 选股策略执行脚本
"""

import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

def check_date():
    """检查日期，周四跳过，周末跳过"""
    today = datetime.now()
    weekday = today.weekday()  # 0=周一, 1=周二, 2=周三, 3=周四, 4=周五
    
    if weekday == 3:
        return False, "⚠️ 今日周四，跳过选股，避免周末持仓风险"
    elif weekday >= 5:
        return False, "⚠️ 今日周末，A股休市"
    
    return True, f"今天 {today.strftime('%Y-%m-%d')} 星期{weekday}，继续执行选股"

def check_market_environment():
    """检查大盘环境"""
    try:
        # 获取上证指数实时行情
        sh_index = ak.stock_zh_index_spot_em()
        sh_data = sh_index[sh_index['代码'] == '000001']
        
        # 获取创业板指
        cyb_data = sh_index[sh_index['代码'] == '399006']
        
        # 获取沪深300
        hs300_data = sh_index[sh_index['代码'] == '000300']
        
        # 获取市场宽度（上涨家数）
        stock_spot = ak.stock_zh_a_spot_em()
        total_stocks = len(stock_spot)
        rising_stocks = len(stock_spot[stock_spot['涨跌幅'] > 0])
        market_width = rising_stocks / total_stocks if total_stocks > 0 else 0
        
        # 获取历史数据计算MA20
        today = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=60)).strftime('%Y%m%d')
        
        # 上证指数MA20
        sh_hist = ak.stock_zh_index_daily(symbol="sh000001")
        sh_hist['收盘'] = pd.to_numeric(sh_hist['收盘'])
        sh_hist['MA20'] = sh_hist['收盘'].rolling(window=20).mean()
        sh_current = sh_hist.iloc[-1]
        sh_ma20 = sh_hist['MA20'].iloc[-1]
        
        # 创业板指MA20
        cyb_hist = ak.stock_zh_index_daily(symbol="sz399006")
        cyb_hist['收盘'] = pd.to_numeric(cyb_hist['收盘'])
        cyb_hist['MA20'] = cyb_hist['收盘'].rolling(window=20).mean()
        cyb_ma20 = cyb_hist['MA20'].iloc[-1]
        
        # 沪深300MA20
        hs300_hist = ak.stock_zh_index_daily(symbol="sh000300")
        hs300_hist['收盘'] = pd.to_numeric(hs300_hist['收盘'])
        hs300_hist['MA20'] = hs300_hist['收盘'].rolling(window=20).mean()
        hs300_ma20 = hs300_hist['MA20'].iloc[-1]
        
        # 判断条件
        sh_ok = float(sh_data['最新价'].values[0]) > sh_ma20 if len(sh_data) > 0 else False
        cyb_ok = float(cyb_data['最新价'].values[0]) > cyb_ma20 if len(cyb_data) > 0 else False
        hs300_ok = float(hs300_data['最新价'].values[0]) > hs300_ma20 if len(hs300_data) > 0 else False
        width_ok = market_width > 0.9
        
        result = {
            'sh_index': float(sh_data['最新价'].values[0]) if len(sh_data) > 0 else 0,
            'sh_ma20': sh_ma20,
            'sh_ok': sh_ok,
            'cyb_index': float(cyb_data['最新价'].values[0]) if len(cyb_data) > 0 else 0,
            'cyb_ma20': cyb_ma20,
            'cyb_ok': cyb_ok,
            'hs300_index': float(hs300_data['最新价'].values[0]) if len(hs300_data) > 0 else 0,
            'hs300_ma20': hs300_ma20,
            'hs300_ok': hs300_ok,
            'market_width': market_width,
            'width_ok': width_ok,
            'all_ok': sh_ok and cyb_ok and hs300_ok and width_ok
        }
        
        return result
        
    except Exception as e:
        print(f"大盘环境检查失败: {e}")
        return None

def scan_hot_sectors():
    """扫描热点板块"""
    try:
        # 获取行业板块行情
        sector_data = ak.stock_board_industry_name_em()
        
        # 筛选涨幅>3%的板块
        hot_sectors = sector_data[sector_data['涨跌幅'] > 3].sort_values('涨跌幅', ascending=False)
        
        result = []
        for _, row in hot_sectors.iterrows():
            sector_name = row['板块名称']
            sector_change = row['涨跌幅']
            
            # 获取板块内涨停家数
            try:
                sector_stocks = ak.stock_board_industry_cons_em(symbol=sector_name)
                limit_up_count = len(sector_stocks[sector_stocks['涨跌幅'] >= 9.9])
                
                if limit_up_count >= 3:  # 优先选择涨停数≥3的板块
                    result.append({
                        'name': sector_name,
                        'change': sector_change,
                        'limit_up': limit_up_count
                    })
            except:
                pass
        
        return result[:5]  # 返回前5个热点板块
        
    except Exception as e:
        print(f"板块扫描失败: {e}")
        return []

def main():
    """主函数"""
    print("=" * 60)
    print("一夜持股法 v6.0 选股策略执行")
    print("=" * 60)
    
    # 1. 日期检查
    print("\n【步骤1】日期检查...")
    should_continue, message = check_date()
    print(message)
    if not should_continue:
        return message
    
    # 2. 大盘环境检查
    print("\n【步骤2】大盘环境检查...")
    env_result = check_market_environment()
    
    if env_result is None:
        return "⚠️ 大盘环境检查失败，请检查网络连接"
    
    print(f"上证指数: {env_result['sh_index']:.2f} vs MA20 {env_result['sh_ma20']:.2f} {'✅' if env_result['sh_ok'] else '❌'}")
    print(f"创业板指: {env_result['cyb_index']:.2f} vs MA20 {env_result['cyb_ma20']:.2f} {'✅' if env_result['cyb_ok'] else '❌'}")
    print(f"沪深300: {env_result['hs300_index']:.2f} vs MA20 {env_result['hs300_ma20']:.2f} {'✅' if env_result['hs300_ok'] else '❌'}")
    print(f"市场宽度: {env_result['market_width']:.2%} {'✅' if env_result['width_ok'] else '❌'}")
    
    if not env_result['all_ok']:
        return "⚠️ 大盘环境不满足条件，今日暂停选股"
    
    # 3. 板块扫描
    print("\n【步骤3】板块扫描...")
    hot_sectors = scan_hot_sectors()
    if hot_sectors:
        print("热点板块:")
        for sector in hot_sectors:
            print(f"  {sector['name']} (+{sector['change']:.2f}%) 涨停{sector['limit_up']}家")
    else:
        print("未找到符合条件的热点板块")
    
    print("\n" + "=" * 60)
    print("由于akshare接口限制，完整的股票筛选需要更多数据处理时间")
    print("建议：使用已安装的 westock-data 技能获取更详细的数据")
    print("=" * 60)
    
    return "大盘环境检查通过，但由于数据获取复杂性，建议使用 westock-data 技能完成后续选股"

if __name__ == "__main__":
    result = main()
    print(f"\n执行结果: {result}")
