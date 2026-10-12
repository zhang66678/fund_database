"""复用分页和日期分段获取逻辑, 通过 API 层请求数据."""

from datetime import date

import pandas as pd

from api.tushare_client import FetchError


def paged(source, endpoint: str, fields: str, size: int = 1000, **params) -> pd.DataFrame:
    """分页读取直到空页; 如果服务器重复返回同一页, 则报错停止.

    size 是请求的每页行数, offset 是已经读取的行数.
    即使某页不足 size 行, 也继续请求, 防止服务器另有较小的行数上限.
    """
    frames = []
    seen = set()
    offset = 0
    # 设置页数保护, 避免异常接口让探索任务无限请求.
    for _ in range(200):
        frame = source.query(endpoint, fields, limit=size, offset=offset, **params)
        if frame.empty:
            return pd.concat(frames, ignore_index=True) if frames else frame
        # 用不含行索引的页面内容识别重复页, 检查 offset 是否被忽略.
        signature = frame.to_json(orient='split', index=False)
        if signature in seen:
            raise FetchError(f'{endpoint}: repeated page; completeness not established')
        seen.add(signature)
        frames.append(frame)
        offset += len(frame)  # 按实际返回行数前进, 而不是按请求的 size 前进.
    raise FetchError(f'{endpoint}: page safety limit reached')


def fetch_series(source, endpoint: str, fields: str, code: str,
                 start: date, end: date) -> pd.DataFrame:
    """按两年窗口请求, 避免长历史被截断, 保留源数据的实际披露日期."""
    parts = []
    date_col = 'nav_date' if endpoint == 'fund_nav' else 'trade_date'
    for year in range(start.year, end.year + 1, 2):
        left = max(start, date(year, 1, 1)).strftime('%Y%m%d')
        right = min(end, date(year + 1, 12, 31)).strftime('%Y%m%d')
        part = source.query(endpoint, fields, ts_code=code, start_date=left, end_date=right)
        if len(part) >= 2000:
            raise FetchError(f'{endpoint}: possible truncation for {left}-{right}')
        if not part.empty and (not part[date_col].between(left, right).all()
                               or not part['ts_code'].eq(code).all()):
            raise FetchError(f'{endpoint}: invalid code/date range')
        parts.append(part)
    return pd.concat(parts, ignore_index=True).drop_duplicates()
