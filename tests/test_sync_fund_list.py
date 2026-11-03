"""使用临时数据库验证名单同步和命令入口, 不请求真实接口."""

import json
import sys
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from database.connection import connect
from database.schema import TABLES, initialize
from etl.sync_fund_list import sync_fund_list
from scripts import sync_fund_list as command


def snapshot(folder, rows):
    folder.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows).reindex(columns=TABLES["fund_basic"]["columns"], fill_value="").fillna("")
    frame.to_csv(folder / "fund_basic_all.csv", index=False, encoding="utf-8-sig")
    (folder / "list_summary.json").write_text(json.dumps({
        "total": len(frame), "as_of": "2026-11-03",
        "counts_by_status": {s: int(frame.status.eq(s).sum()) for s in ("L", "D", "I")},
    }), encoding="utf-8")
    return frame


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "funds.duckdb"
    with connect(path) as db:
        initialize(db)
        db.execute("INSERT INTO fund_basic(ts_code,name,market,status,found_date) VALUES "
                   "('000001.OF','旧名称','O','L','2000-01-01'),"
                   "('000002.OF','名单外保留','O','L','2000-01-01')")
        db.execute("INSERT INTO fund_nav(ts_code,nav_date,ann_date,adj_nav) "
                   "VALUES ('000002.OF','2020-01-01','2020-01-02',1)")
    return path


def test_sync_refreshes_status_adds_skips_invalid_and_preserves_history(database, tmp_path):
    folder = tmp_path / "20261103_091133"
    snapshot(folder, [
        dict(ts_code="000001.OF", name="新名称", market="O", status="D", found_date="20000101", due_date="20261102"),
        dict(ts_code="000003.OF", name="新增", market="O", status="L", found_date="20261030", m_fee="0.1234567891"),
        dict(ts_code="000004.OF", name="未成立", market="O", status="I"),
        dict(ts_code="000005.OF", name="无效日期", market="O", status="L", found_date="20260230"),
        dict(ts_code="0000031.OF", name="历史七位代码", market="O", status="D", found_date="20000101"),
    ])
    result = sync_fund_list(folder, database)
    assert (result["inserted"], result["updated"], result["skipped_invalid"]) == (1, 1, 3)
    assert result["skipped_records"][-1] == {"code": "0000031.OF", "reason": "非六位场外基金代码"}
    assert result["added_codes"] == ["000003.OF"]
    assert result["status_changes"] == [{"code": "000001.OF", "old_status": "L", "new_status": "D"}]
    assert result["missing_from_snapshot"] == ["000002.OF"]
    with connect(database, read_only=True) as db:
        assert db.execute("SELECT name,status,cast(due_date AS VARCHAR) FROM fund_basic WHERE ts_code='000001.OF'").fetchone() == ("新名称", "D", "2026-11-02")
        assert db.execute("SELECT count(*) FROM fund_basic").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM fund_nav").fetchone()[0] == 1
        assert str(db.execute("SELECT m_fee FROM fund_basic WHERE ts_code='000003.OF'").fetchone()[0]) == "0.1234567891"
    again = sync_fund_list(tmp_path, database)
    assert (again["inserted"], again["updated"], again["unchanged"]) == (0, 0, 2)
    assert again["added_codes"] == again["status_changes"] == []


@pytest.mark.parametrize("problem", ["duplicate", "summary", "columns", "date", "overflow"])
def test_invalid_snapshot_leaves_database_unchanged(database, tmp_path, problem):
    folder = tmp_path / "snapshot"
    rows = [dict(ts_code="000001.OF", name="不应写入", market="O", status="D", found_date="20000101")]
    if problem == "duplicate":
        rows.append(rows[0].copy())
    elif problem == "date":
        rows[0]["due_date"] = "invalid"
    elif problem == "overflow":
        rows.append(dict(ts_code="000003.OF", name="溢出", market="O", status="L",
                         found_date="20000101", issue_amount="999999999999999999999999"))
    frame = snapshot(folder, rows)
    if problem == "summary":
        (folder / "list_summary.json").write_text('{}', encoding="utf-8")
    elif problem == "columns":
        frame.drop(columns="benchmark").to_csv(folder / "fund_basic_all.csv", index=False)
    with pytest.raises(Exception):
        sync_fund_list(folder, database)
    with connect(database, read_only=True) as db:
        assert db.execute("SELECT name,status FROM fund_basic ORDER BY ts_code").fetchall() == [("旧名称", "L"), ("名单外保留", "L")]


def test_command_reuses_local_snapshot_or_fetches_once(database, tmp_path, monkeypatch):
    monkeypatch.setattr(command, "ROOT", tmp_path)
    folder = tmp_path / "data/fund_list/20261103_091133"
    rows = [dict(ts_code="000001.OF", name="同步名称", market="O", status="D", found_date="20000101")]
    snapshot(folder, rows)

    def unexpected_client():
        raise AssertionError("Local sync must not create an API client")

    monkeypatch.setattr(command, "Source", unexpected_client)
    monkeypatch.setattr(sys, "argv", ["sync_fund_list", "--from-list", "--database", str(database)])
    assert command.main() == 0
    reports = list((tmp_path / "logs").glob("fund_list_sync_*.json"))
    assert json.loads(reports[0].read_text(encoding="utf-8"))["updated"] == 1

    sentinel = object()
    monkeypatch.setattr(command, "Source", lambda: sentinel)
    calls = []

    def fetch(source, output, as_of):
        assert source is sentinel
        calls.append(output)
        snapshot(output, rows)

    monkeypatch.setattr(command, "datetime", SimpleNamespace(now=lambda: datetime(2026, 11, 3, 9, 12)))
    monkeypatch.setattr(command, "fetch_fund_list", fetch)
    monkeypatch.setattr(sys, "argv", ["sync_fund_list", "--database", str(database)])
    assert command.main() == 0 and len(calls) == 1

    def failed_fetch(*args):
        calls.append("failed")
        raise RuntimeError("模拟断网")

    monkeypatch.setattr(command, "datetime", SimpleNamespace(now=lambda: datetime(2026, 11, 3, 9, 13)))
    monkeypatch.setattr(command, "fetch_fund_list", failed_fetch)
    assert command.main() == 1 and calls[-1] == "failed"
    assert len(list((tmp_path / "logs").glob("fund_list_sync_*.json"))) == 2
    with connect(database, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM fund_basic").fetchone()[0] == 2
        assert db.execute("SELECT status FROM fund_basic WHERE ts_code='000001.OF'").fetchone()[0] == "D"
