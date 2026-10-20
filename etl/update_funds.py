"""读取数据库基金清单逐只更新, 不依赖固定代码"""

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from database.connection import DATABASE_PATH, connect
from api.fund_fields import ASSET_FIELDS, BASIC, DIV_FIELDS, MANAGER_FIELDS, NAV_FIELDS
from database.schema import initialize
from database.writer import asset_last_date, calendar_last_date, update_targets, write_batch
from etl.import_snapshot import normalize
from etl.extract import fetch_series, paged


REFRESH_DAYS = 7
DATASETS = ("basic", "nav", "dividend", "manager", "asset", "calendar")


def _normalize_series(table: str, raw: pd.DataFrame, code: str,
                      rejected_dir: Path) -> tuple[pd.DataFrame, int, dict]:
    """隔离缺少公告日期的净值和资产原始行, 其余记录仍需通过严格校验."""
    missing = raw.ann_date.astype("string").str.strip().fillna("").eq("")
    quality = {}
    if missing.any():
        # 保留异常原始记录供补查, 不用净值日或其他日期替代公告日期.
        rejected_dir.mkdir(parents=True, exist_ok=True)
        path = rejected_dir / f"{table}_{code}.csv"
        raw.loc[missing].to_csv(path, index=False, encoding="utf-8-sig")
        quality = {"rejected_rows": int(missing.sum()), "reason": "missing ann_date",
                   "rejected_file": str(path)}
        logging.warning("%s %s: %s", table, code, quality)
    frame, duplicates = normalize(table, raw.loc[~missing])
    return frame, duplicates, quality


def _update_details(source, connection, code: str, found: date, end: date,
                    refresh_days: int, full_history: bool, datasets: set[str],
                    rejected_dir: Path) -> dict:
    """分红, 经理和资产分别获取并提交, 单项失败不影响其他数据."""
    results = {}
    for dataset, table in (("dividend", "fund_dividend"), ("manager", "fund_manager"), ("asset", "fund_asset")):
        if dataset not in datasets:
            continue
        try:
            if table == "fund_dividend":
                raw = paged(source, "fund_div", DIV_FIELDS, ts_code=code)
            elif table == "fund_manager":
                raw = paged(source, "fund_manager", MANAGER_FIELDS, ts_code=code)
            else:
                if pd.isna(found):
                    raise ValueError("fund_asset: fund inception date is missing")
                last = asset_last_date(connection, code)
                start = max(found, min(last, end) - timedelta(days=refresh_days - 1)) if last and not full_history else found
                if start > end:
                    results[table] = {"status": "skipped", "reason": "Fund starts after end date"}
                    continue
                raw = fetch_series(source, "fund_nav", ASSET_FIELDS, code, start, end)
            if not raw.ts_code.eq(code).all():
                raise ValueError(f"{table}: unexpected fund code")
            quality = {}
            if table == "fund_asset":
                frame, duplicates, quality = _normalize_series(table, raw, code, rejected_dir)
            else:
                frame, duplicates = normalize(table, raw)
            ignored = 0
            if table == "fund_asset":
                # 资产独立确定更新窗口, 两项都未披露的行不入库也不填充.
                useful = frame[["net_asset", "total_netasset"]].notna().any(axis=1)
                ignored = int((~useful).sum())
                frame = frame.loc[useful]
                if (frame[["net_asset", "total_netasset"]] < 0).any().any():
                    raise ValueError("fund_asset: negative asset value")
            counts = write_batch(connection, {table: frame})[table]
            results[table] = {"status": "partial" if quality else "empty" if frame.empty else "ok", "counts": counts,
                              "source_duplicates": duplicates, "ignored_empty_rows": ignored}
            if quality:
                results[table]["quality"] = quality
            if table == "fund_asset":
                results[table].update(start=str(start), end=str(end))
            logging.info("%s %s: %s", table, code, results[table])
        except Exception as exc:
            # 当前资料失败仍继续另一类资料, 原有成功写入不会撤销.
            results[table] = {"status": "failed", "error": str(exc)}
            logging.error("Fund data failed: %s %s: %s", table, code, exc)
    return results


