"""
因子计算基类 + 因子注册表

12个基础因子 + 3个增强因子(v9新增)
├── 量价类: factor1-4, factor9, factor13(RSI), factor14(KDJ)
├── 资金类: factor4, factor7, factor10, factor11, factor15(主力资金流)
└── 行为类: factor3, factor5, factor6, factor8, factor12

v9 新增增强因子:
  factor13_rsi_oversold  — RSI(14)超卖反弹识别
  factor14_kdj           — KDJ低位金叉检测
  factor15_money_flow    — 主力资金流向推算
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FactorResult:
    """单个因子的计算结果"""
    factor_name: str           # 因子名称
    raw_score: float           # 原始得分 [0, 1]
    normalized_score: float    # 标准化得分 [0, 1]
    signal: int = 0            # 信号方向: 1=看多, 0=中性, -1=看空
    signal_str: str = "NEUTRAL"
    confidence: float = 0.5    # 置信度 [0, 1]
    detail: Dict[str, Any] = field(default_factory=dict)  # 详细计算数据

    def __post_init__(self):
        if not self.detail:
            self.detail = {}


class BaseFactor(ABC):
    """因子基类"""

    # 因子元信息（子类必须定义）
    name: str = "base"
    description: str = "基础因子"
    category: str = "未分类"  # 量价/资金/行为

    def __init__(self, weight: float = 0.0):
        self.weight = weight

    @abstractmethod
    def calculate(self, snapshot) -> FactorResult:
        """
        计算因子值

        Args:
            snapshot: MarketSnapshot 对象，包含该股票的完整数据

        Returns:
            FactorResult 对象
        """
        pass

    def normalize_score(self, raw: float, floor: float = 0.0, ceiling: float = 1.0) -> float:
        """将原始得分裁剪至[floor, ceiling]"""
        return max(floor, min(ceiling, raw))

    def compute_confidence(self, data_quality: float, signal_strength: float) -> float:
        """
        计算置信度

        Args:
            data_quality: 数据质量 [0, 1]，数据越完整越接近1
            signal_strength: 信号强度 [0, 1]，信号越明确越接近1
        """
        return self.normalize_score(data_quality * 0.4 + signal_strength * 0.6)


# ── v9 增强因子注册 ──
ENHANCED_FACTOR_NAMES = [
    "rsi_oversold",      # 因子13: RSI超卖反弹
    "kdj_golden_cross",  # 因子14: KDJ低位金叉
    "money_flow",        # 因子15: 主力资金流向
]

# 增强因子推荐权重（用于 scorer 升级）
ENHANCED_FACTOR_WEIGHTS = {
    "rsi_oversold": 0.10,
    "kdj_golden_cross": 0.08,
    "money_flow": 0.12,
}
