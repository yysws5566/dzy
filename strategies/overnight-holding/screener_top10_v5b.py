"""
A股尾盘"一夜持股法"选股策略 v5.1 - 2026-05-08
核心升级：近3日强势板块过滤
- 获取近3个交易日行业板块涨跌幅
- 统计每个板块在近3日内进入涨幅前10的次数
- 近3日出现≥2次的板块 → 强势板块候选
- 只在强势板块中选择个股
数据说明：腾讯历史 amount 单位=元，成交额显示=亿元（/10000）
"""
import akshare as ak
import pandas as pd
import numpy as np
import json
import time
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

today_str = datetime.now().strftime('%Y%m%d')

# 全局重试session
session = requests.Session()
adapter = requests.adapters.HTTPAdapter(max_retries=3)
session.mount('http://', adapter)
session.mount('https://', adapter)

def retry_call(func, *args, max_retries=3, **kwargs):
    """带重试的API调用"""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  ⚠️ {func.__name__} 第{attempt+1}次失败: {e}，重试...")
                time.sleep(2)
            else:
                raise

# ============================================================
# 第一步：大盘环境检查（自带重试）
# ============================================================
print("=" * 60)
print("【大盘环境检查】")
print("=" * 60)

MAX_RETRIES = 3
for attempt in range(MAX_RETRIES):
    try:
        spot_idx = ak.stock_zh_index_spot_sina()
        sh = spot_idx[spot_idx['代码'] == 'sh000001'].iloc[0]
        cy = spot_idx[spot_idx['代码'] == 'sz399006'].iloc[0]
        break
    except Exception as e:
        if attempt < MAX_RETRIES - 1:
            print(f"  ⚠️ 网络波动，第{attempt+1}次失败: {e}")
            time.sleep(3)
        else:
            raise

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

# ============================================================
# 第二步：读取候选池（自动使用今日或最新候选池）
# ============================================================
print("=" * 60)
print("【第一步：读取候选池】")
print("=" * 60)

# 自动查找最新的候选池文件
import glob
candidate_files = sorted(glob.glob('candidates_*.csv'), reverse=True)
if len(candidate_files) > 0:
    candidate_file = candidate_files[0]
else:
    print("⛔ 未找到候选池文件，请先运行 generate_candidates.py")
    exit(1)

df = pd.read_csv(candidate_file, encoding='utf-8-sig')
print(f"使用候选池: {candidate_file}")
print(f"候选池: {len(df)} 只")

# ============================================================
# 第三步：获取近3日行业板块数据（新增核心功能）
# ============================================================
print("\n" + "=" * 60)
print("【第二步：近3日强势板块识别】")
print("=" * 60)

# 获取近3个交易日（今日实时+近2日历史）
print("  获取今日板块数据...")
today_boards = ak.stock_board_industry_name_em()
today_boards['日期'] = today_str
today_boards = today_boards[['板块名称', '板块代码', '涨跌幅', '日期']].copy()

# 近2日历史：使用东财指数日线接口估算交易日
# 先用上证指数历史反推近2个交易日
print("  反推近2个交易日...")
idx_hist = ak.stock_zh_index_daily(symbol='sh000001').tail(5)
trading_dates = idx_hist['date'].tolist()[-3:-1]  # 去掉今日(今日数据已实时获取)，取最近2个历史交易日
print(f"  近2个历史交易日: {trading_dates}")

historical_boards_list = []
# 注意：akshare 没有直接的板块历史接口，改用 stock_board_industry_name_em 只能获取今日数据
# 解决方案：使用东财的板块历史数据（ak.stock_board_industry_index_old_df 或等效方法）
# 实际可行方案：改用 stock_board_industry_name_ths() 同花顺接口，但可能不稳定
# 更可靠的方案：记录每日板块数据到本地 CSV，每次运行时读取近3日

# 尝试读取本地缓存的板块数据
import os
board_cache_dir = 'board_history'
if not os.path.exists(board_cache_dir):
    os.makedirs(board_cache_dir)

