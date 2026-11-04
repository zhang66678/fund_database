"""在临时目录验证日志上限, 清理期限及删除边界."""

import json
import logging
import os
import sys
import time
from pathlib import Path

import pytest

from scripts import clean_logs as command
from scripts import logging_utils


def old_file(path: Path, contents: str, days: int, now: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    os.utime(path, (now - days * 86400, now - days * 86400))
    return path


def test_rotation_limits_files_and_keeps_recent_records(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_utils, "MAX_LOG_BYTES", 80)
    monkeypatch.setattr(logging_utils, "LOG_BACKUPS", 2)
    handler = logging_utils.rotating_handler(tmp_path / "run.log")
    logger = logging.Logger("rotation-test")
    logger.addHandler(handler)
    try:
        for index in range(30):
            logger.info("record-%02d %s", index, "x" * 25)
    finally:
        handler.close()
    files = list(tmp_path.glob("run.log*"))
    assert len(files) == 3 and all(path.stat().st_size <= 80 for path in files)
    assert "record-29" in (tmp_path / "run.log").read_text()
    assert not any("record-00" in path.read_text() for path in files)


def test_cleanup_retention_and_preview_keep_data_and_current_logs(tmp_path):
    root = tmp_path / "logs"
    now = time.time()
    success = old_file(root / "database/database_update_old.json", '{"status":"completed"}', 31, now)
    error = old_file(root / "database/database_update_error.json", '{"status":"partial"}', 91, now)
    recent_error = old_file(root / "database/database_update_recent.json", '{"status":"partial"}', 31, now)
    malformed = old_file(root / "fund_add/add_funds_broken.json", '{bad json', 365, now)
    unknown = old_file(root / "database/user_notes.json", '{}', 365, now)
    current_log = old_file(root / "database/database_update.log", 'still useful', 365, now)
    data = old_file(tmp_path / "data/fund_database.duckdb", 'keep data', 365, now)
    old_preview = root / "previews/old_run"
    old_file(old_preview / "browser/cache", 'old cache', 8, now)
    for folder in (old_preview, old_preview / "browser"):
        os.utime(folder, (now - 8 * 86400, now - 8 * 86400))
    current_preview = root / "previews/current_run"
    old_file(current_preview / "browser/cache", 'current cache', 1, now)
    for folder in (current_preview, current_preview / "browser"):
        os.utime(folder, (now - 20 * 86400, now - 20 * 86400))

    preview = command.clean_logs(root, now=now)
    assert preview["candidate_count"] == 3 and preview["deleted_count"] == 0
    assert success.exists() and error.exists() and old_preview.exists()
    result = command.clean_logs(root, apply=True, now=now)
    assert result["deleted_count"] == 3 and result["deleted_bytes"] > 0
    assert not success.exists() and not error.exists() and not old_preview.exists()
    assert all(path.exists() for path in (recent_error, malformed, unknown, current_log, data, current_preview))
    assert any(item["path"] == str(malformed) for item in result["skipped"])


def test_links_outside_logs_are_preserved(tmp_path):
    root = tmp_path / "logs"
    previews = root / "previews"
    previews.mkdir(parents=True)
    outside = tmp_path / "outside"
    sentinel = old_file(outside / "keep.txt", "keep", 365, time.time())
    link = previews / "old_link"
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(outside), str(link))
        else:
            link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This environment cannot create a link")
    result = command.clean_logs(root, apply=True)
    assert result["deleted_count"] == 0 and result["skipped"]
    assert sentinel.read_text() == "keep" and link.exists()
    with pytest.raises(ValueError):
        command.clean_logs(link, apply=True)


def test_changed_files_are_rechecked_before_deletion(tmp_path, monkeypatch):
    now = time.time()
    root = tmp_path / "logs"
    path = old_file(root / "database/database_update_old.json", '{"status":"completed"}', 31, now)
    original = command.expired_logs

    def plan_then_change(*args, **kwargs):
        plan = original(*args, **kwargs)
        path.write_text('{"status":"partial"}', encoding="utf-8")
        os.utime(path, (now, now))
        return plan

    monkeypatch.setattr(command, "expired_logs", plan_then_change)
    result = command.clean_logs(root, apply=True, now=now)
    assert result["deleted_count"] == 0 and result["skipped"]
    assert path.exists()


def test_command_defaults_to_preview_and_apply_is_explicit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(command, "ROOT", tmp_path)
    path = old_file(tmp_path / "logs/fund_add/add_funds_old.json", '{"status":"completed"}', 31, time.time())
    monkeypatch.setattr(sys, "argv", ["clean_logs"])
    assert command.main() == 0 and path.exists()
    assert json.loads(capsys.readouterr().out)["mode"] == "preview"
    monkeypatch.setattr(sys, "argv", ["clean_logs", "--apply"])
    assert command.main() == 0 and not path.exists()
    assert json.loads(capsys.readouterr().out)["deleted_count"] == 1
