"""读取 AKShare 场外基金净值及分红折算记录."""

from math import isfinite
import re

import pandas as pd


def _dates(frame, column):
    dates = pd.to_datetime(frame[column], format="%Y-%m-%d")
    if dates.isna().any():
        raise ValueError(f"{column} 缺失")
    return dates.dt.date


def _nav_values(frame, column):
    if frame.empty:
        return pd.Series(dtype=float)
    dates = _dates(frame, "净值日期")
    if dates.duplicated().any():
        raise ValueError("AKShare 返回重复净值日")
    values = pd.to_numeric(frame[column], errors="raise")
    if any(not isfinite(v) or v <= 0 for v in values.dropna()):
        raise ValueError(f"AKShare {column}存在无效值")
    return pd.Series(values.to_numpy(dtype=float), index=dates).dropna().sort_index()


def _dividends(frame):
    if frame.empty:
        return {}
    columns = [name for name in frame if re.fullmatch(r"每(?:\d+)?份分红", name)]
    if len(columns) != 1:
        raise ValueError("AKShare 分红表缺少明确的每份金额列")
    column = columns[0]
    header_shares = int(re.fullmatch(r"每(\d+)?份分红", column)[1] or 1)
    cash = {}
    for day, value in zip(_dates(frame, "除息日"), frame[column]):
        match = re.fullmatch(r"每\s*(\d+)?\s*份\s*派现金\s*(\d+(?:\.\d+)?)\s*元", str(value).strip())
        if not match or match[1] == "0" or day in cash:
            raise ValueError(f"无法确认 {day} 的每份分红金额")
        shares = int(match[1] or 1)
        if header_shares != shares:
            raise ValueError(f"{day} 的分红单位与表头不一致")
        cash[day] = float(match[2]) / shares
    return cash


def fetch_nav(client, code, *, before_request=None):
    """一次复核只请求同一份额的四个指标, 保留接口日期."""
    def query(indicator):
        if before_request is not None:
            before_request()
        return client.fund_open_fund_info_em(symbol=code.removesuffix(".OF"), indicator=indicator)

    unit = _nav_values(query("单位净值走势"), "单位净值")
    accum = _nav_values(query("累计净值走势"), "累计净值")
    cash = _dividends(query("分红送配详情"))
    splits = query("拆分详情")
    return unit, accum, cash, set() if splits.empty else set(_dates(splits, "拆分折算日"))
