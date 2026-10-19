"""一个临时数据库场景验证批量排重, 失败隔离及重跑, 不请求真实接口."""

import json
import sys

import pandas as pd
import pytest

from api.tushare_client import FetchError
from database.connection import connect
from database.schema import initialize
from etl.add_funds import add_funds, prepare_fund_batch
from scripts import add_fund as command


def test_batch_skips_existing_continues_after_failure_and_retries(tmp_path, monkeypatch):
    path = tmp_path / "funds.duckdb"
    with connect(path) as db:
        initialize(db)
        # 已退市且尚无净值的基金也必须排除, CSV 名称不能覆盖已有资料.
        db.execute("INSERT INTO fund_basic(ts_code,name,market,status,found_date) "
                   "VALUES ('000001.OF','保留原名','O','D','2020-01-01')")
    batch = tmp_path / "batch.csv"
    batch.write_text("ts_code,name\n000001,不应覆盖\n 000001.of ,重复\n000002,不用此名称\n"
                     "000002.of,重复\n000003,请求失败\n000003.of,失败代码也去重\nbad,非法代码\n000004,继续添加\n",
                     encoding="utf-8-sig")

    class FakeSource:
        def __init__(self):
            self.calls = []
            self.fail = True

        def query(self, endpoint, fields, **params):
            code = params["ts_code"]
            self.calls.append(code)
            assert endpoint == "fund_basic" and params["market"] == "O"
            if self.fail and code == "000003.OF":
                raise FetchError("simulated failure")
            return pd.DataFrame([dict(ts_code=code, name="接口名称", market="O", found_date="20200101")],
                                columns=fields.split(","))

    source = FakeSource()
    first = add_funds(batch, path, source=source)
    assert source.calls == ["000002.OF", "000003.OF", "000004.OF"]
    assert [first[k] for k in ("added", "skipped_existing", "skipped_duplicate", "failed")] == [2, 1, 3, 2]
    assert first["total"] == 8 and first["status"] == "partial"
    with connect(path, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM fund_basic").fetchone()[0] == 3
        assert db.execute("SELECT name FROM fund_basic WHERE ts_code='000001.OF'").fetchone()[0] == "保留原名"
        assert db.execute("SELECT name FROM fund_basic WHERE ts_code='000002.OF'").fetchone()[0] == "接口名称"
        assert db.execute("SELECT count(*) FROM fund_nav").fetchone()[0] == 0

    source.calls.clear()
    again = add_funds(batch, path, source=source)
    assert again["added"] == 0 and again["skipped_existing"] == 3
    assert source.calls == ["000003.OF"]
    source.fail = False
    assert add_funds(batch, path, source=source)["added"] == 1
    # 单只入口继续保留原有 UPSERT 行为, 已有调用方仍可从 scripts 导入.
    assert command.add_fund(source, "000001.of", path)["updated"] == 1

    def unexpected_client():
        raise AssertionError("Existing-only batches must not initialize the real API client")

    monkeypatch.setattr("etl.add_funds.Source", unexpected_client)
    monkeypatch.setattr(command, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["add_fund", "--file", str(batch), "--database", str(path)])
    assert command.main() == 1  # 非法代码仍在原文件中, 命令需返回非零状态.
    summary = json.loads(next((tmp_path / "logs").glob("add_funds_*.json")).read_text(encoding="utf-8"))
    assert summary["added"] == 0 and summary["failed"] == 1
    batch.write_text("ts_code\n000001\n000002.of\n000002\n000004.OF\n", encoding="utf-8-sig")
    assert command.main() == 0


def test_prepare_batch_filters_latest_snapshot_and_preserves_database(tmp_path, monkeypatch):
    path = tmp_path / "funds.duckdb"
    with connect(path) as db:
        initialize(db)
        db.execute("INSERT INTO fund_basic(ts_code,name) VALUES ('000001.OF','尚无净值的基金')")
    snapshots = tmp_path / "data/exploration"
    latest = snapshots / "20261019_093730/fund_basic_all.csv"
    latest.parent.mkdir(parents=True)
    rows = [
        ("000001", "已有", "混合型", "20000101", "L", "O"),
        ("000009.OF", "同日期后代码", "混合型", "20020101", "L", "O"),
        ("000003.OF", "同日期先代码", "混合型", "20020101", "L", "O"),
        ("000004.OF", "先成立", "混合型", "20010101", "L", "O"),
        ("000002.OF", "其他类型", "债券型", "20000101", "L", "O"),
        (" 000004.of ", "先成立", "混合型", "20010101", "L", "O"),
        ("000005.OF", "已终止", "混合型", "19960101", "D", "O"),
        ("000006.OF", "其他市场", "混合型", "19970101", "L", "E"),
        ("000007.OF", "缺少日期", "混合型", "", "L", "O"),
        ("000008.OF", "", "混合型", "20000101", "L", "O"),
        ("bad", "非法代码", "混合型", "20000101", "L", "O"),
    ]
    frame = pd.DataFrame(rows, columns=["ts_code", "name", "fund_type", "found_date", "status", "market"])
    frame.to_csv(latest, index=False, encoding="utf-8-sig")
    older = snapshots / "20260921_095101/fund_basic_all.csv"
    older.parent.mkdir(parents=True)
    # 旧目录的文件后写入, 仍应按快照目录时间而非文件修改时间选择.
    frame.iloc[:1].to_csv(older, index=False, encoding="utf-8-sig")

    def unexpected_client():
        raise AssertionError("Preparing a batch must not initialize the API client")

    monkeypatch.setattr("etl.add_funds.Source", unexpected_client)
    monkeypatch.setattr(command, "Source", unexpected_client)
    output = tmp_path / "batch.csv"
    summary = prepare_fund_batch(snapshots, output, path, limit=2, fund_types=["混合型"])
    selected = pd.read_csv(output, dtype=str)
    assert selected.ts_code.tolist() == ["000004.OF", "000003.OF"]
    assert selected.found_date.tolist() == ["2001-01-01", "2002-01-01"]
    assert summary["skipped_existing"] == 1 and summary["available"] == 3
    assert summary["duplicates"] == 1 and summary["invalid"] == 3
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        prepare_fund_batch(snapshots, output, path)
    assert output.read_bytes() == original

    monkeypatch.setattr(command, "ROOT", tmp_path)
    retired = tmp_path / "retired.csv"
    monkeypatch.setattr(sys, "argv", ["add_fund", "--from-list", "--status", "D", "--fund-types", "混合型",
                                      "--limit", "1", "--output", str(retired), "--database", str(path)])
    assert command.main() == 0
    assert pd.read_csv(retired).ts_code.tolist() == ["000005.OF"]
    report = json.loads(next((tmp_path / "logs").glob("fund_batch_*.json")).read_text(encoding="utf-8"))
    assert report["source_file"] == str(latest) and report["selected"] == 1
    with connect(path, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM fund_basic").fetchone()[0] == 1
