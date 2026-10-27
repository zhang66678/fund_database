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
        db.execute("""INSERT INTO fund_dividend(ts_code,ann_date,ex_date,record_date,pay_date,div_cash,div_proc)
            VALUES ('000001.OF','2020-06-01','2020-06-05','2020-06-04','2020-06-08',0.05,'实施'),
                   ('000001.OF','2020-05-28','2020-06-05','2020-06-04',NULL,0.05,'预案'),
                   ('000001.OF','2019-01-01','2019-01-04','2019-01-03','2019-01-08',NULL,'实施')""")
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
    assert all(t["mode"] == "lines+markers" for t in first[1:])
    assert app.session_state["query_result"]["performance_series"].iloc[1, 1:].isna().all()
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
    assert any("所选来源未返回分红记录" in item.value for item in app.info)
    # 热更新前保存的查询结果可能尚无分红字段, 页面应提示重新查询.
    app.session_state["query_result"]["details"].pop("dividends")
    app.run()
    assert not app.exception
    assert any("点击查询加载分红记录" in item.value for item in app.caption)


def test_custom_dates_curves_overview_and_detail_columns(page):
    app, path = page
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
    dividends = app.session_state["query_result"]["details"]["dividends"]
    assert len(dividends) == 3
    assert [row["div_proc"] for row in dividends[:2]] == ["实施", "预案"]
    assert dividends[0]["ex_date"] == pd.Timestamp("2020-06-05")
    assert dividends[0]["div_cash"] == 0.05 and pd.isna(dividends[-1]["div_cash"])
    assert any("0.05 元/份" in expander.label and expander.proto.expanded for expander in app.expander)
    assert any("金额未提供" in expander.label for expander in app.expander)
    positions = {}
    def visit(node, path=()):
        for i, child in getattr(node, "children", {}).items():
            here = (*path, i)
            if child.type == "subheader" and child.value in ("绩效分析", "分红记录", "基金经理", "资产信息"):
                positions[child.value] = here
            if child.type == "date_input":
                positions["date"] = here
            if child.type == "plotly_chart":
                positions["chart"] = here
            visit(child, here)
    visit(app.main)
    assert positions["date"] < positions["chart"]
    assert positions["绩效分析"][0] == positions["分红记录"][0]
    assert positions["绩效分析"] < positions["分红记录"]
    assert positions["基金经理"][0] == positions["资产信息"][0]
    assert not app.dataframe
    # 分红读取失败时, 已有净值和其他资料仍可正常展示.
    with connect(path) as db:
        db.execute("DROP TABLE fund_dividend")
    app.button(key="submit_query").click().run()
    assert not app.exception
    assert any("分红记录暂不可用" in item.value for item in app.warning)
    assert app.session_state["query_result"]["details"]["asset"] is not None


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
    data = traces(app)
    assert [t["name"] for t in data] == ["单位净值", "累计收益率", "回撤"]
    assert [t["yaxis"] for t in data] == ["y", "y2", "y3"]
    assert data[1]["line"]["dash"] == "dash" and data[2]["fill"] == "tozeroy"
    assert all(t["connectgaps"] is False for t in data)
    layout = json.loads(app.get("plotly_chart")[0].proto.spec)["layout"]
    assert layout["yaxis2"]["side"] == "right" and layout["xaxis"]["matches"] == "x2"
    assert layout["xaxis"]["showticklabels"] and not layout["xaxis2"]["showticklabels"]
    assert layout["yaxis3"]["domain"][1] < layout["yaxis"]["domain"][0]
    assert layout["yaxis3"]["autorangeoptions"]["maxallowed"] == 0
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


