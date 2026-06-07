# -*- coding: utf-8 -*-
"""
临时选股执行脚本 - 强制使用新浪数据源
"""
import sys, os, warnings, time, json
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
warnings.filterwarnings("ignore")

import pandas as pd
from datetime import datetime
from data_fetcher import SinaSource, get_trade_date

KLINE_DAYS = 30
API_DELAY = 0.15

trade_date = get_trade_date()
now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

print(f"高胜率隔夜策略选股 v2.0 (新浪数据源)")
print(f"交易日期: {trade_date}")
print(f"运行时间: {now_str}")
print("=" * 60)

# ===== Step 1: 获取全市场行情 =====
print("\n[1/3] 获取全市场实时行情（新浪财经）...")
df = SinaSource.get_all_stocks()
if df.empty:
    print("  X 无法获取行情数据")
    sys.exit(1)
print(f"  OK 获取到 {len(df)} 只股票")

# ===== Step 2: 预筛选 =====
print("\n[2/3] 实时行情预筛选...")
total = len(df)

required_cols = ["code", "name", "close", "pct_chg", "amount", "circ_mv", "turnover", "high", "volume"]
missing = [c for c in required_cols if c not in df.columns]
if missing:
    print(f"  X 缺少字段: {missing}")
    sys.exit(1)

df = df[~df["name"].str.contains("ST|st|退市", case=False, na=False)]
print(f"  排除ST/退市后: {len(df)}")

df = df[~df["code"].str.startswith(("9", "8", "4"))]
df = df[~df["name"].str.startswith(("N", "C", "U", "W"))]
print(f"  排除北交所/新股后: {len(df)}")

df = df[(df["circ_mv"] >= 5e8) & (df["circ_mv"] <= 1.5e10)]
print(f"  流通市值50-150亿: {len(df)}")

df = df[(df["close"] > 0) & (df["close"] < 20)]
print(f"  股价<20元: {len(df)}")

df = df[(df["pct_chg"] >= 0) & (df["pct_chg"] <= 5)]
print(f"  涨幅0-5%: {len(df)}")

df = df[df["close"] < df["high"]]
print(f"  未涨停: {len(df)}")

df = df[(df["turnover"] >= 1.5) & (df["turnover"] <= 16)]
print(f"  换手1.5-16%: {len(df)}")

df = df[df["amount"] >= 2e8]
print(f"  成交额>=2亿: {len(df)}")

if df.empty:
    print("\n  预筛选后无股票")
    sys.exit(0)

print(f"\n  OK 预筛选通过: {len(df)} 只（从 {total} 只中）")

# ===== Step 3: K线验证 =====
print(f"\n[3/3] K线验证（{len(df)}只，新浪数据源）...")
results = []
fail = {"kline": 0, "amt20": 0, "ma20": 0, "bull": 0, "shadow": 0, "limit": 0, "drop7": 0, "pass": 0}
cnt = 0

