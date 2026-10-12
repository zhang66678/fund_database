"""按已确认的交易日复权净值口径计算指标"""

from math import isfinite, sqrt

import pandas as pd


def calculate_metrics(
    nav: pd.DataFrame, calendar: pd.DataFrame, *,
    periods_per_year: int = 252, risk_free_rate: float = 0.0,
    target_return: float = 0.0,
) -> dict:
    """nav 为单基金区间净值, calendar 为覆盖该区间的 SSE 完整日历.

    使用 nav_date, adj_nav 和日历的 cal_date, is_open 列.
    净值须已选定公告版本. 返回七项指标及实际区间信息,
    收益和风险以小数表示, 比率无单位, 无法计算的结果为 None.
    利率参数为年化小数, 例如 0.02 表示 2%.
    """
    if periods_per_year <= 0 or not all(isfinite(x) and x > -1 for x in (risk_free_rate, target_return)):
        raise ValueError("年化参数无效")
    frame = nav[["nav_date", "adj_nav"]].copy()
    frame["nav_date"] = pd.to_datetime(frame.nav_date.astype(str), errors="raise")
    if frame.empty or frame.nav_date.isna().any() or frame.nav_date.duplicated().any():
        raise ValueError("净值为空或日期缺失, 重复")
    frame = frame.sort_values("nav_date").set_index("nav_date")
    cal = calendar[["cal_date", "is_open"]].copy()
    cal["cal_date"] = pd.to_datetime(cal.cal_date.astype(str), errors="raise")
    cal = cal.set_index("cal_date").sort_index()
    required = pd.date_range(frame.index.min(), frame.index.max())
    if cal.index.has_duplicates or not required.isin(cal.index).all():
        raise ValueError("交易日历未完整覆盖净值区间")
    flags = pd.to_numeric(cal.loc[required, "is_open"], errors="raise")
    if not flags.isin([0, 1]).all():
        raise ValueError("交易日历开闭市标记无效")
    open_dates = flags.index[flags.eq(1)]
    if not open_dates.isin(frame.index).all():
        raise ValueError("区间内缺少开市日净值, 不能按连续日频计算")
    prices = pd.to_numeric(frame.reindex(open_dates).adj_nav, errors="raise")
    if len(prices) < 2 or not prices.map(lambda x: pd.notna(x) and isfinite(x) and x > 0).all():
        raise ValueError("至少需要两个交易日的有效正复权净值")

    returns = prices.pct_change(fill_method=None).iloc[1:]
    days = (prices.index[-1] - prices.index[0]).days
    annual_return = float((prices.iloc[-1] / prices.iloc[0]) ** (365.25 / days) - 1)
    volatility = float(returns.std(ddof=1) * sqrt(periods_per_year)) if len(returns) >= 2 else None
    max_drawdown = float((1 - prices / prices.cummax()).max())
    daily_rf = (1 + risk_free_rate) ** (1 / periods_per_year) - 1
    daily_target = (1 + target_return) ** (1 / periods_per_year) - 1
    # 分母按全部收益观测计数, 不是仅计算下跌日的标准差.
    downside = float(sqrt(((returns - daily_target).clip(upper=0) ** 2).mean() * periods_per_year))

    def ratio(numerator: float, denominator: float | None) -> float | None:
        return float(numerator / denominator) if denominator is not None and denominator > 0 else None

    metrics = {
        "annual_return": annual_return,
        "annual_volatility": volatility,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": ratio(float((returns - daily_rf).mean() * periods_per_year), volatility),
        "annual_downside_risk": downside,
        "sortino_ratio": ratio(float((returns - daily_target).mean() * periods_per_year), downside),
        "calmar_ratio": ratio(annual_return, max_drawdown),
    }
    # 极短区间的年化可能溢出, 不向页面传递无穷值.
    metrics = {key: value if value is None or isfinite(value) else None for key, value in metrics.items()}
    return {
        "metrics": metrics,
        "start_date": prices.index[0].date().isoformat(),
        "end_date": prices.index[-1].date().isoformat(),
        "nav_count": len(prices), "return_count": len(returns),
        "short_period": bool(prices.index[-1] < prices.index[0] + pd.DateOffset(years=1)),
        "excluded_non_trading_rows": len(frame) - len(prices),
        "periods_per_year": periods_per_year,
        "risk_free_rate": risk_free_rate, "target_return": target_return,
    }
