"""用内存数据验证服务层, 不依赖 CSV 或网络."""

from datetime import date

import pandas as pd
import pytest

from services.fund_service import FundService


class StubRepository:
    def __init__(self):
        self.calls = []
        self.nav = pd.DataFrame({
            "nav_date": pd.to_datetime(["2020-01-02"]),
            "unit_nav": [1.2], "accum_nav": [1.4], "adj_nav": [float("nan")],
        })

    def get_nav(self, ts_code, start_date=None, end_date=None):
        self.calls.append((ts_code, start_date, end_date))
        return self.nav.copy()


@pytest.mark.parametrize("code", ["000001", " 000001.of ", "000001.OF"])
def test_normalizes_code_and_passes_dates(code):
    repo = StubRepository()
    result = FundService(repo).get_nav(code, date(2020, 1, 1), date(2020, 1, 3))
    assert repo.calls == [("000001.OF", date(2020, 1, 1), date(2020, 1, 3))]
    pd.testing.assert_frame_equal(result, repo.nav)


def test_selection_preserves_order_and_missing_values():
    repo = StubRepository()
    result = FundService(repo).get_nav("000001", nav_types=["adj_nav", "unit_nav", "adj_nav"])
    assert result.columns.tolist() == ["nav_date", "adj_nav", "unit_nav"]
    assert result.adj_nav.isna().all()
    assert result.unit_nav.tolist() == [1.2]


@pytest.mark.parametrize("params", [
    {"ts_code": ""}, {"ts_code": "12345"}, {"ts_code": "000001.SH"},
    {"ts_code": "000001", "nav_types": []},
    {"ts_code": "000001", "nav_types": ["adj_factor"]},
    {"ts_code": "000001", "start_date": date(2020, 2, 1), "end_date": date(2020, 1, 1)},
])
def test_invalid_input_fails_before_query(params):
    repo = StubRepository()
    with pytest.raises(ValueError):
        FundService(repo).get_nav(**params)
    assert repo.calls == []


def test_empty_result_keeps_selected_columns_and_types():
    repo = StubRepository()
    repo.nav = repo.nav.iloc[:0]
    result = FundService(repo).get_nav("000001", nav_types=["accum_nav"])
    pd.testing.assert_frame_equal(result, repo.nav[["nav_date", "accum_nav"]])
