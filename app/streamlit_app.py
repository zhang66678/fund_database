"""基金净值 demo, 只处理用户交互和图表展示."""

import logging
from calendar import monthrange
from datetime import date

import plotly.graph_objects as go
import pandas as pd
import streamlit as st

from app.bootstrap import create_service


LABELS = {"unit_nav": "单位净值", "accum_nav": "累计净值", "adj_nav": "复权单位净值"}
logger = logging.getLogger(__name__)
METRIC_LABELS = {
    "sharpe_ratio": ("夏普比率", ".4f"), "calmar_ratio": ("卡玛比率", ".4f"),
    "sortino_ratio": ("索提诺比率", ".4f"), "annual_volatility": ("年化波动", ".2%"),
    "annual_return": ("年化收益", ".2%"), "annual_downside_risk": ("年化下行风险", ".2%"),
    "max_drawdown": ("最大回撤", ".2%"),
}


def show_metrics(result: dict) -> None:
    """页面只负责格式化结果, 计算与交易日筛选交给服务层."""
    items = list(METRIC_LABELS.items())
    for row in (items[:4], items[4:]):
        for column, (key, (label, fmt)) in zip(st.columns(len(row)), row):
            value = result["metrics"][key]
            column.metric(label, "无法计算" if value is None else format(value, fmt))
    st.caption(
        f"指标区间: {result['start_date']} 至 {result['end_date']}, "
        f"{result['nav_count']} 条交易日复权净值, 年化系数 {result['periods_per_year']}. "
        f"无风险利率 {result['risk_free_rate']:.1%}, 目标收益率 {result['target_return']:.1%}. "
        f"排除 {result['excluded_non_trading_rows']} 条非交易日披露."
    )
    if result["short_period"]:
        st.warning("该区间不足一年, 年化结果可能被放大, 请结合实际区间查看.")


def select_date(label: str, default: date, key: str, today: date) -> date:
    """显式列出全部年份, 根据年月调整有效日期, 不依赖日历内部年份菜单."""
    st.markdown(label)
    year_col, month_col, day_col = st.columns(3)
    year = year_col.selectbox(
        "年", range(1900, today.year + 1), index=default.year - 1900, key=f"{key}_year",
    )
    max_month = today.month if year == today.year else 12
    month_key = f"{key}_month"
    if st.session_state.get(month_key, 1) > max_month:
        st.session_state[month_key] = max_month
    month = month_col.selectbox(
        "月", range(1, max_month + 1), index=min(default.month, max_month) - 1, key=month_key,
    )
    max_day = today.day if (year, month) == (today.year, today.month) else monthrange(year, month)[1]
    day_key = f"{key}_day"
    if st.session_state.get(day_key, 1) > max_day:
        st.session_state[day_key] = max_day
    day = day_col.selectbox(
        "日", range(1, max_day + 1), index=min(default.day, max_day) - 1, key=day_key,
    )
    return date(year, month, day)


def fill_fund_code() -> None:
    """只在下拉选项变化时填入代码, 不覆盖后续手动输入."""
    if st.session_state.fund_choice is not None:
        st.session_state.fund_code = st.session_state.fund_choice


def show_chart(result: dict | None) -> None:
    """始终绘制同一个图表区域, 未查询或没有结果时显示空坐标图."""
    figure = go.Figure()
    if result is None:
        figure.add_annotation(
            text="请选择时间范围后点击查询", x=0.5, y=0.5,
            xref="paper", yref="paper", showarrow=False,
        )
    else:
        nav = result["nav"]
        for field in result["selected"]:
            # 保留缺失值并断开曲线, 不插值或重算净值.
            figure.add_trace(go.Scatter(
                x=nav.nav_date, y=nav[field], name=LABELS[field],
                mode="lines", connectgaps=False,
            ))
    figure.update_layout(
        height=420, xaxis_title="日期", yaxis_title="净值",
        xaxis_type="date", hovermode="x unified",
    )
    st.plotly_chart(figure, width="stretch", key="nav_chart")
    if result is not None:
        st.caption(
            f"当前展示: {result['code']}, 查询区间 {result['start']} 至 {result['end']}. "
            f"共 {len(nav)} 条记录, {nav.nav_date.min():%Y-%m-%d} 至 {nav.nav_date.max():%Y-%m-%d}."
        )


def _date_text(value) -> str:
    """日期只做显示格式转换, 缺失日期不推断或补造."""
    return "未提供" if pd.isna(value) else f"{value:%Y-%m-%d}"


