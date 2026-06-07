"""
回测引擎
- 基于历史数据模拟T+1策略表现
- 统计胜率、盈亏比、最大回撤、夏普比率
- 因子表现分析（IC值、因子收益率）
"""

import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import config
from config import BacktestConfig, ScoringConfig


@dataclass
class TradeRecord:
    """单笔交易记录"""
    date: str                     # 买入日期
    symbol: str
    name: str
    buy_price: float
    sell_price: float
    shares: int
    position_pct: float
    signal: str                   # STRONG_BUY / BUY
    total_score: float
    return_pct: float             # 收益率
    net_return_pct: float         # 扣除成本后的净收益
    is_win: bool
    hold_days: int = 1            # T+1，持有天数
    exit_reason: str = ""         # 卖出原因
    factor_scores: Dict[str, float] = field(default_factory=dict)


@dataclass
class BacktestResult:
    """回测结果"""
    # 基本统计
    total_trades: int = 0
    win_trades: int = 0
    lose_trades: int = 0

    # 收益率
    win_rate: float = 0.0
    avg_return: float = 0.0
    avg_win_return: float = 0.0
    avg_lose_return: float = 0.0
    profit_factor: float = 0.0     # 盈亏比
    total_return: float = 0.0      # 总收益率
    total_net_return: float = 0.0  # 扣除成本总收益

    # 风险指标
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_consecutive_losses: int = 0

    # 资金曲线
    equity_curve: List[float] = field(default_factory=list)
    daily_returns: List[float] = field(default_factory=list)

    # 因子表现
    factor_ic: Dict[str, float] = field(default_factory=dict)        # IC值
    factor_returns: Dict[str, float] = field(default_factory=dict)   # 因子收益率
    factor_win_rates: Dict[str, float] = field(default_factory=dict) # 各因子信号胜率

    # 信号分层
    strong_buy_stats: Dict[str, Any] = field(default_factory=dict)
    buy_stats: Dict[str, Any] = field(default_factory=dict)

    # 交易明细
    trades: List[TradeRecord] = field(default_factory=list)


