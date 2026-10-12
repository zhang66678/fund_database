"""验证缺口检查不会把节假日当作缺失, 或静默跨过缺失净值."""

import pandas as pd
import pytest

from research.audit_single_fund import inspect_nav


def test_calendar_gaps_and_missing_values_are_separate():
    nav = pd.DataFrame({"nav_date": ["20200103", "20200106", "20200108"],
                        "ann_date": ["20200104", "20200107", "20200109"],
                        "unit_nav": [1., 1., 1.], "accum_nav": [1., 1., 1.],
                        "adj_nav": [1., None, 2.]})
    calendar = pd.DataFrame({"cal_date": ["20200103", "20200104", "20200105", "20200106", "20200107", "20200108"],
                             "is_open": [1, 0, 0, 1, 1, 1]})
    result = inspect_nav(nav, calendar)
    assert result["yearly"][0]["missing_open_dates"] == ["20200107"]
    assert result["yearly"][0]["adj_nulls"] == 1
    assert result["adj_changes_over_20pct"] == []


def test_conflicting_latest_versions_are_rejected():
    nav = pd.DataFrame({"nav_date": ["20200103"] * 2, "ann_date": ["20200104"] * 2,
                        "unit_nav": [1., 2.], "accum_nav": [1., 2.], "adj_nav": [1., 2.]})
    with pytest.raises(ValueError, match="Conflicting"):
        inspect_nav(nav, None)
