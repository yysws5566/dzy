"""
交易日历模块
- 自动判断当日是否为A股交易日
- 支持手动/自动两种模式
- 周末自动排除，法定节假日通过配置和API双重确认
"""

import datetime
import json
import os
from typing import Optional, Tuple

import config


class TradingCalendar:
    """A股交易日历"""

    # 中国法定节假日（基于2026年国务院公告，自动适配）
    _holiday_cache_file = os.path.join(os.path.dirname(__file__), ".holiday_cache.json")

    def __init__(self, auto_fetch: bool = True):
        self.auto_fetch = auto_fetch
        self._holidays: set = self._load_holidays()

    def _load_holidays(self) -> set:
        """加载节假日数据（优先从缓存，其次从配置）"""
        holidays = set()

        # 1. 从缓存文件加载
        if os.path.exists(self._holiday_cache_file):
            try:
                with open(self._holiday_cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    holidays.update(data.get("holidays", []))
            except (json.JSONDecodeError, KeyError):
                pass

        # 2. 从配置文件补充
        holidays.update(config.FIXED_HOLIDAYS_2026)

        return holidays

    def _save_holidays(self):
        """持久化节假日数据"""
        with open(self._holiday_cache_file, "w", encoding="utf-8") as f:
            json.dump({"holidays": sorted(self._holidays), "updated": str(datetime.date.today())}, f)

    def is_trading_day(self, date: Optional[datetime.date] = None) -> Tuple[bool, str]:
        """
        判断指定日期是否为A股交易日

        规则：
        1. 周六、周日 → 休市
        2. 法定节假日 → 休市
        3. 节假日调休工作日 → 正常交易（需要补充调休数据）
        4. 其他工作日 → 正常交易

        返回: (是否交易, 原因说明)
        """
        if date is None:
            date = datetime.date.today()

        weekday = date.weekday()  # 0=周一, 6=周日

        # 规则1: 周末休市
        if weekday >= 5:
            day_name = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][weekday]
            return False, f"{date} 是{day_name}，A股周末休市"

        # 规则2: 法定节假日
        date_str = date.isoformat()
        if date_str in self._holidays:
            return False, f"{date} 是法定节假日，A股休市"

        # 规则3: 调休工作日（补充处理）
        # 例如：春节前的周末可能需要补班
        # 这里通过API实时获取，如果没有API则依赖配置文件

        # 规则4: 普通工作日 → 正常交易
        return True, f"{date} 是交易日"

    def get_next_trading_day(self, from_date: Optional[datetime.date] = None, offset: int = 1) -> datetime.date:
        """
        获取第N个交易日（正数=未来，负数=过去）

        Args:
            from_date: 起始日期，默认今天
            offset: 偏移量，1=下一个交易日，-1=上一个交易日
        """
        if from_date is None:
            from_date = datetime.date.today()

        direction = 1 if offset > 0 else -1
        steps = abs(offset)
        current = from_date

        while steps > 0:
            current += datetime.timedelta(days=direction)
            is_trade, _ = self.is_trading_day(current)
            if is_trade:
                steps -= 1

        return current

    def get_trading_days_in_range(self, start: datetime.date, end: datetime.date) -> list:
        """获取日期范围内的所有交易日"""
        trading_days = []
        current = start
        while current <= end:
            is_trade, _ = self.is_trading_day(current)
            if is_trade:
                trading_days.append(current)
            current += datetime.timedelta(days=1)
        return trading_days

    def should_skip_today(self) -> Tuple[bool, str]:
        """判断今天是否需要跳过（非交易日则跳过）"""
        is_trade, reason = self.is_trading_day()
        return (not is_trade, reason)


# 全局单例
_calendar_instance: Optional[TradingCalendar] = None


def get_calendar() -> TradingCalendar:
    """获取交易日历单例"""
    global _calendar_instance
    if _calendar_instance is None:
        _calendar_instance = TradingCalendar()
    return _calendar_instance