def show_fund_details(result: dict) -> None:
    """分板块展示经理和最新资产, 不在页面取数或计算业务指标."""
    st.caption(f"资料对应基金: {result['code']}, 显示本地最新资料, 不受净值查询区间限制.")
    with st.container(border=True):
        st.subheader("基金经理")
        if "managers" in result["errors"]:
            st.warning(f"经理资料暂不可用: {result['errors']['managers']}")
        elif not result["managers"]:
            st.info("暂无本地基金经理资料.")
        else:
            st.caption("按任职日期倒序展示. 离任日期未提供不代表仍在任职.")
            for index, manager in enumerate(result["managers"]):
                with st.expander(f"{manager['name']} | 任职 {_date_text(manager['begin_date'])}", expanded=index == 0):
                    left, right = st.columns(2)
                    left.write(f"任职日期: {_date_text(manager['begin_date'])}")
                    right.write(f"离任日期: {_date_text(manager['end_date'])}")
                    education = "未提供" if pd.isna(manager["edu"]) else manager["edu"]
                    st.caption(f"学历: {education} | 公告日期: {_date_text(manager['ann_date'])}")
                    st.write("暂无简历." if pd.isna(manager["resume"]) else manager["resume"])
    with st.container(border=True):
        st.subheader("资产信息")
        if "asset" in result["errors"]:
            st.warning(f"资产资料暂不可用: {result['errors']['asset']}")
        elif result["asset"] is None:
            st.info("暂无本地资产披露数据.")
        else:
            asset = result["asset"]
            st.caption(f"资产日期: {_date_text(asset['nav_date'])} | 公告日期: {_date_text(asset['ann_date'])}")
            for column, (field, label) in zip(st.columns(2), (("net_asset", "资产净值"), ("total_netasset", "合计资产净值"))):
                value = asset[field]
                column.metric(label, "未提供" if pd.isna(value) else f"{value:,.2f}")
            st.caption("金额按源数据原值展示, 单位及合计范围待核对, 暂不换算为亿元. 两项金额不可相加.")


def main() -> None:
    st.set_page_config(page_title="基金净值查询", layout="wide")
    st.title("基金净值查询")
    st.caption("展示本地已下载的历史净值, 不会自动下载新数据.")
    today = date.today()
    service = None
    try:
        service = create_service()
        funds = service.list_available_funds()
    except (ValueError, OSError, UnicodeError):
        logger.exception("Failed to load fund choices")
        funds = {}
        st.warning("基金选择列表暂不可用, 仍可手动输入代码查询.")
    st.selectbox(
        "选择已有净值的基金", options=list(funds), index=None,
        format_func=lambda code: f"{code}  {funds[code]}",
        placeholder="请选择基金, 或在下方手动输入代码", key="fund_choice",
        on_change=fill_fund_code, disabled=not funds,
    )
    if "fund_code" not in st.session_state:
        st.session_state.fund_code = "000001"
    code = st.text_input("基金代码", key="fund_code", help="可以手动修改, 最终按此代码查询")
    # 先预留图表位置, 查询完成后填入内容, 始终位于日期选择栏上方.
    chart_area = st.container()
    # 日期放在表单外, 年月变化时立即更新月份和天数选项.
    left, right = st.columns(2)
    with left:
        start = select_date("开始日期", date(2020, 1, 1), "start_date", today)
    with right:
        end = select_date("结束日期", date(2025, 12, 31), "end_date", today)
    selected = [
        field for column, (field, label) in zip(st.columns(3), LABELS.items())
        if column.checkbox(label, value=True, key=field)
    ]
    submitted = st.button("查询")

    if submitted:
        # 新查询先清除旧结果, 出错或无数据时仍保留空图.
        st.session_state["nav_query"] = None
        st.session_state["fund_details"] = None
        try:
            with st.spinner("正在查询..."):
                service = service or create_service()
                nav = service.get_nav(code, start, end, selected)
                st.session_state["fund_details"] = service.get_details(code)
                for dataset, error in st.session_state["fund_details"]["errors"].items():
                    logger.warning("Fund details unavailable: %s: %s", dataset, error)
            if nav.empty:
                st.info("本地没有该基金在所选日期内的净值数据, 请检查代码和日期范围.")
            else:
                result = {"code": code, "start": start, "end": end, "selected": selected,
                          "nav": nav, "metrics": None, "metrics_error": None}
                try:
                    result["metrics"] = service.get_metrics(code, start, end)
                except (ValueError, OSError, UnicodeError) as exc:
                    logger.warning("Metrics unavailable: %s", exc)
                    result["metrics_error"] = str(exc)
                # 保存实际查询的条件和结果, 控件触发重绘时曲线及指标不会消失.
                st.session_state["nav_query"] = result
        except ValueError as exc:
            logger.warning("NAV query rejected: %s", exc)
            st.error(str(exc))
        except (OSError, UnicodeError):
            logger.exception("Failed to read NAV file")
            st.error("无法读取数据, 请检查数据库是否已初始化, DATABASE_PATH 或 NAV_CSV 配置是否正确.")

    result = st.session_state.get("nav_query")
    with chart_area:
        show_chart(result)
    if result is not None:
        if result["metrics"] is not None:
            show_metrics(result["metrics"])
        elif result["metrics_error"]:
            st.warning(f"指标暂不可计算: {result['metrics_error']}")
    details = st.session_state.get("fund_details")
    if details is not None:
        show_fund_details(details)


if __name__ == "__main__":
    main()
