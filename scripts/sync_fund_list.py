"""获取最新基金名单并同步基础资料: python -m scripts.sync_fund_list."""

import argparse
import json
import logging
from datetime import date, datetime
from pathlib import Path

from api.tushare_client import Source
from database.connection import DATABASE_PATH, ROOT
from etl.fund_list import fetch_fund_list
from etl.sync_fund_list import sync_fund_list


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--from-list", type=Path, nargs="?", const=ROOT / "data/fund_list",
                        help="复用完整名单目录; 不传路径时取最新本地名单, 不请求接口")
    args = parser.parse_args()
    log_dir = ROOT / "logs/fund_list"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[
        logging.StreamHandler(), logging.FileHandler(log_dir / "fund_list_sync.log", encoding="utf-8")])
    try:
        if not args.database.is_file():
            raise FileNotFoundError("请先运行 python -m scripts.init_database 初始化数据库")
        folder = args.from_list
        if folder is None:
            folder = ROOT / "data/fund_list" / datetime.now().strftime("%Y%m%d_%H%M%S")
            folder.mkdir(parents=True, exist_ok=False)
            fetch_fund_list(Source(), folder, date.today())
        summary = sync_fund_list(folder, args.database)
        report = log_dir / f"fund_list_sync_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
        report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.info("名单同步完成: 新增=%s 更新=%s 未变化=%s 状态变化=%s 无效跳过=%s 名单外保留=%s; 摘要: %s",
                     summary["inserted"], summary["updated"], summary["unchanged"], len(summary["status_changes"]),
                     summary["skipped_invalid"], len(summary["missing_from_snapshot"]), report)
        return 0
    except Exception as exc:
        logging.error("名单同步失败: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
