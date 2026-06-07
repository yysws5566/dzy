"""
交易日定时调度器
================
每个交易日下午2:30自动唤醒，2:40准时执行尾盘选股扫描。

两种运行模式：
  python scheduler.py              单次模式：等到今天14:40→扫描→退出
  python scheduler.py --daemon     守护模式：持续运行，每天自动扫描

单次模式适合配合 Windows 任务计划程序：
  1. 打开"任务计划程序" (taskschd.msc)
  2. 创建基本任务 → 触发器: 每天 14:30
  3. 操作: 启动程序 → python scheduler.py
  4. 完成。每个交易日14:30自动触发。

也适合 cron (Linux/Mac)：
  crontab -e
  30 14 * * 1-5 cd /path/to/project && python scheduler.py
"""

import encoding_fix  # noqa
import datetime
import json
import os
import signal
import sys
import time
from typing import Optional

from trading_calendar import get_calendar


# ================================================================
# 配置
# ================================================================

# 扫描时间（下午2:40）
SCAN_HOUR = 14
SCAN_MINUTE = 40

# 提前唤醒时间（提前10分钟，用于数据准备）
WAKEUP_BEFORE = 10  # 分钟

# 扫描模式: merged / tail
SCAN_MODE = "merged"


def is_trading_day(date: datetime.date = None) -> bool:
    """判断是否交易日"""
    calendar = get_calendar()
    is_trade, _ = calendar.is_trading_day(date)
    return is_trade


def wait_until_scan_time() -> bool:
    """
    等待到14:40

    如果当前时间已经过了14:40，返回False。
    如果还没到，精确等到14:30（提前10分钟唤醒）。
    """
    now = datetime.datetime.now()

    # 目标时间
    wakeup_time = now.replace(
        hour=SCAN_HOUR, minute=SCAN_MINUTE - WAKEUP_BEFORE,
        second=0, microsecond=0,
    )
    scan_time = now.replace(
        hour=SCAN_HOUR, minute=SCAN_MINUTE,
        second=0, microsecond=0,
    )

    # 如果已经过了扫描时间
    if now >= scan_time:
        print(f"  ⏰ 当前时间 {now.strftime('%H:%M')} 已过扫描时间 {SCAN_MINUTE}:{SCAN_MINUTE}，跳过")
        return False

    # 计算等待秒数
    wait_seconds = (wakeup_time - now).total_seconds()

    if wait_seconds > 0:
        print(f"  💤 等待 {wait_seconds/60:.0f} 分钟后在 {wakeup_time.strftime('%H:%M')} 唤醒...")
        # 分段sleep，每5分钟打印一次状态
        while wait_seconds > 0:
            sleep_chunk = min(300, wait_seconds)  # 最多睡5分钟
            time.sleep(sleep_chunk)
            wait_seconds -= sleep_chunk
            if wait_seconds > 0:
                remaining = wait_seconds / 60
                print(f"     ⏳ 距扫描还有 {remaining:.0f} 分钟...")

    print(f"  ⏰ 已到 {datetime.datetime.now().strftime('%H:%M')}，准备扫描...")
    return True


