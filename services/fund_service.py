"""整理 demo 查询输入和展示列, 不依赖具体存储或绘图库."""

import logging
import re
from collections.abc import Sequence
from datetime import date

import pandas as pd

from database.repository import FundRepository, NAV_COLUMNS
from services.fund_metrics import calculate_metrics


logger = logging.getLogger(__name__)


def _normalize_code(ts_code: str) -> str:
    """净值和基金资料查询共用代码校验, 支持六位代码和 .OF 后缀."""
    code = ts_code.strip().upper()
    if re.fullmatch(r"[0-9]{6}", code):
        code += ".OF"
    if not re.fullmatch(r"[0-9]{6}\.OF", code):
        raise ValueError("请输入六位基金代码, 例如 000001 或 000001.OF")
    return code


def _aligned_prices(navs: dict[str, pd.DataFrame], calendar: pd.DataFrame) -> pd.DataFrame:
    """单基金及多基金共用 SSE 开市日对齐, 保留中间缺口, 不填充净值."""
    first = max(nav.nav_date.min() for nav in navs.values())
    last = min(nav.nav_date.max() for nav in navs.values())
    if first > last:
        raise ValueError("所选基金在查询区间内没有共同净值区间")

    cal = calendar.copy()
    cal["cal_date"] = pd.to_datetime(cal.cal_date)
    cal = cal.set_index("cal_date").sort_index()
    required = pd.date_range(first, last)
    if cal.index.has_duplicates or not required.isin(cal.index).all():
        raise ValueError("交易日历未完整覆盖共同净值区间")
    flags = pd.to_numeric(cal.loc[required, "is_open"], errors="raise")
    if not flags.isin([0, 1]).all():
        raise ValueError("交易日历开闭市标记无效")
    open_dates = flags.index[flags.eq(1)]
    if len(open_dates) < 2:
        raise ValueError("共同区间至少需要两个开市日")

    prices = pd.concat(
        [nav.set_index("nav_date").adj_nav.rename(code) for code, nav in navs.items()], axis=1,
    ).reindex(open_dates)
    # 无效值在派生曲线中作为缺口保留, 原始记录仍交给现有指标入口检查.
    prices = prices.where(prices.gt(0) & prices.lt(float("inf")))
    invalid_edges = prices.iloc[[0, -1]].isna().any()
    if invalid_edges.any():
        codes = ", ".join(invalid_edges.index[invalid_edges])
        raise ValueError(f"共同区间首末开市日缺少有效复权净值: {codes}, 请调整区间")
    return prices


