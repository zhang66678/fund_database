"""用 AKShare 核实的区间收益修正本地复权净值跳变."""

from datetime import date
from decimal import Decimal

import pandas as pd

from database.connection import connect
from database.nav_corrections import UNIT_TOLERANCE, latest_nav, write_nav_changes


DISCREPANCY = "adj_return_discrepancy"
CONSISTENT = "consistent_with_akshare"
MISSING_RETURN = "missing_tushare_adj_return"
RETURN_TOLERANCE = Decimal("0.0005")
PRICE_PRECISION = Decimal("0.0000000001")
REVIEW_COLUMNS = {
    "ts_code", "nav_date", "prev_date", "ann_date", "review_status", "adj_nav", "prev_adj_nav",
    "unit_nav", "prev_unit_nav", "akshare_unit_nav", "akshare_prev_unit_nav", "akshare_accum_nav",
    "akshare_prev_accum_nav", "reference_move_pct", "reinvestment_move_pct",
}


def _checked_returns(group, history):
    pairs = {row[0]: (previous, row) for previous, row in zip(history, history[1:])}
    returns = {}
    for row in group.loc[group.review_status.isin([DISCREPANCY, MISSING_RETURN])].itertuples():
        day = date.fromisoformat(row.nav_date)
        if day not in pairs:
            raise ValueError("缺少断点前后的本地净值")
        before, after = pairs[day]
        original = Decimal(row.reference_move_pct) / 100 + 1
        reinvested = Decimal(row.reinvestment_move_pct) / 100 + 1
        if not original.is_finite() or not reinvested.is_finite() or min(original, reinvested) <= 0:
            raise ValueError("AKShare 核验收益无效")
        if abs(original - reinvested) > RETURN_TOLERANCE:
            raise ValueError("AKShare 两种分红口径未能核对一致")
        if before[0].isoformat() != row.prev_date or after[5].isoformat() != row.ann_date:
            raise ValueError("原库日期或公告版本已变化")
        if before[3] and after[3] and min(before[3], after[3]) > 0 and abs(after[3] / before[3] - original) <= RETURN_TOLERANCE:
            continue
        old_before = Decimal(row.prev_adj_nav) if row.prev_adj_nav else None
        old_after = Decimal(row.adj_nav) if row.adj_nav else None
        if before[3] != old_before or after[3] != old_after:
            raise ValueError("原库复权净值已变化")
        for record, local, ak_unit, ak_accum in (
            (before, row.prev_unit_nav, row.akshare_prev_unit_nav, row.akshare_prev_accum_nav),
            (after, row.unit_nav, row.akshare_unit_nav, row.akshare_accum_nav),
        ):
            if (record[1] is None or record[2] is None or record[1] != Decimal(local)
                    or not ak_unit or not ak_accum or abs(record[1] - Decimal(ak_unit)) > UNIT_TOLERANCE
                    or abs(record[2] - Decimal(ak_accum)) > UNIT_TOLERANCE):
                raise ValueError("断点前后单位或累计净值未能核对一致")
        if day in returns:
            raise ValueError("同一净值日有重复异常记录")
        returns[day] = original
    return returns


def _changes(code, group, history):
    returns = _checked_returns(group, history)
    if not returns:
        return []
    first = min(returns)
    unresolved = group.loc[~group.review_status.isin([DISCREPANCY, CONSISTENT, MISSING_RETURN]), "nav_date"]
    if unresolved.eq("").any() or unresolved.ge(first.isoformat()).any():
        raise ValueError("受影响区间还有未解决的异常")
    changes = []
    scale = Decimal(1)
    previous = None
    for day, unit, _, adj, is_open, ann_date in history:
        if day < first:
            previous = adj
            continue
        if day in returns:
            if not previous or previous <= 0:
                raise ValueError("缺少有效的前一日复权净值")
            adjusted = (previous * returns[day]).quantize(PRICE_PRECISION)
            scale = adjusted / adj if adj and adj > 0 else None
        elif adj is None and is_open == 0:
            continue
        elif not adj or adj <= 0 or scale is None:
            raise ValueError("受影响区间还有未经核实的复权净值缺口")
        else:
            adjusted = (adj * scale).quantize(PRICE_PRECISION)
        if scale is not None:
            if abs(scale - 1) <= Decimal("0.00000001"):
                scale = Decimal(1)
                adjusted = adj
        if not 0 < adjusted < 10**14:
            raise ValueError("重新计算的复权净值超出范围")
        if adjusted != adj:
            changes.append((code, day, ann_date, unit, adj, adjusted, "akshare_return_check"))
        previous = adjusted
    return changes


def repair_review(database, path, *, apply=False):
    review = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    if not {"ts_code", "review_status"}.issubset(review):
        raise ValueError("请选择 AKShare 复核结果")
    affected = review.loc[review.review_status.isin([DISCREPANCY, MISSING_RETURN]), "ts_code"].unique()
    if not len(affected):
        return 0, 0, None, {}
    if missing := REVIEW_COLUMNS - set(review):
        raise ValueError(f"复核结果缺少列: {', '.join(sorted(missing))}")
    if not review.ts_code.str.fullmatch(r"[0-9]{6}\.OF").all():
        raise ValueError("复核结果包含无效基金代码")
    changes, skipped = [], {}
    backup = None
    with connect(database, read_only=not apply) as db:
        if apply:
            db.execute("BEGIN TRANSACTION")
        for code, group in review.loc[review.ts_code.isin(affected)].groupby("ts_code", sort=False):
            try:
                changes.extend(_changes(code, group, latest_nav(db, code)))
            except (ValueError, ArithmeticError) as exc:
                skipped[code] = str(exc)
        if apply and changes:
            backup = write_nav_changes(db, database, changes, scope="batch", source_file=str(path.resolve()))
        if apply:
            db.execute("COMMIT")
    return len(changes), len({row[0] for row in changes}), backup, skipped
