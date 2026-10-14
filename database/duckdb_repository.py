"""DuckDB 只读查询实现, 对外保持与 CSV 相同的查询约定."""

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from database.connection import DATABASE_PATH, connect


class DuckDBFundRepository:
    """每次查询打开并关闭只读连接, 页面不持有长期数据库连接."""

    def __init__(self, path: Path = DATABASE_PATH) -> None:
        self._path = path

    def _query(self, sql: str, params: list | None = None) -> pd.DataFrame:
        try:
            with connect(self._path, read_only=True) as connection:
                return connection.execute(sql, params or []).fetchdf()
        except duckdb.Error as exc:
            # 将存储异常统一为读取失败, 页面无需导入 DuckDB.
            raise OSError("数据库读取失败, 请检查路径, 初始化状态或其他写入进程") from exc

    def list_available_funds(self) -> dict[str, str]:
        """只列出已存储净值的基金, 不过滤已终止基金."""
        frame = self._query('''
            SELECT n.ts_code, COALESCE(b.name, '名称未提供') AS name
            FROM (SELECT DISTINCT ts_code FROM fund_nav) n
            LEFT JOIN fund_basic b ON n.ts_code = b.ts_code
            ORDER BY n.ts_code
        ''')
        return dict(zip(frame.ts_code, frame.name))

    def get_nav(
        self, ts_code: str, start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        """按日期闭区间读取最新公告版本, 金额转浮点供服务层计算."""
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("start_date must not be later than end_date")
        # 主键保证相同公告版本唯一, 日期条件与代码全部参数化.
        return self._query('''
            WITH versions AS (
                SELECT nav_date, unit_nav, accum_nav, adj_nav,
                       ROW_NUMBER() OVER (
                           PARTITION BY nav_date ORDER BY ann_date DESC NULLS LAST
                       ) AS version_rank
                FROM fund_nav
                WHERE ts_code = ? AND nav_date >= ? AND nav_date <= ?
            )
            SELECT nav_date, CAST(unit_nav AS DOUBLE PRECISION) AS unit_nav,
                   CAST(accum_nav AS DOUBLE PRECISION) AS accum_nav,
                   CAST(adj_nav AS DOUBLE PRECISION) AS adj_nav
            FROM versions WHERE version_rank = 1 ORDER BY nav_date
        ''', [ts_code, start_date or date.min, end_date or date.max])

    def get_calendar(self) -> pd.DataFrame:
        """返回含休市日的完整 SSE 日历, 供服务层检查缺口."""
        return self._query("SELECT cal_date, is_open FROM trade_calendar WHERE exchange = ? ORDER BY cal_date", ["SSE"])

    def get_basic(self, ts_code: str) -> pd.DataFrame:
        """读取已经入库的基础资料, 不随净值查询区间回溯."""
        return self._query(
            "SELECT found_date, fund_type, benchmark FROM fund_basic WHERE ts_code = ?", [ts_code],
        )

    def get_managers(self, ts_code: str) -> pd.DataFrame:
        """保留全部任期, 同一姓名和任期仅展示最新公告记录."""
        return self._query('''
            WITH versions AS (
                SELECT ann_date, name, begin_date, end_date, edu, resume,
                       ROW_NUMBER() OVER (
                           PARTITION BY name, begin_date ORDER BY ann_date DESC
                       ) AS version_rank
                FROM fund_manager WHERE ts_code = ?
            )
            SELECT ann_date, name, begin_date, end_date, edu, resume
            FROM versions WHERE version_rank = 1 ORDER BY begin_date DESC, name
        ''', [ts_code])

    def get_assets(self, ts_code: str) -> pd.DataFrame:
        """资产金额转浮点供展示, 数据库仍保留 DECIMAL, 不补齐披露日期."""
        return self._query('''
            WITH versions AS (
                SELECT ann_date, nav_date, net_asset, total_netasset,
                       ROW_NUMBER() OVER (
                           PARTITION BY nav_date ORDER BY ann_date DESC
                       ) AS version_rank
                FROM fund_asset WHERE ts_code = ?
            )
            SELECT ann_date, nav_date, CAST(net_asset AS DOUBLE PRECISION) AS net_asset,
                   CAST(total_netasset AS DOUBLE PRECISION) AS total_netasset
            FROM versions WHERE version_rank = 1 ORDER BY nav_date
        ''', [ts_code])
