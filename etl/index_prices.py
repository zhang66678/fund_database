"""采集宽基指数收盘点位, 单指数提交, 后续按自身最新日期增量更新."""

import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import duckdb

from api.tushare_client import FetchError
from database.connection import DATABASE_PATH, connect
from database.repository import INDEX_NAMES
from database.schema import initialize
from database.writer import write_batch
from etl.import_snapshot import normalize


def update_index_prices(source, database_path: Path = DATABASE_PATH, *,
                        codes: list[str] | None = None, start: date = date(1990, 1, 1),
                        end: date | None = None, full_history: bool = False) -> dict:
    """每五年一段请求, 网络期间不持有数据库连接, 失败指数不写入半段历史."""
    end = end or date.today()
    targets = list(INDEX_NAMES) if codes is None else list(dict.fromkeys(codes))
    if not targets or set(targets) - INDEX_NAMES.keys():
        raise ValueError("请选择支持的宽基指数代码")
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    latest = {}
    if database_path.is_file() and not full_history:
        with connect(database_path, read_only=True) as db:
            if db.execute("SELECT 1 FROM information_schema.tables WHERE table_name='index_daily'").fetchone():
                latest = dict(db.execute("SELECT ts_code, max(trade_date) FROM index_daily GROUP BY ts_code").fetchall())
    results = []
    for code in targets:
        begin = max(start, min(latest[code], end) - timedelta(days=6)) if code in latest else start
        item = {"code": code, "name": INDEX_NAMES[code], "start": begin.isoformat(), "end": end.isoformat()}
        try:
            parts = []
            while begin <= end:
                stop = min(end, (pd.Timestamp(begin) + pd.DateOffset(years=5)).date() - timedelta(days=1))
                raw = source.query("index_daily", "ts_code,trade_date,close", ts_code=code,
                                   start_date=begin.strftime("%Y%m%d"), end_date=stop.strftime("%Y%m%d"))
                frame, _ = normalize("index_daily", raw)
                # 指数历史尚未开始或窗口内无交易时, 跳过空段并继续请求后续年份.
                if not frame.empty:
                    if not frame.ts_code.eq(code).all() or not frame.trade_date.between(begin, stop).all():
                        raise ValueError("指数响应包含请求范围外的代码或日期")
                    if frame.close.isna().any() or not frame.close.map(lambda x: x.is_finite() and x > 0).all():
                        raise ValueError("指数收盘点位缺失或无效")
                    parts.append(frame)
                begin = stop + timedelta(days=1)
            frame = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
            if frame.empty:
                item["status"] = "empty"
            else:
                with connect(database_path) as db:
                    initialize(db)
                    item["counts"] = write_batch(db, {"index_daily": frame})["index_daily"]
                item.update(status="ok", rows=len(frame), last_date=max(frame.trade_date).isoformat())
        except (FetchError, ValueError, OSError, duckdb.Error) as exc:
            item.update(status="failed", error=str(exc))
        results.append(item)
        logging.info("Index update: %s %s", code, item["status"])
    return {"status": "completed" if all(x["status"] == "ok" for x in results) else "partial",
            "source": "tushare", "results": results}
