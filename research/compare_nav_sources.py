"""只读核对 iFinD 与 Tushare 净值, 导出日期覆盖和同日数值差异."""

import argparse
import json
from datetime import date, datetime
from math import isfinite
from pathlib import Path

import pandas as pd
import duckdb

from database.connection import DATABASE_PATH, IFIND_DATABASE_PATH, ROOT, connect
from etl.add_funds import _normalize_code


FIELDS = ("unit_nav", "accum_nav", "adj_nav")


def compare_nav(codes, *, ifind_database=IFIND_DATABASE_PATH, tushare_database=DATABASE_PATH,
                start=None, end=None, tolerance=1e-6):
    """按净值日外连接, Tushare 取最新公告; 复权值另按共同基期归一化核对."""
    targets = list(dict.fromkeys(_normalize_code(code) for code in codes))
    if not targets or not isfinite(tolerance) or tolerance < 0:
        raise ValueError("请提供基金代码及非负有限容差")
    if start and end and start > end:
        raise ValueError("开始日期不能晚于结束日期")
    where, params = "ts_code IN (SELECT unnest(?))", [targets]
    for bound, operator in ((start, ">="), (end, "<=")):
        if bound is not None:
            where += f" AND nav_date {operator} ?"
            params.append(bound)
    values = ", ".join(f"CAST({field} AS DOUBLE) AS {field}" for field in FIELDS)
    with connect(tushare_database, read_only=True) as db:
        tushare = db.execute(f"SELECT ts_code,nav_date,ann_date,{values} FROM fund_nav WHERE {where} "
                             "QUALIFY row_number() OVER(PARTITION BY ts_code,nav_date "
                             "ORDER BY ann_date DESC NULLS LAST)=1", params).fetchdf()
    with connect(ifind_database, read_only=True) as db:
        ifind = db.execute(f"SELECT ts_code,nav_date,fetched_at,{values} FROM fund_nav WHERE {where}", params).fetchdf()
    details = tushare.merge(ifind, on=["ts_code", "nav_date"], how="outer",
                            suffixes=("_tushare", "_ifind"), indicator="availability")
    details = details.sort_values(["ts_code", "nav_date"]).reset_index(drop=True)
    for field in FIELDS:
        details[field + "_diff"] = details[field + "_ifind"] - details[field + "_tushare"]
    details["adj_return_diff"] = float("nan")
    summaries = []
    for code in targets:
        frame = details.loc[details.ts_code.eq(code)]
        item = {"code": code, "common_days": int(frame.availability.eq("both").sum()),
                "only_tushare_days": int(frame.availability.eq("left_only").sum()),
                "only_ifind_days": int(frame.availability.eq("right_only").sum())}
        for field in FIELDS:
            diff = frame[field + "_diff"].dropna().abs()
            item[field + "_comparable_days"] = len(diff)
            item[field + "_diff_days"] = int(diff.gt(tolerance).sum())
            item[field + "_max_abs_diff"] = None if diff.empty else float(diff.max())
        valid = frame.adj_nav_tushare.gt(0) & frame.adj_nav_ifind.gt(0)
        base_date = None
        if valid.any():
            base = frame.loc[valid].iloc[0]
            difference = frame.adj_nav_ifind / base.adj_nav_ifind - frame.adj_nav_tushare / base.adj_nav_tushare
            details.loc[frame.index, "adj_return_diff"] = difference
            base_date = base.nav_date.date().isoformat()
        differences = details.loc[frame.index, "adj_return_diff"].dropna().abs()
        item.update(adj_base_date=base_date, adj_return_comparable_days=len(differences),
                    adj_return_diff_days=int(differences.gt(tolerance).sum()),
                    adj_return_max_abs_diff=None if differences.empty else float(differences.max()))
        summaries.append(item)
    report = {"tushare_database": str(tushare_database), "ifind_database": str(ifind_database),
              "tolerance": tolerance, "difference_direction": "ifind - tushare",
              "fetched_at_timezone": "UTC", "funds": summaries}
    return details, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codes", nargs="+", required=True)
    parser.add_argument("--ifind-database", type=Path, default=IFIND_DATABASE_PATH)
    parser.add_argument("--tushare-database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--tolerance", type=float, default=1e-6)
    parser.add_argument("--output", type=Path, help="新的输出目录, 已存在时不覆盖")
    args = parser.parse_args()
    try:
        folder = args.output or ROOT / "data/nav_comparison" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        if folder.exists():
            raise ValueError("核对输出目录已存在, 请指定新目录")
        details, report = compare_nav(args.codes, ifind_database=args.ifind_database,
                                      tushare_database=args.tushare_database, start=args.start_date,
                                      end=args.end_date, tolerance=args.tolerance)
        folder.mkdir(parents=True, exist_ok=False)
        details.to_csv(folder / "nav_comparison.csv", index=False, encoding="utf-8-sig")
        (folder / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        for item in report["funds"]:
            print(f"{item['code']}: common={item['common_days']}, unit_diff={item['unit_nav_diff_days']}, "
                  f"accum_diff={item['accum_nav_diff_days']}, adj_return_diff={item['adj_return_diff_days']}, "
                  f"only_tushare={item['only_tushare_days']}, only_ifind={item['only_ifind_days']}")
        print(f"核对结果: {folder}")
        return 0
    except (ValueError, OSError, duckdb.Error) as exc:
        print(f"净值核对失败: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
