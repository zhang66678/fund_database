"""读取数据库基金清单逐只更新, 不依赖固定代码或 top_10.csv."""

import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from database.connection import DATABASE_PATH, connect
from database.writer import calendar_last_date, update_targets, write_batch
from etl.import_snapshot import normalize
from api.fund_fields import BASIC
from etl.extract import paged
from api.fund_fields import NAV_FIELDS, DIV_FIELDS
from etl.extract import fetch_series


REFRESH_DAYS = 7


def update_funds(source, database_path: Path = DATABASE_PATH, *,
                 end: date | None = None, refresh_days: int = REFRESH_DAYS) -> dict:
    """默认从每只基金最后净值日前回看 7 天, 补上停更期间的数据.

    基础资料和分红记录量较少, 每次重新获取. 名称用于日志, 代码用于请求.
    本机 DuckDB 更新期间需停止网站, 不在网络请求期间开启数据库事务.
    """
    end = end or date.today()
    if refresh_days < 1:
        raise ValueError("refresh_days must be positive")
    if not database_path.is_file():
        raise FileNotFoundError("Database missing; run scripts.init_database first")
    summary = {"funds": [], "calendar": {}, "status": "completed"}
    with connect(database_path) as connection:
        targets = update_targets(connection)
        logging.info("Update started: funds=%s end=%s refresh_days=%s", len(targets), end, refresh_days)
        if not targets:
            logging.info("No funds in database, nothing to update")
            return summary
        # SSE 日历是共享数据, 每次任务只更新一次, 按年分段防止截断.
        try:
            last = calendar_last_date(connection)
            start = min(last, end) - timedelta(days=refresh_days - 1) if last else min(t[3] for t in targets if t[3])
            parts = []
            for year in range(start.year, end.year + 1):
                left, right = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
                part = source.query("trade_cal", "exchange,cal_date,is_open", exchange="SSE",
                                    start_date=left.strftime("%Y%m%d"), end_date=right.strftime("%Y%m%d"))
                if part.cal_date.duplicated().any() or set(part.cal_date) != set(pd.date_range(left, right).strftime("%Y%m%d")) or not part.exchange.eq("SSE").all():
                    raise ValueError("Incomplete or unexpected SSE calendar")
                parts.append(part)
            calendar, _ = normalize("trade_calendar", pd.concat(parts, ignore_index=True))
            summary["calendar"] = write_batch(connection, {"trade_calendar": calendar})["trade_calendar"]
            logging.info("trade_calendar: %s", summary["calendar"])
        except Exception as exc:
            summary["calendar"] = {"error": str(exc)}
            summary["status"] = "partial"
            logging.error("Calendar update failed: %s", exc)

        for code, name, market, found, last in targets:
            record = {"code": code, "name": name}
            try:
                basic = source.query("fund_basic", BASIC, ts_code=code, market=market or "O")
                if len(basic) != 1 or not basic.ts_code.eq(code).all():
                    raise ValueError("Expected exactly one matching fund basic record")
                found = pd.to_datetime(basic.iloc[0].found_date, format="%Y%m%d").date()
                start = max(found, min(last, end) - timedelta(days=refresh_days - 1)) if last else found
                if start > end:
                    record["status"] = "skipped"
                    logging.info("Skipped future fund: %s %s", code, name)
                    continue
                logging.info("Updating %s %s: %s to %s", code, name, start, end)
                nav = fetch_series(source, "fund_nav", NAV_FIELDS, code, start, end)
                div = paged(source, "fund_div", DIV_FIELDS, ts_code=code)
                if not div.ts_code.eq(code).all():
                    raise ValueError("Unexpected dividend fund code")
                frames = {}
                for table, raw in (("fund_basic", basic), ("fund_nav", nav), ("fund_dividend", div)):
                    frames[table], _ = normalize(table, raw)
                record["counts"] = write_batch(connection, frames)
                record["status"] = "empty_nav" if nav.empty else "ok"
                record["start"] = str(start)
                if nav.empty:
                    logging.warning("No NAV returned for %s %s; existing data preserved", code, name)
                logging.info("Updated %s %s: %s", code, name, record["counts"])
            except Exception as exc:
                # 每只基金是独立任务. Source 已脱敏网络错误, 这里记录失败并继续.
                record.update(status="failed", error=str(exc))
                summary["status"] = "partial"
                logging.error("Fund failed: %s %s: %s", code, name, exc)
            finally:
                summary["funds"].append(record)
    logging.info("Update finished: status=%s funds=%s failed=%s", summary["status"], len(summary["funds"]),
                 sum(r["status"] == "failed" for r in summary["funds"]))
    return summary