# 保存今日板块数据到缓存
cache_today = os.path.join(board_cache_dir, f'boards_{today_str}.csv')
today_boards.to_csv(cache_today, index=False, encoding='utf-8-sig')
print(f"  今日板块数据已缓存: {cache_today}")

# 读取近3日缓存（含今日）
recent_3_days = []
for days_ago in range(4):
    d = (datetime.now() - timedelta(days=days_ago)).strftime('%Y%m%d')
    fpath = os.path.join(board_cache_dir, f'boards_{d}.csv')
    if os.path.exists(fpath):
        df_day = pd.read_csv(fpath, encoding='utf-8-sig')
        recent_3_days.append(df_day)
        print(f"    读取 {d}.csv: {len(df_day)} 个板块")

# 获取近2个历史交易日（用上证指数历史推算）
print("  反推近2个历史交易日...")
idx_hist = ak.stock_zh_index_daily(symbol='sh000001').tail(10)
trading_dates = idx_hist['date'].tolist()[-3:-1]  # 去掉今日，取最近2个历史交易日
print(f"  近2个历史交易日: {trading_dates}")

# 合并近3日数据
all_boards = pd.concat(recent_3_days, ignore_index=True)

# 兜底逻辑：若缓存历史数据不足2天，尝试从东财历史数据获取板块历史
if len(recent_3_days) < 2:
    print("  ⚠️ 历史板块数据不足2天，尝试从东财获取板块历史...")
    # 东财板块指数历史可用 stock_board_industry_index_old_df
    # 但该接口需要具体板块代码，不适合批量操作
    # 改为：直接使用近5个历史交易日对应的指数交易日来找缓存
    for td in trading_dates:
        td_str = td.replace('-', '') if isinstance(td, str) else td
        # 尝试用其他方式补充（如果有的话）
        pass

    # 最终兜底：使用今日数据复制作为历史代理（仅首次）
    if len(recent_3_days) == 1:
        # 只用今日+前1天复制（近3日需至少2天数据）
        all_boards = pd.concat([today_boards, today_boards.copy(), today_boards.copy()], ignore_index=True)
        print("  兜底：复制今日数据作为近3日历史（历史数据将在下次运行时积累）")
    elif len(recent_3_days) == 0:
        all_boards = pd.concat([today_boards, today_boards.copy(), today_boards.copy()], ignore_index=True)
        print("  兜底：仅用今日数据复制（历史数据将在下次运行时积累）")

# 统计各板块近3日的表现
print("\n  统计各板块近3日进入涨幅前10的次数...")
board_stats = {}
for _, row in all_boards.iterrows():
    name = row['板块名称']
    chg = float(row['涨跌幅'])
    date = str(row.get('日期', ''))
    if name not in board_stats:
        board_stats[name] = {
            'dates': [],
            'top10_count': 0,
            'avg_chg': [],
            'total_chg': 0,
        }
    board_stats[name]['dates'].append(date)
    board_stats[name]['avg_chg'].append(chg)
    board_stats[name]['total_chg'] += chg

# 判断各板块近3日是否进入涨幅前10
for name in board_stats:
    dates = list(set(board_stats[name]['dates']))
    cnt = 0
    for d in dates:
        day_df = all_boards[all_boards['日期'].astype(str) == d].sort_values('涨跌幅', ascending=False)
        top10_names = day_df.head(10)['板块名称'].tolist()
        if name in top10_names:
            cnt += 1
    board_stats[name]['top10_count'] = cnt
    board_stats[name]['avg_chg'] = np.mean(board_stats[name]['avg_chg'])

# 强势板块判断逻辑
# 历史数据充足（≥2天真实数据）→ 近3日出现≥2次涨幅前10
# 历史数据不足（只有今日兜底数据）→ 改用"日均涨幅排序"，取前5
history_days = len(recent_3_days)  # 实际有几天历史数据
print(f"  实际历史数据天数: {history_days} 天")

