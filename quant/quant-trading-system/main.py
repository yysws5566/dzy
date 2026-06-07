#!/usr/bin/env python3
"""
A股 T+1 短线多因子量化交易系统
=================================
主入口 - 串联全流程：
  1. 判断交易日 → 非交易日自动跳过
  2. 获取全市场股票 → 流动性初筛
  3. 计算12个T+1短线因子
  4. 多因子加权打分 → 输出买入信号及仓位建议
  5. 自动复盘分析 → 统计胜率和因子表现

用法:
  python main.py              # 当日扫描模式
  python main.py --backtest   # 回测模式
  python main.py --date 2026-06-05  # 指定日期扫描
"""

import encoding_fix  # noqa: F401 - Windows UTF-8编码修复（必须在其他导入之前）

import argparse
import datetime
import json
import sys
import os

# 确保项目路径在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DEFAULT_LIQUIDITY, DEFAULT_WEIGHTS, DEFAULT_SCORING, DEFAULT_BACKTEST
from trading_calendar import get_calendar
from data_fetcher import DataFetcher
from liquidity_filter import LiquidityScreener, MarketSnapshot
from scorer import MultiFactorScorer, CompositeScore
from backtest import BacktestEngine, BacktestResult
from reporter import Reporter
import simulation  # 模拟数据模块（API不可用时的降级方案）

# TickFlow 原生SDK + 尾盘2:40模式
try:
    from tickflow_client import TickFlowClient, get_client
    from tail_scanner import TailScanner
    HAS_TICKFLOW = True
except ImportError:
    HAS_TICKFLOW = False

# 导入12个因子
from factors.factor1_tail_volume_divergence import TailVolumeDivergenceFactor
from factors.factor2_seal_quality import SealQualityFactor
from factors.factor3_gap_gambit import GapGambitFactor
from factors.factor4_northbound_divergence import NorthboundDivergenceFactor
from factors.factor5_auction import AuctionFactor
from factors.factor6_board_reversal import BoardReversalFactor
from factors.factor7_dragon_tiger import DragonTigerFactor
from factors.factor8_sector_lag import SectorLagFactor
from factors.factor9_integer_psych import IntegerPsychFactor
from factors.factor10_margin_sentiment import MarginSentimentFactor
from factors.factor11_block_trade import BlockTradeFactor
from factors.factor12_global_linkage import GlobalLinkageFactor


def get_all_factors(weights: dict = None) -> list:
    """初始化全部12个因子"""
    w = weights or DEFAULT_WEIGHTS.to_dict()
    return [
        TailVolumeDivergenceFactor(weight=w.get("tail_volume_divergence", 0.12)),
        SealQualityFactor(weight=w.get("seal_quality", 0.08)),
        GapGambitFactor(weight=w.get("gap_gambit", 0.10)),
        NorthboundDivergenceFactor(weight=w.get("northbound_divergence", 0.12)),
        AuctionFactor(weight=w.get("auction", 0.10)),
        BoardReversalFactor(weight=w.get("board_reversal", 0.08)),
        DragonTigerFactor(weight=w.get("dragon_tiger", 0.08)),
        SectorLagFactor(weight=w.get("sector_lag", 0.07)),
        IntegerPsychFactor(weight=w.get("integer_psych", 0.05)),
        MarginSentimentFactor(weight=w.get("margin_sentiment", 0.07)),
        BlockTradeFactor(weight=w.get("block_trade", 0.08)),
        GlobalLinkageFactor(weight=w.get("global_linkage", 0.05)),
    ]


def check_trading_day(date: datetime.date = None) -> bool:
    """
    检查交易日
    返回 True=继续运行, False=已跳过
    """
    calendar = get_calendar()
    should_skip, reason = calendar.should_skip_today() if date is None else (
        not calendar.is_trading_day(date)[0], calendar.is_trading_day(date)[1]
    )

    if should_skip:
        print(f"\n  ⏸️  {reason}")
        print(f"  💡 系统自动跳过，如需回测请运行: python main.py --backtest\n")
        return False
    else:
        print(f"\n  ✅ {reason}，系统正常运行\n")
        return True


