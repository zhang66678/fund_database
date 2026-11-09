"""按基金的基期和开市日计算宽基累计收益, 缺失日期保留断点."""

import pandas as pd

from database.repository import INDEX_NAMES


def get_index_series(repository, codes, dates) -> dict:
    targets = list(dict.fromkeys(codes))
    if set(targets) - INDEX_NAMES.keys():
        raise ValueError("包含不支持的宽基指数")
    if not targets:
        return {"series": {}, "errors": {}}
    dates = pd.DatetimeIndex(dates)
    if len(dates) < 2 or dates.hasnans or dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("宽基比较至少需要两个升序且唯一的开市日")
    start, end = dates[0].date(), dates[-1].date()
    frame = repository.get_index_prices(targets, start, end).copy()
    frame["trade_date"] = pd.to_datetime(frame.trade_date)
    if frame.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("指数行情存在重复日期")
    frame["close"] = pd.to_numeric(frame.close, errors="raise")
    frame["close"] = frame.close.where(frame.close.gt(0) & frame.close.lt(float("inf")))
    series, errors = {}, {}
    for code in targets:
        values = frame.loc[frame.ts_code.eq(code)].set_index("trade_date").close.reindex(dates)
        if not values.notna().any():
            errors[code] = "该区间没有指数行情, 请先更新指数数据"
        elif pd.isna(values.iloc[0]):
            errors[code] = "区间首日缺少指数行情, 请缩短区间或更新指数数据"
        elif values.notna().sum() < 2:
            errors[code] = "区间内有效指数行情不足两天"
        else:
            series[code] = pd.DataFrame({"nav_date": dates, "cumulative_return": values.to_numpy() / values.iloc[0] - 1})
    return {"series": series, "errors": errors}
