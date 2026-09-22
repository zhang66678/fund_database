"""定义只读查询约定, 让业务层不依赖 CSV 或具体数据库."""

from datetime import date
from typing import Protocol

import pandas as pd


NAV_COLUMNS = ["nav_date", "unit_nav", "accum_nav", "adj_nav"]


class FundRepository(Protocol):
    """不同存储实现遵守相同方法和返回约定, 由调用方注入具体实现."""

    def get_nav(
        self, ts_code: str, start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        """按包含两端的日期区间查询, 无记录返回同结构的空表.

        输入为完整基金代码. 返回 NAV_COLUMNS, 日期为 pandas datetime64,
        三种净值为浮点数, 缺失值保留, 按净值日期升序排列.
        同日取最新公告版本, 无公告日期的版本优先级最低.
        最新公告存在冲突值时抛出 ValueError, 不任意选择.
        调用方修改返回表不会影响后续查询. 起始日期晚于结束日期时报错.
        """
        ...

    def list_available_funds(self) -> dict[str, str]:
        """返回本地有净值的代码和名称, 按代码排序, 未知名称使用提示文本."""
        ...
