#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股短线选股 v6 - 东方财富K线+新浪实时行情
"""
import os, sys, time, json
from datetime import datetime, timedelta
import urllib.request

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

print("A股短线选股脚本启动 v6.0", flush=True)
print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)


def _to_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def safe_get(url, timeout=12, use_gbk=False):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://finance.eastmoney.com"
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if use_gbk:
                return r.read().decode("gbk", errors="replace")
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
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
# 东方财富全市场行情（用于获取股票候选池）
# ================================================================
def get_em_market_page(page, sort_field="f3", sort_asc=0):
    """
    获取东方财富沪深A股列表（分页）
    sort_field: f3=涨幅, f5=换手率, f20=流通市值
    """
    asc = 0 if sort_asc == 0 else 1
    url = (
        f"https://push2.eastmoney.com/api/qt/clist/get"
        f"?pn={page}&pz=500&po=1&np=1&fltt=2&invt=2"
        f"&fid={sort_field}&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
        f"&fields=f1,f2,f3,f4,f5,f6,f7,f12,f14,f20"
        f"&cb=jQuery&_={int(time.time()*1000)}"
    )
    raw = safe_get(url)
    if not raw:
        return []
    try:
        if raw.startswith("jQuery"):
            raw = raw[raw.index("(")+1:-2]
        data = json.loads(raw)
        items = data.get("data", {}).get("diff", [])
        results = []
        for item in items:
            code = str(item.get("f12", ""))
            if code.startswith(("8", "4", "9")):
                continue
            results.append({
                "code": code,
                "name": str(item.get("f14", "")),
                "price": _to_float(item.get("f2", 0)),
                "pct_chg": _to_float(item.get("f3", 0)),
                "turnover": _to_float(item.get("f5", 0)),
                "amount": _to_float(item.get("f6", 0)),
                "circ_mv": _to_float(item.get("f20", 0)) / 1e8,
            })
        return results
    except Exception as e:
        print(f"  EM行情解析失败(pg{page}): {e}", flush=True)
        return []


def get_market_candidates(max_pages=10):
    """获取全市场候选（涨幅-1%~+2%，价格2~30元，成交额>5000万）"""
    results = []
    seen = set()
    for page in range(1, max_pages + 1):
        items = get_em_market_page(page, sort_field="f3", sort_asc=0)
        if not items:
            break
        print(f"  第{page}页: {len(items)}只", flush=True, end="")
        for s in items:
            code = s["code"]
            if code in seen:
                continue
            seen.add(code)
            # 初级过滤
            if "ST" in s["name"]:
                continue
            pct = s["pct_chg"]
            price = s["price"]
            amount = s["amount"]
            if not (-1 <= pct <= 2):
                continue
            if price < 2 or price > 30:
                continue
            if amount < 5000e4:
                continue
            results.append(s)
        print(f" → 累计候选 {len(results)} 只", flush=True)
        time.sleep(0.3)
    return results


# ================================================================
# 新浪批量实时行情
# ================================================================
def batch_fetch_sina(codes):
    all_data = {}
    batch_size = 80
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        symbols = []
        for code in batch:
            symbols.append(f"sh{code}" if code.startswith("6") else f"sz{code}")
        url = "http://hq.sinajs.cn/list=" + ",".join(symbols)
        raw = safe_get(url, use_gbk=True, timeout=15)
        if not raw:
            time.sleep(0.1)
            continue
        for line in raw.strip().split("\n"):
            if 'hq_str_' not in line:
                continue
            try:
                content = line.split('"')[1]
                fields = content.split(",")
                if len(fields) < 10:
                    continue
                sym = line.split('hq_str_')[1].split('=')[0].strip()
                code = sym[2:]
                name = fields[0]
                prev_close = _to_float(fields[2])
                close = _to_float(fields[3])
                high = _to_float(fields[4])
                low = _to_float(fields[5])
                volume = _to_float(fields[8])
                amount = _to_float(fields[9])
                pct = (close - prev_close) / prev_close * 100 if prev_close > 0 else 0
                all_data[code] = {
                    "name": name, "close": close, "prev_close": prev_close,
                    "high": high, "low": low,
                    "volume": volume, "amount": amount, "pct_chg": pct,
                }
            except Exception:
                continue
        time.sleep(0.1)
    return all_data


# ================================================================
# 东方财富K线
# ================================================================
def get_kline_em(code, count=30):
    secid = f"1.{code}" if code.startswith("6") else f"0.{code}"
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=1&beg=0&end=20500101&lmt={count}"
    )
    raw = safe_get(url, timeout=12)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        klines_raw = data.get("data", {}).get("klines", [])
        # K线倒序（最新在前）→ 反转
        klines = klines_raw[::-1]
        result = []
        for k in klines:
            parts = k.split(",")
            result.append({
                "date": parts[0],
                "open": _to_float(parts[1]),
                "close": _to_float(parts[2]),
                "high": _to_float(parts[3]),
                "low": _to_float(parts[4]),
                "volume": _to_float(parts[5]),
            })
        return result
    except Exception as e:
        return []


# ================================================================
# 检查近20日是否有涨停
# ================================================================
def has_zt_in_kline(kline, days=20, min_pct=9.5):
    """检查前1~days日（不含今日）是否有涨停"""
    # kline[0]是今日，[1]是昨日，...
    end = min(days + 1, len(kline))
    for i in range(1, end):
        d = kline[i]
        if i >= len(kline) - 1:
            break
        prev_c = kline[i+1]["close"]
        cur_c = d["close"]
        if prev_c > 0:
            pct = (cur_c - prev_c) / prev_c * 100
            if pct >= min_pct:
                return True, d["date"], i
    return False, None, None


# ================================================================
# 策略一：尾盘缩量回踩均线法
# ================================================================
def strategy_1(trade_date):
    print(f"\n{'='*60}", flush=True)
    print(f"策略一：尾盘缩量回踩均线法 | 日期: {trade_date}", flush=True)
    print(f"{'='*60}", flush=True)

    print("  [1/3] 获取全市场候选（涨幅-1%~+2%, 价格2~30元, 成交额>5000万）...", flush=True)
    candidates = get_market_candidates(max_pages=10)
    print(f"  OK 候选共 {len(candidates)} 只", flush=True)
    if not candidates:
        return []

    print("  [2/3] 获取新浪实时行情...", flush=True)
    codes = [c["code"] for c in candidates]
    realtime = batch_fetch_sina(codes)
    print(f"  OK 获取到 {len(realtime)} 只实时行情", flush=True)

    print("  [3/3] K线验证（均线多头+近20日有涨停历史）...", flush=True)
    results = []
    total = len(candidates)

    for idx, c in enumerate(candidates):
        code = c["code"]
        s = realtime.get(code, {})
        if not s:
            time.sleep(0.05)
            continue

        close = s.get("close", 0)
        pct = s.get("pct_chg", 0)
        amount = s.get("amount", 0)
        high = s.get("high", 0)
        low = s.get("low", 0)

        # 排除一字板
        if high > 0 and low > 0 and (high - low) / high < 0.01 and pct > 8:
            time.sleep(0.05)
            continue

        # 获取K线
        kline = get_kline_em(code, 30)
        if len(kline) < 20:
            time.sleep(0.1)
            continue

        closes = [d["close"] for d in kline]
        if 0 in closes[:20]:
            time.sleep(0.1)
            continue

        # 检查近20日是否有涨停
        has_zt, zt_date, zt_days_ago = has_zt_in_kline(kline, days=20)
        if not has_zt:
            time.sleep(0.05)
            continue

        # 多头排列
        ma5 = sum(closes[:5]) / 5
        ma10 = sum(closes[:10]) / 10
        ma20 = sum(closes[:20]) / 20
        if not (closes[0] > ma5 > ma10 > ma20):
            time.sleep(0.05)
            continue

        # MA5斜率正
        ma5_3 = sum(closes[:3]) / 3
        ma5_3_old = sum(closes[1:4]) / 3
        if ma5_3 <= ma5_3_old:
            time.sleep(0.05)
            continue

        amount_yi = amount / 1e8
        results.append({
            "code": code, "name": s.get("name", c["name"]),
            "close": round(close, 2), "pct_chg": round(pct, 2),
            "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2),
            "amount_yi": round(amount_yi, 1),
        })

        if (idx + 1) % 50 == 0:
            print(f"    已处理 {idx+1}/{total}... 命中:{len(results)}", flush=True)
        time.sleep(0.1)

    print(f"  OK 策略一筛选完成，共 {len(results)} 只", flush=True)
    return results


# ================================================================
# 策略二：涨停回马枪缩量回踩法
# ================================================================
def strategy_2(trade_date):
    print(f"\n{'='*60}", flush=True)
    print(f"策略二：涨停回马枪缩量回踩法 | 日期: {trade_date}", flush=True)
    print(f"{'='*60}", flush=True)

    # 获取全市场候选（涨幅0%~6%，适合回踩）
    print("  [1/3] 获取全市场候选（涨幅0%~6%, 价格2~30元）...", flush=True)
    candidates = []
    seen = set()
    for page in range(1, 8):
        items = get_em_market_page(page, sort_field="f3", sort_asc=0)
        if not items:
            break
        for s in items:
            code = s["code"]
            if code in seen:
                continue
            seen.add(code)
            pct = s["pct_chg"]
            price = s["price"]
            if not (0 <= pct <= 6):
                continue
            if price < 2 or price > 30:
                continue
            candidates.append(s)
        time.sleep(0.3)
    print(f"  OK 候选共 {len(candidates)} 只", flush=True)
    if not candidates:
        return []

    print("  [2/3] 获取新浪实时行情 + K线验证...", flush=True)
    results = []
    total = len(candidates)

    for idx, c in enumerate(candidates):
        code = c["code"]
        kline = get_kline_em(code, 30)
        if len(kline) < 15:
            time.sleep(0.1)
            continue

        # 找近12日内涨停，且距今>=5天
        zt_found = False
        zt_days_ago = 0
        for i in range(1, min(13, len(kline)-1)):
            prev_c = kline[i+1]["close"]
            cur_c = kline[i]["close"]
            if prev_c > 0 and (cur_c - prev_c) / prev_c >= 0.095:
                zt_found = True
                zt_days_ago = i
                break

        if not zt_found or zt_days_ago < 5:
            time.sleep(0.05)
            continue

        # 近5日未再涨停
        recent_zt = False
        for i in range(1, min(6, len(kline)-1)):
            prev_c = kline[i+1]["close"]
            cur_c = kline[i]["close"]
            if prev_c > 0 and (cur_c - prev_c) / prev_c >= 0.095:
                recent_zt = True
                break
        if recent_zt:
            time.sleep(0.05)
            continue

        closes = [d["close"] for d in kline]
        if 0 in closes[:15]:
            time.sleep(0.1)
            continue

        # 均线支撑
        ma5 = sum(closes[:5]) / 5
        ma10 = sum(closes[:10]) / 10
        ma13 = sum(closes[:13]) / 13
        ma_support = min(ma5, ma10, ma13)
        close = closes[0]
        if close < ma_support * 0.97:
            time.sleep(0.05)
            continue

        # 获取实时行情（含成交额）
        s_data = batch_fetch_sina([code])
        s = s_data.get(code, {})
        pct = s.get("pct_chg", c["pct_chg"])
        amount = s.get("amount", c["amount"])

        amount_yi = amount / 1e8 if amount > 0 else (c["circ_mv"] * 1e8 * 0.03)
        results.append({
            "code": code, "name": s.get("name", c["name"]),
            "close": round(close, 2), "pct_chg": round(pct, 2),
            "zt_days": zt_days_ago,
            "ma5": round(ma5, 2), "ma10": round(ma10, 2),
            "amount_yi": round(amount_yi, 1),
        })

        if (idx + 1) % 30 == 0:
            print(f"    已处理 {idx+1}/{total}... 命中:{len(results)}", flush=True)
        time.sleep(0.15)

    print(f"  OK 策略二筛选完成，共 {len(results)} 只", flush=True)
    return results


# ================================================================
# 主函数
# ================================================================
def main():
    trade_date = get_trade_date()
    print(f"交易日期: {trade_date}", flush=True)

    s1 = strategy_1(trade_date)
    s2 = strategy_2(trade_date)

    lines = []
    lines.append(f"=== A股短线选股信号 {trade_date} ===")
    lines.append(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    lines.append("【策略一：尾盘缩量回踩均线法】")
    if s1:
        for r in s1:
            lines.append(
                f"  {r['code']} {r['name']:<8} "
                f"价:{r['close']:.2f} 涨:{r['pct_chg']:+.2f}% "
                f"成交额:{r['amount_yi']:.1f}亿 "
                f"MA5:{r['ma5']} MA10:{r['ma10']} MA20:{r['ma20']}"
            )
        lines.append(f"  共 {len(s1)} 只")
    else:
        lines.append("  今日无符合条件的股票")

    lines.append("")
    lines.append("【策略二：涨停回马枪缩量回踩法】")
    if s2:
        for r in s2:
            lines.append(
                f"  {r['code']} {r['name']:<8} "
                f"价:{r['close']:.2f} 涨:{r['pct_chg']:+.2f}% "
                f"回调{r['zt_days']}天 市值≈{r['amount_yi']:.1f}亿 "
                f"MA5:{r['ma5']} MA10:{r['ma10']}"
            )
        lines.append(f"  共 {len(s2)} 只")
    else:
        lines.append("  今日无符合条件的股票")

    result_text = "\n".join(lines)
    print("\n" + result_text, flush=True)

    out_file = f"短线选股信号_{trade_date}.txt"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(result_text)
    print(f"\n结果已保存: {out_file}", flush=True)
    return s1, s2


if __name__ == "__main__":
    main()
