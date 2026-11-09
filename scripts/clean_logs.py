"""预览过期日志清单; 添加 --apply 执行清理, 不请求数据接口."""

import argparse
import json
import os
import shutil
import time
from pathlib import Path

from database.connection import ROOT


SUMMARY_PREFIXES = {
    "database": ("database_update_", "interrupted_update_check_", "index_prices_"),
    "fund_add": ("add_funds_", "fund_batch_"),
    "fund_list": ("fund_list_sync_",),
    "research": ("fund_details_",),
}
SUCCESS_DAYS = 30
ERROR_DAYS = 90
PREVIEW_DAYS = 7


def _check_path(root: Path, path: Path) -> None:
    """删除前确认绝对路径位于日志目录内, 拒绝符号链接及目录联接."""
    path.resolve().relative_to(root)
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("跳过链接或目录联接")
        if part == root:
            break
    if path.resolve() == root:
        raise ValueError("不能清理日志根目录")


def _preview_info(root: Path, path: Path) -> tuple[int, float]:
    """按目录及全部子项的最新修改时间判断预览是否过期."""
    _check_path(root, path)
    size, modified = 0, path.stat().st_mtime

    def fail(error):
        raise error

    for parent, directories, files in os.walk(path, onerror=fail, followlinks=False):
        for name in [*directories, *files]:
            child = Path(parent) / name
            _check_path(root, child)
            info = child.stat()
            modified = max(modified, info.st_mtime)
            if child.is_file():
                size += info.st_size
    return size, modified


def expired_logs(root: Path, *, now: float | None = None) -> tuple[list[dict], list[dict]]:
    """只选择已知任务摘要和过期预览目录, 不选择 .log 或其他文件."""
    root = root.absolute()
    if root.resolve() != root or root.is_junction():
        raise ValueError("日志根目录不能是链接或目录联接")
    now = time.time() if now is None else now
    candidates, skipped = [], []
    for group, prefixes in SUMMARY_PREFIXES.items():
        directory = root / group
        if not directory.exists():
            continue
        try:
            _check_path(root, directory)
        except (OSError, ValueError) as exc:
            skipped.append({"path": str(directory), "reason": str(exc)})
            continue
        for path in sorted(directory.glob("*.json")):
            if not path.name.startswith(prefixes):
                continue
            try:
                _check_path(root, path)
                summary = json.loads(path.read_text(encoding="utf-8-sig"))
                days = SUCCESS_DAYS if summary.get("status") == "completed" else ERROR_DAYS
                info = path.stat()
                if info.st_mtime < now - days * 86400:
                    candidates.append({"path": str(path), "kind": "summary", "bytes": info.st_size,
                                       "retention_days": days, "modified": info.st_mtime})
            except (OSError, ValueError, AttributeError) as exc:
                skipped.append({"path": str(path), "reason": str(exc)})
    previews = root / "previews"
    if previews.exists():
        try:
            _check_path(root, previews)
            for path in sorted(previews.iterdir()):
                try:
                    _check_path(root, path)
                    if not path.is_dir():
                        continue
                    size, modified = _preview_info(root, path)
                    if modified < now - PREVIEW_DAYS * 86400:
                        candidates.append({"path": str(path), "kind": "preview", "bytes": size,
                                           "retention_days": PREVIEW_DAYS, "modified": modified})
                except (OSError, ValueError) as exc:
                    skipped.append({"path": str(path), "reason": str(exc)})
        except (OSError, ValueError) as exc:
            skipped.append({"path": str(previews), "reason": str(exc)})
    return candidates, skipped


def clean_logs(root: Path, *, apply: bool = False, now: float | None = None) -> dict:
    """默认只列清单. 执行前再次检查路径和修改时间, 忙碌或变化的文件跳过."""
    root = root.absolute()
    now = time.time() if now is None else now
    candidates, skipped = expired_logs(root, now=now)
    deleted = []
    if apply:
        for item in candidates:
            path = Path(item["path"])
            try:
                _check_path(root, path)
                if item["kind"] == "preview":
                    _, modified = _preview_info(root, path)
                else:
                    modified = path.stat().st_mtime
                if modified != item["modified"] or modified >= now - item["retention_days"] * 86400:
                    raise ValueError("文件在检查后发生变化")
                if item["kind"] == "preview":
                    shutil.rmtree(path)
                else:
                    path.unlink()
                deleted.append(item["path"])
            except (OSError, ValueError) as exc:
                skipped.append({"path": str(path), "reason": str(exc)})
    return {"mode": "apply" if apply else "preview", "candidates": candidates, "skipped": skipped,
            "candidate_count": len(candidates), "candidate_bytes": sum(x["bytes"] for x in candidates),
            "deleted_count": len(deleted), "deleted": deleted,
            "deleted_bytes": sum(x["bytes"] for x in candidates if x["path"] in deleted)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="删除清单中的过期摘要和预览, 默认仅预览")
    args = parser.parse_args()
    try:
        result = clean_logs(ROOT / "logs", apply=args.apply)
    except (OSError, ValueError) as exc:
        print(f"日志清理失败: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(result["skipped"]))


if __name__ == "__main__":
    raise SystemExit(main())
