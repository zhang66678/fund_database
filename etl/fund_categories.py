"""采集分类快照并独立入库, 不修改 Tushare 基础资料和净值."""

import json
from datetime import date
from pathlib import Path

import pandas as pd

from api.ifind_categories import CATEGORY_FIELDS, SOURCE, fetch_categories
from database.connection import DATABASE_PATH, connect
from database.schema import initialize
from database.writer import upsert
from etl.add_funds import _normalize_code


def update_categories(client, folder: Path, database_path: Path = DATABASE_PATH, *,
                      codes: list[str] | None = None, batch_size: int = 20,
                      snapshot_date: date | None = None, snapshot_only: bool = False) -> dict:
    """分批获取本地基金分类, 全部请求成功后提交, 不在网络期间持有写连接.

    codes 不传时查询全部本地基金; 快照模式可显式查询尚未入库的代码.
    缺少分类的代码也保存空记录, 但不会参与同类分组.
    """
    if not 1 <= batch_size <= 100:
        raise ValueError("batch_size 必须为 1 至 100")
    if folder.exists():
        raise FileExistsError("分类快照目录已存在, 请指定新目录")
    targets = None if codes is None else sorted({_normalize_code(code) for code in codes})
    if not snapshot_only or targets is None:
        if not database_path.is_file():
            raise FileNotFoundError("数据库不存在, 请先运行 scripts.init_database")
        with connect(database_path, read_only=True) as db:
            known = {row[0] for row in db.execute("SELECT ts_code FROM fund_basic").fetchall()}
        if targets is not None and set(targets) - known:
            raise ValueError("分类更新包含未入库基金, 请先添加或使用 --snapshot-only")
        targets = sorted(known) if targets is None else targets
    if not targets:
        raise ValueError("没有需要查询分类的基金")
    observed = snapshot_date or date.today()
    parts = [fetch_categories(client, targets[start:start + batch_size])
             for start in range(0, len(targets), batch_size)]
    frame = pd.concat(parts, ignore_index=True)
    frame["source"] = SOURCE
    frame["snapshot_date"] = observed
    summary = {"source": SOURCE, "snapshot_date": observed.isoformat(),
               "indicators": list(CATEGORY_FIELDS), "classification_level": 2,
               "requested_funds": len(targets), "classified_funds": int(frame.category.notna().sum()),
               "missing_funds": int(frame.category.isna().sum()),
               "category_count": int(frame.category.nunique()), "snapshot_only": snapshot_only}
    folder.mkdir(parents=True, exist_ok=False)
    frame.to_csv(folder / "fund_categories.csv", index=False, encoding="utf-8-sig")
    if not snapshot_only:
        with connect(database_path) as db:
            db.execute("BEGIN TRANSACTION")
            try:
                initialize(db)
                summary["counts"] = upsert(db, "fund_category", frame)
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