def test_ifind_name_update_preserves_fund_when_changing_quick_range(page, monkeypatch):
    app, _ = page

    class Service:
        def __init__(self):
            self.name = "本地基金简称"

        def list_available_funds(self):
            return {"000001.OF": self.name}

        def get_nav(self, code, start=None, end=None):
            return pd.DataFrame({"nav_date": pd.date_range(end - pd.Timedelta(days=2), end),
                                 "unit_nav": [1.0, 1.1, 1.2], "accum_nav": [1.0, 1.1, 1.2],
                                 "adj_nav": [1.0, 1.1, 1.2]})

        def get_details(self, code):
            self.name = "iFinD 返回的基金简称"
            return {"code": code, "basic": None, "managers": [], "asset": None,
                    "dividends": [], "errors": {}}

        def get_metrics(self, *args):
            raise ValueError("此测试只验证基金选择和查询日期")

    service = Service()
    monkeypatch.setattr("app.bootstrap.create_service", lambda source: service)
    app.radio(key="data_source").set_value("ifind").run()
    app.selectbox(key="fund_choice").select("000001.OF").run()
    app.multiselect(key="nav_types").set_value(["unit_nav"]).run()
    original_label = app.selectbox(key="fund_choice").options[0]
    assert app.segmented_control(key="time_range").value == "近1年"
    app.button(key="submit_query").click().run()
    first = app.session_state["query_result"]
    assert first["name"] == "iFinD 返回的基金简称" and not first["nav"].empty
    app.segmented_control(key="time_range").select("近3年").run()
    # 浏览器以显示文本提交选择值, 简称更新不能改变已选项的提交值.
    assert app.selectbox(key="fund_choice").options[0] == original_label
    assert app.selectbox(key="fund_choice").value == "000001.OF"
    assert app.text_input(key="fund_code").value == "000001.OF"
    app.button(key="submit_query").click().run()
    assert not app.exception and not app.error
    result = app.session_state["query_result"]
    assert result["code"] == "000001.OF" and not result["nav"].empty
    assert result["start"] < first["start"] and result["range"] == "近3年"
    # 用户主动清空代码仍然生效, 不把旧查询的基金强行恢复回来.
    app.text_input(key="fund_code").set_value("").run()
    assert app.selectbox(key="fund_choice").value is None
    assert app.session_state["draft_query"]["code"] == ""


def test_comparison_page_multi_fund_dates_and_navigation(page):
    app, path = page
    with connect(path) as db:
        db.execute("UPDATE fund_nav SET nav_date=nav_date-INTERVAL '1 year', ann_date=ann_date-INTERVAL '1 year' WHERE ts_code='000002.OF'")
        db.execute("INSERT INTO fund_basic(ts_code,name,found_date) VALUES ('000003.OF','样本丙','2019-01-01')")
        db.execute("INSERT INTO fund_nav SELECT '000003.OF',ann_date,nav_date,unit_nav,accum_nav,adj_nav,accum_div FROM fund_nav WHERE ts_code='000001.OF'")
        db.execute("UPDATE fund_nav SET adj_nav=unit_nav")
    app.selectbox(key="fund_choice").select("000001.OF").run()
    app.button(key="submit_query").click().run()
    original = traces(app)
    app.radio(key="workspace_page").set_value("基金对比").run()
    assert traces(app) == []
    assert app.multiselect(key="comparison_codes").value == []
    codes = ["000001.OF", "000002.OF"]
    app.multiselect(key="comparison_codes").set_value(codes).run()
    app.button(key="submit_comparison").click().run()
    assert len(traces(app)) == 2 and all(t["connectgaps"] is False for t in traces(app))
    assert app.dataframe[0].value.shape == (7, 3)
    result = app.session_state["comparison_result"]
    assert result["start_date"] == "2020-01-01" and result["end_date"] == "2020-01-03"
    assert result["cumulative_returns"].iloc[0, 1:].eq(0).all()
    app.multiselect(key="comparison_codes").set_value([*codes, "000003.OF"]).run()
    assert len(traces(app)) == 2
    assert any("上次对比结果" in i.value for i in app.info)
    app.radio(key="workspace_page").set_value("数据说明").run()
    app.radio(key="workspace_page").set_value("基金分析").run()
    assert traces(app) == original
    assert app.text_input(key="fund_code").value == "000001.OF"
    app.radio(key="workspace_page").set_value("基金对比").run()
    assert len(app.multiselect(key="comparison_codes").value) == 3
    assert len(traces(app)) == 2
    app.button(key="submit_comparison").click().run()
    assert len(traces(app)) == 3 and app.dataframe[0].value.shape == (7, 4)
    app.segmented_control(key="comparison_time_range").select("自定义").run()
    app.date_input(key="comparison_custom_start").set_value(date(2020, 1, 2)).run()
    app.button(key="submit_comparison").click().run()
    assert app.session_state["comparison_result"]["start_date"] == "2020-01-02"
    assert not app.exception


