"""验证精确写入, 重复导入, 修订更新和整批回滚."""

from decimal import Decimal

import duckdb
import pandas as pd
import pytest

from database.connection import connect
from database.schema import TABLES, initialize
from database.duckdb_repository import DuckDBFundRepository
from database.writer import write_batch
from etl.import_snapshot import FILES, import_snapshot, normalize


def test_import_is_idempotent_and_atomic(tmp_path):
    rows = {
        "fund_basic": {"ts_code": "000001.OF", "name": "测试基金"},
        "fund_nav": {"ts_code": "000001.OF", "nav_date": "20200102", "ann_date": "20200103", "unit_nav": "1.25"},
        "fund_dividend": {"ts_code": "000001.OF", "ann_date": "20200103", "ex_date": "20200102", "div_proc": "实施", "div_cash": "0.1"},
        "trade_calendar": {"exchange": "SSE", "cal_date": "20200102", "is_open": "1"},
    }
    for table, row in rows.items():
        pd.DataFrame([row], columns=list(TABLES[table]["columns"])).to_csv(tmp_path / FILES[table], index=False)
    database = tmp_path / "test.duckdb"
    assert all(r["inserted"] == 1 for r in import_snapshot(tmp_path, database).values())
    assert all(r["unchanged"] == 1 and r["inserted"] == r["updated"] == 0
               for r in import_snapshot(tmp_path, database).values())
    nav_path = tmp_path / "fund_nav.csv"
    nav = pd.read_csv(nav_path, dtype=str)
    nav["unit_nav"] = "1.5"
    nav.to_csv(nav_path, index=False)
    assert import_snapshot(tmp_path, database)["fund_nav"]["updated"] == 1
    # 净值先更新, 最后一张日历表触发约束错误, 之前的写入也必须撤销.
    nav["unit_nav"] = "9.0"
    nav.to_csv(nav_path, index=False)
    calendar_path = tmp_path / "trade_cal.csv"
    calendar = pd.read_csv(calendar_path)
    calendar["is_open"] = 2
    calendar.to_csv(calendar_path, index=False)
    with pytest.raises(duckdb.ConstraintException):
        import_snapshot(tmp_path, database)
    with connect(database, read_only=True) as connection:
        assert float(connection.execute("SELECT unit_nav FROM fund_nav").fetchone()[0]) == 1.5
        assert connection.execute("SELECT count(*) FROM fund_nav").fetchone()[0] == 1


def test_database_query_selects_latest_announcement(tmp_path):
    from datetime import date

    path = tmp_path / "query.duckdb"
    with connect(path) as connection:
        initialize(connection)
        connection.execute("INSERT INTO fund_basic (ts_code, name, status) VALUES (?, ?, ?)",
                           ["000001.OF", "历史基金", "D"])
        connection.execute("""INSERT INTO fund_nav (ts_code, nav_date, ann_date, unit_nav, adj_nav)
            VALUES ('000001.OF', '2020-01-02', '2020-01-03', 1, 1),
                   ('000001.OF', '2020-01-02', '2020-01-04', 1.1, NULL),
                   ('000001.OF', '2020-01-03', '2020-01-04', 1.2, 1.2)""")
        connection.execute("INSERT INTO trade_calendar VALUES ('SSE', '2020-01-02', 1)")
    repo = DuckDBFundRepository(path)
    assert repo.list_available_funds() == {"000001.OF": "历史基金"}
    nav = repo.get_nav("000001.OF", date(2020, 1, 2), date(2020, 1, 2))
    assert nav.unit_nav.tolist() == [1.1]
    assert nav.adj_nav.isna().all()
    assert nav.unit_nav.dtype == "float64"
    assert repo.get_nav("unknown").empty
    assert repo.get_nav("unknown").columns.tolist() == nav.columns.tolist()
    assert len(repo.get_calendar()) == 1


def test_decimal_write_uses_schema_precision(tmp_path):
    """较大数值不在抽样内时也能精确写入, 空值和重复写入行为保持不变."""
    raw = pd.DataFrame([
        dict(ts_code="202003.OF", nav_date="20200102", ann_date="20200103", unit_nav="1.12345678"),
        dict(ts_code="202003.OF", nav_date="20200103", ann_date="20200104", unit_nav="10.075872"),
    ], columns=list(TABLES["fund_nav"]["columns"]))
    frame, _ = normalize("fund_nav", raw)
    with connect(tmp_path / "decimal.duckdb") as db:
        initialize(db)
        # 缩小抽样范围, 稳定复现旧实现的 DECIMAL(9,8) 越界问题.
        db.execute("SET pandas_analyze_sample=1")
        assert write_batch(db, {"fund_nav": frame})["fund_nav"]["inserted"] == 2
        assert db.execute("SELECT unit_nav,adj_nav FROM fund_nav ORDER BY nav_date").fetchall() == [
            (Decimal("1.1234567800"), None), (Decimal("10.0758720000"), None)]
        assert write_batch(db, {"fund_nav": frame})["fund_nav"] == {"inserted": 0, "updated": 0, "unchanged": 2}