def build_snapshots(symbols: list, data_fetcher: DataFetcher) -> list:
    """
    构建市场快照列表
    - 获取日线 + 分钟线数据
    - 封装为 MarketSnapshot 对象
    """
    print(f"  📡 获取行情数据 ({len(symbols)} 只标的)...")

    # 批量获取日线
    daily_data = data_fetcher.get_daily_bars(symbols, days=60)

    snapshots = []
    for stock in symbols:
        symbol = stock.get("symbol", "")
        name = stock.get("name", "未知")
        sector = stock.get("sector", "")

        snapshot = MarketSnapshot(symbol=symbol, name=name, sector=sector)
        snapshot.daily_bars = daily_data.get(symbol, [])
        # 分钟线按需获取（仅对通过流动性筛选的标的）
        snapshots.append(snapshot)

    return snapshots


def enrich_snapshots(snapshots: list, data_fetcher: DataFetcher, fetch_advanced: bool = True):
    """
    丰富快照数据（高级因子需要的数据）
    - 分钟线（尾盘分析）
    - 北向资金
    - 龙虎榜
    - 融资融券
    - 大宗交易
    - 集合竞价
    等
    """
    if not fetch_advanced:
        return

    print(f"  📡 获取高级因子数据...")
    symbols = [s.symbol for s in snapshots]

    # 分钟线
    minute_data = data_fetcher.get_minute_bars(symbols[:20], minutes=156)  # 取前20只（最近2天）
    for s in snapshots:
        s.minute_bars = minute_data.get(s.symbol, [])

    # 尝试从TickFlow获取高级数据（降级模式：失败不崩溃）
    for s in snapshots[:30]:  # 限制数量避免超时
        try:
            nb = data_fetcher.tickflow.get_northbound_flow(s.symbol, days=10)
            if nb:
                s.__dict__["_northbound_data"] = nb
        except Exception:
            pass

        try:
            dt = data_fetcher.tickflow.get_dragon_tiger(s.symbol, days=5)
            if dt:
                s.__dict__["_dragon_tiger_data"] = dt
        except Exception:
            pass

        try:
            mg = data_fetcher.tickflow.get_margin_data(s.symbol, days=10)
            if mg:
                s.__dict__["_margin_data"] = mg
        except Exception:
            pass

        try:
            bt = data_fetcher.tickflow.get_block_trades(s.symbol, days=10)
            if bt:
                s.__dict__["_block_trade_data"] = bt
        except Exception:
            pass

    # 外盘数据（全局，只获取一次）
    try:
        gd = data_fetcher.tickflow.get_global_index("SPX")
        if gd:
            for s in snapshots:
                s.__dict__["_global_data"] = gd
    except Exception:
        pass


