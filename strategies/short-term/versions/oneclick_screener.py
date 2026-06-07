# -*- coding: utf-8 -*-
"""
一夜持股法尾盘选股 - 一键运行版
"""
import sys
import time
import akshare as ak
import pandas as pd
from datetime import datetime


def get_market_status():
    """获取大盘状态"""
    try:
        sh = ak.stock_zh_index_daily_em(symbol="sh000001")
        sh['MA5'] = sh['close'].rolling(5).mean()
        sh['MA10'] = sh['close'].rolling(10).mean()
        sh['MA20'] = sh['close'].rolling(20).mean()
        last = sh.iloc[-1]
        return last
    except Exception as e:
        print(f"获取大盘数据失败: {e}")
        return None


def screen_stocks():
    """全市场初筛"""
    try:
        df = ak.stock_zh_a_spot_em()
        
        df = df[~df['名称'].str.contains('ST|退|N|C|U', na=False)]
        
        mask = (
            (df['最新价'] >= 5) & (df['最新价'] <= 35) &
            (df['涨跌幅'] >= 1.5) & (df['涨跌幅'] <= 5) &
            (df['振幅'] >= 3) & (df['振幅'] <= 7) &
            (df['换手率'] >= 5) & (df['换手率'] <= 12) &
            (df['量比'] >= 1.2) & (df['量比'] <= 3.0) &
            (df['成交额'] > 80000000)
        )
        df = df[mask]
        
        if len(df) > 20:
            df = df.nlargest(20, '量比')
        
        return df
    except Exception as e:
        print(f"初筛失败: {e}")
        return pd.DataFrame()


def get_stock_daily_data(symbol):
    """获取个股日线数据"""
    try:
        df = ak.stock_zh_a_daily(symbol=symbol, adjust="qfq")
        return df
    except Exception as e:
        return None


def get_sector_data():
    """获取板块数据"""
    try:
        sector_df = ak.stock_board_industry_name_em()
        return sector_df
    except Exception as e:
        return None


def get_stock_sector(code):
    """获取个股所属板块"""
    try:
        df = ak.stock_zh_a_spot_em()
        stock = df[df['代码'] == code]
        if len(stock) > 0:
            return stock.iloc[0]['行业'], stock.iloc[0]['概念板块']
    except Exception as e:
        pass
    return None, None