class FundService:
    """接收数据访问接口, CSV 和未来数据库实现可以使用同一服务."""

    def __init__(self, repository: FundRepository) -> None:
        self._repository = repository

    def list_available_funds(self) -> dict[str, str]:
        """提供本地基金选择项, 不读取文件或依赖页面组件."""
        return self._repository.list_available_funds()

    def get_metrics(self, ts_code: str, start_date: date, end_date: date) -> dict:
        """指标始终采用复权净值, 不受页面勾选的曲线类型影响."""
        nav = self.get_nav(ts_code, start_date, end_date, ["adj_nav"])
        return calculate_metrics(nav, self._repository.get_calendar())

    def get_performance_series(
        self, ts_code: str, start_date: date | None = None, end_date: date | None = None,
    ) -> pd.DataFrame:
        """返回区间累计收益和负值回撤, 基于复权净值, 缺失开市日保留 NaN.

        累计收益以区间首个开市日为基期. 回撤相对区间内已观测峰值,
        与最大回撤指标的正值展示方向相反, 不引入区间之前的峰值.
        """
        code = _normalize_code(ts_code)
        nav = self.get_nav(code, start_date, end_date, ["adj_nav"])
        if nav.empty:
            return pd.DataFrame(columns=["nav_date", "cumulative_return", "drawdown"])
        prices = _aligned_prices({code: nav}, self._repository.get_calendar())[code]
        return pd.DataFrame({
            "cumulative_return": prices / prices.iloc[0] - 1,
            "drawdown": prices / prices.cummax() - 1,
        }).reset_index(names="nav_date")

    def get_comparison(
        self, ts_codes: Sequence[str], start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict:
        """返回多基金共同区间, 累计收益表及原口径的逐只指标.

        代码规范化后去重, 至少需要两只. 起止日取请求范围内共同覆盖的
        首末 SSE 开市日, 以 ISO 日期字符串返回, 不为避开内部缺口而缩短区间.
        cumulative_returns 为 nav_date 及各基金代码列, 收益为小数, 基期为 0.
        metrics 保留 get_metrics 的完整结果; 不可计算时为 None, 原因在 errors.
        """
        codes = list(dict.fromkeys(_normalize_code(code) for code in ts_codes))
        if len(codes) < 2:
            raise ValueError("请至少选择两只不同的基金")
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("开始日期不能晚于结束日期")
        navs = {}
        for code in codes:
            nav = self.get_nav(code, start_date, end_date, ["adj_nav"])
            if nav.empty:
                raise ValueError(f"{code}: 所选区间没有净值数据")
            navs[code] = nav
        prices = _aligned_prices(navs, self._repository.get_calendar())
        first, last = prices.index[0].date(), prices.index[-1].date()
        cumulative = prices.div(prices.iloc[0]).sub(1).reset_index(names="nav_date")

        metrics, errors = {}, {}
        for code in codes:
            try:
                metrics[code] = self.get_metrics(code, first, last)
            except (ValueError, OSError, UnicodeError) as exc:
                logger.warning("Comparison metrics unavailable: %s: %s", code, exc)
                metrics[code], errors[code] = None, str(exc)
        names = self.list_available_funds()
        return {
            "requested_start_date": None if start_date is None else start_date.isoformat(),
            "requested_end_date": None if end_date is None else end_date.isoformat(),
            "start_date": first.isoformat(), "end_date": last.isoformat(),
            "funds": {code: names.get(code, "名称未提供") for code in codes},
            "cumulative_returns": cumulative, "metrics": metrics, "errors": errors,
        }

    def get_details(self, ts_code: str) -> dict:
        """提供基础资料, 经理任职, 最新资产及分红披露, 各类读取互不影响."""
        code = _normalize_code(ts_code)
        result = {"code": code, "basic": None, "managers": [], "asset": None,
                  "dividends": [], "errors": {}}
        for key, query in (("basic", self._repository.get_basic),
                           ("managers", self._repository.get_managers), ("asset", self._repository.get_assets),
                           ("dividends", self._repository.get_dividends)):
            try:
                frame = query(code)
                result[key] = frame.to_dict("records") if key in ("managers", "dividends") else (
                    None if frame.empty else frame.iloc[-1].to_dict())
            except (ValueError, OSError, UnicodeError) as exc:
                # 返回具体资料的失败原因, 由页面展示并记录日志, 不隐瞒读取错误.
                result["errors"][key] = str(exc)
        return result

    def get_nav(
        self, ts_code: str, start_date: date | None = None,
        end_date: date | None = None, nav_types: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        """返回日期和所选净值列, 未指定类型时返回全部三种净值.

        显式传入空列表时报错. 没有数据时保留空表, 不补造净值.
        """
        code = _normalize_code(ts_code)
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("开始日期不能晚于结束日期")

        # 使用字段名作为类型, 中文标签由后续页面负责显示.
        selected = list(dict.fromkeys(NAV_COLUMNS[1:] if nav_types is None else nav_types))
        if not selected:
            raise ValueError("请至少选择一种净值")
        if any(column not in NAV_COLUMNS[1:] for column in selected):
            raise ValueError("仅支持 unit_nav, accum_nav, adj_nav")

        nav = self._repository.get_nav(code, start_date, end_date)
        return nav.loc[:, ["nav_date", *selected]].copy()
