"""建库并导入单基金快照: python -m scripts.init_database."""

import argparse
import logging
from pathlib import Path

from database.connection import DATABASE_PATH, ROOT
from etl.import_snapshot import import_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data/single_fund_audit/20261012_093903")
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    args = parser.parse_args()
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[
        logging.StreamHandler(), logging.FileHandler(ROOT / "logs/database_import.log", encoding="utf-8")])
    try:
        import_snapshot(args.snapshot, args.database)
    except Exception:
        logging.exception("Database initialization failed")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
