"""
A股尾盘"一夜持股法"选股策略 v2.0 - 2026-05-04 更新版
执行时间：每个交易日 14:45（本次使用收盘数据回测）
"""

import akshare as ak
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta, date

print("=" * 60)
print("【大盘环境检查】")
print("=" * 60)

# ============================================
# 第一部分：大盘环境检查
# ============================================

# 获取实时指数数据（收盘后获取当日收盘数据）
spot_all = ak.stock_zh_index_spot_sina()

sh = spot_all[spot_all['代码'] == 'sh000001'].iloc[0]
cy = spot_all[spot_all['代码'] == 'sz399006'].iloc[0]

sh_code = 'sh000001'
cy_code = 'sz399006'

# 获取历史数据计算MA20
end_date = datetime.now().date()
start_date = (datetime.now() - timedelta(days=60)).date()

sh_hist = ak.stock_zh_index_daily(symbol=sh_code)
sh_hist = sh_hist[sh_hist['date'] >= start_date].tail(25)
sh_hist['MA20'] = sh_hist['close'].rolling(20).mean()
latest_sh = sh_hist.iloc[-1]
sh_price = float(sh['最新价'])
sh_chg = float(sh['涨跌幅'])
sh_ma20 = float(latest_sh['MA20'])

cy_hist = ak.stock_zh_index_daily(symbol=cy_code)
cy_hist = cy_hist[cy_hist['date'] >= start_date].tail(25)
cy_hist['MA20'] = cy_hist['close'].rolling(20).mean()
latest_cy = cy_hist.iloc[-1]
cy_price = float(cy['最新价'])
cy_chg = float(cy['涨跌幅'])
cy_ma20 = float(latest_cy['MA20'])

print(f"上证指数: {sh_price:.2f} | MA20: {sh_ma20:.2f} | 涨跌幅: {sh_chg:+.3f}%")
print(f"创业板指: {cy_price:.2f} | MA20: {cy_ma20:.2f} | 涨跌幅: {cy_chg:+.3f}%")

# 判断条件
sh_above_ma20 = sh_price > sh_ma20
cy_above_ma20 = cy_price > cy_ma20
not_one_sided = sh_chg > -1.0

print()
print("【环境判断】")
print(f"  上证 > MA20: {'✅' if sh_above_ma20 else '❌'} ({sh_price:.2f} {'>' if sh_above_ma20 else '<'} {sh_ma20:.2f})")
print(f"  创业板 > MA20: {'✅' if cy_above_ma20 else '❌'} ({cy_price:.2f} {'>' if cy_above_ma20 else '<'} {cy_ma20:.2f})")
print(f"  上证跌幅 ≤1%: {'✅' if not_one_sided else '❌'} ({sh_chg:+.3f}% {'>' if not_one_sided else '<='} -1%)")

if not (sh_above_ma20 and cy_above_ma20 and not_one_sided):
    print()
    print("⛔ 大盘环境不满足，今日休息。")
    print("不满足的条件已列出，请择日再执行选股。")
    exit(0)

print()
print("✅ 大盘环境满足，开始执行选股策略")
print()

# ============================================
# 第二部分：初筛过滤
# ============================================
print("=" * 60)
print("【第一步：初筛过滤】")
print("=" * 60)

t0 = time.time()
spot = ak.stock_zh_a_spot()
t1 = time.time()
print(f"全市场数据加载完成，共 {len(spot)} 只，耗时 {t1-t0:.1f}s")

# 基础过滤
# 1. 非ST、非退市
df = spot[~spot['名称'].str.contains('ST|退', na=False)].copy()
# 2. 非停牌（昨收>0且最新价>0）
df = df[(df['昨收'] > 0) & (df['最新价'] > 0)]
# 3. 股价 < 20元
df = df[df['最新价'] < 20]
# 4. 成交额 > 5000万
df = df[df['成交额'] > 50000000]
# 5. 涨幅 0-5%
df = df[(df['涨跌幅'] >= 0) & (df['涨跌幅'] <= 5)]
# 6. 过滤北交所
df = df[~df['代码'].str.startswith('bj')]

