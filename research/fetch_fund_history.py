"""获取已选 10 个基金的历史净值, 复权因子和分红, 不保存中间请求文件.

运行: python -m research.fetch_fund_history
"""

import argparse
import json
import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from api.fund_fields import NAV_FIELDS, ADJ_FIELDS, DIV_FIELDS
from etl.extract import paged, fetch_series
from research.select_longest_funds import latest_snapshot
from api.tushare_client import ROOT, FetchError, Source

LOGGER = logging.getLogger(__name__)


def combine_nav(nav: pd.DataFrame, factors: pd.DataFrame) -> pd.DataFrame:
    """按代码和日期匹配因子, 不填充缺失值, 不用净值比值替代独立因子."""
    factors = factors.rename(columns={'trade_date': 'nav_date'}).drop_duplicates()
    if factors.duplicated(['ts_code', 'nav_date']).any():
        raise FetchError('fund_adj: conflicting factors on the same date')
    # 保留同一净值日期的不同公告版本, 不擅自丢弃修订记录.
    return nav.merge(factors, on=['ts_code', 'nav_date'], how='left', validate='many_to_one')


def fetch_one(source, endpoint: str, fields: str, fund, end: date) -> pd.DataFrame:
    """净值按日期分段, 分红和因子分页读取到空页."""
    start = datetime.strptime(fund.found_date, '%Y%m%d').date()
    if endpoint == 'fund_nav':
        return fetch_series(source, endpoint, fields, fund.ts_code, start, end)
    params = {'ts_code': fund.ts_code}
    if endpoint == 'fund_adj':
        params.update(start_date=start.strftime('%Y%m%d'), end_date=end.strftime('%Y%m%d'))
    result = paged(source, endpoint, fields, **params)
    if not result['ts_code'].eq(fund.ts_code).all():
        raise FetchError(f'{endpoint}: unexpected fund code')
    if endpoint == 'fund_adj' and not result.trade_date.between(params['start_date'], params['end_date']).all():
        raise FetchError('fund_adj: date filter ignored')
    # 分红不按净值日期连接, 保留不同方案进度及未来派息日的已公告事件.
    return result.drop_duplicates()


def run(source, selected: Path, output: Path, end: date) -> dict:
    """单个接口失败仍继续其他接口, 每只基金处理完覆盖保存本次运行的进度."""
    funds = pd.read_csv(selected, dtype=str)
    if len(funds) != 10 or funds.ts_code.isna().any() or funds.ts_code.duplicated().any():
        raise ValueError('top_10.csv must contain ten unique fund codes')
    starts = pd.to_datetime(funds.found_date, format='%Y%m%d', errors='raise')
    if starts.isna().any() or starts.gt(pd.Timestamp(end)).any():
        raise ValueError('Invalid inception dates or end date')
    output.mkdir(parents=True, exist_ok=False)
    navs, dividends = [], []
    summary = {'selected_file': str(selected), 'requested_end_date': str(end),
               'started_at': datetime.now().isoformat(timespec='seconds'),
               'status': 'running', 'results': []}
    for fund in funds.itertuples(index=False):
        LOGGER.info('开始基金: %s %s', fund.ts_code, fund.name)
        tables = {}
        record = {'ts_code': fund.ts_code, 'name': fund.name}
        for endpoint, fields in [('fund_nav', NAV_FIELDS), ('fund_adj', ADJ_FIELDS), ('fund_div', DIV_FIELDS)]:
            try:
                table = fetch_one(source, endpoint, fields, fund, end)
                record[endpoint] = {'status': 'empty' if table.empty else 'ok', 'rows': len(table)}
            except (FetchError, ValueError) as exc:
                LOGGER.error('%s %s: %s', fund.ts_code, endpoint, exc)
                record[endpoint] = {'status': 'failed', 'reason': str(exc)}
                table = pd.DataFrame(columns=fields.split(','))
            tables[endpoint] = table
        try:
            nav = combine_nav(tables['fund_nav'], tables['fund_adj'])
        except FetchError as exc:
            record['fund_adj'] = {'status': 'failed', 'reason': str(exc)}
            LOGGER.error('%s: %s', fund.ts_code, exc)
            nav = combine_nav(tables['fund_nav'], pd.DataFrame(columns=ADJ_FIELDS.split(',')))
        record['nav_first_date'] = None if nav.empty else str(nav.nav_date.min())
        record['nav_last_date'] = None if nav.empty else str(nav.nav_date.max())
        record['duplicate_nav_dates'] = int(nav.duplicated(['ts_code', 'nav_date']).sum())
        record['null_counts'] = {c: int(nav[c].isna().sum()) for c in nav.columns}
        record['factor_dates_without_nav'] = int((~tables['fund_adj'].trade_date.isin(nav.nav_date)).sum())
        navs.append(nav)
        dividends.append(tables['fund_div'])
        summary['results'].append(record)
        pd.concat(navs, ignore_index=True).sort_values(['ts_code', 'nav_date', 'ann_date']).to_csv(
            output / 'nav.csv', index=False, encoding='utf-8-sig')
        pd.concat(dividends, ignore_index=True).sort_values(['ts_code', 'ex_date', 'ann_date']).to_csv(
            output / 'dividends.csv', index=False, encoding='utf-8-sig')
        (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    summary['status'] = 'partial' if any(
        r[e]['status'] == 'failed' for r in summary['results'] for e in ('fund_nav', 'fund_adj', 'fund_div')
    ) else 'completed'
    summary['finished_at'] = datetime.now().isoformat(timespec='seconds')
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    LOGGER.info('结束: status=%s output=%s', summary['status'], output)
    return summary


def main() -> int:
    """默认使用最新快照已选出的 10 只基金, 抓取至运行当天."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, help='基金清单快照目录')
    parser.add_argument('--end-date', type=date.fromisoformat, default=date.today(), help='截止日期 YYYY-MM-DD')
    args = parser.parse_args()
    (ROOT / 'logs').mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(
                            ROOT / 'logs' / 'fund_history.log', encoding='utf-8')])
    try:
        folder = args.run_dir or latest_snapshot(ROOT / 'data' / 'fund_list')
        selected = folder / 'longest_lived' / 'top_10.csv'
        if not selected.exists():
            raise ValueError('请先运行 research.select_longest_funds 生成 top_10.csv')
        output = folder / 'longest_lived' / 'history' / datetime.now().strftime('%Y%m%d_%H%M%S')
        summary = run(Source(), selected, output, args.end_date)
        return int(summary['status'] != 'completed')
    except (FetchError, OSError, ValueError) as exc:
        LOGGER.error('任务失败: %s', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
