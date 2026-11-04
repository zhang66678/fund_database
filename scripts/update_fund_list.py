"""更新场外基金总名单快照: python -m scripts.update_fund_list."""

import argparse
import logging
from datetime import date, datetime
from pathlib import Path

from api.tushare_client import ROOT, Source
from etl.fund_list import fetch_fund_list
from scripts.logging_utils import rotating_handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, help="指定输出目录, 默认 data/fund_list/时间戳")
    args = parser.parse_args()
    folder = args.run_dir or ROOT / "data" / "fund_list" / datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = ROOT / "logs/fund_list"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), rotating_handler(log_dir / "fund_list.log")],
    )
    try:
        summary = fetch_fund_list(Source(), folder, date.today())
    except Exception as exc:
        logging.error("Fund list update failed: %s", exc)
        return 1
    logging.info("Fund list saved: %s; total=%s", summary["output"], summary["total"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
