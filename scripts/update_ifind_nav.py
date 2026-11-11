"""日常更新 iFinD 净值, 默认全库: python -m scripts.update_ifind_nav."""

import argparse
import json
import logging
from datetime import date, datetime
from pathlib import Path

import duckdb

from api.ifind_client import IfindClient, MIN_REQUEST_INTERVAL
from database.connection import DATABASE_PATH, IFIND_DATABASE_PATH, ROOT
from database.ifind_nav_store import initialize_store
from etl.ifind_nav import update_ifind_nav
from etl.update_scope import NAV_STATES, REFRESH_DAYS, read_update_codes
from scripts.logging_utils import rotating_handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=IFIND_DATABASE_PATH)
    parser.add_argument("--reference-database", type=Path, default=DATABASE_PATH)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--init-only", action="store_true", help="只建净值库, 不请求接口或读取凭证")
    scope.add_argument("--codes", nargs="+", help="指定基金代码")
    scope.add_argument("--file", type=Path, help="CSV 必须包含 ts_code 列")
    scope.add_argument("--all", action="store_true", help="兼容旧命令, 等同默认全库更新")
    parser.add_argument("--nav-state", choices=NAV_STATES, default="all",
                        help="按 iFinD 库净值筛选: all 全部, existing 已有, missing 尚无")
    parser.add_argument("--refresh-days", type=int, default=REFRESH_DAYS,
                        help="增量回查天数, 默认 7, 包含最后数据日")
    parser.add_argument("--workers", type=int, default=1, choices=range(1, 17), metavar="1-16",
                        help="并发取数线程数, 默认 1; 数据库由主线程写入")
    parser.add_argument("--request-interval", type=float, default=0.15,
                        help="各线程共享的请求间隔(秒), 默认 0.15, 最小 0.1")
    parser.add_argument("--start-date", type=date.fromisoformat, help="默认使用本地成立日期")
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--full-history", action="store_true", help="复查整个指定区间, 不使用增量窗口")
    args = parser.parse_args()
    if args.start_date and args.start_date > args.end_date:
        parser.error("开始日期不能晚于结束日期")
    if args.refresh_days < 1:
        parser.error("--refresh-days 必须为正整数")
    if not MIN_REQUEST_INTERVAL <= args.request_interval < float("inf"):
        parser.error("--request-interval 必须为至少 0.1 的有限数值")
    log_dir = ROOT / "logs/database"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[
        logging.StreamHandler(), rotating_handler(log_dir / "ifind_nav.log")])
    try:
        if args.init_only:
            initialize_store(args.database, args.reference_database)
            print(f"iFinD 净值库已建立: {args.database}")
            return 0
        codes = read_update_codes(args.codes, args.file)
        client = IfindClient()
        client.interval = args.request_interval
        try:
            summary = update_ifind_nav(client, args.database, reference_path=args.reference_database,
                                       codes=codes, start=args.start_date, end=args.end_date,
                                       full_history=args.full_history, nav_state=args.nav_state,
                                       refresh_days=args.refresh_days, workers=args.workers)
        finally:
            client.close()
        summary["request_interval"] = args.request_interval
        if args.file is not None:
            summary["source_file"] = str(args.file)
        path = log_dir / f"ifind_nav_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return int(summary["status"] != "completed")
    except (ValueError, OSError, duckdb.Error) as exc:
        logging.error("iFinD NAV update failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
