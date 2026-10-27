"""基金对比页面, 查询和计算交给 FundService, 这里只编排控件与展示."""

import logging
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.bootstrap import create_service
from app.components import METRICS, choice_code, remember_query, show_date_filters, show_query_source, style_chart


logger = logging.getLogger(__name__)
COLORS = ("#2563EB", "#0D9488", "#7C3AED", "#EA580C", "#DB2777", "#0891B2", "#65A30D", "#475569")


def _common_bounds(service, codes: list[str]) -> tuple[tuple[date, date] | None, str | None]:
    """仅读取日期控件的共同上下界, 正式对齐仍由服务在提交时执行."""
    if len(codes) < 2:
        return None, None
    if st.session_state.get("data_source") == "ifind":
        return (date(1900, 1, 1), date.today()), None
    try:
        spans = []
        for code in codes:
            nav = service.get_nav(code, nav_types=["adj_nav"])
            dates = nav.loc[nav.nav_date.le(pd.Timestamp(date.today())), "nav_date"]
            if dates.empty:
                raise ValueError(f"{code}: 暂无可查询的本地净值")
            spans.append((dates.min().date(), dates.max().date()))
        bounds = max(span[0] for span in spans), min(span[1] for span in spans)
        if bounds[0] > bounds[1]:
            raise ValueError("所选基金没有共同净值区间, 请调整基金选择")
        return bounds, None
    except (ValueError, OSError, UnicodeError) as exc:
        logger.warning("Comparison bounds unavailable: %s", exc)
        return None, str(exc)


def _run_comparison(service, conditions: dict, bounds_error: str | None) -> None:
    """提交后一次性保存条件和结果, 失败时清除旧结果并保留反馈."""
    st.session_state.comparison_result = None
    st.session_state.comparison_feedback = None
    try:
        if len(conditions["codes"]) < 2:
            raise ValueError("请至少选择两只不同的基金")
        if bounds_error:
            raise ValueError(bounds_error)
        all_online = conditions["source"] == "ifind" and conditions["range"] == "全部"
        if (conditions["start"] is None and not all_online) or conditions["end"] is None:
            raise ValueError("请选择开始日期和结束日期")
        with st.spinner("正在比较基金数据..."):
            result = service.get_comparison(conditions["codes"], conditions["start"], conditions["end"])
        result["conditions"] = {**conditions, "codes": list(conditions["codes"])}
        result["source"] = conditions["source"]
        st.session_state.comparison_result = result
    except ValueError as exc:
        logger.warning("Comparison rejected: %s", exc)
        st.session_state.comparison_feedback = str(exc)
    except (OSError, UnicodeError):
        logger.exception("Comparison data read failed")
        st.session_state.comparison_feedback = "无法读取本地数据, 请检查数据源配置和数据库状态."


def _show_chart(result: dict | None) -> None:
    figure = go.Figure()
    if result is None:
        figure.add_annotation(
            text="选择两只或多只基金后开始对比", x=0.5, y=0.5,
            xref="paper", yref="paper", showarrow=False, font=dict(color="#64748B", size=14),
        )
    else:
        curve = result["cumulative_returns"]
        requested = result["conditions"]
        st.caption(f"实际比较区间: {result['start_date']} 至 {result['end_date']} | {len(result['funds'])} 只基金 · {len(curve)} 个开市日")
        requested_dates = tuple(None if requested[key] is None else requested[key].isoformat()
                                for key in ("start", "end"))
        if requested_dates != (result["start_date"], result["end_date"]):
            start_text = requested["start"] or "成立以来"
            st.caption(f"已提交区间: {start_text} 至 {requested['end']}, 按共同覆盖范围内的开市日确定实际区间.")
        for index, (code, name) in enumerate(result["funds"].items()):
            figure.add_trace(go.Scatter(
                x=curve.nav_date, y=curve[code], name=f"{code} {name}", connectgaps=False,
                mode="lines+markers" if curve[code].isna().any() else "lines", marker=dict(size=4),
                line=dict(color=COLORS[index % len(COLORS)], width=2),
                hovertemplate="%{y:.2%}<extra>%{fullData.name}</extra>",
            ))
        if curve.iloc[:, 1:].isna().any().any():
            st.caption("部分基金存在缺失记录, 已保留断点; 稀疏序列显示实际观测点.")
    style_chart(figure, yaxis_title="累计收益率")
    figure.update_yaxes(tickformat=".0%", zeroline=True, zerolinecolor="#CBD5E1")
    st.plotly_chart(figure, width="stretch", theme=None, key="comparison_chart")


def _metric_colors(row: pd.Series) -> pd.Series:
    """按同行已显示的数值比较, 最优红色, 最差绿色, 不改变指标结果."""
    styles = pd.Series("", index=row.index)
    # 去掉百分号仅供颜色比较, 无法计算及同显示精度的数值不强行区分.
    values = pd.to_numeric(row.drop("指标").str.rstrip("%"), errors="coerce")
    if values.nunique() < 2:
        return styles
    lower_is_better = row["指标"] in {"年化波动", "年化下行风险", "最大回撤"}
    best, worst = (values.min(), values.max()) if lower_is_better else (values.max(), values.min())
    styles.loc[values.index[values.eq(best)]] = "color: #DC2626"
    styles.loc[values.index[values.eq(worst)]] = "color: #15803D"
    return styles


