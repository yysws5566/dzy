#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
箱体波动实时监控 — TickFlow Pro 版
===================================
紫金矿业(601899.SH) + 洛阳钼业(603993.SH)

数据源：TickFlow Pro（实时行情流 + K线 + 分时）
核心能力：
  1. QuoteStream 实时推送，毫秒级响应，无需轮询
  2. 买卖点触发：价格抵近箱体边界 + 涨跌幅阈值
  3. 箱体自适应：每日收盘后基于近N日K线+ATR自动重算
  4. 多周期共振检测：分时/日线/周线三级联动
  5. 日志持久化，Console 彩色面板

用法：
  python 箱体监控_实时提醒.py              # 持续监控（实时流）
  python 箱体监控_实时提醒.py --once       # 快照一次
  python 箱体监控_实时提醒.py --backtest   # 回测模式，重算箱体
"""

import io
import os
import sys
import json
import time
import signal
from datetime import datetime, timedelta
from collections import deque

from tickflow import TickFlow
from tickflow.resources.realtime import QuoteStream

# ── Windows UTF-8 ────────────────────────────────────
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── 配置 ────────────────────────────────────────────
SYMBOLS = {
    "601899.SH": {"label": "🏔️ 紫金矿业", "short": "紫金"},
    "603993.SH": {"label": "⛏️ 洛阳钼业", "short": "洛钼"},
}

# 初始箱体（起手参数，后续每日自动校准）
INIT_BOX = {
    "601899.SH": dict(B3=27.80, B2=29.00, B1=29.60,
                       S1=31.50, S2=32.00, S3=33.00,
                       stop=27.50, strong_resist=34.30,
                       box_low=29.00, box_high=31.50),
    "603993.SH": dict(B3=17.00, B2=18.00, B1=18.20,
                       S1=19.00, S2=19.50, S3=20.00,
                       stop=15.80, strong_resist=20.00,
                       box_low=18.00, box_high=19.50),
}

# 触发阈值
INTRADAY_DROP_PCT  = -3.0   # 日内跌超3%→买入关注
INTRADAY_RISE_PCT  =  3.0   # 日内涨超3%→卖出关注
WEEKLY_DROP_PCT    = -5.0   # 周跌超5%→中线买点
WEEKLY_RISE_PCT    =  5.0   # 周涨超5%→中线卖点

# 箱体重算
BOX_RECALC_DAYS    = 10     # 取近N个交易日
BOX_BREAK_DAYS     = 2      # 连续N天在箱体外触发重算

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "箱体监控_日志.txt")

# ── 颜色 ─────────────────────────────────────────────
class C:
    R = '\033[91m'; G = '\033[92m'; Y = '\033[93m'
    B = '\033[94m'; C = '\033[96m'; W = '\033[97m'
    X = '\033[0m'; BD = '\033[1m'

def cprint(text, color=C.W):
    print(f"{color}{text}{C.X}")

def log(msg, color=None):
    ts = datetime.now().strftime("%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    if color:
        cprint(line, color)
    else:
        print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

# ── TickFlow 客户端 ──────────────────────────────────
tf = TickFlow()

def ts_to_dt(ts_ms):
    """毫秒时间戳 → datetime"""
    return datetime.fromtimestamp(ts_ms / 1000)

def dt_to_ts(dt_obj):
    """datetime → 毫秒时间戳"""
    return int(dt_obj.timestamp() * 1000)

# ══════════════════════════════════════════════════════
# 1. 数据获取（全部走 TickFlow）
# ══════════════════════════════════════════════════════

def get_quotes():
    """实时快照（用于 --once 模式）"""
    try:
        df = tf.quotes.get(symbols=list(SYMBOLS.keys()), as_dataframe=True)
        return df
    except Exception as e:
        log(f"❌ 行情获取失败: {e}", C.R)
        return None

def get_daily_klines(symbol, days=BOX_RECALC_DAYS):
    """日K线"""
    try:
        df = tf.klines.get(symbol, period="1d", count=days, as_dataframe=True)
        return df
    except Exception as e:
        log(f"  ⚠️ K线获取失败({symbol}): {e}", C.Y)
        return None

def get_intraday(symbol, period="1m", count=240):
    """当日分时"""
    try:
        df = tf.klines.intraday(symbol, period=period, count=count, as_dataframe=True)
        return df
    except Exception:
        return None

def get_weekly_klines(symbol, weeks=4):
    """周K线"""
    try:
        df = tf.klines.get(symbol, period="1w", count=weeks, as_dataframe=True)
        return df
    except Exception:
        return None

def get_financial_metrics(symbols):
    """基本面指标（辅助判断）"""
    try:
        df = tf.financials.metrics(symbols=list(symbols), latest=True, as_dataframe=True)
        return df
    except Exception:
        return None

# ══════════════════════════════════════════════════════
# 2. 箱体重算引擎
# ══════════════════════════════════════════════════════

def recalc_box(symbol):
    """基于日K + ATR 自动计算箱体"""
    df = get_daily_klines(symbol, BOX_RECALC_DAYS)
    if df is None or len(df) < 5:
        return None

    highs = df["high"].values
    lows  = df["low"].values
    closes = df["close"].values

    # ATR(5)
    trs = []
    for i in range(1, len(df)):
        h, l, pc = highs[i], lows[i], closes[i-1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    atr = sum(trs) / len(trs)

    # 近N日高低范围
    range_high = float(max(highs))
    range_low  = float(min(lows))
    mid = (range_high + range_low) / 2
    half = (range_high - range_low) / 2

    # 箱体上下沿：取70%集中区间
    box_high = round(mid + half * 0.70, 2)
    box_low  = round(mid - half * 0.70, 2)

    # 买卖点基于箱体 + ATR偏移
    new_box = {
        "B3": round(box_low - atr, 2),
        "B2": round(box_low, 2),
        "B1": round(box_low + atr * 0.3, 2),
        "S1": round(box_high - atr * 0.3, 2),
        "S2": round(box_high, 2),
        "S3": round(box_high + atr, 2),
        "stop": round(box_low - 1.5 * atr, 2),
        "strong_resist": round(box_high + 2 * atr, 2),
        "box_low": box_low,
        "box_high": box_high,
        "_atr": round(atr, 3),
        "_recalc_ts": datetime.now().strftime("%m-%d %H:%M"),
    }
    return new_box

# ══════════════════════════════════════════════════════
# 3. 信号检测
# ══════════════════════════════════════════════════════

def detect_signals(symbol, price, pct, open_p, high, low, box, prev_close):
    """检测所有买卖信号，返回 [(名称, 类型, 消息, 颜色)]"""
    sigs = []

    # ── ATR 动态止损 ──
    atr_val = box.get("_atr", 0.01)
    atr_stop = max(price - atr_val * 1.5, price * 0.97)
    atr_stop_pct = (price - atr_stop) / price * 100
    stop_type = "硬止损(-3%)" if atr_stop == price * 0.97 else f"ATR动态(-{atr_stop_pct:.1f}%)"

    # ── 买入侧 ──
    if price <= box["B1"]:
        sigs.append((f"B1试探买 @{box['B1']}", "BUY",
                     f"现价≤B1，轻仓20%试探 | 🛑 止损:{atr_stop:.2f}({stop_type},ATR:{atr_val:.3f})", C.G))
    if price <= box["B2"]:
        sigs.append((f"B2标准买 @{box['B2']}", "BUY",
                     f"现价≤B2，标准仓位30% | 🛑 止损:{atr_stop:.2f}({stop_type},ATR:{atr_val:.3f})", C.G))
    if price <= box["B3"]:
        sigs.append((f"B3重仓买 @{box['B3']}", "BUY+",
                     f"恐慌低点，重仓50% | 🛑 止损:{atr_stop:.2f}({stop_type},ATR:{atr_val:.3f})", C.C))

    # 日内急跌
    if pct <= INTRADAY_DROP_PCT:
        sigs.append(("日内急跌", "ALERT",
                     f"跌幅{pct:.1f}%，关注箱底止跌信号 | 🛑 止损:{atr_stop:.2f}", C.B))

    # ── 卖出侧 ──
    if price >= box["S1"]:
        sigs.append((f"S1减仓卖 @{box['S1']}", "SELL",
                     f"现价≥S1，减仓1/3锁利", C.Y))
    if price >= box["S2"]:
        sigs.append((f"S2标准卖 @{box['S2']}", "SELL",
                     f"现价≥S2，再减1/3", C.R))
    if price >= box["S3"]:
        sigs.append((f"S3清仓卖 @{box['S3']}", "SELL+",
                     f"筹码成本区，全部清仓", C.R))

    # 日内急涨
    if pct >= INTRADAY_RISE_PCT:
        sigs.append(("日内急涨", "ALERT",
                     f"涨幅{pct:.1f}%，关注箱顶滞涨", C.B))

    # ── 止损 ──
    if price <= box["stop"]:
        sigs.append((f"⛔ 止损触发 @{box['stop']}", "STOP",
                     f"跌破止损线，无条件全清！", C.R))

    # ── 突破 ──
    if price > box["strong_resist"]:
        sigs.append(("🚀 突破强阻力", "BREAKOUT",
                     f"趋势可能反转，重新评估", C.C))

    return sigs

# ══════════════════════════════════════════════════════
# 4. 显示面板
# ══════════════════════════════════════════════════════

def display_panel(quotes_df, boxes):
    """打印当前监控面板"""
    print()
    cprint("═" * 72, C.W)
    header = f"  📦 TickFlow Pro 箱体监控  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    cprint(header, C.BD + C.C)
    cprint("═" * 72, C.W)

    for _, row in quotes_df.iterrows():
        symbol = row["symbol"]
        box = boxes[symbol]
        info = SYMBOLS[symbol]

        price  = float(row["last_price"])
        open_p = float(row["open"])
        high   = float(row["high"])
        low    = float(row["low"])
        prev   = float(row["prev_close"])
        pct    = float(row["ext.change_pct"])
        amp    = float(row["ext.amplitude"])
        vol    = float(row["volume"])
        amt    = float(row["amount"])

        pct_color = C.R if pct < 0 else C.G
        print()
        cprint(f"  {info['label']}  {symbol}", C.BD + C.W)
        print(f"     现价: {price:.2f}  |  日内: {pct_color}{pct:+.2f}%{C.X}  "
              f"|  振幅: {amp*100:.2f}%  |  最高:{high:.2f}  最低:{low:.2f}")
        print(f"     昨收: {prev:.2f}  |  成交: {amt/1e8:.2f}亿  |  量: {vol/10000:.0f}万手")

        # 箱体可视化定位条
        total = box["strong_resist"] - box["stop"]
        if total > 0:
            bw = 62
            pos = int((price - box["stop"]) / total * bw)
            pos = max(0, min(bw - 1, pos))
            b1_p = int((box["B1"] - box["stop"]) / total * bw)
            s1_p = int((box["S1"] - box["stop"]) / total * bw)

            bar = ["─"] * bw
            bar[0] = "├"; bar[-1] = "┤"
            if 0 <= b1_p < bw: bar[b1_p] = "┊"
            if 0 <= s1_p < bw: bar[s1_p] = "┊"
            if 0 <= pos < bw: bar[pos] = "●"
            print(f"     止损{box['stop']:.1f} {''.join(bar)} {box['strong_resist']:.1f}强阻")
            print(f"     {'':>6}B3={box['B3']:.1f}  B2={box['B2']:.1f}  B1={box['B1']:.1f}"
                  f"     S1={box['S1']:.1f}  S2={box['S2']:.1f}  S3={box['S3']:.1f}")

        # 信号检测
        sigs = detect_signals(symbol, price, pct, open_p, high, low, box, prev)
        if sigs:
            for name, stype, msg, color in sigs:
                icon = {"BUY":"🟢","BUY+":"💎","SELL":"🟡","SELL+":"🔴",
                        "STOP":"⛔","BREAKOUT":"🚀","ALERT":"🔔"}.get(stype, "📌")
                cprint(f"     {icon} [{stype}] {name}: {msg}", color)
        else:
            print(f"     ✓ 无触发信号，箱体内正常运行")

    print()
    cprint("─" * 72, C.W)
    print(f"  数据源: TickFlow Pro  |  日志: {LOG_FILE}")
    print(f"  按 Ctrl+C 停止")

# ══════════════════════════════════════════════════════
# 5. 事件处理（QuoteStream 实时推送回调）
# ══════════════════════════════════════════════════════

class BoxMonitor:
    """箱体监控核心控制器"""

    def __init__(self):
        self.boxes = {s: dict(b) for s, b in INIT_BOX.items()}
        self.break_counter = {s: 0 for s in SYMBOLS}     # 连续在箱体外计数
        self.break_direction = {s: None for s in SYMBOLS} # "above" / "below"
        self.last_recalc_date = {s: None for s in SYMBOLS}
        self.latest_quotes = {}   # symbol → latest quote dict
        self.signal_cooldown = {} # signal_key → last_alert_ts，防重复报警

    def on_quote(self, quotes):
        """QuoteStream 回调：每笔实时行情推送"""
        for q in quotes:
            symbol = q.get("symbol", "")
            if symbol not in SYMBOLS:
                continue

            price  = float(q.get("last_price", 0) or 0)
            open_p = float(q.get("open", 0) or 0)
            high   = float(q.get("high", 0) or 0)
            low    = float(q.get("low", 0) or 0)
            prev   = float(q.get("prev_close", 0) or 0)
            pct    = float(q.get("ext.change_pct", 0) or 0)
            box    = self.boxes[symbol]

            if price <= 0:
                continue

            self.latest_quotes[symbol] = q

            # 检测信号
            sigs = detect_signals(symbol, price, pct, open_p, high, low, box, prev)
            for name, stype, msg, color in sigs:
                # 防重复：同一信号5分钟内不重复报警
                ck = f"{symbol}:{name}"
                now = time.time()
                if ck in self.signal_cooldown and now - self.signal_cooldown[ck] < 300:
                    continue
                self.signal_cooldown[ck] = now

                icon = {"BUY":"🟢","BUY+":"💎","SELL":"🟡","SELL+":"🔴",
                        "STOP":"⛔","BREAKOUT":"🚀","ALERT":"🔔"}.get(stype, "📌")
                log(f"  {icon} [{stype}] {SYMBOLS[symbol]['label']} {name}: {msg}", color)

            # 检测箱体突破（用于日终自动重算）
            if price > box["box_high"]:
                if self.break_direction[symbol] == "above":
                    self.break_counter[symbol] += 1
                else:
                    self.break_direction[symbol] = "above"
                    self.break_counter[symbol] = 1
            elif price < box["box_low"] and price > box["stop"]:
                if self.break_direction[symbol] == "below":
                    self.break_counter[symbol] += 1
                else:
                    self.break_direction[symbol] = "below"
                    self.break_counter[symbol] = 1
            else:
                self.break_counter[symbol] = 0
                self.break_direction[symbol] = None

            # 连续突破触发重算
            if self.break_counter[symbol] >= BOX_BREAK_DAYS:
                today = datetime.now().strftime("%Y-%m-%d")
                if self.last_recalc_date.get(symbol) != today:
                    log(f"  🔄 {SYMBOLS[symbol]['label']} 箱体突破确认，自动重算...", C.C)
                    new_box = recalc_box(symbol)
                    if new_box:
                        old_low, old_high = box["box_low"], box["box_high"]
                        self.boxes[symbol] = new_box
                        self.break_counter[symbol] = 0
                        self.break_direction[symbol] = None
                        self.last_recalc_date[symbol] = today
                        log(f"    箱体: {old_low}~{old_high} → {new_box['box_low']}~{new_box['box_high']}"
                            f" (ATR={new_box['_atr']})", C.C)

    def daily_recalc(self):
        """每日收盘后自动重算所有标的的箱体"""
        for symbol in SYMBOLS:
            new_box = recalc_box(symbol)
            if new_box:
                old = self.boxes[symbol]
                self.boxes[symbol] = new_box
                log(f"  📐 {SYMBOLS[symbol]['label']} 日终箱体校准: "
                    f"{old['box_low']}~{old['box_high']} → {new_box['box_low']}~{new_box['box_high']}",
                    C.B)

# ══════════════════════════════════════════════════════
# 6. 运行模式
# ══════════════════════════════════════════════════════

def run_stream():
    """实时流模式（主力模式）：QuoteStream 推送，毫秒级响应"""
    monitor = BoxMonitor()
    stream = QuoteStream(tf)

    log("🚀 TickFlow Pro 箱体监控启动（实时流模式）", C.C)
    symbols_str = ", ".join(f"{SYMBOLS[s]['label']}({s})" for s in SYMBOLS)
    log(f"   标的: {symbols_str}", C.W)
    for s, b in monitor.boxes.items():
        log(f"   {SYMBOLS[s]['short']} 初始箱体: {b['box_low']}~{b['box_high']} 止损:{b['stop']} ATR:{b.get('_atr','N/A')}", C.W)
    print()

    # 注册回调
    stream.on_quotes(monitor.on_quote)
    stream.on_error(lambda err: log(f"⚠️ Stream error: {err}", C.Y))

    # 订阅标的
    stream.subscribe(list(SYMBOLS.keys()))
    log(f"📡 已订阅 {len(SYMBOLS)} 只标的，等待实时推送...", C.B)

    # 日终重算定时器（简化：每30分钟检查一次是否过了15:00）
    last_daily_check = None

    def check_eod():
        nonlocal last_daily_check
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        # 15:00 后且今天还没算过
        if now.hour >= 15 and last_daily_check != today_str:
            last_daily_check = today_str
            log("📐 收盘后自动校准箱体...", C.C)
            monitor.daily_recalc()

    # 连接流（block=True 阻塞直到断开）
    try:
        stream.connect(block=False)
        log("✅ 流已连接，接收实时推送中...", C.G)
        # 保持主线程运行，定期做日终检查
        while True:
            time.sleep(30)
            check_eod()
    except KeyboardInterrupt:
        log("\n👋 监控已停止", C.C)
    except Exception as e:
        log(f"❌ 流连接异常: {e}", C.R)
    finally:
        stream.close()


def run_once():
    """快照模式：拉一次实时行情，展示面板"""
    df = get_quotes()
    if df is None or len(df) == 0:
        log("⚠️ 未获取到行情数据", C.Y)
        return

    # 获取K线重算最新箱体
    boxes = {}
    for symbol in SYMBOLS:
        new_box = recalc_box(symbol)
        if new_box:
            boxes[symbol] = new_box
            log(f"  📐 {SYMBOLS[symbol]['label']} 动态箱体: {new_box['box_low']}~{new_box['box_high']}"
                f" (ATR={new_box['_atr']}, 止损={new_box['stop']})", C.B)
        else:
            boxes[symbol] = dict(INIT_BOX[symbol])

    display_panel(df, boxes)


def run_backtest():
    """回测模式：拉近期K线，重算箱体并展示"""
    print()
    cprint("📊 TickFlow Pro · 箱体回测校准", C.BD + C.C)
    print()

    for symbol, info in SYMBOLS.items():
        df = get_daily_klines(symbol, 30)
        if df is None or len(df) < 5:
            print(f"  {info['label']}: 数据不足")
            continue

        print(f"  {info['label']} ({symbol})  近{len(df)}日K线:")
        print(f"    区间: {df['low'].min():.2f} ~ {df['high'].max():.2f}")
        print(f"    最新: {df['close'].iloc[-1]:.2f}  ({df.index[-1]})")
        closes_str = " → ".join(f"{c:.1f}" for c in df["close"].tail(5))
        print(f"    近5日收盘: {closes_str}")

        new_box = recalc_box(symbol)
        if new_box:
            print(f"    📐 动态箱体: {new_box['box_low']} ~ {new_box['box_high']}  (ATR={new_box['_atr']})")
            print(f"       止损: {new_box['stop']}")
            print(f"       买: B1={new_box['B1']}  B2={new_box['B2']}  B3={new_box['B3']}")
            print(f"       卖: S1={new_box['S1']}  S2={new_box['S2']}  S3={new_box['S3']}")

        # 周线
        wdf = get_weekly_klines(symbol, 4)
        if wdf is not None and len(wdf) >= 2:
            w_change = (wdf['close'].iloc[-1] / wdf['close'].iloc[-2] - 1) * 100
            w_color = "🔴" if w_change < -WEEKLY_RISE_PCT else ("🟢" if w_change > WEEKLY_RISE_PCT else "→")
            print(f"    📅 本周涨跌: {w_change:+.2f}% {w_color}")
        print()

    # 基本面
    print("  📋 基本面速览:")
    try:
        fin = get_financial_metrics(SYMBOLS.keys())
        if fin is not None and len(fin) > 0:
            for _, row in fin.iterrows():
                sym = row.get("symbol", "")
                pe = row.get("pe_ttm", "N/A")
                pb = row.get("pb", "N/A")
                roe = row.get("roe", "N/A")
                name = SYMBOLS.get(sym, {}).get("label", sym)
                print(f"    {name}: PE(TTM)={pe}  PB={pb}  ROE={roe}")
    except Exception as e:
        print(f"    基本面数据暂不可用: {e}")

    print()


# ══════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    if "--once" in sys.argv:
        run_once()
    elif "--backtest" in sys.argv:
        run_backtest()
    else:
        # 默认：实时流模式
        run_stream()
