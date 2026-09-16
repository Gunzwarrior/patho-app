"""Focused tests for external SQLite backup and pending-Case rescue tooling.

These tests create their own minimal temporary databases. They intentionally do
not use PathoPilot's database module or the operational ``pathology.db``.
"""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

import backup_operational_db as backup


def _source_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """CREATE TABLE Cases (
               id INTEGER PRIMARY KEY,
               case_number TEXT NOT NULL,
               status TEXT NOT NULL,
               pending_reason TEXT,
               clinical_info TEXT,
               structured_input TEXT,
               rendered_html TEXT,
               created_at TEXT,
               updated_at TEXT,
               preset_short_code_snapshot TEXT,
               preset_name_snapshot TEXT
           );
           INSERT INTO Cases VALUES
             (1, '26PR101', 'pending', 'IHC', 'Saved clinical detail',
              '{"field":"saved value"}', '<p>SAVED PENDING REPORT</p>',
              '2026-09-16 08:00:00', '2026-09-16 09:00:00', 'gt', 'Gastric Trio'),
             (2, '26PR102', 'validated', NULL, 'Do not expose', '{}',
              '<p>VALIDATED ONLY</p>', '2026-09-16 08:00:00', '2026-09-16 09:00:00',
              'vb', 'Gallbladder');"""
    )
    connection.commit()
    connection.close()


def _when(minute: int) -> datetime:
    return datetime(2026, 9, 16, 8, minute, tzinfo=timezone.utc)


def test_backup_is_independently_readable_and_rescue_uses_saved_pending_state(tmp_path):
    source = tmp_path / "live" / "pathology.db"
    source.parent.mkdir()
    _source_db(source)
    backup_dir = tmp_path / "backups"

    snapshot, rescue = backup.create_backup(source, backup_dir, now=_when(0))

    backup.verify_sqlite(snapshot)
    connection = sqlite3.connect(snapshot)
    assert connection.execute("SELECT rendered_html FROM Cases WHERE case_number = '26PR101'").fetchone()[0] == "<p>SAVED PENDING REPORT</p>"
    connection.close()
    rescue_html = rescue.read_text(encoding="utf-8")
    assert "26PR101" in rescue_html
    assert "SAVED PENDING REPORT" in rescue_html
    assert "saved value" in rescue_html
    assert "VALIDATED ONLY" not in rescue_html
    assert "does not reconstruct or re-render" in rescue_html


def test_backup_is_consistent_while_source_uses_wal(tmp_path):
    source = tmp_path / "live" / "pathology.db"
    source.parent.mkdir()
    _source_db(source)
    writer = sqlite3.connect(source)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
    # Hold a real concurrent writer transaction open. An online backup must
    # still obtain a coherent prior committed snapshot rather than requiring a
    # blind file copy or a writer shutdown.
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE Cases SET clinical_info = 'Committed during WAL use' WHERE id = 1")

    snapshot, _ = backup.create_backup(source, tmp_path / "backups", now=_when(10))

    restored = sqlite3.connect(snapshot)
    assert restored.execute("SELECT clinical_info FROM Cases WHERE id = 1").fetchone()[0] == "Saved clinical detail"
    restored.close()
    writer.commit()
    writer.close()


def test_retention_keeps_recent_and_one_older_backup_per_day(tmp_path):
    names = [
        "pathopilot-20260916T120000Z.sqlite",
        "pathopilot-20260916T110000Z.sqlite",
        "pathopilot-20260915T120000Z.sqlite",
        "pathopilot-20260915T110000Z.sqlite",
        "pathopilot-20260914T120000Z.sqlite",
        "unrelated.sqlite",
    ]
    for name in names:
        (tmp_path / name).write_text("placeholder", encoding="utf-8")

    removed = backup.apply_retention(tmp_path, frequent_count=2, daily_count=1)

    assert {path.name for path in removed} == {
        "pathopilot-20260915T110000Z.sqlite",
        "pathopilot-20260914T120000Z.sqlite",
    }
    assert (tmp_path / "pathopilot-20260916T120000Z.sqlite").exists()
    assert (tmp_path / "pathopilot-20260916T110000Z.sqlite").exists()
    assert (tmp_path / "pathopilot-20260915T120000Z.sqlite").exists()
    assert (tmp_path / "unrelated.sqlite").exists()


def test_rescue_failure_does_not_replace_previous_published_artifacts(tmp_path, monkeypatch):
    source = tmp_path / "live" / "pathology.db"
    source.parent.mkdir()
    _source_db(source)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    previous_snapshot = backup_dir / "pathopilot-20260915T120000Z.sqlite"
    previous_snapshot.write_text("previous backup", encoding="utf-8")
    previous_rescue = backup_dir / backup.RESCUE_FILENAME
    previous_rescue.write_text("previous rescue", encoding="utf-8")
    monkeypatch.setattr(backup, "_pending_rescue_html", lambda *_: (_ for _ in ()).throw(RuntimeError("rescue failed")))

    with pytest.raises(RuntimeError, match="rescue failed"):
        backup.create_backup(source, backup_dir, now=_when(20))

    assert previous_snapshot.read_text(encoding="utf-8") == "previous backup"
    assert previous_rescue.read_text(encoding="utf-8") == "previous rescue"
    assert not list(backup_dir.glob("pathopilot-20260916T*.sqlite"))


def test_disposable_restore_round_trip(tmp_path):
    source = tmp_path / "live" / "pathology.db"
    source.parent.mkdir()
    _source_db(source)
    snapshot, _ = backup.create_backup(source, tmp_path / "backups", now=_when(30))
    restore = tmp_path / "restore.db"
    restore.write_bytes(snapshot.read_bytes())

    backup.verify_sqlite(restore)
    restored = sqlite3.connect(restore)
    assert restored.execute("SELECT case_number, status FROM Cases ORDER BY id").fetchall() == [
        ("26PR101", "pending"), ("26PR102", "validated"),
    ]
    restored.close()
