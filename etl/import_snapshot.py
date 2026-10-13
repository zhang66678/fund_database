"""将单基金核查快照转换并写入数据库, 不请求 Tushare."""

import logging
from decimal import Decimal
from pathlib import Path

import pandas as pd

from database.connection import DATABASE_PATH, connect
from database.schema import TABLES, initialize
from database.writer import upsert


FILES = {"fund_basic": "fund_basic.csv", "fund_nav": "fund_nav.csv",
         "fund_dividend": "fund_div.csv", "trade_calendar": "trade_cal.csv"}


def normalize(table: str, source: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """CSV 和接口响应共用类型转换及主键检查."""
    spec = TABLES[table]
    # Tushare 部分缺失日期返回空格, 与空字符串一起转换为 NULL.
    frame = source.loc[:, list(spec["columns"])].astype("string").replace(r"^\s*$", pd.NA, regex=True)
    for column, kind in spec["columns"].items():
        if kind == "DATE":
            frame[column] = pd.to_datetime(frame[column], format="%Y%m%d", errors="raise").dt.date
        elif kind.startswith("DECIMAL"):
            frame[column] = frame[column].map(lambda x: None if pd.isna(x) else Decimal(x))
        elif kind == "INTEGER":
            frame[column] = pd.to_numeric(frame[column], errors="raise").astype("Int64")
    duplicates = int(frame.duplicated().sum())
    frame = frame.drop_duplicates()
    if frame[list(spec["key"])].isna().any().any() or frame.duplicated(list(spec["key"])).any():
        raise ValueError(f"{table}: missing or conflicting primary key")
    return frame, duplicates


def prepare(folder: Path) -> tuple[dict, dict]:
    """统一类型, 去掉完全重复行, 主键冲突则停止, 不任意覆盖源记录."""
    frames, duplicates = {}, {}
    for table, filename in FILES.items():
        frames[table], duplicates[table] = normalize(table, pd.read_csv(folder / filename, dtype="string"))
    if len(frames["fund_basic"]) != 1:
        raise ValueError("This importer requires one fund basic record")
    code = frames["fund_basic"].ts_code.iloc[0]
    for table in ("fund_nav", "fund_dividend"):
        if not frames[table].ts_code.eq(code).all():
            raise ValueError(f"{table}: unexpected fund code")
    return frames, duplicates


def import_snapshot(folder: Path, database_path: Path = DATABASE_PATH) -> dict:
    """四张表一起提交, 失败时全部回滚, 不删除数据库中的历史记录."""
    logging.info("Import started: source=%s database=%s", folder, database_path)
    frames, duplicates = prepare(folder)
    results = {}
    with connect(database_path) as connection:
        connection.execute("BEGIN TRANSACTION")
        try:
            initialize(connection)
            for table, frame in frames.items():
                results[table] = {**upsert(connection, table, frame), "source_duplicates": duplicates[table]}
            connection.execute("COMMIT")
        except Exception:
            # 事务边界捕获后必须重新抛出, 不把失败伪装成成功.
            connection.execute("ROLLBACK")
            logging.exception("Import failed and rolled back")
            raise
    for table, counts in results.items():
        logging.info("%s: %s", table, counts)
    logging.info("Import completed")
    return results
