"""验证指数增量入库与共同基期收益, 使用临时数据库和模拟接口."""

from datetime import date

import pandas as pd
import pytest

from api.tushare_client import FetchError
from database.connection import connect
from database.duckdb_repository import DuckDBFundRepository
from database.schema import initialize
from etl.index_prices import update_index_prices
from services.fund_service import FundService


def test_index_returns_share_fund_base_keep_gaps_and_skip_missing_base(tmp_path):
    path = tmp_path / "indices.duckdb"
    with connect(path) as db:
        initialize(db)
        db.executemany("INSERT INTO index_daily VALUES (?,?,?)", [
            ("000300.SH", "2020-01-02", 100), ("000300.SH", "2020-01-03", 110),
            ("000300.SH", "2020-01-07", 120), ("000300.SH", "2020-01-08", 0),
            ("000905.SH", "2020-01-03", 200), ("000905.SH", "2020-01-07", 220),
        ])
    dates = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"])
    result = FundService(DuckDBFundRepository(path)).get_index_series(
        ["000300.SH", "000905.SH", "000852.SH"], dates,
    )
    curve = result["series"]["000300.SH"]
    assert curve.nav_date.tolist() == dates.tolist()
    assert curve.cumulative_return.iloc[[0, 1, 3]].tolist() == pytest.approx([0, .1, .2])
    assert curve.cumulative_return.iloc[[2, 4]].isna().all()
    assert "首日" in result["errors"]["000905.SH"]
    assert "没有指数行情" in result["errors"]["000852.SH"]
    # 文件变化后, 同一个服务也能读取新下载的日期.
    repo = DuckDBFundRepository(path)
    assert len(repo.get_index_prices(["000300.SH"], dates[0].date(), dates[-1].date())) == 4
    with connect(path) as db:
        db.execute("INSERT INTO index_daily VALUES ('000300.SH','2020-01-06',115)")
    assert len(repo.get_index_prices(["000300.SH"], dates[0].date(), dates[-1].date())) == 5


def test_old_database_can_read_missing_index_table_without_migration(tmp_path):
    path = tmp_path / "old.duckdb"
    with connect(path) as db:
        db.execute("CREATE TABLE fund_nav(code VARCHAR)")
    service = FundService(DuckDBFundRepository(path))
    result = service.get_index_series(["000300.SH"], pd.date_range("2020-01-02", periods=2))
    assert not result["series"] and "000300.SH" in result["errors"]
    with connect(path, read_only=True) as db:
        assert db.execute("SHOW TABLES").fetchall() == [("fund_nav",)]


class IndexSource:
    def __init__(self):
        self.calls = []
        self.close = 100
        self.fail_second_chunk = False

    def query(self, endpoint, fields, **params):
        assert endpoint == "index_daily" and fields == "ts_code,trade_date,close"
        self.calls.append(params)
        if self.fail_second_chunk and len(self.calls) == 2:
            raise FetchError("index_daily: network failure")
        return pd.DataFrame([{
            "ts_code": params["ts_code"], "trade_date": params["end_date"], "close": self.close,
        }])


def test_update_chunks_then_refreshes_last_week_idempotently(tmp_path):
    path, source = tmp_path / "indices.duckdb", IndexSource()
    args = dict(codes=["000300.SH"], start=date(2010, 1, 1), end=date(2016, 1, 8))
    result = update_index_prices(source, path, **args)
    assert result["status"] == "completed" and result["results"][0]["counts"]["inserted"] == 2
    assert [(x["start_date"], x["end_date"]) for x in source.calls] == [
        ("20100101", "20141231"), ("20150101", "20160108"),
    ]
    source.calls.clear()
    repeated = update_index_prices(source, path, **args)
    assert source.calls[0]["start_date"] == "20160102"
    assert repeated["results"][0]["counts"] == {"inserted": 0, "updated": 0, "unchanged": 1}
    source.close = 110
    revised = update_index_prices(source, path, **args)
    assert revised["results"][0]["counts"]["updated"] == 1
    source.calls.clear()
    update_index_prices(source, path, full_history=True, **args)
    assert source.calls[0]["start_date"] == "20100101"


def test_failed_later_chunk_does_not_write_partial_index_history(tmp_path):
    path, source = tmp_path / "indices.duckdb", IndexSource()
    with connect(path) as db:
        initialize(db)
        db.execute("INSERT INTO fund_basic(ts_code,name) VALUES ('000001.OF','保留样本')")
    source.fail_second_chunk = True
    result = update_index_prices(source, path, codes=["000300.SH"], start=date(2010, 1, 1), end=date(2016, 1, 8))
    assert result["status"] == "partial" and result["results"][0]["status"] == "failed"
    with connect(path, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM index_daily").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM fund_basic").fetchone()[0] == 1


@pytest.mark.parametrize("empty_starts", [
    {"20100101"}, {"20150101"}, {"20200101"}, {"20100101", "20150101", "20200101"},
], ids=["leading", "middle", "trailing", "all-empty"])
def test_empty_windows_continue_without_string_reduction_or_partial_writes(tmp_path, empty_starts):
    class EmptyWindowSource(IndexSource):
        def query(self, endpoint, fields, **params):
            frame = super().query(endpoint, fields, **params)
            if params["start_date"] in empty_starts:
                return pd.DataFrame(columns=fields.split(","), dtype="string")
            return frame

    path, source = tmp_path / "indices.duckdb", EmptyWindowSource()
    with connect(path) as db:
        initialize(db)
        db.execute("INSERT INTO fund_basic(ts_code,name) VALUES ('000001.OF','保留样本')")
    result = update_index_prices(source, path, codes=["000300.SH"],
                                 start=date(2010, 1, 1), end=date(2020, 1, 8))
    assert [call["start_date"] for call in source.calls] == ["20100101", "20150101", "20200101"]
    expected = 3 - len(empty_starts)
    if expected:
        assert result["status"] == "completed"
        assert result["results"][0]["counts"]["inserted"] == expected
    else:
        assert result["status"] == "partial" and result["results"][0]["status"] == "empty"
    with connect(path, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM index_daily").fetchone()[0] == expected
        assert db.execute("SELECT count(*) FROM fund_basic").fetchone()[0] == 1


def test_wrong_index_response_is_rejected(tmp_path):
    class WrongSource:
        def query(self, *args, **kwargs):
            return pd.DataFrame([{"ts_code": "000905.SH", "trade_date": "20200103", "close": 1}])

    path = tmp_path / "indices.duckdb"
    result = update_index_prices(WrongSource(), path, codes=["000300.SH"],
                                 start=date(2020, 1, 2), end=date(2020, 1, 3))
    assert result["results"][0]["status"] == "failed" and not path.exists()
