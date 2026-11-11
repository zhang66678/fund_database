"""验证独立净值库, 精确值, 采集边界和 Tushare 库保护, 不请求真实接口."""

from datetime import date
from decimal import Decimal
import json

import pandas as pd
import pytest

from database.connection import connect
from database.ifind_nav_store import initialize_store
from database.schema import initialize
from etl.ifind_nav import update_ifind_nav
from research.compare_nav_sources import compare_nav
from scripts import update_ifind_nav as command


class NavClient:
    def __init__(self):
        self.calls = []
        self.value = "1.1234567890"
        self.fail_code = None
        self.fail_after = None

    def close(self):
        pass

    def query(self, endpoint, payload):
        assert endpoint == "cmd_history_quotation"
        assert payload["indicators"] == "netAssetValue,accumulatedNAV,adjustedNAV"
        assert payload["functionpara"] == {"Interval": "D", "Fill": "Blank"}
        self.calls.append(payload)
        if payload["codes"] == self.fail_code or len(self.calls) == self.fail_after:
            raise ValueError("iFinD 模拟请求失败")
        left = pd.Timestamp(payload["startdate"])
        days = [left.isoformat(), (left + pd.Timedelta(days=1)).isoformat(), payload["enddate"]]
        return {"_fetched_at": "2026-11-09T06:00:00+00:00", "tables": [{
            "thscode": payload["codes"], "time": days,
            "table": {"netAssetValue": [self.value, None, None],
                      "accumulatedNAV": ["2.3", "--", "3.4"], "adjustedNAV": ["4.5", "", None]},
        }]}


def test_only_nav_is_stored_with_exact_values_and_missingness(tmp_path):
    path, client = tmp_path / "ifind.duckdb", NavClient()
    result = update_ifind_nav(client, path, codes=["000001"], start=date(2026, 1, 1), end=date(2026, 1, 5))
    assert result["status"] == "completed"
    assert result["results"][0]["blank_rows"] == 1
    with connect(path, read_only=True) as db:
        assert db.execute("SHOW TABLES").fetchall() == [("fund_nav",)]
        columns = [r[0] for r in db.execute("DESCRIBE fund_nav").fetchall()]
        assert columns == ["ts_code", "nav_date", "unit_nav", "accum_nav", "adj_nav", "fetched_at"]
        rows = db.execute("SELECT unit_nav,accum_nav,adj_nav FROM fund_nav ORDER BY nav_date").fetchall()
        assert rows == [(Decimal("1.1234567890"), Decimal("2.3"), Decimal("4.5")),
                        (None, Decimal("3.4"), None)]
    repeated = update_ifind_nav(client, path, codes=["000001"], start=date(2026, 1, 1), end=date(2026, 1, 5))
    assert repeated["results"][0]["counts"] == {"inserted": 0, "updated": 0, "unchanged": 2}
    client.value = "1.2"
    changed = update_ifind_nav(client, path, codes=["000001"], start=date(2026, 1, 1), end=date(2026, 1, 5))
    assert changed["results"][0]["counts"]["updated"] == 1


def test_reference_is_read_only_and_increment_uses_own_last_date(tmp_path):
    path, reference, client = tmp_path / "ifind.duckdb", tmp_path / "tushare.duckdb", NavClient()
    with connect(reference) as db:
        initialize(db)
        db.execute("INSERT INTO fund_basic(ts_code,name,found_date) VALUES ('000001.OF','样本','2020-01-01')")
    before = reference.read_bytes()
    args = dict(reference_path=reference, codes=["000001"], end=date(2020, 1, 20))
    update_ifind_nav(client, path, **args)
    assert client.calls[0]["startdate"] == "2020-01-01"
    client.calls.clear()
    update_ifind_nav(client, path, **args)
    assert client.calls[0]["startdate"] == "2020-01-14"
    client.calls.clear()
    update_ifind_nav(client, path, full_history=True, **args)
    assert client.calls[0]["startdate"] == "2020-01-01"
    assert reference.read_bytes() == before