class BacktestEngine:
    """T+1策略回测引擎"""

    def __init__(self, backtest_cfg: BacktestConfig = None, scoring_cfg: ScoringConfig = None):
        self.cfg = backtest_cfg or config.DEFAULT_BACKTEST
        self.scoring_cfg = scoring_cfg or config.DEFAULT_SCORING
        self.capital = self.cfg.initial_capital

    def run(self, daily_signals: Dict[str, List[dict]], price_data: Dict[str, List[dict]]) -> BacktestResult:
        """
        执行回测

        Args:
            daily_signals: {date_iso: [{symbol, name, signal, total_score, suggested_position_pct, ...}]}
            price_data: {symbol: [daily_bars]}  历史K线数据

        Returns:
            BacktestResult 回测结果
        """
        result = BacktestResult()
        equity = [self.capital]
        daily_rets = []
        trades = []
        open_positions: Dict[str, dict] = {}  # 当前持仓

        # 按日期排序
        sorted_dates = sorted(daily_signals.keys())

        for i, date_str in enumerate(sorted_dates):
            signals = daily_signals[date_str]

            # 1. 先处理卖出（T+1平仓）
            self._process_exits(date_str, open_positions, price_data, trades, result)

            # 2. 处理买入信号
            available_cash = self.capital * 0.3  # 单日最多用30%资金
            for sig in signals[:self.scoring_cfg.max_positions]:
                if sig["signal"] not in ("STRONG_BUY", "BUY"):
                    continue

                symbol = sig["symbol"]
                if symbol in open_positions:
                    continue  # 已持仓

                # 获取当日价格
                price_info = self._get_price_at_date(symbol, date_str, price_data)
                if price_info is None:
                    continue

                buy_price = price_info["open"]  # 以次日开盘价买入（T+1确认）

                # 仓位计算
                position_pct = min(sig.get("suggested_position_pct", 0.10), self.scoring_cfg.max_position_pct)
                position_value = self.capital * position_pct
                if position_value > available_cash:
                    position_value = available_cash

                shares = int(position_value / buy_price / 100) * 100  # 整手
                if shares == 0:
                    continue

                actual_cost = shares * buy_price * (1 + self.cfg.commission_rate)
                if actual_cost > self.capital * 0.05:  # 至少用5%资金
                    open_positions[symbol] = {
                        "buy_date": date_str,
                        "buy_price": buy_price,
                        "shares": shares,
                        "position_pct": position_pct,
                        "signal": sig["signal"],
                        "total_score": sig["total_score"],
                        "name": sig.get("name", symbol),
                        "factor_scores": sig.get("factor_scores", {}),
                        "cost": actual_cost,
                    }
                    self.capital -= actual_cost
                    available_cash -= actual_cost

            # 3. 记录当日权益
            total_equity = self.capital + sum(
                self._get_position_value(pos, price_data, open_positions)
                for pos in open_positions.values()
            )
            equity.append(total_equity)

            if i > 0:
                daily_rets.append((equity[-1] - equity[-2]) / equity[-2])

        # 4. 强制平仓未平仓位
        last_date = sorted_dates[-1] if sorted_dates else ""
        for symbol in list(open_positions.keys()):
            self._force_close(symbol, last_date, open_positions, price_data, trades, result, "回测到期平仓")

        # 5. 计算统计指标
        result.trades = trades
        result.total_trades = len(trades)
        result.win_trades = sum(1 for t in trades if t.is_win)
        result.lose_trades = result.total_trades - result.win_trades

        self._compute_statistics(result, equity, daily_rets)
        self._compute_factor_performance(result)

        return result

    def _process_exits(self, current_date: str, positions: dict, price_data: dict,
                       trades: list, result: BacktestResult):
        """处理T+1卖出"""
        to_close = []

        for symbol, pos in positions.items():
            buy_date = datetime.date.fromisoformat(pos["buy_date"])
            cur_date = datetime.date.fromisoformat(current_date)
            days_held = (cur_date - buy_date).days

            # T+1：次日卖出
            if days_held >= 1:
                price_info = self._get_price_at_date(symbol, current_date, price_data)
                if price_info:
                    to_close.append((symbol, price_info, days_held, "T+1到期"))
                elif days_held >= 5:
                    # 超期，无价格也强平
                    to_close.append((symbol, None, days_held, "数据缺失强平"))

        for symbol, price_info, days_held, reason in to_close:
            if price_info:
                sell_price = price_info["open"]  # 次日开盘卖
            else:
                sell_price = positions[symbol]["buy_price"]

            self._close_position(symbol, sell_price, positions, trades, result, reason, days_held)

    def _close_position(self, symbol: str, sell_price: float, positions: dict,
                        trades: list, result: BacktestResult, reason: str, days_held: int = 1):
        """平仓"""
        if symbol not in positions:
            return

        pos = positions.pop(symbol)
        buy_price = pos["buy_price"]
        shares = pos["shares"]

        # 计算收益
        gross_return = (sell_price - buy_price) / buy_price

        # 扣除成本（买入佣金 + 卖出佣金 + 印花税）
        cost_rate = self.cfg.commission_rate * 2 + self.cfg.stamp_tax_rate
        # 滑点
        slippage = self.cfg.slippage
        net_return = gross_return - cost_rate - slippage

        # 资金回笼
        sell_proceeds = shares * sell_price * (1 - self.cfg.commission_rate - self.cfg.stamp_tax_rate)
        self.capital += sell_proceeds

        # 止损/止盈触发判定
        if net_return <= self.scoring_cfg.stop_loss_pct:
            exit_reason = f"止损（{reason}）"
        elif net_return >= self.scoring_cfg.take_profit_pct:
            exit_reason = f"止盈（{reason}）"
        else:
            exit_reason = reason

        trade = TradeRecord(
            date=pos["buy_date"],
            symbol=symbol,
            name=pos["name"],
            buy_price=buy_price,
            sell_price=sell_price,
            shares=shares,
            position_pct=pos["position_pct"],
            signal=pos["signal"],
            total_score=pos["total_score"],
            return_pct=gross_return * 100,
            net_return_pct=net_return * 100,
            is_win=net_return > 0,
            hold_days=days_held,
            exit_reason=exit_reason,
            factor_scores=pos.get("factor_scores", {}),
        )
        trades.append(trade)

    def _force_close(self, symbol: str, date: str, positions: dict, price_data: dict,
                     trades: list, result: BacktestResult, reason: str):
        price_info = self._get_price_at_date(symbol, date, price_data)
        sell_price = price_info["close"] if price_info else positions[symbol]["buy_price"]
        self._close_position(symbol, sell_price, positions, trades, result, reason)

    def _get_price_at_date(self, symbol: str, date_str: str, price_data: dict) -> Optional[dict]:
        """获取指定日期的价格"""
        bars = price_data.get(symbol, [])
        for bar in bars:
            bar_date = bar.get("date", bar.get("datetime", ""))
            if isinstance(bar_date, datetime.datetime):
                bar_date = bar_date.strftime("%Y-%m-%d")
            if bar_date == date_str or bar_date.startswith(date_str):
                return bar
        return None

    def _get_position_value(self, pos: dict, price_data: dict, positions: dict) -> float:
        """计算持仓市值（用最近收盘价估算）"""
        symbol = pos.get("symbol", "")
        bars = price_data.get(symbol, [])
        if bars:
            latest_price = bars[-1].get("close", pos["buy_price"])
        else:
            latest_price = pos["buy_price"]
        return pos["shares"] * latest_price

    def _compute_statistics(self, result: BacktestResult, equity: list, daily_rets: list):
        """计算统计指标"""
        if not result.trades:
            return

        wins = [t for t in result.trades if t.is_win]
        loses = [t for t in result.trades if not t.is_win]

        result.win_rate = len(wins) / len(result.trades) if result.trades else 0
        result.avg_return = sum(t.net_return_pct for t in result.trades) / len(result.trades)

        if wins:
            result.avg_win_return = sum(t.net_return_pct for t in wins) / len(wins)
        if loses:
            result.avg_lose_return = sum(t.net_return_pct for t in loses) / len(loses)

        # 盈亏比
        total_win = sum(t.net_return_pct for t in wins) if wins else 0
        total_lose = abs(sum(t.net_return_pct for t in loses)) if loses else 1
        result.profit_factor = total_win / max(total_lose, 0.01)

        # 总收益
        result.total_return = (equity[-1] - equity[0]) / equity[0] * 100

        # 最大回撤
        peak = equity[0]
        max_dd = 0
        for e in equity:
            if e > peak:
                peak = e
            dd = (peak - e) / peak
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown = max_dd * 100

        # 夏普比率（简化计算）
        if daily_rets and len(daily_rets) > 1:
            import math
            avg_ret = sum(daily_rets) / len(daily_rets)
            std_ret = (sum((r - avg_ret)**2 for r in daily_rets) / len(daily_rets)) ** 0.5
            result.sharpe_ratio = (avg_ret / std_ret * (252 ** 0.5)) if std_ret > 0 else 0

        # 卡尔玛比率
        result.calmar_ratio = (result.total_return / 100) / max(max_dd, 0.001)

        # 最大连续亏损
        max_consec = 0
        consec = 0
        for t in result.trades:
            if not t.is_win:
                consec += 1
                max_consec = max(max_consec, consec)
            else:
                consec = 0
        result.max_consecutive_losses = max_consec

        # 分层统计
        strong_buys = [t for t in result.trades if t.signal == "STRONG_BUY"]
        buys = [t for t in result.trades if t.signal == "BUY"]
        result.strong_buy_stats = self._layer_stats(strong_buys)
        result.buy_stats = self._layer_stats(buys)

        result.equity_curve = equity
        result.daily_returns = daily_rets

    def _layer_stats(self, trades: list) -> dict:
        """分层统计"""
        if not trades:
            return {"count": 0, "win_rate": 0, "avg_return": 0}
        wins = sum(1 for t in trades if t.is_win)
        avg = sum(t.net_return_pct for t in trades) / len(trades)
        return {
            "count": len(trades),
            "win_rate": wins / len(trades) * 100,
            "avg_return": avg,
        }

    def _compute_factor_performance(self, result: BacktestResult):
        """计算因子表现（IC值、胜率）"""
        if not result.trades:
            return

        # 收集每个因子在所有交易中的得分和收益
        factor_scores_all: Dict[str, List[Tuple[float, float]]] = {}
        for trade in result.trades:
            for fname, fscore in trade.factor_scores.items():
                if fname not in factor_scores_all:
                    factor_scores_all[fname] = []
                factor_scores_all[fname].append((fscore, trade.net_return_pct))

        # 计算IC（信息系数，因子得分与收益的相关系数）
        for fname, pairs in factor_scores_all.items():
            if len(pairs) < 5:
                continue
            scores = [p[0] for p in pairs]
            returns = [p[1] for p in pairs]
            ic = self._pearson_correlation(scores, returns)
            result.factor_ic[fname] = ic

            # 因子收益率（高分组的平均收益 - 低分组的平均收益）
            sorted_pairs = sorted(pairs, key=lambda x: x[0])
            split = len(sorted_pairs) // 2
            high_group = sorted_pairs[-split:] if split > 0 else sorted_pairs
            low_group = sorted_pairs[:split] if split > 0 else sorted_pairs
            high_avg = sum(p[1] for p in high_group) / max(len(high_group), 1)
            low_avg = sum(p[1] for p in low_group) / max(len(low_group), 1)
            result.factor_returns[fname] = high_avg - low_avg

            # 因子信号胜率（因子得分>0.6的交易胜率 vs <=0.6的胜率）
            high_signal = [p for p in pairs if p[0] > 0.6]
            low_signal = [p for p in pairs if p[0] <= 0.6]
            high_win_rate = sum(1 for p in high_signal if p[1] > 0) / max(len(high_signal), 1) * 100
            low_win_rate = sum(1 for p in low_signal if p[1] > 0) / max(len(low_signal), 1) * 100
            result.factor_win_rates[fname] = {
                "high_signal_win_rate": round(high_win_rate, 1),
                "low_signal_win_rate": round(low_win_rate, 1),
                "high_signal_count": len(high_signal),
            }

    @staticmethod
    def _pearson_correlation(x: List[float], y: List[float]) -> float:
        """皮尔逊相关系数"""
        n = len(x)
        if n < 3:
            return 0
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        std_x = (sum((xi - mean_x)**2 for xi in x)) ** 0.5
        std_y = (sum((yi - mean_y)**2 for yi in y)) ** 0.5
        if std_x == 0 or std_y == 0:
            return 0
        return cov / (std_x * std_y)
