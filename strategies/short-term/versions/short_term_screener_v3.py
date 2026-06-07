# -*- coding: utf-8 -*-
"""
A股短线选股脚本 v3 - 高效版
数据源: AKShare涨停池 + 新浪批量实时行情 + 新浪K线
"""

import os, sys, time, json, math
from datetime import datetime, timedelta
import urllib.request

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ================================================================
# 工具函数
# ================================================================
def _to_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _safe_request(url, headers=None, timeout=10):
    hdrs = headers or {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "http://finance.sina.com.cn",
    }
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("gbk", errors="replace")
    except Exception:
        return None


def get_trade_date():
    today = datetime.now()
    if today.weekday() == 5:
        today -= timedelta(days=1)
    elif today.weekday() == 6:
        today -= timedelta(days=2)
    return today.strftime("%Y%m%d")


def get_previous_trade_date(trade_date, offset=1):
    dt = datetime.strptime(trade_date, "%Y%m%d")
    count = 0
    while count < offset:
        dt -= timedelta(days=1)
        if dt.weekday() < 5:
            count += 1
    return dt.strftime("%Y%m%d")


# ================================================================
# AKShare涨停池
# ================================================================
def get_zt_pool_akshare(date_str):
    import akshare as ak
    try:
        df = ak.stock_zt_pool_em(date=date_str)
        if df is None or df.empty:
            return []
        result = []
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            if code.startswith(("8", "4", "9")):
                continue
            result.append({
                "code": code,
                "name": str(row.get("名称", "")),
                "close": _to_float(row.get("最新价", 0)),
                "pct_chg": _to_float(row.get("涨跌幅", 0)),
                "turnover": _to_float(row.get("换手率", 0)),
                "circ_mv": _to_float(row.get("流通市值", 0)),
                "amount": _to_float(row.get("成交额", 0)),
            })
        return result
    except Exception as e:
        print(f"    AKShare涨停池失败 {date_str}: {e}")
        return []


# ================================================================
# 新浪批量实时行情
# ================================================================
def parse_sina_hq_batch(raw_text):
    """
    解析 hq.sinajs.cn 批量返回
    返回: {code: {name, close, prev_close, pct_chg, volume, amount, open, high, low}}
    """
    result = {}
    for line in raw_text.strip().split("\n"):
        if 'hq_str_' not in line:
            continue
        try:
            # var hq_str_sh600519="贵州茅台,open,prev_close,current,high,low,buy,sell,vol,amount,..."
            content = line.split('"')[1]
            fields = content.split(",")
            if len(fields) < 10:
                continue
            symbol = line.split('hq_str_')[1].split('=')[0].strip()
            # symbol格式: sh600519 或 sz000001
            if symbol.startswith("sh"):
                code = symbol[2:]
            elif symbol.startswith("sz"):
                code = symbol[2:]
            else:
                continue

            name = fields[0]
            open_p = _to_float(fields[1])
            prev_close = _to_float(fields[2])
            close = _to_float(fields[3])
            high = _to_float(fields[4])
            low = _to_float(fields[5])
            volume = _to_float(fields[8])  # 股数
            amount = _to_float(fields[9])  # 金额
            pct_chg = (close - prev_close) / prev_close * 100 if prev_close > 0 else 0

            result[code] = {
                "name": name, "close": close, "prev_close": prev_close,
                "open": open_p, "high": high, "low": low,
                "volume": volume, "amount": amount, "pct_chg": pct_chg,
            }
        except Exception:
            continue
    return result


def batch_fetch_sina(codes, batch_size=50):
    """批量从新浪获取实时行情"""
    all_data = {}
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        symbols = []
        for code in batch:
            if code.startswith("6"):
                symbols.append(f"sh{code}")
            else:
                symbols.append(f"sz{code}")
        url = "http://hq.sinajs.cn/list=" + ",".join(symbols)
        raw = _safe_request(url)
        if raw:
            all_data.update(parse_sina_hq_batch(raw))
        time.sleep(0.3)
    return all_data


# ================================================================
# 新浪K线
# ================================================================
def get_kline_sina(code, datalen=30):
    if code.startswith("6"):
        symbol = f"sh{code}"
    else:
        symbol = f"sz{code}"
    url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={datalen}"
    raw = _safe_request(url, timeout=15)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        result = []
        for d in data:
            result.append({
                "date": d.get("day", ""),
                "open": _to_float(d.get("open")),
                "high": _to_float(d.get("high")),
                "low": _to_float(d.get("low")),
                "close": _to_float(d.get("close")),
                "volume": _to_float(d.get("volume")),
            })
        return result
    except Exception:
        return []