def run_daily_scan(date: datetime.date = None, fetch_advanced: bool = False):
    """
    每日扫描主流程

    1. 判断交易日
    2. 获取候选池
    3. 流动性筛选
    4. 因子计算
    5. 加权打分
    6. 输出报告
    """
    if date is None:
        date = datetime.date.today()

    # 1. 交易日检查
    if not check_trading_day(date):
        return None

    print("=" * 70)
    print(f"  🚀 A股 T+1 多因子量化扫描 — {date}")
    print("=" * 70)

    # 2. 获取全市场股票
    print(f"\n  📋 步骤1/5: 获取候选池...")
    fetcher = DataFetcher()
    all_stocks = fetcher.get_a_share_universe(sample_mode=True)  # 样本模式
    print(f"     全市场样本: {len(all_stocks)} 只")

    # 3. 流动性筛选（优先用真实API，失败则用模拟数据）
    print(f"\n  🔍 步骤2/5: 获取行情 + 流动性筛选...")
    use_simulation = False

    try:
        daily_data = fetcher.get_daily_bars([s["symbol"] for s in all_stocks], days=60)
        # 检查是否所有数据都为空
        valid_count = sum(1 for v in daily_data.values() if v)
        if valid_count < len(all_stocks) * 0.3:
            raise RuntimeError(f"API数据覆盖率不足 ({valid_count}/{len(all_stocks)})")
    except Exception as e:
        print(f"     ⚠️ API数据获取失败: {e}")
        print(f"     🔄 自动切换为模拟数据模式...")
        daily_data = simulation.simulate_universe_data(all_stocks, days=60)
        use_simulation = True

    # 构建快照
    snapshots = []
    for stock in all_stocks:
        symbol = stock.get("symbol", "")
        name = stock.get("name", "未知")
        sector = stock.get("sector", "")
        snap = MarketSnapshot(symbol=symbol, name=name, sector=sector)
        snap.daily_bars = daily_data.get(symbol, [])
        snapshots.append(snap)

    # 执行流动性筛选
    screener = LiquidityScreener(DEFAULT_LIQUIDITY)
    passed, rejected = screener.screen(all_stocks, daily_data)
    print(f"     通过: {len(passed)} 只, 剔除: {len(rejected)} 只")

    if use_simulation:
        print(f"     📝 当前为模拟数据模式，结果仅供验证系统流程")

    if not passed:
        print("  ⚠️ 无标的通过流动性筛选，退出扫描")
        return None

    valid_symbols = {p["symbol"] for p in passed}
    valid_snapshots = [s for s in snapshots if s.symbol in valid_symbols]

    # 4. 丰富数据（分钟线等）
    print(f"\n  📡 步骤3/5: 获取分钟线数据...")
    if use_simulation:
        minute_data = simulation.simulate_minute_data(all_stocks, daily_data)
        for s in valid_snapshots:
            s.minute_bars = minute_data.get(s.symbol, [])
    else:
        minute_data = fetcher.get_minute_bars(list(valid_symbols)[:20], minutes=156)
        for s in valid_snapshots:
            s.minute_bars = minute_data.get(s.symbol, [])

    # 获取高级数据（仅在非模拟模式下尝试）
    if fetch_advanced and not use_simulation:
        enrich_snapshots(valid_snapshots, fetcher, fetch_advanced=True)

    # 5. 因子计算 + 打分
    print(f"\n  🧮 步骤4/5: 计算12个因子并打分...")
    factors = get_all_factors()
    scorer = MultiFactorScorer(DEFAULT_WEIGHTS, DEFAULT_SCORING)
    scores = []

    for i, snap in enumerate(valid_snapshots):
        if i % 5 == 0:
            print(f"     进度: {i+1}/{len(valid_snapshots)}")

        # 计算12个因子
        factor_results = []
        for factor in factors:
            try:
                result = factor.calculate(snap)
                factor_results.append(result)
            except Exception as e:
                print(f"     [警告] {snap.symbol} 因子{factor.name}计算异常: {e}")
                continue

        # 加权打分
        composite = scorer.score(snap.symbol, snap.name, snap.sector, factor_results)
        scores.append(composite)

    # 排序
    scores = scorer.rank_candidates(scores)

    # 6. 输出报告
    print(f"\n  📊 步骤5/5: 生成报告...")
    reporter = Reporter()

    # 控制台报告
    reporter.print_daily_summary(scores, date.isoformat())

    # 保存报告文件
    report_path = reporter.save_daily_report(scores, date.isoformat())
    print(f"\n  📄 详细报告已保存: {report_path}")

    return scores


