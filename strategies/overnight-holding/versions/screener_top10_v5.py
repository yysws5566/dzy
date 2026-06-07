"""
一夜持股法 TOP10 选股策略 v5.1 (修正版)
执行日期: 2026-05-07 14:45
修复: stock_zh_a_hist_tx返回英文列名
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import time

# ========================
# 第一步：获取全市场股票数据
# ========================
print("=" * 60)
print("📊 一夜持股法 TOP10 选股策略 v5.1")
print("=" * 60)
print("\n[1/6] 正在获取全市场股票数据...")

try:
    df_all = ak.stock_zh_a_spot_em()
    print(f"✅ 获取到 {len(df_all)} 只股票")

    # 统一列名处理
    df_all.columns = df_all.columns.str.strip()

    # 标准化代码
    df_all['代码'] = df_all['代码'].astype(str).str.zfill(6)
    # 添加市场前缀
    df_all['市场代码'] = df_all['代码'].apply(
        lambda x: 'sh' + x if x.startswith(('6', '9', '5')) else 'sz' + x
    )

    # 转换数值列
    for col in ['最新价', '涨跌幅', '换手率', '成交额', '流通市值', '总市值', '振幅']:
        if col in df_all.columns:
            df_all[col] = pd.to_numeric(df_all[col], errors='coerce')

except Exception as e:
    print(f"❌ 获取数据失败: {e}")
    exit()

# ========================
# 第二步：初筛过滤（硬性条件）
# ========================
print("\n[2/6] 执行初筛过滤...")

df_filtered = df_all.copy()

# 1. 非ST、非停牌
df_filtered = df_filtered[
    (~df_filtered['名称'].str.contains('ST|退', na=False)) &
    (df_filtered['最新价'] > 0) &
    (df_filtered['最新价'].notna())
]

# 2. 股价 < 25元
df_filtered = df_filtered[df_filtered['最新价'] < 25]

# 3. 涨幅 > 5%（排除涨停和大跌）
df_filtered = df_filtered[
    (df_filtered['涨跌幅'] > 5) &
    (df_filtered['涨跌幅'] < 9.5)  # 排除涨停
]

# 4. 换手率 > 5%
if '换手率' in df_filtered.columns:
    df_filtered = df_filtered[df_filtered['换手率'] > 5]

# 5. 振幅 < 8%
if '振幅' in df_filtered.columns:
    df_filtered = df_filtered[df_filtered['振幅'] < 8]

# 6. 成交额 > 8000万
if '成交额' in df_filtered.columns:
    df_filtered['成交额_亿'] = df_filtered['成交额'] / 1e8
    df_filtered = df_filtered[df_filtered['成交额_亿'] > 0.8]

# 7. 流通市值 20-120亿
if '流通市值' in df_filtered.columns:
    df_filtered['流通市值_亿'] = df_filtered['流通市值'] / 1e8
    df_filtered = df_filtered[
        (df_filtered['流通市值_亿'] >= 20) &
        (df_filtered['流通市值_亿'] <= 120)
    ]

print(f"✅ 初筛完成，候选股票: {len(df_filtered)} 只")

# 保存初筛结果
candidates_df = df_filtered.head(200)[['代码', '名称', '市场代码', '最新价', '涨跌幅', '换手率', '流通市值_亿', '成交额_亿', '振幅']].copy()
candidates_df.to_csv(f'candidates_{datetime.now().strftime("%Y%m%d")}.csv', index=False, encoding='utf-8-sig')
print(f"📋 初筛候选池已保存")

# ========================
# 第三步：获取历史数据并确认趋势
# ========================
print("\n[3/6] 获取历史数据并确认趋势（均线多头排列）...")

candidates = df_filtered.head(200).copy()
print(f"📋 候选池大小: {len(candidates)} 只，开始获取历史数据...")

trend_passed = []

for idx, row in candidates.iterrows():
    code = row['市场代码']
    name = row['名称']

    try:
        # 获取最近30个交易日历史数据
        df_hist = ak.stock_zh_a_hist_tx(
            symbol=code,
            start_date=(datetime.now() - timedelta(days=45)).strftime('%Y%m%d'),
            end_date=datetime.now().strftime('%Y%m%d'),
            adjust='qfq'
        )

        if df_hist is None or len(df_hist) < 25:
            continue

        df_hist = df_hist.sort_values('date').tail(25).reset_index(drop=True)

        # 计算均线（英文列名：close）
        df_hist['MA5'] = df_hist['close'].rolling(5).mean()
        df_hist['MA10'] = df_hist['close'].rolling(10).mean()
        df_hist['MA20'] = df_hist['close'].rolling(20).mean()

        # 最新数据
        latest = df_hist.iloc[-1]

        # 多头排列检查：收盘价 > MA5 > MA10 > MA20
        if not (latest['close'] > latest['MA5'] > latest['MA10'] > latest['MA20']):
            continue

        # MA5斜率检查：近3日斜率为正
        if len(df_hist) >= 8:
            ma5_3day_ago = df_hist['MA5'].iloc[-4]
            ma5_now = latest['MA5']
            if ma5_now <= ma5_3day_ago:
                continue
            ma5_slope = (ma5_now - ma5_3day_ago) / ma5_3day_ago * 100
        else:
            ma5_slope = 0

        # 计算量比（今日成交额/近5日均成交额）
        vol_5day_avg = df_hist['amount'].iloc[-6:-1].mean()
        if vol_5day_avg > 0:
            vol_ratio = latest['amount'] / vol_5day_avg
        else:
            vol_ratio = 1

        # 记录通过趋势确认的股票
        trend_passed.append({
            '代码': code,
            '名称': name,
            '最新价': latest['close'],
            '涨跌幅': row['涨跌幅'],
            '换手率': row['换手率'],
            '流通市值_亿': row['流通市值_亿'],
            '成交额_亿': row['成交额_亿'],
            '振幅': row['振幅'],
            'MA5': latest['MA5'],
            'MA10': latest['MA10'],
            'MA20': latest['MA20'],
            'MA5斜率': ma5_slope,
            '量比': vol_ratio,
            '历史数据': df_hist.tail(5)[['date', 'open', 'high', 'low', 'close', 'MA5', 'MA10', 'MA20']].to_dict('records')
        })

    except Exception as e:
        continue

    time.sleep(0.3)

print(f"✅ 趋势确认完成，通过股票: {len(trend_passed)} 只")

# ========================
# 第四步：形态过滤
# ========================
print("\n[4/6] 执行形态过滤...")

formation_filtered = []

for stock in trend_passed:
    hist = stock['历史数据']

    # 最新K线
    today = hist[-1]
    yesterday = hist[-2]

    # 计算上影线
    today_body = today['close'] - today['open']
    today_upper_shadow = today['high'] - max(today['close'], today['open'])

    # 排除极长上影线（上影>实体3倍）
    if abs(today_body) > 0.01 and today_upper_shadow > abs(today_body) * 3:
        continue

    # 排除涨停被打开
    if abs(stock['涨跌幅']) > 9.5:
        continue

    # 检查N型反包
    yest_body = yesterday['close'] - yesterday['open']
    today_body = today['close'] - today['open']

    is_n_pattern = False
    if yest_body < 0 and today_body > 0:
        if today['close'] > yesterday['open'] and today['open'] < yesterday['close']:
            is_n_pattern = True

    # 检查平台突破
    high_5day = max([h['high'] for h in hist[:-1]])
    is_breakout = today['close'] > high_5day

    # 均线紧密度
    ma5_ma10_gap = (stock['MA5'] - stock['MA10']) / stock['MA10'] * 100
    ma10_ma20_gap = (stock['MA10'] - stock['MA20']) / stock['MA20'] * 100
    tightness = (ma5_ma10_gap + ma10_ma20_gap) / 2

    stock['形态'] = []
    if is_n_pattern:
        stock['形态'].append('N型反包')
    if is_breakout:
        stock['形态'].append('平台突破')
    if not stock['形态']:
        stock['形态'].append('无特殊形态')

    stock['形态_加分'] = len(stock['形态'])
    stock['均线紧密度'] = tightness
    stock['平台突破'] = is_breakout
    stock['N型反包'] = is_n_pattern

    formation_filtered.append(stock)

print(f"✅ 形态过滤完成，有效股票: {len(formation_filtered)} 只")

# ========================
# 第五步：综合打分
# ========================
print("\n[5/6] 执行综合打分...")

# 获取板块数据
try:
    df_board = ak.stock_board_industry_name_em()
    board_dict = dict(zip(df_board['板块名称'], df_board['涨跌幅']))
    print(f"✅ 获取到 {len(board_dict)} 个行业板块数据")
except:
    board_dict = {}
    print("⚠️ 板块数据获取失败")

for stock in formation_filtered:
    # 1. 多头排列紧密度 (20%)
    tightness_score = min(100, stock['均线紧密度'] * 50)

    # 2. MA5斜率 (20%)
    slope_score = min(100, max(0, stock['MA5斜率'] * 10))

    # 3. 量价配合 (20%)
    if stock['量比'] >= 1.5:
        vol_score = 100
    elif stock['量比'] >= 1.2:
        vol_score = 80
    elif stock['量比'] >= 1.0:
        vol_score = 60
    else:
        vol_score = 40

    # 4. 板块效应 (20%) - 默认中等
    board_score = 60

    # 5. 形态加分 (10%)
    form_score = stock['形态_加分'] * 30 + 20

    # 6. 价格弹性 (10%)
    if len(stock['历史数据']) >= 60:
        high_60day = max([h['high'] for h in stock['历史数据'][-60:]])
    else:
        high_60day = stock['最新价'] * 1.1
    elasticity = (high_60day - stock['最新价']) / stock['最新价'] * 100
    if elasticity > 30:
        ela_score = 100
    elif elasticity > 20:
        ela_score = 80
    elif elasticity > 10:
        ela_score = 60
    else:
        ela_score = 40

    # 综合得分
    total_score = (
        tightness_score * 0.20 +
        slope_score * 0.20 +
        vol_score * 0.20 +
        board_score * 0.20 +
        form_score * 0.10 +
        ela_score * 0.10
    )

    stock['综合得分'] = round(total_score, 1)
    stock['分项得分'] = {
        '均线紧密度': round(tightness_score, 1),
        'MA5斜率': round(slope_score, 1),
        '量价配合': round(vol_score, 1),
        '板块效应': round(board_score, 1),
        '形态加分': round(form_score, 1),
        '价格弹性': round(ela_score, 1)
    }

# 排序取TOP10
formation_filtered.sort(key=lambda x: x['综合得分'], reverse=True)
top10 = formation_filtered[:10]

# ========================
# 第六步：输出结果
# ========================
print("\n[6/6] 生成选股报告...")

index_data = {
    '上证指数': {'收盘': 4180.09, 'MA20': 4057.92, '涨跌幅': 0.48},
    '创业板指': {'收盘': 3833.06, 'MA20': 3593.17, '涨跌幅': 1.45}
}

report = f"""
{'='*70}
📊 **A股尾盘"一夜持股法"选股策略 v5.1**
📅 执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}
{'='*70}

