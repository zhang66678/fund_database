"""下载或增量更新宽基指数点位: python -m scripts.update_index_prices."""

import argparse
import json
import logging
from datetime import date, datetime
from pathlib import Path

from api.tushare_client import Source
from database.connection import DATABASE_PATH, ROOT
from database.repository import INDEX_NAMES
from etl.index_prices import update_index_prices
from scripts.logging_utils import rotating_handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--codes", nargs="+", choices=list(INDEX_NAMES), help="默认更新全部五个指数")
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(1990, 1, 1))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--full-history", action="store_true", help="复查指定起止日期内的全部指数历史")
    args = parser.parse_args()
    if args.start_date > args.end_date:
        parser.error("开始日期不能晚于结束日期")
    log_dir = ROOT / "logs/database"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[
        logging.StreamHandler(), rotating_handler(log_dir / "index_prices.log")])
    try:
        summary = update_index_prices(Source(), args.database, codes=args.codes, start=args.start_date,
                                      end=args.end_date, full_history=args.full_history)
    except (ValueError, OSError, RuntimeError) as exc:
        logging.error("Index update failed: %s", exc)
        return 1
    path = log_dir / f"index_prices_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(summary["status"] != "completed")


if __name__ == "__main__":
    raise SystemExit(main())
