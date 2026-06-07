"""
模拟数据模块
- 在API不可达时生成逼真的A股模拟数据
- 用于系统演示、回测验证和因子效果展示
- 生成的模拟数据具有真实的量价特征
"""

import datetime
import math
import random
from typing import Any, Dict, List


def _generate_daily_bars(symbol: str, days: int = 60, base_price: float = 50.0,
                          trend: float = 0.0, volatility: float = 0.025) -> List[Dict[str, Any]]:
    """
    生成模拟日线数据

    Args:
        symbol: 股票代码
        days: 生成天数
        base_price: 基础价格
        trend: 趋势方向 (-0.3~0.3)
        volatility: 日波动率
    """
    # 根据symbol的hash确定随机种子（保证同一symbol生成一致的数据）
    seed = sum(ord(c) for c in symbol)
    random.seed(seed)

    bars = []
    price = base_price * (0.7 + random.random() * 0.6)  # 起始价在base_price的70%-130%

    start_date = datetime.date.today() - datetime.timedelta(days=days)
    current_date = start_date

    # 按板块设定不同的趋势
    trend += random.uniform(-0.1, 0.1)  # 加入随机偏移

    for i in range(days):
        # 跳过周末
        while current_date.weekday() >= 5:
            current_date += datetime.timedelta(days=1)

        # 日收益率（带趋势和波动）
        daily_ret = random.gauss(trend / 252, volatility)

        # 偶尔加入一些特殊形态
        if random.random() < 0.03:
            # 涨停
            daily_ret = random.uniform(0.095, 0.10)
        elif random.random() < 0.02:
            # 跌停
            daily_ret = random.uniform(-0.10, -0.095)
        elif random.random() < 0.05:
            # 大阳线
            daily_ret = random.uniform(0.04, 0.08)
        elif random.random() < 0.04:
            # 大阴线
            daily_ret = random.uniform(-0.07, -0.03)

        open_price = price * (1 + random.gauss(0, 0.005))  # 开盘有小幅跳空
        close_price = price * (1 + daily_ret)

        # 日内高低点
        intra_range = abs(close_price - open_price) + price * random.uniform(0.005, volatility)
        high = max(open_price, close_price) + intra_range * random.uniform(0, 0.3)
        low = min(open_price, close_price) - intra_range * random.uniform(0, 0.3)
        low = max(low, 0.01)

        # 成交量
        base_volume = int(price * random.uniform(500000, 3000000))
        if abs(daily_ret) > 0.05:
            base_volume = int(base_volume * random.uniform(1.5, 3.0))  # 大波动放量
        volume = base_volume
        turnover = volume * price

        bars.append({
            "date": current_date.isoformat(),
            "open": round(open_price, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close_price, 2),
            "volume": volume,
            "turnover": round(turnover, 0),
            "turnover_rate": round(random.uniform(0.3, 5.0), 2),
        })

        price = close_price
        current_date += datetime.timedelta(days=1)

    random.seed()  # 重置随机种子

    # 注入信号种子：为特定股票的最后几天注入真实交易形态
    _inject_signal_seeds(symbol, bars, base_price)

    return bars


