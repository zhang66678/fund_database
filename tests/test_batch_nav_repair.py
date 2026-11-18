from datetime import date
from decimal import Decimal
import sys

import pandas as pd

from database.connection import connect
from database.schema import initialize
from etl.batch_nav_repair import repair_review
from etl.nav_reconcile import _check_interval
from scripts import audit_nav


def test_review_and_apply_corrects_only_verified_breaks(tmp_path, monkeypatch):
    codes = ["000001.OF", "000002.OF", "000003.OF"]
    days = [date(2026, 6, day) for day in range(1, 6)]
    units = list(map(Decimal, ["1", "1.01", "1.02", "1.03", "1.05"]))
    old = list(map(Decimal, ["2", "2.02", "5.1", "5.15", "2.1"]))
    database = tmp_path / "funds.duckdb"
    with connect(database) as db:
        initialize(db)
        db.executemany("""INSERT INTO fund_nav (ts_code, nav_date, ann_date, unit_nav, accum_nav, adj_nav)
                          VALUES (?, ?, ?, ?, ?, ?)""",
                       [(code, day, day, unit, unit, adj) for code in codes for day, unit, adj in zip(days, units, old)])
        db.execute("UPDATE fund_nav SET adj_nav = 6 WHERE ts_code = ? AND nav_date = ?", [codes[2], days[2]])

    records = []
    for code in codes:
        for index in (2, 4):
            expected = (units[index] / units[index - 1] - 1) * 100
            records.append(dict(ts_code=code, nav_date=days[index], ann_date=days[index], prev_date=days[index - 1],
                                unit_nav=units[index], prev_unit_nav=units[index - 1], adj_nav=old[index],
                                prev_adj_nav=old[index - 1], akshare_unit_nav=units[index], akshare_accum_nav=units[index],
                                akshare_prev_unit_nav=units[index - 1], akshare_prev_accum_nav=units[index - 1],
                                reference_move_pct=expected, reinvestment_move_pct=expected,
                                review_status="adj_return_discrepancy" if code != codes[1] else "akshare_failed"))
    review = tmp_path / "review.csv"
    scan = tmp_path / "scan.csv"
    pd.DataFrame(records).to_csv(scan, index=False)

    def offline_review(path, codes, *args, **kwargs):
        assert codes is None
        return pd.read_csv(path, dtype=str)

    monkeypatch.setattr(audit_nav, "_review", offline_review)
    monkeypatch.setattr(sys, "argv", ["audit_nav", "--review", str(scan), "--output", str(review),
                                      "--database", str(database), "--apply"])
    assert audit_nav.main() == 1  # 第 2 只基金的接口失败仍反映到退出码.

    with connect(database, read_only=True) as db:
        def prices(code):
            return [value for value, in db.execute("SELECT adj_nav FROM fund_nav WHERE ts_code = ? ORDER BY nav_date", [code]).fetchall()]

        assert prices(codes[0]) == [unit * 2 for unit in units]
        assert prices(codes[1]) == old
        assert prices(codes[2]) == [*old[:2], Decimal(6), *old[3:]]
    backups = list((tmp_path / "nav_audit/backups").glob("*.csv"))
    assert len(backups) == 1 and len(pd.read_csv(backups[0])) == 2
    assert "reason" not in pd.read_csv(backups[0]).columns
    assert repair_review(database, review, apply=True)[:3] == (0, 0, None)


def test_fill_verified_missing_adjusted_nav_and_continue_series(tmp_path):
    code = "003092.OF"
    days = [date(2026, 6, day) for day in range(1, 5)]
    units = [Decimal(value) for value in ("1", "1.01", "1.02", "1.03")]
    old = [Decimal("2"), None, None, Decimal("5.15")]
    database = tmp_path / "funds.duckdb"
    with connect(database) as db:
        initialize(db)
        db.executemany("""INSERT INTO fund_nav (ts_code, nav_date, ann_date, unit_nav, accum_nav, adj_nav)
                          VALUES (?, ?, ?, ?, ?, ?)""",
                       [(code, day, day, unit, unit, adj) for day, unit, adj in zip(days, units, old)])
    rows = []
    for index in range(1, len(days)):
        expected = (units[index] / units[index - 1] - 1) * 100
        rows.append(dict(ts_code=code, nav_date=days[index], ann_date=days[index], prev_date=days[index - 1],
                         unit_nav=units[index], prev_unit_nav=units[index - 1], adj_nav=old[index],
                         prev_adj_nav=old[index - 1], akshare_unit_nav=units[index], akshare_accum_nav=units[index],
                         akshare_prev_unit_nav=units[index - 1], akshare_prev_accum_nav=units[index - 1],
                         reference_move_pct=expected, reinvestment_move_pct=expected,
                         review_status="missing_tushare_adj_return"))
    review = tmp_path / "review.csv"
    pd.DataFrame(rows).to_csv(review, index=False)
    assert repair_review(database, review, apply=True)[:2] == (3, 1)
    with connect(database, read_only=True) as db:
        assert db.execute("SELECT adj_nav FROM fund_nav ORDER BY nav_date").fetchall() == [(unit * 2,) for unit in units]
    assert repair_review(database, review, apply=True)[:3] == (0, 0, None)


def test_confirmed_cash_amount_resolves_only_the_verified_dividend():
    before, day = date(2026, 2, 5), date(2026, 2, 6)
    unit = pd.Series({before: 1.601, day: 1.520})
    accum = pd.Series({before: 1.601, day: 1.601})
    cash = {day: 0.0805}
    row = dict(nav_date=str(day), prev_date=str(before), unit_nav='1.520', prev_unit_nav='1.601',
               accum_nav='1.601', prev_accum_nav='1.601', adj_move_pct='-0.03288455')
    assert _check_interval(row, unit, accum, cash, set())['review_status'] == 'dividend_data_conflict'
    checked = _check_interval(row, unit, accum, cash, set(), confirmed_cash=cash)
    assert checked['review_status'] == 'consistent_with_akshare' and checked['cash_amount_confirmed']
    assert _check_interval(row, unit, accum, cash, set(), confirmed_cash={day: 0.081})['review_status'] == 'dividend_data_conflict'
