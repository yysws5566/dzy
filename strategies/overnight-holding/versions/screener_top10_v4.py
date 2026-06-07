"""
A股尾盘"一夜持股法"选股策略 v4.0 - 2026-05-07
基于昨日候选池 + 今日腾讯历史数据更新
"""
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

today_str = datetime.now().strftime('%Y%m%d')
print("=" * 60)
print("【大盘环境检查】")
print("=" * 60)

# 大盘环境检查（使用新浪实时指数）
spot_idx = ak.stock_zh_index_spot_sina()
sh = spot_idx[spot_idx['代码'] == 'sh000001'].iloc[0]
cy = spot_idx[spot_idx['代码'] == 'sz399006'].iloc[0]

# 计算MA20
sh_code, cy_code = 'sh000001', 'sz399006'
sh_hist = ak.stock_zh_index_daily(symbol=sh_code).tail(25)
cy_hist = ak.stock_zh_index_daily(symbol=cy_code).tail(25)
sh_hist['MA20'] = sh_hist['close'].rolling(20).mean()
cy_hist['MA20'] = cy_hist['close'].rolling(20).mean()

sh_price = float(sh['最新价'])
cy_price = float(cy['最新价'])
sh_chg = float(sh['涨跌幅'])
cy_chg = float(cy['涨跌幅'])
sh_ma20 = float(sh_hist.iloc[-1]['MA20'])
cy_ma20 = float(cy_hist.iloc[-1]['MA20'])

print(f"上证指数: {sh_price:.2f} | MA20: {sh_ma20:.2f} | 涨跌幅: {sh_chg:+.3f}%")
print(f"创业板指: {cy_price:.2f} | MA20: {cy_ma20:.2f} | 涨跌幅: {cy_chg:+.3f}%")
print()
print(f"  上证 > MA20: {'✅' if sh_price > sh_ma20 else '❌'}")
print(f"  创业板 > MA20: {'✅' if cy_price > cy_ma20 else '❌'}")
print(f"  上证跌幅 ≤1%: {'✅' if sh_chg > -1.0 else '❌'}")

if not (sh_price > sh_ma20 and cy_price > cy_ma20 and sh_chg > -1.0):
    print("\n⛔ 大盘环境不满足，今日休息。")
    exit(0)

print("\n✅ 大盘环境满足，开始执行选股策略\n")

# 读取候选池
print("=" * 60)
print("【第一步：读取候选池】")
print("=" * 60)
df = pd.read_csv('candidates_20260507.csv', encoding='utf-8-sig')
print(f"候选池: {len(df)} 只")

# 获取今日数据并更新
print("\n" + "=" * 60)
print("【第二步：获取今日历史数据 & 趋势确认】")
print("=" * 60)

def get_tx_hist(code, days=30):
    try:
        c = code.lower()
        if c.startswith('sh') or c.startswith('bj'):
            tx_code = code
        else:
            tx_code = 'sz' + code[2:]
        end = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')
        df = ak.stock_zh_a_hist_tx(symbol=tx_code, start_date=start, end_date=end)
        if df is None or len(df) == 0:
            return None
        return df.tail(days)
    except:
        return None

candidates = []
total = len(df)
t0 = datetime.now()

