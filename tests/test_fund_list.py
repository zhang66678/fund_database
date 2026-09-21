"""Offline checks for pagination; no credentials or network requests."""

import pandas as pd
import pytest

from research.explore_funds import paged
from research.tushare_source import FetchError


def test_pagination_continues_after_a_short_page():
    """A server may impose a smaller cap than the requested page size."""
    class LimitedSource:
        def query(self, endpoint, fields, **params):
            values = ['A', 'B', 'C'][params['offset']:params['offset'] + 2]
            return pd.DataFrame({'ts_code': values})

    frame = paged(LimitedSource(), 'fund_basic', 'ts_code', size=1000)
    assert frame.ts_code.tolist() == ['A', 'B', 'C']


def test_ignored_offset_is_not_reported_as_complete():
    class RepeatingSource:
        def query(self, endpoint, fields, **params):
            return pd.DataFrame({'ts_code': ['A', 'B']})

    with pytest.raises(FetchError, match='repeated page'):
        paged(RepeatingSource(), 'fund_basic', 'ts_code')


def test_empty_status_preserves_schema():
    class EmptySource:
        def query(self, endpoint, fields, **params):
            return pd.DataFrame(columns=['ts_code'])

    frame = paged(EmptySource(), 'fund_basic', 'ts_code')
    assert frame.empty
    assert list(frame.columns) == ['ts_code']
