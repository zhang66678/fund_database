"""离线检查名单分页和快照输出; 不读取凭证, 不发起网络请求."""

import json
from datetime import date

import pandas as pd
import pytest

from api.tushare_client import FetchError
from etl.extract import paged
from etl.fund_list import fetch_fund_list


def test_fetch_fund_list_writes_snapshot_and_summary(tmp_path):
    class StaticSource:
        def query(self, endpoint, fields, **params):
            if params["offset"]:
                return pd.DataFrame(columns=["ts_code"])
            status = params["status"]
            rows = {
                "L": ["000001.OF", "20010101", "", ""],
                "D": ["000002.OF", "20020101", "20250101", ""],
                "I": ["000003.OF", "20260101", "", ""],
            }
            code, found, due, delist = rows[status]
            return pd.DataFrame([{
                "ts_code": code, "market": "O", "status": status,
                "found_date": found, "due_date": due, "delist_date": delist,
            }])

    summary = fetch_fund_list(StaticSource(), tmp_path, date(2026, 10, 28))

    assert summary["counts_by_status"] == {"L": 1, "D": 1, "I": 1}
    assert summary["total"] == 3 and summary["as_of"] == "2026-10-28"
    for name in ("fund_basic_L.csv", "fund_basic_D.csv", "fund_basic_I.csv",
                 "fund_basic_all.csv", "list_summary.json"):
        assert (tmp_path / name).is_file()
    saved = json.loads((tmp_path / "list_summary.json").read_text(encoding="utf-8"))
    assert saved["total"] == 3 and saved["output"] == str(tmp_path)


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