# ================================================================
# 策略一：尾盘缩量回踩均线法
# ================================================================
def strategy_1_pullback_ma(trade_date):
    print(f"\n{'='*60}")
    print(f"策略一：尾盘缩量回踩均线法 | 日期: {trade_date}")
    print(f"{'='*60}")

    print("  [1/4] 获取近20日涨停股...")
    zt_codes = set()
    for offset_days in range(0, 22):
        d = get_previous_trade_date(trade_date, offset_days)
        stocks = get_zt_pool_akshare(d)
        for s in stocks:
            code = s["code"]
            if "ST" in s["name"]:
                continue
            zt_codes.add(code)
        time.sleep(0.3)
    print(f"  OK 近20日有涨停的股票共 {len(zt_codes)} 只")
    if not zt_codes:
        return []

    print("  [2/4] 获取实时行情预筛选...")
    all_data = batch_fetch_sina(list(zt_codes))
    if not all_data:
        print("  X 无法获取实时行情数据")
        return []
    print(f"  OK 获取到 {len(all_data)} 只行情")

    # 预筛选（调整条件：用成交额估算市值门槛，用量比替代换手率）
    candidates = []
    for code, s in all_data.items():
        name = s["name"]
        if "ST" in name or "st" in name.lower():
            continue
        pct_chg = s["pct_chg"]
        amount = s["amount"]  # 元
        avg_price = s["close"]

        # 涨幅 -1%~+2%
        if not (-1 <= pct_chg <= 2):
            continue
        # 成交额过滤（30亿市值 * 3%换手 ≈ 9000万，宽松点用5000万）
        if amount < 5000e4:  # 5000万
            continue
        # 价格 < 30元
        if avg_price >= 30:
            continue
        # 量比：需要20日均量，暂用成交额判断
        # 排除一字板涨停（今日涨停且波动极小）
        today_zt = get_zt_pool_akshare(trade_date)
        today_zt_codes = {s["code"] for s in today_zt}
        if code in today_zt_codes:
            # 今日涨停，检查是否一字板
            if s["high"] == s["low"] or (s["high"] - s["low"]) / s["close"] < 0.01:
                continue  # 排除一字板

        candidates.append({"code": code, "sina": s})

    print(f"  OK 预筛选后剩余 {len(candidates)} 只")
    if not candidates:
        return []

    print("  [3/4] 获取历史数据计算均线和MACD...")
    results = []
    total = len(candidates)

    for i, c in enumerate(candidates):
        code = c["code"]
        s = c["sina"]
        kline = get_kline_sina(code)
        if len(kline) < 20:
            time.sleep(0.1)
            continue

        closes = [d["close"] for d in kline]
        if 0 in closes or len(closes) < 20:
            time.sleep(0.1)
            continue

        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma20 = sum(closes[-20:]) / 20
        ma20_prev = sum(closes[-21:-1]) / 20 if len(closes) >= 21 else ma20

        # 均线多头排列，MA20上行
        if not (ma5 > ma10 > ma20 and ma20 > ma20_prev):
            time.sleep(0.1)
            continue

        # 收盘在MA5和MA10之间（允许±2%误差）
        current_close = s["close"]
        if not (current_close <= ma5 * 1.01 and current_close >= ma10 * 0.98):
            time.sleep(0.1)
            continue

        # MACD DIF > 0
        if len(closes) >= 26:
            ema12 = closes[0]
            ema26 = closes[0]
            for c_val in closes[1:]:
                ema12 = c_val * (2/13) + ema12 * (11/13)
                ema26 = c_val * (2/27) + ema26 * (25/27)
            dif = ema12 - ema26
            if dif <= 0:
                time.sleep(0.1)
                continue
        else:
            time.sleep(0.1)
            continue

        results.append({
            "code": code, "name": s["name"],
            "close": current_close, "pct_chg": round(s["pct_chg"], 2),
            "amount_yi": round(s["amount"] / 1e8, 2),
            "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2),
            "strategy": "策略一",
        })

        if (i + 1) % 10 == 0:
            print(f"    已处理 {i+1}/{total}...")
        time.sleep(0.15)

    print(f"  [4/4] 策略一筛选完成，共 {len(results)} 只")
    return results


