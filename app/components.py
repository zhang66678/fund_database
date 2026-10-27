"""基金分析页面的原生布局组件, 不直接操作数据库或计算绩效指标."""

from datetime import date
import os

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st


NAV_LABELS = {"unit_nav": "单位净值", "accum_nav": "累计净值", "adj_nav": "复权单位净值"}
NAV_COLORS = {"unit_nav": "#2563EB", "accum_nav": "#0D9488", "adj_nav": "#7C3AED"}
CHART_LABELS = {**NAV_LABELS, "cumulative_return": "累计收益率", "drawdown": "回撤"}
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


def remember_query(field: str, key: str, draft_key: str = "draft_query") -> None:
    # 编辑条件独立于控件 key 保存, 切换导航后不会被控件清理机制丢弃.
    st.session_state[draft_key][field] = st.session_state[key]


def _choose_fund() -> None:
    if st.session_state.fund_choice is not None:
        st.session_state.fund_code = st.session_state.fund_choice
        remember_query("code", "fund_code")


def _edit_code(funds: dict) -> None:
    remember_query("code", "fund_code")
    code = choice_code(st.session_state.fund_code)
    # 修改选择框不会触发其 on_change, 不会把手动输入反向覆盖.
    st.session_state.fund_choice = code if code in funds else None


def _change_source() -> None:
    """数据源切换后清除两页结果和日期, 基金代码与曲线选择继续保留."""
    for key in ("query_result", "query_feedback", "comparison_result", "comparison_feedback"):
        st.session_state.pop(key, None)
    for prefix, draft_key in (("", "draft_query"), ("comparison_", "comparison_draft")):
        for field in ("custom_start", "custom_end"):
            key = prefix + field
            st.session_state.pop(key, None)
            st.session_state.get(draft_key, {}).pop(key, None)


def show_navigation() -> str:
    with st.sidebar:
        with st.container(key="sidebar_brand"):
            st.markdown("### Fund Analytics")
            st.caption("基金研究与分析")
        with st.container(key="sidebar_navigation"):
            page = st.radio("导航", ("基金分析", "基金对比", "数据说明"), key="workspace_page", label_visibility="collapsed")
        st.divider()
        labels = {"tushare": "本地数据（CSV）" if os.environ.get("NAV_CSV") else "Tushare（本地数据库）",
                  "ifind": "iFinD（接口查询）"}
        source = st.radio("数据源", tuple(labels), format_func=labels.get, key="data_source",
                          on_change=_change_source)
        if source == "ifind":
            st.caption("提交查询后在线取数, 相同请求缓存 10 分钟, 不写入数据库.")
            st.caption("基金选项沿用本地名单, 也可手动输入代码. 绩效使用本地 SSE 日历.")
        else:
            st.caption("本地历史数据查询, 不自动下载新数据.")
    return page


def show_query_source(result: dict | None) -> None:
    if result is None:
        return
    if result.get("source") == "ifind":
        fetched = result.get("fetched_at")
        stamp = "" if not fetched else pd.Timestamp(fetched).tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S")
        st.caption(f"数据来源: iFinD | 净值获取时间: {stamp}（北京时间） | 计算日历: 本地 SSE")
    else:
        st.caption("数据来源: 本地 CSV" if os.environ.get("NAV_CSV") else "数据来源: Tushare（本地数据库）")


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
            format_func=lambda code: f"{code}  {funds[code]}", placeholder="按代码或名称选择基金",
            on_change=_choose_fund, disabled=not funds,
        )
        code = code_col.text_input(
            "基金代码", key="fund_code", placeholder="输入基金代码",
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


def show_date_filters(
    bounds: tuple[date, date] | None, *, draft_key: str = "draft_query", prefix: str = "",
) -> dict:
    """复用日期布局, 不同页面通过前缀和独立草稿保存各自条件."""
    draft = st.session_state[draft_key]
    range_key = prefix + "time_range"
    if range_key not in st.session_state:
        st.session_state[range_key] = draft["range"]
    label = st.segmented_control(
        "时间范围", RANGES, key=range_key, required=True, disabled=bounds is None, wrap=True,
        on_change=remember_query, args=("range", range_key, draft_key),
    )
    start, end = None, None
    if bounds is not None:
        if label == "自定义":
            defaults = range_dates("近1年", bounds)
            keys = (prefix + "custom_start", prefix + "custom_end")
            for column, key, title, default in zip(st.columns(2), keys, ("开始日期", "结束日期"), defaults):
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
                    format="YYYY-MM-DD", on_change=remember_query, args=(key, key, draft_key),
                )
            start, end = (st.session_state[key] for key in keys)
        else:
            start, end = range_dates(label, bounds)
        if st.session_state.get("data_source") == "ifind":
            if label == "全部":
                start = None  # 接口适配层查询成立日期后确定全历史起点.
            st.caption("iFinD 按所选日期查询. 快捷区间以今天为基准, 实际净值日期以查询结果为准.")
        else:
            st.caption(f"本地净值范围: {bounds[0]} 至 {bounds[1]}. 快捷区间以末日为基准, 含两端.")
    else:
        st.caption("选择基金后设置查询日期范围.")
    return {"range": label, "start": start, "end": end}