def _show_performance(result: dict | None) -> None:
    with st.container(border=True, key="comparison_performance"):
        st.subheader("绩效对照")
        if result is None:
            st.caption("提交对比后展示同一区间的七项绩效指标.")
            return
        table = {"指标": [label for label, _ in METRICS.values()]}
        for code, name in result["funds"].items():
            payload = result["metrics"][code]
            values = {} if payload is None else payload["metrics"]
            table[f"{code} {name}"] = [
                "无法计算" if values.get(key) is None else format(values[key], fmt)
                for key, (_, fmt) in METRICS.items()
            ]
        st.dataframe(
            pd.DataFrame(table).style.apply(_metric_colors, axis=1),
            hide_index=True, width="stretch", height="auto",
            column_config={"指标": st.column_config.TextColumn("指标", pinned=True, width="small")},
        )
        st.caption("同行最优值为红色, 最差值为绿色; 中间值, 全部相同或无法计算时保持原色. 收益和比率越高越优, 波动, 下行风险及最大回撤越低越优.")
        if len(result["funds"]) > 2:
            st.caption("基金较多时, 可在表格内横向滚动查看完整指标.")
        for code, error in result["errors"].items():
            st.warning(f"{result['funds'][code]} ({code}): {error}")
        if any(item and item["short_period"] for item in result["metrics"].values()):
            st.warning("该区间不足一年, 年化结果可能被放大, 请结合实际区间查看.")
        with st.expander("指标计算口径"):
            st.caption("累计收益与指标沿用 SSE 开市日口径, 特殊基金的实际适用日历需单独核查. 无法计算表示样本或风险分母不满足计算条件.")
            for code, payload in result["metrics"].items():
                if payload is None:
                    continue
                st.markdown(f"**{result['funds'][code]} · {code}**")
                st.caption(f"实际区间: {payload['start_date']} 至 {payload['end_date']}, {payload['nav_count']} 条交易日复权净值, 排除 {payload['excluded_non_trading_rows']} 条非交易日披露.")
                st.caption(f"年化系数 {payload['periods_per_year']}, 无风险利率 {payload['risk_free_rate']:.1%}, 目标收益率 {payload['target_return']:.1%}.")


def show_comparison_page() -> None:
    st.header("基金对比")
    st.caption("多基金选择 · 累计收益对比 · 同区间绩效")
    try:
        service = create_service(st.session_state.data_source)
        funds = service.list_available_funds()
    except (ValueError, OSError, UnicodeError) as exc:
        logger.exception("Comparison fund choices unavailable")
        st.error(f"无法读取本地基金列表或所选来源配置: {exc}")
        with st.container(border=True, key="comparison_panel"):
            _show_chart(None)
        return
    draft = st.session_state.setdefault("comparison_draft", {"codes": [], "range": "近1年"})
    # 草稿不属于控件, 导航切换后可恢复; 已提交结果独立保存.
    selected = st.session_state.get("comparison_codes", draft["codes"])
    st.session_state.comparison_codes = [code for code in selected if code in funds]
    draft["codes"] = list(st.session_state.comparison_codes)
    with st.container(border=True, key="comparison_search"):
        codes = st.multiselect(
            "选择对比基金", options=list(funds), key="comparison_codes", disabled=not funds,
            format_func=lambda code: f"{code} {funds[code]}", placeholder="按代码或名称选择两只及以上基金",
            on_change=remember_query, args=("codes", "comparison_codes", "comparison_draft"),
        )
        if st.session_state.data_source == "ifind":
            if "comparison_manual_codes" not in st.session_state:
                st.session_state.comparison_manual_codes = draft.get("manual_codes", "")
            manual = st.text_input("补充基金代码", key="comparison_manual_codes", placeholder="例如 003349,000001",
                                   on_change=remember_query, args=("manual_codes", "comparison_manual_codes", "comparison_draft"))
            extra = [choice_code(code) for code in manual.replace("，", ",").split(",") if code.strip()]
            codes = list(dict.fromkeys([*codes, *extra]))
        st.caption("选择至少 2 只基金. 比较时使用所选来源净值的共同覆盖区间.")
    bounds, bounds_error = _common_bounds(service, codes)
    with st.container(border=True, key="comparison_panel"):
        st.subheader("累计收益对比")
        conditions = show_date_filters(bounds, draft_key="comparison_draft", prefix="comparison_")
        conditions["codes"] = list(codes)
        conditions["source"] = st.session_state.data_source
        note_col, button_col = st.columns([4, 1], vertical_alignment="bottom")
        note_col.caption("基于复权净值, 统一从 0% 开始, 缺失记录不填充.")
        submitted = button_col.button("开始对比", type="primary", width="stretch", key="submit_comparison")
        if submitted:
            _run_comparison(service, conditions, bounds_error)
        result = st.session_state.get("comparison_result")
        if result is not None and any(result["conditions"][key] != conditions[key] for key in ("codes", "start", "end")):
            st.info("条件已修改, 当前仍展示上次对比结果. 点击开始对比后统一更新.")
        if bounds_error:
            st.error(bounds_error)
        feedback = st.session_state.get("comparison_feedback")
        if feedback and feedback != bounds_error:
            st.error(f"上次对比未成功: {feedback}")
        show_query_source(result)
        _show_chart(result)
    _show_performance(result)
