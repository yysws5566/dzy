#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股短线选股 v7.0 - 全新浪数据源
依赖: 新浪全市场行情 + 新浪K线
大数据量用新浪API完成
"""
import json, urllib.request, time
from datetime import datetime

def fetch_sina_market(page=1, num=100):
    url = (f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"Market_Center.getHQNodeData?page={page}&num={num}"
            f"&sort=changepercent&asc=0&node=hs_a&symbol=&_s_r_a=page")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://finance.sina.com.cn/"
    })
    resp = urllib.request.urlopen(req, timeout=15)
    data = resp.read().decode("gbk", errors="replace")
    return json.loads(data)

def fetch_kline_sina(symbol, count=30):
    """symbol: sh600000 or sz000001"""
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={count}")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn/"
    })
    resp = urllib.request.urlopen(req, timeout=10)
    data = resp.read().decode("gbk", errors="replace")
    klines = json.loads(data)
    # 按日期正序排序
    klines_sorted = sorted(klines, key=lambda x: x["day"])
    result = []
    for k in klines_sorted:
        result.append({
            "date": k["day"],
            "close": float(k["close"]),
        })
    return result

def safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default

def check_stock(s, kline, strategy_type):
    """
    s: 新浪行情字典
    kline: 正序列表 [最早, ..., 今天]
    strategy_type: 's1' or 's2'
    """
    closes = [d["close"] for d in kline]
    if len(closes) < 20:
        return None
    if any(c <= 0 for c in closes[-20:]):
        return None

    today_close = closes[-1]
    ma20 = sum(closes[-20:]) / 20
    ma10 = sum(closes[-10:]) / 10
    ma5  = sum(closes[-5:])  / 5

    pct = safe_float(s.get("changepercent", 0))
    price = safe_float(s.get("trade", 0))
    amount = safe_float(s.get("amount", 0))
    nmc = safe_float(s.get("nmc", 0))  # 流通市值(万)
    turnover = safe_float(s.get("turnoverratio", 0))

    if strategy_type == "s1":
        # 策略一：尾盘缩量回踩均线法
        # 条件：涨幅-1%~+2%，多头排列，MA5斜率正，近20日有涨停
        if not (-1 <= pct <= 2):
            return None
        # 多头排列
        if not (today_close > ma5 > ma10 > ma20):
            return None
        # MA5斜率（近3日 vs 前3日）
        if len(closes) >= 6:
            ma5_now = sum(closes[-3:]) / 3
            ma5_prev = sum(closes[-6:-3]) / 3
            if ma5_now <= ma5_prev:
                return None
        # 近20日有涨停（不含今天）
        has_zt = False
        for i in range(len(closes)-2, max(len(closes)-21, 0), -1):
            prev = closes[i-1]
            cur = closes[i]
            if prev > 0 and (cur - prev) / prev >= 0.095:
                has_zt = True
                break
        if not has_zt:
            return None
        return {
            "code": s.get("code", ""),
            "name": s.get("name", ""),
            "price": price,
            "pct": pct,
            "turn": turnover,
            "nmc": nmc / 10000,
            "amount": amount / 1e8,
            "ma5": round(ma5, 2),
            "ma10": round(ma10, 2),
            "ma20": round(ma20, 2),
        }

    elif strategy_type == "s2":
        # 策略二：涨停回马枪缩量回踩法
        # 涨幅0%~+6%，近12日内涨停距今>=5天，近5日未再涨停，均线支撑
        if not (0 <= pct <= 6):
            return None
        # 找涨停日（近12日，不含今天）
        zt_idx = -1
        for i in range(len(closes)-2, max(len(closes)-13, 0), -1):
            if i > 0:
                prev = closes[i-1]
                cur = closes[i]
                if prev > 0 and (cur - prev) / prev >= 0.095:
                    zt_idx = i
                    break
        if zt_idx < 0:
            return None
        days_since_zt = (len(closes) - 1) - zt_idx
        if days_since_zt < 5:
            return None
        # 近5日未再涨停
        for i in range(len(closes)-2, max(len(closes)-6, 0), -1):
            if i > 0:
                if (closes[i] - closes[i-1]) / closes[i-1] >= 0.095:
                    return None  # 近5日又涨停了
        # 均线支撑（收盘在MA10附近或以上）
        if today_close < ma10 * 0.97:
            return None
        return {
            "code": s.get("code", ""),
            "name": s.get("name", ""),
            "price": price,
            "pct": pct,
            "turn": turnover,
            "nmc": nmc / 10000,
            "amount": amount / 1e8,
            "zt_gap": days_since_zt,
            "ma5": round(ma5, 2),
            "ma10": round(ma10, 2),
            "ma20": round(ma20, 2),
        }

    return None

def main():
    trade_date = datetime.now().strftime("%Y%m%d")
    print("="*60)
    print(f"大盘环境 + 短线选股 v7.0 | 日期: {trade_date}")
    print("="*60)

    # 步骤1：获取全市场行情
    print("\n[1/4] 获取全市场实时行情...")
    all_stocks = []
    for page in range(1, 60):
        try:
            data = fetch_sina_market(page=page, num=100)
            if not data:
                break
            all_stocks.extend(data)
            if len(data) < 100:
                break
            time.sleep(0.15)
        except Exception as e:
            print(f"  第{page}页失败: {e}")
            break
    print(f"  OK 全市场共 {len(all_stocks)} 只")

    # 预筛选 — 策略一：涨幅-1%~+2%，价格<20元，成交额>5000万
    s1_pool = []
    for s in all_stocks:
        name = s.get("name", "")
        if "ST" in name or "N" in name:
            continue
        pct = safe_float(s.get("changepercent", 999))
        price = safe_float(s.get("trade", 0))
        amount = safe_float(s.get("amount", 0))
        nmc = safe_float(s.get("nmc", 0))
        if not (-1 <= pct <= 2):
            continue
        if price < 2 or price > 20:
            continue
        if amount < 50000000:
            continue
        if nmc > 1500000:
            continue
        s1_pool.append(s)

    # 预筛选 — 策略二：涨幅0%~+6%，价格<20元
    s2_pool = []
    for s in all_stocks:
        name = s.get("name", "")
        if "ST" in name or "N" in name:
            continue
        pct = safe_float(s.get("changepercent", 999))
        price = safe_float(s.get("trade", 0))
        amount = safe_float(s.get("amount", 0))
        nmc = safe_float(s.get("nmc", 0))
        if not (0 <= pct <= 6):
            continue
        if price < 2 or price > 20:
            continue
        if amount < 50000000:
            continue
        if nmc > 1500000:
            continue
        s2_pool.append(s)

    print(f"  策略一预筛选: {len(s1_pool)} 只")
    print(f"  策略二预筛选: {len(s2_pool)} 只")

    # 合并去重
    all_to_check = {}
    for s in s1_pool:
        sym = s.get("symbol", "")
        if sym not in all_to_check:
            all_to_check[sym] = {"s1": True, "s2": False, "data": s}
        else:
            all_to_check[sym]["s1"] = True
    for s in s2_pool:
        sym = s.get("symbol", "")
        if sym not in all_to_check:
            all_to_check[sym] = {"s1": False, "s2": True, "data": s}
        else:
            all_to_check[sym]["s2"] = True

    print(f"  需K线验证: {len(all_to_check)} 只")

    # 步骤2：K线验证
    print("\n[2/4] K线验证（均线+涨停历史）...")
    s1_results = []
    s2_results = []
    checked = 0
    kline_fail = 0

    for sym, info in all_to_check.items():
        checked += 1
        try:
            kline = fetch_kline_sina(sym, count=30)
        except Exception:
            kline_fail += 1
            time.sleep(0.05)
            continue

        if not kline or len(kline) < 20:
            kline_fail += 1
            time.sleep(0.05)
            continue

        s = info["data"]

        if info["s1"]:
            r = check_stock(s, kline, "s1")
            if r:
                s1_results.append(r)

        if info["s2"]:
            r = check_stock(s, kline, "s2")
            if r:
                s2_results.append(r)

        if checked % 50 == 0:
            print(f"  已验证 {checked}/{len(all_to_check)} | "
                  f"策略一:{len(s1_results)} 策略二:{len(s2_results)} | "
                  f"K线失败:{kline_fail}")
        time.sleep(0.05)

    print(f"  OK 验证完成: 共{checked}只，K线失败{kline_fail}只")
    print(f"  策略一通过: {len(s1_results)} 只")
    print(f"  策略二通过: {len(s2_results)} 只")

    # 输出
    print("\n" + "="*60)
    print("【策略一：尾盘缩量回踩均线法】")
    print("="*60)
    if s1_results:
        # 按成交额降序
        for r in sorted(s1_results, key=lambda x: -x["amount"]):
            print(f"  {r['code']} {r['name']:<8} 价:{r['price']:.2f}  "
                  f"涨:{r['pct']:+.2f}% 换手:{r['turn']:.1f}%  "
                  f"流通:{r['nmc']:.1f}亿  "
                  f"MA5>{r['ma5']:.2f}>MA10>{r['ma10']:.2f}>MA20>{r['ma20']:.2f}")
        print(f"  共 {len(s1_results)} 只")
    else:
        print("  今日无符合条件的股票")

    print()
    print("="*60)
    print("【策略二：涨停回马枪缩量回踩法】")
    print("="*60)
    if s2_results:
        for r in sorted(s2_results, key=lambda x: -x["amount"]):
            print(f"  {r['code']} {r['name']:<8} 价:{r['price']:.2f}  "
                  f"涨:{r['pct']:+.2f}% 换手:{r['turn']:.1f}%  "
                  f"流通:{r['nmc']:.1f}亿 涨停距今{r['zt_gap']}天  "
                  f"MA5>{r['ma5']:.2f}>MA10>{r['ma10']:.2f}>MA20>{r['ma20']:.2f}")
        print(f"  共 {len(s2_results)} 只")
    else:
        print("  今日无符合条件的股票")

    # 保存结果
    out_lines = []
    out_lines.append(f"=== A股短线选股信号 {trade_date} ===")
    out_lines.append(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    out_lines.append("")
    out_lines.append("【策略一：尾盘缩量回踩均线法】")
    if s1_results:
        for r in sorted(s1_results, key=lambda x: -x["amount"]):
            out_lines.append(f"  {r['code']} {r['name']} 价:{r['price']:.2f} "
                            f"涨:{r['pct']:+.2f}% 换手:{r['turn']:.1f}% "
                            f"流通:{r['nmc']:.1f}亿 "
                            f"MA5>{r['ma5']:.2f}>MA10>{r['ma10']:.2f}>MA20>{r['ma20']:.2f}")
        out_lines.append(f"  共 {len(s1_results)} 只")
    else:
        out_lines.append("  今日无符合条件的股票")

    out_lines.append("")
    out_lines.append("【策略二：涨停回马枪缩量回踩法】")
    if s2_results:
        for r in sorted(s2_results, key=lambda x: -x["amount"]):
            out_lines.append(f"  {r['code']} {r['name']} 价:{r['price']:.2f} "
                            f"涨:{r['pct']:+.2f}% 换手:{r['turn']:.1f}% "
                            f"流通:{r['nmc']:.1f}亿 涨停距今{r['zt_gap']}天 "
                            f"MA5>{r['ma5']:.2f}>MA10>{r['ma10']:.2f}>MA20>{r['ma20']:.2f}")
        out_lines.append(f"  共 {len(s2_results)} 只")
    else:
        out_lines.append("  今日无符合条件的股票")

    out_text = "\n".join(out_lines)
    out_file = f"短线选股信号_{trade_date}.txt"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(out_text)
    print(f"\n结果已保存: {out_file}")

    # 保存JSON供二次过滤
    import os
    json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_screener_raw_results.json")
    with open("_screener_raw_results.json", "w", encoding="utf-8") as f:
        json.dump({"strategy1": s1_results, "strategy2": s2_results}, f, ensure_ascii=False, indent=2)
    print(f"原始JSON已保存: _screener_raw_results.json")

if __name__ == "__main__":
    main()
