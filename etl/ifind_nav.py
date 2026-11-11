"""采集 iFinD 净值到独立数据库, 名单只读复用本地 Tushare 档案."""

import logging
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from time import perf_counter

import duckdb
import pandas as pd

from api.ifind_nav import NAV_COLUMNS, fetch_nav
from database.connection import DATABASE_PATH, IFIND_DATABASE_PATH, connect
from database.ifind_nav_store import check_paths, nav_writer
from etl.add_funds import _normalize_code
from etl.update_scope import NAV_STATES, REFRESH_DAYS, fetch_updates


def _prepare_nav(client, code, begin, end):
    item = {"code": code, "start": begin.isoformat(), "end": end.isoformat()}
    tick = perf_counter()
    try:
        nav = fetch_nav(client, code, begin, end)
        item["fetch_seconds"] = round(perf_counter() - tick, 3)
        # Blank 返回的三列全空行只是占位, 不作为一条净值写入.
        frame = nav.loc[nav[NAV_COLUMNS[1:]].notna().any(axis=1)].copy()
        item["blank_rows"] = len(nav) - len(frame)
        if frame.empty:
            item["status"] = "empty"
            return item, None
        stamp = pd.Timestamp(nav.attrs["fetched_at"])
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError("iFinD 净值获取时间无效")
        frame["ts_code"] = code
        frame["fetched_at"] = stamp.tz_convert("UTC").tz_localize(None)
        item.update(status="ok", rows=len(frame), first_date=frame.nav_date.min().date().isoformat(),
                    last_date=frame.nav_date.max().date().isoformat())
        return item, frame
    except (ValueError, OSError) as exc:
        item.update(status="failed", error=str(exc), fetch_seconds=round(perf_counter() - tick, 3))
        return item, None


def update_ifind_nav(client, database_path: Path = IFIND_DATABASE_PATH, *,
                     reference_path: Path = DATABASE_PATH, codes: list[str] | None = None,
                     start: date | None = None, end: date | None = None,
                     full_history: bool = False, nav_state: str = "all",
                     refresh_days: int = REFRESH_DAYS, workers: int = 1) -> dict:
    """默认更新公共名单全库, 按 iFinD 净值筛选范围, 单基金整体提交."""
    check_paths(database_path, reference_path)
    if not 1 <= workers <= 16:
        raise ValueError("workers 必须为 1 至 16")
    if nav_state not in NAV_STATES or refresh_days < 1:
        raise ValueError("nav_state 必须为 all/existing/missing, refresh_days 必须为正整数")
    end = end or date.today()
    if start is not None and start > end:
        raise ValueError("开始日期不能晚于结束日期")
    targets = None if codes is None else list(dict.fromkeys(_normalize_code(code) for code in codes))
    found = {}
    if targets is None or start is None:
        if not reference_path.is_file():
            raise ValueError("缺少本地名单, 请指定基金代码和 --start-date")
        with connect(reference_path, read_only=True) as db:
            found = dict(db.execute("SELECT ts_code,found_date FROM fund_basic ORDER BY ts_code").fetchall())
    targets = list(found) if targets is None else targets
    if not targets:
        raise ValueError("没有待采集的基金")
    latest = {}
    if database_path.is_file():
        with connect(database_path, read_only=True) as db:
            if db.execute("SELECT 1 FROM information_schema.tables WHERE table_name='fund_nav'").fetchone():
                latest = dict(db.execute("SELECT ts_code,max(nav_date) FROM fund_nav GROUP BY ts_code").fetchall())
    if nav_state != "all":
        targets = [code for code in targets if (code in latest) == (nav_state == "existing")]
    if start is None and any(found.get(code) is None for code in targets):
        raise ValueError("部分基金缺少本地成立日期, 请指定 --start-date")
    results, started = [], perf_counter()

    def prepare(code):
        begin = start or found[code]
        if code in latest and not full_history:
            begin = max(begin, min(latest[code], end) - timedelta(days=refresh_days - 1))
        return _prepare_nav(client, code, begin, end)

    prepared = fetch_updates(prepare, targets, workers, thread_name_prefix="ifind-nav")
    with nav_writer(database_path, reference_path) as save, closing(prepared):
        for index, (item, frame) in enumerate(prepared, 1):
            if frame is not None:
                tick = perf_counter()
                try:
                    item["counts"] = save(frame)
                except (ValueError, OSError, duckdb.Error) as exc:
                    item.update(status="failed", error=str(exc))
                item["write_seconds"] = round(perf_counter() - tick, 3)
            results.append(item)
            logging.info("iFinD 更新进度 %s/%s: %s %s rows=%s fetch=%.3fs write=%.3fs", index, len(targets),
                         item["code"], item["status"], item.get("rows", 0),
                         item.get("fetch_seconds", 0), item.get("write_seconds", 0))
    return {"status": "completed" if all(item["status"] == "ok" for item in results) else "partial",
            "source": "ifind", "database": str(database_path), "funds": len(targets),
            "nav_state": nav_state, "refresh_days": refresh_days, "full_history": full_history, "workers": workers,
            "elapsed_seconds": round(perf_counter() - started, 3), "results": results}