def show_filters(bounds: tuple[date, date] | None) -> tuple[dict, bool]:
    draft = st.session_state.draft_query
    conditions = show_date_filters(bounds)
    if "nav_types" not in st.session_state:
        st.session_state.nav_types = draft["selected"]
    curve_col, button_col = st.columns([4, 1], vertical_alignment="bottom")
    selected = curve_col.multiselect(
        "图表曲线", options=list(CHART_LABELS), format_func=CHART_LABELS.get, key="nav_types",
        on_change=remember_query, args=("selected", "nav_types"),
        help="净值使用左轴, 累计收益率使用右轴, 回撤展示在下方. 收益和回撤基于复权净值.",
    )
    submitted = button_col.button("查询", key="submit_query", type="primary", width="stretch")
    return {**conditions, "selected": list(selected)}, submitted


def _date_text(value) -> str:
    return "暂无数据" if pd.isna(value) else f"{value:%Y-%m-%d}"


def show_overview(result: dict | None) -> None:
    with st.container(border=True, key="fund_overview"):
        st.subheader("基金概览")
        latest = None
        if result is None:
            st.caption("提交查询后展示基金资料和查询区间末日净值.")
        else:
            if not result["nav"].empty:
                latest = result["nav"].iloc[-1]
            if result["start"] is not None:
                st.caption(f"对应净值日期: {_date_text(None if latest is None else latest.nav_date)} | 已提交区间: {result['start']} 至 {result['end']}")
            elif latest is not None:
                st.caption(f"实际净值范围: {_date_text(result['nav'].nav_date.iloc[0])} 至 {_date_text(latest.nav_date)}")
            else:
                st.caption("暂无可确定的净值区间.")
        details = {} if result is None else result["details"]
        basic = details.get("basic") or {}
        if result is not None and result.get("source") == "ifind":
            st.caption("iFinD 投资类型和业绩比较基准暂未接入.")
        if "basic" in details.get("errors", {}):
            st.warning(f"基础资料暂不可用: {details['errors']['basic']}")
        date_col, type_col = st.columns(2)
        date_col.markdown(f"**成立日期:** {_date_text(basic.get('found_date'))}")
        fund_type, benchmark = basic.get("fund_type"), basic.get("benchmark")
        type_col.markdown(f"**投资类型:** {'暂无数据' if pd.isna(fund_type) else fund_type}")
        st.markdown(f"**业绩比较基准:** {'暂无数据' if pd.isna(benchmark) else benchmark}")
        for column, field in zip(st.columns(2), ("unit_nav", "accum_nav")):
            value = None if latest is None else latest[field]
            column.metric("区间末日" + NAV_LABELS[field], "暂无数据" if pd.isna(value) else f"{value:.4f}")


