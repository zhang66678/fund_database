"""读取数据库基金清单逐只更新, 不依赖固定代码"""

import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from functools import partial
from pathlib import Path

import pandas as pd

from database.connection import DATABASE_PATH, connect
from database.schema import initialize
from database.writer import calendar_last_date, update_targets, write_batch
from etl.import_snapshot import normalize
from etl.update_fund_data import fetch_fund_update, save_fund_update


REFRESH_DAYS = 7
DATASETS = ("basic", "nav", "dividend", "manager", "asset", "calendar")


def update_funds(source, database_path: Path = DATABASE_PATH, *,
                 end: date | None = None, refresh_days: int = REFRESH_DAYS,
                 full_history: bool = False, datasets: Sequence[str] | None = None,
                 codes: Sequence[str] | None = None, workers: int = 1,
                 nav_state: str = "all") -> dict:
    """默认从每只基金最后净值日前回看 7 天, 补上停更期间的数据.

    基础资料, 分红和经理每次重新获取; 净值和资产按各自最新日期补更.
    基础资料与净值共同提交, 分红, 经理和资产各自提交并记录失败.
    净值和资产缺少公告日期时隔离原始行, 合格记录继续入库, 本轮标记 partial.
    datasets 不传时更新全部, 指定时只请求和写入选中的数据.
    codes 不传时更新全库, 指定时仅处理库内匹配的基金, 未入库代码报错.
    full_history 对选中的净值, 资产和日历复查全部历史.
    workers 控制并发取数数量, 有界预取, 数据库仍在主线程串行写入.
    nav_state 可选 all/existing/missing, 按任务开始时是否有净值筛选基金.
    本机 DuckDB 更新期间需停止网站, 不在网络请求期间开启数据库事务.
    """
    end = end or date.today()
    if not 1 <= workers <= 16:
        raise ValueError("workers must be between 1 and 16")
    if nav_state not in ("all", "existing", "missing"):
        raise ValueError("nav_state must be all, existing or missing")
    if refresh_days < 1:
        raise ValueError("refresh_days must be positive")
    selected = set(DATASETS if datasets is None else datasets)
    if not selected or selected - set(DATASETS):
        raise ValueError(f"datasets must contain one or more of: {', '.join(DATASETS)}")
    requested_codes = None
    if codes is not None:
        normalized = {code.strip().upper() for code in codes}
        requested_codes = {code + ".OF" if len(code) == 6 and code.isdigit() else code for code in normalized}
        if not requested_codes or "" in requested_codes:
            raise ValueError("指定的基金代码不能为空")
    if not database_path.is_file():
        raise FileNotFoundError("Database missing; run scripts.init_database first")
    summary = {"funds": [], "calendar": {}, "status": "completed",
               "datasets": [item for item in DATASETS if item in selected],
               "workers": workers, "nav_state": nav_state}
    if requested_codes is not None:
        summary["requested_codes"] = sorted(requested_codes)
    rejected_dir = database_path.parent / "rejected_rows" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    with connect(database_path) as connection:
        # 为旧数据库补建新增的表, 不清空已有数据.
        connection.execute("BEGIN TRANSACTION")
        initialize(connection)
        connection.execute("COMMIT")
        targets = update_targets(connection)
        if requested_codes is not None:
            missing = requested_codes - {target[0] for target in targets}
            if missing:
                raise ValueError(f"以下基金不在数据库中, 请先添加: {', '.join(sorted(missing))}")
            # 只筛选本轮任务, 不改变原有基金清单和各表的数据更新规则.
            targets = [target for target in targets if target[0] in requested_codes]
        if nav_state != "all":
            targets = [target for target in targets if (target[4] is not None) == (nav_state == "existing")]
        summary["target_count"] = len(targets)
        logging.info("Update started: funds=%s datasets=%s end=%s refresh_days=%s full_history=%s workers=%s nav_state=%s",
                     len(targets), summary["datasets"], end, refresh_days, full_history, workers, nav_state)
        if not targets:
            logging.info("No funds in database, nothing to update")
            return summary
        # SSE 日历是共享数据, 每次任务只更新一次, 按年分段防止截断.
        if "calendar" in selected:
            try:
                last = calendar_last_date(connection)
                start = min(last, end) - timedelta(days=refresh_days - 1) if last and not full_history else min(t[3] for t in targets if t[3])
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

        if selected == {"calendar"}:
            return summary
        # 一次聚合资产末日, 避免每只基金重复查询大表; 工作线程不持有连接.
        asset_dates = dict(connection.execute(
            "SELECT ts_code, max(nav_date) FROM fund_asset GROUP BY ts_code"
        ).fetchall()) if "asset" in selected else {}
        fetch = partial(fetch_fund_update, source, end=end, refresh_days=refresh_days,
                        full_history=full_history, datasets=selected, rejected_dir=rejected_dir)

        def prepare(target):
            return fetch(target, asset_dates.get(target[0]))

        def save_results(results):
            for index, prepared in enumerate(results, start=1):
                record = save_fund_update(connection, prepared)
                summary["funds"].append(record)
                if record["status"] in ("failed", "partial"):
                    summary["status"] = "partial"
                logging.info("更新进度 %s/%s code=%s", index, len(targets), record["code"])

        if workers == 1:
            save_results(map(prepare, targets))
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fund-fetch") as pool:
                # 限制在途历史数据数量, 防止两万只基金的 DataFrame 同时堆在内存中.
                save_results(pool.map(prepare, targets, buffersize=workers * 2))
    logging.info("Update finished: status=%s funds=%s failed=%s", summary["status"], len(summary["funds"]),
                 sum(r["status"] in ("failed", "partial") for r in summary["funds"]))
    return summary
