"""基金分析工作区, 负责页面编排, 查询调度和浏览器会话状态."""

import logging
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from app.bootstrap import create_service
from app.comparison_page import show_comparison_page
from app.components import (
    choice_code, show_chart, show_data_notes, show_details, show_filters,
    show_navigation, show_overview, show_performance, show_search,
)


logger = logging.getLogger(__name__)


def _read_bounds(service, code: str) -> tuple[tuple[date, date] | None, str | None]:
    """通过既有查询接口确定日期控件的合法范围, 不新增数据库接口."""
    if service is None or not code.strip():
        return None, None
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
            raise ValueError("请至少选择一种净值")
        if bounds_error:
            raise ValueError(bounds_error)
        if bounds is not None and conditions["range"] == "自定义" and (
            conditions["start"] is None or conditions["end"] is None
        ):
            raise ValueError("请选择开始日期和结束日期")
        with st.spinner("正在查询基金数据..."):
            service = service or create_service()
            code, start, end = conditions["code"], conditions["start"], conditions["end"]
            # 始终读取三种净值供概览使用, 绘图仅显示用户选择的曲线.
            nav = service.get_nav(code, start, end)
            details = service.get_details(code)
            result = {
                **conditions, "code": details["code"], "name": funds.get(details["code"], "名称未提供"),
                "selected": list(conditions["selected"]), "nav": nav,
                "details": details, "metrics": None, "metrics_error": None,
            }
            for dataset, error in details["errors"].items():
                logger.warning("Fund details unavailable: %s: %s", dataset, error)
            if nav.empty:
                st.session_state.query_feedback = {
                    "level": "info", "message": "本地没有该基金在所选日期内的净值数据, 请检查代码和日期范围.",
                }
            else:
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
            "level": "error", "message": "无法读取本地数据, 请检查数据源配置和数据库初始化状态.",
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
        service = create_service()
        funds = service.list_available_funds()
    except (ValueError, OSError, UnicodeError):
        logger.exception("Fund choices unavailable")
        st.warning("基金列表暂不可用, 仍可手动输入代码查询.")

    if "draft_query" not in st.session_state:
        st.session_state.draft_query = {
            "code": "", "range": "近1年", "selected": ["unit_nav"],
        }
    code = show_search(funds)
    st.session_state.draft_query["code"] = code
    bounds, bounds_error = _read_bounds(service, code)
    # 概览位置在图表前, 但内容在处理本次提交后写入, 避免显示上一轮结果.
    overview_area = st.container()
    with st.container(border=True, key="nav_panel"):
        st.subheader("历史净值走势")
        conditions, submitted = show_filters(bounds)
        conditions["code"] = code
        if submitted:
            _run_query(service, funds, conditions, bounds, bounds_error)
        result = st.session_state.get("query_result")
        if result is not None and _has_pending_changes(result, conditions):
            st.info("条件已修改, 当前仍展示上次查询结果. 点击查询后统一更新.")
        feedback = st.session_state.get("query_feedback")
        if feedback is not None:
            getattr(st, feedback["level"])(feedback["message"])
        show_chart(result)

    with overview_area:
        show_overview(result)
    show_performance(result)
    show_details(result)


if __name__ == "__main__":
    main()
