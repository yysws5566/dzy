"""
量化交易系统 - 全局配置
A股 T+1 短线多因子策略
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List

# ============================================================
# API 密钥
# ============================================================
TICKFLOW_API_KEY = os.environ.get("TICKFLOW_API_KEY", "")
TICKFLOW_BASE_URL = "https://api.tickflow.com"  # TickFlow API 基础地址

# Finance API 网关配置（用于补充数据）
FINANCE_GATEWAY_URL = "https://internal-api.z.ai"
FINANCE_API_PREFIX = "/external/finance"
FINANCE_HEADERS = {"X-Z-AI-From": "Z"}

# ============================================================
# 交易日历
# ============================================================
# A股休市月份/日期（动态更新，通过交易日历模块自动获取）
# 固定休假日（春节、国庆等以每年实际公告为准）
FIXED_HOLIDAYS_2026 = [
    "2026-01-01",  # 元旦
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",  # 春节(2/17除夕)
    "2026-04-06",  # 清明节
    "2026-05-01", "2026-05-04", "2026-05-05",  # 劳动节
    "2026-06-19",  # 端午节
    "2026-09-25",  # 中秋节
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",  # 国庆节
]

# ============================================================
# 流动性筛选参数
# ============================================================
@dataclass
class LiquidityFilter:
    """流动性初筛条件"""
    min_daily_volume: int = 5_000_000         # 最小日成交量（股），500万股
    min_daily_turnover: float = 20_000_000    # 最小日成交额（元），2000万
    min_avg_volume_20d: int = 3_000_000       # 20日均量（股），300万
    max_price: float = 200.0                  # 最高股价（元），排除高价垃圾股
    min_price: float = 3.0                    # 最低股价（元），排除ST和1元以下仙股
    exclude_st: bool = True                    # 排除ST股
    exclude_new_listings: int = 60            # 排除上市不足N天的次新股
    min_turnover_rate: float = 0.5            # 最小换手率（%），排除僵尸股


# ============================================================
# 12因子权重配置
# ============================================================
@dataclass
class FactorWeights:
    """多因子加权配置，权重合计=1.0"""
    # 量价类因子（权重合计 0.35）
    tail_volume_divergence: float = 0.12   # 因子1: 尾盘量价背离
    seal_quality: float = 0.08             # 因子2: 封板质量
    gap_gambit: float = 0.10               # 因子3: 缺口博弈
    integer_psych: float = 0.05            # 因子9: 整数关口

    # 资金类因子（权重合计 0.35）
    northbound_divergence: float = 0.12    # 因子4: 北向资金背离
    dragon_tiger: float = 0.08             # 因子7: 龙虎榜
    margin_sentiment: float = 0.07         # 因子10: 融资情绪
    block_trade: float = 0.08              # 因子11: 大宗交易

    # 行为类因子（权重合计 0.30）
    auction: float = 0.10                  # 因子5: 集合竞价
    board_reversal: float = 0.08           # 因子6: 断板反包
    sector_lag: float = 0.07               # 因子8: 板块滞后
    global_linkage: float = 0.05           # 因子12: 外盘联动

    def to_dict(self) -> Dict[str, float]:
        return {
            "tail_volume_divergence": self.tail_volume_divergence,
            "seal_quality": self.seal_quality,
            "gap_gambit": self.gap_gambit,
            "northbound_divergence": self.northbound_divergence,
            "auction": self.auction,
            "board_reversal": self.board_reversal,
            "dragon_tiger": self.dragon_tiger,
            "sector_lag": self.sector_lag,
            "integer_psych": self.integer_psych,
            "margin_sentiment": self.margin_sentiment,
            "block_trade": self.block_trade,
            "global_linkage": self.global_linkage,
        }

    def validate(self):
        total = sum(self.to_dict().values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"因子权重合计应为1.0，当前为{total:.4f}")


# ============================================================
# 打分与仓位配置
# ============================================================
@dataclass
class ScoringConfig:
    """打分与仓位管理参数"""
    # 信号阈值（可根据实盘/模拟模式调整）
    buy_threshold: float = 0.58       # 综合得分 >= 此值触发买入（模拟模式偏低，实盘建议0.65）
    strong_buy_threshold: float = 0.75  # 综合得分 >= 此值强买入

    # 仓位管理
    max_positions: int = 8            # 最大持仓数
    base_position_pct: float = 0.10   # 基础仓位比例（总资金10%）
    max_position_pct: float = 0.25    # 单票最大仓位比例
    strong_position_pct: float = 0.18  # 强买入仓位比例

    # 风险管理
    stop_loss_pct: float = -0.05      # 止损线 -5%
    take_profit_pct: float = 0.08     # 止盈线 +8%
    max_daily_loss_pct: float = -0.03  # 单日最大亏损 -3%（触发后停止交易）
    t_plus_1_hold_days: int = 1       # T+1持有天数（默认次日卖出）


# ============================================================
# 回测配置
# ============================================================
@dataclass
class BacktestConfig:
    """回测参数"""
    lookback_days: int = 120          # 回测回溯天数
    initial_capital: float = 1_000_000  # 初始资金（元）
    commission_rate: float = 0.0003   # 佣金费率（万分之三）
    stamp_tax_rate: float = 0.001     # 印花税（千分之一，仅卖出）
    slippage: float = 0.001           # 滑点（0.1%）


# 默认配置实例
DEFAULT_LIQUIDITY = LiquidityFilter()
DEFAULT_WEIGHTS = FactorWeights()
DEFAULT_SCORING = ScoringConfig()
DEFAULT_BACKTEST = BacktestConfig()
