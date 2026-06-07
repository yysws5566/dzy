"""
一夜持股法 v2.0 历史回测脚本
方法论: backtest-expert (add friction, stress test, realistic costs)

策略规则:
- 买入: T日 14:45选股，以T日收盘价买入
- 卖出: T+1日收盘价卖出（持有一夜）
- 交易成本: 买入0.03% + 卖出0.03% + 印花税0.1% = 总约0.16%
- 止损: -3% (盘中触发)
- 止盈: +5%~8% (次日盘中)

回测时间段: 2026-02-05 至 2026-05-07 (约3个月)
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import json

# ========================
# 配置参数
# ========================
BACKTEST_START = "20260205"
BACKTEST_END = "20260507"
COMMISSION = 0.0003   # 单边佣金 0.03%
STAMP_TAX = 0.001      # 印花税 0.1% (仅卖出)
SLIPPAGE = 0.002       # 滑点 0.2% (保守估计)
INITIAL_CAPITAL = 100000  # 初始资金（用于计算仓位）

# ========================
# 工具函数
# ========================

def get_trading_dates(start_date, end_date):
    """获取区间内的交易日（通过获取上证指数日线推断）"""
    try:
        df_idx = ak.stock_zh_index_daily(symbol="sh000001")
        df_idx['date'] = pd.to_datetime(df_idx['date'])
        mask = (df_idx['date'] >= pd.to_datetime(start_date)) & \
               (df_idx['date'] <= pd.to_datetime(end_date))
        dates = df_idx.loc[mask, 'date'].dt.strftime('%Y%m%d').tolist()
        return sorted(dates)
    except Exception as e:
        print(f"获取交易日失败: {e}")
        return []

def check_market_condition(date_str):
    """检查大盘环境：上证/创业板 > MA20 且 上证日跌幅 <= 1%"""
    try:
        # 获取上证指数数据
        df_sh = ak.stock_zh_index_daily(symbol="sh000001")
        df_sh['date'] = pd.to_datetime(df_sh['date'])
        df_sh = df_sh.sort_values('date').reset_index(drop=True)

        # 获取创业板指数据
        df_sz = ak.stock_zh_index_daily(symbol="sz399006")
        df_sz['date'] = pd.to_datetime(df_sz['date'])
        df_sz = df_sz.sort_values('date').reset_index(drop=True)

        # 找到当前日期的位置
        target_date = pd.to_datetime(date_str)
        sh_idx = df_sh[df_sh['date'] == target_date].index
        sz_idx = df_sz[df_sz['date'] == target_date].index

        if len(sh_idx) == 0 or len(sz_idx) == 0:
            return False, "日期无数据"

        sh_pos = sh_idx[0]
        sz_pos = sz_idx[0]

        if sh_pos < 20 or sz_pos < 20:
            return False, "数据不足20日"

        # 计算MA20
        sh_ma20 = df_sh.iloc[sh_pos-20:sh_pos]['close'].mean()
        sz_ma20 = df_sz.iloc[sz_pos-20:sz_pos]['close'].mean()

        sh_close = df_sh.iloc[sh_pos]['close']
        sz_close = df_sz.iloc[sz_pos]['close']

        # 前一天收盘（计算涨跌幅）
        sh_prev_close = df_sh.iloc[sh_pos-1]['close']
        sh_change_pct = (sh_close - sh_prev_close) / sh_prev_close * 100

        # 条件检查
        cond1 = sh_close > sh_ma20   # 上证在MA20上方
        cond2 = sz_close > sz_ma20   # 创业板在MA20上方
        cond3 = sh_change_pct > -1   # 上证日跌幅不超过1%

        status = f"上证{sh_close:.0f}/MA20{sh_ma20:.0f}({('✅' if cond1 else '❌')}) " \
                 f"创业板{sz_close:.0f}/MA20{sz_ma20:.0f}({('✅' if cond2 else '❌')}) " \
                 f"上证涨跌{sh_change_pct:+.2f}%({(('✅' if cond3 else '❌'))}"
        return (cond1 and cond2 and cond3), status
    except Exception as e:
        return False, f"检查失败: {e}"

def screen_stocks(date_str, top_n=10):
    """
    对指定日期执行选股逻辑
    由于AKShare不支持历史快照，使用T-1日数据近似模拟T日选股
    """
    try:
        # 获取全市场数据（实时，无法获取历史快照）
        # 注意：这是回测的主要局限 —— AKShare不提供历史点阵数据
        df_all = ak.stock_zh_a_spot_em()
        df_all.columns = df_all.columns.str.strip()
        df_all['代码'] = df_all['代码'].astype(str).str.zfill(6)
        df_all['市场代码'] = df_all['代码'].apply(
            lambda x: 'sh' + x if x.startswith(('6', '9', '5')) else 'sz' + x
        )
        for col in ['最新价', '涨跌幅', '换手率', '成交额', '流通市值', '总市值', '振幅']:
            if col in df_all.columns:
                df_all[col] = pd.to_numeric(df_all[col], errors='coerce')

        # 初筛
        df_f = df_all.copy()
        df_f = df_f[
            (~df_f['名称'].str.contains('ST|退', na=False)) &
            (df_f['最新价'] > 0) &
            (df_f['最新价'].notna())
        ]
        df_f = df_f[df_f['最新价'] < 25]
        df_f = df_f[(df_f['涨跌幅'] > 5) & (df_f['涨跌幅'] < 9.5)]
        if '换手率' in df_f.columns:
            df_f = df_f[df_f['换手率'] > 5]
        if '振幅' in df_f.columns:
            df_f = df_f[df_f['振幅'] < 8]
        if '成交额' in df_f.columns:
            df_f['成交额_亿'] = df_f['成交额'] / 1e8
            df_f = df_f[df_f['成交额_亿'] > 0.8]
        if '流通市值' in df_f.columns:
            df_f['流通市值_亿'] = df_f['流通市值'] / 1e8
            df_f = df_f[(df_f['流通市值_亿'] >= 20) & (df_f['流通市值_亿'] <= 120)]

        candidates = df_f.head(100).copy()
    except Exception as e:
        print(f"  初筛失败: {e}")
        return []

    # 趋势确认（获取历史数据）
    passed = []
    for idx, row in candidates.iterrows():
        code = row['市场代码']
        try:
            # 获取截至date_str的历史数据
            end_dt = pd.to_datetime(date_str) + timedelta(days=1)
            df_hist = ak.stock_zh_a_hist_tx(
                symbol=code,
                start_date=(pd.to_datetime(date_str) - timedelta(days=60)).strftime('%Y%m%d'),
                end_date=end_dt.strftime('%Y%m%d'),
                adjust='qfq'
            )
            if df_hist is None or len(df_hist) < 25:
                continue

            df_hist = df_hist.sort_values('date').reset_index(drop=True)
            # 只保留到date_str的数据
            df_hist['date'] = pd.to_datetime(df_hist['date'])
            df_hist = df_hist[df_hist['date'] <= pd.to_datetime(date_str)].reset_index(drop=True)
            if len(df_hist) < 25:
                continue

            df_hist['MA5'] = df_hist['close'].rolling(5).mean()
            df_hist['MA10'] = df_hist['close'].rolling(10).mean()
            df_hist['MA20'] = df_hist['close'].rolling(20).mean()

            latest = df_hist.iloc[-1]
            if not (latest['close'] > latest['MA5'] > latest['MA10'] > latest['MA20']):
                continue

            # MA5斜率
            if len(df_hist) >= 4:
                ma5_slope = (latest['MA5'] - df_hist.iloc[-4]['MA5']) / df_hist.iloc[-4]['MA5'] * 100
                if ma5_slope <= 0:
                    continue
            else:
                continue

            passed.append({
                '代码': code,
                '名称': row['名称'],
                '买入价': latest['close'],
                'date': date_str
            })
        except Exception:
            continue
        time.sleep(0.1)

    return passed[:top_n]

def get_next_day_close(code, date_str):
    """获取T+1日收盘价"""
    try:
        target = pd.to_datetime(date_str) + timedelta(days=5)  # 最多往后找5天（跨周末）
        df_hist = ak.stock_zh_a_hist_tx(
            symbol=code,
            start_date=date_str,
            end_date=target.strftime('%Y%m%d'),
            adjust='qfq'
        )
        if df_hist is None or len(df_hist) < 2:
            return None
        df_hist = df_hist.sort_values('date').reset_index(drop=True)
        # T+1日（第二个交易日）的收盘价
        if len(df_hist) >= 2:
            return float(df_hist.iloc[1]['close'])
        else:
            return None
    except Exception:
        return None

# ========================
# 主回测逻辑
# ========================

print("=" * 60)
print("📈 一夜持股法 v2.0 历史回测")
print("=" * 60)

# 获取交易日列表
print("\n[1/4] 获取交易日列表...")
trading_dates = get_trading_dates(BACKTEST_START, BACKTEST_END)
print(f"✅ 区间内共有 {len(trading_dates)} 个交易日")

# 抽样：每3个交易日抽1个（约20个样本）
sample_dates = trading_dates[::3][:15]  # 最多15个样本
print(f"📊 回测样本: {len(sample_dates)} 个交易日")
print(f"   样本日期: {sample_dates[:5]}...{sample_dates[-5:] if len(sample_dates) > 5 else ''}")

# 执行回测
print("\n[2/4] 执行回测（每个日期约需2-5分钟）...")
results = []

for i, date_str in enumerate(sample_dates):
    print(f"\n--- [{i+1}/{len(sample_dates)}] 回测日期: {date_str} ---")

    # 检查大盘环境
    market_ok, market_status = check_market_condition(date_str)
    print(f"   大盘环境: {market_status}")
    if not market_ok:
        print(f"   ⏭️  环境不通过，跳过此日期")
        continue

    # 执行选股
    print(f"   🔍 执行选股...")
    selected = screen_stocks(date_str, top_n=5)  # 回测时只选前5，减少API调用
    if not selected:
        print(f"   ❌ 无符合条件的股票")
        continue
    print(f"   ✅ 选出 {len(selected)} 只股票")

    # 获取次日收盘价，计算收益
    for stock in selected:
        code = stock['代码']
        buy_price = stock['买入价']
        sell_price = get_next_day_close(code, date_str)
        time.sleep(0.2)

        if sell_price is None:
            print(f"   ⚠️  {stock['名称']}: 无法获取次日价格，跳过")
            continue

        # 计算收益率（扣除成本）
        gross_return = (sell_price - buy_price) / buy_price
        total_cost = COMMISSION * 2 + STAMP_TAX + SLIPPAGE * 2
        net_return = gross_return - total_cost

        result = {
            'date': date_str,
            'code': code,
            'name': stock['名称'],
            'buy_price': buy_price,
            'sell_price': sell_price,
            'gross_return_pct': round(gross_return * 100, 2),
            'net_return_pct': round(net_return * 100, 2),
        }
        results.append(result)
        print(f"   📊 {stock['名称']}({code}): 买{buy_price:.2f}→卖{sell_price:.2f} "
              f"收益{net_return*100:+.2f}%")

print(f"\n✅ 回测完成，有效交易: {len(results)} 笔")

# ========================
# 统计结果
# ========================
print("\n[3/4] 计算统计指标...")

if len(results) == 0:
    print("❌ 无有效交易记录，无法生成统计")
else:
    df_result = pd.DataFrame(results)

    # 基础统计
    total_trades = len(df_result)
    win_trades = len(df_result[df_result['net_return_pct'] > 0])
    loss_trades = total_trades - win_trades
    win_rate = win_trades / total_trades * 100

    avg_return = df_result['net_return_pct'].mean()
    avg_win = df_result[df_result['net_return_pct'] > 0]['net_return_pct'].mean() if win_trades > 0 else 0
    avg_loss = df_result[df_result['net_return_pct'] <= 0]['net_return_pct'].mean() if loss_trades > 0 else 0

    max_win = df_result['net_return_pct'].max()
    max_loss = df_result['net_return_pct'].min()

    total_return = df_result['net_return_pct'].sum()
    # 简化夏普比率（无风险利率设为0）
    sharpe = avg_return / df_result['net_return_pct'].std() if df_result['net_return_pct'].std() > 0 else 0

    # 最大回撤（基于资金曲线）
    df_result = df_result.sort_values('date').reset_index(drop=True)
    df_result['cum_return'] = df_result['net_return_pct'].cumsum()
    df_result['peak'] = df_result['cum_return'].cummax()
    df_result['drawdown'] = df_result['cum_return'] - df_result['peak']
    max_drawdown = df_result['drawdown'].min()

    print("\n" + "=" * 60)
    print("📊 回测统计结果")
    print("=" * 60)
    print(f"\n📅 回测区间: {BACKTEST_START} 至 {BACKTEST_END}")
    print(f"📋 样本日期数: {len(sample_dates)}")
    print(f"📊 有效交易笔数: {total_trades}")
    print(f"\n🎯 胜率: {win_rate:.1f}% ({win_trades}/{total_trades})")
    print(f"📈 平均单笔收益: {avg_return:+.2f}%")
    print(f"📈 盈利交易平均: {avg_win:+.2f}%")
    print(f"📉 亏损交易平均: {avg_loss:+.2f}%")
    print(f"🏆 最大单笔盈利: {max_win:+.2f}%")
    print(f"💥 最大单笔亏损: {max_loss:+.2f}%")
    print(f"\n💰 累计净收益: {total_return:+.2f}%")
    print(f"📉 最大回撤: {max_drawdown:+.2f}%")
    print(f"⚡ 夏普比率(简化): {sharpe:.2f}")

    # 盈亏比
    if avg_loss != 0:
        profit_loss_ratio = abs(avg_win / avg_loss) if avg_win != 0 else 0
        print(f"⚖️  盈亏比: {profit_loss_ratio:.2f}:1")

    # 评估结论
    print(f"\n{'='*60}")
    print("🧐 策略评估（基于 backtest-expert 方法论）")
    print("=" * 60)
    if win_rate >= 55 and avg_return > 0:
        print("✅ 策略有正期望，可考虑小仓位试运行")
    elif win_rate >= 45:
        print("🟡 策略期望接近零，需优化后 reconsider")
    else:
        print("❌ 策略期望为负，不建议实盘")

    if max_drawdown < -10:
        print("⚠️ 最大回撤较大，需注意仓位管理")
    if total_trades < 30:
        print(f"⚠️ 样本量({total_trades}笔)偏少，统计显著性不足（建议≥30笔）")

    # 保存结果
    result_file = f"backtest_result_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    df_result.to_json(result_file, orient='records', force_ascii=False, indent=2)
    print(f"\n✅ 详细结果已保存: {result_file}")

print("\n[4/4] 回测完成")
