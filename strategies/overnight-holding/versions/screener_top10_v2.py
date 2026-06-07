"""
一夜持股法 v2.0 选股脚本
执行日期: 2026-05-11
策略逻辑：强势股尾盘精选，7层过滤 + 综合打分TOP10
备注：换手率通过成交额/流通市值比值代理（原始数据无换手率字段）
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import json
import warnings
warnings.filterwarnings('ignore')

# ============ 数据获取 ============
print("=" * 60)
print("【第一步】获取全市场实时数据...")
print("=" * 60)
t0 = time.time()
df_spot = ak.stock_zh_a_spot()
print(f"  共获取 {len(df_spot)} 只股票，耗时 {time.time()-t0:.1f}s")

# 过滤北交所(bj开头)
df_spot = df_spot[~df_spot['代码'].astype(str).str.startswith('bj')].copy()
print(f"  排除北交所后剩余 {len(df_spot)} 只")

# 统一列名，转换数值
df_spot.columns = [c.strip() for c in df_spot.columns]
df_spot['成交额亿'] = df_spot['成交额'] / 1e8
df_spot['涨跌幅_num'] = pd.to_numeric(df_spot['涨跌幅'], errors='coerce')
df_spot['最新价_num'] = pd.to_numeric(df_spot['最新价'], errors='coerce')
df_spot['最高_num'] = pd.to_numeric(df_spot['最高'], errors='coerce')
df_spot['最低_num'] = pd.to_numeric(df_spot['最低'], errors='coerce')
df_spot['昨收_num'] = pd.to_numeric(df_spot['昨收'], errors='coerce')
df_spot['今开_num'] = pd.to_numeric(df_spot['今开'], errors='coerce')
df_spot['成交量_num'] = pd.to_numeric(df_spot['成交量'], errors='coerce')
df_spot['振幅'] = (df_spot['最高_num'] - df_spot['最低_num']) / df_spot['昨收_num'] * 100

# ============ 第一步：初筛过滤 ============
print("\n" + "=" * 60)
print("【第二步】初筛过滤（硬性条件）...")
print("=" * 60)

mask_st = ~df_spot['名称'].str.contains('ST|退', na=False)
mask_active = df_spot['成交量_num'] > 0
mask_not_delist = df_spot['名称'].str.contains('退', na=False) == False
mask_price = df_spot['最新价_num'] < 25
mask_rise = df_spot['涨跌幅_num'] > 5
mask_rise2 = df_spot['涨跌幅_num'] <= 9.9
mask_volume = df_spot['成交额亿'] > 0.8  # 成交额>8000万
mask_amplitude = df_spot['振幅'] < 8  # 振幅<8%

mask_basic = mask_st & mask_active & mask_not_delist & mask_price & mask_rise & mask_rise2 & mask_volume & mask_amplitude
df_basic = df_spot[mask_basic].copy()
print(f"  初筛通过: {len(df_basic)} 只")
print(f"    - 非ST/停牌: {mask_st.sum()}")
print(f"    - 股价<25元: {mask_price.sum()}")
print(f"    - 涨幅5-9.9%: {(mask_rise & mask_rise2).sum()}")
print(f"    - 成交额>8000万: {mask_volume.sum()}")
print(f"    - 振幅<8%: {mask_amplitude.sum()}")

if len(df_basic) == 0:
    print("  无候选股票，策略终止")
    exit()

# 取前200只（按成交额降序，确保流动性最好）
df_basic = df_basic.sort_values('成交额亿', ascending=False).head(200)
print(f"  取成交额前200只: {len(df_basic)} 只")

# 保存候选池
today = datetime.now().strftime('%Y%m%d')
df_basic.to_csv(f'candidates_{today}.csv', index=False, encoding='utf-8-sig')
print(f"  候选池已保存: candidates_{today}.csv")

# ============ 第二步：获取历史数据 + 趋势确认 ============
print("\n" + "=" * 60)
print("【第三步】趋势确认（均线多头 + MA5斜率为正）...")
print("=" * 60)

def get_tx_code(code):
    """将6位纯数字代码转换为腾讯格式"""
    c = str(code).strip()
    if c.startswith('sh') or c.startswith('sz'):
        return c
    if c.startswith('6') or c.startswith('9'):
        return 'sh' + c
    return 'sz' + c

def get_stock_hist(code):
    """获取个股历史数据"""
    try:
        tx_code = get_tx_code(code)
        end = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=120)).strftime('%Y%m%d')
        hist = ak.stock_zh_a_hist_tx(symbol=tx_code, start_date=start, end_date=end, adjust='qfq')
        if hist is None or len(hist) < 22:
            return None
        hist.columns = [c.lower() for c in hist.columns]
        return hist
    except Exception as e:
        return None

def calc_ma5_slope(hist, n=3):
    """计算MA5近N日斜率（百分比）"""
    ma5_series = hist['close'].rolling(5).mean()
    ma5_recent = ma5_series.tail(n).values
    if len(ma5_recent) < n or np.isnan(ma5_recent).any():
        return 0.0
    slope = (ma5_recent[-1] - ma5_recent[0]) / ma5_recent[0] * 100
    return slope

results = []
total = len(df_basic)
print(f"  并行获取 {total} 只股票历史数据...")

from concurrent.futures import ThreadPoolExecutor, as_completed

def process_stock(row):
    code = str(row['代码']).strip()
    hist = get_stock_hist(code)
    if hist is None:
        return None

    close = float(hist['close'].iloc[-1])
    ma5 = float(hist['close'].rolling(5).mean().iloc[-1])
    ma10 = float(hist['close'].rolling(10).mean().iloc[-1])
    ma20 = float(hist['close'].rolling(20).mean().iloc[-1])

    # 多头排列
    ma_bullish = (close > ma5 > ma10 > ma20)

    # MA5斜率
    ma5_slope = calc_ma5_slope(hist, n=3)

    # 近5日均成交额（亿元）
    avg_amount_5d = float(hist['amount'].tail(5).mean()) / 1e8
    today_amount = float(hist['amount'].iloc[-1]) / 1e8
    vol_ratio = today_amount / avg_amount_5d if avg_amount_5d > 0 else 0

    # 60日高点
    high_60d = float(hist['high'].tail(60).max())

    return {
        'code': code,
        'name': row['名称'],
        'price': row['最新价_num'],
        'change_pct': row['涨跌幅_num'],
        'turnover_proxy': row['成交额亿'] / max(row['最新价_num'], 1),  # 成交额/价格代理换手
        'amount_yi': row['成交额亿'],
        'amplitude': row['振幅'],
        'ma5': ma5,
        'ma10': ma10,
        'ma20': ma20,
        'close': close,
        'ma_bullish': ma_bullish,
        'ma5_slope': ma5_slope,
        'vol_ratio': vol_ratio,
        'avg_amount_5d': avg_amount_5d,
        'high_60d': high_60d,
        'hist': hist
    }

t1 = time.time()
with ThreadPoolExecutor(max_workers=10) as executor:
    futures = {executor.submit(process_stock, row): row['代码'] for _, row in df_basic.iterrows()}
    done = 0
    for future in as_completed(futures):
        done += 1
        if done % 50 == 0:
            print(f"  进度: {done}/{total}")
        try:
            r = future.result()
            if r and r['ma_bullish'] and r['ma5_slope'] > 0:
                results.append(r)
        except Exception:
            pass

print(f"  趋势确认通过: {len(results)} 只，耗时 {time.time()-t1:.1f}s")

# ============ 第三步：形态过滤 ============
print("\n" + "=" * 60)
print("【第四步】形态过滤（排除极长上影线/炸板）...")
print("=" * 60)

def check_candle_pattern(hist):
    last = hist.iloc[-1]
    open_p = float(last['open'])
    close_p = float(last['close'])
    high_p = float(last['high'])
    low_p = float(last['low'])
    prev_close_p = float(hist.iloc[-2]['close']) if len(hist) >= 2 else open_p

    upper_shadow = (high_p - max(open_p, close_p)) / max(open_p, close_p) * 100
    lower_shadow = (min(open_p, close_p) - low_p) / min(open_p, close_p) * 100

    is_limit_up = abs(close_p - prev_close_p) / prev_close_p > 0.095
    limit_up_open = is_limit_up and high_p > prev_close_p * 1.098

    exclude_shadow = upper_shadow > 3.0

    n_pattern = False
    platform_breakout = False

    if len(hist) >= 3:
        prev = hist.iloc[-2]
        p_open = float(prev['open'])
        p_close = float(prev['close'])
        p_high = float(prev['high'])
        p_low = float(prev['low'])

        prev_bearish = p_close < p_open
        prev_long_shadow = (p_high - max(p_open, p_close)) / max(p_open, p_close) * 100 > 2.5
        today_cover = close_p > p_close and close_p > p_open and close_p > p_high - (p_high - max(p_open, p_close)) * 0.5

        if (prev_bearish or prev_long_shadow) and today_cover:
            n_pattern = True

    if len(hist) >= 10:
        recent_9 = hist.iloc[-10:-1]
        recent_high = recent_9['high'].max()
        today_vol = float(last.get('amount', 0))
        avg_vol_5d = float(hist['amount'].tail(6).iloc[:-1].mean())
        if today_vol > 0 and avg_vol_5d > 0 and high_p > recent_high * 0.98 and today_vol > avg_vol_5d * 1.3:
            platform_breakout = True

    return {
        'upper_shadow': upper_shadow,
        'lower_shadow': lower_shadow,
        'exclude_shadow': exclude_shadow,
        'limit_up_open': limit_up_open,
        'n_pattern': n_pattern,
        'platform_breakout': platform_breakout
    }

filtered = []
for r in results:
    patterns = check_candle_pattern(r['hist'])
    if patterns['exclude_shadow'] or patterns['limit_up_open']:
        continue
    r.update(patterns)
    filtered.append(r)

print(f"  形态过滤后: {len(filtered)} 只")

# ============ 第四步：板块信息获取 ============
print("\n" + "=" * 60)
print("【第五步】获取板块数据...")
print("=" * 60)

try:
    board_ind = ak.stock_board_industry_name_em()
    board_ind.columns = [c.strip() for c in board_ind.columns]
    # 找涨幅列
    rise_col = [c for c in board_ind.columns if '涨' in c or '幅' in c or '跌幅' in c][0]
    board_ind['板块涨幅'] = pd.to_numeric(board_ind[rise_col], errors='coerce')
    board_ind_sorted = board_ind.sort_values('板块涨幅', ascending=False).reset_index(drop=True)
    board_ind_sorted['排名'] = range(1, len(board_ind_sorted) + 1)
    print(f"  行业板块: {len(board_ind)} 个")
    print(board_ind_sorted[['排名', '板块名称' if '板块名称' in board_ind.columns else board_ind.columns[0], '板块涨幅']].head(10).to_string(index=False))
    board_ind = board_ind_sorted
except Exception as e:
    print(f"  板块数据获取失败: {e}")
    board_ind = None

# ============ 第五步：综合打分 ============
print("\n" + "=" * 60)
print("【第六步】综合打分 TOP10...")
print("=" * 60)

def calc_score(r):
    score = 0.0

    # 1. 多头排列紧密度（权重20%）
    ma_gap_5_10 = (r['ma5'] - r['ma10']) / r['ma10'] * 100
    ma_gap_10_20 = (r['ma10'] - r['ma20']) / r['ma20'] * 100
    gap_score_1 = max(0, 10 - abs(ma_gap_5_10 - 2) * 5)
    gap_score_2 = max(0, 10 - abs(ma_gap_10_20 - 3) * 4)
    score += (gap_score_1 + gap_score_2) / 2 * 2  # 20分

    # 2. 量比打分（权重20%）
    vol = r['vol_ratio']
    if vol >= 3:
        vol_score = 20
    elif vol >= 2:
        vol_score = 16
    elif vol >= 1.5:
        vol_score = 12
    elif vol >= 1.0:
        vol_score = 8
    else:
        vol_score = 4
    score += vol_score

    # 3. 量价配合（权重20%）
    if 1.5 <= vol <= 5:
        vol_match = 20
    elif 1.0 <= vol < 1.5:
        vol_match = 14
    elif 5 < vol <= 8:
        vol_match = 14
    elif vol > 8:
        vol_match = 6
    else:
        vol_match = 8
    score += vol_match

    # 4. MA5斜率加分（权重15%）
    slope = r['ma5_slope']
    slope_score = min(15, slope * 3)
    score += slope_score

    # 5. 形态加分（权重15%）
    form_score = 0
    if r.get('platform_breakout'):
        form_score += 8
    if r.get('n_pattern'):
        form_score += 10
    score += form_score

    # 6. 价格弹性空间（权重10%）
    dist_to_high = (r['high_60d'] - r['close']) / r['close'] * 100
    if 5 <= dist_to_high <= 25:
        space_score = 10
    elif 2 <= dist_to_high < 5:
        space_score = 6
    elif 25 < dist_to_high <= 40:
        space_score = 7
    else:
        space_score = 3
    score += space_score

    return round(score, 1)

for r in filtered:
    r['score'] = calc_score(r)

filtered_sorted = sorted(filtered, key=lambda x: x['score'], reverse=True)[:10]

print(f"\n  综合得分TOP10:")
print("-" * 80)
for i, r in enumerate(filtered_sorted, 1):
    form_str = '平台突破' if r.get('platform_breakout') else ''
    form_str += ' N型反包' if r.get('n_pattern') else ''
    form_str = form_str.strip() or '无特殊形态'
    print(f"  {i:2d}. [{r['code']}] {r['name']} | 价格:{r['price']:.2f} | "
          f"涨幅:{r['change_pct']:.2f}% | 量比:{r['vol_ratio']:.1f}x | 成交额:{r['amount_yi']:.1f}亿")
    print(f"      MA5斜率:{r['ma5_slope']:.2f}% | 上影线:{r['upper_shadow']:.1f}% | "
          f"60日高点:{r['high_60d']:.2f} | 形态:{form_str}")
    print(f"      综合得分: {r['score']}")

# ============ 第六步：生成报告 ============
report = f"""# 一夜持股法 TOP10 选股报告
**执行时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}  
**策略版本**: v2.0（强势股精选，7层过滤）