def run_backtest():
    """回测模式"""
    print("\n" + "=" * 70)
    print("  📊 回测模式 — 历史数据复盘")
    print("=" * 70)

    # 回测设置
    backtest_cfg = DEFAULT_BACKTEST
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=backtest_cfg.lookback_days)

    calendar = get_calendar()
    trading_days = calendar.get_trading_days_in_range(start_date, end_date)
    print(f"\n  📅 回测区间: {start_date} ~ {end_date}")
    print(f"     交易日数: {len(trading_days)} 天")

    # 获取数据
    fetcher = DataFetcher()
    all_stocks = fetcher.get_a_share_universe(sample_mode=True)
    print(f"     股票池:   {len(all_stocks)} 只")

    # 获取历史价格
    print(f"\n  📡 获取历史K线...")
    symbols = [s["symbol"] for s in all_stocks]
    use_simulation = False
    try:
        price_data = fetcher.get_daily_bars(symbols, days=backtest_cfg.lookback_days + 60)
        valid_count = sum(1 for v in price_data.values() if v)
        if valid_count < len(all_stocks) * 0.3:
            raise RuntimeError(f"API数据覆盖率不足")
    except Exception as e:
        print(f"     ⚠️ API数据获取失败: {e}")
        print(f"     🔄 自动切换为模拟数据模式...")
        price_data = simulation.simulate_universe_data(all_stocks, days=backtest_cfg.lookback_days + 60)
        use_simulation = True

    print(f"     获取完成: {len(price_data)} 只")
    if use_simulation:
        print(f"     📝 当前为模拟数据模式，回测结果仅供验证系统流程")

    # 模拟每日信号（基于历史数据）
    print(f"\n  🧮 模拟历史信号...")
    factors = get_all_factors()
    scorer = MultiFactorScorer(DEFAULT_WEIGHTS, DEFAULT_SCORING)
    screener = LiquidityScreener(DEFAULT_LIQUIDITY)

    daily_signals = {}

    for i, day in enumerate(trading_days[20:]):  # 前20天用于计算均线等指标
        if i % 5 == 0:
            print(f"     进度: {i+1}/{len(trading_days)-20} ({day})")

        day_str = day.isoformat()
        day_signals = []

        for stock in all_stocks:
            symbol = stock["symbol"]

            # 截取该日期之前的日线数据
            symbol_bars = price_data.get(symbol, [])
            # 筛选该日期之前的数据
            historical_bars = []
            for bar in symbol_bars:
                bar_date = bar.get("date", bar.get("datetime", ""))
                if isinstance(bar_date, datetime.datetime):
                    bar_date = bar_date.strftime("%Y-%m-%d")
                if bar_date <= day_str:
                    historical_bars.append(bar)

            if len(historical_bars) < 20:
                continue

            # 构建快照
            snap = MarketSnapshot(symbol=stock["symbol"], name=stock["name"], sector=stock.get("sector", ""))
            snap.daily_bars = historical_bars

            # 检查流动性
            rejected_reason = screener._check_liquidity(stock, historical_bars)
            if rejected_reason:
                continue

            # 计算因子
            factor_results = []
            for factor in factors:
                try:
                    result = factor.calculate(snap)
                    factor_results.append(result)
                except Exception:
                    continue

            # 打分
            composite = scorer.score(snap.symbol, snap.name, snap.sector, factor_results)

            if composite.signal in ("STRONG_BUY", "BUY"):
                day_signals.append({
                    "symbol": composite.symbol,
                    "name": composite.name,
                    "signal": composite.signal,
                    "total_score": composite.total_score,
                    "suggested_position_pct": composite.suggested_position_pct,
                    "factor_scores": {fr.factor_name: fr.normalized_score for fr in factor_results},
                })

        daily_signals[day_str] = day_signals

    # 运行回测
    print(f"\n  ⚙️ 执行回测...")
    engine = BacktestEngine(backtest_cfg, DEFAULT_SCORING)
    result = engine.run(daily_signals, price_data)

    # 输出报告
    reporter = Reporter()
    reporter.print_backtest_report(result)
    bt_path = reporter.save_backtest_report(result)
    trade_log_path = reporter.save_trade_log(result.trades)
    print(f"\n  📄 回测报告: {bt_path}")
    print(f"  📄 交易日志: {trade_log_path}")

    return result


def save_signals(signals, method: str, date: datetime.date = None):
    """保存信号为JSON，供次日复盘使用"""
    if date is None:
        date = datetime.date.today()
    filepath = os.path.join(os.path.dirname(__file__), "reports",
                             f"signals_{date.isoformat()}.json")
    data = {
        "date": date.isoformat(),
        "method": method,
        "signals": [],
    }
    for s in signals:
        if hasattr(s, '__dict__'):
            item = {
                "symbol": s.symbol, "name": s.name,
                "buy_price": s.current_price,
                "total_score": s.total_score,
                "signal": s.signal,
                "position_pct": s.position_pct,
                "change_pct": s.change_pct,
                "factor_scores": {},
            }
            # 收集因子得分
            for fn in ["tail_volume_score", "intraday_trend_score", "volume_accum_score",
                        "auction_score", "gap_score", "reversal_score", "ma_position_score",
                        "integer_psych_score", "sector_rel_score",
                        "tail_volume", "intraday_trend", "volume_accum",
                        "auction", "gap", "reversal", "ma_position",
                        "seal_quality", "global_linkage", "overnight_risk"]:
                val = getattr(s, fn, None)
                if val is not None:
                    # 去掉 _score 后缀统一命名
                    key = fn.replace("_score", "")
                    item["factor_scores"][key] = round(val, 4)
            data["signals"].append(item)

    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return filepath