def show_chart(result: dict | None) -> None:
    selected = [] if result is None else result["selected"]
    has_drawdown = result is None or "drawdown" in selected
    # 主图左轴放净值, 右轴放收益; 下方回撤区与主图联动日期缩放.
    figure = make_subplots(
        rows=2 if has_drawdown else 1, cols=1, shared_xaxes=True,
        specs=[[{"secondary_y": True}], [{}]] if has_drawdown else [[{"secondary_y": True}]],
        row_heights=[0.74, 0.26] if has_drawdown else [1], vertical_spacing=0.09,
    )
    if result is None or result["nav"].empty:
        figure.add_annotation(
            text="选择基金和时间范围后点击查询" if result is None else "该区间暂无净值数据",
            x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False,
            font=dict(color="#64748B", size=14),
        )
    else:
        nav = result["nav"]
        for field in selected:
            if field not in NAV_LABELS:
                continue
            figure.add_trace(go.Scatter(
                x=nav.nav_date, y=nav[field], name=NAV_LABELS[field], mode="lines", connectgaps=False,
                line=dict(color=NAV_COLORS[field], width=2),
                hovertemplate="%{y:.4f}<extra>%{fullData.name}</extra>",
            ), row=1, col=1, secondary_y=False)
        series = result.get("performance_series")
        if series is not None and not series.empty:
            if "cumulative_return" in selected:
                figure.add_trace(go.Scatter(
                    x=series.nav_date, y=series.cumulative_return, name="累计收益率",
                    mode="lines+markers" if series.cumulative_return.isna().any() else "lines", marker=dict(size=4),
                    connectgaps=False, line=dict(color="#D97706", width=2, dash="dash"),
                    hovertemplate="%{y:.2%}<extra>%{fullData.name}</extra>",
                ), row=1, col=1, secondary_y=True)
            if has_drawdown:
                figure.add_trace(go.Scatter(
                    x=series.nav_date, y=series.drawdown, name="回撤",
                    mode="lines+markers" if series.drawdown.isna().any() else "lines", marker=dict(size=4),
                    connectgaps=False, line=dict(color="#DC5967", width=1.5),
                    fill="tozeroy", fillcolor="rgba(220, 89, 103, 0.14)",
                    hovertemplate="%{y:.2%}<extra>%{fullData.name}</extra>",
                ), row=2, col=1)
            if series.iloc[:, 1:].isna().any().any():
                st.caption("收益与回撤存在缺失开市日, 已保留断点; 回撤仅按区间内已观测净值峰值计算.")
        elif result.get("series_error"):
            st.warning(f"收益与回撤曲线暂不可绘制: {result['series_error']}")
        st.caption(f"当前展示: {result['name']} · {result['code']} | 实际净值日期: {_date_text(nav.nav_date.iloc[0])} 至 {_date_text(nav.nav_date.iloc[-1])}")
    style_chart(figure)
    figure.update_layout(height=560 if has_drawdown else 440)
    figure.update_xaxes(type="date", title_text=None)
    # 日期轴放在主图和回撤区之间, 回撤从日期轴下方的 0% 线向下展开.
    figure.update_xaxes(title_text="日期", title_standoff=4, showticklabels=True, row=1, col=1)
    if has_drawdown:
        figure.update_xaxes(showticklabels=False, row=2, col=1)
    figure.update_yaxes(title_text="净值", tickformat=".4f", row=1, col=1, secondary_y=False)
    figure.update_yaxes(
        title_text="累计收益率", tickformat=".2%", showgrid=False,
        visible="cumulative_return" in selected, title_font_color="#D97706", tickfont_color="#D97706",
        row=1, col=1, secondary_y=True,
    )
    if has_drawdown:
        figure.update_yaxes(
            title_text="回撤", tickformat=".2%", rangemode="tozero", zeroline=True,
            zerolinecolor="#CBD5E1", title_font_color="#DC5967", autorangeoptions=dict(maxallowed=0),
            row=2, col=1,
        )
    if any(field in selected for field in ("cumulative_return", "drawdown")):
        st.caption("累计收益以区间首个有效开市日复权净值为 0%; 回撤从区间内已观测峰值向下绘制, 与最大回撤卡片的正值口径方向相反.")
    st.plotly_chart(figure, width="stretch", theme=None, key="nav_chart")


def style_chart(figure: go.Figure, yaxis_title: str = "净值") -> None:
    """统一单基金和对比页面的图表风格, 不改变传入数据."""
    figure.update_layout(
        template="plotly_white", paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
        font=dict(family="Segoe UI, Microsoft YaHei, sans-serif", size=12, color="#64748B"),
        height=440, xaxis_title="日期", yaxis_title=yaxis_title, xaxis_type="date",
        hovermode="x unified", hoverlabel=dict(bgcolor="#FFFFFF", bordercolor="#E2E8F0"),
        legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, font=dict(color="#1F2937")),
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
        # 半宽卡片按 3, 2, 2 排列, 保留七项指标及原有计算口径.
        for row in (items[:3], items[3:5], items[5:]):
            for column, (key, (label, fmt)) in zip(st.columns(len(row)), row):
                value = metrics["metrics"][key]
                column.metric(label, "无法计算" if value is None else format(value, fmt), border=True)
        if metrics["short_period"]:
            st.warning("该区间不足一年, 年化结果可能被放大, 请结合实际区间查看.")
        with st.expander("指标计算口径"):
            st.caption(f"实际区间: {metrics['start_date']} 至 {metrics['end_date']}, {metrics['nav_count']} 条交易日复权净值.")
            st.caption(f"年化系数 {metrics['periods_per_year']}, 无风险利率 {metrics['risk_free_rate']:.1%}, 目标收益率 {metrics['target_return']:.1%}.")
            st.caption(f"排除 {metrics['excluded_non_trading_rows']} 条非交易日披露. 无法计算表示样本或风险分母不满足计算条件.")


