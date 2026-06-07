# -*- coding: utf-8 -*-
"""
A股统一金融数据抓取模块

数据源优先级：
  1. AKShare（主源，数据最全，东方财富底层）
  2. 腾讯财经（备用，实时行情+分时）
  3. 新浪财经（备用，实时行情+K线）

设计原则：
  - 主源失败自动降级到备用源
  - 统一返回格式，调用方无需关心数据来源
  - 内置重试、延迟、异常处理
  - Windows GBK 编码兼容

用法：
  from data_fetcher import MarketData

  md = MarketData()
  df = md.get_all_stocks()          # 全市场实时行情
  kline = md.get_kline("600000", 30) # 个股日K线
  idx = md.get_index_daily("000001", 30)  # 指数日线
  zt = md.get_zt_pool("20260426")    # 涨停股池
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta

# Windows 编码兼容
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests
import pandas as pd


# ========== 通用配置 ==========
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn",
    "Accept": "application/json,text/html,*/*",
}

API_RETRY = 3       # 重试次数
API_DELAY = 0.3     # API调用间隔（秒）
REQUEST_TIMEOUT = 15


# ========== 工具函数 ==========
def _safe_request(url, params=None, timeout=REQUEST_TIMEOUT, headers=None):
    """带重试的 HTTP GET 请求"""
    hdrs = headers or REQUEST_HEADERS
    for attempt in range(API_RETRY):
        try:
            r = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            if attempt < API_RETRY - 1:
                time.sleep(API_DELAY * (attempt + 1))
            else:
                return None


def _to_numeric(series):
    """安全转换为数值"""
    return pd.to_numeric(series, errors="coerce")


def _code_to_sina_symbol(code):
    """
    股票代码转新浪symbol格式
    600000 -> sh600000, 000001 -> sz000001, 300001 -> sz300001
    注意：指数代码需特殊处理（上证指数000001用sh，不是sz）
    """
    code = str(code).strip()
    # 指数代码特殊处理
    if _is_index_code(code):
        if code.startswith("399") or code.startswith("9"):
            return f"sz{code}"
        else:  # 上证系列指数：000001, 000016, 000300等
            return f"sh{code}"
    # 个股代码
    if code.startswith("6"):
        return f"sh{code}"
    elif code.startswith(("0", "3")):
        return f"sz{code}"
    return code


def _is_index_code(code):
    """判断是否为指数代码（上证000xxx、深证399xxx、中证9xxxxx）"""
    code = str(code).strip()
    return (code.startswith("000") and code in ("000001", "000002", "000003", "000016", "000300", "000905", "000852")) \
        or code.startswith("399") \
        or code.startswith("9")


def _code_to_tencent_symbol(code):
    """
    股票代码转腾讯symbol格式
    600000 -> sh600000, 000001 -> sz000001
    """
    return _code_to_sina_symbol(code)


def get_trade_date():
    """获取最近的交易日期（当天或前一个交易日）"""
    today = datetime.now()
    if today.weekday() == 5:  # 周六
        trade_date = today - timedelta(days=1)
    elif today.weekday() == 6:  # 周日
        trade_date = today - timedelta(days=2)
    else:
        trade_date = today
    return trade_date.strftime("%Y%m%d")


def get_previous_trade_date(trade_date, offset=1):
    """获取前N个交易日的日期（跳过周末）"""
    dt = datetime.strptime(trade_date, "%Y%m%d")
    count = 0
    while count < offset:
        dt -= timedelta(days=1)
        if dt.weekday() < 5:
            count += 1
    return dt.strftime("%Y%m%d")


# ================================================================
# 数据源1：AKShare（主源）
# ================================================================
class AKShareSource:
    """AKShare 数据源 — 数据最全，东方财富底层"""

    name = "AKShare"
    _akshare_available = None

    @classmethod
    def is_available(cls):
        """检查 AKShare 是否已安装"""
        if cls._akshare_available is None:
            try:
                import akshare as ak
                cls._akshare_available = True
                cls._ak = ak
            except ImportError:
                cls._akshare_available = False
                cls._ak = None
        return cls._akshare_available

    @classmethod
    def safe_call(cls, func, *args, **kwargs):
        """带重试的 AKShare API 调用"""
        for attempt in range(API_RETRY):
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                if attempt < API_RETRY - 1:
                    wait = API_DELAY * (attempt + 1)
                    print(f"    [AKShare 重试 {attempt+1}/{API_RETRY}] {func.__name__}: {e}")
                    time.sleep(wait)
                else:
                    print(f"    [AKShare 失败] {func.__name__}: {e}")
                    return None

    @classmethod
    def get_all_stocks(cls):
        """
        获取A股全部实时行情
        返回 DataFrame，统一列名：code, name, close, pct_chg, volume, amount,
                                    turnover, vol_ratio, circ_mv, total_mv, high, low, open
        """
        if not cls.is_available():
            return pd.DataFrame()

        ak = cls._ak

        # 尝试全市场实时行情
        df = cls.safe_call(ak.stock_zh_a_spot_em)
        if df is not None and not df.empty:
            return cls._normalize_spot(df)

        # 降级：分别获取沪深
        df_sh = cls.safe_call(ak.stock_sh_a_spot_em)
        df_sz = cls.safe_call(ak.stock_sz_a_spot_em)
        if df_sh is not None and df_sz is not None:
            return cls._normalize_spot(pd.concat([df_sh, df_sz], ignore_index=True))

        return pd.DataFrame()

    @classmethod
    def _normalize_spot(cls, df):
        """统一 AKShare 实时行情列名"""
        col_map = {}
        for col in df.columns:
            if "代码" in col:
                col_map[col] = "code"
            elif "名称" in col:
                col_map[col] = "name"
            elif "最新价" in col:
                col_map[col] = "close"
            elif "涨跌幅" in col and "5分钟" not in col and "60日" not in col and "年初" not in col:
                col_map[col] = "pct_chg"
            elif "涨跌额" in col:
                col_map[col] = "chg"
            elif "成交量" in col:
                col_map[col] = "volume"
            elif "成交额" in col:
                col_map[col] = "amount"
            elif "振幅" in col:
                col_map[col] = "amplitude"
            elif "换手率" in col:
                col_map[col] = "turnover"
            elif "量比" in col:
                col_map[col] = "vol_ratio"
            elif "流通市值" in col:
                col_map[col] = "circ_mv"
            elif "总市值" in col:
                col_map[col] = "total_mv"
            elif "最高" in col:
                col_map[col] = "high"
            elif "最低" in col:
                col_map[col] = "low"
            elif "开盘" in col:
                col_map[col] = "open"
        df = df.rename(columns=col_map)

        # 统一数值类型
        for col in ["close", "pct_chg", "chg", "volume", "amount", "amplitude",
                    "turnover", "vol_ratio", "circ_mv", "total_mv", "high", "low", "open"]:
            if col in df.columns:
                df[col] = _to_numeric(df[col])
        return df

    @classmethod
    def get_kline(cls, code, days=30, start_date=None, end_date=None):
        """
        获取个股日K线
        code: 6位股票代码
        days: 获取最近N个交易日
        返回 DataFrame，列：date, open, high, low, close, volume, amount, pct_chg, turnover, amplitude
        """
        if not cls.is_available():
            return pd.DataFrame()

        if end_date is None:
            end_date = get_trade_date()
        if start_date is None:
            start_date = get_previous_trade_date(end_date, days + 10)

        ak = cls._ak
        df = cls.safe_call(
            ak.stock_zh_a_hist,
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust=""
        )

        if df is None or df.empty:
            return pd.DataFrame()

        # AKShare 列名：日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
        kline_map = {
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "振幅": "amplitude", "涨跌幅": "pct_chg",
            "涨跌额": "chg", "换手率": "turnover",
        }
        df = df.rename(columns={k: v for k, v in kline_map.items() if k in df.columns})

        for col in ["open", "close", "high", "low", "volume", "amount",
                    "amplitude", "pct_chg", "chg", "turnover"]:
            if col in df.columns:
                df[col] = _to_numeric(df[col])

        # 取最近N天
        if len(df) > days:
            df = df.tail(days)

        return df.reset_index(drop=True)

    @classmethod
    def get_index_daily(cls, code, days=30):
        """
        获取指数日线数据
        code: 指数代码（如 000001=上证指数, 399006=创业板指）
        返回 DataFrame，列：date, open, high, low, close, volume, amount, pct_chg
        """
        if not cls.is_available():
            return pd.DataFrame()

        ak = cls._ak
        end_date = get_trade_date()
        start_date = get_previous_trade_date(end_date, days + 10)

        df = cls.safe_call(
            ak.stock_zh_index_daily_em,
            symbol=code,
            start_date=start_date,
            end_date=end_date,
        )

        if df is None or df.empty:
            return pd.DataFrame()

        idx_map = {
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount",
        }
        df = df.rename(columns={k: v for k, v in idx_map.items() if k in df.columns})

        for col in ["open", "close", "high", "low", "volume", "amount"]:
            if col in df.columns:
                df[col] = _to_numeric(df[col])

        # 计算涨跌幅
        if "close" in df.columns and len(df) > 1:
            df["pct_chg"] = df["close"].pct_change() * 100

        if len(df) > days:
            df = df.tail(days)

        return df.reset_index(drop=True)

    @classmethod
    def get_zt_pool(cls, date_str):
        """
        获取指定日期的涨停股池
        返回 DataFrame，列：code, name, close, pct_chg, ...
        """
        if not cls.is_available():
            return pd.DataFrame()

        ak = cls._ak
        df = cls.safe_call(ak.stock_zt_pool_em, date=date_str)
        if df is not None and not df.empty:
            return df
        return pd.DataFrame()

    @classmethod
    def get_zt_pool_previous(cls, date_str):
        """获取昨日涨停股今日表现"""
        if not cls.is_available():
            return pd.DataFrame()

        ak = cls._ak
        df = cls.safe_call(ak.stock_zt_pool_previous_em, date=date_str)
        if df is not None and not df.empty:
            return df
        return pd.DataFrame()

    @classmethod
    def get_sector_rank(cls):
        """获取当日行业板块涨幅排名"""
        if not cls.is_available():
            return pd.DataFrame()

        ak = cls._ak
        df = cls.safe_call(ak.stock_board_industry_name_em)
        if df is not None and not df.empty:
            return df
        return pd.DataFrame()

    @classmethod
    def get_stock_in_sector(cls, sector_name):
        """获取指定板块的成分股"""
        if not cls.is_available():
            return pd.DataFrame()

        ak = cls._ak
        df = cls.safe_call(ak.stock_board_industry_cons_em, symbol=sector_name)
        if df is not None and not df.empty:
            return df
        return pd.DataFrame()


# ================================================================
# 数据源2：腾讯财经（备用）
# ================================================================
class TencentSource:
    """腾讯财经数据源 — 实时行情、分时数据"""

    name = "腾讯财经"

    # 腾讯实时行情接口（支持批量查询，逗号分隔）
    TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="

    @classmethod
    def get_all_stocks(cls):
        """
        腾讯接口不支持一次获取全市场，仅作备用
        返回统一格式的 DataFrame
        """
        # 腾讯接口只适合单只或少量查询，不适合全量
        print("    [提示] 腾讯接口不支持全量行情，跳过")
        return pd.DataFrame()

    @classmethod
    def get_stock_quote(cls, code):
        """
        获取单只股票实时行情
        code: 6位代码
        返回 dict
        """
        symbol = _code_to_tencent_symbol(code)
        url = f"{cls.TENCENT_QUOTE_URL}{symbol}"
        r = _safe_request(url)
        if r is None:
            return {}

        text = r.text.strip()
        if not text or "~" not in text:
            return {}

        # 腾讯行情字段以 ~ 分隔
        fields = text.split("~")
        if len(fields) < 50:
            return {}

        try:
            return {
                "code": fields[2],
                "name": fields[1],
                "close": float(fields[3]),
                "pct_chg": round(float(fields[32]) * 100, 2) if fields[32] else 0,
                "volume": int(fields[6]),
                "amount": float(fields[37]) if fields[37] else 0,
                "high": float(fields[33]) if fields[33] else 0,
                "low": float(fields[34]) if fields[34] else 0,
                "open": float(fields[5]) if fields[5] else 0,
            }
        except (ValueError, IndexError):
            return {}


# ================================================================
# 数据源3：新浪财经（备用）
# ================================================================
class SinaSource:
    """新浪财经数据源 — 全市场行情、日K线"""

    name = "新浪财经"

    SINA_SPOT_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    PAGE_SIZE = 80

    @classmethod
    def get_all_stocks(cls):
        """
        获取沪深A股全部实时行情（分页）
        返回统一列名的 DataFrame
        """
        all_stocks = []
        page = 1

        while True:
            params = {
                "page": page,
                "num": cls.PAGE_SIZE,
                "sort": "changepercent",
                "asc": 0,
                "node": "hs_a",
                "symbol": "",
                "_s_r_a": "auto",
            }
            r = _safe_request(cls.SINA_SPOT_URL, params)
            if r is None:
                break

            try:
                data = r.json()
            except Exception:
                break

            if not data or not isinstance(data, list):
                break

            all_stocks.extend(data)

            if len(data) < cls.PAGE_SIZE:
                break

            page += 1
            time.sleep(API_DELAY)

        if not all_stocks:
            return pd.DataFrame()

        df = pd.DataFrame(all_stocks)
        return cls._normalize_spot(df)

    @classmethod
    def _normalize_spot(cls, df):
        """统一新浪实时行情列名"""
        col_map = {
            "symbol": "sina_symbol",
            "code": "code",
            "name": "name",
            "trade": "close",
            "changepercent": "pct_chg",
            "change": "chg",
            "volume": "volume",
            "amount": "amount",
            "amplitude": "amplitude",
            "turnoverratio": "turnover",
            "nmc": "circ_mv",   # 新浪单位：万元
            "mktcap": "total_mv",  # 新浪单位：万元
            "high": "high",
            "low": "low",
            "open": "open",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        for col in ["close", "pct_chg", "chg", "volume", "amount", "amplitude",
                    "turnover", "circ_mv", "total_mv", "high", "low", "open"]:
            if col in df.columns:
                df[col] = _to_numeric(df[col])

        # 新浪市值单位为万元，转为元与AKShare统一
        if "circ_mv" in df.columns:
            df["circ_mv"] = df["circ_mv"] * 10000
        if "total_mv" in df.columns:
            df["total_mv"] = df["total_mv"] * 10000

        return df

    @classmethod
    def get_kline(cls, code, days=30):
        """
        获取个股/指数日K线（新浪）
        code: 6位股票/指数代码
        返回统一列名的 DataFrame
        """
        symbol = _code_to_sina_symbol(code)

        # 新浪指数K线：上证用 sh000001 格式但数据异常
        # 改用腾讯接口获取指数K线（数据更准确）
        if _is_index_code(code):
            return cls._get_index_kline_tencent(code, days)

        params = {
            "symbol": symbol,
            "scale": 240,
            "ma": "no",
            "datalen": days,
        }
        r = _safe_request(cls.SINA_KLINE_URL, params)
        if r is None:
            return pd.DataFrame()

        try:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                df = pd.DataFrame(data)
                kline_map = {
                    "day": "date", "open": "open", "high": "high",
                    "low": "low", "close": "close", "volume": "volume",
                }
                df = df.rename(columns={k: v for k, v in kline_map.items() if k in df.columns})
                for col in ["open", "close", "high", "low", "volume"]:
                    if col in df.columns:
                        df[col] = _to_numeric(df[col])
                return df
        except Exception:
            pass

        return pd.DataFrame()

    @classmethod
    def _get_index_kline_tencent(cls, code, days=30):
        """
        通过腾讯接口获取指数日K线（备用方案，新浪指数数据不准）
        返回统一格式的 DataFrame
        """
        symbol = _code_to_tencent_symbol(code)
        # 腾讯日K线接口（非复权，参数更简洁）
        url = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
        params = {
            "_var": "kline_day",
            "param": f"{symbol},day,,,30,",
        }
        r = _safe_request(url, params, headers={
            "User-Agent": REQUEST_HEADERS["User-Agent"],
            "Referer": "https://gu.qq.com/",
        })
        if r is None:
            return pd.DataFrame()

        try:
            text = r.text.strip()
            if text.startswith("kline_day="):
                text = text[len("kline_day="):]
            data = json.loads(text)
            day_data = data.get("data", {}).get(symbol, {}).get("day", [])
            if day_data and isinstance(day_data, list) and len(day_data) > 0:
                records = []
                for item in day_data:
                    # 腾讯格式: ["2026-04-24", "3400.12", "3415.56", "3395.00", "3408.88", "123456789"]
                    if len(item) >= 6:
                        records.append({
                            "date": item[0],
                            "open": float(item[1]),
                            "close": float(item[2]),
                            "high": float(item[3]),
                            "low": float(item[4]),
                            "volume": float(item[5]),
                        })
                df = pd.DataFrame(records)
                if len(df) > days:
                    df = df.tail(days)
                return df.reset_index(drop=True)
        except Exception:
            pass

        return pd.DataFrame()


# ================================================================
# 统一数据接口（主入口）
# ================================================================
class MarketData:
    """
    A股统一金融数据接口
    自动在 AKShare（主源）、腾讯/新浪（备用）之间切换
    """

    def __init__(self, verbose=False):
        """
        verbose: 是否打印数据源切换信息
        """
        self.verbose = verbose
        self._last_source = None

    def _log(self, msg):
        if self.verbose:
            print(f"    [MarketData] {msg}")

    def get_all_stocks(self):
        """
        获取A股全市场实时行情（统一格式）
        优先 AKShare，失败降级到新浪
        """
        # 主源：AKShare
        if AKShareSource.is_available():
            self._log("使用 AKShare 获取全市场行情")
            df = AKShareSource.get_all_stocks()
            if df is not None and not df.empty:
                self._last_source = "AKShare"
                return df
            self._log("AKShare 获取失败，尝试备用源")

        # 备用：新浪
        self._log("使用 新浪财经 获取全市场行情")
        df = SinaSource.get_all_stocks()
        if df is not None and not df.empty:
            self._last_source = "新浪财经"
            return df

        self._log("所有数据源均失败")
        self._last_source = None
        return pd.DataFrame()

    def get_kline(self, code, days=30, start_date=None, end_date=None):
        """
        获取个股日K线（统一格式）
        code: 6位股票代码
        days: 最近N个交易日
        优先 AKShare，失败降级到新浪
        """
        # 主源：AKShare
        if AKShareSource.is_available():
            self._log(f"使用 AKShare 获取 {code} K线")
            df = AKShareSource.get_kline(code, days, start_date, end_date)
            if df is not None and not df.empty:
                self._last_source = "AKShare"
                return df
            self._log("AKShare 获取失败，尝试备用源")

        # 备用：新浪
        self._log(f"使用 新浪财经 获取 {code} K线")
        df = SinaSource.get_kline(code, days)
        if df is not None and not df.empty:
            self._last_source = "新浪财经"
            return df

        self._log(f"{code} K线获取失败")
        return pd.DataFrame()

    def get_index_daily(self, code, days=30):
        """
        获取指数日线数据
        code: 指数代码（000001=上证, 399006=创业板）
        仅 AKShare 支持，新浪备用做K线格式兼容
        """
        if AKShareSource.is_available():
            self._log(f"使用 AKShare 获取指数 {code} 日线")
            df = AKShareSource.get_index_daily(code, days)
            if df is not None and not df.empty:
                self._last_source = "AKShare"
                return df
            self._log("AKShare 获取失败，尝试备用源")

        # 备用：腾讯指数K线接口（新浪指数数据不准）
        self._log(f"使用 腾讯财经 获取指数 {code} K线")
        df = SinaSource.get_kline(code, days)  # 内部自动识别指数代码走腾讯
        if df is not None and not df.empty:
            self._last_source = "新浪财经"
            return df

        return pd.DataFrame()

    def get_zt_pool(self, date_str):
        """获取涨停股池（仅 AKShare 支持）"""
        if AKShareSource.is_available():
            df = AKShareSource.get_zt_pool(date_str)
            if df is not None and not df.empty:
                self._last_source = "AKShare"
                return df
        return pd.DataFrame()

    def get_zt_pool_previous(self, date_str):
        """获取昨日涨停今日表现（仅 AKShare 支持）"""
        if AKShareSource.is_available():
            df = AKShareSource.get_zt_pool_previous(date_str)
            if df is not None and not df.empty:
                self._last_source = "AKShare"
                return df
        return pd.DataFrame()

    def get_sector_rank(self):
        """获取行业板块涨幅排名（仅 AKShare 支持）"""
        if AKShareSource.is_available():
            df = AKShareSource.get_sector_rank()
            if df is not None and not df.empty:
                self._last_source = "AKShare"
                return df
        return pd.DataFrame()

    def get_stock_in_sector(self, sector_name):
        """获取板块成分股（仅 AKShare 支持）"""
        if AKShareSource.is_available():
            df = AKShareSource.get_stock_in_sector(sector_name)
            if df is not None and not df.empty:
                self._last_source = "AKShare"
                return df
        return pd.DataFrame()

    def check_market_env(self):
        """
        检查大盘环境（用于自动化策略前置过滤）
        返回 dict: {
            "pass": bool,
            "sh": {"close": float, "ma20": float, "pct_chg": float},
            "cyb": {"close": float, "ma20": float, "pct_chg": float},
            "reasons": [str]  # 不满足的原因列表
        }
        """
        result = {"pass": True, "reasons": []}

        # 获取上证指数数据
        sh_df = self.get_index_daily("000001", 25)
        cyb_df = self.get_index_daily("399006", 25)

        if sh_df.empty:
            result["pass"] = False
            result["reasons"].append("无法获取上证指数数据")
            return result

        # 计算上证 MA20 和当日涨跌幅
        sh_closes = sh_df["close"].dropna().tolist()
        if len(sh_closes) < 20:
            result["pass"] = False
            result["reasons"].append("上证指数数据不足20天")
            return result

        sh_close = sh_closes[-1]
        sh_ma20 = sum(sh_closes[-20:]) / 20
        sh_pct = ((sh_closes[-1] / sh_closes[-2]) - 1) * 100 if len(sh_closes) > 1 else 0

        result["sh"] = {"close": round(sh_close, 2), "ma20": round(sh_ma20, 2), "pct_chg": round(sh_pct, 2)}

        if sh_close <= sh_ma20:
            result["pass"] = False
            result["reasons"].append(f"上证指数({sh_close:.2f})低于MA20({sh_ma20:.2f})")

        if sh_pct <= -1:
            result["pass"] = False
            result["reasons"].append(f"上证指数当日跌幅({sh_pct:.2f}%)超过-1%")

        # 创业板
        if not cyb_df.empty:
            cyb_closes = cyb_df["close"].dropna().tolist()
            if len(cyb_closes) >= 20:
                cyb_close = cyb_closes[-1]
                cyb_ma20 = sum(cyb_closes[-20:]) / 20
                cyb_pct = ((cyb_closes[-1] / cyb_closes[-2]) - 1) * 100 if len(cyb_closes) > 1 else 0

                result["cyb"] = {"close": round(cyb_close, 2), "ma20": round(cyb_ma20, 2), "pct_chg": round(cyb_pct, 2)}

                if cyb_close <= cyb_ma20:
                    result["pass"] = False
                    result["reasons"].append(f"创业板指({cyb_close:.2f})低于MA20({cyb_ma20:.2f})")
        else:
            result["pass"] = False
            result["reasons"].append("无法获取创业板指数据")

        return result


# ========== 独立测试 ==========
if __name__ == "__main__":
    print("=" * 60)
    print("A股统一金融数据模块 测试")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    md = MarketData(verbose=True)

    # 测试1: 数据源可用性
    print("\n--- 数据源检测 ---")
    print(f"  AKShare: {'✅ 可用' if AKShareSource.is_available() else '❌ 未安装'}")
    print(f"  新浪财经: ✅ (HTTP接口)")
    print(f"  腾讯财经: ✅ (HTTP接口)")

    # 测试2: 大盘环境检查
    print("\n--- 大盘环境检查 ---")
    env = md.check_market_env()
    if env["pass"]:
        print("  ✅ 大盘环境满足")
    else:
        print(f"  ⛔ 大盘环境不满足: {'; '.join(env['reasons'])}")
    if "sh" in env:
        s = env["sh"]
        print(f"  上证: {s['close']} / MA20={s['ma20']} / 涨跌={s['pct_chg']}%")
    if "cyb" in env:
        c = env["cyb"]
        print(f"  创业板: {c['close']} / MA20={c['ma20']} / 涨跌={c['pct_chg']}%")

    # 测试3: 全市场行情
    print("\n--- 全市场行情 ---")
    df = md.get_all_stocks()
    if not df.empty:
        print(f"  ✅ 获取 {len(df)} 只股票 (数据源: {md._last_source})")
        if "code" in df.columns:
            print(f"  列名: {list(df.columns)}")
            print(f"  示例: {df.iloc[0].to_dict()}")
    else:
        print("  ❌ 获取失败")

    # 测试4: K线
    print("\n--- 个股K线 (600519) ---")
    kline = md.get_kline("600519", 10)
    if not kline.empty:
        print(f"  ✅ 获取 {len(kline)} 条 (数据源: {md._last_source})")
        print(f"  最新: {kline.iloc[-1].to_dict()}")
    else:
        print("  ❌ 获取失败")

    # 测试5: 指数日线
    print("\n--- 指数日线 ---")
    idx = md.get_index_daily("000001", 5)
    if not idx.empty:
        print(f"  ✅ 上证指数 {len(idx)} 条 (数据源: {md._last_source})")
        print(f"  最新: {idx.iloc[-1].to_dict()}")
    else:
        print("  ❌ 获取失败")

    print("\n" + "=" * 60)
    print("测试完成")