def test_late_failure_rolls_back_fund_and_other_funds_continue(tmp_path):
    path, client = tmp_path / "ifind.duckdb", NavClient()
    client.fail_after = 2
    result = update_ifind_nav(client, path, codes=["000001", "000002"],
                              start=date(2020, 1, 1), end=date(2024, 1, 10))
    assert result["status"] == "partial"
    assert [r["status"] for r in result["results"]] == ["failed", "ok"]
    with connect(path, read_only=True) as db:
        assert db.execute("SELECT DISTINCT ts_code FROM fund_nav").fetchall() == [("000002.OF",)]


def test_wrong_target_or_missing_found_date_fails_before_requests(tmp_path):
    path, reference, client = tmp_path / "ifind.duckdb", tmp_path / "tushare.duckdb", NavClient()
    with connect(reference) as db:
        initialize(db)
        db.execute("INSERT INTO fund_basic(ts_code) VALUES ('000001.OF')")
    before = reference.read_bytes()
    with pytest.raises(ValueError, match="路径"):
        update_ifind_nav(client, reference, reference_path=reference, codes=["000001"])
    with pytest.raises(ValueError, match="专用库"):
        initialize_store(reference)
    with pytest.raises(ValueError, match="成立日期"):
        update_ifind_nav(client, path, reference_path=reference, codes=["000001"])
    assert not client.calls and not path.exists() and reference.read_bytes() == before


def test_init_only_command_does_not_load_api_credentials(tmp_path, monkeypatch):
    path = tmp_path / "ifind.duckdb"

    def forbidden_client():
        raise AssertionError("建空库不应初始化凭证客户端")

    monkeypatch.setattr(command, "IfindClient", forbidden_client)
    monkeypatch.setattr("sys.argv", ["update_ifind_nav", "--init-only", "--database", str(path)])
    assert command.main() == 0
    with connect(path, read_only=True) as db:
        assert db.execute("SHOW TABLES").fetchall() == [("fund_nav",)]


def test_default_command_updates_shared_list_and_keeps_all_alias(tmp_path, monkeypatch):
    path, reference, client = tmp_path / "ifind.duckdb", tmp_path / "tushare.duckdb", NavClient()
    with connect(reference) as db:
        initialize(db)
        db.executemany("INSERT INTO fund_basic(ts_code,found_date) VALUES (?,?)", [
            ("000001.OF", "2026-01-01"), ("000002.OF", "2026-01-02"),
        ])
    before = reference.read_bytes()
    monkeypatch.setattr(command, "ROOT", tmp_path)
    monkeypatch.setattr(command, "IfindClient", lambda: client)
    args = ["update_ifind_nav", "--database", str(path), "--reference-database", str(reference),
            "--end-date", "2026-01-20", "--refresh-days", "3"]
    monkeypatch.setattr("sys.argv", args)
    assert command.main() == 0
    assert [(c["codes"], c["startdate"]) for c in client.calls] == [
        ("000001.OF", "2026-01-01"), ("000002.OF", "2026-01-02"),
    ]
    client.calls.clear()
    monkeypatch.setattr("sys.argv", [*args, "--all", "--workers", "2", "--request-interval", "0.3"])
    assert command.main() == 0
    assert [c["startdate"] for c in client.calls] == ["2026-01-18", "2026-01-18"]
    report = sorted((tmp_path / "logs/database").glob("ifind_nav_*.json"))[-1]
    summary = json.loads(report.read_text(encoding="utf-8"))
    assert summary["workers"] == 2 and summary["request_interval"] == client.interval == .3
    assert reference.read_bytes() == before


