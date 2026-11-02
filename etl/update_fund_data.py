"""单只基金取数与写入分开, 网络任务可并发, 数据库仍由主线程提交."""

import logging
from datetime import timedelta
from pathlib import Path

import pandas as pd

from api.fund_fields import ASSET_FIELDS, BASIC, DIV_FIELDS, MANAGER_FIELDS, NAV_FIELDS
from database.writer import write_batch
from etl.extract import fetch_series, paged
from etl.import_snapshot import normalize


def _normalize_series(table: str, raw: pd.DataFrame, code: str,
                      rejected_dir: Path) -> tuple[pd.DataFrame, int, dict]:
    """隔离缺少公告日期的原始行, 其余记录仍需通过严格校验."""
    missing = raw.ann_date.astype("string").str.strip().fillna("").eq("")
    quality = {}
    if missing.any():
        rejected_dir.mkdir(parents=True, exist_ok=True)
        path = rejected_dir / f"{table}_{code}.csv"
        raw.loc[missing].to_csv(path, index=False, encoding="utf-8-sig")
        quality = {"rejected_rows": int(missing.sum()), "reason": "missing ann_date",
                   "rejected_file": str(path)}
        logging.warning("%s %s: %s", table, code, quality)
    frame, duplicates = normalize(table, raw.loc[~missing])
    return frame, duplicates, quality


def _series_start(found, last, end, refresh_days, full_history):
    if pd.isna(found):
        raise ValueError("fund inception date is missing")
    return max(found, min(last, end) - timedelta(days=refresh_days - 1)) if last and not full_history else found


def _fetch_details(source, code, found, last_asset, end, refresh_days,
                   full_history, datasets, rejected_dir):
    results, frames = {}, {}
    for dataset, table in (("dividend", "fund_dividend"), ("manager", "fund_manager"), ("asset", "fund_asset")):
        if dataset not in datasets:
            continue
        try:
            if table == "fund_dividend":
                raw = paged(source, "fund_div", DIV_FIELDS, ts_code=code)
            elif table == "fund_manager":
                raw = paged(source, "fund_manager", MANAGER_FIELDS, ts_code=code)
            else:
                start = _series_start(found, last_asset, end, refresh_days, full_history)
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
                useful = frame[["net_asset", "total_netasset"]].notna().any(axis=1)
                ignored = int((~useful).sum())
                frame = frame.loc[useful]
                if (frame[["net_asset", "total_netasset"]] < 0).any().any():
                    raise ValueError("fund_asset: negative asset value")
            frames[table] = frame
            results[table] = {"status": "partial" if quality else "empty" if frame.empty else "ok",
                              "source_duplicates": duplicates, "ignored_empty_rows": ignored}
            if quality:
                results[table]["quality"] = quality
            if table == "fund_asset":
                results[table].update(start=str(start), end=str(end))
        except Exception as exc:
            results[table] = {"status": "failed", "error": str(exc)}
    return results, frames


def fetch_fund_update(source, target, last_asset, *, end, refresh_days,
                      full_history, datasets, rejected_dir):
    """只请求和清洗数据, 不接触数据库连接. 单项失败仍尝试其他资料."""
    code, name, market, found, last = target
    record = {"code": code, "name": name, "status": "ok"}
    frames = {}
    try:
        if "basic" in datasets:
            basic = source.query("fund_basic", BASIC, ts_code=code, market=market or "O")
            if len(basic) != 1 or not basic.ts_code.eq(code).all():
                raise ValueError("Expected exactly one matching fund basic record")
            frames["fund_basic"], _ = normalize("fund_basic", basic)
            found = frames["fund_basic"].found_date.iloc[0]
        if "nav" in datasets:
            start = _series_start(found, last, end, refresh_days, full_history)
            if start <= end:
                logging.info("Updating %s %s: %s to %s", code, name, start, end)
                nav = fetch_series(source, "fund_nav", NAV_FIELDS, code, start, end)
                frames["fund_nav"], _, quality = _normalize_series("fund_nav", nav, code, rejected_dir)
                record["status"] = "partial" if quality else "empty_nav" if nav.empty else "ok"
                if quality:
                    record["quality"] = {"fund_nav": quality}
                record["start"] = str(start)
                if nav.empty:
                    logging.warning("No NAV returned for %s %s; existing data preserved", code, name)
            else:
                record["status"] = "skipped"
    except Exception as exc:
        # 基础资料与净值共同提交, 任一失败时都不提交这两项.
        frames = {}
        record.update(status="failed", error=str(exc))
    details, detail_frames = _fetch_details(source, code, found, last_asset, end, refresh_days,
                                          full_history, datasets, rejected_dir)
    if details:
        record["details"] = details
    return record, frames, detail_frames


def save_fund_update(connection, prepared):
    """仅由主线程调用, 基础/净值共同提交, 其余表逐项提交并隔离失败."""
    record, frames, detail_frames = prepared
    if frames:
        try:
            record["counts"] = write_batch(connection, frames)
            logging.info("Updated %s %s: %s", record["code"], record["name"], record["counts"])
        except Exception as exc:
            record.update(status="failed", error=str(exc))
    if record["status"] == "failed":
        logging.error("Fund failed: %s %s: %s", record["code"], record["name"], record["error"])
    for table, result in record.get("details", {}).items():
        if table in detail_frames:
            try:
                result["counts"] = write_batch(connection, {table: detail_frames[table]})[table]
                record.setdefault("counts", {})[table] = result["counts"]
            except Exception as exc:
                result.update(status="failed", error=str(exc))
        if result["status"] in ("failed", "partial") and record["status"] != "failed":
            record["status"] = "partial"
        logging.info("%s %s: %s", table, record["code"], result)
    return record