def run_scan() -> dict:
    """
    执行尾盘选股扫描

    Returns:
        {"signals": [...], "file": "..."} 或空
    """
    print(f"\n{'='*60}")
    print(f"  🚀 {SCAN_MODE.upper()} 尾盘选股自动扫描")
    print(f"  📅 {datetime.date.today().isoformat()}  {datetime.datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}\n")

    # 导入扫描模块
    try:
        from tickflow_client import get_client
        from merged_scanner import MergedScanner
        from tail_scanner import TailScanner
        HAS_TICKFLOW = True
    except ImportError as e:
        print(f"  ❌ 模块导入失败: {e}")
        return {}

    # 连接TickFlow
    print("  📡 连接TickFlow API...")
    try:
        client = get_client()
        test = client.get_realtime_quotes(["600519.SH"])
        if not test:
            raise RuntimeError("行情获取失败")
        print("     ✅ 连接正常")
    except Exception as e:
        print(f"     ❌ TickFlow连接失败: {e}")
        return {}

    # 获取候选池
    print("  📋 构建候选池...")
    try:
        universes = client.list_universes(region="CN", category="equity")
        sw1 = [u for u in universes if "SW1" in u["id"]]
        all_symbols = set()
        for u in sw1[:12]:
            try:
                syms = client.get_universe_symbols(u["id"])
                all_symbols.update(syms[:10])
            except Exception:
                continue
        if not all_symbols:
            all_symbols = {
                "600519.SH","600036.SH","600030.SH","601012.SH",
                "000858.SZ","000001.SZ","002594.SZ","300750.SZ",
                "300059.SZ","688981.SH","601318.SH","600887.SH",
                "600900.SH","000333.SZ","002415.SZ","600373.SH",
                "603096.SH","688169.SH","000887.SZ","300912.SZ",
            }
        symbols_list = list(all_symbols)
        print(f"     {len(symbols_list)} 只")
    except Exception as e:
        print(f"     ⚠️ {e}，使用预设池")
        symbols_list = [
            "600519.SH","000001.SZ","300750.SZ","601012.SH",
            "000858.SZ","002594.SZ","300059.SZ","688981.SH",
            "600036.SH","601318.SH","600030.SH","600887.SH",
            "000333.SZ","002415.SZ","300274.SZ","000568.SZ",
            "600900.SH","600373.SH","603096.SH","688169.SH",
        ]

    # 加载权重
    weights = None
    buy_thresh = 0.55
    reports_dir = os.path.join(os.path.dirname(__file__), "reports")

    try:
        merged_w = os.path.join(reports_dir, "merged_weights.json")
        overnight_w = os.path.join(reports_dir, "overnight_optimal.json")

        if os.path.exists(merged_w):
            with open(merged_w, "r", encoding="utf-8") as f:
                wdata = json.load(f)
            weights = wdata.get("weights")
            recs = wdata.get("total_records", 0)
            print(f"     ✅ 复盘权重 ({recs}笔实盘反馈)")
        elif os.path.exists(overnight_w):
            with open(overnight_w, "r", encoding="utf-8") as f:
                wdata = json.load(f)
            old = wdata.get("weights", {})
            weights = {
                "tail_volume": old.get("tail_volume", 0.15),
                "intraday_trend": old.get("intraday_trend", 0.13),
                "volume_accum": old.get("volume_accum", 0.10),
                "auction": old.get("auction", 0.12),
                "gap": old.get("gap", 0.10),
                "reversal": old.get("reversal", 0.08),
                "ma_position": old.get("ma_position", 0.07),
                "seal_quality": old.get("seal_quality", 0.10),
                "global_linkage": old.get("global_linkage", 0.07),
                "overnight_risk": old.get("overnight_risk", 0.08),
            }
            total = sum(weights.values())
            weights = {k: v/total for k, v in weights.items()}
            print(f"     ✅ 隔夜优化权重")
    except Exception:
        pass

    # 执行扫描
    print(f"\n  🔍 执行 {SCAN_MODE.upper()} 扫描...")
    try:
        if SCAN_MODE == "merged":
            scanner = MergedScanner(client=client, weights=weights, buy_threshold=buy_thresh)
            signals = scanner.scan(symbols_list, refine_top=20)
        else:
            scanner = TailScanner(client=client, weights=weights, buy_threshold=buy_thresh)
            signals = scanner.scan(symbols_list)
    except Exception as e:
        print(f"  ❌ 扫描异常: {e}")
        import traceback
        traceback.print_exc()
        return {}

    if not signals:
        print("  ⚠️ 今日无信号")
        return {}

    # 输出结果
    today = datetime.date.today()
    print(f"\n  {'='*50}")
    print(f"  📊 {today} 尾盘选股结果")
    print(f"  {'='*50}")
    print(f"  买入信号: {len(signals)} 只")
    strong = [s for s in signals if getattr(s, 'signal', '') == "STRONG_BUY"]
    print(f"    强买入: {len(strong)} | 普通: {len(signals) - len(strong)}")
    print(f"  {'='*50}")

    for i, s in enumerate(signals[:15], 1):
        price = getattr(s, 'current_price', 0)
        chg = getattr(s, 'change_pct', 0) * 100
        score = getattr(s, 'total_score', 0)
        sig = getattr(s, 'signal', 'BUY')
        pos = getattr(s, 'position_pct', 0) * 100
        icon = "🔥" if sig == "STRONG_BUY" else "📈"
        print(f"  {i:>2}. {s.symbol} {s.name:<8} {price:>8.2f} {chg:+.2f}%  "
              f"{score:.3f} {icon} {pos:.0f}%")

    # 保存信号文件
    signal_items = []
    for s in signals:
        item = {
            "symbol": s.symbol,
            "name": s.name,
            "date": today.isoformat(),
            "buy_price": getattr(s, 'current_price', 0),
            "total_score": getattr(s, 'total_score', 0),
            "signal": getattr(s, 'signal', 'BUY'),
            "position_pct": getattr(s, 'position_pct', 0),
            "change_pct": getattr(s, 'change_pct', 0),
            "factor_scores": {},
        }
        for fn in ["tail_volume", "intraday_trend", "volume_accum", "auction",
                    "gap", "reversal", "ma_position", "seal_quality",
                    "global_linkage", "overnight_risk"]:
            val = getattr(s, fn, None)
            if val is not None:
                item["factor_scores"][fn] = round(val, 4)
        signal_items.append(item)

    os.makedirs(reports_dir, exist_ok=True)
    signal_file = os.path.join(reports_dir, f"signals_{today.isoformat()}.json")
    with open(signal_file, "w", encoding="utf-8") as f:
        json.dump({"date": today.isoformat(), "method": SCAN_MODE, "signals": signal_items},
                  f, ensure_ascii=False, indent=2)

    print(f"\n  💾 信号已保存: {signal_file}")
    print(f"  💡 次日收盘后: python main.py --review --review-type {SCAN_MODE}")

    return {"signals": signal_items, "file": signal_file}


