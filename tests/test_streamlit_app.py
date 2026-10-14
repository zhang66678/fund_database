"""用本地临时数据验证布局重构后的查询, 筛选和会话状态."""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from app.components import range_dates
from database.connection import connect
from database.schema import initialize


@pytest.fixture
def page(tmp_path, monkeypatch):
    path = tmp_path / "page.duckdb"
    with connect(path) as db:
        initialize(db)
        db.executemany("INSERT INTO fund_basic(ts_code,name,found_date,fund_type,benchmark) VALUES (?,?,?,?,?)", [
            ("000001.OF", "样本甲", "2019-01-01", "混合型", "测试指数收益率×80%+债券指数收益率×20%"),
            ("000002.OF", "样本乙", "2020-01-01", None, None)])
        rows = []
        for code, year in (("000001.OF", 2020), ("000002.OF", 2021)):
            for day, unit, accum, adj in ((1, 1, 1.1, 1.2), (2, 1.1, 1.2, None), (3, 1.2, 1.3, 1.4)):
                rows.append((code, date(year, 1, day), date(year, 1, day), unit, accum, adj))
        db.executemany("INSERT INTO fund_nav(ts_code,nav_date,ann_date,unit_nav,accum_nav,adj_nav) VALUES (?,?,?,?,?,?)", rows)
        days = pd.date_range("2020-01-01", "2021-01-03")
        open_days = {row[1] for row in rows}
        db.executemany("INSERT INTO trade_calendar VALUES ('SSE',?,?)",
                       [(d.date(), int(d.date() in open_days)) for d in days])
        db.execute("INSERT INTO fund_manager(ts_code,name,begin_date,ann_date,edu,resume) VALUES "
                   "('000001.OF','经理甲','2019-01-01','2019-01-02','硕士','测试简历')")
        db.execute("INSERT INTO fund_asset VALUES ('000001.OF','2020-07-01','2020-06-30',1234.5,NULL)")
    monkeypatch.delenv("NAV_CSV", raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(path))
    app = Path(__file__).resolve().parents[1] / "app/streamlit_app.py"
    return AppTest.from_file(str(app), default_timeout=15).run(), path


def traces(app):
    assert not app.exception
    assert len(app.get("plotly_chart")) == 1
    return json.loads(app.get("plotly_chart")[0].proto.spec)["data"]


def test_search_pending_changes_and_navigation_preserve_results(page):
    app, _ = page
    assert traces(app) == []
    assert app.selectbox(key="fund_choice").value is None
    assert app.text_input(key="fund_code").value == ""
    app.selectbox(key="fund_choice").select("000001.OF").run()
    assert app.text_input(key="fund_code").value == "000001.OF"
    app.button(key="submit_query").click().run()
    first = traces(app)
    app.text_input(key="fund_code").set_value("000002").run()
    assert app.selectbox(key="fund_choice").value == "000002.OF"
    app.segmented_control(key="time_range").select("全部").run()
    assert app.text_input(key="fund_code").value == "000002"
    assert app.session_state["query_result"]["code"] == "000001.OF"
    assert traces(app) == first
    assert any("条件已修改" in item.value for item in app.info)
    app.radio(key="workspace_page").set_value("数据说明").run()
    app.radio(key="workspace_page").set_value("基金分析").run()
    assert app.text_input(key="fund_code").value == "000002"
    assert app.segmented_control(key="time_range").value == "全部"
    assert traces(app) == first
    app.button(key="submit_query").click().run()
    assert not app.exception
    assert app.session_state["query_result"]["code"] == "000002.OF"
    assert any(m.value == "**投资类型:** 暂无数据" for m in app.markdown)
    assert any(m.value == "**业绩比较基准:** 暂无数据" for m in app.markdown)