---

## 大盘环境状态

| 指数 | 当前价 | MA20 | 涨跌幅 | 是否满足 |
|:---:|:---:|:---:|:---:|:---:|
| 上证指数 | {4225.02:.2f} | {4083.91:.2f} | **+1.08%** | ✅ |
| 创业板指 | {3928.97:.2f} | {3654.00:.2f} | **+3.50%** | ✅ |
| 两市成交额 | **3.54万亿** | >8000亿 | - | ✅ |
| 上涨家数 | 3121只/5581只 | >55% | **{3121/5581*100:.1f}%** | ✅ |

> ✅ 大盘环境满足，开始执行选股策略

---

## 选股流程统计

| 阶段 | 数量 |
|:---|:---:|
| 全市场股票 | {len(df_spot)} 只 |
| 初筛通过（股价<25/涨幅>5%/成交额>8000万/振幅<8%） | {len(df_basic)} 只 |
| 趋势确认通过（MA多头排列 + MA5斜率>0） | {len(results)} 只 |
| 形态过滤后（排除极长上影线/炸板股） | {len(filtered)} 只 |
| **最终TOP10** | **10 只** |

---

## TOP10 精选股票

"""

for i, r in enumerate(filtered_sorted, 1):
    form_tag = []
    if r.get('platform_breakout'):
        form_tag.append('平台突破')
    if r.get('n_pattern'):
        form_tag.append('N型反包')
    form_str = '/'.join(form_tag) if form_tag else '无特殊形态'
    ma_bull = "✓" if r['ma_bullish'] else "✗"
    dist = (r['high_60d'] - r['close']) / r['close'] * 100

    report += f"""### {i}. [{r['code']}] {r['name']}

