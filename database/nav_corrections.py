"""将核实过的复权净值写回 fund_nav, 并备份原值."""

import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

from database.connection import connect


UNIT_TOLERANCE = Decimal("0.0001")
CHANGE_COLUMNS = ("ts_code", "nav_date", "ann_date", "unit_nav", "adj_nav_before", "adj_nav_after", "source")


def latest_nav(db, code):
    rows = db.execute("""
        SELECT n.nav_date, n.unit_nav, n.accum_nav, n.adj_nav, c.is_open, n.ann_date
        FROM fund_nav n LEFT JOIN trade_calendar c ON c.exchange = 'SSE' AND c.cal_date = n.nav_date
        WHERE n.ts_code = ?
        QUALIFY row_number() OVER (PARTITION BY n.nav_date ORDER BY n.ann_date DESC NULLS LAST) = 1
        ORDER BY n.nav_date
    """, [code]).fetchall()
    if not rows:
        raise ValueError(f"{code} 在本地库没有历史净值")
    return rows


def current_nav(db, code):
    return [row[:5] for row in latest_nav(db, code)]


def validate_series(base, reviewed):
    """完整覆盖本地净值日, 仅允许跳过明确休市的日期."""
    closed_days = 0
    for day, unit, _, _, is_open in base:
        other = reviewed.get(day)
        if other is None:
            if is_open != 0:
                raise ValueError(f"修正序列缺少 {day}, 请补齐历史后再导入")
            closed_days += 1
        elif unit is None or abs(unit - other[0]) > UNIT_TOLERANCE:
            raise ValueError(f"{day} 单位净值与本地库不符, 请先核对原值")
    return closed_days


def write_nav_changes(db, database, changes, *, scope, source_file):
    """先备份原值, 再由调用方在当前事务内提交修改."""
    backup_dir = Path(database).parent / "nav_audit/backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc)
    backup = backup_dir / f"fund_nav_before_{scope}_{timestamp:%Y%m%d_%H%M%S_%fZ}.csv"
    with backup.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow((*CHANGE_COLUMNS, "source_file", "prepared_at_utc"))
        writer.writerows((*row, source_file, timestamp.isoformat()) for row in changes)
    # 使用十进制字符串保留数据库的净值精度.
    staged = pd.DataFrame(changes, columns=CHANGE_COLUMNS)
    for field in ("unit_nav", "adj_nav_before", "adj_nav_after"):
        staged[field] = staged[field].astype("string")
    db.register("nav_repair", staged)
    updated = db.execute("""
        UPDATE fund_nav AS n SET adj_nav = CAST(r.adj_nav_after AS DECIMAL(24,10))
        FROM nav_repair r WHERE n.ts_code = r.ts_code AND n.nav_date = r.nav_date AND n.ann_date = r.ann_date
          AND n.unit_nav IS NOT DISTINCT FROM CAST(r.unit_nav AS DECIMAL(24,10))
          AND n.adj_nav IS NOT DISTINCT FROM CAST(r.adj_nav_before AS DECIMAL(24,10))
    """).fetchone()[0]
    if updated != len(changes):
        raise ValueError("实际修改行数与预期不符, 本次修正已回滚")
    return backup


def save_corrections(database, code, reviewed, *, source, source_file, apply=False):
    with connect(database, read_only=not apply) as db:
        if apply:
            db.execute("BEGIN TRANSACTION")
        latest = latest_nav(db, code)
        base = [row[:5] for row in latest]
        closed_days = validate_series(base, reviewed)
        matched = len(base) - closed_days
        changes = [(code, day, ann_date, unit, adj, reviewed[day][1], source)
                   for day, unit, _, adj, _, ann_date in latest
                   if day in reviewed and adj != reviewed[day][1]]
        backup = None
        if apply and changes:
            backup = write_nav_changes(db, database, changes, scope=code, source_file=source_file)
        if apply:
            db.execute("COMMIT")
    return {"rows": matched, "changes": len(changes), "closed_dates_without_correction": closed_days,
            "reference_only_dates": len(reviewed) - matched, "backup": backup}
