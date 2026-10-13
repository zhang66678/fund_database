"""基金分析页面的原生布局组件, 不直接操作数据库或计算绩效指标."""

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


NAV_LABELS = {"unit_nav": "单位净值", "accum_nav": "累计净值", "adj_nav": "复权单位净值"}
NAV_COLORS = {"unit_nav": "#2563EB", "accum_nav": "#0D9488", "adj_nav": "#7C3AED"}
RANGES = ("近1个月", "近3个月", "近6个月", "近1年", "近3年", "全部", "自定义")
METRICS = {
    "annual_return": ("年化收益", ".2%"), "annual_volatility": ("年化波动", ".2%"),
    "max_drawdown": ("最大回撤", ".2%"), "sharpe_ratio": ("夏普比率", ".4f"),
    "calmar_ratio": ("卡玛比率", ".4f"), "sortino_ratio": ("索提诺比率", ".4f"),
    "annual_downside_risk": ("年化下行风险", ".2%"),
}


def choice_code(value: str) -> str:
    """仅用于控件匹配, 正式代码校验仍交给 FundService."""
    value = value.strip().upper()
    return value + ".OF" if len(value) == 6 and value.isdigit() else value


def _remember(field: str, key: str) -> None:
    # 编辑条件独立于控件 key 保存, 切换导航后不会被控件清理机制丢弃.
    st.session_state.draft_query[field] = st.session_state[key]


def _choose_fund() -> None:
    if st.session_state.fund_choice is not None:
        st.session_state.fund_code = st.session_state.fund_choice
        _remember("code", "fund_code")


def _edit_code(funds: dict) -> None:
    _remember("code", "fund_code")
    code = choice_code(st.session_state.fund_code)
    # 修改选择框不会触发其 on_change, 不会把手动输入反向覆盖.
    st.session_state.fund_choice = code if code in funds else None


def show_navigation() -> str:
    with st.sidebar:
        with st.container(key="sidebar_brand"):
            st.markdown("### Fund Analytics")
            st.caption("基金研究与分析")
        with st.container(key="sidebar_navigation"):
            page = st.radio("导航", ("基金分析", "数据说明"), key="workspace_page", label_visibility="collapsed")
        st.divider()
        st.caption("本地历史数据查询, 不自动下载新数据.")
    return page


def show_search(funds: dict) -> str:
    draft = st.session_state.draft_query
    if "fund_code" not in st.session_state:
        st.session_state.fund_code = draft["code"]
    if "fund_choice" not in st.session_state:
        code = choice_code(draft["code"])
        st.session_state.fund_choice = code if code in funds else None
    with st.container(border=True, key="fund_search"):
        select_col, code_col = st.columns([3, 1], gap="medium")
        select_col.selectbox(
            "选择基金", options=list(funds), index=None, key="fund_choice",
            format_func=lambda code: f"{code}  {funds[code]}", placeholder="按代码或名称选择本地基金",
            on_change=_choose_fund, disabled=not funds,
        )
        code = code_col.text_input(
            "基金代码", key="fund_code", placeholder="例如 000001",
            on_change=_edit_code, args=(funds,), help="保留手动查询, 最终以此处代码为准",
        )
    return code


def range_dates(label: str, bounds: tuple[date, date]) -> tuple[date, date]:
    """快捷范围按最新本地净值日回溯自然月, 包含两端并限制在数据范围内."""
    first, last = bounds
    if label == "全部":
        return first, last
    months = {"近1个月": 1, "近3个月": 3, "近6个月": 6, "近1年": 12, "近3年": 36}[label]
    return max(first, (pd.Timestamp(last) - pd.DateOffset(months=months)).date()), last


