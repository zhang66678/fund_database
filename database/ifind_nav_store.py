"""iFinD 净值专用库, 与现有 Tushare 库保持独立."""

from pathlib import Path
from contextlib import contextmanager

from database.connection import DATABASE_PATH, IFIND_DATABASE_PATH, connect
from database.schema import IFIND_TABLES, initialize_ifind
from database.writer import upsert


def check_paths(path: Path, reference_path: Path = DATABASE_PATH) -> None:
    if path.resolve() in {DATABASE_PATH.resolve(), reference_path.resolve()}:
        raise ValueError("iFinD 净值库不能使用 Tushare 数据库路径")


def initialize_store(path: Path = IFIND_DATABASE_PATH, reference_path: Path = DATABASE_PATH) -> None:
    check_paths(path, reference_path)
    with connect(path) as db:
        initialize_ifind(db)


@contextmanager
def nav_writer(path: Path, reference_path: Path = DATABASE_PATH):
    """整轮任务复用一个主线程写连接, 每只基金仍独立提交或回滚."""
    check_paths(path, reference_path)
    with connect(path) as db:
        initialize_ifind(db)

        def save(frame):
            db.execute("BEGIN TRANSACTION")
            try:
                counts = upsert(db, "fund_nav", frame, spec=IFIND_TABLES["fund_nav"])
                db.execute("COMMIT")
                return counts
            except Exception:
                db.execute("ROLLBACK")
                raise

        yield save


def save_nav(path: Path, frame) -> dict:
    """单次写入入口; 批量更新使用 nav_writer 复用连接."""
    with nav_writer(path) as save:
        return save(frame)