def run_merged_scan(date: datetime.date = None):
    """
    融合版选股模式（两套算法优势基因合并 + 复盘反馈权重）

    10个融合因子 = 8通用 + 2增强
    """
    if not HAS_TICKFLOW:
        print("  ❌ TickFlow SDK 未安装")
        return None

    # 动态导入
    try:
        from merged_scanner import MergedScanner
    except ImportError:
        print("  ❌ merged_scanner.py 不存在")
        return None

    if date is None:
        date = datetime.date.today()

    if not check_trading_day(date):
        return None

    print("=" * 70)
    print(f"  🧬 融合算法 T+1 选股扫描 — {date}")
    print("=" * 70)

    # 连接
    print(f"\n  📡 TickFlow Pro...")
    try:
        client = get_client()
        test = client.get_realtime_quotes(["600519.SH"])
        if not test:
            raise RuntimeError("行情获取失败")
        print(f"     ✅ OK")
    except Exception as e:
        print(f"     ❌ {e}")
        return None

    # 候选池
    print(f"\n  📋 候选池...")
    try:
        universes = client.list_universes(region="CN", category="equity")
        sw1 = [u for u in universes if "SW1" in u["id"]]
        all_symbols = set()
        for u in sw1[:10]:
            try:
                syms = client.get_universe_symbols(u["id"])
                all_symbols.update(syms[:12])
            except Exception:
                continue
        if not all_symbols:
            all_symbols = [
                "600519.SH","600036.SH","600030.SH","601012.SH",
                "000858.SZ","000001.SZ","002594.SZ","300750.SZ",
                "300059.SZ","688981.SH","601318.SH","600887.SH",
                "600900.SH","000333.SZ","300274.SZ","002415.SZ",
                "600373.SH","603096.SH","688169.SH",
            ]
        symbols_list = list(all_symbols)
        print(f"     {len(symbols_list)} 只")
    except Exception as e:
        print(f"     ⚠️ {e}")
        symbols_list = ["600519.SH","000001.SZ","300750.SZ","601012.SH",
                         "000858.SZ","002594.SZ","300059.SZ","688981.SH"]
        print(f"     预设池 {len(symbols_list)} 只")

    # 加载权重（优先复盘优化权重，自动适配因子数量）
    weights = None
    buy_thresh = 0.55
    try:
        merged_w_path = os.path.join(os.path.dirname(__file__), "reports", "merged_weights.json")
        overnight_path = os.path.join(os.path.dirname(__file__), "reports", "overnight_optimal.json")

        if os.path.exists(merged_w_path):
            with open(merged_w_path) as f:
                wdata = json.load(f)
            weights = wdata.get("weights")
            recs = wdata.get("total_records", 0)
            print(f"     ✅ 加载复盘优化权重 ({recs}笔真实交易反馈)")
        elif os.path.exists(overnight_path):
            with open(overnight_path) as f:
                wdata = json.load(f)
            old_weights = wdata.get("weights", {})
            # 映射：旧版9因子 → 融合版10因子
            weights = {
                "tail_volume": old_weights.get("tail_volume", 0.15),
                "intraday_trend": old_weights.get("intraday_trend", 0.13),
                "volume_accum": old_weights.get("volume_accum", 0.10),
                "auction": old_weights.get("auction", 0.12),
                "gap": old_weights.get("gap", 0.10),
                "reversal": old_weights.get("reversal", 0.08),
                "ma_position": old_weights.get("ma_position", 0.07),
                "seal_quality": old_weights.get("seal_quality", 0.10),
                "global_linkage": old_weights.get("global_linkage", 0.07),
                "overnight_risk": old_weights.get("overnight_risk", 0.08),
            }
            # 归一化
            total = sum(weights.values())
            weights = {k: v/total for k, v in weights.items()}
            print(f"     ✅ 加载隔夜优化权重 (已适配融合算法)")
        else:
            print(f"     📝 使用默认等权重")
    except Exception as e:
        print(f"     📝 使用默认等权重 ({e})")

    # 扫描
    print(f"\n  🔍 融合算法扫描...")
    scanner = MergedScanner(client=client, weights=weights, buy_threshold=buy_thresh)
    signals = scanner.scan(symbols_list, refine_top=20)

    # 输出
    print(f"\n  📊 融合算法结果:")
    if signals:
        print(f"     {'='*50}")
        print(f"     信号: {len(signals)} 只")
        strong = [s for s in signals if s.signal == "STRONG_BUY"]
        print(f"       强买入: {len(strong)} | 普通: {len(signals)-len(strong)}")
        print(f"     {'='*50}")
        print(f"     {'代码':<14} {'名称':<10} {'涨跌':<8} {'总分':<7} {'信号':<12} {'仓位':<6}")
        print(f"     {'-'*50}")
        for s in signals[:15]:
            icon = "S" if s.signal == "STRONG_BUY" else "B"
            print(f"     {s.symbol:<14} {s.name:<10} {s.change_pct*100:+6.2f}% "
                  f"{s.total_score:.3f}  {icon}-{s.signal:<10} {s.position_pct*100:.0f}%")

        # Top3因子明细
        print(f"\n     📋 Top3 因子明细:")
        for s in signals[:3]:
            print(f"\n     --- {s.symbol} {s.name} ({s.total_score:.3f}) ---")
            fn_map = {
                "tail_volume": "尾盘放量", "intraday_trend": "日内趋势",
                "volume_accum": "量能堆积", "auction": "竞价强度",
                "gap": "缺口博弈", "reversal": "断板反包",
                "ma_position": "均线位置", "seal_quality": "封板质量",
                "global_linkage": "外盘联动", "overnight_risk": "隔夜风险",
            }
            score_map = {
                "tail_volume": s.tail_volume, "intraday_trend": s.intraday_trend,
                "volume_accum": s.volume_accum, "auction": s.auction,
                "gap": s.gap, "reversal": s.reversal,
                "ma_position": s.ma_position, "seal_quality": s.seal_quality,
                "global_linkage": s.global_linkage, "overnight_risk": s.overnight_risk,
            }
            for key, cn_name in fn_map.items():
                score = score_map.get(key, 0.5)
                bar = "#" * int(score * 20) + "-" * (20 - int(score * 20))
                detail = s.details.get(key, {})
                pattern = detail.get("pattern", "-")
                print(f"       {cn_name:<12} [{bar}] {score:.3f}  {pattern}")

        # 保存信号供复盘
        spath = save_signals(signals, "merged", date)
        print(f"\n     💾 信号已保存: {spath}")
        print(f"     💡 明日收盘后运行: python review_engine.py --type merged")
    else:
        print(f"     ⚠️ 未发现信号")

    print(f"\n  ⏰ 建议: 14:55前下单 → 次日开盘卖出 → 收盘后复盘")
    print("=" * 70)
    return signals


