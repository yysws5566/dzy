# -*- coding: utf-8 -*-
"""
高胜率隔夜策略 - 选股脚本 v3.0
数据源：新浪财经（实时行情）+ 腾讯证券（日K线）
完全绕过 akshare（网络层阻断问题）
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import pandas as pd
import urllib.request
import urllib.parse

# ============================================================
# 新浪财经：全市场实时行情
# ============================================================
SINA_SPOT_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
SINA_PAGE_SIZE = 80

def get_all_stocks_sina():
    """分页获取新浪全市场实时行情，返回 DataFrame"""
    all_stocks = []
    page = 1
    while True:
        url = (f"{SINA_SPOT_URL}?page={page}&num={SINA_PAGE_SIZE}"
                f"&sort=changepercent&asc=0&node=hs_a&symbol=&_s_r_a=auto")
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://finance.sina.com.cn"
            })
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
            if not data or not isinstance(data, list):
                break
            all_stocks.extend(data)
            if len(data) < SINA_PAGE_SIZE:
                break
            page += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  新浪行情页 {page} 失败: {e}")
            break

    if not all_stocks:
        return pd.DataFrame()

    df = pd.DataFrame(all_stocks)

    # 新浪列名映射
    col_map = {
        "symbol": "sina_symbol",
        "code": "code",
        "name": "name",
        "trade": "close",
        "price": "prev_close",
        "changepercent": "pct_chg",
        "change": "chg",
        "volume": "volume",
        "amount": "amount",
        "amplitude": "amplitude",
        "turnoverratio": "turn_over",
        "nmc": "circ_mv",
        "mktcap": "total_mv",
        "high": "high",
        "low": "low",
        "open": "open",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    for col in ["close", "pct_chg", "chg", "volume", "amount",
                "amplitude", "turn_over", "circ_mv", "total_mv",
                "high", "low", "open"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 新浪市值单位：万元 -> 元
    if "circ_mv" in df.columns:
        df["circ_mv"] = df["circ_mv"] * 10000
    if "total_mv" in df.columns:
        df["total_mv"] = df["total_mv"] * 10000

    return df


# ============================================================
# 腾讯证券：日K线（个股 + 指数）
# ============================================================
def get_kline_tencent(code, days=30):
    """
    通过腾讯接口获取个股/指数日K线
    code: 6位股票代码
    返回 DataFrame: date, open, close, high, low, volume
    """
    # 判断代码格式
    code_str = str(code).strip()
    if code_str.startswith("6") or code_str.startswith("5"):
        symbol = f"sh{code_str}"
    else:
        symbol = f"sz{code_str}"

    # 指数代码特殊处理
    if code_str == "000001":
        symbol = "sh000001"
    elif code_str == "399006":
        symbol = "sz399006"

    url = ("https://web.ifzq.gtimg.cn/appstock/app/kline/get?"
           f"_var=kline_day&param={symbol},day,,,{max(days, 60)},")
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://gu.qq.com/"
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read().decode("utf-8").strip()

        if text.startswith("kline_day="):
            text = text[len("kline_day="):]

        data = json.loads(text)
        day_data = data.get("data", {}).get(symbol, {}).get("day", [])

        if not day_data or not isinstance(day_data, list):
            return pd.DataFrame()

        records = []
        for item in day_data:
            if len(item) >= 6:
                records.append({
                    "date": item[0],
                    "open": float(item[1]),
                    "close": float(item[2]),
                    "high": float(item[3]),
                    "low": float(item[4]),
                    "volume": float(item[5]),
                })
        df = pd.DataFrame(records)
        if len(df) > days:
            df = df.tail(days)
        return df.reset_index(drop=True)

    except Exception as e:
        return pd.DataFrame()


def get_index_kline_tencent(code, days=30):
    """获取指数K线（封装）"""
    return get_kline_tencent(code, days)


# ============================================================
# 工具函数
# ============================================================
def get_trade_date():
    """获取当前交易日（周末则回退）"""
    today = datetime.now()
    if today.weekday() == 5:
        today -= timedelta(days=1)
    elif today.weekday() == 6:
        today -= timedelta(days=2)
    return today.strftime("%Y%m%d")


def get_prev_trade_date(trade_date_str, offset=1):
    """获取前N个交易日"""
    dt = datetime.strptime(trade_date_str, "%Y%m%d")
    count = 0
    while count < offset:
        dt -= timedelta(days=1)
        if dt.weekday() < 5:
            count += 1
    return dt.strftime("%Y%m%d")


# ============================================================
# 主选股逻辑
# ============================================================
def run_screener():
    trade_date = get_trade_date()
    print(f"高胜率隔夜策略选股 | 交易日期: {trade_date}")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Step 1: 新浪财经获取全市场实时行情
    print("\n[1/3] 获取全市场实时行情（新浪财经）...")
    df = get_all_stocks_sina()
    if df.empty:
        print("  X 无法获取行情数据")
        return []

    print(f"  OK 获取到 {len(df)} 只股票")

    # Step 2: 预筛选
    print("\n[2/3] 实时行情预筛选...")

    required_cols = ["code", "name", "close", "pct_chg", "amount",
                     "circ_mv", "turn_over", "high"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        print(f"  X 数据缺少字段: {missing}")
        return []

    # 排除ST、退市
    df = df[~df["name"].str.contains("ST|st|退市", case=False, na=False)]
    print(f"  排除ST/退市后: {len(df)}")

    # 排除北交所、科创板标记等
    df = df[~df["code"].str.startswith(("9", "8", "4"))]
    print(f"  排除北交所/新股后: {len(df)}")

    # 流通市值 50亿～150亿
    df = df[(df["circ_mv"] >= 5_000_000_00) & (df["circ_mv"] <= 15_000_000_000)]
    print(f"  流通市值50-150亿: {len(df)}")

    # 股价 < 20元
    df = df[(df["close"] > 0) & (df["close"] < 20)]
    print(f"  股价<20元: {len(df)}")

    # 涨幅 0%～5%
    df = df[(df["pct_chg"] >= 0) & (df["pct_chg"] <= 5)]
    print(f"  涨幅0-5%: {len(df)}")

    # 未触及涨停（close < high）
    df = df[df["close"] < df["high"]]
    print(f"  未触及涨停: {len(df)}")

    # 换手率
    if "turn_over" in df.columns:
        df = df[(df["turn_over"] >= 1.5) & (df["turn_over"] <= 16)]
        print(f"  换手率1.5-16%: {len(df)}")

    # 成交额 ≥ 2亿
    df = df[df["amount"] >= 200_000_000]
    print(f"  成交额≥2亿: {len(df)}")
    pre_filtered = df.reset_index(drop=True)

    if pre_filtered.empty:
        print("\n⚠️ 预筛选后无候选股票")
        return []

    # Step 3: K线验证（腾讯证券）
    print(f"\n[3/3] K线验证（腾讯证券）— 共 {len(pre_filtered)} 只...")
    results = []

    for idx, row in pre_filtered.iterrows():
        code = str(row["code"]).strip()
        name = row["name"]

        kline = get_kline_tencent(code, days=30)
        if kline.empty or len(kline) < 20:
            print(f"  {code} {name}: K线数据不足({len(kline)}天)")
            continue

        # 计算MA20和站上MA20的天数
        kline = kline.sort_values("date").reset_index(drop=True)
        kline["ma20"] = kline["close"].rolling(20).mean()
        kline["above_ma20"] = kline["close"] > kline["ma20"]

        # 站上MA20天数
        recent = kline.tail(20)
        above_days = int(recent["above_ma20"].sum())

        # 20日均成交额（需要估算：成交额 = 成交量 × 均价）
        # 腾讯K线只有量（手），用 close*volume*100 估算
        kline["est_amount"] = kline["close"] * kline["volume"] * 100  # 成交量单位：手
        avg_amount_20d = kline["est_amount"].tail(20).mean()

        # 均线排列检查（最近一天）
        latest = kline.iloc[-1]
        ma5 = kline["close"].tail(5).mean()
        ma10 = kline["close"].tail(10).mean()
        ma20 = latest["ma20"]

        # 多头排列判断
        bullish = latest["close"] > ma5 > ma10 > ma20

        # MA5斜率（近3日）
        if len(kline) >= 3:
            ma5_today = kline["close"].iloc[-5:].mean()
            ma5_3d_ago = kline["close"].iloc[-8:-3].mean() if len(kline) >= 8 else ma5_today
            ma5_slope_pos = ma5_today > ma5_3d_ago
        else:
            ma5_slope_pos = False

        # 排除长上影线（上影线长度 > 实体长度的50%）
        body = abs(latest["close"] - kline["open"].iloc[-1])
        upper_shadow = latest["high"] - max(latest["close"], kline["open"].iloc[-1])
        long_upper_shadow = (body > 0) and (upper_shadow > body * 0.5)

        # 今日振幅
        amplitude_today = (latest["high"] - latest["low"]) / kline["open"].iloc[-1] * 100 if kline["open"].iloc[-1] > 0 else 0

        # 判断
        reasons_pass = []
        reasons_fail = []

        if above_days < 15:
            reasons_fail.append(f"站MA20仅{above_days}天<15")
        else:
            reasons_pass.append(f"站MA20{above_days}天✅")

        if avg_amount_20d < 300_000_000:
            reasons_fail.append(f"20日均成交额{avg_amount_20d/1e8:.1f}亿<3亿")
        else:
            reasons_pass.append(f"20日均成交{avg_amount_20d/1e8:.1f}亿✅")

        if not bullish:
            reasons_fail.append("非多头排列")
        else:
            reasons_pass.append("多头排列✅")

        if long_upper_shadow:
            reasons_fail.append("长上影线（抛压重）")
        else:
            reasons_pass.append("无长上影✅")

        if amplitude_today > 12:
            reasons_fail.append(f"振幅{amplitude_today:.1f}%>12%（假突破风险）")
        else:
            reasons_pass.append(f"振幅{amplitude_today:.1f}%✅")

        if reasons_fail:
            print(f"  {code} {name}: ❌ {'; '.join(reasons_fail)}")
        else:
            print(f"  {code} {name}: ✅ {'; '.join(reasons_pass)}")
            results.append({
                "code": code,
                "name": name,
                "close": round(latest["close"], 2),
                "pct_chg": round(row["pct_chg"], 2),
                "turn_over": round(row.get("turn_over", 0), 2),
                "circ_mv": round(row["circ_mv"] / 1e8, 2),  # 亿
                "amount": round(row["amount"] / 1e8, 2),
                "above_ma20_days": above_days,
                "ma5_slope_pos": ma5_slope_pos,
                "bullish": bullish,
                "reasons": "; ".join(reasons_pass),
            })

        time.sleep(0.2)  # 限速

    # 输出结果
    print("\n" + "=" * 60)
    if not results:
        print("【选股结果】今日无符合条件的标的 ❌")
    else:
        print(f"【选股结果】共 {len(results)} 只符合条件的股票：\n")
        for r in results:
            print(f"  {r['code']} {r['name']} | 现价{r['close']} | 涨幅{r['pct_chg']}% "
                  f"| 换手{r['turn_over']}% | 市值{r['circ_mv']}亿 "
                  f"| 站MA20 {r['above_ma20_days']}天 | {r['reasons']}")

    # 保存结果
    out_file = f"隔夜策略选股_{trade_date}.txt"
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), out_file)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"高胜率隔夜策略选股 {trade_date}\n")
        f.write("=" * 60 + "\n\n")
        if results:
            for r in results:
                f.write(f"{r['code']} {r['name']}  现价{r['close']}  涨幅{r['pct_chg']}%  "
                        f"换手{r['turn_over']}%  市值{r['circ_mv']}亿  "
                        f"站MA20{r['above_ma20_days']}天  多头排列{'是' if r['bullish'] else '否'}\n")
        else:
            f.write("今日无符合条件的标的。\n")
    print(f"\n结果已保存至：{out_file}")
    return results


if __name__ == "__main__":
    run_screener()