print(f"初筛后候选股票: {len(df)} 只")

# 由于AKShare的spot数据没有换手率，我们需要通过其他方式过滤
# 这里先取涨幅在0.5%-5%区间且成交额较大的股票
df_good = df.sort_values('成交额', ascending=False).head(200)
print(f"按成交额排序，取前 200 只进入趋势确认阶段")

print()

# ============================================
# 第三部分：获取历史数据计算MA
# ============================================
print("=" * 60)
print("【第二步：个股趋势确认】")
print("=" * 60)

def get_tx_hist(code, days=30):
    """获取腾讯历史数据 - 动态日期"""
    try:
        c = code.lower()
        if c.startswith('sh') or c.startswith('bj'):
            tx_code = 'sh' + code[2:]
        else:
            tx_code = 'sz' + code[2:]

        end = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')
        df = ak.stock_zh_a_hist_tx(symbol=tx_code, start_date=start, end_date=end)
        if df is None or len(df) == 0:
            return None
        return df.tail(days)
    except Exception as e:
        return None

candidates = []
batch = 0
total = len(df_good)

for idx, row in df_good.iterrows():
    batch += 1
    code = row['代码']
    name = row['名称']

    if batch % 50 == 0:
        print(f"  处理中... {batch}/{total}")

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

    # 多头排列：收盘 > MA5 > MA10 > MA20
    bull = current_close > ma5 > ma10 > ma20
    # 5日均线近3日斜率为正
    slope_positive = ma5 > ma5_3d_ago

    if bull and slope_positive:
        ma5_slope = (ma5 - ma5_3d_ago) / ma5_3d_ago * 100

        prev_close = close[-2] if len(close) >= 2 else current_close
        prev_high = hist['high'].values[-2] if len(hist) >= 2 else current_close
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

        # 量比代理
        avg_5d_amt = hist['amount'].tail(5).mean()
        today_amt = hist['amount'].values[-1]
        vol_ratio = today_amt / avg_5d_amt if avg_5d_amt > 0 else 1.0

        # 均线紧密度
        gap_5_10 = (ma5 - ma10) / ma10 * 100
        gap_10_20 = (ma10 - ma20) / ma20 * 100

        candidates.append({
            '代码': code,
            '名称': name,
            '最新价': row['最新价'],
            '涨跌幅': row['涨跌幅'],
            '成交额': row['成交额'],
            'MA5': ma5,
            'MA10': ma10,
            'MA20': ma20,
            '当前收盘': current_close,
            'MA5斜率': ma5_slope,
            'N型反包': n_type,
            '平台突破': platform_break,
            '量比代理': vol_ratio,
            'gap_5_10': gap_5_10,
            'gap_10_20': gap_10_20,
            'today_high': today_high,
            'today_open': today_open,
            'today_low': today_low,
            'prev_high': prev_high,
            'prev_close': prev_close,
        })

print(f"趋势确认后候选股票: {len(candidates)} 只")

if len(candidates) == 0:
    print()
    print("⛔ 没有符合条件的股票，请择日再执行。")
    exit(0)

# ============================================
# 第四部分：形态过滤
# ============================================
print()
print("=" * 60)
print("【第三步：形态筛选】")
print("=" * 60)

filtered = []
for c in candidates:
    code = c['代码']
    name = c['名称']
    today_high = c['today_high']
    today_open = c['today_open']
    current_close = c['当前收盘']

    # 上影线判断
    upper_shadow = (today_high - max(current_close, today_open)) / today_open * 100
    body = abs(current_close - today_open) / today_open * 100

    # 禁止：极长上影线
    if upper_shadow > body * 3 and upper_shadow > 3:
        print(f"  排除 {code} {name}: 极长上影线({upper_shadow:.1f}%)")
        continue

    # 禁止：涨幅过大
    if c['涨跌幅'] > 5:
        print(f"  排除 {code} {name}: 涨幅过大({c['涨跌幅']:.2f}%)")
        continue

    # 检查是否接近60日高点
    hist_full = get_tx_hist(code, days=60)
    if hist_full is not None and len(hist_full) >= 60:
        ma60_high = hist_full['close'].tail(60).max()
        if current_close > ma60_high * 0.95:
            print(f"  排除 {code} {name}: 接近60日高点({current_close:.2f} vs {ma60_high:.2f})")
            continue

    filtered.append(c)