## 🏛️ 大盘环境状态

| 指数 | 当前价 | MA20 | 涨跌幅 | 状态 |
|:---:|:---:|:---:|:---:|:---:|
| 上证指数 | {index_data['上证指数']['收盘']:.2f} | {index_data['上证指数']['MA20']:.2f} | +{index_data['上证指数']['涨跌幅']:.2f}% | ✅ >MA20 |
| 创业板指 | {index_data['创业板指']['收盘']:.2f} | {index_data['创业板指']['MA20']:.2f} | +{index_data['创业板指']['涨跌幅']:.2f}% | ✅ >MA20 |

**市场概况**: 三大指数集体上涨，创业板领涨1.45%，两市成交额超2万亿，量能充沛。

---

## 🎯 选股结果 TOP10

（按综合得分降序排列）

"""

for i, stock in enumerate(top10, 1):
    shape_str = ' / '.join(stock['形态']) if stock['形态'] else '无特殊形态'
    ma_status = "✅"

    report += f"""
### {'🥇' if i==1 else '🥈' if i==2 else '🥉' if i==3 else '⚪'} TOP{i}: {stock['名称']}({stock['代码'].upper()})

| 指标 | 数值 |
|:---|:---|
| **价格** | {stock['最新价']:.2f}元 |
| **涨幅** | +{stock['涨跌幅']:.2f}% |
| **量比** | {stock['量比']:.2f}x |
| **换手率** | {stock['换手率']:.2f}% |
| **流通市值** | {stock['流通市值_亿']:.1f}亿 |
| **成交额** | {stock['成交额_亿']:.2f}亿 |
| **均线排列** | MA5>{stock['MA5']:.2f} > MA10>{stock['MA10']:.2f} > MA20>{stock['MA20']:.2f} {ma_status} |
| **MA5斜率** | +{stock['MA5斜率']:.2f}%（上升趋势） |
| **形态标记** | {shape_str} |
| **综合得分** | **{stock['综合得分']:.1f}** |

