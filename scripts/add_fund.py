"""添加场外基金资料: 单只代码, --file 批量入库或 --from-list 生成候选清单."""

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

from database.connection import DATABASE_PATH, ROOT
from etl.add_funds import DEFAULT_BATCH_SIZE, add_fund, add_funds, prepare_fund_batch
from api.tushare_client import Source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("code", nargs="?", help="基金代码, 例如 040001 或 040001.OF")
    parser.add_argument("--file", type=Path, dest="csv_path", help="固定批次 CSV, 必须有 ts_code 列")
    parser.add_argument("--from-list", type=Path, nargs="?", const=ROOT / "data/exploration",
                        help="仅生成批次清单; 不传路径时取最新本地基金名单")
    parser.add_argument("--limit", type=int, help=f"生成清单时最多选几只, 默认 {DEFAULT_BATCH_SIZE}")
    parser.add_argument("--status", nargs="+", choices=("L", "D", "I"), help="筛选存续状态, 默认 L")
    parser.add_argument("--fund-types", nargs="+", help="筛选基金类型, 可选多个, 如 股票型 混合型")
    parser.add_argument("--output", type=Path, help="生成的批次 CSV 路径, 默认自动生成时间戳文件名")
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    args = parser.parse_args()
    if sum(value is not None for value in (args.code, args.csv_path, args.from_list)) != 1:
        parser.error("基金代码, --file 和 --from-list 三选一")
    if args.from_list is None and any(value is not None for value in (args.limit, args.status, args.fund_types, args.output)):
        parser.error("--limit, --status, --fund-types 和 --output 仅用于 --from-list 模式")
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[
        logging.StreamHandler(), logging.FileHandler(log_dir / "add_fund.log", encoding="utf-8")])
    try:
        if args.from_list is not None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            output = args.output or ROOT / "data/fund_batches" / f"batch_{stamp}.csv"
            summary = prepare_fund_batch(
                args.from_list, output, args.database,
                limit=args.limit if args.limit is not None else DEFAULT_BATCH_SIZE,
                statuses=args.status or ["L"], fund_types=args.fund_types,
            )
            path = log_dir / f"fund_batch_{stamp}.json"
            path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            logging.info("已生成 %s 只基金的候选清单: %s; 筛选摘要: %s", summary["selected"], output, path)
            logging.info("本次未入库. 确认清单后用 --file 指向同一文件, 再运行统一更新命令")
            return 0
        if args.csv_path is not None:
            summary = add_funds(args.csv_path, args.database)
            path = log_dir / f"add_funds_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
            path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            logging.info("批量添加摘要: %s", path)
            logging.info('请运行 python -m scripts.update_database --file "%s" 更新本批基金; 自定义数据库需传同一 --database', args.csv_path)
            return int(summary["status"] != "completed")
        add_fund(Source(), args.code, args.database)
        logging.info("完成. 请运行 python -m scripts.update_database --codes %s 更新该基金; 自定义数据库需传同一 --database", args.code)
        return 0
    except Exception as exc:
        # Source 已对接口异常脱敏, 命令入口记录失败并返回非零状态.
        logging.error("Add fund failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