print(f"形态过滤后候选股票: {len(filtered)} 只")

if len(filtered) == 0:
    print()
    print("⛔ 形态过滤后无候选股票。")
    exit(0)

# ============================================
# 第五部分：综合打分排序
# ============================================
print()
print("=" * 60)
print("【第四步：综合打分（TOP10）】")
print("=" * 60)

for c in filtered:
    score = 0.0

    # 1. 均线紧密度（20%）
    gap_score = min(c['gap_5_10'] + c['gap_10_20'], 10) / 10 * 20
    score += gap_score

    # 2. MA5斜率（20%）
    ma5_score = min(c['MA5斜率'] * 5, 20) if c['MA5斜率'] > 0 else 0
    score += ma5_score

    # 3. 量比（20%）
    vol_score = min(c['量比代理'] / 2, 1.0) * 20
    score += vol_score

    # 4. 形态加分（10%）
    shape_score = 0
    if c['平台突破']:
        shape_score += 5
    if c['N型反包']:
        shape_score += 5
    score += shape_score

    # 5. 价格弹性（10%）：距近期高点空间
    recent_high = c.get('today_high', c['当前收盘'])
    if recent_high > 0:
        distance = (recent_high - c['当前收盘']) / recent_high * 100
        elastic_score = max(0, min(distance / 10, 1.0)) * 10
        score += elastic_score

    # 6. 成交额活跃度（10%）
    max_amt = max([x['成交额'] for x in filtered])
    active_score = (c['成交额'] / max_amt) * 10 if max_amt > 0 else 0
    score += active_score

    # 7. 涨幅合理性（10%）
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

# 排序取TOP10
filtered.sort(key=lambda x: x['综合得分'], reverse=True)
top10 = filtered[:10]

# ============================================
# 第六部分：输出结果
# ============================================
print()
print("=" * 60)
print("【选股结果 - TOP10】")
print("=" * 60)

print()
print("【大盘环境状态】")
print(f"  上证指数：{sh_price:.2f} / MA20 {sh_ma20:.2f} / 涨跌幅 {sh_chg:+.3f}%")
print(f"  创业板指：{cy_price:.2f} / MA20 {cy_ma20:.2f} / 涨跌幅 {cy_chg:+.3f}%")
print(f"  环境判断：✅ 满足（上证>MA20, 创业板>MA20, 跌幅≤1%）")

print()
print("=" * 60)
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

print()
print("=" * 60)
print()
print("⚠️  策略已过滤，请于尾盘（14:55-15:00）结合分时图（确认回踩均线不破）决策，")
print("    并严格执行次日早盘止盈止损纪律。")
print()
print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (回测模式-使用收盘数据)")
print("=" * 60)

# 保存结果
import json
result_data = {
    '执行时间': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    '模式': '回测（收盘数据）',
    '大盘环境': {
        '上证指数': {'最新价': sh_price, 'MA20': sh_ma20, '涨跌幅': sh_chg},
        '创业板指': {'最新价': cy_price, 'MA20': cy_ma20, '涨跌幅': cy_chg}
    },
    '候选数量': len(candidates),
    '过滤后数量': len(filtered),
    'top10': top10
}

today_str = datetime.now().strftime('%Y%m%d')
with open(f'screener_top10_v3_{today_str}.json', 'w', encoding='utf-8') as f:
    json.dump(result_data, f, ensure_ascii=False, indent=2, default=str)

print(f"\n结果已保存至 screener_top10_v3_{today_str}.json")
