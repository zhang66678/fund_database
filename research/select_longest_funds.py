"""从最新完整场外基金快照中筛选存续时间最长的 10 个代码, 包含已终止基金.

运行方式: python -m research.select_longest_funds
只读取本地文件, 不读取 Token, 不调用 Tushare, 不修改原始清单.
"""

import argparse
import json
import logging
import re
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)


def latest_snapshot(base: Path) -> Path:
    """按采集目录的时间戳选择最新完整快照, 跳过尚未生成摘要的失败任务."""
    candidates = [
        path for path in base.iterdir()
        if path.is_dir() and re.fullmatch(r'\d{8}_\d{6}', path.name)
        and (path / 'fund_basic_all.csv').is_file()
        and (path / 'list_summary.json').is_file()
    ]
    if not candidates:
        raise ValueError('没有找到完整清单快照, 请先运行 research.explore_funds list')
    return max(candidates, key=lambda path: path.name)


def parse_dates(series: pd.Series) -> pd.Series:
    """只接受接口的 YYYYMMDD 格式, 空值或非法日期转为 NaT."""
    text = series.astype('string').str.strip()
    return pd.to_datetime(text.where(text.str.fullmatch(r'\d{8}', na=False)),
                          format='%Y%m%d', errors='coerce')


def rank_funds(frame: pd.DataFrame, as_of: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """计算并排序, 同时返回无法参与排名的记录及原因.

    L 计算至采集日, 即使存在合同到期日也不提前结束.
    D 优先使用 delist_date, 缺失时以 due_date 近似, 并保留来源标记.
    截止日无效时排除记录, 不将已终止基金默认计算至今天.
    """
    required = {'ts_code', 'name', 'market', 'status', 'found_date', 'delist_date', 'due_date'}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f'清单缺少字段: {sorted(missing)}')
    data = frame.copy()
    codes = data['ts_code'].astype('string').str.strip()
    if codes.isna().any() or codes.eq('').any() or codes.duplicated().any():
        raise ValueError('基金代码缺失或重复, 请检查原始快照')
    cutoff = pd.Timestamp(as_of)
    start = parse_dates(data['found_date'])
    delist = parse_dates(data['delist_date'])
    due = parse_dates(data['due_date'])
    active = data['status'].eq('L')
    ended = data['status'].eq('D')
    # 有退市日期但格式非法时不悄悄改用到期日, 保留异常供排查.
    has_delist = data['delist_date'].astype('string').fillna('').str.strip().ne('')
    end = pd.Series(pd.NaT, index=data.index, dtype='datetime64[ns]')
    end.loc[active] = cutoff
    end.loc[ended & has_delist] = delist
    end.loc[ended & ~has_delist] = due
    data['end_date_source'] = ''
    data.loc[active, 'end_date_source'] = 'snapshot_as_of'
    data.loc[ended & has_delist, 'end_date_source'] = 'delist_date'
    data.loc[ended & ~has_delist, 'end_date_source'] = 'due_date_proxy'
    data['uses_due_date_proxy'] = ended & ~has_delist
    data['as_of'] = as_of.isoformat()
    data['calculation_end_date'] = end.dt.strftime('%Y-%m-%d')
    data['age_days'] = (end - start).dt.days.astype('Int64')
    # 天数决定排名, 年数只是便于阅读的近似展示, 不用于排序.
    data['age_years_approx'] = (data['age_days'] / 365.2425).round(2)

    reason = pd.Series('', index=data.index)
    checks = [
        (~data['market'].eq('O'), 'not_otc'),
        (~data['status'].isin(['L', 'D']), 'not_active_or_terminated'),
        (start.isna(), 'invalid_found_date'),
        (start.gt(cutoff), 'found_after_snapshot'),
        (end.isna(), 'missing_or_invalid_end_date'),
        (end.gt(cutoff), 'end_after_snapshot'),
        (end.lt(start), 'end_before_found_date'),
    ]
    # 每条记录保留首个排除原因, 不在源数据上填补或猜测日期.
    for invalid, label in checks:
        reason.loc[reason.eq('') & invalid] = label
    excluded = data.loc[reason.ne('')].copy()
    excluded['exclusion_reason'] = reason.loc[excluded.index]
    ranked = data.loc[reason.eq('')].sort_values(
        ['age_days', 'ts_code'], ascending=[False, True]
    ).reset_index(drop=True)
    ranked.insert(0, 'rank', range(1, len(ranked) + 1))
    return ranked, excluded


def select_snapshot(folder: Path) -> pd.DataFrame:
    """核对快照后输出排名和摘要, 重跑覆盖本快照的筛选结果, 保留原始 CSV."""
    metadata = json.loads((folder / 'list_summary.json').read_text(encoding='utf-8'))
    as_of = date.fromisoformat(metadata['as_of'])
    frame = pd.read_csv(folder / 'fund_basic_all.csv', dtype=str)
    if len(frame) != metadata['total']:
        raise ValueError('清单行数与快照摘要不一致')
    counts = frame['status'].value_counts().to_dict()
    expected = metadata['counts_by_status']
    if any(counts.get(key, 0) != expected.get(key, 0) for key in set(counts) | set(expected)):
        raise ValueError('状态数量与快照摘要不一致')
    ranked, excluded = rank_funds(frame, as_of)
    if len(ranked) < 10:
        raise ValueError(f'有效候选仅 {len(ranked)} 条, 不足 10 条')
    top = ranked.head(10)
    output = folder / 'longest_lived'
    output.mkdir(exist_ok=True)
    for name, result in [('top_10.csv', top), ('ranked_all.csv', ranked), ('excluded.csv', excluded)]:
        result.to_csv(output / name, index=False, encoding='utf-8-sig')
    summary = {
        'source_snapshot': folder.name, 'as_of': as_of.isoformat(),
        'input_rows': len(frame), 'eligible_rows': len(ranked), 'excluded_rows': len(excluded),
        'eligible_status_counts': ranked['status'].value_counts().to_dict(),
        'selected_status_counts': top['status'].value_counts().to_dict(),
        'due_date_proxy_rows': int(ranked['uses_due_date_proxy'].sum()),
        'selected_due_date_proxy_rows': int(top['uses_due_date_proxy'].sum()),
        'exclusion_counts': excluded['exclusion_reason'].value_counts().to_dict(),
        'method': 'L: snapshot date; D: delist_date, otherwise due_date proxy; age_days descending, ts_code ascending',
        'limitation': 'Ranking uses source dates, not independently verified termination dates. Share-class codes are ranked separately.',
    }
    (output / 'selection_summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    LOGGER.info('筛选完成: 输入=%s 有效=%s 排除=%s 输出=%s', len(frame), len(ranked), len(excluded), output)
    LOGGER.info('使用到期日近似的候选=%s, 前十名=%s', summary['due_date_proxy_rows'], summary['selected_due_date_proxy_rows'])
    return top


def main() -> int:
    """默认选择最新完整快照, 也可以显式指定旧快照复现结果."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, help='指定清单快照目录, 默认自动选择最新完整快照')
    args = parser.parse_args()
    (ROOT / 'logs').mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(
                            ROOT / 'logs' / 'fund_selection.log', encoding='utf-8')])
    LOGGER.info('开始筛选最长存续基金')
    try:
        folder = args.run_dir or latest_snapshot(ROOT / 'data' / 'exploration')
        LOGGER.info('使用快照: %s', folder)
        select_snapshot(folder)
    except (OSError, ValueError, KeyError) as exc:
        LOGGER.error('筛选失败: %s', exc)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
