"""只验证可手算的核心公式, 零风险和交易日缺口."""

from math import sqrt

import pandas as pd
import pytest

from services.fund_metrics import calculate_metrics


def sample(prices):
    dates = pd.date_range("2020-01-06", periods=3)
    return (pd.DataFrame({"nav_date": dates, "adj_nav": prices}),
            pd.DataFrame({"cal_date": dates, "is_open": [1, 1, 1]}))


def test_seven_metrics_with_hand_calculated_returns():
    # 收益为 20%, -10%, 均值 5%, 样本方差 0.045, 回撤 10%.
    nav, calendar = sample([100, 120, 108])
    result = calculate_metrics(nav, calendar)
    annual = 1.08 ** (365.25 / 2) - 1
    volatility = sqrt(0.045 * 252)
    downside = sqrt(0.01 / 2 * 252)
    assert result["metrics"] == pytest.approx({
        "annual_return": annual, "annual_volatility": volatility,
        "max_drawdown": 0.1, "sharpe_ratio": 0.05 * 252 / volatility,
        "annual_downside_risk": downside, "sortino_ratio": 0.05 * 252 / downside,
        "calmar_ratio": annual / 0.1,
    })
    assert result["return_count"] == 2 and result["short_period"]
    # 验证两个年化目标按复利日化, 不直接从日收益扣减年利率.
    metrics = calculate_metrics(nav, calendar, risk_free_rate=0.02, target_return=0.03)["metrics"]
    rf, target = 1.02 ** (1 / 252) - 1, 1.03 ** (1 / 252) - 1
    assert metrics["sharpe_ratio"] == pytest.approx((0.05 - rf) * 252 / volatility)
    assert metrics["sortino_ratio"] == pytest.approx((0.05 - target) * 252 / sqrt((0.1 + target) ** 2 / 2 * 252))


def test_constant_nav_has_no_defined_risk_ratios():
    nav, calendar = sample([100, 100, 100])
    metrics = calculate_metrics(nav, calendar)["metrics"]
    assert metrics["annual_return"] == metrics["max_drawdown"] == metrics["annual_volatility"] == metrics["annual_downside_risk"] == 0
    assert metrics["sharpe_ratio"] is metrics["sortino_ratio"] is metrics["calmar_ratio"] is None


def test_missing_open_day_is_not_treated_as_one_day_return():
    nav, calendar = sample([100, 120, 108])
    with pytest.raises(ValueError, match="开市日"):
        calculate_metrics(nav.iloc[[0, 2]], calendar)
    calendar.loc[1, "is_open"] = 0
    result = calculate_metrics(nav, calendar)
    assert result["excluded_non_trading_rows"] == 1
    assert result["metrics"]["annual_volatility"] is None
