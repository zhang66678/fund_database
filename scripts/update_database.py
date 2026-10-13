"""按数据库基金清单统一更新全部数据: python -m scripts.update_database."""

import argparse
import json
import logging
from datetime import date, datetime
from pathlib import Path

from database.connection import DATABASE_PATH, ROOT
from etl.update_funds import DATASETS, REFRESH_DAYS, update_funds
from api.tushare_client import Source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--refresh-days", type=int, default=REFRESH_DAYS)
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--datasets", nargs="+", choices=DATASETS,
                        help="指定更新条目, 可选多个; 不指定时更新全部")
    parser.add_argument("--full-history", action="store_true", help="对所选净值, 资产或日历复查全部历史")
    args = parser.parse_args()
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[
        logging.StreamHandler(), logging.FileHandler(log_dir / "database_update.log", encoding="utf-8")])
    try:
        summary = update_funds(Source(), args.database, end=args.end_date,
                               refresh_days=args.refresh_days, full_history=args.full_history,
                               datasets=args.datasets)
        path = log_dir / f"database_update_{datetime.now():%Y%m%d_%H%M%S}.json"
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return int(summary["status"] != "completed")
    except Exception as exc:
        logging.error("Update failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
