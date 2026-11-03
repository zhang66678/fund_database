"""将完整场外基金名单批量同步到基础资料表, 保留其他表及名单外基金."""

import json
from pathlib import Path

import pandas as pd

from database.connection import DATABASE_PATH, connect
from database.schema import TABLES
from database.writer import write_batch
from etl.fund_list import STATUSES, latest_snapshot
from etl.import_snapshot import normalize


def sync_fund_list(folder: Path, database_path: Path = DATABASE_PATH) -> dict:
    """复用完整快照, 一次事务新增基金和刷新资料, 不逐只请求接口."""
    if not database_path.is_file():
        raise FileNotFoundError("请先运行 python -m scripts.init_database 初始化数据库")
    if not (folder / "list_summary.json").is_file():
        folder = latest_snapshot(folder)
    manifest = json.loads((folder / "list_summary.json").read_text(encoding="utf-8"))
    raw = pd.read_csv(folder / "fund_basic_all.csv", dtype=str, encoding="utf-8-sig", keep_default_na=False)
    missing = set(TABLES["fund_basic"]["columns"]) - set(raw.columns)
    if missing:
        raise ValueError(f"名单缺少基础字段: {', '.join(sorted(missing))}")
    raw = raw.apply(lambda column: column.str.strip())
    counts = {status: int(raw.status.eq(status).sum()) for status in STATUSES}
    if raw.empty or manifest.get("total") != len(raw) or manifest.get("counts_by_status") != counts:
        raise ValueError("名单行数或状态计数与完整快照摘要不一致")
    if not raw.market.eq("O").all() or not raw.status.isin(STATUSES).all() or raw.ts_code.duplicated().any():
        raise ValueError("名单包含非场外基金, 非法状态或重复代码")

    # 沿用现有入库范围, 源名单中的历史七位代码不截断或映射成六位代码.
    checks = {
        "非六位场外基金代码": raw.ts_code.str.fullmatch(r"[0-9]{6}\.OF"),
        "名称缺失": raw.name.ne(""),
        "成立日期无效": pd.to_datetime(raw.found_date, format="%Y%m%d", errors="coerce").notna(),
    }
    valid = pd.DataFrame(checks).all(axis=1)
    skipped = [
        {"code": raw.at[index, "ts_code"], "reason": "; ".join(reason for reason, mask in checks.items() if not mask.at[index])}
        for index in raw.index[~valid]
    ]
    frame, _ = normalize("fund_basic", raw.loc[valid])
    with connect(database_path) as db:
        previous = dict(db.execute("SELECT ts_code, status FROM fund_basic").fetchall())
        added = sorted(set(frame.ts_code) - previous.keys())
        changes = [
            {"code": row.ts_code, "old_status": previous[row.ts_code], "new_status": row.status}
            for row in frame.itertuples()
            if row.ts_code in previous and previous[row.ts_code] != row.status
        ]
        counts = write_batch(db, {"fund_basic": frame})["fund_basic"]
    return {
        "status": "completed", "source_file": str(folder / "fund_basic_all.csv"),
        "as_of": manifest.get("as_of"), "database": str(database_path), "total": len(raw),
        **counts, "added_codes": added, "status_changes": changes,
        "skipped_invalid": len(skipped), "skipped_records": skipped,
        "missing_from_snapshot": sorted(previous.keys() - set(raw.ts_code)),
    }
