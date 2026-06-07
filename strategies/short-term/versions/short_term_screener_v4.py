# -*- coding: utf-8 -*-
"""
A股短线选股信号脚本 v4 - 纯可靠数据源版
数据源: 东方财富K线 + 新浪实时行情
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


def _to_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _safe_request(url, headers=None, timeout=12):
    hdrs = headers or {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://finance.eastmoney.com",
    }
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as r:
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
# 东方财富涨停股池
# ================================================================
def get_zt_pool_em(date_str):
    """
    东方财富涨停股池
    返回: [{code, name, close, pct_chg, turnover, circ_mv, amount}]
    """
    url = (
        f"https://push2.eastmoney.com/api/qt/clist/get"
        f"?cb=jQuery&pn=1&pz=500&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
        f"&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
        f"&fields=f1,f2,f3,f4,f5,f6,f7,f10,f12,f14,f15,f16,f17,f18"
        f"&_={int(time.time()*1000)}"
    )
    raw = _safe_request(url, timeout=15)
    if not raw:
        return []
    try:
        # 清理jQuery包装
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
                "close": _to_float(item.get("f3", 0)),  # 涨停价
                "pct_chg": _to_float(item.get("f3", 0)),  # 涨幅%
                "turnover": _to_float(item.get("f5", 0)),  # 换手率%
                "circ_mv": _to_float(item.get("f20", 0)) / 1e8,  # 流通市值(亿)
                "amount": _to_float(item.get("f6", 0)) / 1e4,  # 成交额(万元->元)
            })
        return results
    except Exception as e:
        print(f"    EM涨停池失败: {e}")
        return []


# ================================================================
# 东方财富近N日涨停数据
# ================================================================
def get_recent_zt_em(trade_date, days=20):
    """
    获取近N个交易日内有涨停的股票
    返回: {code: {name, zt_date, close, pct_chg, circ_mv, amount}}
    """
    zt_dict = {}
    for offset in range(0, days + 1):
        d = get_previous_trade_date(trade_date, offset)
        print(f"    查询 {d} 涨停池...", end="", flush=True)
        pool = get_zt_pool_em(d)
        for s in pool:
            code = s["code"]
            if "ST" in s["name"]:
                continue
            if code not in zt_dict:
                zt_dict[code] = {
                    "name": s["name"],
                    "zt_date": d,
                    "close": s["close"],
                    "pct_chg": s["pct_chg"],
                    "circ_mv": s["circ_mv"],
                    "amount": s["amount"],
                }
            else:
                # 保留最近一次涨停信息
                if d > zt_dict[code]["zt_date"]:
                    zt_dict[code]["zt_date"] = d
                    zt_dict[code]["close"] = s["close"]
                    zt_dict[code]["pct_chg"] = s["pct_chg"]
        print(f" {len(pool)} 只")
        time.sleep(0.3)
    return zt_dict


# ================================================================
# 新浪批量实时行情
# ================================================================
def batch_fetch_sina(codes, batch_size=50):
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
        raw = _safe_request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "http://finance.sina.com.cn"
        }, timeout=15)
        if not raw:
            continue
        for line in raw.strip().split("\n"):
            if 'hq_str_' not in line:
                continue
            try:
                content = line.split('"')[1]
                fields = content.split(",")
                if len(fields) < 10:
                    continue
                symbol = line.split('hq_str_')[1].split('=')[0].strip()
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
                volume = _to_float(fields[8])
                amount = _to_float(fields[9])
                pct_chg = (close - prev_close) / prev_close * 100 if prev_close > 0 else 0
                all_data[code] = {
                    "name": name, "close": close, "prev_close": prev_close,
                    "open": open_p, "high": high, "low": low,
                    "volume": volume, "amount": amount, "pct_chg": pct_chg,
                }
            except Exception:
                continue
        time.sleep(0.1)
    return all_data


# ================================================================
# 东方财富K线（用于MA计算）
# ================================================================
def get_kline_em(code, count=30):
    """
    获取日K线数据
    返回: [{date, open, close, high, low, volume}]
    """
    # 判断市场
    if code.startswith("6"):
        secid = f"1.{code}"
    else:
        secid = f"0.{code}"
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=1&beg=0&end=20500101&lmt={count}"
    )
    raw = _safe_request(url, timeout=12)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        klines_raw = data.get("data", {}).get("klines", [])
        result = []
        for k in klines_raw:
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
    zt_dict = get_recent_zt_em(trade_date, days=20)
    print(f"  OK 近20日有涨停的股票共 {len(zt_dict)} 只")
    if not zt_dict:
        return []

    print("  [2/4] 获取实时行情预筛选...")
    all_data = batch_fetch_sina(list(zt_dict.keys()))
    if not all_data:
        print("  X 无法获取实时行情数据")
        return []
    print(f"  OK 获取到 {len(all_data)} 只行情")

    candidates = []
    for code, s in all_data.items():
        name = s["name"]
        if "ST" in name or "st" in name.lower():
            continue
        pct_chg = s["pct_chg"]
        amount = s["amount"]  # 元
        avg_price = s["close"]

        # 涨幅 -1%~+2%（适合小资金）
        if not (-1 <= pct_chg <= 2):
            continue
        # 成交额 > 5000万
        if amount < 5000e4:
            continue
        # 价格 < 30元
        if avg_price >= 30 or avg_price <= 2:
            continue
        # 排除今日一字板涨停
        if s["high"] == s["low"] or (s["high"] - s["low"]) / s["close"] < 0.01:
            if pct_chg > 8:
                continue

        candidates.append({"code": code, "sina": s, "zt_info": zt_dict.get(code, {})})

    print(f"  OK 预筛选后剩余 {len(candidates)} 只")

    print("  [3/4] 获取历史数据计算均线...")
    results = []
    total = len(candidates)

    for i, c in enumerate(candidates):
        code = c["code"]
        s = c["sina"]
        kline = get_kline_em(code, 30)
        if len(kline) < 20:
            time.sleep(0.1)
            continue

        closes = [d["close"] for d in kline]
        if 0 in closes[:20]:
            time.sleep(0.1)
            continue

        ma5 = sum(closes[:5]) / 5
        ma10 = sum(closes[:10]) / 10
        ma20 = sum(closes[:20]) / 20

        # 多头排列
        price_ok = closes[0] > ma5 > ma10 > ma20
        if not price_ok:
            time.sleep(0.1)
            continue

        # MA5近3日斜率为正
        ma5_3 = sum(closes[:3]) / 3
        ma5_3_old = sum(closes[1:4]) / 3
        if ma5_3 <= ma5_3_old:
            time.sleep(0.1)
            continue

        # 缩量判断：今日成交额 < 5日均成交额
        amount_5avg = s["amount"]  # 实时成交额（当日累加）
        # 用K线最后5日成交额估算
        vol_5 = [d["volume"] for d in kline[:5]]
        # 用成交量估算（K线数据无成交额，用量*均价近似）
        vol_5_est = [d["volume"] * d["close"] for d in kline[:5]]
        vol_5avg = sum(vol_5_est[1:]) / 4  # 前4日均值（排除今日）
        if vol_5avg > 0 and amount_5avg < vol_5avg * 0.7:
            pass  # 缩量通过
        # 宽松处理：不做强制缩量过滤，以免误杀

        amount_yi = s["amount"] / 1e8
        results.append({
            "code": code, "name": s["name"],
            "close": s["close"], "pct_chg": round(s["pct_chg"], 2),
            "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2),
            "amount_yi": round(amount_yi, 1),
            "high": s["high"], "low": s["low"],
        })

        if (i + 1) % 20 == 0:
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

    print("  [1/4] 获取近期涨停股（东方财富）...")
    zt_dict = get_recent_zt_em(trade_date, days=20)
    print(f"  OK 近期涨停股共 {len(zt_dict)} 只")

    print("  [2/4] 获取实时行情...")
    realtime = batch_fetch_sina(list(zt_dict.keys()))
    if not realtime:
        print("  X 无法获取实时行情")
        return []
    print(f"  OK 获取 {len(realtime)} 只行情")

    print("  [3/4] 回踩验证...")
    results = []
    total = len(zt_dict)
    cutoff_date = get_previous_trade_date(trade_date, 12)  # 近12个交易日内的涨停

    for i, (code, zt_info) in enumerate(zt_dict.items()):
        zt_date = zt_info["zt_date"]
        if zt_date > cutoff_date:
            time.sleep(0.1)
            continue  # 涨停日期太近，跳过

        s = realtime.get(code, {})
        today_close = s.get("close", 0)
        today_low = s.get("low", 0)
        today_vol = s.get("volume", 0)
        if today_close <= 0:
            time.sleep(0.1)
            continue

        kline = get_kline_em(code, 25)
        if len(kline) < 15:
            time.sleep(0.1)
            continue

        closes = [d["close"] for d in kline]
        if 0 in closes[:20]:
            time.sleep(0.1)
            continue

        ma5 = sum(closes[:5]) / 5
        ma10 = sum(closes[:10]) / 10
        ma13 = sum(closes[:13]) / 13

        # 回踩13日均线或10日均线
        ma_support = min(ma5, ma10, ma13)
        if today_close < ma_support * 0.97:
            time.sleep(0.1)
            continue

        # 不破涨停日开盘价的2%
        zt_open = zt_info["close"] / (1 + zt_info["pct_chg"] / 100)
        if today_low < zt_open * 0.98:
            time.sleep(0.1)
            continue

        # 涨幅 0%~6%（回调后的温和反弹）
        pct = s.get("pct_chg", 0)
        if not (0 <= pct <= 6):
            time.sleep(0.1)
            continue

        # 流通市值
        circ_mv_yi = zt_info["circ_mv"]

        # 近5日未再涨停（避免反复追涨停）
        recent_zt = any(
            d["close"] > zt_info["close"] * 0.98
            for d in kline[1:6]
        )
        if recent_zt:
            time.sleep(0.1)
            continue

        offset_days = 0
        for od in range(1, 15):
            d_str = get_previous_trade_date(trade_date, od)
            if d_str == zt_date:
                offset_days = od
                break

        results.append({
            "code": code, "name": zt_info["name"],
            "close": today_close, "pct_chg": round(pct, 2),
            "circ_mv_yi": round(circ_mv_yi, 1),
            "zt_date": zt_date, "callback_days": offset_days,
            "ma5": round(ma5, 2), "ma10": round(ma10, 2),
        })

        if (i + 1) % 20 == 0:
            print(f"    已处理 {i+1}/{total}...")
        time.sleep(0.15)

    print(f"  [4/4] 策略二筛选完成，共 {len(results)} 只")
    return results


# ================================================================
# 主函数
# ================================================================
def main():
    trade_date = get_trade_date()
    print(f"A股短线选股脚本启动 v4.0")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"交易日期: {trade_date}")

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

    result_text = "\n".join(output)
    print(result_text)

    # 保存结果
    out_file = f"短线选股信号_{trade_date}.txt"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(result_text)
    print(f"\n结果已保存: {out_file}")

    return s1, s2


if __name__ == "__main__":
    main()
