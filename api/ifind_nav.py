"""iFinD 历史净值请求与精确数值解析, 仅请求三种净值."""

from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd


NAV_FIELDS = {"netAssetValue": "unit_nav", "accumulatedNAV": "accum_nav", "adjustedNAV": "adj_nav"}
NAV_COLUMNS = ["nav_date", *NAV_FIELDS.values()]


def _nav_value(value):
    if pd.isna(value) or str(value).strip() in ("", "--"):
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError("iFinD 净值格式无效") from None
    if not number.is_finite() or number <= 0:
        raise ValueError("iFinD 净值必须为有效正数")
    return number


def fetch_nav(client, code: str, start: date, end: date) -> pd.DataFrame:
    """最多三个自然年一段, 使用 Blank 保留缺失, 不请求基金资料或补造公告日."""
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    frames, fetched = [], []
    for year in range(start.year, end.year + 1, 3):
        left, right = max(start, date(year, 1, 1)), min(end, date(year + 2, 12, 31))
        data = client.query("cmd_history_quotation", {
            "codes": code, "indicators": ",".join(NAV_FIELDS),
            "startdate": left.isoformat(), "enddate": right.isoformat(),
            "functionpara": {"Interval": "D", "Fill": "Blank"},
        })
        fetched.append(data["_fetched_at"])
        for table in data.get("tables") or []:
            if table.get("thscode") != code:
                raise ValueError("iFinD 返回的基金代码与请求不一致")
            frame = pd.DataFrame({"nav_date": table.get("time", []), **(table.get("table") or {})})
            if set(NAV_FIELDS) - set(frame.columns):
                raise ValueError("iFinD 净值返回缺少请求字段")
            frame = frame.rename(columns=NAV_FIELDS).loc[:, NAV_COLUMNS]
            frame["nav_date"] = pd.to_datetime(frame.nav_date, format="ISO8601")
            if not frame.nav_date.between(pd.Timestamp(left), pd.Timestamp(right)).all():
                raise ValueError("iFinD 返回日期超出请求区间")
            for column in NAV_COLUMNS[1:]:
                frame[column] = frame[column].map(_nav_value).astype(object)
            if not frame.empty:
                frames.append(frame)
    nav = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=NAV_COLUMNS)
    nav = nav.drop_duplicates().sort_values("nav_date").reset_index(drop=True)
    if nav.nav_date.duplicated().any():
        raise ValueError("iFinD 同一日期返回了不同净值")
    nav.attrs["fetched_at"] = min(fetched)
    return nav