def _inject_signal_seeds(symbol: str, bars: list, base_price: float):
    """
    为指定的"信号种子"股票注入真实交易形态
    确保模拟数据能产生买入信号，用于验证系统流程
    """
    # 信号种子股票集合（选6只不同板块的代表）
    SIGNAL_SEEDS = {
        "600519.SS": "尾盘放量抢筹型",   # 贵州茅台
        "300750.SZ": "突破缺口型",       # 宁德时代
        "000858.SZ": "断板反包型",       # 五粮液
        "002594.SZ": "北向背离型",       # 比亚迪
        "688981.SS": "整数关口突破型",   # 中芯国际
        "601012.SS": "板块滞涨补涨型",   # 隆基绿能
    }

    if symbol not in SIGNAL_SEEDS:
        return

    if len(bars) < 15:
        return

    pattern = SIGNAL_SEEDS[symbol]
    last_idx = -1

    # 修改最后1-3天的数据，注入特定形态
    if pattern == "尾盘放量抢筹型":
        # 最近几天：稳步上涨 + 最后一天尾盘放量拉高 + 站上整数关口/均线
        for i in range(-7, 0):
            if i >= -len(bars):
                prev_close = bars[i-1]["close"] if i-1 >= -len(bars) else bars[i]["close"]
                if i >= -3:
                    # 近3天放量上涨
                    bars[i]["close"] = round(prev_close * (1.02 + random.uniform(0, 0.025)), 2)
                    bars[i]["open"] = round(bars[i]["close"] * 0.985, 2)
                    bars[i]["high"] = round(bars[i]["close"] * 1.025, 2)
                    bars[i]["low"] = round(bars[i]["open"] * 0.99, 2)
                    bars[i]["volume"] = int(bars[i]["volume"] * (3.0 if i == -1 else 2.0))
                    bars[i]["turnover"] = bars[i]["volume"] * bars[i]["close"]
                    bars[i]["turnover_rate"] = round(random.uniform(3.0, 8.0), 2)
                else:
                    # 前几天温和上涨
                    bars[i]["close"] = round(prev_close * (1.005 + random.uniform(0, 0.01)), 2)
                    bars[i]["open"] = round(bars[i]["close"] * 0.995, 2)
                    bars[i]["high"] = round(bars[i]["close"] * 1.015, 2)
                    bars[i]["low"] = round(bars[i]["open"] * 0.995, 2)
                    bars[i]["volume"] = int(bars[i]["volume"] * 1.5)
                    bars[i]["turnover"] = bars[i]["volume"] * bars[i]["close"]

    elif pattern == "突破缺口型":
        # 整理后向上跳空突破 + 放量 + 近整数关口
        for offset in range(7, 0, -1):
            i = -offset
            if i >= -len(bars):
                prev_close = bars[i-1]["close"] if i-1 >= -len(bars) else base_price
                prev_high = bars[i-1]["high"] if i-1 >= -len(bars) else prev_close
                if offset >= 4:
                    # 前期窄幅整理（缩量）
                    bars[i]["close"] = round(prev_close * (1 + random.uniform(-0.008, 0.008)), 2)
                    bars[i]["open"] = round(prev_close * (1 + random.uniform(-0.005, 0.005)), 2)
                    bars[i]["high"] = round(bars[i]["close"] * 1.012, 2)
                    bars[i]["low"] = round(bars[i]["close"] * 0.988, 2)
                elif offset == 3:
                    # 开始放量试探
                    bars[i]["close"] = round(prev_close * 1.015, 2)
                    bars[i]["volume"] = int(bars[i]["volume"] * 1.8)
                elif offset == 2:
                    # 向上跳空突破 + 放量
                    bars[i]["open"] = round(prev_high * 1.02, 2)
                    bars[i]["close"] = round(bars[i]["open"] * 1.04, 2)
                    bars[i]["high"] = round(bars[i]["close"] * 1.015, 2)
                    bars[i]["low"] = round(bars[i]["open"] * 0.992, 2)
                    bars[i]["volume"] = int(bars[i]["volume"] * 3.5)
                    bars[i]["turnover_rate"] = round(random.uniform(4.0, 10.0), 2)
                else:
                    # 最后1天：继续放量走高 + 高开
                    bars[i]["open"] = round(bars[i-1]["close"] * 1.015, 2)
                    bars[i]["close"] = round(bars[i]["open"] * 1.035, 2)
                    bars[i]["high"] = round(bars[i]["close"] * 1.02, 2)
                    bars[i]["low"] = round(bars[i]["open"] * 0.995, 2)
                    bars[i]["volume"] = int(bars[i]["volume"] * 2.0)
                    bars[i]["turnover_rate"] = round(random.uniform(3.0, 7.0), 2)
                bars[i]["turnover"] = bars[i]["volume"] * bars[i]["close"]

    elif pattern == "断板反包型":
        # 倒数第4天涨停，倒数第3天开板回落，倒数第2天企稳，最后1天放量反包突破
        if len(bars) >= 5:
            # 倒数第4天涨停
            bars[-4]["close"] = round(bars[-5]["close"] * 1.10, 2)
            bars[-4]["high"] = bars[-4]["close"]
            bars[-4]["open"] = round(bars[-5]["close"] * 1.03, 2)
            bars[-4]["low"] = bars[-4]["open"]
            bars[-4]["volume"] = int(bars[-4]["volume"] * 0.5)  # 缩量涨停
            bars[-4]["turnover"] = bars[-4]["volume"] * bars[-4]["close"]
            # 倒数第3天冲高回落（断板）
            bars[-3]["open"] = round(bars[-4]["close"] * 1.04, 2)
            bars[-3]["high"] = round(bars[-4]["close"] * 1.08, 2)
            bars[-3]["close"] = round(bars[-4]["close"] * 0.96, 2)
            bars[-3]["low"] = round(bars[-3]["close"] * 0.96, 2)
            bars[-3]["volume"] = int(bars[-3]["volume"] * 3.0)  # 放量分歧
            bars[-3]["turnover"] = bars[-3]["volume"] * bars[-3]["close"]
            # 倒数第2天企稳
            bars[-2]["open"] = round(bars[-3]["close"] * 1.00, 2)
            bars[-2]["close"] = round(bars[-3]["close"] * 1.01, 2)
            bars[-2]["high"] = round(bars[-2]["close"] * 1.02, 2)
            bars[-2]["low"] = round(bars[-2]["open"] * 0.995, 2)
            bars[-2]["volume"] = int(bars[-2]["volume"] * 1.2)
            bars[-2]["turnover"] = bars[-2]["volume"] * bars[-2]["close"]
            # 最后1天放量反包突破前高
            bars[-1]["open"] = round(bars[-2]["close"] * 1.015, 2)
            bars[-1]["close"] = round(max(bars[-4]["high"], bars[-3]["high"]) * 1.02, 2)
            bars[-1]["high"] = round(bars[-1]["close"] * 1.02, 2)
            bars[-1]["low"] = round(bars[-1]["open"] * 0.99, 2)
            bars[-1]["volume"] = int(bars[-1]["volume"] * 3.5)
            bars[-1]["turnover_rate"] = round(random.uniform(5.0, 12.0), 2)
            bars[-1]["turnover"] = bars[-1]["volume"] * bars[-1]["close"]

    elif pattern == "北向背离型":
        # 近7日股价横盘/小跌但成交量持续放大 + 最后一天高开高走（模拟资金暗中吸筹后启动）
        for offset in range(7, 0, -1):
            i = -offset
            if i >= -len(bars):
                prev_close = bars[i-1]["close"] if i-1 >= -len(bars) else bars[i]["close"]
                if offset >= 4:
                    # 前期缩量横盘
                    bars[i]["close"] = round(prev_close * (1 + random.uniform(-0.008, 0.003)), 2)
                    bars[i]["open"] = round(bars[i]["close"] * (1 + random.uniform(-0.005, 0.005)), 2)
                    bars[i]["high"] = round(max(bars[i]["open"], bars[i]["close"]) * 1.01, 2)
                    bars[i]["low"] = round(min(bars[i]["open"], bars[i]["close"]) * 0.99, 2)
                elif offset == 3:
                    # 开始放量小跌
                    bars[i]["close"] = round(prev_close * 0.995, 2)
                    bars[i]["open"] = round(prev_close * 1.002, 2)
                    bars[i]["volume"] = int(bars[i]["volume"] * 2.0)
                elif offset == 2:
                    # 继续放量下跌（背离信号）
                    bars[i]["close"] = round(prev_close * 0.992, 2)
                    bars[i]["open"] = round(prev_close * 1.003, 2)
                    bars[i]["volume"] = int(bars[i]["volume"] * 2.5)
                else:
                    # 最后1天高开高走（聪明钱开始拉升）
                    bars[i]["open"] = round(prev_close * 1.02, 2)
                    bars[i]["close"] = round(bars[i]["open"] * 1.035, 2)
                    bars[i]["high"] = round(bars[i]["close"] * 1.015, 2)
                    bars[i]["low"] = round(bars[i]["open"] * 0.995, 2)
                    bars[i]["volume"] = int(bars[i]["volume"] * 3.0)
                    bars[i]["turnover_rate"] = round(random.uniform(3.0, 6.0), 2)
                bars[i]["turnover"] = bars[i]["volume"] * bars[i]["close"]

    elif pattern == "整数关口突破型":
        # 逼近整数关口 + 最后1-2天放量突破
        int_level = round(base_price / 10) * 10  # 最近的整十价位
        if int_level == 0:
            int_level = 50
        for i in range(-5, 0):
            if i >= -len(bars):
                prev_close = bars[i-1]["close"] if i-1 >= -len(bars) else int_level * 0.9
                if i <= -4:
                    bars[i]["close"] = round(int_level * 0.85 + random.uniform(0, int_level * 0.02), 2)
                elif i == -3:
                    bars[i]["close"] = round(int_level * 0.92, 2)
                elif i == -2:
                    bars[i]["close"] = round(int_level * 0.97, 2)
                    bars[i]["volume"] = int(bars[i]["volume"] * 2.0)
                elif i == -1:
                    # 放量突破整数关口
                    bars[i]["open"] = round(int_level * 0.99, 2)
                    bars[i]["close"] = round(int_level * 1.05, 2)
                    bars[i]["high"] = round(bars[i]["close"] * 1.025, 2)
                    bars[i]["low"] = round(bars[i]["open"] * 0.992, 2)
                    bars[i]["volume"] = int(bars[i]["volume"] * 4.0)
                    bars[i]["turnover_rate"] = round(random.uniform(4.0, 8.0), 2)
                bars[i]["open"] = bars[i].get("open", round(prev_close * 1.005, 2))
                bars[i]["turnover"] = bars[i]["volume"] * bars[i]["close"]

    elif pattern == "板块滞涨补涨型":
        # 前期横盘整理（板块涨但个股滞涨），最后3天开始放量补涨
        for offset in range(8, 0, -1):
            i = -offset
            if i >= -len(bars):
                prev_close = bars[i-1]["close"] if i-1 >= -len(bars) else bars[i]["close"]
                if offset > 4:
                    # 长期横盘（板块涨但此股不动）
                    bars[i]["close"] = round(prev_close * (1 + random.uniform(-0.005, 0.005)), 2)
                    bars[i]["open"] = round(prev_close * (1 + random.uniform(-0.003, 0.003)), 2)
                    bars[i]["volume"] = int(bars[i]["volume"] * 0.8)
                elif offset == 4:
                    bars[i]["close"] = round(prev_close * 1.008, 2)
                    bars[i]["volume"] = int(bars[i]["volume"] * 1.5)
                elif offset == 3:
                    bars[i]["close"] = round(prev_close * 1.02, 2)
                    bars[i]["volume"] = int(bars[i]["volume"] * 2.0)
                elif offset == 2:
                    bars[i]["close"] = round(prev_close * 1.025, 2)
                    bars[i]["volume"] = int(bars[i]["volume"] * 2.5)
                else:
                    # 最后1天加速补涨
                    bars[i]["open"] = round(prev_close * 1.01, 2)
                    bars[i]["close"] = round(prev_close * 1.04, 2)
                    bars[i]["high"] = round(bars[i]["close"] * 1.02, 2)
                    bars[i]["low"] = round(bars[i]["open"] * 0.995, 2)
                    bars[i]["volume"] = int(bars[i]["volume"] * 3.5)
                    bars[i]["turnover_rate"] = round(random.uniform(5.0, 10.0), 2)
                bars[i]["high"] = bars[i].get("high", round(bars[i]["close"] * 1.02, 2))
                bars[i]["low"] = bars[i].get("low", round(bars[i]["open"] * 0.99, 2))
                bars[i]["turnover"] = bars[i]["volume"] * bars[i]["close"]