def show_dividends(result: dict | None) -> None:
    with st.container(border=True, key="dividend_panel"):
        st.subheader("分红记录")
        if result is None:
            st.caption("查询后展示分红记录.")
            return
        details = result["details"]
        if "dividends" in details["errors"]:
            st.warning(f"分红记录暂不可用: {details['errors']['dividends']}")
            return
        records = details.get("dividends")
        if records is None:
            st.caption("点击查询加载分红记录.")
            return
        if not records:
            st.info("暂无本地分红记录, 不代表基金从未分红.")
            return
        st.caption("按除息日倒序展示本地全部披露, 不受净值区间限制. 同一事件可能有不同方案或公告版本.")
        # 折叠列表在卡片内滚动, 保留每条披露, 不累加不同版本的派现金额.
        with st.container(height=340, border=False):
            for index, record in enumerate(records):
                cash = record["div_cash"]
                amount = "金额未提供" if pd.isna(cash) else f"{cash:.10f}".rstrip("0").rstrip(".") + " 元/份"
                progress = "进度未提供" if pd.isna(record["div_proc"]) else record["div_proc"]
                with st.expander(f"{_date_text(record['ex_date'])} | {amount} | {progress}", expanded=index == 0):
                    st.write(f"权益登记日: {_date_text(record['record_date'])}")
                    st.write(f"派息日: {_date_text(record['pay_date'])}")
                    st.caption(f"公告日期: {_date_text(record['ann_date'])} | 方案进度: {progress}")


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
        st.caption(f"资料对应: {result['name']} · {details['code']}, 采用所选来源的当前资料, 不受净值区间限制.")
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
        st.write("侧栏可切换 Tushare 本地数据库和 iFinD HTTP 接口. 本地模式不请求 Tushare; iFinD 模式在提交查询后取数, 相同请求缓存 10 分钟, 不写入数据库. 切换来源会清除两页旧结果.")
        st.write("本地快捷区间以最新净值日为基准; iFinD 快捷区间以今天为基准, 全部范围从接口返回的成立日期开始查询. 实际净值日期以结果为准, 不将当天尚未披露的净值视作实时行情.")
        st.write("单位净值, 累计净值和复权单位净值直接使用来源数据. 多条曲线保留原始量级, 缺失值保持断点, 不插值或归一化.")
        st.write("单基金图上方左轴为原始净值, 右轴为区间累计收益率, 下方为回撤. 收益采用区间首个开市日复权净值为基期; 回撤相对所选区间内已观测峰值, 不含区间前历史峰值, 以负百分比展示. 两条派生曲线使用 SSE 开市日, 不填充缺失记录.")
        st.write("概览资料来自所选来源, 不按净值区间还原历史资料或历史基准. iFinD 首版仅接入简称和成立日期, 投资类型及基准暂未接入; 两源复权方法仍需进一步核对, 不保证指标结果完全一致.")
        st.write("基金对比使用共同开市日的复权净值计算累计收益, 从相同起点的 0% 开始. 原始净值不修改, 中间缺失保留断点, 实际比较区间以结果标注为准.")
    with st.container(border=True, key="notes_metrics"):
        st.subheader("绩效指标")
        st.write("七项指标均通过现有服务按交易日复权净值计算, 不随所选曲线类型变化. 实际计算区间, 样本数, 年化系数, 无风险利率和目标收益率以查询结果中的计算口径为准.")
        st.write("区间不足一年时保留年化提示. 两种来源均使用本地 SSE 日历, 缺少或未覆盖查询日期时仅展示净值并提示计算失败. 特殊基金的交易日适用性仍需单独核查.")
    with st.container(border=True, key="notes_details"):
        st.subheader("分红, 经理与资产资料")
        st.write("分红展示本地全部披露, 按除息日和公告日期倒序, 金额按每份派现金额原值展示. 同一事件可能有不同方案进度或公告版本, 不将记录条数当作实际分红次数, 不直接累加各版本金额. 无本地记录不代表从未分红.")
        st.write("同一经理姓名和任期采用最新公告记录, 离任日期未提供不代表仍在任职. 资产展示本地最新披露日期及原始金额, 不假设单位, 不换算为亿元, 不将两种口径相加.")
        st.write("分红, 经理与资产资料不按净值查询区间截取, 不能直接视为历史时点可得信息. CSV 对照模式和 iFinD 首版暂不提供这三类资料, 不自动用其他来源补齐.")
