"""更新数据库基金数据, 支持全库, --file 批次或 --codes 指定代码."""

import argparse
import json
import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from database.connection import DATABASE_PATH, ROOT
from etl.update_funds import DATASETS, REFRESH_DAYS, update_funds
from api.tushare_client import MIN_REQUEST_INTERVAL, Source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--refresh-days", type=int, default=REFRESH_DAYS)
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--datasets", nargs="+", choices=DATASETS,
                        help="指定更新条目, 可选多个; 不指定时更新全部")
    parser.add_argument("--full-history", action="store_true", help="对所选净值, 资产或日历复查全部历史")
    parser.add_argument("--workers", type=int, default=1, choices=range(1, 17), metavar="1-16",
                        help="并发取数线程数, 默认 1; 数据库仍由主线程写入")
    parser.add_argument("--nav-state", choices=("all", "existing", "missing"), default="all",
                        help="按是否已有净值筛选基金: all 全部, existing 已有, missing 尚无; 默认 all")
    parser.add_argument("--request-interval", type=float, default=0.15,
                        help="所有线程共享的请求最小间隔(秒), 默认 0.15, 最小 0.12 (500次/分钟)")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--file", type=Path, dest="csv_path", help="只更新批次 CSV 中已入库的基金, 必须有 ts_code 列")
    scope.add_argument("--codes", nargs="+", help="只更新指定的已入库基金, 可传多个六位代码或 .OF 代码")
    args = parser.parse_args()
    if not MIN_REQUEST_INTERVAL <= args.request_interval < float("inf"):
        parser.error("--request-interval 必须为至少 0.12 的有限数值, 以遵守 500 次/分钟上限")
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[
        logging.StreamHandler(), logging.FileHandler(log_dir / "database_update.log", encoding="utf-8")])
    try:
        codes = args.codes
        if args.csv_path is not None:
            batch = pd.read_csv(args.csv_path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
            if "ts_code" not in batch.columns:
                raise ValueError("批次 CSV 必须包含 ts_code 列")
            codes = batch.ts_code.tolist()
        source = Source()
        source.interval = args.request_interval
        summary = update_funds(source, args.database, end=args.end_date,
                               refresh_days=args.refresh_days, full_history=args.full_history,
                               datasets=args.datasets, codes=codes, workers=args.workers,
                               nav_state=args.nav_state)
        summary["request_interval"] = args.request_interval
        if args.csv_path is not None:
            summary["source_file"] = str(args.csv_path)
        path = log_dir / f"database_update_{datetime.now():%Y%m%d_%H%M%S}.json"
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return int(summary["status"] != "completed")
    except Exception as exc:
        logging.error("Update failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
