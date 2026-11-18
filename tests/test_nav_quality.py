from datetime import date

from database.connection import connect
from database.schema import initialize
from etl.nav_quality import audit_nav


def test_audit_excludes_only_changes_explained_by_verified_events(tmp_path):
    database = tmp_path / "audit.duckdb"
    previous, current = date(2026, 6, 1), date(2026, 6, 2)
    rows = {
        "000001.OF": (0.7, 1, 1),        # 已实施分红与净值一致.
        "000002.OF": (0.7, 1, 1.1),      # 分红无法解释复权变化.
        "000003.OF": (0.7, 1, 1),        # 分红仍处于预案.
        "000004.OF": (0.98, 1.28, 1.4),  # 除息后仍有大幅涨幅.
        "000005.OF": (0.7, 1, None),     # 复权净值缺失.
        "000006.OF": (0.5, 1, 1),        # 复权收益与已核实折算一致.
        "000007.OF": (0.5, 1, 1.1),      # 复权收益与折算不一致.
        "000008.OF": (0.7, 1, 1),        # 分红记录金额冲突.
    }
    with connect(database) as db:
        initialize(db)
        for code, values in rows.items():
            db.execute("INSERT INTO fund_basic (ts_code) VALUES (?)", [code])
            db.executemany("""INSERT INTO fund_nav (ts_code, ann_date, nav_date, unit_nav, accum_nav, adj_nav)
                              VALUES (?, ?, ?, ?, ?, ?)""",
                           [(code, previous, previous, 1, 1, 1), (code, current, current, *values)])
            if code not in {"000006.OF", "000007.OF"}:
                db.execute("""INSERT INTO fund_dividend (ts_code, ann_date, ex_date, net_ex_date, div_cash, div_proc)
                              VALUES (?, ?, ?, ?, 0.3, ?)""",
                           [code, previous, previous, current, "预案" if code == "000003.OF" else "实施"])
        db.executemany("""INSERT INTO fund_dividend (ts_code, ann_date, ex_date, net_ex_date, div_cash, div_proc)
                          VALUES (?, ?, ?, ?, ?, '实施')""",
                       [("000001.OF", current, previous, current, 0.3), ("000008.OF", current, previous, current, 0.25)])

    local, _ = audit_nav(database, start=current, end=current)
    assert set(local.ts_code) == set(rows) - {"000001.OF"}
    assert local.set_index("ts_code").at["000005.OF", "reason"] == "invalid_adj_nav"

    splits = tmp_path / "splits.csv"
    splits.write_text("ts_code,event_date,split_ratio\n000006,2026-06-02,2\n000007.OF,2026-06-02,2\n", encoding="utf-8")
    reviewed, _ = audit_nav(database, start=current, end=current, split_events=splits)
    assert set(reviewed.ts_code) == set(local.ts_code) - {"000006.OF"}
