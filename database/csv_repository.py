"""通过已有探索文件提供只读查询, 不调用 Tushare 或修改原始数据."""

from datetime import date
from pathlib import Path

import pandas as pd

from database.repository import BASIC_COLUMNS, NAV_COLUMNS


def _read_columns(path: Path, columns: list[str]) -> pd.DataFrame:
    # 先按字符串读取, 避免基金代码丢失前导零和日期被推断为整数.
    frame = pd.read_csv(path, dtype="string", encoding="utf-8-sig")
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{path.name}: missing columns {sorted(missing)}")
    return frame.loc[:, columns].copy()


def _parse_date(values: pd.Series, *, required: bool) -> pd.Series:
    # 空公告日期可以保留, 但非空的非法日期不能静默转换成缺失值.
    parsed = pd.to_datetime(values, format="%Y%m%d", errors="coerce")
    invalid = values.notna() & (~values.str.fullmatch(r"\d{8}", na=False) | parsed.isna())
    if invalid.any() or (required and parsed.isna().any()):
        raise ValueError(f"Invalid or missing date in {values.name}")
    return parsed


class CsvFundRepository:
    """初始化时读取一次指定快照, 文件更新后需重新创建实例.

    当前样本规模适合内存读取, 全量数据阶段应替换为数据库查询.
    """

    def __init__(self, nav_path: Path, names_path: Path | None = None,
                 calendar_path: Path | None = None) -> None:
        self._names_path = names_path
        self._calendar_path = calendar_path
        nav = _read_columns(nav_path, ["ts_code", "ann_date", *NAV_COLUMNS])
        if nav.ts_code.fillna("").str.strip().eq("").any():
            raise ValueError("NAV contains missing codes")
        nav["nav_date"] = _parse_date(nav.nav_date, required=True)
        nav["ann_date"] = _parse_date(nav.ann_date, required=False)
        for column in NAV_COLUMNS[1:]:
            nav[column] = pd.to_numeric(nav[column], errors="raise").astype("float64")
        self._nav = nav.sort_values(
            ["ts_code", "nav_date", "ann_date"], kind="stable", na_position="first",
        )

    def list_available_funds(self) -> dict[str, str]:
        """名称来自样本名单, 选项范围始终以实际净值代码为准."""
        names = {}
        if self._names_path is not None:
            frame = _read_columns(self._names_path, ["ts_code", "name"])
            if frame.ts_code.isna().any() or frame.ts_code.duplicated().any():
                raise ValueError("Missing or duplicate codes in fund names")
            names = frame.set_index("ts_code")["name"].dropna().to_dict()
        return {code: names.get(code, "名称未提供") for code in sorted(self._nav.ts_code.unique())}

    def get_calendar(self) -> pd.DataFrame:
        """仅在计算指标时读取日历, 不影响单独查询净值."""
        if self._calendar_path is None:
            raise ValueError("尚未配置交易日历, 暂不能计算指标")
        frame = _read_columns(self._calendar_path, ["exchange", "cal_date", "is_open"])
        return frame.loc[frame.exchange.eq("SSE"), ["cal_date", "is_open"]].copy()

    def get_managers(self, ts_code: str) -> pd.DataFrame:
        """旧 CSV 对照模式尚未提供经理资料, 不混用数据库数据."""
        raise ValueError("CSV 对照模式暂不提供基金经理资料")

    def get_basic(self, ts_code: str) -> pd.DataFrame:
        """从同一份基金名单读取资料, 旧名单没有的字段保持缺失."""
        if self._names_path is None:
            return pd.DataFrame(columns=BASIC_COLUMNS)
        frame = pd.read_csv(self._names_path, dtype="string", encoding="utf-8-sig")
        basic = frame.loc[frame.ts_code.eq(ts_code)].reindex(columns=BASIC_COLUMNS).copy()
        basic["found_date"] = _parse_date(basic.found_date.astype("string"), required=False)
        return basic.reset_index(drop=True)

    def get_assets(self, ts_code: str) -> pd.DataFrame:
        """旧 CSV 对照模式尚未提供资产资料, 不混用数据库数据."""
        raise ValueError("CSV 对照模式暂不提供基金资产资料")

    def get_dividends(self, ts_code: str) -> pd.DataFrame:
        """旧 CSV 对照模式尚未提供分红资料, 不混用数据库数据."""
        raise ValueError("CSV 对照模式暂不提供分红记录")

    def get_nav(
        self, ts_code: str, start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        """返回独立的查询结果, 空值不填充, 不补造交易日或净值."""
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("start_date must not be later than end_date")
        mask = self._nav.ts_code.eq(ts_code)
        if start_date is not None:
            mask &= self._nav.nav_date.ge(pd.Timestamp(start_date))
        if end_date is not None:
            mask &= self._nav.nav_date.le(pd.Timestamp(end_date))
        result = self._nav.loc[mask].drop_duplicates()
        # 每个净值日期只展示最新公告, 避免同日多点造成曲线歧义.
        latest = result.groupby("nav_date")["ann_date"].transform("max")
        result = result.loc[result.ann_date.eq(latest) | (result.ann_date.isna() & latest.isna())]
        if result.nav_date.duplicated().any():
            raise ValueError("Conflicting NAV values for the latest announcement")
        return result.loc[:, NAV_COLUMNS].copy().reset_index(drop=True)