def update_funds(source, database_path: Path = DATABASE_PATH, *,
                 end: date | None = None, refresh_days: int = REFRESH_DAYS,
                 full_history: bool = False, datasets: Sequence[str] | None = None,
                 codes: Sequence[str] | None = None) -> dict:
    """默认从每只基金最后净值日前回看 7 天, 补上停更期间的数据.

    基础资料, 分红和经理每次重新获取; 净值和资产按各自最新日期补更.
    基础资料与净值共同提交, 分红, 经理和资产各自提交并记录失败.
    净值和资产缺少公告日期时隔离原始行, 合格记录继续入库, 本轮标记 partial.
    datasets 不传时更新全部, 指定时只请求和写入选中的数据.
    codes 不传时更新全库, 指定时仅处理库内匹配的基金, 未入库代码报错.
    full_history 对选中的净值, 资产和日历复查全部历史.
    本机 DuckDB 更新期间需停止网站, 不在网络请求期间开启数据库事务.
    """
    end = end or date.today()
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
               "datasets": [item for item in DATASETS if item in selected]}
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
        logging.info("Update started: funds=%s datasets=%s end=%s refresh_days=%s full_history=%s",
                     len(targets), summary["datasets"], end, refresh_days, full_history)
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

        for index, (code, name, market, found, last) in enumerate(targets, start=1):
            if selected == {"calendar"}:
                break
            # 每开始处理一只基金输出进度, 总数来自本轮数据库基金清单.
            logging.info("更新进度 %s/%s", index, len(targets))
            record = {"code": code, "name": name, "status": "ok"}
            summary["funds"].append(record)
            try:
                frames = {}
                if "basic" in selected:
                    basic = source.query("fund_basic", BASIC, ts_code=code, market=market or "O")
                    if len(basic) != 1 or not basic.ts_code.eq(code).all():
                        raise ValueError("Expected exactly one matching fund basic record")
                    frames["fund_basic"], _ = normalize("fund_basic", basic)
                    found = frames["fund_basic"].found_date.iloc[0]
                if "nav" in selected:
                    if pd.isna(found):
                        raise ValueError("fund_nav: fund inception date is missing")
                    start = max(found, min(last, end) - timedelta(days=refresh_days - 1)) if last and not full_history else found
                    if start <= end:
                        logging.info("Updating %s %s: %s to %s", code, name, start, end)
                        nav = fetch_series(source, "fund_nav", NAV_FIELDS, code, start, end)
                        frames["fund_nav"], _, quality = _normalize_series("fund_nav", nav, code, rejected_dir)
                        record["status"] = "partial" if quality else "empty_nav" if nav.empty else "ok"
                        if quality:
                            record["quality"] = {"fund_nav": quality}
                            summary["status"] = "partial"
                        record["start"] = str(start)
                        if nav.empty:
                            logging.warning("No NAV returned for %s %s; existing data preserved", code, name)
                    else:
                        record["status"] = "skipped"
                        logging.info("Skipped future NAV: %s %s", code, name)
                if frames:
                    # 先保存基础资料和净值, 后续分红异常不会阻止入库.
                    record["counts"] = write_batch(connection, frames)
                    logging.info("Updated %s %s: %s", code, name, record["counts"])
            except Exception as exc:
                # 每只基金是独立任务. Source 已脱敏网络错误, 这里记录失败并继续.
                record.update(status="failed", error=str(exc))
                summary["status"] = "partial"
                logging.error("Fund failed: %s %s: %s", code, name, exc)
            if selected & {"dividend", "manager", "asset"}:
                record["details"] = _update_details(source, connection, code, found, end, refresh_days,
                                                     full_history, selected, rejected_dir)
                for table, result in record["details"].items():
                    if result["status"] in ("failed", "partial"):
                        summary["status"] = "partial"
                        if record["status"] != "failed":
                            record["status"] = "partial"
                    if "counts" in result:
                        record.setdefault("counts", {})[table] = result["counts"]
    logging.info("Update finished: status=%s funds=%s failed=%s", summary["status"], len(summary["funds"]),
                 sum(r["status"] in ("failed", "partial") for r in summary["funds"]))
    return summary
