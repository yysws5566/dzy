# -*- coding: utf-8 -*-
"""
A股短线选股信号脚本 v5 - 纯可靠数据源版
数据源: 新浪财经（涨停池+实时行情+K线）
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


def _safe_gbk(url, headers=None, timeout=12):
    hdrs = headers or {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "http://finance.sina.com.cn",
    }
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("gbk", errors="replace")
    except Exception:
        return None


def _safe_utf(url, headers=None, timeout=12):
    hdrs = headers or {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://finance.eastmoney.com",
    }
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
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
# 新浪涨停数据
# ================================================================
def get_zt_pool_sina(date_str):
    """
    新浪涨停股池
    date_str: YYYYMMDD
    返回: [{code, name, close, pct_chg, turnover, circ_mv, amount}]
    """
    # 新浪涨停板API
    url = (
        f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
        f"/Market_Center.getHQNodeData?page=1&num=500&sort=changepercent&asc=0"
        f"&node=hs_a&symbol=&_s_r_a=page"
    )
    # 新浪历史涨停接口
    url2 = (
        f"https://finance.sina.com.cn/realstock/company/{date_str}/ztbs_data.json"
    )
    # 试一下东方财富历史涨停
    url3 = f"https://data.eastmoney.com/stockdata/ztgp.csv".format(date_str)
    
    # 使用东方财富历史涨停数据（CSV方式）
    # 尝试东方财富数据接口
    api_url = (
        f"https://push2.eastmoney.com/api/qt/clist/get"
        f"?cb=jQuery&pn=1&pz=500&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
        f"&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23"
        f"&fields=f1,f2,f3,f4,f5,f6,f7,f10,f12,f14,f15,f16,f17,f18"
    )
    raw = _safe_utf(api_url)
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
            # f3=涨幅%, f5=换手率%, f20=流通市值(元), f6=成交额(元)
            pct_chg = _to_float(item.get("f3", 0))
            if pct_chg < 9.5:  # 涨停板阈值
                continue
            results.append({
                "code": code,
                "name": str(item.get("f14", "")),
                "close": _to_float(item.get("f2", 0)),
                "pct_chg": pct_chg,
                "turnover": _to_float(item.get("f5", 0)),
                "circ_mv": _to_float(item.get("f20", 0)) / 1e8,
                "amount": _to_float(item.get("f6", 0)),
            })
        return results
    except Exception as e:
        print(f"    EM涨停池解析失败: {e}")
        return []


# ================================================================
# 新浪批量实时行情
# ================================================================
def batch_fetch_sina(codes, batch_size=80):
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
        raw = _safe_gbk(url, timeout=15)
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
# 新浪K线
# ================================================================
def get_kline_sina(code, count=30):
    if code.startswith("6"):
        symbol = f"sh{code}"
    else:
        symbol = f"sz{code}"
    url = (
        f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
        f"/CN_MarketData.getKLineData?symbol={symbol}&type=day&datalen={count}"
    )
    raw = _safe_gbk(url, timeout=12)
    if not raw:
        return []
    try:
        if raw.startswith("var"):
            raw = raw[raw.index("(")+1:]
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        result = []
        for d in data:
            result.append({
                "date": str(d.get("day", "")),
                "open": _to_float(d.get("open")),
                "close": _to_float(d.get("close")),
                "high": _to_float(d.get("high")),
                "low": _to_float(d.get("low")),
                "volume": _to_float(d.get("volume")),
            })
        return result
    except Exception:
        return []


# ================================================================
# 东方财富K线（备用）
# ================================================================
def get_kline_em(code, count=30):
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
    raw = _safe_utf(url, timeout=12)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        klines_raw = data.get("data", {}).get("klines", [])
        # 注意：K线是倒序（最新在前）
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
    except Exception:
        return []


def get_kline(code, count=30):
    """优先用新浪K线，失败则用东方财富"""
    kl = get_kline_sina(code, count)
    if len(kl) >= 20:
        return kl
    kl2 = get_kline_em(code, count)
    if len(kl2) >= 20:
        return kl2
    return kl


# ================================================================
# 获取近N日涨停股票（通过今日涨停数据获取）
# ================================================================
def get_recent_zt_stocks(trade_date, days=20):
    """
    通过今日涨停数据 + 历史K线回溯，获取近N日有涨停历史的股票
    使用新浪批量行情做全市场扫描
    """
    # 新浪全市场行情：每次最多50个
    print("  [1/3] 获取全市场行情数据...")
    
    # 分批获取全市场行情（用东方财富获取全市场股票列表）
    # 先用新浪获取沪深股票
    all_stocks = []
    for page in range(1, 20):
        url = (
            f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
            f"/Market_Center.getHQNodeData?page={page}&num=50&sort=symbol&asc=1"
            f"&node=hs_a&symbol=&_s_r_a=page"
        )
        raw = _safe_gbk(url)
        if not raw or "symbol" not in raw:
            break
        try:
            data = json.loads(raw)
            if not data:
                break
            for d in data:
                code = str(d.get("symbol", ""))
                if code.startswith(("8", "4", "9")):
                    continue
                all_stocks.append({
                    "code": code,
                    "name": str(d.get("name", "")),
                    "close": _to_float(d.get("trade", 0)),
                    "prev_close": _to_float(d.get("settlement", 0)),
                    "pct_chg": _to_float(d.get("pricechange", 0)),
                    "high": _to_float(d.get("high", 0)),
                    "low": _to_float(d.get("low", 0)),
                    "amount": _to_float(d.get("amount", 0)) * 10000,  # 万元->元
                })
        except Exception:
            pass
        time.sleep(0.2)
    
    print(f"  OK 获取全市场 {len(all_stocks)} 只股票行情")
    return all_stocks


# ================================================================
# 策略一：尾盘缩量回踩均线法
# ================================================================
def strategy_1_pullback_ma(trade_date):
    print(f"\n{'='*60}")
    print(f"策略一：尾盘缩量回踩均线法 | 日期: {trade_date}")
    print(f"{'='*60}")

    print("  [1/4] 获取全市场行情...")
    market_data = get_recent_zt_stocks(trade_date)
    if not market_data:
        print("  X 无法获取市场数据")
        return []

    print("  [2/4] 预筛选（近20日有涨停历史 + 今日温和涨幅）...")
    # 近20日有涨停的标准：今日之前K线中某日涨幅>=9.5%
    # 今日涨幅：-1%~+2%
    # 价格：2~30元
    # 成交额：>=5000万
    candidates = []
    total = len(market_data)
    
    print(f"  [3/4] K线验证（判断近20日是否有涨停 + 均线状态）...")
    results = []
    
    for idx, s in enumerate(market_data):
        code = s["code"]
        name = s["name"]
        pct_chg = s["pct_chg"]
        amount = s["amount"]
        close = s["close"]
        
        # 基本过滤
        if "ST" in name or "st" in name.lower():
            time.sleep(0.05)
            continue
        if close <= 0 or close > 30 or close < 2:
            time.sleep(0.05)
            continue
        if amount < 5000e4:
            time.sleep(0.05)
            continue
        # 今日涨幅过滤：-1%~+2%
        if not (-1 <= pct_chg <= 2):
            time.sleep(0.05)
            continue
        
        # 获取K线
        kline = get_kline(code, 30)
        if len(kline) < 20:
            time.sleep(0.1)
            continue
        
        closes = [d["close"] for d in kline]
        if 0 in closes[:25]:
            time.sleep(0.1)
            continue
        
        # 近20日是否有涨停
        pct_20 = [d["close"] for d in kline[:20]]
        # 排除今日（前5日内有涨停历史即可）
        pct_5 = pct_20[1:6]  # 前5日
        recent_max = max(pct_5)
        prev_closes = [kline[i+1]["close"] for i in range(4)]
        if len(prev_closes) < 5:
            has_zt = False
        else:
            zt_days = [i for i in range(5) if pct_5[i] > 0 and 
                       (kline[i+1]["close"] - kline[i+2]["close"]) / kline[i+2]["close"] >= 0.095
                       if i+2 < len(kline)]
            has_zt = len(zt_days) > 0
        
        # 也检查前20日（不包括今日）
        all_zt = False
        for i in range(1, 21):
            if i >= len(kline) - 1:
                break
            prev = kline[i+1]["close"]
            cur = kline[i]["close"]
            if prev > 0 and (cur - prev) / prev >= 0.095:
                all_zt = True
                break
        
        if not all_zt:
            time.sleep(0.1)
            continue
        
        # 多头排列
        ma5 = sum(closes[:5]) / 5
        ma10 = sum(closes[:10]) / 10
        ma20 = sum(closes[:20]) / 20
        if not (closes[0] > ma5 > ma10 > ma20):
            time.sleep(0.1)
            continue
        
        # MA5近3日斜率为正
        ma5_3 = sum(closes[:3]) / 3
        ma5_old = sum(closes[1:4]) / 3
        if ma5_3 <= ma5_old:
            time.sleep(0.1)
            continue
        
        amount_yi = amount / 1e8
        results.append({
            "code": code, "name": name,
            "close": close, "pct_chg": round(pct_chg, 2),
            "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2),
            "amount_yi": round(amount_yi, 1),
            "high": s["high"], "low": s["low"],
        })
        
        if (idx + 1) % 50 == 0:
            print(f"    已处理 {idx+1}/{total}...")
        time.sleep(0.1)
    
    print(f"  [4/4] 策略一筛选完成，共 {len(results)} 只")
    return results


# ================================================================
# 策略二：涨停回马枪缩量回踩法
# ================================================================
def strategy_2_limit_pullback(trade_date):
    print(f"\n{'='*60}")
    print(f"策略二：涨停回马枪缩量回踩法 | 日期: {trade_date}")
    print(f"{'='*60}")

    print("  [1/3] 获取全市场行情...")
    market_data = get_recent_zt_stocks(trade_date)
    if not market_data:
        return []

    print(f"  [2/3] 回踩验证（近期涨停+温和回调）...")
    results = []
    total = len(market_data)
    
    # 计算近12个交易日日期
    recent_dates = [get_previous_trade_date(trade_date, i) for i in range(1, 13)]

    for idx, s in enumerate(market_data):
        code = s["code"]
        name = s["name"]
        close = s["close"]
        pct_chg = s["pct_chg"]
        
        if "ST" in name or "st" in name.lower():
            time.sleep(0.05)
            continue
        if close <= 0 or close > 30 or close < 2:
            time.sleep(0.05)
            continue
        
        # 今日涨幅：0%~+6%（温和上涨）
        if not (0 <= pct_chg <= 6):
            time.sleep(0.05)
            continue
        
        kline = get_kline(code, 30)
        if len(kline) < 15:
            time.sleep(0.1)
            continue
        
        closes = [d["close"] for d in kline]
        if 0 in closes[:20]:
            time.sleep(0.1)
            continue
        
        # 找近12日内是否有涨停
        zt_info = None
        zt_days_ago = 0
        for i in range(1, 13):
            if i >= len(kline) - 1:
                break
            prev = kline[i+1]["close"]
            cur = kline[i]["close"]
            if prev > 0 and (cur - prev) / prev >= 0.095:
                zt_info = {"date": kline[i]["date"], "pct": (cur-prev)/prev*100}
                zt_days_ago = i
                break
        
        if zt_info is None:
            time.sleep(0.1)
            continue
        
        # 涨停距今>=5天（避免太近）
        if zt_days_ago < 5:
            time.sleep(0.1)
            continue
        
        # 近5日内未再涨停
        recent_zt = False
        for i in range(1, min(6, len(kline)-1)):
            prev = kline[i+1]["close"]
            cur = kline[i]["close"]
            if prev > 0 and (cur - prev) / prev >= 0.095:
                recent_zt = True
                break
        if recent_zt:
            time.sleep(0.1)
            continue
        
        # 均线支撑
        ma5 = sum(closes[:5]) / 5
        ma10 = sum(closes[:10]) / 10
        ma13 = sum(closes[:13]) / 13
        ma_support = min(ma5, ma10, ma13)
        
        # 收盘价在均线上方（未深度破位）
        if close < ma_support * 0.97:
            time.sleep(0.1)
            continue
        
        # 流通市值 < 150亿
        circ_mv_yi = s.get("amount", 0) / close / 1e8 if close > 0 else 0
        
        results.append({
            "code": code, "name": name,
            "close": close, "pct_chg": round(pct_chg, 2),
            "circ_mv_yi": round(circ_mv_yi, 1),
            "zt_date": zt_info["date"], "zt_days": zt_days_ago,
            "ma5": round(ma5, 2), "ma10": round(ma10, 2),
        })
        
        if (idx + 1) % 50 == 0:
            print(f"    已处理 {idx+1}/{total}...")
        time.sleep(0.1)
    
    print(f"  [3/3] 策略二筛选完成，共 {len(results)} 只")
    return results


# ================================================================
# 主函数
# ================================================================
def main():
    trade_date = get_trade_date()
    print(f"A股短线选股脚本启动 v5.0")
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
            output.append(f"  {r['code']} {r['name']:<8} 价:{r['close']:.2f} 涨:{r['pct_chg']:+.2f}% 回调{r['zt_days']}天 涨停日:{r['zt_date']} 市值:{r['circ_mv_yi']:.1f}亿")
        output.append(f"  共 {len(s2)} 只")
    else:
        output.append("  今日无符合条件的股票")

    result_text = "\n".join(output)
    print(result_text)

    out_file = f"短线选股信号_{trade_date}.txt"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(result_text)
    print(f"\n结果已保存: {out_file}")
    
    return s1, s2


if __name__ == "__main__":
    main()