def run_tail_scan(date: datetime.date = None):
    """
    尾盘2:40选股模式（TickFlow原生SDK + 胜率优化权重）

    专为 T+1 尾盘买入策略设计：
    - 数据全来自 TickFlow SDK
    - 14:40 实时计算9个尾盘因子
    - 14:55 前输出买入信号
    - 次日开盘卖出
    """
    if not HAS_TICKFLOW:
        print("  ❌ TickFlow SDK 未安装，请先执行: pip install tickflow")
        return None

    if date is None:
        date = datetime.date.today()

    # 1. 交易日检查
    if not check_trading_day(date):
        return None

    print("=" * 70)
    print(f"  ⏰ 尾盘2:40 T+1选股扫描 — {date}")
    print("=" * 70)

    # 2. 初始化TickFlow客户端
    print(f"\n  📡 连接TickFlow API...")
    try:
        client = get_client()
        # 验证连接
        test = client.get_realtime_quotes(["600519.SH"])
        if not test:
            raise RuntimeError("行情获取失败")
        print(f"     ✅ API连接正常")
    except Exception as e:
        print(f"     ❌ TickFlow连接失败: {e}")
        return None

    # 3. 获取全市场候选池
    print(f"\n  📋 获取候选池...")
    try:
        # 从申万行业板块获取股票
        universes = client.list_universes(region="CN", category="equity")
        sw1_industries = [u for u in universes if "SW1" in u["id"]]
        print(f"     申万一级行业: {len(sw1_industries)} 个")

        # 收集各行业代表股
        all_symbols = set()
        for u in sw1_industries[:8]:  # 取前8个行业
            try:
                symbols = client.get_universe_symbols(u["id"])
                all_symbols.update(symbols[:15])  # 每个行业取15只
            except Exception:
                continue

        if not all_symbols:
            # 降级为预设候选池
            all_symbols = [
                "600519.SH", "600036.SH", "600030.SH", "600887.SH",
                "601012.SH", "601318.SH", "600900.SH", "601899.SH",
                "000858.SZ", "000333.SZ", "000001.SZ", "000651.SZ",
                "002594.SZ", "002415.SZ", "000568.SZ", "300750.SZ",
                "300059.SZ", "300274.SZ", "300124.SZ", "300760.SZ",
                "688981.SH", "688111.SH",
            ]

        symbols_list = list(all_symbols)
        print(f"     候选池: {len(symbols_list)} 只")
    except Exception as e:
        print(f"     ⚠️ 板块获取失败: {e}")
        symbols_list = [
            "600519.SH", "600036.SH", "600030.SH", "601012.SH",
            "000858.SZ", "000001.SZ", "002594.SZ", "300750.SZ",
            "300059.SZ", "688981.SH",
        ]
        print(f"     使用预设候选池: {len(symbols_list)} 只")

    # 4. 加载优化权重（优先隔夜优化权重，其次贝叶斯优化权重）
    weights = None
    buy_thresh = 0.55  # 默认阈值
    strong_thresh = 0.72
    try:
        # 优先加载隔夜优化权重
        overnight_path = os.path.join(os.path.dirname(__file__), "reports", "overnight_optimal.json")
        opt_path = os.path.join(os.path.dirname(__file__), "reports", "optimal_weights.json")

        load_path = None
        if os.path.exists(overnight_path):
            load_path = overnight_path
        elif os.path.exists(opt_path):
            load_path = opt_path

        if load_path:
            with open(load_path, "r") as f:
                opt_data = json.load(f)
            weights = opt_data.get("weights")
            buy_thresh = opt_data.get("buy_threshold", 0.55)
            strong_thresh = buy_thresh + 0.17
            exit_s = opt_data.get("exit_strategy", "open")
            wr = opt_data.get("predicted_win_rate", 0)
            exit_name = "次日开盘卖" if exit_s == "open" else "次日收盘卖"
            label = "隔夜优化" if "overnight" in load_path else "贝叶斯优化"
            print(f"     ✅ 加载{label}权重 (胜率{wr*100 if wr<1 else wr:.0f}%, {exit_name})")
        else:
            print(f"     📝 使用默认权重（运行 overnight_optimizer.py 获取优化权重）")
    except Exception:
        print(f"     📝 权重加载失败，使用默认值")

    # 5. 执行尾盘扫描
    print(f"\n  🔍 执行尾盘2:40扫描...")
    scanner = TailScanner(
        client=client,
        weights=weights,
        buy_threshold=buy_thresh,
        strong_buy_threshold=strong_thresh,
    )

    signals = scanner.scan(symbols_list)

    # 6. 输出结果
    print(f"\n  📊 扫描结果:")
    print(f"     {'='*50}")
    if signals:
        print(f"     买入信号: {len(signals)} 只")
        strong = [s for s in signals if s.signal == "STRONG_BUY"]
        normal = [s for s in signals if s.signal == "BUY"]
        print(f"       强买入: {len(strong)} 只")
        print(f"       普通买入: {len(normal)} 只")
        print(f"     {'='*50}")
        print(f"     {'代码':<14} {'名称':<10} {'价格':<8} {'涨跌':<8} {'总分':<7} {'信号':<12} {'仓位':<6}")
        print(f"     {'-'*50}")
        for s in signals[:15]:
            icon = "S" if s.signal == "STRONG_BUY" else "B"
            print(f"     {s.symbol:<14} {s.name:<10} {s.current_price:<8.2f} "
                  f"{s.change_pct*100:+.2f}%   {s.total_score:.3f}  "
                  f"{icon}-{s.signal:<10} {s.position_pct*100:.0f}%")
        if len(signals) > 15:
            print(f"     ... 还有 {len(signals)-15} 只")

        # 因子明细
        print(f"\n     📋 Top3 因子明细:")
        for s in signals[:3]:
            print(f"\n     --- {s.symbol} {s.name} (总分:{s.total_score:.3f}) ---")
            for key, val in s.details.items():
                if isinstance(val, dict) and "pattern" in val:
                    pattern = val["pattern"]
                    score_key = key.replace("_score", "").replace("_", " ")
                    # 找到对应的score
                    score_map = {
                        "tail_volume": s.tail_volume_score,
                        "intraday_trend": s.intraday_trend_score,
                        "volume_accum": s.volume_accum_score,
                        "auction": s.auction_score,
                        "gap": s.gap_score,
                        "reversal": s.reversal_score,
                        "ma_position": s.ma_position_score,
                        "integer_psych": s.integer_psych_score,
                        "sector_rel": s.sector_rel_score,
                    }
                    score = score_map.get(key, 0)
                    bar = "#" * int(score * 20) + "-" * (20 - int(score * 20))
                    print(f"       {key:<16} [{bar}] {score:.3f}  {pattern}")
    else:
        print(f"     ⚠️ 未发现符合条件的买入信号")
        print(f"     💡 可能原因: 阈值过高 | 今日市场弱势 | 候选池偏小")

    print(f"\n  ⏰ 建议操作:")
    print(f"     14:55前完成下单，尾盘集合竞价买入")
    print(f"     次日开盘卖出，严格执行T+1纪律")
    print(f"     止损线: -5% | 止盈线: +8%")

    # 保存信号供复盘
    if signals:
        spath = save_signals(signals, "tail", date)
        print(f"\n  💾 信号已保存: {spath}")
        print(f"  💡 明日收盘后运行: python main.py --review --review-type tail")

    print("\n" + "=" * 70)
    return signals


