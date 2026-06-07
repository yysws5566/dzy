# -*- coding: utf-8 -*-
"""
A股短线选股脚本 - 优化版
数据源: 新浪财经API（全市场实时） + AKShare涨停池
"""

import os, sys, time, json, math
from datetime import datetime, timedelta

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
def _safe_request(url, headers=None, timeout=10):
    import urllib.request
    hdrs = headers or {
        "User-Agent": "Mozilla/5.0",
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


def _to_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


# ================================================================
# 新浪全市场行情
# ================================================================
def get_all_stocks_sina():
    """
    通过新浪API获取全市场实时行情（批量）
    返回: list of dicts
    """
    # 新浪全市场列表接口
    url = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeDataSimple?page=1&num=5500&sort=changepercent&asc=0&node=hs_a"
    raw = _safe_request(url)
    if not raw:
        return []

    try:
        data = json.loads(raw)
    except Exception:
        return []

    result = []
    for item in data:
        try:
            symbol = str(item.get("symbol", ""))
            # 过滤非A股
            if not (symbol.startswith("sh6") or symbol.startswith("sz0") or symbol.startswith("sz3")):
                continue
            code = symbol[2:]
            result.append({
                "code": code,
                "name": str(item.get("name", "")),
                "close": _to_float(item.get("trade", 0)),
                "prev_close": _to_float(item.get("settlement", 0)),
                "open": _to_float(item.get("open", 0)),
                "high": _to_float(item.get("high", 0)),
                "low": _to_float(item.get("low", 0)),
                "volume": _to_float(item.get("volume", 0)),
                "amount": _to_float(item.get("amount", 0)),
                "turnover": _to_float(item.get("turnoverratio", 0)),
                "pct_chg": _to_float(item.get("changepercent", 0)),
                "circ_mv": _to_float(item.get("mktcap", 0)) * 1e8 if _to_float(item.get("mktcap", 0)) > 0 else 0,
                # 尝试从总市值字段估算
            })
        except Exception:
            continue
    return result


def get_stock_kline_sina(code):
    """
    获取个股日K线（新浪，最多30条）
    返回: list of {date, open, high, low, close, volume}
    """
    if code.startswith("6"):
        symbol = f"sh{code}"
    else:
        symbol = f"sz{code}"

    url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen=30"
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
# AKShare涨停池
# ================================================================
def get_zt_pool_akshare(date_str):
    """使用AKShare获取涨停池"""
    import akshare as ak
    try:
        df = ak.stock_zt_pool_em(date=date_str)
        if df is None or df.empty:
            return []
        # 统一列名
        df = df.rename(columns={
            "代码": "code", "名称": "name",
            "最新价": "close", "涨跌幅": "pct_chg",
            "换手率": "turnover", "流通市值": "circ_mv",
            "成交额": "amount",
        })
        for col in ["close", "pct_chg", "turnover", "circ_mv"]:
            if col in df.columns:
                df[col] = pd_to_numeric(df[col])
        return df.to_dict("records")
    except Exception as e:
        print(f"    AKShare涨停池失败 {date_str}: {e}")
        return []


def pd_to_numeric(series):
    """安全转换为数值"""
    try:
        import pandas as pd
        return pd.to_numeric(series, errors="coerce").fillna(0)
    except Exception:
        return series


# ================================================================
# 策略一：尾盘缩量回踩均线法
# ================================================================
def strategy_1_pullback_ma(trade_date):
    """
    条件：
    1. 近20日有涨停
    2. 涨幅 -1%~+2%
    3. 换手率 1.5%~6%
    4. 流通市值 30亿~150亿
    5. 均线多头 MA5>MA10>MA20，MA20上行
    6. 收盘在MA5和MA10之间
    7. MACD DIF>0
    8. 非ST
    """
    print(f"\n{'='*60}")
    print(f"策略一：尾盘缩量回踩均线法 | 日期: {trade_date}")
    print(f"{'='*60}")

    # Step 1: 获取近20日涨停股
    print("  [1/4] 获取近20日涨停股...")
    zt_codes = set()
    for offset_days in range(0, 22):
        d = get_previous_trade_date(trade_date, offset_days)
        stocks = get_zt_pool_akshare(d)
        for s in stocks:
            code = str(s.get("code", ""))
            if not code.startswith(("8", "4", "9")):
                zt_codes.add(code)
        time.sleep(0.3)
    print(f"  OK 近20日有涨停的股票共 {len(zt_codes)} 只")

    if not zt_codes:
        print("  X 无涨停股数据")
        return []

    # Step 2: 获取全市场实时行情
    print("  [2/4] 获取实时行情预筛选...")
    all_stocks = get_all_stocks_sina()
    if not all_stocks:
        print("  X 无法获取实时行情数据")
        return []
    print(f"  OK 获取到 {len(all_stocks)} 只股票行情")

    # 转为dict方便查询
    stock_map = {s["code"]: s for s in all_stocks}

    # 预筛选
    candidates = []
    for code in zt_codes:
        s = stock_map.get(code)
        if not s:
            continue
        name = s.get("name", "")
        if "ST" in name or "st" in name.lower():
            continue
        if code.startswith(("8", "4")):
            continue

        pct_chg = s.get("pct_chg", 0)
        turnover = s.get("turnover", 0)
        circ_mv = s.get("circ_mv", 0)

        if not (-1 <= pct_chg <= 2):
            continue
        if not (1.5 <= turnover <= 6):
            continue
        if not (30e8 <= circ_mv <= 150e8):
            continue

        candidates.append(s)

    print(f"  OK 预筛选后剩余 {len(candidates)} 只")
    if not candidates:
        return []

    # Step 3: 逐只获取K线验证均线和MACD
    print("  [3/4] 获取历史数据计算均线和MACD...")
    results = []
    pre_date = get_previous_trade_date(trade_date, 30)
    total = len(candidates)

    for i, s in enumerate(candidates):
        code = s["code"]
        kline = get_stock_kline_sina(code)
        if len(kline) < 20:
            time.sleep(0.1)
            continue

        closes = [d["close"] for d in kline]
        vols = [d["volume"] for d in kline]

        if 0 in closes or len(closes) < 20:
            time.sleep(0.1)
            continue

        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma20 = sum(closes[-20:]) / 20
        ma20_prev = sum(closes[-21:-1]) / 20 if len(closes) >= 21 else ma20

        # 条件：均线多头排列，MA20上行
        if not (ma5 > ma10 > ma20 and ma20 > ma20_prev):
            time.sleep(0.1)
            continue

        # 收盘在MA5和MA10之间
        current_close = s["close"]
        if not (current_close <= ma5 and current_close >= ma10 * 0.98):
            time.sleep(0.1)
            continue

        # MACD DIF > 0
        if len(closes) >= 26:
            ema12 = closes[0]
            ema26 = closes[0]
            for c in closes[1:]:
                ema12 = c * (2/13) + ema12 * (11/13)
                ema26 = c * (2/27) + ema26 * (25/27)
            dif = ema12 - ema26
            if dif <= 0:
                time.sleep(0.1)
                continue
        else:
            time.sleep(0.1)
            continue

        circ_mv_yi = s.get("circ_mv", 0) / 1e8
        results.append({
            "code": code,
            "name": s.get("name", ""),
            "close": current_close,
            "pct_chg": s.get("pct_chg", 0),
            "turnover": round(s.get("turnover", 0), 2),
            "circ_mv_yi": round(circ_mv_yi, 2),
            "ma5": round(ma5, 2),
            "ma10": round(ma10, 2),
            "ma20": round(ma20, 2),
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
    """
    条件：
    1. 近3-10日内有实体阳线涨停（非一字板）
    2. 涨停后第3-8天回调
    3. 回调至涨停阳线实体下沿附近
    4. 缩量至涨停日1/3以下
    5. 不破涨停底
    6. 股价在13日均线上方
    7. 流通市值 30亿~150亿
    8. 非ST
    """
    print(f"\n{'='*60}")
    print(f"策略二：涨停回马枪缩量回踩法 | 日期: {trade_date}")
    print(f"{'='*60}")

    print("  [1/4] 获取近3-10日涨停股...")
    zt_stocks = {}  # code -> {zt_date, name, zt_close, circ_mv}

    for offset_days in range(3, 11):
        d = get_previous_trade_date(trade_date, offset_days)
        stocks = get_zt_pool_akshare(d)
        for s in stocks:
            code = str(s.get("code", ""))
            if code.startswith(("8", "4", "9")):
                continue
            name = str(s.get("name", ""))
            if "ST" in name:
                continue
            if code not in zt_stocks:
                zt_stocks[code] = {
                    "zt_date": d,
                    "name": name,
                    "zt_close": s.get("close", 0),
                    "circ_mv": s.get("circ_mv", 0),
                    "offset_days": offset_days,
                }
        time.sleep(0.3)

    print(f"  OK 近3-10日有涨停的股票共 {len(zt_stocks)} 只")
    if not zt_stocks:
        return []

    print("  [2/4] 逐只验证回马枪条件...")
    results = []
    total = len(zt_stocks)

    for i, (code, zt_info) in enumerate(zt_stocks.items()):
        offset_days = zt_info["offset_days"]
        if offset_days < 3 or offset_days > 8:
            continue

        circ_mv_yi = zt_info["circ_mv"] / 1e8
        if circ_mv_yi < 30 or circ_mv_yi > 150:
            continue

        zt_date = zt_info["zt_date"]
        pre_date = get_previous_trade_date(zt_date, 5)
        kline = get_stock_kline_sina(code)

        if len(kline) < 5:
            time.sleep(0.1)
            continue

        # 找涨停日
        zt_day_data = None
        for d in kline:
            if d["date"] == zt_date:
                zt_day_data = d
                break

        if not zt_day_data:
            time.sleep(0.1)
            continue

        zt_open = zt_day_data["open"]
        zt_close_p = zt_day_data["close"]
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
        today_data = None
        for d in kline:
            if d["date"] == trade_date:
                today_data = d
                break
        if not today_data:
            today_data = kline[-1]

        today_close = today_data["close"]
        today_vol = today_data["volume"]
        today_low = today_data["low"]

        if today_close <= 0:
            time.sleep(0.1)
            continue

        # 条件3: 回调至涨停实体下沿附近
        if today_close > zt_close_p * 1.02:
            time.sleep(0.1)
            continue
        if today_close < zt_open * 0.98:
            time.sleep(0.1)
            continue

        # 条件4: 缩量至涨停日1/3以下
        if zt_vol > 0 and today_vol > zt_vol / 3:
            time.sleep(0.1)
            continue

        # 条件5: 不破涨停底
        if today_low < zt_open * 0.98:
            time.sleep(0.1)
            continue

        # 条件6: 13日均线支撑
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
        all_stocks = get_all_stocks_sina()
        stock_map = {s["code"]: s for s in all_stocks}
        s = stock_map.get(code, {})
        today_pct = s.get("pct_chg", 0)

        results.append({
            "code": code,
            "name": zt_info["name"],
            "close": today_close,
            "pct_chg": today_pct,
            "circ_mv_yi": round(circ_mv_yi, 2),
            "zt_date": zt_date,
            "callback_days": offset_days,
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
    output.append("")
    output.append(f"=== A股短线选股信号 {trade_date} ===")
    output.append(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    output.append("")
    output.append("【策略一：尾盘缩量回踩均线法】")
    if s1:
        for r in s1:
            output.append(f"  {r['code']} {r['name']:<8} 价:{r['close']:.2f} 涨:{r['pct_chg']:+.2f}% 换:{r['turnover']:.2f}% 市值:{r['circ_mv_yi']:.1f}亿 MA5:{r['ma5']} MA10:{r['ma10']} MA20:{r['ma20']}")
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

    # 保存文件
    out_file = f"短线选股信号_{trade_date}.txt"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"\n结果已保存至: {out_file}")

    return s1, s2


if __name__ == "__main__":
    main()
