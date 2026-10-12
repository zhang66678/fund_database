"""添加场外基金资料: python -m scripts.add_fund 040001.OF."""

import argparse
import logging
import re
from pathlib import Path

from database.connection import DATABASE_PATH, ROOT, connect
from database.writer import write_batch
from etl.import_snapshot import normalize
from api.fund_fields import BASIC
from api.tushare_client import Source


def add_fund(source, code: str, database_path: Path = DATABASE_PATH) -> dict:
    """从接口获取真实资料并 UPSERT, 不修改净值或其他历史表."""
    code = code.strip().upper()
    if re.fullmatch(r"[0-9]{6}", code):
        code += ".OF"
    if not re.fullmatch(r"[0-9]{6}\.OF", code):
        raise ValueError("请输入六位场外基金代码, 例如 040001 或 040001.OF")
    if not database_path.is_file():
        raise FileNotFoundError("请先运行 python -m scripts.init_database 初始化数据库")
    logging.info("Adding fund: %s", code)
    raw = source.query("fund_basic", BASIC, ts_code=code, market="O")
    if len(raw) != 1 or not raw.ts_code.eq(code).all() or not raw.market.eq("O").all():
        raise ValueError("未获取到唯一匹配的场外基金资料, 数据库未修改")
    frame, _ = normalize("fund_basic", raw)
    # 后续历史回填需要成立日期, 名称用于页面及更新日志.
    if frame.found_date.isna().any() or frame.name.fillna("").str.strip().eq("").any():
        raise ValueError("基金名称或成立日期缺失, 暂不能加入更新清单")
    with connect(database_path) as connection:
        counts = write_batch(connection, {"fund_basic": frame})["fund_basic"]
    logging.info("Fund saved: %s %s %s", code, frame.name.iloc[0], counts)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("code", help="基金代码, 例如 040001 或 040001.OF")
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    args = parser.parse_args()
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[
        logging.StreamHandler(), logging.FileHandler(ROOT / "logs/add_fund.log", encoding="utf-8")])
    try:
        add_fund(Source(), args.code, args.database)
        logging.info("完成. 请运行 python -m scripts.update_database 获取历史净值和分红; 自定义数据库需传同一 --database")
        return 0
    except Exception as exc:
        # Source 已对接口异常脱敏, 命令入口记录失败并返回非零状态.
        logging.error("Add fund failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
