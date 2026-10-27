"""验证 HTTP 缓存和字段适配, 不依赖真实账号或网络."""

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from api.ifind_client import IfindClient
from database.ifind_repository import IfindFundRepository
from services.fund_service import FundService


@pytest.fixture
def http(monkeypatch):
    calls = []
    monkeypatch.setenv("IFIND_REFRESH_TOKEN", "test-refresh-token")

    def post(url, *, headers, json, timeout):
        endpoint = url.rsplit("/", 1)[-1]
        calls.append((endpoint, json))
        if endpoint == "get_access_token":
            data = {"errorcode": 0, "data": {"access_token": "test-access-token"}}
        elif endpoint == "basic_data_service":
            items = json.get("indipara") or [{"indicator": "ths_fund_short_name_fund", "indiparams": []}]
            rank = int(items[0]["indiparams"][1]) if items[0]["indicator"] == "ths_service_start_fund" else 1
            values = {
                "ths_fund_short_name_fund": "接口样本", "ths_fund_establishment_date_fund": "20260921",
                "ths_fund_manager_current_fund": "经理甲,经理乙",
                "ths_fund_manager_his_fund": "经理旧(20260921-20260922) 经理甲(20260922-至今) 经理乙(20260923-至今)",
                "ths_service_start_fund": "经理乙(2026-09-23)" if rank == 1 else "经理甲(2026-09-22)",
                "ths_education_fund": "硕士" if rank == 1 else "博士",
                "ths_resume_fund": "经理乙简历" if rank == 1 else "经理甲简历",
                "ths_latest_rp_fund": "20260630", "ths_fanv_fund": 100,
                "ths_fatv_fund": 120, "ths_quarterly_report_dd_fund": "20260721",
            }
            data = {"errorcode": 0, "tables": [{"thscode": json["codes"], "table": {
                item["indicator"]: [values[item["indicator"]]] for item in items
            }}]}
        elif endpoint == "data_pool":
            data = {"errorcode": 0, "tables": [{"table": {
                "jydm": [json["functionpara"]["jjlb"]], "p04925_f010": ["2026/09/22"],
                "p04925_f004": ["2026/09/23"], "p04925_f003": ["2026/09/23"],
                "p04925_f002": ["--"], "p04925_f001": ["0.05"],
            }}]}
        else:
            data = {"errorcode": 0, "tables": [{"thscode": json["codes"],
                "time": ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"],
                "table": {"netAssetValue": [1.0, 1.1, None, 1.2],
                          "accumulatedNAV": [1.4, 1.5, 1.6, 1.7],
                          "adjustedNAV": [1.8, 2.0, 1.9, 2.1]}}]}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)

    monkeypatch.setattr("api.ifind_client.requests.post", post)
    return calls


class Reference:
    def list_available_funds(self):
        return {"003349.OF": "名单名称"}

    def get_calendar(self):
        return pd.DataFrame({"cal_date": pd.date_range("2026-09-21", "2026-09-24"), "is_open": 1})


def test_http_data_feeds_existing_metrics_and_cache_preserves_missing_values(http):
    repo = IfindFundRepository(IfindClient(), Reference())
    service = FundService(repo)
    assert service.list_available_funds() == {"003349.OF": "名单名称"}
    assert http == []  # 编辑基金选项时不发起请求.
    start, end = date(2026, 9, 21), date(2026, 9, 24)
    nav = service.get_nav("003349", start, end)
    assert nav.columns.tolist() == ["nav_date", "unit_nav", "accum_nav", "adj_nav"]
    assert pd.isna(nav.unit_nav.iloc[2])
    nav.iloc[0, 1] = 999
    assert service.get_nav("003349", start, end).unit_nav.iloc[0] == 1
    assert service.get_metrics("003349", start, end)["nav_count"] == 4
    assert service.get_performance_series("003349", start, end).shape == (4, 3)
    assert [name for name, _ in http] == ["get_access_token", "cmd_history_quotation"]
    details = service.get_details("003349")
    assert details["basic"]["found_date"] == pd.Timestamp("2026-09-21")
    assert pd.isna(details["basic"]["fund_type"])
    assert details["errors"] == {}
    assert service.list_available_funds()["003349.OF"] == "接口样本"


def test_cache_expiry_reloads_data_and_keeps_token_in_memory(http, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("api.ifind_client.time.monotonic", lambda: now[0])
    client = IfindClient()
    payload = {"codes": "003349.OF"}
    first = client.query("basic_data_service", payload)
    assert client.query("basic_data_service", payload)["_fetched_at"] == first["_fetched_at"]
    assert len(http) == 2
    now[0] += 601
    client.query("basic_data_service", payload)
    assert [name for name, _ in http] == ["get_access_token", "basic_data_service", "basic_data_service"]


@pytest.mark.parametrize("failure", ["network", "permission", "invalid_json"])
def test_http_failures_are_readable_and_do_not_expose_credentials(monkeypatch, failure):
    monkeypatch.setenv("IFIND_REFRESH_TOKEN", "secret-test-token")

    def post(*args, **kwargs):
        if failure == "network":
            raise requests.ConnectionError("secret-test-token")
        if failure == "invalid_json":
            raise ValueError("secret-test-token")
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"errorcode": -1, "errmsg": "secret-test-token"})

    monkeypatch.setattr("api.ifind_client.requests.post", post)
    with pytest.raises(ValueError, match="iFinD") as error:
        IfindClient().query("basic_data_service", {})
    assert "secret-test-token" not in str(error.value)


