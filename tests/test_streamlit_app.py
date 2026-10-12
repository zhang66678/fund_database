"""通过页面交互验证查询链路, 使用临时数据且不访问网络."""

import json
import os
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture
def page(tmp_path, monkeypatch):
    path = tmp_path / "nav.csv"
    pd.DataFrame({
        "ts_code": ["000001.OF"] * 3,
        "ann_date": ["20200102", "20200103", "20200104"],
        "nav_date": ["20200101", "20200102", "20200103"],
        "unit_nav": [1.0, 1.1, 1.2], "accum_nav": [1.1, 1.2, 1.3],
        "adj_nav": [1.2, None, 1.4],
    }).to_csv(path, index=False)
    monkeypatch.setenv("NAV_CSV", str(path))
    names_path = tmp_path / "names.csv"
    pd.DataFrame({"ts_code": ["000001.OF", "999999.OF"], "name": ["测试基金", "未下载基金"]}).to_csv(names_path, index=False)
    monkeypatch.setenv("FUND_NAMES_CSV", str(names_path))
    app = Path(__file__).resolve().parents[1] / "app/streamlit_app.py"
    return AppTest.from_file(str(app), default_timeout=15).run()


def test_fund_selection_fills_code_but_allows_manual_query(page):
    selector = page.selectbox(key="fund_choice")
    assert selector.options == ["000001.OF  测试基金"]
    selector.select("000001.OF").run()
    assert page.text_input(key="fund_code").value == "000001.OF"
    page.button[0].click().run()
    assert not page.exception
    assert len(page.get("plotly_chart")) == 1
    page.text_input(key="fund_code").set_value("999999").run()
    page.button[0].click().run()
    assert not page.exception
    assert page.text_input(key="fund_code").value == "999999"
    assert len(page.info) == 1


def test_query_draws_selected_series_without_table(page):
    set_date(page, "start_date", date(2020, 1, 2))
    set_date(page, "end_date", date(2020, 1, 3))
    page.checkbox(key="accum_nav").uncheck()
    page.button[0].click().run()
    assert not page.exception
    assert not page.error
    assert len(page.get("plotly_chart")) == 1
    traces = json.loads(page.get("plotly_chart")[0].proto.spec)["data"]
    assert [trace["name"] for trace in traces] == ["单位净值", "复权单位净值"]
    assert all(trace["connectgaps"] is False for trace in traces)
    assert len(traces[0]["x"]) == 2
    assert not page.dataframe


def set_date(page, key, value):
    for part in ("year", "month", "day"):
        page.selectbox(key=f"{key}_{part}").select(getattr(value, part)).run()


def test_date_range_extends_from_1900_to_today(page):
    for key in ("start_date", "end_date"):
        assert page.selectbox(key=f"{key}_year").options == [str(y) for y in range(1900, date.today().year + 1)]
    set_date(page, "start_date", date(1900, 1, 1))
    set_date(page, "end_date", date.today())
    page.button[0].click().run()
    assert not page.exception
    assert not page.error
    assert page.selectbox(key="start_date_year").value == 1900
    assert page.selectbox(key="end_date_month").options[-1] == str(date.today().month)
    assert page.selectbox(key="end_date_day").options[-1] == str(date.today().day)


def test_leap_day_and_short_month_are_adjusted(page):
    set_date(page, "start_date", date(2020, 2, 29))
    page.selectbox(key="start_date_year").select(2021).run()
    assert not page.exception
    assert page.selectbox(key="start_date_day").value == 28
    set_date(page, "start_date", date(2021, 1, 31))
    page.selectbox(key="start_date_month").select(4).run()
    assert not page.exception
    assert page.selectbox(key="start_date_day").value == 30


def test_empty_selection_and_unknown_fund_show_messages(page):
    for checkbox in page.checkbox:
        checkbox.uncheck()
    page.button[0].click().run()
    assert not page.exception
    assert len(page.error) == 1
    assert not page.get("plotly_chart")
    page.checkbox(key="unit_nav").check()
    page.text_input[0].set_value("999999")
    page.button[0].click().run()
    assert not page.exception
    assert len(page.info) == 1
    assert not page.dataframe


def test_missing_file_shows_error(page, monkeypatch, tmp_path):
    monkeypatch.setenv("NAV_CSV", str(tmp_path / "missing.csv"))
    page.button[0].click().run()
    assert not page.exception
    assert len(page.error) == 1


def test_metrics_do_not_depend_on_visible_nav_types(page, tmp_path, monkeypatch):
    nav_path = Path(os.environ["NAV_CSV"])
    nav = pd.read_csv(nav_path)
    nav["adj_nav"] = [1.2, 1.3, 1.4]
    nav.to_csv(nav_path, index=False)
    calendar_path = tmp_path / "trade_cal.csv"
    pd.DataFrame({"exchange": ["SSE"] * 3, "cal_date": ["20200101", "20200102", "20200103"],
                  "is_open": [1, 1, 1]}).to_csv(calendar_path, index=False)
    monkeypatch.setenv("CALENDAR_CSV", str(calendar_path))
    page.button[0].click().run()
    assert not page.exception and not page.error
    assert len(page.metric) == 7
    assert next(m.value for m in page.metric if m.label == "最大回撤") == "0.00%"
    before = [m.value for m in page.metric]
    page.checkbox(key="adj_nav").uncheck()
    page.button[0].click().run()
    assert [m.value for m in page.metric] == before
    assert not page.dataframe