# ================================================================
# 策略二：涨停回马枪缩量回踩法
# ================================================================
def strategy_2_limit_pullback(trade_date):
    print(f"\n{'='*60}")
    print(f"策略二：涨停回马枪缩量回踩法 | 日期: {trade_date}")
    print(f"{'='*60}")

    print("  [1/4] 获取近3-10日涨停股...")
    zt_stocks = {}  # code -> info

    for offset_days in range(3, 11):
        d = get_previous_trade_date(trade_date, offset_days)
        stocks = get_zt_pool_akshare(d)
        for s in stocks:
            code = s["code"]
            if code.startswith(("8", "4", "9")) or "ST" in s["name"]:
                continue
            if code not in zt_stocks:
                zt_stocks[code] = {
                    "zt_date": d, "name": s["name"],
                    "zt_close": s["close"], "circ_mv": s["circ_mv"],
                    "offset_days": offset_days,
                }
        time.sleep(0.3)

    print(f"  OK 近3-10日有涨停的股票共 {len(zt_stocks)} 只")
    if not zt_stocks:
        return []

    # 预过滤市值
    filtered = {code: info for code, info in zt_stocks.items()
                if 30e8 <= info["circ_mv"] <= 150e8}
    print(f"  OK 市值过滤后剩余 {len(filtered)} 只")
    if not filtered:
        return []

    print("  [2/4] 逐只验证回马枪条件...")

    # 批量获取这批股票的实时行情
    realtime = batch_fetch_sina(list(filtered.keys()))
    results = []
    total = len(filtered)

    for i, (code, zt_info) in enumerate(filtered.items()):
        offset_days = zt_info["offset_days"]
        zt_date = zt_info["zt_date"]
        zt_open = zt_info["zt_close"] * 0.9  # 估算涨停开盘价
        zt_close_p = zt_info["zt_close"]

        kline = get_kline_sina(code)
        if len(kline) < 5:
            time.sleep(0.1)
            continue

        # 找涨停日
        zt_day_data = next((d for d in kline if d["date"] == zt_date), None)
        if not zt_day_data:
            time.sleep(0.1)
            continue

        zt_open = zt_day_data["open"]
        zt_vol = zt_day_data["volume"]
        zt_high = zt_day_data["high"]
        zt_low = zt_day_data["low"]

        # 排除一字板
        if zt_close_p > 0:
            body_pct = abs(zt_open - zt_close_p) / zt_close_p
            range_pct = (zt_high - zt_low) / zt_close_p if zt_close_p > 0 else 0
            if body_pct < 0.005 and range_pct < 0.005:
                time.sleep(0.1)
                continue

        # 今日数据
        today_data = next((d for d in kline if d["date"] == trade_date), kline[-1])
        today_close = today_data["close"]
        today_vol = today_data["volume"]
        today_low = today_data["low"]

        if today_close <= 0:
            time.sleep(0.1)
            continue

        # 回调至涨停阳线实体下沿附近
        if today_close > zt_close_p * 1.02:
            time.sleep(0.1)
            continue
        if today_close < zt_open * 0.98:
            time.sleep(0.1)
            continue

        # 缩量至涨停日1/3以下
        if zt_vol > 0 and today_vol > zt_vol / 3:
            time.sleep(0.1)
            continue

        # 不破涨停底
        if today_low < zt_open * 0.98:
            time.sleep(0.1)
            continue

        # 13日均线支撑
        if len(kline) >= 13:
            closes_13 = [d["close"] for d in kline[-13:]]
            ma13 = sum(closes_13) / 13
            if today_close < ma13:
                time.sleep(0.1)
                continue
        else:
            time.sleep(0.1)
            continue

        # 获取今日涨幅
        s = realtime.get(code, {})
        today_pct = s.get("pct_chg", 0)
        circ_mv_yi = zt_info["circ_mv"] / 1e8

        results.append({
            "code": code, "name": zt_info["name"],
            "close": today_close, "pct_chg": round(today_pct, 2),
            "circ_mv_yi": round(circ_mv_yi, 1),
            "zt_date": zt_date, "callback_days": offset_days,
            "strategy": "策略二",
        })

        if (i + 1) % 10 == 0:
            print(f"    已处理 {i+1}/{total}...")
        time.sleep(0.15)

    print(f"  [3/4] 策略二筛选完成，共 {len(results)} 只")
    return results


# ================================================================
# 主函数
# ================================================================
def main():
    trade_date = get_trade_date()
    print(f"短线选股启动 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 日期: {trade_date}")

    s1 = strategy_1_pullback_ma(trade_date)
    s2 = strategy_2_limit_pullback(trade_date)

    output = []
    output.append(f"=== A股短线选股信号 {trade_date} ===")
    output.append(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    output.append("")
    output.append("【策略一：尾盘缩量回踩均线法】")
    if s1:
        for r in s1:
            output.append(f"  {r['code']} {r['name']:<8} 价:{r['close']:.2f} 涨:{r['pct_chg']:+.2f}% 成交额:{r['amount_yi']:.1f}亿 MA5:{r['ma5']} MA10:{r['ma10']} MA20:{r['ma20']}")
        output.append(f"  共 {len(s1)} 只")
    else:
        output.append("  今日无符合条件的股票")

    output.append("")
    output.append("【策略二：涨停回马枪缩量回踩法】")
    if s2:
        for r in s2:
            output.append(f"  {r['code']} {r['name']:<8} 价:{r['close']:.2f} 涨:{r['pct_chg']:+.2f}% 回调{r['callback_days']}天 涨停日:{r['zt_date']} 市值:{r['circ_mv_yi']:.1f}亿")
        output.append(f"  共 {len(s2)} 只")
    else:
        output.append("  今日无符合条件的股票")

    output.append("")
    output.append("以上为技术面筛选结果，不构成投资建议。")
    output.append("建议尾盘14:50-14:57买入，次日冲高卖出。")

    text = "\n".join(output)
    print("\n" + text)

    out_file = f"短线选股信号_{trade_date}.txt"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"\n结果已保存至: {out_file}")

    return s1, s2


if __name__ == "__main__":
    main()