for _, row in df.iterrows():
    code = str(row["code"])
    name = row["name"]
    cnt += 1

    kline = SinaSource.get_kline(code, KLINE_DAYS)
    if kline.empty or len(kline) < 20:
        fail["kline"] += 1
        time.sleep(API_DELAY)
        continue

    closes = kline["close"].dropna().tolist()
    opens_list = kline["open"].dropna().tolist()
    highs_list = kline["high"].dropna().tolist()
    lows_list = kline["low"].dropna().tolist()
    volumes = kline["volume"].dropna().tolist()

    if len(closes) < 20 or any(c == 0 for c in closes[-20:]):
        fail["kline"] += 1
        time.sleep(API_DELAY)
        continue

    # 20日均成交额>=3亿
    daily_amts = []
    for j in range(len(closes) - 20, len(closes)):
        if "amount" in kline.columns and pd.notna(kline["amount"].iloc[j]):
            daily_amts.append(float(kline["amount"].iloc[j]))
        else:
            daily_amts.append(volumes[j] * (opens_list[j] + closes[j]) / 2)
    avg_amt = sum(daily_amts) / len(daily_amts) if daily_amts else 0
    if avg_amt < 3e8:
        fail["amt20"] += 1
        time.sleep(API_DELAY)
        continue

    # 站20日线>=15天
    ma20_list = [sum(closes[j-20:j])/20 for j in range(20, len(closes)+1)]
    above = 0
    check = min(20, len(ma20_list))
    for j in range(check):
        ci = len(closes) - check + j
        mi = len(ma20_list) - check + j
        if ci >= 0 and mi >= 0 and closes[ci] >= ma20_list[mi]:
            above += 1
    if above < 15:
        fail["ma20"] += 1
        time.sleep(API_DELAY)
        continue

    # 多头排列
    if len(closes) >= 20:
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma20_now = sum(closes[-20:]) / 20
        if not (ma5 > ma10 > ma20_now):
            fail["bull"] += 1
            time.sleep(API_DELAY)
            continue
    else:
        fail["bull"] += 1
        time.sleep(API_DELAY)
        continue

    # 无长上影线
    body = abs(closes[-1] - opens_list[-1])
    upper = highs_list[-1] - max(closes[-1], opens_list[-1])
    if body > 0 and upper > body * 2:
        fail["shadow"] += 1
        time.sleep(API_DELAY)
        continue

    # 近10日无跌停、近5日无跌>7%
    skip = False
    n = len(closes)
    for j in range(n-1, max(n-11, 0), -1):
        if closes[j-1] > 0:
            chg = (closes[j] / closes[j-1] - 1) * 100
            dfn = n - 1 - j
            if dfn < 10 and chg <= -9.5:
                fail["limit"] += 1
                skip = True
                break
            if dfn < 5 and chg <= -7:
                fail["drop7"] += 1
                skip = True
                break
    if skip:
        time.sleep(API_DELAY)
        continue

    # 通过
    results.append({
        "code": code, "name": name,
        "close": float(row["close"]),
        "pct_chg": round(float(row["pct_chg"]), 2),
        "turnover": round(float(row["turnover"]), 2) if pd.notna(row.get("turnover")) else 0,
        "circ_mv_yi": round(float(row["circ_mv"])/1e8, 2),
        "avg_amount_yi": round(avg_amt/1e8, 2),
        "above_ma20_days": above,
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20_now, 2),
    })
    fail["pass"] += 1

    if cnt % 30 == 0:
        print(f"    进度 {cnt}/{len(df)} (通过:{fail['pass']})...")

    time.sleep(API_DELAY)

# ===== 输出统计 =====
print(f"\n  K线验证统计:")
print(f"    通过: {fail['pass']}")
print(f"    K线获取失败: {fail['kline']}")
print(f"    20日均成交<3亿: {fail['amt20']}")
print(f"    站20日线<15天: {fail['ma20']}")
print(f"    非多头排列: {fail['bull']}")
print(f"    长上影线: {fail['shadow']}")
print(f"    近10日跌停: {fail['limit']}")
print(f"    近5日跌>7%: {fail['drop7']}")

# 保存结果文件
lines = [
    "",
    "高胜率隔夜策略选股信号 v2.0",
    f"  {now_str}  交易日: {trade_date}",
    "-" * 65,
    "",
    "选股条件：",
    "  流通市值50-150亿 | 日均成交>=3亿 | 换手1.5-16%",
    "  股价<20元 | 站20日线>=15天 | 涨0-5%未涨停",
    "  v2.0: 多头排列(MA5>MA10>MA20) | 无长上影线",
    "-" * 65,
]

if results:
    results.sort(key=lambda x: x["pct_chg"], reverse=True)
    lines.append("")
    for i, r in enumerate(results, 1):
        lines.append(
            f"  {i:>2}. {r['code']}  {r['name']:<8}  "
            f"现价:{r['close']:>7.2f}  涨幅:{r['pct_chg']:>+5.2f}%  "
            f"换手:{r['turnover']:>5.2f}%  "
            f"流通市值:{r['circ_mv_yi']:>6.1f}亿  "
            f"日均成交:{r['avg_amount_yi']:>5.1f}亿  "
            f"站20日线:{r['above_ma20_days']}/20  "
            f"MA5>{r['ma10']}>{r['ma20']}"
        )
    lines.append("")
    lines.append(f"  OK 共筛选出 {len(results)} 只")
else:
    lines.append("")
    lines.append("  今日无符合条件的股票")

lines.extend([
    "", "-" * 65,
    "  以上为技术面筛选结果，不构成投资建议",
    "  操作建议：14:50-14:57确认买入，次日9:45-10:30冲高卖出",
    "  严格止损：次日低开超2%且15分钟不收回即止损",
])

output_text = "\n".join(lines)
out_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"隔夜策略选股_{trade_date}.txt")
with open(out_file, "w", encoding="utf-8") as f:
    f.write(output_text)

print(f"\n{output_text}")
print(f"\n结果已保存: {out_file}")

# 同时输出JSON供二次过滤使用
if results:
    json_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"screener_raw_{trade_date}.json")
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"原始数据JSON: {json_file}")
