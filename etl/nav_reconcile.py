"""对筛查命中的净值区间补充 AKShare 对照数据."""

from datetime import date
from math import isclose, isfinite
from threading import Lock
from time import monotonic, sleep

import pandas as pd

from api.akshare_nav import fetch_nav
from etl.update_scope import fetch_updates


NAV_TOLERANCE = 0.0001
RETURN_TOLERANCE_PP = 0.05


def _check_interval(row, unit, accum, cash, splits, *, confirmed_cash=None):
    if row["nav_date"] == "":
        return {"review_status": "no_local_nav", "akshare_latest_date": unit.index.max() if len(unit) else None}
    end = date.fromisoformat(row["nav_date"])
    prev = date.fromisoformat(row["prev_date"]) if row["prev_date"] else None
    result = {"akshare_unit_nav": unit.get(end), "akshare_accum_nav": accum.get(end),
              "akshare_prev_unit_nav": unit.get(prev), "akshare_prev_accum_nav": accum.get(prev)}
    if not prev or prev not in unit or end not in unit:
        return {**result, "review_status": "missing_akshare_endpoints" if prev else "no_previous_nav"}
    if prev >= end:
        return {**result, "review_status": "invalid_interval"}
    events = {day: cash[day] for day in sorted(cash) if prev < day <= end}
    split_dates = sorted(day for day in splits if prev < day <= end)
    result.update(akshare_unit_move_pct=(unit[end] / unit[prev] - 1) * 100,
                  akshare_cash_events="; ".join(f"{day}={amount:g}" for day, amount in events.items()),
                  akshare_split_dates="; ".join(map(str, split_dates)))
    for field in ("unit_nav", "accum_nav"):
        for prefix in ("", "prev_"):
            val, other = row.get(prefix + field), result[f"akshare_{prefix}{field}"]
            if val and other is not None and not isclose(float(val), other, rel_tol=0, abs_tol=NAV_TOLERANCE):
                return {**result, "review_status": f"{field}_discrepancy"}
    if split_dates:
        return {**result, "review_status": "split_needs_review"}
    if prev in accum and end in accum:
        reported_cash = (accum[end] - unit[end]) - (accum[prev] - unit[prev])
        confirmed = bool(events) and events == confirmed_cash
        if not confirmed and not isclose(sum(events.values()), reported_cash, rel_tol=0, abs_tol=2 * NAV_TOLERANCE):
            return {**result, "review_status": "dividend_data_conflict"}
        if confirmed:
            result["cash_amount_confirmed"] = True
    ref_factor = reinvest_factor = unit[end] / unit[prev]
    for day, amount in events.items():
        if day not in unit:
            return {**result, "review_status": "missing_ex_date_nav"}
        before = unit.iloc[unit.index.get_loc(day) - 1]
        if before <= amount:
            return {**result, "review_status": "invalid_dividend"}
        ref_factor *= before / (before - amount)
        reinvest_factor *= 1 + amount / unit[day]
    ref_pct = (ref_factor - 1) * 100
    reinvest_pct = (reinvest_factor - 1) * 100
    result.update(reference_move_pct=ref_pct, reinvestment_move_pct=reinvest_pct)
    if not row["adj_move_pct"]:
        return {**result, "review_status": "missing_tushare_adj_return"}
    adj_pct = float(row["adj_move_pct"])
    result["adj_minus_reference_pp"] = adj_pct - ref_pct
    if min(abs(adj_pct - ref_pct), abs(adj_pct - reinvest_pct)) <= RETURN_TOLERANCE_PP:
        status = "consistent_with_akshare"
    else:
        status = "adj_return_discrepancy"
    return {**result, "review_status": status}


def review_nav(candidates, client, *, workers=1, request_interval=0.0, progress=None):
    """每只基金只取一组 AKShare 数据, 逐条解释同一份快照的命中区间."""
    if workers < 1 or not isfinite(request_interval) or request_interval < 0:
        raise ValueError("并发数必须为正, 请求间隔必须是非负有限数值")
    request_lock = Lock()
    last_request = 0.0

    def wait_for_request():
        nonlocal last_request
        with request_lock:
            sleep(max(0, last_request + request_interval - monotonic()))
            last_request = monotonic()

    def review_fund(item):
        code, group = item
        try:
            if group.fund_type.str.contains("货币").any():
                details = [{"review_status": "money_fund_needs_review"}] * len(group)
            else:
                data = fetch_nav(client, code, before_request=wait_for_request if request_interval else None)
                details = [_check_interval(row, *data) for row in group.to_dict("records")]
        except Exception as exc:
            details = [{"review_status": "akshare_failed", "review_error": str(exc)[:250]}] * len(group)
        return [{"index": idx, **detail} for idx, detail in zip(group.index, details, strict=True)]

    groups = candidates.fillna("").groupby("ts_code", sort=False)
    results = []
    fetched = fetch_updates(review_fund, groups, workers, thread_name_prefix="akshare-review")
    for completed, details in enumerate(fetched, start=1):
        results.extend(details)
        if progress is not None:
            progress(completed, groups.ngroups)
    return candidates.join(pd.DataFrame(results).set_index("index"), validate="one_to_one")


def rebuild_nav(client, code, start, end):
    """按除息前净值分段连乘, 仅处理可核验现金分红的历史."""
    unit, accum, cash, splits = fetch_nav(client, code)
    unit = unit.loc[[start <= day <= end for day in unit.index]]
    if unit.empty or unit.index[0] != start:
        raise ValueError("AKShare 净值未覆盖成立日, 无法确定完整序列的基准")
    if any(start <= day <= end for day in splits):
        raise ValueError("发现拆分或折算, 需要核对份额变化后再重建")
    if any(start <= day <= end and day not in unit for day in cash):
        raise ValueError("除息日净值缺失, 无法重建")
    factor, cumulative_cash, previous = 1.0, 0.0, None
    rows, checked_dates = [], 0
    for day, nav in unit.items():
        amount = cash.get(day, 0)
        if amount:
            if previous is None or previous <= amount:
                raise ValueError(f"{day} 缺少有效除息前净值")
            factor *= previous / (previous - amount)
            cumulative_cash += amount
        if day in accum:
            checked_dates += 1
            if abs(accum[day] - nav - cumulative_cash) > 2 * NAV_TOLERANCE:
                raise ValueError(f"{day} 的累计净值、单位净值和累计分红不一致")
        rows.append((code, day, nav, nav * factor, "akshare_ex_date_v1"))
        previous = nav
    if not checked_dates or unit.index[-1] not in accum:
        raise ValueError("缺少末日累计净值, 无法核对分红总额")
    return pd.DataFrame(rows, columns=["ts_code", "nav_date", "unit_nav", "reference_adj_nav", "source"])


if __name__ == "__main__":
    # 独立环境只依赖 AKShare, 向调用进程返回 CSV.
    import sys
    import akshare

    akshare.set_timeout((10, 30))
    try:
        print(rebuild_nav(akshare, sys.argv[1], *map(date.fromisoformat, sys.argv[2:])).to_csv(index=False), end="")
    except Exception as exc:
        raise SystemExit(str(exc)) from None
