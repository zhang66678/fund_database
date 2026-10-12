"""集中管理 DuckDB 连接和默认路径."""

from contextlib import contextmanager
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "data/fund_database.duckdb"


@contextmanager
def connect(path: Path = DATABASE_PATH, *, read_only: bool = False):
    """连接使用完自动关闭, 事务由调用方控制."""
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(path), read_only=read_only)
    try:
        yield connection
    finally:
        connection.close()