def _generate_minute_bars(daily_bars: List[dict], days: int = 2) -> List[dict]:
    """
    基于日线数据生成模拟分钟线（5分钟K线）
    每天78根（09:30-11:30=24根，13:00-15:00=24根，共48根）
    实际A股全天240分钟，5分钟=48根。但部分API用78根(含集合竞价)
    我们按标准48根来生成
    """
    bars = []
    for day_bar in daily_bars[-days:]:
        open_p = day_bar["open"]
        close_p = day_bar["close"]
        high_p = day_bar["high"]
        low_p = day_bar["low"]

        # 模拟日内走势
        if close_p > open_p:
            # 收阳：先下探再回升
            trajectory = "V"
            mid = low_p + (high_p - low_p) * 0.3
        else:
            # 收阴：先冲高再回落
            trajectory = "A"
            mid = high_p - (high_p - low_p) * 0.3

        n_candles = 48  # 5分钟K线
        price = open_p
        trend_direction = 1 if close_p > open_p else -1

        for i in range(n_candles):
            # 模拟盘中走势
            progress = i / n_candles

            if trajectory == "V":
                if progress < 0.3:
                    price += trend_direction * (low_p - open_p) / (0.3 * n_candles) + random.gauss(0, 0.02)
                elif progress > 0.7:
                    price += (close_p - low_p) / (0.3 * n_candles) + random.gauss(0, 0.02)
                else:
                    price += random.gauss(0, 0.015)
            else:
                if progress < 0.3:
                    price += (high_p - open_p) / (0.3 * n_candles) + random.gauss(0, 0.02)
                elif progress > 0.7:
                    price += trend_direction * (close_p - high_p) / (0.3 * n_candles) + random.gauss(0, 0.02)
                else:
                    price += random.gauss(0, 0.015)

            price = max(low_p * 0.98, min(high_p * 1.02, price))

            # 尾盘特征（最后6根）
            if i >= n_candles - 6:
                # 尾盘放量
                vol_mult = random.uniform(1.2, 2.5)
            else:
                vol_mult = random.uniform(0.6, 1.2)

            c_open = price + random.gauss(0, 0.01)
            c_close = price + random.gauss(0, 0.01)
            c_high = max(c_open, c_close) + abs(random.gauss(0, 0.01))
            c_low = min(c_open, c_close) - abs(random.gauss(0, 0.01))

            bars.append({
                "datetime": day_bar["date"] + f"T{9+i//6:02d}:{(i%6)*5:02d}:00",
                "open": round(c_open, 2),
                "high": round(c_high, 2),
                "low": round(c_low, 2),
                "close": round(c_close, 2),
                "volume": int(day_bar["volume"] / n_candles * vol_mult),
            })

    return bars


