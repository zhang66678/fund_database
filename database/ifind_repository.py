"""将 iFinD 在线数据转换为页面现有的只读查询格式, 不写入数据库."""

from datetime import date

import pandas as pd

from api.ifind_client import IfindClient
from database.repository import BASIC_COLUMNS, NAV_COLUMNS


NAV_FIELDS = {
    "netAssetValue": "unit_nav", "accumulatedNAV": "accum_nav", "adjustedNAV": "adj_nav",
}
NAME_FIELD = "ths_fund_short_name_fund"
FOUND_FIELD = "ths_fund_establishment_date_fund"


class IfindFundRepository:
    def __init__(self, client: IfindClient, reference):
        self._client = client
        self._reference = reference
        self._names = {}

    def list_available_funds(self) -> dict[str, str]:
        """本地名单仅用于选择代码, 已查询名称以 iFinD 返回值为准."""
        try:
            names = self._reference.list_available_funds()
        except (ValueError, OSError):
            names = {}
        return dict(sorted((names | self._names).items()))

    def get_calendar(self) -> pd.DataFrame:
        """首版显式复用本地 SSE 日历, 不视作 iFinD 基金数据."""
        return self._reference.get_calendar()

    def get_basic(self, ts_code: str) -> pd.DataFrame:
        data = self._client.query("basic_data_service", {
            "codes": ts_code, "indipara": [
                {"indicator": field, "indiparams": [""]} for field in (NAME_FIELD, FOUND_FIELD)
            ],
        })
        tables = data.get("tables") or []
        if not tables:
            return pd.DataFrame(columns=BASIC_COLUMNS)
        if tables[0].get("thscode", ts_code) != ts_code:
            raise ValueError("iFinD 返回的基金代码与请求不一致")
        frame = pd.DataFrame(tables[0].get("table") or {})
        if frame.empty:
            return pd.DataFrame(columns=BASIC_COLUMNS)
        if {NAME_FIELD, FOUND_FIELD} - set(frame.columns):
            raise ValueError("iFinD 基础资料返回缺少请求字段")
        name = frame[NAME_FIELD].iloc[0]
        if pd.notna(name):
            self._names[ts_code] = str(name)
        basic = frame.rename(columns={FOUND_FIELD: "found_date"}).reindex(columns=BASIC_COLUMNS)
        basic["found_date"] = pd.to_datetime(basic.found_date.astype("string"), format="%Y%m%d")
        return basic

    def get_nav(self, ts_code: str, start_date: date | None = None,
                end_date: date | None = None) -> pd.DataFrame:
        end = end_date or date.today()
        if start_date is None:
            basic = self.get_basic(ts_code)
            if basic.empty or pd.isna(basic.found_date.iloc[0]):
                raise ValueError("iFinD 未返回成立日期, 请指定查询日期区间")
            start_date = basic.found_date.iloc[0].date()
        if start_date > end:
            raise ValueError("开始日期不能晚于结束日期")
        frames, fetched = [], []
        # 按最多 3 个自然年拆分长历史, 避免一次下载整段基金历史.
        for year in range(start_date.year, end.year + 1, 3):
            left = max(start_date, date(year, 1, 1))
            right = min(end, date(year + 2, 12, 31))
            data = self._client.query("cmd_history_quotation", {
                "codes": ts_code, "indicators": ",".join(NAV_FIELDS),
                "startdate": left.isoformat(), "enddate": right.isoformat(),
                "functionpara": {"Interval": "D", "Fill": "Blank"},
            })
            fetched.append(data["_fetched_at"])
            for table in data.get("tables") or []:
                if table.get("thscode") != ts_code:
                    raise ValueError("iFinD 返回的基金代码与请求不一致")
                frame = pd.DataFrame({"nav_date": table.get("time", []), **(table.get("table") or {})})
                if set(NAV_FIELDS) - set(frame.columns):
                    raise ValueError("iFinD 净值返回缺少请求字段")
                frame = frame.rename(columns=NAV_FIELDS).loc[:, NAV_COLUMNS]
                frame["nav_date"] = pd.to_datetime(frame.nav_date)
                for column in NAV_COLUMNS[1:]:
                    frame[column] = pd.to_numeric(frame[column], errors="raise").astype(float)
                if not frame.nav_date.between(pd.Timestamp(left), pd.Timestamp(right)).all():
                    raise ValueError("iFinD 返回日期超出请求区间")
                frames.append(frame)
        nav = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=NAV_COLUMNS)
        nav = nav.drop_duplicates().sort_values("nav_date").reset_index(drop=True)
        if nav.nav_date.duplicated().any():
            raise ValueError("iFinD 同一日期返回了不同净值")
        nav.attrs["fetched_at"] = min(fetched)
        return nav

    def get_managers(self, ts_code: str) -> pd.DataFrame:
        raise ValueError("iFinD 基金经理资料暂未接入")

    def get_assets(self, ts_code: str) -> pd.DataFrame:
        raise ValueError("iFinD 资产资料暂未接入")

    def get_dividends(self, ts_code: str) -> pd.DataFrame:
        raise ValueError("iFinD 分红记录暂未接入")
