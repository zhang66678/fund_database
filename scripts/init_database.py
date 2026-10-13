"""初始化数据库, 可选导入单基金快照: python -m scripts.init_database."""

import argparse
import logging
from pathlib import Path

from database.connection import DATABASE_PATH, ROOT, connect
from database.schema import initialize
from etl.import_snapshot import import_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, help="可选的单基金快照目录, 不指定时仅建库")
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    args = parser.parse_args()
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[
        logging.StreamHandler(), logging.FileHandler(ROOT / "logs/database_import.log", encoding="utf-8")])
    try:
        if args.snapshot is not None:
            import_snapshot(args.snapshot, args.database)
        else:
            logging.info("Database initialization started: %s", args.database)
            with connect(args.database) as connection:
                # 四张表在同一事务中创建, 重复运行不会清空已有数据.
                connection.execute("BEGIN TRANSACTION")
                initialize(connection)
                connection.execute("COMMIT")
            logging.info("Database initialization completed: %s", args.database)
    except Exception:
        logging.exception("Database initialization failed")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