def test_custom_dates_curves_overview_and_detail_columns(page):
    app, _ = page
    app.selectbox(key="fund_choice").select("000001.OF").run()
    app.segmented_control(key="time_range").select("自定义").run()
    assert app.date_input(key="custom_start").proto.min == "2020-01-01"
    assert app.date_input(key="custom_end").proto.max == "2020-01-03"
    app.date_input(key="custom_start").set_value(date(2020, 1, 2)).run()
    app.date_input(key="custom_end").set_value(date(2020, 1, 3)).run()
    app.multiselect(key="nav_types").set_value(["unit_nav", "accum_nav", "adj_nav"]).run()
    app.button(key="submit_query").click().run()
    assert [t["name"] for t in traces(app)] == ["单位净值", "累计净值", "复权单位净值"]
    assert [t["line"]["color"] for t in traces(app)] == ["#2563EB", "#0D9488", "#7C3AED"]
    assert all(t["connectgaps"] is False and len(t["x"]) == 2 for t in traces(app))
    assert app.session_state["query_result"]["start"] == date(2020, 1, 2)
    assert pd.isna(app.session_state["query_result"]["nav"].adj_nav.iloc[0])
    app.multiselect(key="nav_types").set_value(["adj_nav"]).run()
    app.button(key="submit_query").click().run()
    assert len(traces(app)) == 1
    assert traces(app)[0]["line"]["color"] == "#7C3AED"
    assert next(m.value for m in app.metric if m.label == "区间末日单位净值") == "1.2000"
    assert next(m.value for m in app.metric if m.label == "区间末日累计净值") == "1.3000"
    assert next(m.value for m in app.metric if m.label == "资产净值") == "1,234.50"
    assert next(m.value for m in app.metric if m.label == "合计资产净值") == "未提供"
    assert any(m.value == "**成立日期:** 2019-01-01" for m in app.markdown)
    assert any(m.value == "**投资类型:** 混合型" for m in app.markdown)
    assert any(m.value == "**业绩比较基准:** 测试指数收益率×80%+债券指数收益率×20%" for m in app.markdown)
    assert not any("**样本甲**" in m.value for m in app.markdown)
    assert any("离任日期未提供不代表仍在任职" in c.value for c in app.caption)
    positions = {}
    def visit(node, path=()):
        for i, child in getattr(node, "children", {}).items():
            here = (*path, i)
            if child.type == "subheader" and child.value in ("基金经理", "资产信息"):
                positions[child.value] = here
            if child.type == "date_input":
                positions["date"] = here
            if child.type == "plotly_chart":
                positions["chart"] = here
            visit(child, here)
    visit(app.main)
    assert positions["date"] < positions["chart"]
    assert positions["基金经理"][0] == positions["资产信息"][0]
    assert not app.dataframe


def test_invalid_conditions_empty_data_and_storage_failure_keep_empty_chart(page, monkeypatch, tmp_path):
    app, _ = page
    app.multiselect(key="nav_types").set_value([]).run()
    app.button(key="submit_query").click().run()
    assert app.error and traces(app) == []
    app.multiselect(key="nav_types").set_value(["unit_nav"]).run()
    app.selectbox(key="fund_choice").select("000001.OF").run()
    app.segmented_control(key="time_range").select("自定义").run()
    app.date_input(key="custom_start").set_value(date(2020, 1, 3)).run()
    app.date_input(key="custom_end").set_value(date(2020, 1, 1)).run()
    app.button(key="submit_query").click().run()
    assert any("开始日期不能晚于结束日期" in e.value for e in app.error)
    assert traces(app) == []
    app.text_input(key="fund_code").set_value("999999").run()
    app.button(key="submit_query").click().run()
    assert not app.error and traces(app) == []
    assert any("净值数据" in item.value for item in app.info)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "missing.duckdb"))
    app.button(key="submit_query").click().run()
    assert app.error and traces(app) == []


def test_metrics_do_not_depend_on_curve_selection(page):
    app, path = page
    with connect(path) as db:
        db.execute("UPDATE fund_nav SET adj_nav=unit_nav WHERE ts_code='000001.OF'")
    app.selectbox(key="fund_choice").select("000001.OF").run()
    app.button(key="submit_query").click().run()
    metrics = app.session_state["query_result"]["metrics"]
    assert metrics["metrics"]["max_drawdown"] == 0
    assert len(metrics["metrics"]) == 7
    app.multiselect(key="nav_types").set_value(["adj_nav", "accum_nav"]).run()
    app.button(key="submit_query").click().run()
    assert app.session_state["query_result"]["metrics"] == metrics
    assert any("该区间不足一年" in w.value for w in app.warning)
    assert not app.exception


def test_quick_ranges_use_local_last_date_and_handle_leap_year():
    bounds = date(2020, 6, 15), date(2024, 2, 29)
    expected = {"近1个月": date(2024, 1, 29), "近3个月": date(2023, 11, 29),
                "近6个月": date(2023, 8, 29), "近1年": date(2023, 2, 28),
                "近3年": date(2021, 2, 28), "全部": bounds[0]}
    for label, start in expected.items():
        assert range_dates(label, bounds) == (start, bounds[1])
    assert range_dates("近1年", (date(2024, 2, 1), bounds[1])) == (date(2024, 2, 1), bounds[1])