def test_concurrent_fetch_keeps_writes_on_main_thread_and_isolates_failure(tmp_path, monkeypatch):
    from threading import Barrier, get_ident
    from database import ifind_nav_store

    path, barrier, threads = tmp_path / "ifind.duckdb", Barrier(3), set()
    initialize_store(path)
    with connect(path) as db:
        db.execute("INSERT INTO fund_nav(ts_code,nav_date,adj_nav) VALUES ('000002.OF','2019-12-31',1)")

    class ParallelClient(NavClient):
        def query(self, endpoint, payload):
            threads.add(get_ident())
            barrier.wait(timeout=5)
            if payload["codes"] == "000002.OF":
                raise ValueError("iFinD 模拟单基金失败")
            return super().query(endpoint, payload)

    main_thread, saved, original = get_ident(), [], ifind_nav_store.upsert

    def save(connection, table, frame, **kwargs):
        assert get_ident() == main_thread
        saved.append(frame.ts_code.iloc[0])
        return original(connection, table, frame, **kwargs)

    monkeypatch.setattr(ifind_nav_store, "upsert", save)
    result = update_ifind_nav(ParallelClient(), path, codes=["000001", "000002", "000003"],
                              start=date(2020, 1, 1), end=date(2020, 1, 20), workers=3)
    assert len(threads) == 3 and main_thread not in threads
    assert result["workers"] == 3 and result["status"] == "partial"
    assert [r["status"] for r in result["results"]] == ["ok", "failed", "ok"]
    assert saved == ["000001.OF", "000003.OF"]
    with connect(path, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM fund_nav WHERE ts_code='000002.OF'").fetchone()[0] == 1


def test_batch_reuses_writer_connection_and_recovers_after_write_failure(tmp_path, monkeypatch):
    from database import ifind_nav_store
    from contextlib import contextmanager

    path, opened, original = tmp_path / "ifind.duckdb", [], ifind_nav_store.connect

    @contextmanager
    def tracked_connect(database, **kwargs):
        opened.append(database)
        with original(database, **kwargs) as db:
            yield db

    class Client(NavClient):
        def query(self, endpoint, payload):
            data = super().query(endpoint, payload)
            if payload["codes"] == "000002.OF":
                data["tables"][0]["table"]["netAssetValue"][0] = "1e30"
            return data

    monkeypatch.setattr(ifind_nav_store, "connect", tracked_connect)
    result = update_ifind_nav(Client(), path, codes=["000001", "000002", "000003"],
                              start=date(2020, 1, 1), end=date(2020, 1, 20))
    assert opened == [path]
    assert [r["status"] for r in result["results"]] == ["ok", "failed", "ok"]
    assert all("write_seconds" in r for r in result["results"])
    with connect(path, read_only=True) as db:
        assert db.execute("SELECT DISTINCT ts_code FROM fund_nav ORDER BY ts_code").fetchall() == [
            ("000001.OF",), ("000003.OF",),
        ]


@pytest.mark.parametrize("state,full,code,begin", [
    ("existing", False, "000001.OF", "2026-01-18"),
    ("missing", False, "000002.OF", "2026-01-02"),
    ("existing", True, "000001.OF", "2026-01-01"),
    ("missing", True, "000002.OF", "2026-01-02"),
])
def test_nav_state_uses_ifind_coverage_even_during_full_history(tmp_path, state, full, code, begin):
    path, reference, client = tmp_path / "ifind.duckdb", tmp_path / "tushare.duckdb", NavClient()
    with connect(reference) as db:
        initialize(db)
        db.executemany("INSERT INTO fund_basic(ts_code,found_date) VALUES (?,?)", [
            ("000001.OF", "2026-01-01"), ("000002.OF", "2026-01-02"),
        ])
        # Tushare 有净值的是第二只, iFinD 有净值的是第一只.
        db.execute("INSERT INTO fund_nav(ts_code,nav_date,ann_date,adj_nav) "
                   "VALUES ('000002.OF','2026-01-20','2026-01-21',1)")
    initialize_store(path, reference)
    with connect(path) as db:
        db.execute("INSERT INTO fund_nav(ts_code,nav_date,adj_nav) VALUES ('000001.OF','2026-01-20',1)")
    result = update_ifind_nav(client, path, reference_path=reference, end=date(2026, 1, 20),
                              nav_state=state, refresh_days=3, full_history=full)
    assert result["status"] == "completed" and result["funds"] == 1
    assert [(c["codes"], c["startdate"]) for c in client.calls] == [(code, begin)]
    assert result["nav_state"] == state and result["refresh_days"] == 3


def test_empty_existing_scope_does_not_fall_back_to_all_funds(tmp_path):
    path, reference, client = tmp_path / "ifind.duckdb", tmp_path / "tushare.duckdb", NavClient()
    with connect(reference) as db:
        initialize(db)
        db.execute("INSERT INTO fund_basic(ts_code,found_date) VALUES ('000001.OF','2026-01-01')")
    result = update_ifind_nav(client, path, reference_path=reference, nav_state="existing")
    assert result["status"] == "completed" and result["funds"] == 0
    assert result["results"] == [] and client.calls == []


@pytest.mark.parametrize("content", ["ts_code\n", "ts_code\n\" \"\n", "name\nfoo\n"])
def test_invalid_batch_fails_before_loading_credentials(tmp_path, monkeypatch, content):
    batch = tmp_path / "batch.csv"
    batch.write_text(content, encoding="utf-8-sig")

    def forbidden_client():
        raise AssertionError("无效批次不应初始化凭证客户端")

    monkeypatch.setattr(command, "ROOT", tmp_path)
    monkeypatch.setattr(command, "IfindClient", forbidden_client)
    monkeypatch.setattr("sys.argv", ["update_ifind_nav", "--file", str(batch)])
    assert command.main() == 1


def test_empty_and_conflicting_responses_preserve_existing_nav(tmp_path):
    path, client = tmp_path / "ifind.duckdb", NavClient()
    args = dict(codes=["000001"], start=date(2026, 1, 1), end=date(2026, 1, 5))
    update_ifind_nav(client, path, **args)
    with connect(path, read_only=True) as db:
        before = db.execute("SELECT * FROM fund_nav ORDER BY nav_date").fetchall()
    query = client.query
    client.query = lambda *a: {"tables": [], "_fetched_at": "2026-11-09T06:00:00+00:00"}
    assert update_ifind_nav(client, path, **args)["results"][0]["status"] == "empty"

    def conflict(*args):
        data = query(*args)
        data["tables"][0]["time"][-1] = data["tables"][0]["time"][0]
        return data

    client.query = conflict
    assert update_ifind_nav(client, path, **args)["results"][0]["status"] == "failed"
    with connect(path, read_only=True) as db:
        assert db.execute("SELECT * FROM fund_nav ORDER BY nav_date").fetchall() == before


def test_comparison_uses_latest_announcements_and_separates_adjustment_base(tmp_path):
    reference, path = tmp_path / "tushare.duckdb", tmp_path / "ifind.duckdb"
    with connect(reference) as db:
        initialize(db)
        db.executemany("INSERT INTO fund_nav(ts_code,nav_date,ann_date,unit_nav,accum_nav,adj_nav) "
                       "VALUES ('000001.OF',?,?,?,?,?)", [
            ("2020-01-02", "2020-01-02", 9, 9, 9),
            ("2020-01-02", "2020-01-03", 1, 1, 10),
            ("2020-01-03", "2020-01-03", 1.1, 1.1, 11),
            ("2020-01-06", "2020-01-06", 1.2, 1.2, 12),
        ])
    initialize_store(path, reference)
    with connect(path) as db:
        db.executemany("INSERT INTO fund_nav VALUES ('000001.OF',?,?,?,?,'2026-11-09 06:00:00')", [
            ("2020-01-02", 1, 1, 20), ("2020-01-03", 1.1, 1.15, 22), ("2020-01-07", 1.3, 1.3, 26),
        ])
    details, report = compare_nav(["000001"], ifind_database=path, tushare_database=reference)
    item = report["funds"][0]
    assert (item["common_days"], item["only_tushare_days"], item["only_ifind_days"]) == (2, 1, 1)
    assert item["unit_nav_diff_days"] == 0 and item["accum_nav_diff_days"] == 1
    assert item["adj_nav_diff_days"] == 2 and item["adj_return_diff_days"] == 0
    assert item["adj_base_date"] == "2020-01-02" and len(details) == 4
