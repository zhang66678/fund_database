"""生成候选批次并添加场外基金基础资料, 跳过已有代码, 不下载历史净值."""

import logging
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pandas as pd

from api.fund_fields import BASIC
from api.tushare_client import Source
from database.connection import DATABASE_PATH, connect
from database.writer import existing_fund_codes, write_batch
from etl.import_snapshot import normalize


DEFAULT_BATCH_SIZE = 20


def _normalize_code(code: str) -> str:
    code = code.strip().upper()
    if re.fullmatch(r"[0-9]{6}", code):
        code += ".OF"
    if not re.fullmatch(r"[0-9]{6}\.OF", code):
        raise ValueError("请输入六位场外基金代码, 例如 040001 或 040001.OF")
    return code


def _fetch_basic(source, code: str) -> pd.DataFrame:
    """单只与批量添加共用接口校验及清洗, CSV 只提供代码."""
    raw = source.query("fund_basic", BASIC, ts_code=code, market="O")
    if len(raw) != 1 or not raw.ts_code.eq(code).all() or not raw.market.eq("O").all():
        raise ValueError("未获取到唯一匹配的场外基金资料, 数据库未修改")
    frame, _ = normalize("fund_basic", raw)
    if frame.found_date.isna().any() or frame.name.fillna("").str.strip().eq("").any():
        raise ValueError("基金名称或成立日期缺失, 暂不能加入更新清单")
    return frame


def add_fund(source, code: str, database_path: Path = DATABASE_PATH) -> dict:
    """保留原单只添加行为: 获取最新资料并 UPSERT, 不删除历史数据."""
    code = _normalize_code(code)
    if not database_path.is_file():
        raise FileNotFoundError("请先运行 python -m scripts.init_database 初始化数据库")
    logging.info("Adding fund: %s", code)
    frame = _fetch_basic(source, code)
    with connect(database_path) as connection:
        counts = write_batch(connection, {"fund_basic": frame})["fund_basic"]
    logging.info("Fund saved: %s %s %s", code, frame.name.iloc[0], counts)
    return counts