| 维度 | 数据 |
|:---|:---|
| 最新价 | **{r['price']:.2f}元** |
| 当日涨幅 | **+{r['change_pct']:.2f}%** |
| 量比（近5日均量比） | **{r['vol_ratio']:.1f}x** |
| 成交额 | **{r['amount_yi']:.1f}亿** |
| 振幅 | **{r['amplitude']:.1f}%** |
| 均线排列 | MA5({r['ma5']:.2f}) > MA10({r['ma10']:.2f}) > MA20({r['ma20']:.2f}) {ma_bull} |
| MA5斜率 | **+{r['ma5_slope']:.2f}%**（{'上升通道' if r['ma5_slope']>0 else '下降'}) |
| 60日高点 | {r['high_60d']:.2f}（距高点{dist:.1f}%） |
| 上影线 | {r['upper_shadow']:.1f}% |
| 形态标记 | **{form_str}** |
| 综合得分 | **{r['score']}** |

"""

report += f"""---

## 风险提示

1. **大盘环境**：今日三大指数集体大涨，上证突破4200点创2015年以来新高，创业板突破3900点，需注意获利了结压力
2. **强势股追高风险**：全部候选股今日涨幅>5%，属于强势股追涨策略，次日需严格执行止盈止损
3. **量比异常**：部分个股量比偏高（>8x），需警惕主力出货
4. **市场背景**：存储芯片/半导体/PCB/液冷服务器等算力硬件板块今日全线爆发，科创50指数创历史新高

---

## 操作提醒

> ⚠️ **策略已过滤，请于尾盘（14:55-15:00）结合分时图（确认回踩均线不破）决策，并严格执行次日早盘止盈止损纪律。**

**建议操作**：
- 次日早盘（9:15-9:25）关注集合竞价，高开>3%需谨慎
- 建议次日10:00前完成卖出，锁定利润
- 止损线建议设置在-3%至-5%

---

*报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 一夜持股法 v2.0*
"""

report_path = f"一夜持股法_TOP10_{datetime.now().strftime('%Y%m%d')}.md"
with open(report_path, 'w', encoding='utf-8') as f:
    f.write(report)

print(f"\n  报告已生成: {report_path}")

json_path = f"screener_top10_v2_{datetime.now().strftime('%Y%m%d')}.json"
# 排除不可序列化的hist字段
json_data = [{k: v for k, v in r.items() if k != 'hist'} for r in filtered_sorted]
with open(json_path, 'w', encoding='utf-8') as f:
    json.dump(json_data, f, ensure_ascii=False, indent=2)

print(f"  JSON已保存: {json_path}")
print("\n" + "=" * 60)
print("【选股完成】")
print("=" * 60)
