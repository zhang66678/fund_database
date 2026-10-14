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


class ComparisonRepository:
    def __init__(self):
        self.frames = {}
        for code, days, prices in (
            ("000001.OF", [3, 4, 6, 7, 8, 9], [50, 999, 100, 120, 108, 110]),
            ("000002.OF", [6, 7, 8, 9], [10, 9, 12, 11]),
            ("000003.OF", [6, 7, 8, 9], [20, 22, 23, 21]),
        ):
            self.frames[code] = pd.DataFrame({
                "nav_date": pd.to_datetime([f"2020-01-{day:02}" for day in days]),
                "adj_nav": prices,
            })

    def get_nav(self, code, start_date=None, end_date=None):
        frame = self.frames[code]
        return frame.loc[frame.nav_date.between(
            pd.Timestamp(start_date or date.min), pd.Timestamp(end_date or date.max),
        )].copy()

    def get_calendar(self):
        dates = pd.date_range("2020-01-03", "2020-01-09")
        return pd.DataFrame({"cal_date": dates, "is_open": (dates.dayofweek < 5).astype(int)})

    def list_available_funds(self):
        return {code: f"样本{index}" for index, code in enumerate(self.frames, 1)}


def test_comparison_uses_common_dates_and_hand_calculated_returns():
    service = FundService(ComparisonRepository())
    result = service.get_comparison(
        ["000001", "000002.of", "000001.OF"], date(2020, 1, 4), date(2020, 1, 10),
    )
    assert result["requested_start_date"] == "2020-01-04"
    assert result["requested_end_date"] == "2020-01-10"
    assert (result["start_date"], result["end_date"]) == ("2020-01-06", "2020-01-09")
    assert result["funds"] == {"000001.OF": "样本1", "000002.OF": "样本2"}
    curve = result["cumulative_returns"]
    assert curve.nav_date.tolist() == list(pd.date_range("2020-01-06", "2020-01-09"))
    assert curve["000001.OF"].tolist() == pytest.approx([0, 0.2, 0.08, 0.1])
    assert curve["000002.OF"].tolist() == pytest.approx([0, -0.1, 0.2, 0.1])
    assert not result["errors"]
    for code in result["funds"]:
        assert result["metrics"][code] == service.get_metrics(code, date(2020, 1, 6), date(2020, 1, 9))


def test_comparison_preserves_gaps_and_rejects_incomparable_inputs():
    repo = ComparisonRepository()
    repo.frames["000003.OF"] = repo.frames["000003.OF"].drop(index=1)
    service = FundService(repo)
    result = service.get_comparison(list(repo.frames))
    curve = result["cumulative_returns"]
    assert pd.isna(curve.loc[curve.nav_date.eq("2020-01-07"), "000003.OF"]).all()
    assert curve.iloc[0, 1:].eq(0).all()
    assert result["metrics"]["000003.OF"] is None
    assert "开市日" in result["errors"]["000003.OF"]
    assert result["metrics"]["000001.OF"] and result["metrics"]["000002.OF"]
    with pytest.raises(ValueError, match="两只不同"):
        service.get_comparison(["000001", "000001.OF"])
    with pytest.raises(ValueError, match="开始日期"):
        service.get_comparison(["000001", "000002"], date(2020, 1, 9), date(2020, 1, 6))
    repo.frames["000002.OF"].loc[0, "adj_nav"] = float("nan")
    with pytest.raises(ValueError, match="首末开市日"):
        service.get_comparison(["000001", "000002"])
    repo.frames["000003.OF"]["nav_date"] -= pd.DateOffset(years=1)
    with pytest.raises(ValueError, match="共同净值区间"):
        service.get_comparison(["000001", "000003"])
