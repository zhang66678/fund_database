"""组装 demo 服务, 路径和存储实现集中在这里."""

import os
from pathlib import Path

from database.csv_repository import CsvFundRepository
from services.fund_service import FundService


ROOT = Path(__file__).resolve().parents[1]
# 默认使用已经验证的样本快照, 可通过 NAV_CSV 环境变量切换文件.
DEFAULT_NAV_PATH = Path(
    "data/exploration/20260921_095101/longest_lived/history/20260921_102854/nav.csv"
)


def create_service() -> FundService:
    """每次查询读取一次文件, demo 暂不增加缓存和失效管理."""
    path = Path(os.environ.get("NAV_CSV") or DEFAULT_NAV_PATH)
    if not path.is_absolute():
        path = ROOT / path
    # 默认匹配当前净值快照对应的样本名单, 自定义文件可另设 FUND_NAMES_CSV.
    names_setting = os.environ.get("FUND_NAMES_CSV")
    names_path = Path(names_setting) if names_setting else path.parent.parent.parent / "top_10.csv"
    if not names_path.is_absolute():
        names_path = ROOT / names_path
    return FundService(CsvFundRepository(path, names_path if names_setting or names_path.is_file() else None))
