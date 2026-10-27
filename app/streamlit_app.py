"""基金分析工作区, 负责页面编排, 查询调度和浏览器会话状态."""

import logging
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from app.bootstrap import create_service
from app.comparison_page import show_comparison_page
from app.components import (
    choice_code, show_chart, show_data_notes, show_details, show_dividends, show_filters,
    show_navigation, show_overview, show_performance, show_search, show_query_source,
)


logger = logging.getLogger(__name__)


def _read_bounds(service, code: str) -> tuple[tuple[date, date] | None, str | None]:
    """通过既有查询接口确定日期控件的合法范围, 不新增数据库接口."""
    if service is None or not code.strip():
        return None, None
    if st.session_state.get("data_source") == "ifind":
        # 在线模式编辑条件时不下载净值, 提交后才确定实际返回范围.
        return (date(1900, 1, 1), date.today()), None
    try:
        nav = service.get_nav(code)
        dates = nav.loc[nav.nav_date.le(pd.Timestamp(date.today())), "nav_date"]
        if dates.empty:
            return None, None
        return (dates.min().date(), dates.max().date()), None
    except (ValueError, OSError, UnicodeError) as exc:
        logger.warning("NAV bounds unavailable: %s", exc)
        return None, str(exc)


def _run_query(service, funds: dict, conditions: dict, bounds, bounds_error: str | None) -> None:
    """将同一次提交的条件和各板块结果一起保存, 不混用未提交条件."""
    st.session_state.query_result = None
    st.session_state.query_feedback = None
    try:
        if not conditions["selected"]:
            raise ValueError("请至少选择一种曲线")
        if bounds_error:
            raise ValueError(bounds_error)
        if bounds is not None and conditions["range"] == "自定义" and (
            conditions["start"] is None or conditions["end"] is None
        ):
            raise ValueError("请选择开始日期和结束日期")
        with st.spinner("正在查询基金数据..."):
            service = service or create_service(conditions["source"])
            code, start, end = conditions["code"], conditions["start"], conditions["end"]
            # 始终读取三种净值供概览使用, 绘图仅显示用户选择的曲线.
            nav = service.get_nav(code, start, end)
            details = service.get_details(code)
            if conditions["source"] == "ifind":
                funds = service.list_available_funds()
            result = {
                **conditions, "code": details["code"], "name": funds.get(details["code"], "名称未提供"),
                "selected": list(conditions["selected"]), "nav": nav,
                "details": details, "metrics": None, "metrics_error": None,
                "performance_series": None, "series_error": None,
                "fetched_at": nav.attrs.get("fetched_at"),
            }
            for dataset, error in details["errors"].items():
                logger.warning("Fund details unavailable: %s: %s", dataset, error)
            if nav.empty:
                st.session_state.query_feedback = {
                    "level": "info", "message": "所选来源没有该基金在所选日期内的净值数据, 请检查代码和日期范围.",
                }
            else:
                if any(field in conditions["selected"] for field in ("cumulative_return", "drawdown")):
                    try:
                        result["performance_series"] = service.get_performance_series(code, start, end)
                    except (ValueError, OSError, UnicodeError) as exc:
                        logger.warning("Performance series unavailable: %s", exc)
                        result["series_error"] = str(exc)
                try:
                    result["metrics"] = service.get_metrics(code, start, end)
                except (ValueError, OSError, UnicodeError) as exc:
                    logger.warning("Metrics unavailable: %s", exc)
                    result["metrics_error"] = str(exc)
            st.session_state.query_result = result
    except ValueError as exc:
        logger.warning("Fund query rejected: %s", exc)
        st.session_state.query_feedback = {"level": "error", "message": str(exc)}
    except (OSError, UnicodeError):
        logger.exception("Fund data read failed")
        st.session_state.query_feedback = {
            "level": "error", "message": "无法读取数据, 请检查所选数据源配置.",
        }


def _has_pending_changes(result: dict, conditions: dict) -> bool:
    """仅比较影响实际查询的条件, 等价的代码写法不标为变化."""
    return (
        result["code"] != choice_code(conditions["code"])
        or any(result[key] != conditions[key] for key in ("start", "end", "selected"))
    )


def main() -> None:
    page_title = st.session_state.get("workspace_page", "基金分析")
    st.set_page_config(page_title=f"Fund Analytics | {page_title}", layout="wide", initial_sidebar_state="expanded")
    st.html(Path(__file__).with_name("styles.css"))
    page = show_navigation()
    if page == "数据说明":
        show_data_notes()
        return
    if page == "基金对比":
        show_comparison_page()
        return

    st.header("基金分析")
    st.caption("历史净值 · 绩效分析 · 基金资料")
    service, funds = None, {}
    try:
        service = create_service(st.session_state.data_source)
        funds = service.list_available_funds()
    except (ValueError, OSError, UnicodeError) as exc:
        logger.exception("Fund choices unavailable")
        st.warning("基金列表暂不可用, 仍可手动输入代码查询.")
        if st.session_state.data_source == "ifind":
            st.warning(str(exc))

    if "draft_query" not in st.session_state:
        st.session_state.draft_query = {
            "code": "", "range": "近1年", "selected": ["unit_nav", "cumulative_return", "drawdown"],
        }
    code = show_search(funds)
    bounds, bounds_error = _read_bounds(service, code)
    # 概览位置在图表前, 但内容在处理本次提交后写入, 避免显示上一轮结果.
    overview_area = st.container()
    with st.container(border=True, key="nav_panel"):
        st.subheader("净值与收益走势")
        conditions, submitted = show_filters(bounds)
        conditions["code"] = code
        conditions["source"] = st.session_state.data_source
        if submitted:
            _run_query(service, funds, conditions, bounds, bounds_error)
        result = st.session_state.get("query_result")
        if result is not None and _has_pending_changes(result, conditions):
            st.info("条件已修改, 当前仍展示上次查询结果. 点击查询后统一更新.")
        feedback = st.session_state.get("query_feedback")
        if feedback is not None:
            getattr(st, feedback["level"])(feedback["message"])
        show_query_source(result)
        show_chart(result)

    with overview_area:
        show_overview(result)
    with st.container(key="analysis_row"):
        left, right = st.columns(2, gap="medium")
        with left:
            show_performance(result)
        with right:
            show_dividends(result)
    show_details(result)


if __name__ == "__main__":
    main()
