"""批量写入 schema 中的业务表, 由事务保证每批数据的原子性."""

from database.schema import TABLES


def update_targets(connection) -> list[tuple]:
    """从完整基金表读取任务, 包括已终止基金和没有净值的新入库基金."""
    return connection.execute('''
        SELECT b.ts_code, b.name, b.market, b.found_date, max(n.nav_date)
        FROM fund_basic b LEFT JOIN fund_nav n ON b.ts_code = n.ts_code
        GROUP BY b.ts_code, b.name, b.market, b.found_date ORDER BY b.ts_code
    ''').fetchall()


def calendar_last_date(connection):
    return connection.execute("SELECT max(cal_date) FROM trade_calendar WHERE exchange = ?", ["SSE"]).fetchone()[0]


def asset_last_date(connection, code: str):
    """资产披露频率可能低于净值, 增量更新使用资产表自己的最新日期."""
    return connection.execute("SELECT max(nav_date) FROM fund_asset WHERE ts_code = ?", [code]).fetchone()[0]


def write_batch(connection, frames: dict) -> dict:
    """单个基金的数据共同提交, 失败时回滚."""
    connection.execute("BEGIN TRANSACTION")
    try:
        result = {table: upsert(connection, table, frame) for table, frame in frames.items()}
        connection.execute("COMMIT")
        return result
    except Exception:
        connection.execute("ROLLBACK")
        raise


def upsert(connection, table: str, frame) -> dict:
    """统计新增, 变化和未变化记录, 仅写入新增或变化的数据."""
    spec = TABLES[table]
    columns, keys = list(spec["columns"]), spec["key"]
    values = [name for name in columns if name not in keys]
    # SQL 标识符只取自固定 schema, 用户数据通过注册表传入.
    join = " AND ".join(f't."{k}" = s."{k}"' for k in keys)
    changed = " OR ".join(f't."{c}" IS DISTINCT FROM s."{c}"' for c in values)
    connection.register("incoming", frame)
    try:
        # 先转换到目标类型, 避免浮点/Decimal 差异影响幂等性统计.
        casts = ", ".join(f'CAST("{c}" AS {spec["columns"][c]}) AS "{c}"' for c in columns)
        connection.execute(f'CREATE OR REPLACE TEMP TABLE staged AS SELECT {casts} FROM incoming')
        inserted, updated = connection.execute(
            f'SELECT count(*) FILTER (WHERE t."{keys[0]}" IS NULL), '
            f'count(*) FILTER (WHERE t."{keys[0]}" IS NOT NULL AND ({changed})) '
            f'FROM staged s LEFT JOIN "{table}" t ON {join}'
        ).fetchone()
        names = ", ".join(f'"{c}"' for c in columns)
        selected = ", ".join(f's."{c}"' for c in columns)
        key_sql = ", ".join(f'"{k}"' for k in keys)
        assignments = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in values)
        connection.execute(
            f'INSERT INTO "{table}" ({names}) SELECT {selected} FROM staged s '
            f'LEFT JOIN "{table}" t ON {join} WHERE t."{keys[0]}" IS NULL OR ({changed}) '
            f'ON CONFLICT ({key_sql}) DO UPDATE SET {assignments}'
        )
        return {"inserted": inserted, "updated": updated, "unchanged": len(frame) - inserted - updated}
    finally:
        connection.unregister("incoming")