def calculate_macd(close, fast=12, slow=26, signal=9):
    """计算MACD"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    hist = macd - signal_line
    return macd, signal_line, hist


def calculate_score(stock, df_daily, sector_df):
    """计算评分"""
    score = 0
    details = {}
    
    last_close = stock['最新价']
    volume_ratio = stock['量比']
    market_cap = stock['流通市值'] / 100000000
    
    trend_score = 0
    if df_daily is not None and len(df_daily) >= 20:
        df_daily['MA5'] = df_daily['close'].rolling(5).mean()
        df_daily['MA10'] = df_daily['close'].rolling(10).mean()
        df_daily['MA20'] = df_daily['close'].rolling(20).mean()
        df_daily['MACD'], df_daily['MACD_signal'], df_daily['MACD_hist'] = calculate_macd(df_daily['close'])
        
        last_day = df_daily.iloc[-1]
        ma5_above_ma10 = last_day['MA5'] > last_day['MA10']
        ma10_above_ma20 = last_day['MA10'] > last_day['MA20']
        price_above_ma5 = last_close > last_day['MA5']
        macd_above_zero = last_day['MACD'] > 0
        
        if ma5_above_ma10 and ma10_above_ma20:
            trend_score += 10
        if price_above_ma5:
            trend_score += 8
        if macd_above_zero:
            trend_score += 7
    score += trend_score
    details['趋势强度'] = f"{trend_score}/25"
    
    volume_score = 0
    if 1.5 <= volume_ratio <= 2.5:
        volume_score = 15
    elif 1.2 <= volume_ratio < 1.5:
        volume_score = 10
    elif 2.5 < volume_ratio <= 3.0:
        volume_score = 5
    score += volume_score
    details['量比合理'] = f"{volume_score}/15"
    
    market_cap_score = 0
    if 20 <= market_cap <= 50:
        market_cap_score = 5
    elif 50 < market_cap <= 80:
        market_cap_score = 3
    elif 80 < market_cap <= 100:
        market_cap_score = 1
    score += market_cap_score
    details['市值弹性'] = f"{market_cap_score}/5"
    
    volume_price_score = 25
    score += volume_price_score
    details['量价结构'] = f"{volume_price_score}/35"
    
    industry_name, _ = get_stock_sector(stock['代码'])
    sector_score = 0
    sector_rank = 0
    sector_change = 0
    
    if sector_df is not None and industry_name:
        sector = sector_df[sector_df['板块名称'].str.contains(industry_name, na=False)]
        if len(sector) > 0:
            sector_change = sector.iloc[0]['涨跌幅']
            sector_df_sorted = sector_df.sort_values('涨跌幅', ascending=False).reset_index(drop=True)
            sector_match = sector_df_sorted[sector_df_sorted['板块名称'].str.contains(industry_name, na=False)]
            if len(sector_match) > 0:
                sector_rank = sector_match.index[0] + 1
                
                if sector_rank <= 10 and sector_change > 2:
                    sector_score = 20
                elif sector_rank <= 20:
                    sector_score = 10
    
    score += sector_score
    details['板块热度'] = f"{sector_score}/20"
    details['板块名'] = industry_name if industry_name else "未知"
    details['板块涨幅'] = sector_change
    details['板块排名'] = sector_rank
    
    details['总分'] = score
    return score, details


def main():
    print("="*50)
    print("       一夜持股法 v4.0 - 尾盘选股")
    print("="*50)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    output = []
    output.append("="*50)
    output.append("       一夜持股法 v4.0 - 尾盘选股")
    output.append("="*50)
    output.append(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    output.append("")
    
    print("[步骤1/4] 大盘环境评估...")
    market = get_market_status()
    
    if market is None:
        print("获取大盘数据失败，程序终止")
        input("\n按回车键退出...")
        return
    
    print(f"上证: {market['close']:.2f} | MA5: {market['MA5']:.2f} | MA10: {market['MA10']:.2f} | MA20: {market['MA20']:.2f}")
    
    ma5_above_ma10 = market['MA5'] > market['MA10']
    price_above_ma20 = market['close'] > market['MA20']
    
    if price_above_ma20 and ma5_above_ma10:
        position = "满仓(绿)"
    elif price_above_ma20 and not ma5_above_ma10:
        position = "半仓(黄)"
    else:
        position = "回避(红)"
    
    print(f"仓位: {position}")
    
    output.append("[步骤1/4] 大盘环境评估")
    output.append(f"上证: {market['close']:.2f} | MA5: {market['MA5']:.2f} | MA10: {market['MA10']:.2f} | MA20: {market['MA20']:.2f}")
    output.append(f"仓位: {position}")
    
    if position == "回避(红)":
        print("\n>>> 大盘回避模式，今日不操作")
        output.append("\n>>> 大盘回避模式，今日不操作")
        save_output(output)
        input("\n按回车键退出...")
        return
    
    print("\n[步骤2/4] 全市场初筛...")
    candidates = screen_stocks()
    print(f"初筛结果: {len(candidates)} 只")
    
    output.append("\n[步骤2/4] 全市场初筛")
    output.append(f"初筛结果: {len(candidates)} 只")
    
    if len(candidates) == 0:
        print("无符合条件的候选股")
        output.append("无符合条件的候选股")
        save_output(output)
        input("\n按回车键退出...")
        return
    
    print("\n[步骤3/4] 批量尾盘验证...")
    sector_df = get_sector_data()
    results = []
    
    total = len(candidates)
    for i, (_, stock) in enumerate(candidates.iterrows(), 1):
        code = stock['代码']
        name = stock['名称']
        print(f"正在验证 {i}/{total}: {name}...", end='\r')
        
        symbol = f"sh{code}" if code.startswith('6') else f"sz{code}"
        
        df_daily = get_stock_daily_data(symbol)
        
        if df_daily is not None and len(df_daily) >= 20:
            high_20d = df_daily['high'].tail(20).max()
            distance = (high_20d - stock['最新价']) / high_20d * 100
            
            if distance < 3:
                continue
        
        score, details = calculate_score(stock, df_daily, sector_df)
        
        results.append({
            'code': code,
            'name': name,
            'price': stock['最新价'],
            'change': stock['涨跌幅'],
            'turnover': stock['换手率'],
            'volume_ratio': stock['量比'],
            'market_cap': stock['流通市值'] / 100000000,
            'score': score,
            'details': details
        })
        
        time.sleep(0.2)
    
    print()
    
    results.sort(key=lambda x: x['score'], reverse=True)
    final_candidates = results[:5]
    
    print("\n[步骤4/4] 生成结果...")
    
    output.append("\n[步骤3/4] 批量尾盘验证完成")
    output.append("\n[步骤4/4] 生成结果")
    output.append("\n" + "="*50)
    output.append(f"筛选链: 全市场 -> 初筛{len(candidates)}只 -> 验证{len(final_candidates)}只 -> TOP{len(final_candidates)}")
    output.append("="*50)
    output.append("\n【精选候选 TOP5】")
    output.append("")
    
    print("\n" + "="*50)
    print("【精选候选 TOP5】")
    print("="*50)
    
    for i, stock in enumerate(final_candidates, 1):
        tag = "[★1]" if i == 1 else "[▲2]" if i == 2 else "[△3]" if i == 3 else "[4]" if i == 4 else "[5]"
        line1 = f"{tag} {stock['code']} {stock['name']}"
        line2 = f"    现价: {stock['price']:.2f} | 涨幅: +{stock['change']:.2f}% | 换手: {stock['turnover']:.2f}% | 量比: {stock['volume_ratio']:.2f} | 总分: {stock['score']}/100"
        line3 = f"    板块: {stock['details']['板块名']} +{stock['details']['板块涨幅']:.1f}% 第{stock['details']['板块排名']} | 流通: {stock['market_cap']:.1f}亿"
        line4 = f"    目标: {stock['price']:.2f}±0.3% | 止盈: +3/+5% | 止损: -2%"
        
        print(line1)
        print(line2)
        print(line3)
        print(line4)
        print()
        
        output.append(line1)
        output.append(line2)
        output.append(line3)
        output.append(line4)
        output.append("")
    
    output.append("="*50)
    output.append("【隔夜持仓纪律】")
    output.append("- 次日高开>2% -> 5分钟不续涨清仓")
    output.append("- 高开0-2% -> 9:45破开盘价清仓")
    output.append("- 低开<-0.5% -> 开盘即清仓")
    output.append("- 10:00前必须清/留决策完成")
    output.append("- 一夜持股=惯性溢价，不是趋势行情")
    output.append("="*50)
    output.append("\n注: 仅供研究，不构成投资建议")
    
    print("="*50)
    print("【隔夜持仓纪律】")
    print("- 次日高开>2% -> 5分钟不续涨清仓")
    print("- 高开0-2% -> 9:45破开盘价清仓")
    print("- 低开<-0.5% -> 开盘即清仓")
    print("- 10:00前必须清/留决策完成")
    print("- 一夜持股=惯性溢价，不是趋势行情")
    print("="*50)
    print("\n注: 仅供研究，不构成投资建议")
    
    save_output(output)
    print("\n完成！结果已保存到 选股结果.txt")
    input("\n按回车键退出...")


def save_output(content):
    try:
        filename = f"选股结果_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write('\n'.join(content))
        return filename
    except Exception as e:
        print(f"保存文件失败: {e}")
        return None


if __name__ == "__main__":
    main()
