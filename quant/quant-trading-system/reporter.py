"""
报告生成模块
- 生成每日扫描报告（买入信号 + 仓位建议）
- 生成回测复盘报告（胜率 + 因子表现）
- 支持控制台输出和Markdown文件
"""

import encoding_fix  # noqa: F401

import datetime
import json
import os
from typing import Dict, List, Optional

from scorer import CompositeScore
from backtest import BacktestResult, TradeRecord


class Reporter:
    """报告生成器"""

    def __init__(self, output_dir: str = None):
        self.output_dir = output_dir or os.path.join(os.path.dirname(__file__), "reports")
        os.makedirs(self.output_dir, exist_ok=True)

    def print_daily_summary(self, scores: List[CompositeScore], trade_date: str = None):
        """打印每日扫描摘要（控制台）"""
        if trade_date is None:
            trade_date = datetime.date.today().isoformat()

        print("=" * 70)
        print(f"  🐂 A股 T+1 短线多因子扫描报告 — {trade_date}")
        print("=" * 70)

        buy_signals = [s for s in scores if s.signal in ("STRONG_BUY", "BUY")]
        strong_buys = [s for s in buy_signals if s.signal == "STRONG_BUY"]

        print(f"\n  📊 扫描概览")
        print(f"     候选池标的: {len(scores)} 只")
        print(f"     买入信号:   {len(buy_signals)} 只")
        print(f"     强买入:     {len(strong_buys)} 只")
        print(f"     普通买入:   {len(buy_signals) - len(strong_buys)} 只")

        if buy_signals:
            print(f"\n  {'='*60}")
            print(f"  🎯 买入信号明细")
            print(f"  {'='*60}")
            print(f"  {'排名':<5} {'代码':<14} {'名称':<10} {'总分':<7} {'信号':<12} {'仓位':<8} {'风险':<8} {'量价':<7} {'资金':<7} {'行为':<7}")
            print(f"  {'-'*60}")

            for i, s in enumerate(buy_signals[:20], 1):
                signal_icon = "🔥" if s.signal == "STRONG_BUY" else "📈"
                print(f"  {i:<5} {s.symbol:<14} {s.name:<10} {s.total_score:.3f}  "
                      f"{signal_icon}{s.signal:<10} {s.suggested_position_pct*100:.1f}%    "
                      f"{s.risk_level:<8} {s.price_volume_score:.3f}  {s.capital_flow_score:.3f}  {s.behavior_score:.3f}")

            if len(buy_signals) > 20:
                print(f"  ... 还有 {len(buy_signals) - 20} 只买入信号（详见报告文件）")

        # 因子表现总结
        print(f"\n  {'='*60}")
        print(f"  📈 因子类别表现")
        print(f"  {'='*60}")
        avg_pv = sum(s.price_volume_score for s in scores) / max(len(scores), 1)
        avg_cf = sum(s.capital_flow_score for s in scores) / max(len(scores), 1)
        avg_bh = sum(s.behavior_score for s in scores) / max(len(scores), 1)
        print(f"     量价类因子均分: {avg_pv:.3f}")
        print(f"     资金类因子均分: {avg_cf:.3f}")
        print(f"     行为类因子均分: {avg_bh:.3f}")

        print(f"\n  ⚠️ 风险提示: 本报告仅供研究参考，不构成投资建议。股市有风险，投资需谨慎。")
        print("=" * 70)

    def save_daily_report(self, scores: List[CompositeScore], trade_date: str = None) -> str:
        """保存每日扫描报告为Markdown文件"""
        if trade_date is None:
            trade_date = datetime.date.today().isoformat()

        buy_signals = [s for s in scores if s.signal in ("STRONG_BUY", "BUY")]

        lines = []
        lines.append(f"# A股 T+1 短线多因子扫描报告")
        lines.append(f"**日期**: {trade_date}")
        lines.append(f"**生成时间**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 📊 扫描概览")
        lines.append("")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 候选池标的 | {len(scores)} |")
        lines.append(f"| 买入信号 | {len(buy_signals)} |")
        lines.append(f"| 强买入 | {len([s for s in buy_signals if s.signal == 'STRONG_BUY'])} |")
        lines.append(f"| 普通买入 | {len([s for s in buy_signals if s.signal == 'BUY'])} |")
        lines.append("")

        if buy_signals:
            lines.append("## 🎯 买入信号明细")
            lines.append("")
            lines.append("| 排名 | 代码 | 名称 | 板块 | 总分 | 信号 | 建议仓位 | 风险 | 量价 | 资金 | 行为 |")
            lines.append("|------|------|------|------|------|------|----------|------|------|------|------|")
            for i, s in enumerate(buy_signals[:30], 1):
                icon = "🔥" if s.signal == "STRONG_BUY" else "📈"
                lines.append(f"| {i} | {s.symbol} | {s.name} | {s.sector} | {s.total_score:.3f} | "
                             f"{icon} {s.signal} | {s.suggested_position_pct*100:.1f}% | {s.risk_level} | "
                             f"{s.price_volume_score:.3f} | {s.capital_flow_score:.3f} | {s.behavior_score:.3f} |")

            # 每只买入信号的因子明细
            lines.append("")
            lines.append("## 📋 因子明细")
            for s in buy_signals[:10]:
                lines.append(f"### {s.symbol} {s.name} (总分: {s.total_score:.3f})")
                lines.append("")
                if s.factor_results:
                    lines.append("| 因子 | 得分 | 信号 | 置信度 | 详情 |")
                    lines.append("|------|------|------|--------|------|")
                    for fname, fr in s.factor_results.items():
                        pattern = fr.detail.get("pattern", fr.detail.get("proxy_pattern", "-"))
                        signal_str = "📈多" if fr.signal == 1 else ("📉空" if fr.signal == -1 else "➖中")
                        lines.append(f"| {fname} | {fr.normalized_score:.3f} | {signal_str} | {fr.confidence:.2f} | {pattern} |")
                lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("## ⚠️ 风险提示")
        lines.append("> 本报告由量化交易系统自动生成，仅供研究参考，不构成投资建议。")
        lines.append("> 股市有风险，投资需谨慎。T+1策略存在隔夜风险，请根据自身风险承受能力审慎决策。")

        filename = f"daily_scan_{trade_date}.md"
        filepath = os.path.join(self.output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return filepath

    def print_backtest_report(self, result: BacktestResult):
        """打印回测复盘报告"""
        print("\n" + "=" * 70)
        print("  📊 回测复盘报告")
        print("=" * 70)

        print(f"\n  🏆 交易统计")
        print(f"     总交易次数:     {result.total_trades}")
        print(f"     盈利次数:       {result.win_trades}")
        print(f"     亏损次数:       {result.lose_trades}")
        print(f"     胜率:           {result.win_rate*100:.1f}%")

        print(f"\n  💰 收益指标")
        print(f"     总收益率:       {result.total_return:.2f}%")
        print(f"     平均收益率:     {result.avg_return:.2f}%")
        print(f"     平均盈利:       {result.avg_win_return:.2f}%")
        print(f"     平均亏损:       {result.avg_lose_return:.2f}%")
        print(f"     盈亏比:         {result.profit_factor:.2f}")

        print(f"\n  ⚠️ 风险指标")
        print(f"     最大回撤:       {result.max_drawdown:.2f}%")
        print(f"     夏普比率:       {result.sharpe_ratio:.2f}")
        print(f"     卡尔玛比率:     {result.calmar_ratio:.2f}")
        print(f"     最大连续亏损:   {result.max_consecutive_losses} 次")

        # 信号分层
        print(f"\n  📶 信号分层表现")
        sb = result.strong_buy_stats
        b = result.buy_stats
        print(f"     🔥 强买入: {sb.get('count', 0)}笔, 胜率{sb.get('win_rate', 0):.1f}%, 均收{sb.get('avg_return', 0):.2f}%")
        print(f"     📈 普通买入: {b.get('count', 0)}笔, 胜率{b.get('win_rate', 0):.1f}%, 均收{b.get('avg_return', 0):.2f}%")

        # 因子表现
        if result.factor_ic:
            print(f"\n  🔬 因子表现（IC值）")
            sorted_factors = sorted(result.factor_ic.items(), key=lambda x: abs(x[1]), reverse=True)
            for fname, ic in sorted_factors:
                bar = "█" * int(abs(ic) * 50) + "░" * (10 - int(abs(ic) * 50))
                fr = result.factor_returns.get(fname, 0)
                print(f"     {fname:<28} IC={ic:+.3f} {bar} 因子收益={fr:+.2f}%")

        # 最近交易
        if result.trades:
            print(f"\n  📜 最近10笔交易")
            print(f"     {'日期':<12} {'代码':<12} {'名称':<10} {'买入':<8} {'卖出':<8} {'收益':<8} {'结果':<6} {'原因'}")
            for t in result.trades[-10:]:
                icon = "✅" if t.is_win else "❌"
                print(f"     {t.date:<12} {t.symbol:<12} {t.name:<10} {t.buy_price:<8.2f} {t.sell_price:<8.2f} "
                      f"{t.net_return_pct:+6.2f}% {icon:<6} {t.exit_reason}")

        print("=" * 70)

    def save_backtest_report(self, result: BacktestResult) -> str:
        """保存回测报告为Markdown"""
        date_str = datetime.date.today().isoformat()

        lines = []
        lines.append(f"# 回测复盘报告")
        lines.append(f"**生成日期**: {date_str}")
        lines.append(f"**回测周期**: 近{result.total_trades}笔交易")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 🏆 交易统计")
        lines.append("")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 总交易次数 | {result.total_trades} |")
        lines.append(f"| 盈利次数 | {result.win_trades} |")
        lines.append(f"| 亏损次数 | {result.lose_trades} |")
        lines.append(f"| 胜率 | {result.win_rate*100:.1f}% |")
        lines.append(f"| 平均收益率 | {result.avg_return:.2f}% |")
        lines.append(f"| 盈亏比 | {result.profit_factor:.2f} |")
        lines.append(f"| 最大回撤 | {result.max_drawdown:.2f}% |")
        lines.append(f"| 夏普比率 | {result.sharpe_ratio:.2f} |")
        lines.append("")
        lines.append("## 🔬 因子表现")
        lines.append("")
        lines.append("| 因子 | IC值 | 因子收益率 | 高信号胜率 | 低信号胜率 |")
        lines.append("|------|------|------------|------------|------------|")
        for fname, ic in sorted(result.factor_ic.items(), key=lambda x: abs(x[1]), reverse=True):
            fr = result.factor_returns.get(fname, 0)
            fwr = result.factor_win_rates.get(fname, {})
            lines.append(f"| {fname} | {ic:+.3f} | {fr:+.2f}% | {fwr.get('high_signal_win_rate', '-')}% | "
                         f"{fwr.get('low_signal_win_rate', '-')}% |")
        lines.append("")

        filename = f"backtest_report_{date_str}.md"
        filepath = os.path.join(self.output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return filepath

    def save_trade_log(self, trades: List[TradeRecord]) -> str:
        """保存交易日志"""
        date_str = datetime.date.today().isoformat()
        filepath = os.path.join(self.output_dir, f"trade_log_{date_str}.json")
        data = []
        for t in trades:
            data.append({
                "date": t.date,
                "symbol": t.symbol,
                "name": t.name,
                "buy_price": t.buy_price,
                "sell_price": t.sell_price,
                "shares": t.shares,
                "return_pct": round(t.net_return_pct, 3),
                "is_win": t.is_win,
                "signal": t.signal,
                "total_score": round(t.total_score, 3),
                "exit_reason": t.exit_reason,
            })
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return filepath