def test_comparison_page_handles_gaps_invalid_ranges_and_storage_failure(page, monkeypatch, tmp_path):
    app, path = page
    app.radio(key="workspace_page").set_value("基金对比").run()
    codes = ["000001.OF", "000002.OF"]
    app.multiselect(key="comparison_codes").set_value(codes).run()
    app.button(key="submit_comparison").click().run()
    assert traces(app) == [] and any("没有共同净值区间" in e.value for e in app.error)
    app.multiselect(key="comparison_codes").set_value(codes[:1]).run()
    app.button(key="submit_comparison").click().run()
    assert traces(app) == [] and any("至少选择两只" in e.value for e in app.error)
    with connect(path) as db:
        db.execute("UPDATE fund_nav SET nav_date=nav_date-INTERVAL '1 year', ann_date=ann_date-INTERVAL '1 year', adj_nav=unit_nav WHERE ts_code='000002.OF'")
    app.multiselect(key="comparison_codes").set_value(codes).run()
    app.button(key="submit_comparison").click().run()
    result = app.session_state["comparison_result"]
    assert result["metrics"]["000001.OF"] is None
    assert result["metrics"]["000002.OF"] is not None
    assert result["cumulative_returns"]["000001.OF"].isna().sum() == 1
    assert app.dataframe[0].value.iloc[:, 1].eq("无法计算").all()
    assert traces(app)[0]["connectgaps"] is False
    app.segmented_control(key="comparison_time_range").select("自定义").run()
    app.date_input(key="comparison_custom_start").set_value(date(2020, 1, 3)).run()
    app.date_input(key="comparison_custom_end").set_value(date(2020, 1, 1)).run()
    app.button(key="submit_comparison").click().run()
    assert traces(app) == [] and any("开始日期不能晚于结束日期" in e.value for e in app.error)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "missing.duckdb"))
    app.run()
    assert traces(app) == [] and any("无法读取本地基金列表" in e.value for e in app.error)