def show_filters(bounds: tuple[date, date] | None) -> tuple[dict, bool]:
    draft = st.session_state.draft_query
    if "time_range" not in st.session_state:
        st.session_state.time_range = draft["range"]
    label = st.segmented_control(
        "时间范围", RANGES, key="time_range", required=True, disabled=bounds is None, wrap=True,
        on_change=_remember, args=("range", "time_range"),
    )
    start, end = None, None
    if bounds is not None:
        if label == "自定义":
            defaults = range_dates("近1年", bounds)
            for column, key, title, default in zip(st.columns(2), ("custom_start", "custom_end"), ("开始日期", "结束日期"), defaults):
                draft.setdefault(key, default)
                if key not in st.session_state:
                    st.session_state[key] = draft[key]
                # 换基金时仅修正超出新数据范围的编辑日期, 不修改已查询结果.
                value = st.session_state[key]
                if value is not None:
                    st.session_state[key] = min(max(value, bounds[0]), bounds[1])
                    draft[key] = st.session_state[key]
                column.date_input(
                    title, value=None, min_value=bounds[0], max_value=bounds[1], key=key,
                    format="YYYY-MM-DD", on_change=_remember, args=(key, key),
                )
            start, end = st.session_state.custom_start, st.session_state.custom_end
        else:
            start, end = range_dates(label, bounds)
        st.caption(f"本地净值范围: {bounds[0]} 至 {bounds[1]}. 快捷区间以末日为基准, 含两端.")
    else:
        st.caption("尚未取得该基金的本地净值范围.")
    if "nav_types" not in st.session_state:
        st.session_state.nav_types = draft["selected"]
    curve_col, button_col = st.columns([4, 1], vertical_alignment="bottom")
    selected = curve_col.multiselect(
        "净值曲线", options=list(NAV_LABELS), format_func=NAV_LABELS.get, key="nav_types",
        on_change=_remember, args=("selected", "nav_types"), help="允许选择多条曲线, 数值保留原始口径",
    )
    submitted = button_col.button("查询", key="submit_query", type="primary", width="stretch")
    return {"range": label, "start": start, "end": end, "selected": list(selected)}, submitted


def _date_text(value) -> str:
    return "暂无数据" if pd.isna(value) else f"{value:%Y-%m-%d}"


def show_overview(result: dict | None) -> None:
    with st.container(border=True, key="fund_overview"):
        st.subheader("基金概览")
        latest = None
        if result is None:
            st.caption("提交查询后展示基金身份和查询区间末日净值.")
        else:
            st.markdown(f"**{result['name']}** · {result['code']}")
            if not result["nav"].empty:
                latest = result["nav"].iloc[-1]
            if result["start"] is not None:
                st.caption(f"对应净值日期: {_date_text(None if latest is None else latest.nav_date)} | 已提交区间: {result['start']} 至 {result['end']}")
            else:
                st.caption("暂无可确定的净值区间.")
        for column, field in zip(st.columns(2), ("unit_nav", "accum_nav")):
            value = None if latest is None else latest[field]
            column.metric("区间末日" + NAV_LABELS[field], "暂无数据" if pd.isna(value) else f"{value:.4f}")


def show_chart(result: dict | None) -> None:
    figure = go.Figure()
    if result is None or result["nav"].empty:
        figure.add_annotation(
            text="选择基金和时间范围后点击查询" if result is None else "该区间暂无净值数据",
            x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False,
            font=dict(color="#64748B", size=14),
        )
    else:
        nav = result["nav"]
        for field in result["selected"]:
            figure.add_trace(go.Scatter(
                x=nav.nav_date, y=nav[field], name=NAV_LABELS[field], mode="lines", connectgaps=False,
                line=dict(color=NAV_COLORS[field], width=2),
                hovertemplate="%{y:.4f}<extra>%{fullData.name}</extra>",
            ))
        st.caption(f"当前展示: {result['name']} · {result['code']} | {result['start']} 至 {result['end']}")
    figure.update_layout(
        template="plotly_white", paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
        font=dict(family="Segoe UI, Microsoft YaHei, sans-serif", size=12, color="#64748B"),
        height=440, xaxis_title="日期", yaxis_title="净值", xaxis_type="date",
        hovermode="x unified", hoverlabel=dict(bgcolor="#FFFFFF", bordercolor="#E2E8F0"),
        legend=dict(orientation="h", y=1.02, x=0, font=dict(color="#1F2937")),
        margin=dict(l=10, r=10, t=35, b=10),
    )
    figure.update_xaxes(
        gridcolor="#EDF2F7", zeroline=False, showline=False,
        tickformatstops=[
            dict(dtickrange=[None, "M1"], value="%m-%d"),
            dict(dtickrange=["M1", "M12"], value="%Y-%m"),
            dict(dtickrange=["M12", None], value="%Y"),
        ],
        hoverformat="%Y-%m-%d", title_font=dict(size=12), automargin=True,
    )
    figure.update_yaxes(
        gridcolor="#EDF2F7", zeroline=False, showline=False, title_font=dict(size=12), automargin=True,
    )
    st.plotly_chart(figure, width="stretch", theme=None, key="nav_chart")


