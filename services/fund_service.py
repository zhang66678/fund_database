"""整理 demo 查询输入和展示列, 不依赖具体存储或绘图库."""

import re
from collections.abc import Sequence
from datetime import date

import pandas as pd

from database.repository import FundRepository, NAV_COLUMNS
from services.fund_metrics import calculate_metrics


def _normalize_code(ts_code: str) -> str:
    """净值和基金资料查询共用代码校验, 支持六位代码和 .OF 后缀."""
    code = ts_code.strip().upper()
    if re.fullmatch(r"[0-9]{6}", code):
        code += ".OF"
    if not re.fullmatch(r"[0-9]{6}\.OF", code):
        raise ValueError("请输入六位基金代码, 例如 000001 或 000001.OF")
    return code


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

    def get_details(self, ts_code: str) -> dict:
        """提供经理任职记录及最新资产披露, 一类资料失败不影响另一类."""
        code = _normalize_code(ts_code)
        result = {"code": code, "managers": [], "asset": None, "errors": {}}
        for key, query in (("managers", self._repository.get_managers), ("asset", self._repository.get_assets)):
            try:
                frame = query(code)
                result[key] = frame.to_dict("records") if key == "managers" else (
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