def prepare_fund_batch(
    list_path: Path, output_path: Path, database_path: Path = DATABASE_PATH, *,
    limit: int = DEFAULT_BATCH_SIZE, statuses: Sequence[str] = ("L",),
    fund_types: Sequence[str] | None = None,
) -> dict:
    """从名单筛选固定批次, 只读数据库并写 CSV, 不初始化 API 客户端.

    list_path 为快照根目录时, 按 YYYYMMDD_HHMMSS 目录名取最新名单.
    排重后按成立日期及代码排序再取 limit 条, 以固定文件保存便于重试.
    """
    if limit < 1:
        raise ValueError("筛选数量必须大于 0")
    if not statuses or set(statuses) - {"L", "D", "I"}:
        raise ValueError("基金状态请选择 L, D 或 I")
    if not database_path.is_file():
        raise FileNotFoundError("请先运行 python -m scripts.init_database 初始化数据库")
    if list_path.is_dir():
        snapshots = sorted(path for path in list_path.glob("*/fund_basic_all.csv")
                           if re.fullmatch(r"\d{8}_\d{6}", path.parent.name))
        if not snapshots:
            raise FileNotFoundError("未找到基金名单快照, 请用 --from-list 指定 CSV 路径")
        list_path = snapshots[-1]
    logging.info("Batch selection started: source=%s limit=%s status=%s types=%s",
                 list_path, limit, list(statuses), fund_types)
    columns = ["ts_code", "name", "fund_type", "found_date", "status", "market"]
    frame = pd.read_csv(list_path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    if set(columns) - set(frame.columns):
        raise ValueError(f"基金总名单必须包含这些列: {', '.join(columns)}")
    frame = frame.loc[:, columns].apply(lambda column: column.str.strip())
    total = len(frame)
    codes = frame.ts_code.str.upper()
    frame["ts_code"] = codes.where(~codes.str.fullmatch(r"[0-9]{6}"), codes + ".OF")
    frame["found_date"] = pd.to_datetime(frame.found_date, format="%Y%m%d", errors="coerce")
    valid = frame.ts_code.str.fullmatch(r"[0-9]{6}\.OF") & frame.name.ne("") & frame.found_date.notna()
    invalid = int((~valid).sum())
    frame = frame.loc[valid]
    duplicates = int(frame.ts_code.duplicated().sum())
    frame = frame.drop_duplicates("ts_code")
    if fund_types:
        unknown = set(fund_types) - set(frame.fund_type)
        if unknown:
            raise ValueError(f"名单中没有这些基金类型: {', '.join(sorted(unknown))}")
    frame = frame.loc[frame.market.eq("O") & frame.status.isin(statuses)]
    if fund_types:
        frame = frame.loc[frame.fund_type.isin(fund_types)]
    matched = len(frame)
    with connect(database_path, read_only=True) as connection:
        existing = existing_fund_codes(connection)
    skipped = int(frame.ts_code.isin(existing).sum())
    # 先排除已有基金再限数量, 防止已有代码占用本批名额.
    frame = frame.loc[~frame.ts_code.isin(existing)].sort_values(["found_date", "ts_code"])
    available = len(frame)
    selected = frame.head(limit).drop(columns="market")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    selected.to_csv(output_path, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d", mode="x")
    summary = {
        "status": "completed", "source_file": str(list_path), "database": str(database_path),
        "output_file": str(output_path), "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "statuses": list(statuses), "fund_types": list(fund_types) if fund_types else None,
        "limit": limit, "total": total, "invalid": invalid, "duplicates": duplicates,
        "matched": matched, "skipped_existing": skipped, "available": available, "selected": len(selected),
    }
    logging.info("Batch selection finished: selected=%s available=%s existing=%s invalid=%s duplicates=%s output=%s",
                 len(selected), available, skipped, invalid, duplicates, output_path)
    return summary


def add_funds(csv_path: Path, database_path: Path = DATABASE_PATH, *, source=None) -> dict:
    """从固定 CSV 批次逐只添加, 已有代码不请求接口, 失败后可重跑同一文件.

    source 可注入测试客户端; 默认仅在出现新代码时初始化真实客户端.
    每只基金单独提交事务, 单只失败不撤销其他基金的成功结果.
    """
    batch = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    if "ts_code" not in batch.columns:
        raise ValueError("批次 CSV 必须包含 ts_code 列")
    if not database_path.is_file():
        raise FileNotFoundError("请先运行 python -m scripts.init_database 初始化数据库")
    summary = {
        "source_file": str(csv_path), "database": str(database_path),
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "completed", "total": len(batch), "added": 0,
        "skipped_existing": 0, "skipped_duplicate": 0, "failed": 0, "funds": [],
    }
    logging.info("Batch add started: file=%s rows=%s database=%s", csv_path, len(batch), database_path)
    with connect(database_path) as connection:
        # 必须读取 fund_basic 全表, 包括已终止及尚无净值的基金.
        existing = existing_fund_codes(connection)
        seen = set()
        for row, raw_code in enumerate(batch.ts_code, start=2):
            record = {"row": row, "code": None}
            summary["funds"].append(record)
            try:
                code = _normalize_code(raw_code)
                record["code"] = code
                duplicate = code in seen
                seen.add(code)
                if duplicate:
                    record["status"] = "skipped_duplicate"
                elif code in existing:
                    record["status"] = "skipped_existing"
                else:
                    # 延迟初始化: 全是已有代码的批次不需要 Token 或 API 请求.
                    if source is None:
                        source = Source()
                    frame = _fetch_basic(source, code)
                    record.update(status="added", name=frame.name.iloc[0],
                                  counts=write_batch(connection, {"fund_basic": frame})["fund_basic"])
                    existing.add(code)
                summary[record["status"]] += 1
                logging.info("Batch fund: row=%s code=%s status=%s", row, code, record["status"])
            except Exception as exc:
                # 请求层已脱敏接口错误, 保留失败原因并继续下一只, 不静默忽略.
                record.update(status="failed", error=str(exc))
                summary["failed"] += 1
                summary["status"] = "partial"
                logging.error("Batch fund failed: row=%s code=%s error=%s", row, record["code"], exc)
    summary["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    logging.info("Batch add finished: status=%s added=%s existing=%s duplicates=%s failed=%s",
                 summary["status"], summary["added"], summary["skipped_existing"],
                 summary["skipped_duplicate"], summary["failed"])
    return summary
