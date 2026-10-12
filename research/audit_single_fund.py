"""单基金指标准备核查, 只获取基础数据和检查质量, 不计算业绩指标.

运行: python -m research.audit_single_fund
"""

import argparse
import json
import logging
from datetime import date, datetime

import pandas as pd

from api.fund_fields import BASIC
from etl.extract import paged
from api.fund_fields import NAV_FIELDS, DIV_FIELDS
from etl.extract import fetch_series
from api.tushare_client import ROOT, FetchError, Source


def inspect_nav(nav: pd.DataFrame, calendar: pd.DataFrame | None) -> dict:
    """保留原始披露记录, 统计时按最新公告选取版本, 冲突不静默处理."""
    result = {"rows": len(nav), "null_counts": nav.isna().sum().astype(int).to_dict()}
    if nav.empty:
        return result
    frame = nav.copy()
    for column in ("nav_date", "ann_date"):
        frame[column] = pd.to_datetime(frame[column], format="%Y%m%d", errors="raise")
    if frame.nav_date.isna().any():
        raise ValueError("Missing NAV dates")
    result["duplicate_dates"] = int(frame.nav_date.duplicated().sum())
    frame = frame.drop_duplicates().sort_values(["nav_date", "ann_date"], na_position="first")
    latest = frame.groupby("nav_date").ann_date.transform("max")
    frame = frame[frame.ann_date.eq(latest) | (frame.ann_date.isna() & latest.isna())]
    if frame.nav_date.duplicated().any():
        raise ValueError("Conflicting latest announcements")
    for column in ("unit_nav", "accum_nav", "adj_nav"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    result.update(first_date=str(frame.nav_date.min().date()), last_date=str(frame.nav_date.max().date()),
                  nonpositive={c: int(frame[c].le(0).sum()) for c in ("unit_nav", "accum_nav", "adj_nav")},
                  nonfinite={c: int(frame[c].isin([float("inf"), -float("inf")]).sum())
                             for c in ("unit_nav", "accum_nav", "adj_nav")})
    # 大幅变化只标为待核查, 不自动删除. 阈值是质量筛查参数而非涨跌幅限制.
    changes = frame.adj_nav.pct_change(fill_method=None)
    result["adj_changes_over_20pct"] = frame.loc[changes.abs().gt(0.2), "nav_date"].dt.strftime("%Y%m%d").tolist()
    open_dates = None
    if calendar is not None:
        open_dates = pd.DatetimeIndex(pd.to_datetime(calendar.loc[calendar.is_open.astype(str).eq("1"), "cal_date"], format="%Y%m%d"))
    yearly = []
    for year, group in frame.groupby(frame.nav_date.dt.year):
        observed = pd.DatetimeIndex(group.nav_date)
        record = {"year": int(year), "rows": len(group), "adj_nulls": int(group.adj_nav.isna().sum()),
                  "max_calendar_gap_days": int(group.nav_date.diff().dt.days.max()) if len(group) > 1 else None}
        if open_dates is not None:
            expected = open_dates[(open_dates.year == year) & (open_dates >= frame.nav_date.min()) & (open_dates <= frame.nav_date.max())]
            record["missing_open_dates"] = expected.difference(observed).strftime("%Y%m%d").tolist()
            record["non_open_dates"] = observed.difference(open_dates).strftime("%Y%m%d").tolist()
        yearly.append(record)
    result["yearly"] = yearly
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", default="000001.OF")
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    output = ROOT / "data" / "single_fund_audit" / datetime.now().strftime("%Y%m%d_%H%M%S")
    output.mkdir(parents=True, exist_ok=False)
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[
        logging.StreamHandler(), logging.FileHandler(ROOT / "logs/single_fund_audit.log", encoding="utf-8")])
    summary = {"code": args.code, "requested_end": str(args.end_date), "started_at": datetime.now().isoformat(),
               "source": "Tushare Pro live request", "endpoints": {}, "status": "running"}
    tables = {}
    try:
        source = Source()
        basic = source.query("fund_basic", BASIC, ts_code=args.code, market="O")
        if len(basic) != 1 or not basic.ts_code.eq(args.code).all():
            raise ValueError("Expected exactly one fund basic record")
        basic.to_csv(output / "fund_basic.csv", index=False, encoding="utf-8-sig")
        summary["endpoints"]["fund_basic"] = {"status": "ok", "rows": len(basic)}
        start = datetime.strptime(basic.iloc[0].found_date, "%Y%m%d").date()
        if start > args.end_date:
            raise ValueError("Requested end precedes inception")
        for endpoint in ("fund_nav", "fund_div", "trade_cal"):
            try:
                if endpoint == "fund_nav":
                    table = fetch_series(source, endpoint, NAV_FIELDS, args.code, start, args.end_date)
                elif endpoint == "fund_div":
                    table = paged(source, endpoint, DIV_FIELDS, ts_code=args.code)
                    if not table.ts_code.eq(args.code).all():
                        raise ValueError("Unexpected dividend fund code")
                else:
                    parts = []
                    for year in range(start.year, args.end_date.year + 1):
                        left, right = max(start, date(year, 1, 1)), min(args.end_date, date(year, 12, 31))
                        part = source.query("trade_cal", "exchange,cal_date,is_open", exchange="SSE",
                                            start_date=left.strftime("%Y%m%d"), end_date=right.strftime("%Y%m%d"))
                        expected = pd.date_range(left, right).strftime("%Y%m%d")
                        if part.cal_date.duplicated().any() or set(part.cal_date) != set(expected) or not part.exchange.eq("SSE").all():
                            raise ValueError("Incomplete or unexpected exchange calendar")
                        parts.append(part)
                    table = pd.concat(parts, ignore_index=True)
                table.to_csv(output / f"{endpoint}.csv", index=False, encoding="utf-8-sig")
                tables[endpoint] = table
                summary["endpoints"][endpoint] = {"status": "empty" if table.empty else "ok", "rows": len(table)}
            except (FetchError, ValueError) as exc:
                summary["endpoints"][endpoint] = {"status": "failed", "reason": str(exc)}
                logging.error("%s: %s", endpoint, exc)
        if "fund_nav" in tables:
            summary["nav_quality"] = inspect_nav(tables["fund_nav"], tables.get("trade_cal"))
        summary["status"] = "completed" if all(x["status"] == "ok" for x in summary["endpoints"].values()) else "partial"
    except (FetchError, ValueError, OSError) as exc:
        summary["status"] = "failed"
        summary["error"] = str(exc)
        logging.error("Audit failed: %s", exc)
    summary["finished_at"] = datetime.now().isoformat()
    (output / "audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Audit %s: %s", summary["status"], output)
    return int(summary["status"] != "completed")


if __name__ == "__main__":
    raise SystemExit(main())
