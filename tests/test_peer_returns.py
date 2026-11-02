"""验证细分类来源, 周期边界, 公告版本和同类比较, 不请求真实网络."""

from datetime import date
from types import SimpleNamespace
import json
import sys

import pandas as pd
import pytest

from api.ifind_categories import CATEGORY_FIELDS, fetch_categories
from database.connection import connect
from database.duckdb_repository import DuckDBFundRepository
from database.schema import initialize
from etl import fund_categories
from services.fund_service import FundService


class CategoryClient:
    """模拟已鉴权客户端, 分类值仅用于验证映射, 不代表真实基金分类."""

    def __init__(self):
        self.calls = []
        self.values = {"000001.OF": ("混合型", "灵活配置型", "测试三级"),
                       "000002.OF": ("混合型", "偏股混合型", None)}

    def query(self, endpoint, payload):
        self.calls.append((endpoint, payload))
        return {"_fetched_at": "2026-11-02T01:00:00+00:00", "tables": [
            {"thscode": code, "table": {field: [value] for field, value in zip(CATEGORY_FIELDS, self.values[code])}}
            for code in payload["codes"].split(",") if code in self.values
        ]}


def test_ifind_uses_user_provided_indicators_and_keeps_missing_classification():
    client = CategoryClient()
    client.values["000002.OF"] = ("混合型", "--", "可用三级")
    frame = fetch_categories(client, ["000001.OF", "000002.OF", "000003.OF"])
    assert client.calls == [("basic_data_service", {
        "codes": "000001.OF,000002.OF,000003.OF", "indipara": [
            {"indicator": "ths_invest_type_first_classi_fund", "indiparams": []},
            {"indicator": "ths_invest_type_second_classi_fund", "indiparams": []},
            {"indicator": "ths_tzlxsjfl_fund", "indiparams": []},
        ],
    })]
    assert frame.category.iloc[0] == "混合型 / 灵活配置型"
    assert frame.category.iloc[1:].isna().all()
    assert frame.class_level3.iloc[:2].tolist() == ["测试三级", "可用三级"]
    assert pd.isna(frame.class_level3.iloc[2])
    assert frame.attrs["fetched_at"] == "2026-11-02T01:00:00+00:00"


@pytest.mark.parametrize("failure", ["unexpected_code", "duplicate", "missing_field", "multiple_rows"])
def test_invalid_ifind_category_response_is_rejected(failure):
    table = {"thscode": "000001.OF", "table": {field: ["测试分类"] for field in CATEGORY_FIELDS}}
    tables = [table]
    if failure == "unexpected_code":
        table["thscode"] = "000002.OF"
    elif failure == "duplicate":
        tables.append(table)
    elif failure == "missing_field":
        table["table"].pop("ths_invest_type_second_classi_fund")
    else:
        table["table"] = {field: ["甲", "乙"] for field in CATEGORY_FIELDS}
    client = SimpleNamespace(query=lambda *args: {"tables": tables})
    with pytest.raises(ValueError, match="iFinD 分类"):
        fetch_categories(client, ["000001.OF"])


