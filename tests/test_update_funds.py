"""用一个多基金场景验证数据库清单驱动, 补更, 重跑和失败隔离."""

from datetime import date

import pandas as pd

from database.connection import connect
from database.schema import initialize
from etl.update_funds import update_funds
from api.tushare_client import FetchError


def test_database_driven_update_continues_after_failure(tmp_path):
    path = tmp_path / "update.duckdb"
    with connect(path) as db:
        initialize(db)
        for code in ("000001.OF", "000002.OF", "000003.OF"):
            db.execute("INSERT INTO fund_basic(ts_code,name,market,found_date) VALUES (?,?,'O','2020-01-01')", [code, code])
        db.execute("INSERT INTO fund_nav(ts_code,nav_date,ann_date,unit_nav) VALUES ('000002.OF','2020-01-10','2020-01-11',0.9)")

    class FakeSource:
        def __init__(self):
            self.windows = []

        def query(self, endpoint, fields, **params):
            code = params.get("ts_code")
            if code == "000001.OF":
                raise FetchError("simulated failure")
            if endpoint == "trade_cal":
                dates = pd.date_range(params["start_date"], params["end_date"])
                return pd.DataFrame({"exchange": "SSE", "cal_date": dates.strftime("%Y%m%d"), "is_open": 1})
            if endpoint == "fund_basic":
                return pd.DataFrame([dict(ts_code=code, name=code, market="O", found_date="20200101", status="L")], columns=fields.split(','))
            if endpoint == "fund_div":
                return pd.DataFrame(columns=fields.split(','))
            self.windows.append((code, params["start_date"], params["end_date"]))
            return pd.DataFrame([dict(ts_code=code, nav_date=d, ann_date=a, unit_nav=1.2, adj_nav=1.2)
                                 for d, a in [("20200110", "20200111"), ("20200115", "20200116")]], columns=fields.split(','))

    source = FakeSource()
    result = update_funds(source, path, end=date(2020, 2, 10))
    assert [r["status"] for r in result["funds"]] == ["failed", "ok", "ok"]
    assert ("000002.OF", "20200104", "20200210") in source.windows
    assert ("000003.OF", "20200101", "20200210") in source.windows
    assert result["funds"][1]["counts"]["fund_nav"]["updated"] == 1
    again = update_funds(source, path, end=date(2020, 2, 10))
    assert again["funds"][1]["counts"]["fund_nav"] == {"inserted": 0, "updated": 0, "unchanged": 2}
    with connect(path, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM fund_nav").fetchone()[0] == 4