def test_all_history_starts_at_ifind_found_date(http):
    repo = IfindFundRepository(IfindClient(), Reference())
    nav = repo.get_nav("003349.OF", end_date=date(2026, 9, 24))
    payload = http[-1][1]
    assert payload["startdate"] == "2026-09-21"
    assert payload["functionpara"]["Fill"] == "Blank"
    assert len(nav) == 4


def test_conflicting_nav_is_rejected_instead_of_choosing_a_value(http, monkeypatch):
    client = IfindClient()
    data = client.query("cmd_history_quotation", {"codes": "003349.OF"})
    table = data["tables"][0]
    table["time"][-1] = table["time"][0]
    monkeypatch.setattr(client, "query", lambda *args: data)
    with pytest.raises(ValueError, match="同一日期"):
        IfindFundRepository(client, Reference()).get_nav("003349.OF", date(2026, 9, 21), date(2026, 9, 24))


def test_empty_result_keeps_nav_schema_and_calendar_failure_does_not_block_nav(http, monkeypatch):
    reference = Reference()

    def no_calendar():
        raise OSError("本地日历不可用")

    monkeypatch.setattr(reference, "get_calendar", no_calendar)
    client = IfindClient()
    service = FundService(IfindFundRepository(client, reference))
    start, end = date(2026, 9, 21), date(2026, 9, 24)
    assert len(service.get_nav("003349", start, end)) == 4
    with pytest.raises(OSError, match="本地日历不可用"):
        service.get_metrics("003349", start, end)
    monkeypatch.setattr(client, "query", lambda *args: {"tables": [], "_fetched_at": "2026-10-27T01:00:00+00:00"})
    nav = service.get_nav("003349", start, end)
    assert nav.empty and nav.columns.tolist() == ["nav_date", "unit_nav", "accum_nav", "adj_nav"]


def test_details_align_manager_profiles_dates_and_asset_meanings(http):
    service = FundService(IfindFundRepository(IfindClient(), Reference()))
    details = service.get_details("003349")
    assert details["errors"] == {}
    managers = details["managers"]
    assert [m["name"] for m in managers] == ["经理乙", "经理甲", "经理旧"]
    assert [m["edu"] for m in managers[:2]] == ["硕士", "博士"]
    assert managers[0]["resume"] == "经理乙简历"
    assert managers[0]["position_status"] == "现任" and pd.isna(managers[0]["end_date"])
    assert managers[-1]["end_date"] == pd.Timestamp("2026-09-22")
    assert pd.isna(managers[-1]["resume"]) and all(pd.isna(m["ann_date"]) for m in managers)
    asset = details["asset"]
    assert asset["nav_date"] == pd.Timestamp("2026-06-30")
    assert asset["ann_date"] == pd.Timestamp("2026-07-21")
    assert asset["net_asset"] == 100 and asset["total_asset"] == 120
    assert pd.isna(asset["total_netasset"])  # 总资产不冒充合计净资产.
    dividend = details["dividends"][0]
    assert dividend["ann_date"] == pd.Timestamp("2026-09-22")
    assert dividend["ex_date"] == dividend["record_date"] == pd.Timestamp("2026-09-23")
    assert dividend["div_cash"] == 0.05 and pd.isna(dividend["pay_date"])
    assert pd.isna(dividend["div_proc"])
    assert next(payload for name, payload in http if name == "data_pool")["functionpara"]["dtype"] == "2"


def test_one_detail_failure_does_not_replace_it_with_reference_data(http, monkeypatch):
    client = IfindClient()
    query = client.query

    def fail_dividends(endpoint, payload):
        if endpoint == "data_pool":
            raise ValueError("iFinD 分红接口权限不足")
        return query(endpoint, payload)

    monkeypatch.setattr(client, "query", fail_dividends)
    details = FundService(IfindFundRepository(client, Reference())).get_details("003349")
    assert set(details["errors"]) == {"dividends"}
    assert details["dividends"] == [] and len(details["managers"]) == 3
    assert details["asset"]["net_asset"] == 100


@pytest.mark.parametrize("message,empty", [("no data.", True), ("permission denied", False)])
def test_report_empty_result_is_distinct_from_failure(http, monkeypatch, message, empty):
    client = IfindClient()
    client.query("basic_data_service", {"codes": "003349.OF", "indipara": []})
    monkeypatch.setattr("api.ifind_client.requests.post", lambda *args, **kwargs: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"errorcode": -4001, "errmsg": message},
    ))
    if empty:
        assert client.query("data_pool", {})["tables"] == []
    else:
        with pytest.raises(ValueError, match="-4001"):
            client.query("data_pool", {})