def simulate_universe_data(stocks: List[dict], days: int = 60) -> Dict[str, List[dict]]:
    """
    为全量股票池生成模拟日线数据

    Returns:
        {symbol: [daily_bars]}
    """
    sector_trends = {
        "白酒": 0.15, "银行": -0.05, "医药": -0.10, "证券": 0.05,
        "食品饮料": 0.08, "光伏": -0.15, "保险": 0.02, "电力": 0.10,
        "有色": 0.05, "建材": -0.08, "机械": 0.03, "煤炭": 0.12,
        "家电": 0.06, "安防": -0.05, "汽车": 0.10, "电子": 0.04,
        "面板": -0.03, "农牧": -0.08, "电池": 0.08, "工控": 0.06,
        "医疗": -0.05, "半导体": 0.12, "软件": 0.08, "手机": 0.03,
        "电池材料": 0.05, "石油": 0.10,
    }

    sector_volatility = {
        "白酒": 0.022, "银行": 0.012, "医药": 0.025, "证券": 0.028,
        "光伏": 0.030, "电池": 0.028, "半导体": 0.030, "软件": 0.028,
        "煤炭": 0.025, "有色": 0.024,
    }

    result = {}
    for stock in stocks:
        symbol = stock["symbol"]
        sector = stock.get("sector", "")
        base_price = 50.0

        # 根据股票名称估算大致价格区间
        name = stock.get("name", "")
        if "茅台" in name:
            base_price = 1800.0
        elif "宁德" in name:
            base_price = 200.0
        elif "比亚迪" in name:
            base_price = 250.0
        elif "迈瑞" in name:
            base_price = 280.0
        elif "金山" in name:
            base_price = 300.0
        elif "中芯" in name:
            base_price = 55.0
        elif any(w in name for w in ["工商", "中国银行", "农业银行"]):
            base_price = 5.0
        elif "银行" in name:
            base_price = 15.0
        elif "石油" in name:
            base_price = 8.0
        elif "神华" in name:
            base_price = 30.0
        elif any(w in name for w in ["亿纬", "阳光", "汇川"]):
            base_price = 80.0
        elif "爱尔" in name:
            base_price = 35.0
        elif "温氏" in name:
            base_price = 18.0
        elif "牧原" in name:
            base_price = 45.0

        trend = sector_trends.get(sector, 0.0)
        vol = sector_volatility.get(sector, 0.022)

        bars = _generate_daily_bars(symbol, days, base_price, trend, vol)
        result[symbol] = bars

    return result


def simulate_minute_data(stocks: List[dict], daily_data: Dict[str, List[dict]]) -> Dict[str, List[dict]]:
    """为股票池生成模拟分钟线数据"""
    result = {}
    for stock in stocks[:25]:  # 限制数量
        symbol = stock["symbol"]
        bars = daily_data.get(symbol, [])
        if bars:
            result[symbol] = _generate_minute_bars(bars, days=2)
    return result
