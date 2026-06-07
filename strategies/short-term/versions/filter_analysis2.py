# -*- coding: utf-8 -*-
"""二次过滤分析脚本 v2"""
import requests
import sys
import os

# 设置编码
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 策略一初选股票代码
stocks = ['603933','603777','603093','603489','688081','002853','000819','002686','600520','000936','600261','603878','002338','603283','600715','000570','605168','603328','002300','605588','688345','603332','603788','603108','000551','002899','605189']

# 腾讯实时行情接口（批量查询）
symbols = ','.join([f'sh{s}' if s.startswith('6') or s.startswith('5') else f'sz{s}' for s in stocks])
url = f'https://qt.gtimg.cn/q={symbols}'

results = []
try:
    r = requests.get(url, timeout=15)
    lines = r.text.strip().split('\n')

    print('='*100)
    print('策略一初选股实时行情（数据修正版）')
    print('='*100)
    print(f"{'代码':<8} {'名称':<10} {'现价':>8} {'昨收':>8} {'涨跌幅':>8} {'换手率':>8} {'成交额(亿)':>10} {'流通市值(亿)':>12}")
    print('-'*100)

    for line in lines:
        if '~' in line:
            fields = line.split('~')
            if len(fields) > 50:
                code = fields[2]
                name = fields[1]
                price = float(fields[3])
                prev_close = float(fields[4])
                pct = float(fields[32]) if fields[32] else 0
                turnover = float(fields[38]) if fields[38] else 0
                # 成交额字段37是万元，转为亿
                amount_wan = float(fields[37]) if fields[37] else 0
                amount_yi = amount_wan / 10000
                # 流通市值字段44是万元，转为亿
                circ_mv_wan = float(fields[44]) if fields[44] else 0
                circ_mv_yi = circ_mv_wan / 10000

                results.append({
                    'code': code,
                    'name': name,
                    'price': price,
                    'prev_close': prev_close,
                    'pct': pct,
                    'turnover': turnover,
                    'amount_yi': amount_yi,
                    'circ_mv_yi': circ_mv_yi
                })

                print(f"{code:<8} {name:<10} {price:>8.2f} {prev_close:>8.2f} {pct:>+7.2f}% {turnover:>7.2f}% {amount_yi:>10.2f} {circ_mv_yi:>12.2f}")

    print()
    print('='*100)
    print('【二次过滤筛选结果】')
    print('='*100)

    # 基础过滤：股价<20元、流通市值10-200亿、非急拉超5%
    filtered1 = [r for r in results if
        r['price'] < 20 and
        r['circ_mv_yi'] < 200 and
        r['circ_mv_yi'] > 10 and
        abs(r['pct']) <= 5
    ]

    print(f"\n[OK] 条件1（股价<20元、流通市值10-200亿、非急拉超5%）：通过 {len(filtered1)} 只")
    for r in filtered1:
        print(f"   {r['code']} {r['name']} 现价:{r['price']:.2f} 涨幅:{r['pct']:+.2f}% 流通市值:{r['circ_mv_yi']:.1f}亿")

    # 优选：小市值低价（适合小资金）
    filtered2 = [r for r in filtered1 if 5 <= r['price'] <= 15 and r['circ_mv_yi'] < 80]
    print(f"\n[OK] 优选条件（股价5-15元、流通市值<80亿）：{len(filtered2)} 只")
    for r in filtered2:
        print(f"   * {r['code']} {r['name']} 现价:{r['price']:.2f} 涨幅:{r['pct']:+.2f}% 流通市值:{r['circ_mv_yi']:.1f}亿")

    # 稳健选择：价格适中、换手率合理
    filtered3 = [r for r in filtered1 if
        8 <= r['price'] <= 18 and
        r['circ_mv_yi'] < 100 and
        1 <= r['turnover'] <= 6
    ]
    print(f"\n[OK] 稳健条件（股价8-18元、流通市值<100亿、换手率1-6%）：{len(filtered3)} 只")
    for r in filtered3:
        print(f"   {r['code']} {r['name']} 现价:{r['price']:.2f} 涨幅:{r['pct']:+.2f}% 换手:{r['turnover']:.2f}% 流通市值:{r['circ_mv_yi']:.1f}亿")

    print()
    print('='*100)
    print('【未通过二次过滤的股票及原因】')
    print('='*100)
    filtered_codes = set(r['code'] for r in filtered1)
    for r in results:
        if r['code'] not in filtered_codes:
            reasons = []
            if r['price'] >= 20:
                reasons.append(f'股价{r["price"]:.2f}>=20元')
            if r['circ_mv_yi'] >= 200:
                reasons.append(f'流通市值{r["circ_mv_yi"]:.1f}亿>200亿')
            if r['circ_mv_yi'] <= 10:
                reasons.append(f'流通市值{r["circ_mv_yi"]:.1f}亿<10亿(过小)')
            if abs(r['pct']) > 5:
                reasons.append(f'涨跌幅{r["pct"]:+.2f}%超+-5%')
            print(f"   X {r['code']} {r['name']}: {', '.join(reasons)}")

except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
