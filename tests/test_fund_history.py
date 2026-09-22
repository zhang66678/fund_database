"""验证日期分段和因子匹配, 不调用网络接口."""

from datetime import date

import pandas as pd
import pytest

from research.fetch_fund_history import combine_nav, fetch_series
from research.tushare_source import FetchError


def test_windows_cover_leap_day_and_end_date_without_overlap():
    class FakeSource:
        def __init__(self):
            self.calls = []

        def query(self, endpoint, fields, **params):
            self.calls.append((params['start_date'], params['end_date']))
            return pd.DataFrame(columns=fields.split(','))

    source = FakeSource()
    fetch_series(source, 'fund_nav', 'ts_code,nav_date', 'A', date(2020, 2, 29), date(2024, 3, 1))
    assert source.calls == [('20200229', '20211231'), ('20220101', '20231231'), ('20240101', '20240301')]


def test_merge_preserves_announcements_and_missing_factors():
    nav = pd.DataFrame({'ts_code': ['A', 'A', 'A'], 'nav_date': ['20200101', '20200101', '20200102'],
                        'ann_date': ['20200102', '20200103', '20200103']})
    factors = pd.DataFrame({'ts_code': ['A'], 'trade_date': ['20200101'], 'adj_factor': [2.0]})
    result = combine_nav(nav, factors)
    assert len(result) == 3
    assert result.adj_factor.iloc[:2].tolist() == [2.0, 2.0]
    assert pd.isna(result.adj_factor.iloc[2])


def test_conflicting_factors_are_rejected():
    nav = pd.DataFrame({'ts_code': ['A'], 'nav_date': ['20200101']})
    factors = pd.DataFrame({'ts_code': ['A', 'A'], 'trade_date': ['20200101'] * 2, 'adj_factor': [1., 2.]})
    with pytest.raises(FetchError, match='conflicting'):
        combine_nav(nav, factors)


def test_ignored_date_filter_is_rejected():
    class FakeSource:
        def query(self, endpoint, fields, **params):
            return pd.DataFrame({'ts_code': ['A'], 'nav_date': ['19990101']})

    with pytest.raises(FetchError, match='range'):
        fetch_series(FakeSource(), 'fund_nav', 'ts_code,nav_date', 'A', date(2020, 1, 1), date(2020, 12, 31))