def test_category_snapshots_are_idempotent_and_missing_funds_do_not_use_old_categories(tmp_path):
    path = tmp_path / "categories.duckdb"
    with connect(path) as db:
        initialize(db)
        db.execute("INSERT INTO fund_basic(ts_code) VALUES ('000001.OF'), ('000002.OF')")
    client = CategoryClient()
    first = fund_categories.update_categories(client, tmp_path / "first", path, batch_size=1,
                                              snapshot_date=date(2026, 10, 30))
    assert [call[1]["codes"] for call in client.calls] == ["000001.OF", "000002.OF"]
    again = fund_categories.update_categories(client, tmp_path / "again", path, snapshot_date=date(2026, 10, 30))
    assert first["counts"]["inserted"] == 2
    assert again["counts"] == {"inserted": 0, "updated": 0, "unchanged": 2}
    repo = DuckDBFundRepository(path)
    assert repo.get_peer_category("000001.OF").category.tolist() == ["混合型 / 灵活配置型"]
    del client.values["000001.OF"]
    fund_categories.update_categories(client, tmp_path / "same_day", path, codes=["000001"],
                                      snapshot_date=date(2026, 10, 30))
    assert repo.get_peer_category("000001.OF").empty
    fund_categories.update_categories(client, tmp_path / "new", path, codes=["000001"],
                                      snapshot_date=date(2026, 11, 2))
    assert repo.get_peer_category("000001.OF").empty
    assert not repo.get_peer_category("000002.OF").empty
    with connect(path, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM fund_category").fetchone()[0] == 3
    # 快照模式在数据库文件不存在时仍可运行, 不创建数据库.
    missing = tmp_path / "missing.duckdb"
    saved = fund_categories.update_categories(client, tmp_path / "snapshot", missing,
                                              codes=["000002"], snapshot_only=True)
    assert saved["snapshot_only"] and not missing.exists()


def test_failed_later_batch_does_not_write_partial_classifications(tmp_path):
    path = tmp_path / "failure.duckdb"
    with connect(path) as db:
        initialize(db)
        db.execute("INSERT INTO fund_basic(ts_code) VALUES ('000001.OF'), ('000002.OF')")
    client = CategoryClient()
    original = client.query

    def query(endpoint, payload):
        if payload["codes"] == "000002.OF":
            raise ValueError("模拟接口失败")
        return original(endpoint, payload)

    client.query = query
    with pytest.raises(ValueError, match="模拟接口失败"):
        fund_categories.update_categories(client, tmp_path / "failed", path, batch_size=1)
    with connect(path, read_only=True) as db:
        assert db.execute("SELECT count(*) FROM fund_category").fetchone()[0] == 0
    assert not (tmp_path / "failed").exists()


def test_category_command_snapshot_mode_uses_ifind_without_database_write(tmp_path, monkeypatch):
    from scripts import update_fund_categories as command

    client = CategoryClient()
    monkeypatch.setattr(command, "IfindClient", lambda: client)
    folder = tmp_path / "snapshot"
    missing = tmp_path / "missing.duckdb"
    monkeypatch.setattr(sys, "argv", ["update_fund_categories", "--codes", "000001", "000002",
                                    "--database", str(missing), "--run-dir", str(folder), "--snapshot-only"])
    assert command.main() == 0
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert summary["source"] == "ifind" and summary["classification_level"] == 2
    assert summary["requested_funds"] == summary["classified_funds"] == 2
    assert not missing.exists()


@pytest.fixture
def peer_repository(tmp_path):
    path = tmp_path / "peers.duckdb"
    with connect(path) as db:
        initialize(db)
        for i in range(1, 8):
            code = f"{i:06}.OF"
            db.execute("INSERT INTO fund_basic(ts_code,name,status) VALUES (?,?,?)",
                       [code, f"样本{i}", "D" if i == 4 else "L"])
            db.execute("INSERT INTO fund_category(ts_code,source,snapshot_date,category) VALUES (?, 'ifind', '2026-11-02', ?)",
                       [code, "混合型 / 偏股混合型" if i == 7 else "混合型 / 灵活配置型"])
            starts = ["2023-10-30", "2025-10-30", "2025-12-31", "2026-04-30",
                      "2026-07-30", "2026-09-30", "2026-10-23"]
            if i == 6:
                starts = []
            for day in starts:
                db.execute("INSERT INTO fund_nav(ts_code,nav_date,ann_date,adj_nav) VALUES (?,?,?,1)",
                           [code, day, day])
            price = {1: 1.1, 2: 1.2, 3: 1.1, 4: 0.9, 5: 2, 6: 1.3, 7: 5}[i]
            db.execute("INSERT INTO fund_nav(ts_code,nav_date,ann_date,adj_nav) VALUES (?,'2026-10-30','2026-10-30',?)",
                       [code, price])
        # 最新公告缺值时不回退到旧版本, 第五只基金不能进入有效样本.
        db.execute("INSERT INTO fund_nav(ts_code,nav_date,ann_date,adj_nav) VALUES ('000005.OF','2026-10-30','2026-10-31',NULL)")
    return DuckDBFundRepository(path)


def test_returns_use_subcategory_common_dates_latest_versions_and_single_batch(peer_repository, monkeypatch):
    calls = []
    original = peer_repository.get_peer_nav_points

    def batch(code, dates):
        calls.append(dates)
        return original(code, dates)

    monkeypatch.setattr(peer_repository, "get_peer_nav_points", batch)
    result = FundService(peer_repository).get_peer_returns("000001", date(2026, 11, 2))
    assert len(calls) == 1
    assert result["as_of"] == "2026-10-30"
    assert result["category"] == "混合型 / 灵活配置型"
    assert result["category_source"] == "ifind"
    assert result["category_snapshot_date"] == "2026-11-02"
    groups = result["groups"]
    assert [len(groups[key]) for key in ("stages", "quarters", "years")] == [7, 8, 8]
    for item in groups["stages"]:
        assert item["fund_return"] == pytest.approx(0.1)
        assert item["peer_average"] == pytest.approx(0.075)
        assert (item["rank"], item["sample_count"], item["quartile"], item["quartile_label"]) == (2, 4, 2, "良好")
    quarter = groups["quarters"][0]
    assert (quarter["label"], quarter["start_date"], quarter["end_date"], quarter["partial"]) == (
        "2026年Q4", "2026-09-30", "2026-10-30", True)
    assert groups["quarters"][-1]["label"] == "2025年Q1"
    assert [item["label"] for item in groups["years"]] == [f"{year}年" for year in range(2026, 2018, -1)]
    assert groups["years"][0]["fund_return"] == pytest.approx(0.1)
    assert groups["years"][0]["partial"]
    assert groups["years"][-1]["fund_return"] is None
    assert groups["years"][-1]["rank"] is None


def test_small_samples_missing_classification_and_invalid_prices_preserve_reasons(peer_repository):
    service = FundService(peer_repository)
    path = peer_repository._path
    with connect(path) as db:
        db.execute("DELETE FROM fund_category WHERE ts_code NOT IN ('000001.OF','000002.OF')")
    item = service.get_peer_returns("000001", date(2026, 11, 2))["groups"]["stages"][0]
    assert (item["rank"], item["sample_count"]) == (2, 2)
    assert item["quartile"] is None and "不足四只" in item["reason"]
    with connect(path) as db:
        db.execute("DELETE FROM fund_category")
    item = service.get_peer_returns("000001", date(2026, 11, 2))["groups"]["stages"][0]
    assert item["fund_return"] == pytest.approx(0.1)
    assert item["rank"] is None and "二级分类" in item["reason"]
    with connect(path) as db:
        db.execute("UPDATE fund_nav SET adj_nav=0 WHERE ts_code='000001.OF' AND nav_date='2026-10-30'")
    item = service.get_peer_returns("000001", date(2026, 11, 2))["groups"]["stages"][0]
    assert item["fund_return"] is None and "有效正复权净值" in item["reason"]


def test_old_database_without_classification_does_not_require_schema_write(tmp_path):
    path = tmp_path / "old.duckdb"
    with connect(path) as db:
        db.execute("CREATE TABLE fund_nav(ts_code VARCHAR, nav_date DATE, ann_date DATE, adj_nav DOUBLE)")
        db.execute("INSERT INTO fund_nav VALUES ('000001.OF','2026-10-23','2026-10-23',1), ('000001.OF','2026-10-30','2026-10-30',1.1)")
    repo = DuckDBFundRepository(path)
    assert repo.get_peer_category("000001.OF").empty
    # get_nav 原接口还要求单位/累计净值列, 此处只验证分类查询对旧库的兼容.
    with connect(path, read_only=True) as db:
        assert db.execute("SHOW TABLES").fetchall() == [("fund_nav",)]


def test_missing_week_boundary_does_not_become_month_return(peer_repository):
    with connect(peer_repository._path) as db:
        db.execute("DELETE FROM fund_nav WHERE ts_code='000001.OF' AND nav_date='2026-10-23'")
    groups = FundService(peer_repository).get_peer_returns("000001", date(2026, 11, 2))["groups"]
    assert groups["stages"][0]["fund_return"] is None
    assert groups["stages"][0]["start_date"] is None
    assert groups["stages"][1]["fund_return"] == pytest.approx(0.1)
