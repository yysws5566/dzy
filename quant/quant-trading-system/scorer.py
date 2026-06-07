"""
多因子加权打分器
- 综合12个因子得分，输出加权总分
- 根据得分阈值生成买入/卖出信号
- 支持因子表现追踪
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import config
from config import FactorWeights, ScoringConfig
from factors import FactorResult


@dataclass
class CompositeScore:
    """综合打分结果"""
    symbol: str
    name: str
    sector: str = ""

    # 总分
    total_score: float = 0.0
    weighted_score: float = 0.0

    # 各类别得分
    price_volume_score: float = 0.0   # 量价类
    capital_flow_score: float = 0.0   # 资金类
    behavior_score: float = 0.0       # 行为类

    # 信号
    signal: str = "HOLD"              # BUY / STRONG_BUY / HOLD / SELL
    signal_score: float = 0.5         # 信号得分

    # 因子明细
    factor_results: Dict[str, FactorResult] = field(default_factory=dict)

    # 置信度
    avg_confidence: float = 0.0
    data_completeness: float = 0.0    # 数据完整度

    # 仓位建议
    suggested_position_pct: float = 0.0
    risk_level: str = "MEDIUM"        # LOW / MEDIUM / HIGH


class MultiFactorScorer:
    """多因子加权打分器"""

    def __init__(self, weights: FactorWeights = None, scoring_cfg: ScoringConfig = None):
        self.weights = weights or config.DEFAULT_WEIGHTS
        self.cfg = scoring_cfg or config.DEFAULT_SCORING
        self.weights.validate()

        # 因子类别映射
        self.price_volume_factors = ["tail_volume_divergence", "seal_quality", "gap_gambit", "integer_psych"]
        self.capital_flow_factors = ["northbound_divergence", "dragon_tiger", "margin_sentiment", "block_trade"]
        self.behavior_factors = ["auction", "board_reversal", "sector_lag", "global_linkage"]

    def score(self, symbol: str, name: str, sector: str,
              factor_results: List[FactorResult]) -> CompositeScore:
        """
        综合打分

        Args:
            symbol: 股票代码
            name: 股票名称
            sector: 所属板块
            factor_results: 12个因子的计算结果

        Returns:
            CompositeScore 综合评分
        """
        result = CompositeScore(symbol=symbol, name=name, sector=sector)

        if not factor_results:
            result.total_score = 0.5
            return result

        # 建立因子名→结果的映射
        factor_map = {fr.factor_name: fr for fr in factor_results}
        result.factor_results = factor_map

        weights_dict = self.weights.to_dict()
        weighted_sum = 0.0
        total_weight = 0.0

        # 分类累加
        pv_sum, pv_weight = 0.0, 0.0
        cf_sum, cf_weight = 0.0, 0.0
        bh_sum, bh_weight = 0.0, 0.0

        confidences = []
        data_qualities = []

        for fname, weight in weights_dict.items():
            if fname not in factor_map:
                continue

            fr = factor_map[fname]
            score = fr.normalized_score
            confidence = fr.confidence

            # 按置信度调整权重
            adjusted_weight = weight * (0.5 + 0.5 * confidence)
            weighted_sum += score * adjusted_weight
            total_weight += adjusted_weight

            # 分类汇总
            if fname in self.price_volume_factors:
                pv_sum += score * weight
                pv_weight += weight
            elif fname in self.capital_flow_factors:
                cf_sum += score * weight
                cf_weight += weight
            elif fname in self.behavior_factors:
                bh_sum += score * weight
                bh_weight += weight

            confidences.append(confidence)
            # 数据完整度：有结果的因子比例
            data_qualities.append(1.0 if fr.detail.get("error") is None else 0.5)

        # 计算总分
        if total_weight > 0:
            result.weighted_score = weighted_sum / total_weight
        else:
            result.weighted_score = 0.5

        # 各类别得分
        result.price_volume_score = pv_sum / max(pv_weight, 0.001)
        result.capital_flow_score = cf_sum / max(cf_weight, 0.001)
        result.behavior_score = bh_sum / max(bh_weight, 0.001)

        # 综合（各类别等权再平均 + 加权得分）
        result.total_score = (
            result.weighted_score * 0.6 +
            (result.price_volume_score + result.capital_flow_score + result.behavior_score) / 3 * 0.4
        )

        # 置信度
        result.avg_confidence = sum(confidences) / max(len(confidences), 1)
        result.data_completeness = sum(data_qualities) / max(len(data_qualities), 1)

        # 信号判定
        result.signal, result.signal_score = self._determine_signal(result)

        # 仓位建议
        result.suggested_position_pct, result.risk_level = self._suggest_position(result)

        return result

    def _determine_signal(self, score: CompositeScore) -> Tuple[str, float]:
        """
        根据得分判定交易信号

        考虑因素：
        - 总分本身
        - 各类别是否一致（一致性越高信号越可靠）
        - 置信度
        """
        total = score.total_score
        confidence = score.avg_confidence

        # 各类别得分标准差（越小越一致）
        cats = [score.price_volume_score, score.capital_flow_score, score.behavior_score]
        cat_std = (sum((c - sum(cats)/3)**2 for c in cats) / 3) ** 0.5 if cats else 0
        consistency_bonus = max(0, 0.05 - cat_std) * 2  # 一致性奖励

        adjusted_score = total + consistency_bonus

        if adjusted_score >= self.cfg.strong_buy_threshold and confidence > 0.55:
            return "STRONG_BUY", adjusted_score
        elif adjusted_score >= self.cfg.buy_threshold and confidence > 0.4:
            return "BUY", adjusted_score
        elif adjusted_score <= 0.30:
            return "SELL", adjusted_score
        else:
            return "HOLD", adjusted_score

    def _suggest_position(self, score: CompositeScore) -> Tuple[float, str]:
        """仓位建议"""
        if score.signal == "STRONG_BUY":
            pct = self.cfg.strong_position_pct
            risk = "MEDIUM"
        elif score.signal == "BUY":
            pct = self.cfg.base_position_pct
            risk = "MEDIUM"
        elif score.signal == "SELL":
            pct = 0.0
            risk = "HIGH"
        else:
            pct = 0.0
            risk = "LOW"

        # 根据置信度微调
        if score.avg_confidence > 0.8 and pct > 0:
            pct = min(self.cfg.max_position_pct, pct * 1.3)
            risk = "LOW"
        elif score.avg_confidence < 0.45 and pct > 0:
            pct *= 0.6
            risk = "HIGH"

        return pct, risk

    def rank_candidates(self, scores: List[CompositeScore]) -> List[CompositeScore]:
        """对候选池排序（买入信号在前，按总分降序）"""
        buy_signals = [s for s in scores if s.signal in ("STRONG_BUY", "BUY")]
        others = [s for s in scores if s.signal not in ("STRONG_BUY", "BUY")]

        buy_signals.sort(key=lambda s: (s.signal == "STRONG_BUY", s.total_score), reverse=True)
        others.sort(key=lambda s: s.total_score, reverse=True)

        return buy_signals + others