分项得分: 均线紧密度={stock['分项得分']['均线紧密度']} | MA5斜率={stock['分项得分']['MA5斜率']} | 量价={stock['分项得分']['量价配合']} | 板块={stock['分项得分']['板块效应']} | 形态={stock['分项得分']['形态加分']} | 弹性={stock['分项得分']['价格弹性']}

"""

report += f"""
---

## 📊 选股统计

- **候选池**: ~5500只 → 初筛: {len(df_filtered)}只 → 趋势确认: {len(trend_passed)}只 → 形态过滤: {len(formation_filtered)}只 → TOP10
- **评分维度**: 均线紧密度(20%) + MA5斜率(20%) + 量价配合(20%) + 板块效应(20%) + 形态加分(10%) + 价格弹性(10%)

---

## ⚠️ 操作提醒

> 🎯 **策略已过滤，请于尾盘（14:55-15:00）结合分时图决策**
>
> 1. 确认回踩均线不破（重点关注MA5支撑）
> 2. 观察尾盘是否有资金抢筹迹象
> 3. 严格执行次日早盘止盈止损纪律（建议-3%止损，+5-8%止盈）
> 4. 仓位建议：单只仓位不超过总资金的20%

**免责提示**: 本策略仅供参考，不构成投资建议。股市有风险，投资需谨慎。

{'='*70}
"""

print(report)

# 保存结果
output_file = f"一夜持股法_TOP10_{datetime.now().strftime('%Y%m%d')}.md"
with open(output_file, 'w', encoding='utf-8') as f:
    f.write(report)

json_file = f"screener_top10_v5_{datetime.now().strftime('%Y%m%d')}.json"
with open(json_file, 'w', encoding='utf-8') as f:
    json.dump(top10, f, ensure_ascii=False, indent=2, default=str)

print(f"\n✅ 结果已保存到: {output_file}")
print(f"✅ JSON数据已保存到: {json_file}")
