"""命令行脚本共用的日志大小限制."""

from logging.handlers import RotatingFileHandler
from pathlib import Path


MAX_LOG_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 5


def rotating_handler(path: Path) -> RotatingFileHandler:
    """写满 10 MB 后轮转, 保留最近五个备份."""
    return RotatingFileHandler(path, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUPS,
                               encoding="utf-8", delay=True)