if history_days >= 2:
    # 正常模式：近3日至少2次进入涨幅前10
    strong_boards = {name: stats for name, stats in board_stats.items() if stats['top10_count'] >= 2}
    print(f"  【正常模式】近3日强势板块（出现≥2次涨幅前10）: {len(strong_boards)} 个")
else:
    # 首次运行兜底模式：按日均涨幅排序，取前8
    print(f"  【兜底模式】历史数据不足，按日均涨幅排序取前8")
    sorted_by_avg = sorted(board_stats.items(), key=lambda x: x[1]['avg_chg'], reverse=True)
    strong_boards = dict(sorted_by_avg[:8])
    print(f"  兜底强势板块: {[name for name, _ in sorted_by_avg[:8]]}")

if len(strong_boards) > 0:
    # 按总涨幅排序，取前10
    sorted_strong = sorted(strong_boards.items(), key=lambda x: x[1]['total_chg'], reverse=True)
    print(f"\n  【近3日强势板块TOP10】")
    print(f"  {'板块名称':18s}  {'前10次数':>6s}  {'3日总涨幅':>8s}  {'日均涨幅':>8s}")
    print(f"  {'-'*50}")
    for i, (name, stats) in enumerate(sorted_strong[:10], 1):
        print(f"  #{i:02d} {name:16s}  {stats['top10_count']}次       {stats['total_chg']:+6.2f}%   {stats['avg_chg']:+5.2f}%")
    strong_board_names = [name for name, _ in sorted_strong]
else:
    print("  ⚠️ 未找到近3日强势板块，使用今日涨幅前5作为备选")
    sorted_today = today_boards.sort_values('涨跌幅', ascending=False)
    strong_board_names = sorted_today.head(5)['板块名称'].tolist()
    strong_boards = {}

strong_board_set = set(strong_board_names)

# ============================================================
# 第四步：获取强势板块的成分股
# ============================================================
print("\n" + "=" * 60)
print("【第三步：获取强势板块成分股】")
print("=" * 60)

# 获取强势板块的板块代码
boards_df = ak.stock_board_industry_name_em()
strong_board_codes = []
for name in strong_board_names:
    matches = boards_df[boards_df['板块名称'] == name]
    if len(matches) > 0:
        strong_board_codes.append(matches.iloc[0]['板块代码'])

print(f"  强势板块数量: {len(strong_board_codes)}")
print(f"  板块名称: {', '.join(strong_board_names[:8])}")

# 并行获取成分股
print(f"\n  并行获取 {len(strong_board_codes)} 个强势板块的成分股...")
t_cons = time.time()
strong_stocks = set()

def fetch_board_stocks(board_code):
    try:
        cons = ak.stock_board_industry_cons_em(symbol=board_code)
        # 标准化：去除sh/sz/bj前缀，转6位字符串
        codes = []
        for code in cons['代码'].tolist():
            code_str = str(code).strip().upper()
            code_str = code_str.replace('SH', '').replace('SZ', '').replace('BJ', '')
            codes.append(code_str)
        return set(codes)
    except Exception:
        return set()

with ThreadPoolExecutor(max_workers=5) as pool:
    futures = {pool.submit(fetch_board_stocks, bc): bc for bc in strong_board_codes}
    for future in as_completed(futures):
        board_code = futures[future]
        try:
            stocks = future.result()
            # 找板块名称
            board_name = ''
            for name in strong_board_names:
                matches = boards_df[boards_df['板块名称'] == name]
                if len(matches) > 0 and matches.iloc[0]['板块代码'] == board_code:
                    board_name = name
                    break
            print(f"    {board_name} ({board_code}): {len(stocks)} 只成分股")
            strong_stocks.update(stocks)
        except Exception as e:
            print(f"    {board_code} 获取失败: {e}")

elapsed_cons = time.time() - t_cons
print(f"\n  强势板块合计成分股去重后: {len(strong_stocks)} 只")
print(f"  板块成分获取耗时: {elapsed_cons:.1f}s")

