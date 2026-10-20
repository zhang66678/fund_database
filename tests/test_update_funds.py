"""验证数据库清单驱动, 补更, 重跑和分红失败隔离."""

from datetime import date
from pathlib import Path
import json
import logging
import sys

import pandas as pd
import pytest

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
            if endpoint in ("fund_div", "fund_manager"):
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


@pytest.mark.parametrize("failure", ["request", "normalize", "write"])
def test_dividend_failure_preserves_other_data(tmp_path, failure):
    """分红请求, 主键校验或写入失败, 均不影响其他表及下一只基金."""
    path = tmp_path / "dividend_failure.duckdb"
    codes = ("000001.OF", "000002.OF")
    with connect(path) as db:
        initialize(db)
        for code in codes:
            db.execute("INSERT INTO fund_basic(ts_code,name,market,found_date) VALUES (?,?,'O','2020-01-01')", [code, code])
        db.execute("""INSERT INTO fund_dividend(ts_code,ann_date,ex_date,div_proc,div_cash)
                      VALUES ('000001.OF','2020-01-02','2020-01-03','implemented',0.1)""")

    class FakeSource:
        def __init__(self):
            self.endpoints = []

        def query(self, endpoint, fields, **params):
            self.endpoints.append(endpoint)
            code = params["ts_code"]
            row = dict(ts_code=code, ann_date="20200102")
            if endpoint == "fund_basic":
                row.update(name="updated", market="O", found_date="20200101")
            elif endpoint == "fund_div":
                if code == codes[0] and failure == "request":
                    raise FetchError("simulated dividend failure")
                if params["offset"]:
                    return pd.DataFrame(columns=fields.split(","))
                row.update(ex_date="20200103", div_proc="implemented", div_cash="0.2")
                if code == codes[0]:
                    if failure == "normalize":
                        row["ex_date"] = None
                    elif failure == "write":
                        # 超过 DECIMAL(24,10) 范围, 验证真实写入失败时的事务隔离.
                        row["div_cash"] = "100000000000000.0"
            elif endpoint == "fund_manager":
                if params["offset"]:
                    return pd.DataFrame(columns=fields.split(","))
                row.update(name="Manager", begin_date="20200101")
            else:
                row.update(nav_date="20200110", unit_nav="1.2", adj_nav="1.3",
                           net_asset="10", total_netasset="12")
            return pd.DataFrame([row], columns=fields.split(","))

    source = FakeSource()
    result = update_funds(source, path, end=date(2020, 2, 10),
                          datasets=("basic", "nav", "dividend", "manager", "asset"))
    assert result["status"] == "partial"
    assert [r["status"] for r in result["funds"]] == ["partial", "ok"]
    failed = result["funds"][0]
    assert failed["details"]["fund_dividend"]["status"] == "failed"
    assert failed["details"]["fund_dividend"]["error"]
    if failure == "normalize":
        assert "'ex_date': 1" in failed["details"]["fund_dividend"]["error"]
    for record in result["funds"]:
        for table in ("fund_nav", "fund_manager", "fund_asset"):
            assert record["counts"][table]["inserted"] == 1
    with connect(path, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM fund_basic WHERE name='updated'").fetchone()[0] == 2
        assert db.execute("SELECT div_cash=0.1 FROM fund_dividend WHERE ts_code='000001.OF'").fetchone()[0]
        assert db.execute("SELECT div_cash=0.2 FROM fund_dividend WHERE ts_code='000002.OF'").fetchone()[0]
        for table in ("fund_nav", "fund_manager", "fund_asset"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 2

    # 只更新净值时不请求分红, 已入库净值重复更新也不会新增记录.
    source.endpoints.clear()
    again = update_funds(source, path, end=date(2020, 2, 10), datasets=("nav",))
    assert again["status"] == "completed"
    assert set(source.endpoints) == {"fund_nav"}
    assert all(r["counts"]["fund_nav"] == {"inserted": 0, "updated": 0, "unchanged": 1}
               for r in again["funds"])


@pytest.mark.parametrize("missing_count", [5, 1201])
def test_missing_announcements_are_saved_separately(tmp_path, missing_count):
    """部分或全部公告日期缺失时保留原始行, 合格数据入库且不误报完整成功."""
    path = tmp_path / "money_fund.duckdb"
    with connect(path) as db:
        initialize(db)
        db.execute("""INSERT INTO fund_basic(ts_code,name,market,found_date)
                      VALUES ('080011.OF','Money fund','O','2005-12-12')""")
    dates = pd.date_range("2006-01-01", periods=1201)
    raw = pd.DataFrame({"ts_code": "080011.OF", "nav_date": dates.strftime("%Y%m%d"),
                        "ann_date": (dates + pd.Timedelta(days=1)).strftime("%Y%m%d"),
                        "unit_nav": "1.0", "accum_nav": None, "adj_nav": "11108.9765",
                        "accum_div": None, "net_asset": None, "total_netasset": None})
    raw.loc[0, "net_asset"] = "10.25"
    raw.loc[raw.index[-missing_count:], "ann_date"] = None
    raw.loc[raw.index[-1], "ann_date"] = " "
    raw.loc[raw.index[-1], "net_asset"] = "12.5"

    class FakeSource:
        def query(self, endpoint, fields, **params):
            assert endpoint == "fund_nav"
            window = raw.nav_date.between(params["start_date"], params["end_date"])
            return raw.loc[window, fields.split(",")].copy()

    result = update_funds(FakeSource(), path, end=date(2009, 12, 31), datasets=("nav", "asset"))
    record = result["funds"][0]
    assert result["status"] == record["status"] == "partial"
    assert record["counts"]["fund_nav"]["inserted"] == 1201 - missing_count
    assert record["counts"]["fund_asset"]["inserted"] == int(missing_count < 1201)
    for quality in (record["quality"]["fund_nav"], record["details"]["fund_asset"]["quality"]):
        assert quality["rejected_rows"] == missing_count
        rejected = pd.read_csv(Path(quality["rejected_file"]), dtype="string", keep_default_na=False)
        assert len(rejected) == missing_count
        assert rejected.ann_date.str.strip().eq("").all()
        assert set(rejected.nav_date) == set(raw.nav_date.iloc[-missing_count:])
    again = update_funds(FakeSource(), path, end=date(2009, 12, 31), datasets=("nav", "asset"))
    assert again["status"] == "partial"
    assert again["funds"][0]["counts"]["fund_nav"]["inserted"] == 0
    with connect(path, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM fund_nav").fetchone()[0] == 1201 - missing_count
        assert db.execute("SELECT count(*) FROM fund_nav WHERE ann_date IS NULL OR accum_nav IS NOT NULL").fetchone()[0] == 0
        if missing_count < 1201:
            assert db.execute("SELECT CAST(adj_nav AS VARCHAR) FROM fund_nav LIMIT 1").fetchone()[0] == "11108.9765000000"


def test_update_scope_uses_batch_or_codes_without_touching_other_funds(tmp_path, monkeypatch, caplog):
    """指定范围只请求目标基金, 重跑幂等, 空清单和未入库代码不会退回全库更新."""
    from scripts import update_database as command

    path = tmp_path / "scope.duckdb"
    with connect(path) as db:
        initialize(db)
        for code in ("000001.OF", "000002.OF", "000003.OF"):
            db.execute("INSERT INTO fund_basic(ts_code,name,market,found_date) VALUES (?,?,'O','2020-01-01')", [code, code])
        db.execute("INSERT INTO fund_nav(ts_code,nav_date,ann_date,unit_nav) VALUES ('000001.OF','2020-01-10','2020-01-11',0.9)")
    batch = tmp_path / "new_funds.csv"
    pd.DataFrame({"ts_code": ["000002", " 000002.of ", "000003.OF"]}).to_csv(batch, index=False, encoding="utf-8-sig")

    class FakeSource:
        def __init__(self):
            self.codes = []

        def query(self, endpoint, fields, **params):
            assert endpoint == "fund_nav"
            assert params["ts_code"] != "000001.OF"
            self.codes.append(params["ts_code"])
            return pd.DataFrame([dict(ts_code=params["ts_code"], nav_date="20200110", ann_date="20200111",
                                      unit_nav="1.2", adj_nav="1.2")], columns=fields.split(","))

    source = FakeSource()
    monkeypatch.setattr(command, "Source", lambda: source)
    monkeypatch.setattr(command, "ROOT", tmp_path)
    common = ["update_database", "--database", str(path), "--datasets", "nav", "--end-date", "2020-01-31"]
    monkeypatch.setattr(sys, "argv", [*common, "--file", str(batch)])
    caplog.set_level(logging.INFO)
    assert command.main() == 0
    assert source.codes == ["000002.OF", "000003.OF"]
    assert "更新进度 1/2" in caplog.text and "更新进度 2/2" in caplog.text
    summary_path = next((tmp_path / "logs").glob("database_update_*.json"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["requested_codes"] == ["000002.OF", "000003.OF"]
    assert summary["source_file"] == str(batch)

    source.codes.clear()
    monkeypatch.setattr(sys, "argv", [*common, "--codes", "000003", "000003.of"])
    assert command.main() == 0
    assert source.codes == ["000003.OF"]
    with connect(path, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM fund_nav").fetchone()[0] == 3
        assert float(db.execute("SELECT unit_nav FROM fund_nav WHERE ts_code='000001.OF'").fetchone()[0]) == 0.9

    source.codes.clear()
    with pytest.raises(ValueError, match="不在数据库"):
        update_funds(source, path, codes=["000003", "999999"], datasets=("nav",))
    with pytest.raises(ValueError, match="不能为空"):
        update_funds(source, path, codes=[], datasets=("nav",))
    pd.DataFrame(columns=["ts_code"]).to_csv(batch, index=False)
    monkeypatch.setattr(sys, "argv", [*common, "--file", str(batch)])
    assert command.main() == 1
    assert source.codes == []