for i, (idx, row) in enumerate(df.iterrows()):
    code = row['代码']
    name = row['名称']
    
    if (i+1) % 50 == 0:
        elapsed = (datetime.now() - t0).total_seconds()
        print(f"  处理中... {i+1}/{total} (已用{elapsed:.0f}s)")
    
    hist = get_tx_hist(code, days=30)
    if hist is None or len(hist) < 25:
        continue
    
    close = hist['close'].values
    if len(close) < 25:
        continue
    
    ma5 = close[-5:].mean()
    ma10 = close[-10:].mean()
    ma20 = close[-20:].mean()
    ma5_3d_ago = close[-8:-3].mean() if len(close) >= 8 else close[-5:].mean()
    current_close = close[-1]
    
    # 多头排列
    bull = current_close > ma5 > ma10 > ma20
    slope_positive = ma5 > ma5_3d_ago
    
    if bull and slope_positive:
        ma5_slope = (ma5 - ma5_3d_ago) / ma5_3d_ago * 100
        prev_close = close[-2] if len(close) >= 2 else current_close
        prev_open = hist['open'].values[-2] if len(hist) >= 2 else current_close
        today_open = hist['open'].values[-1]
        today_high = hist['high'].values[-1]
        today_low = hist['low'].values[-1]
        
        # N型反包
        prev_is_yin = prev_close < prev_open
        today_cover = current_close > prev_open and current_close > prev_close
        n_type = prev_is_yin and today_cover
        
        # 平台突破
        recent_high = max(hist['high'].values[-7:-1]) if len(hist) >= 7 else today_high
        platform_break = today_high > recent_high
        
        # 量比
        avg_5d_amt = hist['amount'].tail(5).mean()
        today_amt = hist['amount'].values[-1]
        vol_ratio = today_amt / avg_5d_amt if avg_5d_amt > 0 else 1.0
        
        # 均线紧密度
        gap_5_10 = (ma5 - ma10) / ma10 * 100
        gap_10_20 = (ma10 - ma20) / ma20 * 100
        
        candidates.append({
            '代码': code,
            '名称': name,
            '最新价': current_close,
            '涨跌幅': (current_close - prev_close) / prev_close * 100,
            '成交额': today_amt,
            'MA5': ma5,
            'MA10': ma10,
            'MA20': ma20,
            'MA5斜率': ma5_slope,
            'N型反包': n_type,
            '平台突破': platform_break,
            '量比代理': vol_ratio,
            'gap_5_10': gap_5_10,
            'gap_10_20': gap_10_20,
            'today_high': today_high,
            'today_open': today_open,
            'today_low': today_low,
            'prev_high': hist['high'].values[-2] if len(hist) >= 2 else today_high,
            'prev_close': prev_close,
        })

elapsed = (datetime.now() - t0).total_seconds()
print(f"趋势确认后候选: {len(candidates)} 只 (耗时 {elapsed:.0f}s)")

if len(candidates) == 0:
    print("\n⛔ 没有符合条件的股票。")
    exit(0)

# 形态过滤
print("\n" + "=" * 60)
print("【第三步：形态筛选】")
print("=" * 60)

filtered = []
for c in candidates:
    code = c['代码']
    name = c['名称']
    today_high = c['today_high']
    today_open = c['today_open']
    current_close = c['当前收盘'] if '当前收盘' in c else c['最新价']
    
    upper_shadow = (today_high - max(current_close, today_open)) / today_open * 100
    body = abs(current_close - today_open) / today_open * 100
    
    if upper_shadow > body * 3 and upper_shadow > 3:
        print(f"  排除 {code} {name}: 极长上影线({upper_shadow:.1f}%)")
        continue
    
    # 检查60日高点
    hist_full = get_tx_hist(code, days=60)
    if hist_full is not None and len(hist_full) >= 60:
        ma60_high = hist_full['close'].tail(60).max()
        if current_close > ma60_high * 0.95:
            print(f"  排除 {code} {name}: 接近60日高点")
            continue
    
    filtered.append(c)

print(f"形态过滤后: {len(filtered)} 只")

if len(filtered) == 0:
    print("\n⛔ 形态过滤后无候选股票。")
    exit(0)

# 综合打分
print("\n" + "=" * 60)
print("【第四步：综合打分（TOP10）】")
print("=" * 60)