def test_source_switch_clears_both_results_preserves_code_and_queries_only_on_submit(page, monkeypatch):
    from api.ifind_client import IfindClient

    app, _ = page
    calls = []
    monkeypatch.setenv("IFIND_REFRESH_TOKEN", "test-token")

    def query(self, endpoint, payload):
        calls.append(endpoint)
        if endpoint == "basic_data_service":
            values = {
                "ths_fund_short_name_fund": "iFinD 样本甲", "ths_fund_establishment_date_fund": "20190101",
                "ths_fund_manager_current_fund": "接口经理", "ths_fund_manager_his_fund": "接口经理(20190101-至今)",
                "ths_service_start_fund": "接口经理(2019-01-01)", "ths_education_fund": "硕士",
                "ths_resume_fund": "接口经理简历", "ths_latest_rp_fund": "20260930",
                "ths_fanv_fund": 200, "ths_fatv_fund": 250, "ths_quarterly_report_dd_fund": "20261027",
            }
            return {"tables": [{"table": {item["indicator"]: [values[item["indicator"]]] for item in payload["indipara"]}}]}
        if endpoint == "data_pool":
            params = payload["functionpara"]
            if not params["sdate"] <= "20200102" <= params["edate"]:
                return {"tables": []}
            return {"tables": [{"table": {"jydm": [params["jjlb"]], "p04925_f010": ["2020/01/02"],
                "p04925_f004": ["2020/01/03"], "p04925_f003": ["2020/01/03"],
                "p04925_f002": ["2020/01/06"], "p04925_f001": ["0.07"]}}]}
        dates = ["2020-01-01", "2020-01-02", "2020-01-03"]
        values = {"netAssetValue": [2.0, 2.1, 2.2], "accumulatedNAV": [3.0, 3.1, 3.2],
                  "adjustedNAV": [4.0, 4.1, 4.2]}
        indices = [i for i, day in enumerate(dates) if payload["startdate"] <= day <= payload["enddate"]]
        return {"_fetched_at": "2026-10-27T01:00:00+00:00", "tables": [{
            "thscode": payload["codes"], "time": [dates[i] for i in indices],
            "table": {key: [items[i] for i in indices] for key, items in values.items()},
        }]}

    monkeypatch.setattr(IfindClient, "query", query)
    app.selectbox(key="fund_choice").select("000001.OF").run()
    app.button(key="submit_query").click().run()
    assert app.session_state["query_result"]["source"] == "tushare"
    app.session_state["comparison_result"] = {"old": True}
    app.radio(key="data_source").set_value("ifind").run()
    assert traces(app) == [] and calls == []
    assert app.text_input(key="fund_code").value == "000001.OF"
    assert "comparison_result" not in app.session_state
    app.segmented_control(key="time_range").select("自定义").run()
    app.date_input(key="custom_start").set_value(date(2020, 1, 1)).run()
    app.date_input(key="custom_end").set_value(date(2020, 1, 3)).run()
    assert calls == []
    app.button(key="submit_query").click().run()
    result = app.session_state["query_result"]
    assert result["source"] == "ifind" and result["nav"].unit_nav.iloc[0] == 2
    assert result["name"] == "iFinD 样本甲"
    assert result["metrics"]["nav_count"] == 3
    assert any("数据来源: iFinD" in c.value for c in app.caption)
    assert result["details"]["errors"] == {}
    assert result["details"]["dividends"][0]["div_cash"] == 0.07
    assert result["details"]["managers"][0]["name"] == "接口经理"
    assert next(m.value for m in app.metric if m.label == "基金资产总值") == "250.00"
    assert any("0.07 元/份" in e.label for e in app.expander)
    assert any("接口经理简历" in m.value for m in app.markdown)
    app.segmented_control(key="time_range").select("全部").run()
    app.button(key="submit_query").click().run()
    assert len(traces(app)) == 3 and app.session_state["query_result"]["start"] is None
    app.radio(key="workspace_page").set_value("基金对比").run()
    app.multiselect(key="comparison_codes").set_value(["000001.OF"]).run()
    app.text_input(key="comparison_manual_codes").set_value("003349").run()
    app.segmented_control(key="comparison_time_range").select("自定义").run()
    app.date_input(key="comparison_custom_start").set_value(date(2020, 1, 1)).run()
    app.date_input(key="comparison_custom_end").set_value(date(2020, 1, 3)).run()
    app.button(key="submit_comparison").click().run()
    assert len(traces(app)) == 2
    assert app.session_state["comparison_result"]["source"] == "ifind"
    app.segmented_control(key="comparison_time_range").select("全部").run()
    app.button(key="submit_comparison").click().run()
    assert len(traces(app)) == 2 and app.session_state["comparison_result"]["start_date"] == "2020-01-01"
    app.radio(key="workspace_page").set_value("基金分析").run()

    def fail(*args):
        raise ValueError("iFinD 测试请求失败")

    monkeypatch.setattr(IfindClient, "query", fail)
    app.button(key="submit_query").click().run()
    assert traces(app) == [] and app.session_state["query_result"] is None
    assert any("iFinD 测试请求失败" in e.value for e in app.error)
    app.radio(key="workspace_page").set_value("基金对比").run()
    app.radio(key="data_source").set_value("tushare").run()
    assert traces(app) == []
    app.radio(key="workspace_page").set_value("基金分析").run()
    assert traces(app) == []
    assert app.text_input(key="fund_code").value == "000001.OF"
