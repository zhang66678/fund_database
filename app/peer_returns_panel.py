"""涨幅与同类比较展示, 只格式化服务结果, 不查询或计算收益."""

from html import escape

import streamlit as st


PERIOD_TABS = (("stages", "阶段涨幅"), ("quarters", "季度涨幅"), ("years", "年度涨幅"))


def _return_text(value) -> str:
    if value is None:
        return '<span class="peer-missing">—</span>'
    color = "positive" if value > 0 else "negative" if value < 0 else "neutral"
    return f'<span class="peer-return peer-{color}">{value:+.2%}</span>'


def _quartile(item: dict) -> str:
    quartile = item["quartile"]
    if quartile is None:
        return '<span class="peer-missing">—</span>'
    label = escape(item["quartile_label"])
    cells = []
    for level in range(1, 5):
        state = "active" if level == quartile else "filled" if level > quartile else "empty"
        cells.append(f'<span class="peer-quartile-{state}"></span>')
    return (f'<div class="peer-quartile" role="img" aria-label="第{quartile}四分位, {label}">'
            f'{"".join(cells)}</div><span class="peer-quartile-label">{label}</span>')


def _returns_table(periods: list[dict], label: str) -> str:
    """按指标为行, 周期为列渲染, 原始文字全部转义后进入 HTML."""
    headings = []
    for item in periods:
        partial = f'<small>截至 {escape(item["end_date"] or "日期未提供")}</small>' if item["partial"] else ""
        headings.append(f'<th scope="col">{escape(item["label"])}{partial}</th>')
    rows = []
    for field, title in (("fund_return", label), ("peer_average", "同类平均"),
                         ("rank", "同类排名"), ("quartile", "四分位排名")):
        cells = []
        for item in periods:
            interval = f'{item["start_date"] or "—"} 至 {item["end_date"] or "—"}'
            hint = escape(f'{interval}; {item["reason"]}' if item["reason"] else interval, quote=True)
            if field == "quartile":
                value = _quartile(item)
            elif field == "rank":
                value = "—" if item["rank"] is None else f'{item["rank"]:,} / {item["sample_count"]:,}'
            else:
                value = _return_text(item[field])
            cells.append(f'<td title="{hint}">{value}</td>')
        rows.append(f'<tr><th scope="row">{escape(title)}</th>{"".join(cells)}</tr>')
    return (f'<div class="peer-table-scroll" tabindex="0" role="region" aria-label="{escape(label)}同类比较">'
            f'<table class="peer-returns-table"><caption class="peer-sr-only">{escape(label)}与同类比较</caption>'
            f'<thead><tr><th scope="col">统计项</th>{"".join(headings)}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def show_peer_returns(result: dict | None) -> None:
    with st.container(border=True, key="peer_returns_panel"):
        st.subheader("涨幅与同类比较")
        if result is None or result["nav"].empty:
            st.caption("查询基金后展示阶段, 季度和年度涨幅及同类排名.")
            return
        if result.get("source") == "ifind":
            st.info("iFinD 在线模式暂不提供同类基金批量比较, 请切换至 Tushare 本地数据源查看.")
            return
        if result.get("peer_returns_error"):
            st.warning(f"涨幅与同类比较暂不可用: {result['peer_returns_error']}")
            return
        data = result.get("peer_returns")
        if data is None:
            st.caption("点击查询加载涨幅与同类比较.")
            return
        st.caption(f"{result['name']} · {result['code']} · 统计截至 {data['as_of']}, 不受上方图表区间影响.")
        if data["category"]:
            st.caption(f"同类: {data['category']} · 分类来源: iFinD · 本基金分类获取日期: {data['category_snapshot_date']}")
        else:
            st.info("本基金暂无有效二级分类, 仅展示可计算的基金涨幅, 同类平均和排名暂缺.")
        tabs = st.tabs([label for _, label in PERIOD_TABS])
        for tab, (key, label) in zip(tabs, PERIOD_TABS):
            with tab:
                periods = data["groups"][key]
                st.html(_returns_table(periods, label))
        st.caption("净值来源: Tushare 本地数据. 同类平均为有效份额的等权平均, 含本基金; 各周期样本数可能不同. 四分位由优到劣为优秀, 良好, 一般, 不佳.")
        st.caption("涨幅由本地复权净值计算, 不年化. 当前季度和年度截至最新净值日; 数据不足显示 —.")