for c in filtered:
    score = 0.0
    gap_score = min(c['gap_5_10'] + c['gap_10_20'], 10) / 10 * 20
    score += gap_score
    ma5_score = min(c['MA5斜率'] * 5, 20) if c['MA5斜率'] > 0 else 0
    score += ma5_score
    vol_score = min(c['量比代理'] / 2, 1.0) * 20
    score += vol_score
    shape_score = 0
    if c['平台突破']:
        shape_score += 5
    if c['N型反包']:
        shape_score += 5
    score += shape_score
    recent_high = c.get('today_high', c['最新价'])
    if recent_high > 0:
        distance = (recent_high - c['最新价']) / recent_high * 100
        elastic_score = max(0, min(distance / 10, 1.0)) * 10
        score += elastic_score
    max_amt = max([x['成交额'] for x in filtered])
    active_score = (c['成交额'] / max_amt) * 10 if max_amt > 0 else 0
    score += active_score
    chg = c['涨跌幅']
    if 1.0 <= chg <= 3.0:
        chg_score = 10
    elif 3.0 < chg <= 5.0:
        chg_score = 7
    elif 0 <= chg < 1.0:
        chg_score = 5
    else:
        chg_score = 3
    score += chg_score
    c['综合得分'] = round(score, 1)

filtered.sort(key=lambda x: x['综合得分'], reverse=True)
top10 = filtered[:10]

# 输出结果
print("\n" + "=" * 60)
print("【选股结果 - TOP10】")
print("=" * 60)
print(f"\n【大盘环境状态】")
print(f"  上证指数：{sh_price:.2f} / MA20 {sh_ma20:.2f} / 涨跌幅 {sh_chg:+.3f}%")
print(f"  创业板指：{cy_price:.2f} / MA20 {cy_ma20:.2f} / 涨跌幅 {cy_chg:+.3f}%")
print(f"  环境判断：✅ 满足（上证>MA20, 创业板>MA20, 跌幅≤1%）")
print("\n" + "=" * 60)
print("【选股结果（按综合得分降序排列）】")
print("=" * 60)

for rank, c in enumerate(top10, 1):
    name = c['名称']
    code = c['代码']
    ma_str = f"MA5={c['MA5']:.2f} > MA10={c['MA10']:.2f} > MA20={c['MA20']:.2f} ✓"
    shape_marks = []
    if c['平台突破']:
        shape_marks.append("平台突破")
    if c['N型反包']:
        shape_marks.append("N型反包")
    shape_str = "/".join(shape_marks) if shape_marks else "无特殊形态"
    vol_str = f"{c['量比代理']:.2f}x"
    print()
    print(f"#{rank} [{code}] {name}")
    print(f"  价格：{c['最新价']:.2f}元 | 涨幅：{c['涨跌幅']:+.2f}% | 量比：{vol_str}")
    print(f"  成交额：{c['成交额']/1e8:.2f}亿")
    print(f"  均线排列：{ma_str}")
    print(f"  MA5斜率：{c['MA5斜率']:+.3f}%")
    print(f"  形态标记：{shape_str}")
    print(f"  综合得分：{c['综合得分']:.1f}")

print("\n" + "=" * 60)
print("\n⚠️  策略已过滤，请于尾盘（14:55-15:00）结合分时图（确认回踩均线不破）决策，")
print("    并严格执行次日早盘止盈止损纪律。")
print(f"\n执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (v4.0 昨日候选池+今日数据更新)")
print("=" * 60)

# 保存结果
import json
result_data = {
    '执行时间': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    '模式': 'v4.0 昨日候选池+今日数据更新',
    '大盘环境': {
        '上证指数': {'最新价': sh_price, 'MA20': sh_ma20, '涨跌幅': sh_chg},
        '创业板指': {'最新价': cy_price, 'MA20': cy_ma20, '涨跌幅': cy_chg}
    },
    '候选数量': len(candidates),
    '过滤后数量': len(filtered),
    'top10': top10
}
with open(f'screener_top10_v4_{today_str}.json', 'w', encoding='utf-8') as f:
    json.dump(result_data, f, ensure_ascii=False, indent=2, default=str)
print(f"\n结果已保存至 screener_top10_v4_{today_str}.json")
