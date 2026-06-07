# -*- coding: utf-8 -*-
"""
大盘环境检查 + 选股脚本 v2.0
"""
import os, sys
os.chdir(r'c:\Users\西西家的咩咩\WorkBuddy\20260426121709')
sys.path.insert(0, r'c:\Users\西西家的咩咩\WorkBuddy\20260426121709')

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import json
import urllib.request
import time
from datetime import datetime

from data_fetcher import MarketData, get_trade_date


def get_rt_index(code, name):
    """通过腾讯接口获取指数实时行情"""
    url = f'https://qt.gtimg.cn/q=sh{code}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://gu.qq.com/'
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read().decode('gbk', errors='replace').strip()
        if '~' in text:
            fields = text.split('~')
            return {
                'code': code,
                'name': name,
                'close': float(fields[3]),
                'pct_chg': float(fields[32]) * 100 if fields[32] else 0,
                'pre_close': float(fields[4]) if fields[4] else 0,
            }
    except Exception as e:
        print(f'    [警告] {name} 实时数据获取失败: {e}')
    return None


def check_market_env():
    """检查大盘环境"""
    print('=' * 60)
    print('[大盘环境检查]')
    print('=' * 60)

    md = MarketData(verbose=False)

    # 获取实时指数
    sh_rt = get_rt_index('000001', 'shangzheng')
    cyb_rt = get_rt_index('399006', 'chuangyeban')

    # 获取历史数据计算MA20
    sh_df = md.get_index_daily('000001', 25)
    cyb_df = md.get_index_daily('399006', 25)

    if sh_df.empty:
        print('  [错误] 无法获取上证指数历史数据')
        return None

    sh_closes = sh_df['close'].dropna().tolist()
    if len(sh_closes) < 20:
        print('  [错误] 上证指数数据不足20天')
        return None

    sh_close = sh_closes[-1]
    sh_ma20 = sum(sh_closes[-20:]) / 20
    sh_pct = ((sh_closes[-1] / sh_closes[-2]) - 1) * 100 if len(sh_closes) > 1 else 0

    cyb_ma20 = None
    cyb_close = None
    cyb_pct = 0
    if not cyb_df.empty:
        cyb_closes = cyb_df['close'].dropna().tolist()
        if len(cyb_closes) >= 20:
            cyb_close = cyb_closes[-1]
            cyb_ma20 = sum(cyb_closes[-20:]) / 20
            cyb_pct = ((cyb_closes[-1] / cyb_closes[-2]) - 1) * 100 if len(cyb_closes) > 1 else 0

    print(f'  上证指数: {sh_close:.2f}  MA20={sh_ma20:.2f}  涨跌幅={sh_pct:+.2f}%')
    if cyb_ma20:
        cyb_display = f'{cyb_close:.2f}' if cyb_close else 'N/A'
        print(f'  创业板指: {cyb_display}  MA20={cyb_ma20:.2f}  涨跌幅={cyb_pct:+.2f}%')
    if sh_rt:
        print(f'  (实时: 上证 {sh_rt["close"]:.2f} {sh_rt["pct_chg"]:+.2f}%)')
    if cyb_rt and cyb_close:
        print(f'  (实时: 创业板 {cyb_rt["close"]:.2f} {cyb_rt["pct_chg"]:+.2f}%)')

    env_pass = True
    reasons = []

    if sh_rt:
        sh_close_rt = sh_rt['close']
        if sh_close_rt <= sh_ma20:
            env_pass = False
            reasons.append(f'shangzheng({sh_close_rt:.2f}) <= MA20({sh_ma20:.2f})')
        if sh_rt['pct_chg'] <= -1:
            env_pass = False
            reasons.append(f'shangzheng跌幅({sh_rt["pct_chg"]:.2f}%) > -1%')

    if cyb_rt and cyb_ma20:
        if cyb_rt['close'] <= cyb_ma20:
            env_pass = False
            reasons.append(f'chuangyeban({cyb_rt["close"]:.2f}) <= MA20({cyb_ma20:.2f})')

    print()
    if env_pass:
        print('[OK] 大盘环境满足，开始执行选股')
    else:
        print('[FAIL] 大盘环境不满足，暂停选股')
        for r in reasons:
            print(f'  - {r}')
        return None

    return {
        'sh_close': sh_close,
        'sh_ma20': sh_ma20,
        'sh_pct': sh_pct,
        'sh_rt_close': sh_rt['close'] if sh_rt else sh_close,
        'sh_rt_pct': sh_rt['pct_chg'] if sh_rt else sh_pct,
        'cyb_close': cyb_close,
        'cyb_ma20': cyb_ma20,
        'cyb_pct': cyb_pct,
        'cyb_rt_close': cyb_rt['close'] if cyb_rt else cyb_close,
        'cyb_rt_pct': cyb_rt['pct_chg'] if cyb_rt else cyb_pct,
    }


def run_overnight_screener():
    """运行隔夜选股脚本"""
    # 动态导入避免循环依赖
    import importlib
    mod = importlib.import_module('overnight_screener')
    return mod.run_screener(), mod.format_output


def main():
    trade_date = get_trade_date()
    print(f'\n高胜率隔夜策略选股 v2.0 | 交易日期: {trade_date}')
    print(f'运行时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print()

    # 大盘环境检查
    env = check_market_env()
    if env is None:
        return

    # 运行选股
    print('=' * 60)
    print('[选股执行]')
    print('=' * 60)
    results, fmt_fn = run_overnight_screener()
    output = fmt_fn(results, trade_date)
    print(output)

    # 保存文件
    output_dir = os.path.dirname(os.path.abspath(__file__))
    output_file = os.path.join(output_dir, f'隔夜策略选股_{trade_date}.txt')
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(output)
    print(f'\n结果已保存至: {output_file}')


if __name__ == '__main__':
    main()