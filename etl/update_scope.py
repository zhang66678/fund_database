"""两源更新共用的范围, 默认值和并发取数, 空批次不能回退为全库."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pandas as pd


REFRESH_DAYS = 7
NAV_STATES = ("all", "existing", "missing")


def fetch_updates(prepare, targets, workers: int, *, thread_name_prefix="fund-fetch"):
    """只并发取数, 调用方迭代并写入; 限制在途数据避免全库结果堆积."""
    if workers == 1:
        yield from map(prepare, targets)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=thread_name_prefix) as pool:
            yield from pool.map(prepare, targets, buffersize=workers * 2)


def read_update_codes(codes=None, csv_path: Path | None = None) -> list[str] | None:
    if codes is not None and csv_path is not None:
        raise ValueError("--codes 与 --file 不能同时使用")
    if csv_path is not None:
        batch = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        if "ts_code" not in batch.columns:
            raise ValueError("批次 CSV 必须包含 ts_code 列")
        codes = batch.ts_code.tolist()
    if codes is None:
        return None
    if not codes or any(not code.strip() for code in codes):
        raise ValueError("更新范围不能为空, 请检查基金代码或批次 CSV")
    return list(codes)
