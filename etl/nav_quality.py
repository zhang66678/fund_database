"""从本地 Tushare 库筛查可疑净值, 输出供复核的记录."""

from itertools import batched
from math import isfinite

import duckdb
import pandas as pd


def _read_split_events(path):
    columns = ["ts_code", "event_date", "split_ratio"]
    if path is None:
        return pd.DataFrame(columns=columns)
    events = pd.read_csv(path, usecols=columns, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    code = events.ts_code.str.strip().str.upper().str.removesuffix(".OF")
    if not code.str.fullmatch(r"[0-9]{6}").all():
        raise ValueError("折算清单包含无效的场外基金代码")
    events["ts_code"] = code + ".OF"
    events["event_date"] = pd.to_datetime(events.event_date, format="%Y-%m-%d").dt.date
    events["split_ratio"] = pd.to_numeric(events.split_ratio, errors="raise")
    if (events.event_date.isna().any() or
            not events.split_ratio.map(lambda value: isfinite(value) and value > 0).all()):
        raise ValueError("折算日必须有效, 折算比例必须为正的有限数值")
    return events.drop_duplicates()


def audit_nav(database, *, codes=None, start=None, end=None, gap_pp=2.0, move_pct=20.0, split_events=None):
    """每个净值日取最新公告, 保留起始日之前一条净值用于检查接缝."""
    if start and end and start > end:
        raise ValueError("开始日期不能晚于结束日期")
    if any(not isfinite(x) or x <= 0 for x in (gap_pp, move_pct)):
        raise ValueError("涨幅阈值必须为正的有限数值")
    if codes is not None and not codes:
        raise ValueError("审核范围不能为空")
    params = {"codes": codes, "start": start, "end": end, "gap_pp": gap_pp, "move_pct": move_pct}
    with duckdb.connect(str(database), read_only=True, config={"threads": 4, "memory_limit": "2GB"}) as db:
        db.execute("SET preserve_insertion_order = false")
        db.register("verified_split_events", _read_split_events(split_events))
        funds = db.execute("""
            SELECT coalesce(b.ts_code, n.ts_code) AS ts_code, b.name, b.fund_type, b.status AS fund_status,
                   n.ts_code IS NOT NULL AS has_nav
            FROM fund_basic b FULL JOIN (SELECT DISTINCT ts_code FROM fund_nav) n USING (ts_code)
            WHERE $codes::VARCHAR[] IS NULL OR b.ts_code IN (SELECT unnest($codes::VARCHAR[]))
        """, {"codes": codes}).fetchdf()
        if codes is not None and (missing := set(codes) - set(funds.ts_code)):
            raise ValueError(f"基金名单中没有 {', '.join(sorted(missing))}")
        query = """
            WITH latest AS (
                SELECT ts_code, nav_date, ann_date, unit_nav, accum_nav, adj_nav
                FROM fund_nav
                WHERE ($codes::VARCHAR[] IS NULL OR ts_code IN (SELECT unnest($codes::VARCHAR[])))
                  AND ts_code BETWEEN $first AND $last
                  AND ($end::DATE IS NULL OR nav_date <= $end::DATE)
                QUALIFY row_number() OVER (PARTITION BY ts_code, nav_date ORDER BY ann_date DESC) = 1
            ), previous AS (
                SELECT *, lag(nav_date) OVER w AS prev_date, lag(unit_nav) OVER w AS prev_unit_nav,
                    lag(accum_nav) OVER w AS prev_accum_nav, lag(adj_nav) OVER w AS prev_adj_nav
                FROM latest WINDOW w AS (PARTITION BY ts_code ORDER BY nav_date)
            ), moves AS (
                SELECT *, nav_date - prev_date AS gap_days,
                    CASE WHEN unit_nav > 0 AND prev_unit_nav > 0
                         THEN (unit_nav / prev_unit_nav - 1) * 100 END AS unit_move_pct,
                    CASE WHEN adj_nav > 0 AND prev_adj_nav > 0
                         THEN (adj_nav / prev_adj_nav - 1) * 100 END AS adj_move_pct
                FROM previous WHERE $start::DATE IS NULL OR nav_date >= $start::DATE
            ), flagged AS (
                SELECT *, adj_move_pct - unit_move_pct AS return_gap_pp,
                    CASE WHEN unit_nav IS NULL OR unit_nav <= 0 THEN 'invalid_unit_nav'
                         WHEN adj_nav IS NULL OR adj_nav <= 0 THEN 'invalid_adj_nav'
                         WHEN prev_date IS NOT NULL AND (prev_adj_nav IS NULL OR prev_adj_nav <= 0) THEN 'invalid_adj_nav'
                         WHEN abs(adj_move_pct - unit_move_pct) >= greatest(10, $gap_pp) THEN 'large_return_gap'
                         WHEN abs(adj_move_pct - unit_move_pct) >= $gap_pp THEN 'return_gap'
                         WHEN greatest(abs(unit_move_pct), abs(adj_move_pct)) >= $move_pct THEN 'large_move'
                    END AS reason
                FROM moves
            ), events AS (
                SELECT DISTINCT ts_code, coalesce(net_ex_date, ex_date) AS event_date, 'dividend' AS kind,
                       div_cash::DOUBLE AS amount
                FROM fund_dividend
                WHERE trim(div_proc) = '实施' AND div_cash > 0 AND ts_code BETWEEN $first AND $last
                UNION ALL
                SELECT ts_code, event_date::DATE, 'split', split_ratio::DOUBLE
                FROM verified_split_events WHERE ts_code BETWEEN $first AND $last
            ), with_events AS (
                SELECT f.*, e.has_local_dividend, e.kind, e.amount
                FROM (SELECT * FROM flagged WHERE reason IS NOT NULL) f
                LEFT JOIN LATERAL (
                    SELECT count(*) FILTER (WHERE kind = 'dividend') > 0 AS has_local_dividend,
                           CASE WHEN count(*) = 1 THEN min(kind) END AS kind,
                           CASE WHEN count(*) = 1 THEN min(amount) END AS amount
                    FROM events WHERE ts_code = f.ts_code AND event_date > f.prev_date AND event_date <= f.nav_date
                ) e ON TRUE
            ), checked AS (
                SELECT *, CASE
                    WHEN kind = 'split' THEN abs(adj_move_pct - ((unit_nav * amount / prev_unit_nav - 1) * 100))
                    WHEN kind = 'dividend' AND amount < prev_unit_nav
                         AND abs((accum_nav - unit_nav) - (prev_accum_nav - prev_unit_nav) - amount) <= 0.0002 THEN
                        least(abs(adj_move_pct - ((unit_nav / (prev_unit_nav - amount) - 1) * 100)),
                              abs(adj_move_pct - (((unit_nav + amount) / prev_unit_nav - 1) * 100)))
                    END AS event_gap_pp
                FROM with_events
            )
            SELECT ts_code, nav_date, ann_date, prev_date, gap_days, reason,
                CAST(unit_nav AS VARCHAR) AS unit_nav, CAST(prev_unit_nav AS VARCHAR) AS prev_unit_nav,
                CAST(accum_nav AS VARCHAR) AS accum_nav, CAST(prev_accum_nav AS VARCHAR) AS prev_accum_nav,
                CAST(adj_nav AS VARCHAR) AS adj_nav, CAST(prev_adj_nav AS VARCHAR) AS prev_adj_nav,
                round(unit_move_pct, 8) AS unit_move_pct, round(adj_move_pct, 8) AS adj_move_pct,
                round(return_gap_pp, 8) AS return_gap_pp, has_local_dividend
            FROM checked
            WHERE NOT coalesce(reason IN ('large_return_gap', 'return_gap', 'large_move')
                               AND abs(adj_move_pct) < $move_pct AND event_gap_pp <= 0.05, FALSE)
        """
        # 按代码分批限制窗口的内存开销, 同一只基金始终在同一批.
        scope = funds.loc[funds.has_nav, "ts_code"]
        batches = list(batched(sorted(scope), 2_000)) or [("",)]
        frame = pd.concat([
            db.execute(query, {**params, "first": batch[0], "last": batch[-1]}).fetchdf()
            for batch in batches
        ], ignore_index=True)
    frame["same_date_large_gaps"] = frame.reason.eq("large_return_gap").groupby(frame.nav_date).transform("sum")
    frame["abs_gap"] = frame.return_gap_pp.abs()
    frame = frame.sort_values(["same_date_large_gaps", "abs_gap", "ts_code", "nav_date"],
                              ascending=[False, False, True, True]).drop(columns="abs_gap")
    fields = ["ts_code", "name", "fund_type", "fund_status"]
    frame = frame.merge(funds[fields], on="ts_code", how="left", validate="many_to_one")
    if start is None and end is None:
        missing = funds.loc[~funds.has_nav, fields].assign(reason="no_nav_history")
        if not missing.empty:
            frame = pd.concat([frame, missing], ignore_index=True)
    return frame.reindex(columns=fields + [c for c in frame if c not in fields]), int(funds.has_nav.sum())