# ============================================================
# 第五步：获取今日历史数据 & 趋势确认
# ============================================================
print("\n" + "=" * 60)
print("【第四步：获取历史数据 & 趋势确认】")
print("=" * 60)

def get_tx_hist(code, days=30):
    """获取腾讯股票历史数据"""
    try:
        # 标准化code为字符串（前缀+6位纯数字）
        code_str = str(code).strip()
        # 纯6位数字 → 判断沪/深前缀
        if code_str.isdigit() and len(code_str) == 6:
            if code_str.startswith('6') or code_str.startswith('9'):
                tx_code = 'sh' + code_str   # 沪市: 600xxx, 601xxx, 688xxx
            else:
                tx_code = 'sz' + code_str   # 深市: 000xxx, 002xxx, 300xxx
        # 已有前缀
        elif code_str.startswith('sh') or code_str.startswith('sz') or code_str.startswith('bj'):
            tx_code = code_str
        else:
            tx_code = 'sz' + code_str  # 兜底当深市
        end = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')
        df_h = ak.stock_zh_a_hist_tx(symbol=tx_code, start_date=start, end_date=end)
        if df_h is None or len(df_h) == 0:
            return None
        return df_h.tail(days)
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

    bull = current_close > ma5 > ma10 > ma20
    slope_positive = ma5 > ma5_3d_ago

    if bull and slope_positive:
        ma5_slope = (ma5 - ma5_3d_ago) / ma5_3d_ago * 100
        prev_close = close[-2] if len(close) >= 2 else current_close
        prev_open = hist['open'].values[-2] if len(hist) >= 2 else current_close
        today_open = hist['open'].values[-1]
        today_high = hist['high'].values[-1]
        today_low = hist['low'].values[-1]

        prev_is_yin = prev_close < prev_open
        today_cover = current_close > prev_open and current_close > prev_close
        n_type = prev_is_yin and today_cover

        recent_high = max(hist['high'].values[-7:-1]) if len(hist) >= 7 else today_high
        platform_break = today_high > recent_high

        avg_5d_amt = hist['amount'].tail(5).mean()
        today_amt = hist['amount'].values[-1]
        vol_ratio = today_amt / avg_5d_amt if avg_5d_amt > 0 else 1.0

        gap_5_10 = (ma5 - ma10) / ma10 * 100
        gap_10_20 = (ma10 - ma20) / ma20 * 100

        candidates.append({
            '代码': code,
            '名称': name,
            '最新价': current_close,
            '涨跌幅': (current_close - prev_close) / prev_close * 100,
            '成交额': today_amt,
            '成交额_亿': today_amt / 10000,
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
trend_count = len(candidates)
print(f"趋势确认后候选: {trend_count} 只 (耗时 {elapsed:.0f}s)")

if len(candidates) == 0:
    print("\n⛔ 没有符合条件的股票。")
    exit(0)

# ============================================================
# 第六步：板块过滤 - 只保留属于强势板块的个股
# ============================================================
print("\n" + "=" * 60)
print("【第五步：板块过滤 - 近3日强势板块】")
print("=" * 60)

# 标记候选股是否属于强势板块
in_strong_count = 0
for c in candidates:
    raw_code = str(c['代码']).upper().replace('SH', '').replace('SZ', '').replace('BJ', '')
    if raw_code in strong_stocks:
        c['属于强势板块'] = True
        in_strong_count += 1
    else:
        c['属于强势板块'] = False

print(f"  候选股中属于近3日强势板块: {in_strong_count}/{len(candidates)} 只")
print(f"  强势板块: {', '.join(strong_board_names[:5])}")

# 板块过滤策略：
# 有交集 → 严格过滤（只保留强势板块内的个股）
# 无交集 → 宽松模式（保留所有趋势股，打分时加权）
if in_strong_count > 0:
    # 严格模式
    strong_filtered = [c for c in candidates if c['属于强势板块']]
    filter_mode = "严格模式"
else:
    # 宽松模式：保留所有趋势股，打分时给强势板块成分股加权
    strong_filtered = candidates[:]
    filter_mode = "宽松模式"
    print(f"\n  ⚠️ 强势板块交集为0，切换为【宽松模式】")
    print(f"     保留所有{len(strong_filtered)}只趋势股，打分时对强势板块成分股额外加权")

print(f"\n  板块过滤后: {len(strong_filtered)} 只 ({filter_mode})")

# ============================================================
# 第七步：形态过滤
# ============================================================
print("\n" + "=" * 60)
print("【第六步：形态筛选】")
print("=" * 60)

filtered = []
for c in strong_filtered:
    code = c['代码']
    name = c['名称']
    today_high = c['today_high']
    today_open = c['today_open']
    current_close = c['最新价']

    upper_shadow = (today_high - max(current_close, today_open)) / today_open * 100
    body = abs(current_close - today_open) / today_open * 100

    if upper_shadow > body * 3 and upper_shadow > 3:
        print(f"  排除 {code} {name}: 极长上影线({upper_shadow:.1f}%)")
        continue

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

# ============================================================
# 第八步：综合打分 v5.1（含近3日强势板块过滤）
# ============================================================
print("\n" + "=" * 60)
print("【第七步：综合打分（TOP10）- v5.1近3日强势板块】")
print("=" * 60)

print("  打分权重：")
print("    均线紧密度(20%) + MA5斜率(20%) + 量价配合(20%)")
print("    + 形态加分(10%) + 价格弹性(10%) + 活跃度(10%) + 涨幅合理性(10%)")
print("    + 板块效应：近3日强势板块 × 持续强势强度加分")
print(f"\n  【近3日强势板块详情】（已严格过滤）:")
for i, (name, stats) in enumerate(sorted_strong[:8], 1):
    print(f"    #{i} {name}: 前10出现{stats['top10_count']}次, 3日总涨幅{stats['total_chg']:+5.2f}%")

for c in filtered:
    score = 0.0
    # 均线紧密度 20%
    gap_score = min(c['gap_5_10'] + c['gap_10_20'], 10) / 10 * 20
    score += gap_score
    # MA5斜率 20%
    ma5_score = min(c['MA5斜率'] * 5, 20) if c['MA5斜率'] > 0 else 0
    score += ma5_score
    # 量价配合 20%
    vol_score = min(c['量比代理'] / 2, 1.0) * 20
    score += vol_score
    # 形态加分 10%
    shape_score = (5 if c['平台突破'] else 0) + (5 if c['N型反包'] else 0)
    score += shape_score
    # 价格弹性 10%
    recent_high = c.get('today_high', c['最新价'])
    if recent_high > 0:
        distance = (recent_high - c['最新价']) / recent_high * 100
        elastic_score = max(0, min(distance / 10, 1.0)) * 10
        score += elastic_score
    # 活跃度 10%
    max_amt = max([x['成交额_亿'] for x in filtered])
    active_score = (c['成交额_亿'] / max_amt) * 10 if max_amt > 0 else 0
    score += active_score
    # 涨幅合理性 10%
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
    # 强势板块加分（已过滤，此处额外加分强化主线）
    # 宽松模式：所有候选股在打分时检测是否属于强势板块成分股
    board_bonus = 0
    raw_code = str(c['代码']).upper().replace('SH', '').replace('SZ', '').replace('BJ', '')
    is_in_strong = raw_code in strong_stocks
    if is_in_strong:
        # 属于强势板块：查找该板块近3日出现次数，加分
        for name in strong_board_names[:8]:
            stats = board_stats.get(name, {})
            cnt = stats.get('top10_count', 1)
            board_bonus = min(cnt * 5, 15)  # 3次=15分, 2次=10分, 1次=5分
            break
    c['板块加分'] = board_bonus
    c['属于强势板块'] = is_in_strong
    score += c['板块加分']
    c['综合得分'] = round(score, 1)

filtered.sort(key=lambda x: x['综合得分'], reverse=True)
top10 = filtered[:10]

# 输出
print("\n" + "=" * 60)
print("【选股结果 - TOP10】")
print("=" * 60)
print(f"\n【大盘环境状态】")
print(f"  上证指数：{sh_price:.2f} / MA20 {sh_ma20:.2f} / 涨跌幅 {sh_chg:+.3f}%")
print(f"  创业板指：{cy_price:.2f} / MA20 {cy_ma20:.2f} / 涨跌幅 {cy_chg:+.3f}%")
print(f"  环境判断：✅ 满足")
print(f"\n【近3日强势板块】")
for i, (name, stats) in enumerate(sorted_strong[:8], 1):
    print(f"  #{i} {name:14s} 出现{stats['top10_count']}次  3日总涨幅{stats['total_chg']:+6.2f}%")

print("\n" + "=" * 60)
print("【选股结果（按综合得分降序排列）】")
print("=" * 60)

for rank, c in enumerate(top10, 1):
    ma_str = f"MA5={c['MA5']:.2f} > MA10={c['MA10']:.2f} > MA20={c['MA20']:.2f} ✓"
    shape_marks = []
    if c['平台突破']:
        shape_marks.append("平台突破")
    if c['N型反包']:
        shape_marks.append("N型反包")
    shape_str = "/".join(shape_marks) if shape_marks else "无特殊形态"
    vol_str = f"{c['量比代理']:.2f}x"
    sector_str = f"强势板块+{c['板块加分']}分" if c['板块加分'] > 0 else "—"

    print()
    print(f"#{rank} [{c['代码']}] {c['名称']}")
    print(f"  价格：{c['最新价']:.2f}元 | 涨幅：{c['涨跌幅']:+.2f}% | 量比：{vol_str}")
    print(f"  成交额：{c['成交额_亿']:.2f}亿")
    print(f"  均线排列：{ma_str}")
    print(f"  MA5斜率：{c['MA5斜率']:+.3f}%")
    print(f"  形态标记：{shape_str}")
    print(f"  板块效应：{sector_str}")
    print(f"  综合得分：{c['综合得分']:.1f}")

print("\n" + "=" * 60)
print(f"  【v5.1 升级说明】")
print(f"    板块过滤：近3日强势板块（≥2次进涨幅前10），而非仅今日热点")
print(f"    过滤模式：{filter_mode}（交集{int(in_strong_count)}只）")
print(f"    数据缓存：每日自动保存板块数据到 board_history/ 目录，3日后积累完整")
print(f"    策略逻辑：找持续3日的主线板块，而非追单日热点")
print()
print("\n⚠️  策略已过滤，请于尾盘（14:55-15:00）结合分时图决策，")
print("    并严格执行次日早盘止盈止损纪律。")
print(f"\n执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (v5.1 近3日强势板块)")
print("=" * 60)

# 保存结果
strong_boards_detail = [
    {'板块名称': name, '前10次数': stats['top10_count'],
     '3日总涨幅': round(stats['total_chg'], 2), '日均涨幅': round(stats['avg_chg'], 2)}
    for name, stats in sorted_strong[:10]
]

result_data = {
    '执行时间': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    '模式': 'v5.1 近3日强势板块过滤',
    '大盘环境': {
        '上证指数': {'最新价': sh_price, 'MA20': sh_ma20, '涨跌幅': sh_chg},
        '创业板指': {'最新价': cy_price, 'MA20': cy_ma20, '涨跌幅': cy_chg}
    },
    '近3日强势板块': strong_boards_detail,
    '候选数量': len(candidates),
    '强势板块过滤后数量': len(strong_filtered),
    '形态过滤后数量': len(filtered),
    'top10': top10
}
with open(f'screener_top10_v5b_{today_str}.json', 'w', encoding='utf-8') as f:
    json.dump(result_data, f, ensure_ascii=False, indent=2, default=str)
print(f"\n结果已保存至 screener_top10_v5b_{today_str}.json")