def show_performance(result: dict | None) -> None:
    with st.container(border=True, key="performance_panel"):
        st.subheader("绩效分析")
        if result is None or result["nav"].empty:
            st.caption("查询有效区间后展示七项绩效指标.")
            return
        if result["metrics"] is None:
            st.warning(f"指标暂不可计算: {result['metrics_error']}")
            return
        metrics = result["metrics"]
        items = list(METRICS.items())
        for row in (items[:3], items[3:]):
            for column, (key, (label, fmt)) in zip(st.columns(len(row)), row):
                value = metrics["metrics"][key]
                column.metric(label, "无法计算" if value is None else format(value, fmt), border=True)
        if metrics["short_period"]:
            st.warning("该区间不足一年, 年化结果可能被放大, 请结合实际区间查看.")
        with st.expander("指标计算口径"):
            st.caption(f"实际区间: {metrics['start_date']} 至 {metrics['end_date']}, {metrics['nav_count']} 条交易日复权净值.")
            st.caption(f"年化系数 {metrics['periods_per_year']}, 无风险利率 {metrics['risk_free_rate']:.1%}, 目标收益率 {metrics['target_return']:.1%}.")
            st.caption(f"排除 {metrics['excluded_non_trading_rows']} 条非交易日披露. 无法计算表示样本或风险分母不满足计算条件.")


def _show_managers(details: dict | None) -> None:
    st.subheader("基金经理")
    if details is None:
        st.caption("查询后展示本地经理资料.")
    elif "managers" in details["errors"]:
        st.warning(f"经理资料暂不可用: {details['errors']['managers']}")
    elif not details["managers"]:
        st.info("暂无本地基金经理资料.")
    else:
        st.caption("按任职日期倒序. 离任日期未提供不代表仍在任职.")
        # 多任期列表在卡片内滚动, 避免拉长整个分析页面.
        with st.container(height=340, border=False):
            for index, manager in enumerate(details["managers"]):
                with st.expander(f"{manager['name']} | 任职 {_date_text(manager['begin_date'])}", expanded=index == 0):
                    st.write(f"任职日期: {_date_text(manager['begin_date'])}")
                    st.write(f"离任日期: {'未提供' if pd.isna(manager['end_date']) else _date_text(manager['end_date'])}")
                    education = "未提供" if pd.isna(manager["edu"]) else manager["edu"]
                    st.caption(f"学历: {education} | 公告日期: {_date_text(manager['ann_date'])}")
                    st.write("暂无简历." if pd.isna(manager["resume"]) else manager["resume"])


def _show_asset(details: dict | None) -> None:
    st.subheader("资产信息")
    if details is None:
        st.caption("查询后展示最新本地资产披露.")
    elif "asset" in details["errors"]:
        st.warning(f"资产资料暂不可用: {details['errors']['asset']}")
    elif details["asset"] is None:
        st.info("暂无本地资产披露数据.")
    else:
        asset = details["asset"]
        st.caption(f"资产日期: {_date_text(asset['nav_date'])} | 公告日期: {_date_text(asset['ann_date'])}")
        for field, label in (("net_asset", "资产净值"), ("total_netasset", "合计资产净值")):
            value = asset[field]
            st.metric(label, "未提供" if pd.isna(value) else f"{value:,.2f}")
        st.caption("金额按源数据原值展示, 单位及合计范围待核对, 不换算为亿元, 两项不可相加.")


def show_details(result: dict | None) -> None:
    details = None if result is None else result["details"]
    if details is not None:
        st.caption(f"资料对应: {result['name']} · {details['code']}, 采用本地最新资料, 不受净值区间限制.")
    with st.container(key="details_row"):
        left, right = st.columns(2, gap="medium")
        with left, st.container(border=True, key="manager_panel"):
            _show_managers(details)
        with right, st.container(border=True, key="asset_panel"):
            _show_asset(details)


def show_data_notes() -> None:
    st.header("数据说明")
    with st.container(border=True, key="notes_nav"):
        st.subheader("数据范围与净值")
        st.write("页面读取本地已下载数据, 不提供实时行情, 不自动请求 Tushare. 快捷区间以所选基金的本地最新净值日为基准, 全部范围取实际数据起止日.")
        st.write("单位净值, 累计净值和复权单位净值直接使用来源数据. 多条曲线保留原始量级, 缺失值保持断点, 不插值或归一化.")
    with st.container(border=True, key="notes_metrics"):
        st.subheader("绩效指标")
        st.write("七项指标均通过现有服务按交易日复权净值计算, 不随所选曲线类型变化. 实际计算区间, 样本数, 年化系数, 无风险利率和目标收益率以查询结果中的计算口径为准.")
        st.write("区间不足一年时保留年化提示. 当前使用 SSE 日历, 特殊基金的交易日适用性仍需单独核查. 缺少有效净值或完整日历时不补造指标.")
    with st.container(border=True, key="notes_details"):
        st.subheader("经理与资产资料")
        st.write("同一经理姓名和任期采用最新公告记录, 离任日期未提供不代表仍在任职. 资产展示本地最新披露日期及原始金额, 不假设单位, 不换算为亿元, 不将两种口径相加.")
        st.write("经理与资产资料不按净值查询区间截取, 不能直接视为历史时点可得信息. CSV 对照模式暂不提供这两类资料.")
