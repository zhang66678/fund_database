"""DuckDB 只读查询实现, 对外保持与 CSV 相同的查询约定."""

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from database.connection import DATABASE_PATH, connect


class DuckDBFundRepository:
    """复用少量只读结果, 缓存未命中时打开并关闭连接, 不持有长期连接."""

    def __init__(self, path: Path = DATABASE_PATH) -> None:
        self._path = path
        self._cache = {}
        self._cache_version = None

    def _file_version(self) -> tuple:
        """主文件或 WAL 改变后丢弃缓存, 不持有数据库连接或阻塞后续更新."""
        info = self._path.stat()
        version = [(info.st_mtime_ns, info.st_size)]
        try:
            wal = self._path.with_name(self._path.name + ".wal").stat()
            version.append((wal.st_mtime_ns, wal.st_size))
        except FileNotFoundError:
            version.append(None)
        return tuple(version)

    def _query(self, sql: str, params: list | None = None, *, cache: bool = True) -> pd.DataFrame:
        try:
            version = self._file_version()
            if version != self._cache_version:
                self._cache.clear()
                self._cache_version = version
            key = (sql, tuple(tuple(value) if isinstance(value, list) else value for value in (params or [])))
            if cache and key in self._cache:
                return self._cache[key].copy()
            with connect(self._path, read_only=True) as connection:
                frame = connection.execute(sql, params or []).fetchdf()
            if cache:
                # 仅保留最近的少量查询结果, 返回副本避免页面修改污染缓存.
                if len(self._cache) >= 64:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[key] = frame
                return frame.copy()
            return frame
        except (duckdb.Error, OSError) as exc:
            self._cache.clear()
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

    def get_peer_category(self, ts_code: str) -> pd.DataFrame:
        """每只基金使用最近采集的 iFinD 二级分类, 最新缺失时不回退旧分类."""
        exists = self._query("SELECT 1 FROM information_schema.tables WHERE table_name='fund_category'")
        if exists.empty:
            return pd.DataFrame(columns=["category", "source", "snapshot_date",
                                         "class_level1", "class_level2", "class_level3"])
        frame = self._query('''
            SELECT category, source, snapshot_date, class_level1, class_level2, class_level3
            FROM fund_category WHERE ts_code = ? AND source = 'ifind'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY snapshot_date DESC) = 1
        ''', [ts_code])
        return frame.loc[frame.category.notna() & frame.category.str.strip().ne("")].reset_index(drop=True)

    def get_peer_nav_points(self, ts_code: str, dates: list[date]) -> pd.DataFrame:
        """单次查询同类基金指定日期的复权净值, 避免逐只扫描完整历史."""
        if not dates:
            return pd.DataFrame(columns=["ts_code", "nav_date", "adj_nav"])
        return self._query('''
            WITH categories AS (
                SELECT ts_code, category FROM fund_category WHERE source = 'ifind'
                QUALIFY ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY snapshot_date DESC) = 1
            ), versions AS (
                SELECT n.ts_code, n.nav_date, n.adj_nav,
                       ROW_NUMBER() OVER (PARTITION BY n.ts_code, n.nav_date
                                          ORDER BY n.ann_date DESC NULLS LAST) AS version_rank
                FROM fund_nav n JOIN categories c ON n.ts_code = c.ts_code
                JOIN fund_basic b ON n.ts_code = b.ts_code
                WHERE c.category = (SELECT category FROM categories WHERE ts_code = ?)
                  AND n.nav_date IN (SELECT unnest(?::DATE[]))
            )
            SELECT ts_code, nav_date, CAST(adj_nav AS DOUBLE) AS adj_nav
            FROM versions WHERE version_rank = 1 ORDER BY ts_code, nav_date
        ''', [ts_code, sorted(set(dates))], cache=False)

    def get_nav(
        self, ts_code: str, start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        """复用单基金最新公告版历史净值, 在内存按日期闭区间截取."""
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("start_date must not be later than end_date")
        # 主键保证相同公告版本唯一, 基金代码参数化, 日期在内存筛选.
        frame = self._query('''
            WITH versions AS (
                SELECT nav_date, unit_nav, accum_nav, adj_nav,
                       ROW_NUMBER() OVER (
                           PARTITION BY nav_date ORDER BY ann_date DESC NULLS LAST
                       ) AS version_rank
                FROM fund_nav
                WHERE ts_code = ?
            )
            SELECT nav_date, CAST(unit_nav AS DOUBLE PRECISION) AS unit_nav,
                   CAST(accum_nav AS DOUBLE PRECISION) AS accum_nav,
                   CAST(adj_nav AS DOUBLE PRECISION) AS adj_nav
            FROM versions WHERE version_rank = 1 ORDER BY nav_date
        ''', [ts_code])
        if start_date is not None:
            frame = frame.loc[frame.nav_date.ge(pd.Timestamp(start_date))]
        if end_date is not None:
            frame = frame.loc[frame.nav_date.le(pd.Timestamp(end_date))]
        return frame.reset_index(drop=True)

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

    def get_dividends(self, ts_code: str) -> pd.DataFrame:
        """读取全部分红披露, 保留公告及方案版本, 不汇总金额."""
        return self._query('''
            SELECT ann_date, ex_date, record_date, pay_date,
                   CAST(div_cash AS DOUBLE PRECISION) AS div_cash, div_proc
            FROM fund_dividend WHERE ts_code = ?
            ORDER BY ex_date DESC NULLS LAST, ann_date DESC NULLS LAST, div_proc
        ''', [ts_code])
