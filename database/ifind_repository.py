"""将 iFinD 在线数据转换为页面现有的只读查询格式, 不写入数据库."""

from datetime import date
import re

import pandas as pd

from api.ifind_client import IfindClient
from database.repository import ASSET_COLUMNS, BASIC_COLUMNS, DIVIDEND_COLUMNS, MANAGER_COLUMNS, NAV_COLUMNS


NAV_FIELDS = {
    "netAssetValue": "unit_nav", "accumulatedNAV": "accum_nav", "adjustedNAV": "adj_nav",
}
NAME_FIELD = "ths_fund_short_name_fund"
FOUND_FIELD = "ths_fund_establishment_date_fund"
DIVIDEND_FIELDS = {
    "p04925_f010": "ann_date", "p04925_f004": "ex_date", "p04925_f003": "record_date",
    "p04925_f002": "pay_date", "p04925_f001": "div_cash",
}


def _dates(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values.replace({"--": None, "": None}).astype("string"), format="mixed")


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

    def _basic(self, ts_code: str, indicators: dict[str, list[str]]) -> pd.DataFrame:
        """读取单基金的基础指标, 保留接口未提供的值."""
        data = self._client.query("basic_data_service", {
            "codes": ts_code, "indipara": [
                {"indicator": field, "indiparams": params} for field, params in indicators.items()
            ],
        })
        tables = data.get("tables") or []
        if not tables:
            return pd.DataFrame(columns=list(indicators))
        if tables[0].get("thscode", ts_code) != ts_code:
            raise ValueError("iFinD 返回的基金代码与请求不一致")
        frame = pd.DataFrame(tables[0].get("table") or {})
        if frame.empty:
            return pd.DataFrame(columns=list(indicators))
        if set(indicators) - set(frame.columns):
            raise ValueError("iFinD 基础资料返回缺少请求字段")
        return frame.replace({"--": None, "": None})

    def get_basic(self, ts_code: str) -> pd.DataFrame:
        frame = self._basic(ts_code, {NAME_FIELD: [""], FOUND_FIELD: [""]})
        if frame.empty:
            return pd.DataFrame(columns=BASIC_COLUMNS)
        name = frame[NAME_FIELD].iloc[0]
        if pd.notna(name):
            self._names[ts_code] = str(name)
        basic = frame.rename(columns={FOUND_FIELD: "found_date"}).reindex(columns=BASIC_COLUMNS)
        basic["found_date"] = _dates(basic.found_date)
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
        """历任名单提供任职起止日, 现任经理补充学历和简历."""
        current_field, history_field = "ths_fund_manager_current_fund", "ths_fund_manager_his_fund"
        raw = self._basic(ts_code, {current_field: [], history_field: []})
        if raw.empty:
            return pd.DataFrame(columns=MANAGER_COLUMNS)
        current_text = raw[current_field].astype("string").fillna("").iloc[0]
        current = {name.strip() for name in current_text.split(",") if name.strip()}
        history = raw[history_field].astype("string").fillna("").iloc[0]
        pattern = r"([^()（）]+)[(（](\d{8})-(\d{8}|至今)[)）]"
        records = []
        for name, begin, end in re.findall(pattern, history):
            records.append({"name": name.strip(" ,，;；\n"), "begin_date": begin,
                            "end_date": None if end == "至今" else end,
                            "position_status": "现任" if end == "至今" else "已离任"})
        if re.sub(pattern, "", history).strip(" ,，;；\n"):
            raise ValueError("iFinD 历任经理格式无法识别, 未将人员与日期强行对应")
        # 历任指标缺失时, 仍保留现任名单; 不根据空离任日期推断在任.
        known = {row["name"] for row in records}
        records.extend({"name": name, "position_status": "现任"} for name in sorted(current - known))
        if not records:
            return pd.DataFrame(columns=MANAGER_COLUMNS)
        managers = pd.DataFrame(records).reindex(columns=[*MANAGER_COLUMNS, "position_status"])
        managers[["edu", "resume"]] = managers[["edu", "resume"]].astype(object)
        for field in ("ann_date", "begin_date", "end_date"):
            managers[field] = _dates(managers[field])
        profile_fields = {"ths_education_fund": "edu", "ths_resume_fund": "resume"}
        # 官方按任职回报排名提供前 10 位, 用返回的姓名与任职日匹配, 不按名单顺序拼接.
        for rank in range(1, min(len(current), 10) + 1):
            profile = self._basic(ts_code, {
                "ths_service_start_fund": ["100", str(rank), "100"],
                **{field: ["100", str(rank), "101"] for field in profile_fields},
            })
            if profile.empty or pd.isna(profile["ths_service_start_fund"].iloc[0]):
                continue
            matched = re.fullmatch(r"(.+)[(（](\d{4}-\d{2}-\d{2})[)）]", profile["ths_service_start_fund"].iloc[0])
            if not matched:
                raise ValueError("iFinD 经理任职资料格式无法识别")
            name, begin = matched.groups()
            mask = managers.name.eq(name) & (managers.begin_date.eq(pd.Timestamp(begin)) | managers.begin_date.isna())
            managers.loc[mask, "begin_date"] = pd.Timestamp(begin)
            for field, column in profile_fields.items():
                managers.loc[mask, column] = profile[field].iloc[0]
        return managers.drop_duplicates().sort_values("begin_date", ascending=False, na_position="last").reset_index(drop=True)

    def get_assets(self, ts_code: str) -> pd.DataFrame:
        """先确定最新报告期, 再按同一报告期获取净资产、总资产及季报披露日."""
        period_field = "ths_latest_rp_fund"
        latest = self._basic(ts_code, {period_field: [date.today().isoformat(), "1"]})
        if latest.empty or pd.isna(latest[period_field].iloc[0]):
            return pd.DataFrame(columns=ASSET_COLUMNS)
        period = _dates(latest[period_field]).iloc[0]
        fields = {"ths_fanv_fund": "net_asset", "ths_fatv_fund": "total_asset",
                  "ths_quarterly_report_dd_fund": "ann_date"}
        raw = self._basic(ts_code, {
            "ths_fanv_fund": [period.strftime("%Y%m%d")],
            "ths_fatv_fund": [period.strftime("%Y%m%d")],
            "ths_quarterly_report_dd_fund": [str(period.year), str(period.quarter)],
        })
        assets = raw.rename(columns=fields).reindex(columns=[*ASSET_COLUMNS, "total_asset"])
        if assets.empty:
            return assets
        assets["nav_date"] = period
        assets["ann_date"] = _dates(assets.ann_date)
        for field in ("net_asset", "total_asset"):
            assets[field] = pd.to_numeric(assets[field], errors="raise").astype(float)
        return assets

    def get_dividends(self, ts_code: str) -> pd.DataFrame:
        """基金分红报表提供事件明细, 按首次公告日期分段读取成立以来记录."""
        basic = self.get_basic(ts_code)
        if basic.empty or pd.isna(basic.found_date.iloc[0]):
            raise ValueError("iFinD 未提供成立日期, 无法确定分红查询起点")
        start, end = basic.found_date.iloc[0].date(), date.today()
        frames = []
        for year in range(start.year, end.year + 1, 3):
            data = self._client.query("data_pool", {
                "reportname": "p04925", "functionpara": {
                    "sdate": max(start, date(year, 1, 1)).strftime("%Y%m%d"),
                    "edate": min(end, date(year + 2, 12, 31)).strftime("%Y%m%d"),
                    "jjlb": ts_code, "dtype": "2",  # 按首次公告日期查询.
                }, "outputpara": ",".join(["jydm", *DIVIDEND_FIELDS]),
            })
            for table in data.get("tables") or []:
                frame = pd.DataFrame(table.get("table") or {})
                if frame.empty:
                    continue
                if {"jydm", *DIVIDEND_FIELDS} - set(frame.columns):
                    raise ValueError("iFinD 分红报表缺少请求字段")
                if not frame.jydm.eq(ts_code).all():
                    raise ValueError("iFinD 分红报表返回了其他基金, 未混入查询结果")
                frames.append(frame.rename(columns=DIVIDEND_FIELDS).reindex(columns=DIVIDEND_COLUMNS))
        if not frames:
            return pd.DataFrame(columns=DIVIDEND_COLUMNS)
        dividends = pd.concat(frames, ignore_index=True).replace({"--": None, "": None})
        for field in ("ann_date", "ex_date", "record_date", "pay_date"):
            dividends[field] = _dates(dividends[field])
        dividends["div_cash"] = pd.to_numeric(dividends.div_cash, errors="raise").astype(float)
        # 报表未提供方案进度, 保持缺失, 不将有派息日期的记录自行标记为已实施.
        return dividends.drop_duplicates().sort_values(["ex_date", "ann_date"], ascending=False, na_position="last").reset_index(drop=True)
