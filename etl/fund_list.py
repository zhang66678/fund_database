"""获取场外基金总名单快照, 供批次筛选和后续数据更新使用."""

import json
import logging
import re
from datetime import date
from pathlib import Path

import pandas as pd

from api.fund_fields import BASIC
from api.tushare_client import FetchError, Source
from etl.extract import paged


LOGGER = logging.getLogger(__name__)
SNAPSHOT_PATTERN = re.compile(r"\d{8}_\d{6}")
STATUSES = ("L", "D", "I")


def _save_csv(path: Path, frame: pd.DataFrame) -> None:
    """保存不含行索引的 CSV, 使用 BOM 方便 Windows 软件识别中文."""
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _save_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_fund_list(source: Source, folder: Path, as_of: date) -> dict:
    """获取 L/D/I 三种状态的场外基金, 保存快照并返回摘要.

    folder 为本次输出目录, as_of 为采集日期. 函数不筛选样本, 不获取
    历史净值, 也不写入数据库. 只有全部状态获取和校验完成后才写入
    list_summary.json.
    """
    folder.mkdir(parents=True, exist_ok=True)
    frames, counts = [], {}
    for status in STATUSES:
        # fund_basic 文档未明确列出分页参数, 先用小页实测 offset 行为.
        first = source.query("fund_basic", BASIC, market="O", status=status, limit=2, offset=0)
        if len(first) > 2:
            raise FetchError("fund_basic: limit ignored; cannot establish complete list")
        if len(first) == 2:
            second = source.query("fund_basic", BASIC, market="O", status=status, limit=2, offset=2)
            combined = source.query("fund_basic", BASIC, market="O", status=status, limit=4, offset=0)
            if list(pd.concat([first, second])["ts_code"]) != list(combined["ts_code"]):
                raise FetchError("fund_basic: pagination probe failed")
            if set(first["ts_code"]) & set(second["ts_code"]):
                raise FetchError("fund_basic: offset ignored")

        frame = paged(source, "fund_basic", BASIC, market="O", status=status)
        # 先保存各状态原始响应, 后续校验失败时仍可排查源数据.
        _save_csv(folder / f"fund_basic_{status}.csv", frame)
        if not frame["market"].eq("O").all() or not frame["status"].eq(status).all():
            raise FetchError("fund_basic: market/status filter mismatch")
        counts[status] = len(frame)
        frames.append(frame)

    all_funds = pd.concat(frames, ignore_index=True)
    if all_funds.empty or all_funds["ts_code"].isna().any() or all_funds["ts_code"].duplicated().any():
        raise FetchError("fund_basic: empty list or invalid/duplicate codes")
    _save_csv(folder / "fund_basic_all.csv", all_funds)

    date_quality = {}
    for status, group in all_funds.groupby("status"):
        date_quality[status] = {}
        for column in ("found_date", "due_date", "delist_date"):
            parsed = pd.to_datetime(group[column], format="%Y%m%d", errors="coerce")
            date_quality[status][column] = {
                "nonempty": int(group[column].notna().sum()),
                "valid": int(parsed.notna().sum()),
            }

    summary = {
        "as_of": as_of.isoformat(),
        "counts_by_status": counts,
        "total": len(all_funds),
        "coverage": "All pages returned by Tushare for L/D/I; not independently reconciled to a regulator list",
        "invalid_found_dates": int(
            pd.to_datetime(all_funds.found_date, format="%Y%m%d", errors="coerce").isna().sum()
        ),
        "date_quality_by_status": date_quality,
        "output": str(folder),
    }
    _save_json(folder / "list_summary.json", summary)
    LOGGER.info("Fund list complete: %s; output=%s", counts, folder)
    return summary


def latest_snapshot(base: Path) -> Path:
    """按目录名时间戳选择最新完整快照, 跳过尚未生成摘要的任务."""
    if not base.is_dir():
        raise ValueError("没有找到基金名单快照, 请先运行 scripts.update_fund_list")
    candidates = [
        path for path in base.iterdir()
        if path.is_dir() and SNAPSHOT_PATTERN.fullmatch(path.name)
        and (path / "fund_basic_all.csv").is_file()
        and (path / "list_summary.json").is_file()
    ]
    if not candidates:
        raise ValueError("没有找到完整清单快照, 请先运行 scripts.update_fund_list")
    return max(candidates, key=lambda path: path.name)
