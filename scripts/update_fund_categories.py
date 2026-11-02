"""更新 iFinD 投资类型分类: python -m scripts.update_fund_categories."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from api.ifind_client import IfindClient
from database.connection import DATABASE_PATH, ROOT
from etl.fund_categories import update_categories


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--run-dir", type=Path, help="快照目录, 已存在时不覆盖")
    parser.add_argument("--snapshot-only", action="store_true", help="只保存快照, 不修改数据库")
    parser.add_argument("--codes", nargs="+", help="只查询指定基金, 默认查询全部本地基金")
    parser.add_argument("--batch-size", type=int, default=20, choices=range(1, 101), metavar="1-100",
                        help="单次请求基金数, 默认 20; 实际权限和配额由 iFinD 账号决定")
    args = parser.parse_args()
    folder = args.run_dir or ROOT / "data/fund_categories" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    try:
        summary = update_categories(IfindClient(), folder, args.database, codes=args.codes,
                                    batch_size=args.batch_size, snapshot_only=args.snapshot_only)
    except (ValueError, OSError) as exc:
        print(f"分类更新失败: {exc}")
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
