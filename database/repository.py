"""定义只读查询约定, 让业务层不依赖 CSV 或具体数据库."""

from datetime import date
from typing import Protocol

import pandas as pd


NAV_COLUMNS = ["nav_date", "unit_nav", "accum_nav", "adj_nav"]
BASIC_COLUMNS = ["found_date", "fund_type", "benchmark"]
MANAGER_COLUMNS = ["ann_date", "name", "begin_date", "end_date", "edu", "resume"]
ASSET_COLUMNS = ["ann_date", "nav_date", "net_asset", "total_netasset"]
DIVIDEND_COLUMNS = ["ann_date", "ex_date", "record_date", "pay_date", "div_cash", "div_proc"]
INDEX_NAMES = {"000300.SH": "沪深300", "000905.SH": "中证500", "000852.SH": "中证1000",
               "000016.SH": "上证50", "399006.SZ": "创业板指"}


class FundRepository(Protocol):
    """不同存储实现遵守相同方法和返回约定, 由调用方注入具体实现."""

    def get_index_prices(self, codes: list[str], start_date: date, end_date: date) -> pd.DataFrame:
        """返回宽基指数的 ts_code, trade_date, close, 按代码和日期排序.

        close 为原始收盘点位, 不归一化. 无记录返回空表, 未接入的来源抛出 ValueError.
        """
        ...

    def get_peer_category(self, ts_code: str) -> pd.DataFrame:
        """返回最新二级分类的 category, source, snapshot_date, 无匹配返回空表.

        附带 class_level1, class_level2, class_level3 原文, 分组固定使用一级/二级路径.
        未支持同类比较的来源抛出 ValueError, 不自动混用其他净值来源.
        """
        ...

    def get_peer_nav_points(self, ts_code: str, dates: list[date]) -> pd.DataFrame:
        """批量返回同一细分类的 ts_code, nav_date, adj_nav, 每日取最新公告版本.

        只含本地基金, 不按当前存续状态排除历史基金; 不补齐缺失日期或净值.
        分类使用每只基金最近的采集结果, 不宣称还原历史时点分类.
        """
        ...

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
        """返回基金选择项, 按代码排序; 在线来源可沿用本地候选名单."""
        ...

    def get_calendar(self) -> pd.DataFrame:
        """返回 SSE 日历的 cal_date, is_open 列, 供日频指标使用."""
        ...

    def get_basic(self, ts_code: str) -> pd.DataFrame:
        """返回 BASIC_COLUMNS 中的当前基金资料, 成立日期为 pandas 日期.

        无记录时返回同结构空表, 缺失字段保留, 不还原历史时点的资料.
        """
        ...

    def get_managers(self, ts_code: str) -> pd.DataFrame:
        """返回 MANAGER_COLUMNS, 同一姓名和任期取最新公告, 按任职日期倒序.

        不根据离任日期缺失推断在任. 未支持此项的数据源抛出 ValueError.
        """
        ...

    def get_assets(self, ts_code: str) -> pd.DataFrame:
        """返回 ASSET_COLUMNS, 每个净值日期取最新公告, 按日期升序.

        保留两种资产口径和缺失值, 不换算单位. 未支持此项的数据源抛出 ValueError.
        """
        ...

    def get_dividends(self, ts_code: str) -> pd.DataFrame:
        """返回 DIVIDEND_COLUMNS, 按除息日和公告日倒序展示全部本地披露.

        日期为 pandas 日期, 派现金额为浮点数, 保留缺失值和不同方案版本.
        不累加金额或推断实际分红次数, 未支持的数据源抛出 ValueError.
        """
        ...