def run_once():
    """单次运行模式 — 适合 Windows 任务计划程序"""
    today = datetime.date.today()

    # 1. 交易日检查
    if not is_trading_day(today):
        calendar = get_calendar()
        _, reason = calendar.is_trading_day(today)
        print(f"\n  ⏸️  {reason}，自动退出")
        return

    print(f"\n  ✅ {today} 是交易日")

    # 2. 等待到14:40
    if not wait_until_scan_time():
        return

    # 3. 精确等到14:40
    now = datetime.datetime.now()
    scan_time = now.replace(hour=SCAN_HOUR, minute=SCAN_MINUTE, second=0, microsecond=0)
    wait = (scan_time - now).total_seconds()
    if wait > 0:
        time.sleep(wait)

    # 4. 执行扫描
    run_scan()


def run_daemon():
    """守护进程模式 — 持续运行，每天自动触发"""
    print("=" * 60)
    print("  🔄 尾盘选股调度守护进程")
    print("=" * 60)
    print(f"  扫描时间: 每个交易日 {SCAN_HOUR:02d}:{SCAN_MINUTE:02d}")
    print(f"  扫描模式: {SCAN_MODE.upper()}")
    print(f"  按 Ctrl+C 停止")
    print("=" * 60)

    # 信号处理
    shutdown = False

    def handle_signal(sig, frame):
        nonlocal shutdown
        print("\n  🛑 收到停止信号，正在退出...")
        shutdown = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    while not shutdown:
        today = datetime.date.today()

        if is_trading_day(today):
            print(f"\n  📅 {today} 交易日，等待 {SCAN_HOUR:02d}:{SCAN_MINUTE:02d}...")

            # 等到扫描时间
            if wait_until_scan_time():
                # 精确等到14:40
                now = datetime.datetime.now()
                scan_time = now.replace(hour=SCAN_HOUR, minute=SCAN_MINUTE, second=0, microsecond=0)
                wait = (scan_time - now).total_seconds()
                if wait > 0:
                    time.sleep(wait)

                if not shutdown:
                    run_scan()

            # 今天已经扫描过了，等到明天
            if not shutdown:
                tomorrow = today + datetime.timedelta(days=1)
                next_check = datetime.datetime.combine(
                    tomorrow,
                    datetime.time(hour=8, minute=0),  # 早上8点重新检查
                )
                wait_until = (next_check - datetime.datetime.now()).total_seconds()
                if wait_until > 0:
                    print(f"\n  😴 今日扫描完成，明天 {tomorrow} 再见...")
                    # 分段sleep
                    while wait_until > 0 and not shutdown:
                        chunk = min(3600, wait_until)
                        time.sleep(chunk)
                        wait_until -= chunk
        else:
            # 非交易日，等到明天
            tomorrow = today + datetime.timedelta(days=1)
            next_check = datetime.datetime.combine(
                tomorrow, datetime.time(hour=8, minute=0),
            )
            wait_until = (next_check - datetime.datetime.now()).total_seconds()
            if wait_until > 0:
                calendar = get_calendar()
                _, reason = calendar.is_trading_day(today)
                print(f"\n  ⏸️  {reason}，等待下一个交易日...")
                while wait_until > 0 and not shutdown:
                    chunk = min(3600, wait_until)
                    time.sleep(chunk)
                    wait_until -= chunk

    print("  ✅ 守护进程已停止")


# ================================================================
# CLI
# ================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="交易日14:40自动尾盘选股调度器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python scheduler.py              单次模式（适合Windows任务计划程序）
  python scheduler.py --daemon      守护模式（持续运行）
  python scheduler.py --mode tail   使用尾盘9因子模式
  python scheduler.py --mode merged 使用融合10因子模式（默认）

Windows任务计划程序配置:
  1. Win+R → taskschd.msc
  2. 创建任务 → 触发器: 每天 14:30, 仅工作日
  3. 操作: 程序 python.exe, 参数 scheduler.py
  4. 起始于: C:\\Users\\...\\quant-trading-system
        """,
    )
    parser.add_argument("--daemon", action="store_true", help="守护进程模式（持续运行）")
    parser.add_argument("--mode", choices=["merged", "tail"], default="merged",
                        help="扫描模式 (default: merged)")
    parser.add_argument("--now", action="store_true", help="跳过等待，立即扫描（测试用）")
    args = parser.parse_args()

    SCAN_MODE = args.mode

    if args.now:
        # 立即执行（测试用，不检查交易日）
        print("  ⚠️ 测试模式：立即执行扫描\n")
        run_scan()
    elif args.daemon:
        run_daemon()
    else:
        run_once()