def run_full_pipeline():
    """全流程模式：当日扫描 + 回测复盘"""
    print("\n" + "=" * 70)
    print("  🔄 全流程模式")
    print("=" * 70)

    # 先执行当日扫描
    scores = run_daily_scan(fetch_advanced=False)
    if scores is None:
        print("  ⚠️ 当日扫描跳过，仅执行回测")

    # 再执行回测
    print("\n")
    result = run_backtest()

    print("\n" + "=" * 70)
    print("  ✅ 全流程完成")
    print("=" * 70)


# ============================================================
# CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="A股 T+1 短线多因子量化交易系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py --tail             # 尾盘2:40选股模式（TickFlow原生SDK）
  python main.py                    # 当日扫描模式（旧版12因子）
  python main.py --backtest         # 回测模式
  python main.py --full             # 全流程（扫描+回测）
  python main.py --date 2026-06-05  # 指定日期扫描
  python main.py --sample           # 仅打印样本股票列表
        """,
    )
    parser.add_argument("--backtest", action="store_true", help="运行回测模式")
    parser.add_argument("--full", action="store_true", help="全流程模式（扫描+回测）")
    parser.add_argument("--date", type=str, default=None, help="指定扫描日期 (YYYY-MM-DD)")
    parser.add_argument("--sample", action="store_true", help="打印样本股票列表")
    parser.add_argument("--advanced", action="store_true", help="启用高级数据获取（需要TickFlow API）")
    parser.add_argument("--tail", action="store_true", help="尾盘2:40选股模式（TickFlow原生SDK）")
    parser.add_argument("--merged", action="store_true", help="融合算法模式（两套因子合并+复盘反馈）")
    parser.add_argument("--review", action="store_true", help="复盘昨日信号")
    parser.add_argument("--review-type", choices=["merged", "tail"], default="merged",
                        help="复盘算法类型")
    parser.add_argument("--save-signals", action="store_true", help="选股后保存信号供复盘")

    args = parser.parse_args()

    if args.review:
        from review_engine import run_daily_review
        run_daily_review(signal_type=args.review_type)
        return

    if args.merged:
        scan_date = None
        if args.date:
            try:
                scan_date = datetime.date.fromisoformat(args.date)
            except ValueError:
                print(f"  ❌ 日期格式错误: {args.date}")
                sys.exit(1)
        run_merged_scan(date=scan_date)
        return

    if args.tail:
        scan_date = None
        if args.date:
            try:
                scan_date = datetime.date.fromisoformat(args.date)
            except ValueError:
                print(f"  ❌ 日期格式错误: {args.date}")
                sys.exit(1)
        run_tail_scan(date=scan_date)
        return

    if args.sample:
        fetcher = DataFetcher()
        stocks = fetcher.get_a_share_universe(sample_mode=True)
        print(f"\n  📋 A股样本股票池 ({len(stocks)} 只):")
        for s in stocks:
            print(f"     {s['symbol']:<14} {s['name']:<8} {s['exchange']:<6} {s['sector']}")
        return

    if args.backtest:
        run_backtest()
        return

    if args.full:
        run_full_pipeline()
        return

    # 默认：当日扫描模式
    scan_date = None
    if args.date:
        try:
            scan_date = datetime.date.fromisoformat(args.date)
        except ValueError:
            print(f"  ❌ 日期格式错误: {args.date}，请使用 YYYY-MM-DD 格式")
            sys.exit(1)

    run_daily_scan(date=scan_date, fetch_advanced=args.advanced)


if __name__ == "__main__":
    main()
