"""探索第 1 步: 获取场外基金清单, 保存快照并检查基础数据质量.

在项目根目录运行: python -m research.explore_funds list
本脚本不筛选样本, 不获取历史净值, 也不写入数据库.
"""

import argparse
import json
import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from api.fund_fields import BASIC
from api.tushare_client import FetchError, ROOT, Source
from etl.extract import paged

LOGGER = logging.getLogger(__name__)


def save_json(path: Path, payload: dict | list) -> None:
    """保存统计摘要; 保留中文字符并缩进, 方便直接阅读."""
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def save_csv(path: Path, frame: pd.DataFrame) -> None:
    """保存不含 DataFrame 行索引的 CSV; 使用 BOM 方便 Windows 软件识别中文."""
    frame.to_csv(path, index=False, encoding='utf-8-sig')


def fetch_list(source: Source, folder: Path, as_of: date) -> None:
    """分别获取三种状态的清单, 保存原始快照, 合并结果与质量摘要.

    folder 为本次输出目录, as_of 为采集日期, 不是接口的历史查询条件.
    "全量"仅指 Tushare 返回的全部分页, 尚未与外部名录核对.
    """
    frames = []
    counts = {}
    # L: 存续; D: 摘牌或到期; I: 发行中.market='O' 显式指定场外市场.
    for status in ('L', 'D', 'I'):
        # fund_basic 文档未明确列出分页参数, 先实测其行为: 
        # 两页各 2 条的代码顺序, 应与一次获取 4 条的结果一致且没有重叠.
        first = source.query('fund_basic', BASIC, market='O', status=status, limit=2, offset=0)
        if len(first) > 2:
            raise FetchError('fund_basic: limit ignored; cannot establish complete list')
        if len(first) == 2:
            second = source.query('fund_basic', BASIC, market='O', status=status, limit=2, offset=2)
            combined = source.query('fund_basic', BASIC, market='O', status=status, limit=4, offset=0)
            if list(pd.concat([first, second])['ts_code']) != list(combined['ts_code']):
                raise FetchError('fund_basic: pagination probe failed')
            if set(first['ts_code']) & set(second['ts_code']):
                raise FetchError('fund_basic: offset ignored')
        frame = paged(source, 'fund_basic', BASIC, market='O', status=status)
        # 先保留各状态的原始响应, 后续校验失败时仍可排查源数据.
        save_csv(folder / f'fund_basic_{status}.csv', frame)
        if not frame['market'].eq('O').all() or not frame['status'].eq(status).all():
            raise FetchError('fund_basic: market/status filter mismatch')
        counts[status] = len(frame)
        frames.append(frame)
    # 不静默去重: 代码缺失或重复时停止, 避免掩盖分页或源数据问题.
    all_funds = pd.concat(frames, ignore_index=True)
    if all_funds.empty or all_funds['ts_code'].isna().any() or all_funds['ts_code'].duplicated().any():
        raise FetchError('fund_basic: empty list or invalid/duplicate codes')
    save_csv(folder / 'fund_basic_all.csv', all_funds)
    # 分别统计"有值"和"能解析为日期"的数量; 非法日期转为 NaT.
    # 这里只做质量检查, 不据此把到期日认定为实际终止日.
    date_quality = {}
    for status, group in all_funds.groupby('status'):
        date_quality[status] = {}
        for column in ('found_date', 'due_date', 'delist_date'):
            parsed = pd.to_datetime(group[column], format='%Y%m%d', errors='coerce')
            date_quality[status][column] = {
                'nonempty': int(group[column].notna().sum()), 'valid': int(parsed.notna().sum())
            }
    # 最后写入摘要: 只有全部状态获取和校验完成, 才生成此文件.
    save_json(folder / 'list_summary.json', {
        'as_of': as_of.isoformat(), 'counts_by_status': counts, 'total': len(all_funds),
        'coverage': 'All pages returned by Tushare for L/D/I; not independently reconciled to a regulator list',
        'invalid_found_dates': int(pd.to_datetime(all_funds.found_date, format='%Y%m%d', errors='coerce').isna().sum()),
        'date_quality_by_status': date_quality,
    })
    LOGGER.info('List complete: %s; output=%s', counts, folder)


def main() -> int:
    """解析命令行, 配置输出与日志并运行清单任务; 返回进程退出码."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('step', choices=['list'])
    parser.add_argument('--run-dir', type=Path)
    args = parser.parse_args()
    # 默认使用时间戳目录保存独立快照; 显式指定已有目录会覆盖同名输出.
    folder = args.run_dir or ROOT / 'data' / 'fund_list' / datetime.now().strftime('%Y%m%d_%H%M%S')
    folder.mkdir(parents=True, exist_ok=True)
    log_dir = ROOT / 'logs'
    log_dir.mkdir(exist_ok=True)
    # 同时输出到终端和本地日志, 便于观察进度以及事后检查失败原因.
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(log_dir / 'fund_exploration.log', encoding='utf-8')])
    LOGGER.info('Start step=%s output=%s', args.step, folder)
    try:
        source = Source()
        fetch_list(source, folder, date.today())
    except FetchError as exc:
        # 此处只记录请求层整理过的安全错误信息; 非零退出码表示任务失败.
        LOGGER.error('Task failed: %s', exc)
        return 1
    LOGGER.info('End step=%s', args.step)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
