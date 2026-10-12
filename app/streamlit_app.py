"""基金净值 demo, 只处理用户交互和图表展示."""

import logging
from calendar import monthrange
from datetime import date

import plotly.graph_objects as go
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

    if not submitted:
        return
    try:
        with st.spinner("正在查询..."):
            service = service or create_service()
            nav = service.get_nav(code, start, end, selected)
    except ValueError as exc:
        logger.warning("NAV query rejected: %s", exc)
        st.error(str(exc))
        return
    except (OSError, UnicodeError):
        logger.exception("Failed to read NAV file")
        st.error("无法读取数据, 请检查数据库是否已初始化, DATABASE_PATH 或 NAV_CSV 配置是否正确.")
        return

    if nav.empty:
        st.info("本地没有该基金在所选日期内的净值数据, 请检查代码和日期范围.")
        return
    try:
        show_metrics(service.get_metrics(code, start, end))
    except (ValueError, OSError, UnicodeError) as exc:
        logger.warning("Metrics unavailable: %s", exc)
        st.warning(f"指标暂不可计算: {exc}")
    figure = go.Figure()
    for field in selected:
        # 保留缺失值并断开曲线, 不插值或重算净值.
        figure.add_trace(go.Scatter(
            x=nav.nav_date, y=nav[field], name=LABELS[field],
            mode="lines", connectgaps=False,
        ))
    figure.update_layout(xaxis_title="日期", yaxis_title="净值", hovermode="x unified")
    st.plotly_chart(figure, width="stretch")
    st.caption(f"共 {len(nav)} 条记录, {nav.nav_date.min():%Y-%m-%d} 至 {nav.nav_date.max():%Y-%m-%d}")


if __name__ == "__main__":
    main()
