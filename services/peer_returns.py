"""固定周期涨幅与本地同类排名, 不依赖页面或具体数据库."""

from datetime import date
from math import ceil

import pandas as pd


QUARTILES = ("优秀", "良好", "一般", "不佳")
MAX_BOUNDARY_GAP = pd.Timedelta(days=14)


def _periods(end: pd.Timestamp) -> dict[str, list[dict]]:
    """季度和年度包含当前未结束周期, 周期以基金最新净值日为基准."""
    def period(label, start, stop, partial=False):
        return {"label": label, "start_cutoff": start.normalize(), "end_cutoff": stop.normalize(),
                "partial": partial}

    previous_year = pd.Timestamp(end.year - 1, 12, 31)
    stages = [period("近一周", end - pd.Timedelta(days=7), end)]
    stages.extend(period(label, end - pd.DateOffset(months=months), end) for label, months in
                  (("近一月", 1), ("近三月", 3), ("近半年", 6)))
    stages.append(period("今年来", previous_year, end))
    stages.extend(period(label, end - pd.DateOffset(years=years), end)
                  for label, years in (("近一年", 1), ("近三年", 3)))
    quarters = []
    for offset in range(8):
        quarter = end.to_period("Q") - offset
        stop = min(end, quarter.end_time.normalize())
        quarters.append(period(f"{quarter.year}年Q{quarter.quarter}",
                               quarter.start_time - pd.Timedelta(days=1), stop,
                               end < quarter.end_time.normalize()))
    years = [period(f"{year}年", pd.Timestamp(year - 1, 12, 31),
                    min(end, pd.Timestamp(year, 12, 31)), end < pd.Timestamp(year, 12, 31))
             for year in range(end.year, end.year - 8, -1)]
    return {"stages": stages, "quarters": quarters, "years": years}


def _anchor(dates: pd.DatetimeIndex, cutoff: pd.Timestamp) -> pd.Timestamp | None:
    """休市日取截止日前最近披露日; 超过 14 天不当作有效边界."""
    position = dates.searchsorted(cutoff, side="right") - 1
    if position < 0 or cutoff - dates[position] > MAX_BOUNDARY_GAP:
        return None
    return dates[position]


def _valid(values):
    return values.where(values.gt(0) & values.lt(float("inf")))


def _calculate_period(item, left, right, prices, peers, has_category):
    """同一周期先校验本基金, 再使用相同日期的有效同类样本计算排名."""
    result = {**item, "start_date": None if left is None else left.date().isoformat(),
              "end_date": None if right is None else right.date().isoformat(),
              "fund_return": None, "peer_average": None, "rank": None, "sample_count": 0,
              "quartile": None, "quartile_label": None, "reason": None}
    if left is None or right is None or left >= right:
        result["reason"] = "历史不足或周期边界缺少净值"
        return result
    own = prices.loc[[left, right]]
    if own.isna().any():
        result["reason"] = "周期边界缺少有效正复权净值"
        return result
    value = float(own.iloc[1] / own.iloc[0] - 1)
    if not -float("inf") < value < float("inf"):
        result["reason"] = "周期涨幅不是有限数值"
        return result
    result["fund_return"] = value
    if not has_category:
        result["reason"] = "没有二级分类, 请先更新基金分类"
        return result
    if left not in peers.columns or right not in peers.columns:
        result["reason"] = "同类周期边界数据不足"
        return result
    returns = (peers[right] / peers[left] - 1).replace([float("inf"), -float("inf")], float("nan")).dropna()
    result["sample_count"] = len(returns)
    if len(returns) < 2 or prices.name not in returns.index:
        result["reason"] = "同类有效样本不足两只或本基金不在样本中"
        return result
    result["peer_average"] = float(returns.mean())
    result["rank"] = 1 + int(returns.round(12).gt(round(value, 12)).sum())
    if len(returns) >= 4:
        result["quartile"] = ceil(result["rank"] / len(returns) * 4)
        result["quartile_label"] = QUARTILES[result["quartile"] - 1]
    else:
        result["reason"] = "同类有效样本不足四只, 不提供四分位"
    return result


def get_peer_returns(repository, code: str, as_of: date) -> dict:
    """使用最新分类快照与统一净值日期, 每个周期独立确定有效样本.

    涨幅为末日复权净值 / 基期复权净值 - 1, 不要求区间内部日频连续.
    同类平均为含本基金的有效份额等权平均; 至少两只才提供比较结果.
    排名按涨幅降序, 保留 12 位小数判断并列, 并列采用最小名次.
    四分位按 ceil(名次 / 样本数 * 4), 样本少于四只时不提供四分位.
    """
    category = repository.get_peer_category(code)
    nav = repository.get_nav(code, end_date=as_of)
    if nav.empty:
        raise ValueError("没有可用于阶段涨幅计算的净值")
    nav = nav.sort_values("nav_date").set_index("nav_date")
    nav.index = pd.DatetimeIndex(nav.index)
    if nav.index.has_duplicates:
        raise ValueError("阶段涨幅净值存在重复日期")
    prices = _valid(pd.to_numeric(nav.adj_nav, errors="raise")).rename(code)
    end = nav.index.max()
    groups = _periods(end)
    boundaries = {}
    for periods in groups.values():
        for item in periods:
            for key in ("start_cutoff", "end_cutoff"):
                cutoff = item[key]
                boundaries[cutoff] = _anchor(nav.index, cutoff)
    dates = sorted({point.date() for point in boundaries.values() if point is not None})
    points = repository.get_peer_nav_points(code, dates) if not category.empty else pd.DataFrame()
    if points.empty:
        peers = pd.DataFrame()
    else:
        points = points.copy()
        points["nav_date"] = pd.to_datetime(points.nav_date)
        if points.duplicated(["ts_code", "nav_date"]).any():
            raise ValueError("同类净值存在重复日期")
        points["adj_nav"] = _valid(pd.to_numeric(points.adj_nav, errors="raise"))
        peers = points.pivot(index="ts_code", columns="nav_date", values="adj_nav")
    for key, periods in groups.items():
        results = []
        for item in periods:
            left = boundaries[item.pop("start_cutoff")]
            right = boundaries[item.pop("end_cutoff")]
            results.append(_calculate_period(item, left, right, prices, peers, not category.empty))
        groups[key] = results
    metadata = {} if category.empty else category.iloc[0].to_dict()
    return {"code": code, "as_of": end.date().isoformat(), "category": metadata.get("category"),
            "category_source": metadata.get("source"),
            "category_snapshot_date": None if category.empty else pd.Timestamp(metadata["snapshot_date"]).date().isoformat(),
            "groups": groups}
