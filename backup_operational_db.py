#!/usr/bin/env python3
"""Create verified PathoPilot SQLite backups and a pending-Case rescue page.

This is deliberately external operational tooling.  It does not import the
application or connect to its configured default database: callers must name
the source database explicitly, either with command-line options or the
PATHOPILOT_BACKUP_* environment variables used by the supplied systemd unit.
"""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone


BACKUP_PREFIX = "pathopilot-"
BACKUP_SUFFIX = ".sqlite"
RESCUE_FILENAME = "latest_pending_cases.html"
DEFAULT_FREQUENT_RETENTION = 288  # 48 hours at a ten-minute cadence.
DEFAULT_DAILY_RETENTION = 14
_BACKUP_NAME_RE = re.compile(r"^pathopilot-(\d{8}T\d{6}Z)(?:-\d+)?\.sqlite$")


class BackupError(RuntimeError):
    """Raised when a backup or its rescue artifact cannot be safely made."""


def _utc_timestamp(now: datetime | None = None) -> str:
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("Backup timestamp must have a timezone.")
    return instant.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _atomic_replace(temp_path: Path, target: Path) -> None:
    """Durably replace one artifact only after its complete temporary write."""
    with temp_path.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temp_path, target)
    directory_fd = os.open(str(target.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def verify_sqlite(path: Path) -> None:
    """Confirm the SQLite file opens independently and passes integrity_check."""
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise BackupError(f"SQLite verification could not open {path}: {error}") from error
    if result is None or result[0] != "ok":
        detail = "no integrity result" if result is None else str(result[0])
        raise BackupError(f"SQLite integrity check failed for {path}: {detail}")


def _pending_rescue_html(snapshot_path: Path, generated_at: str) -> str:
    """Build rescue content from saved fields in the backup, never live rendering."""
    connection = sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """SELECT case_number, pending_reason, clinical_info, structured_input,
                      rendered_html, created_at, updated_at,
                      preset_short_code_snapshot, preset_name_snapshot
                 FROM Cases
                WHERE status = 'pending'
                ORDER BY case_number"""
        ).fetchall()
    except sqlite3.Error as error:
        raise BackupError(f"Could not read pending Cases from verified backup: {error}") from error
    finally:
        connection.close()

    sections = []
    for row in rows:
        structured = row["structured_input"]
        try:
            structured = json.dumps(json.loads(structured or "{}"), ensure_ascii=False, indent=2, sort_keys=True)
        except (TypeError, ValueError):
            # Preserve a malformed historical value as evidence rather than
            # making the rescue itself unavailable.
            structured = structured or ""
        metadata = [
            ("Saved/updated", row["updated_at"] or row["created_at"] or ""),
            ("Pending reason", row["pending_reason"] or ""),
            ("Frozen preset", " / ".join(filter(None, (row["preset_short_code_snapshot"], row["preset_name_snapshot"])))),
            ("Clinical information", row["clinical_info"] or ""),
        ]
        metadata_html = "".join(
            f"<dt>{html.escape(label)}</dt><dd>{html.escape(str(value))}</dd>" for label, value in metadata
        )
        # rendered_html is the saved Case artifact from the backup.  It is
        # intentionally not regenerated against current content.
        sections.append(
            "<section>"
            f"<h2>{html.escape(row['case_number'])}</h2><dl>{metadata_html}</dl>"
            "<h3>Saved rendered report</h3>"
            f"<div class=\"report\">{row['rendered_html'] or '<em>No saved rendered report.</em>'}</div>"
            "<h3>Saved structured input</h3>"
            f"<pre>{html.escape(str(structured))}</pre>"
            "</section>"
        )
    body = "".join(sections) or "<p>No pending Cases were present in this backup.</p>"
    return f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><title>PathoPilot pending-Case rescue</title>
<style>body{{font-family:sans-serif;margin:2rem;max-width:70rem}}section{{border-top:1px solid #999;margin-top:2rem;padding-top:1rem}}dt{{font-weight:bold}}dd{{margin:0 0 .6rem}}.report{{border:1px solid #ccc;padding:1rem}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style>
</head><body><h1>PathoPilot pending-Case rescue</h1>
<p>Generated {html.escape(generated_at)} from a verified SQLite backup. This file shows saved Case state only; it does not reconstruct or re-render Cases.</p>
{body}</body></html>"""


def _write_temp_text(directory: Path, content: str) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=".pathopilot-rescue-", suffix=".tmp", dir=directory, text=True)
    path = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _backup_timestamp(path: Path) -> datetime | None:
    match = _BACKUP_NAME_RE.fullmatch(path.name)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def apply_retention(backup_dir: Path, *, frequent_count: int, daily_count: int) -> list[Path]:
    """Keep recent snapshots plus one older snapshot per UTC day; return removals."""
    if frequent_count < 1 or daily_count < 0:
        raise ValueError("frequent retention must be at least 1 and daily retention cannot be negative.")
    backups = [(path, _backup_timestamp(path)) for path in backup_dir.iterdir()]
    backups = [(path, timestamp) for path, timestamp in backups if timestamp is not None]
    backups.sort(key=lambda item: (item[1], item[0].name), reverse=True)
    keep = {path for path, _ in backups[:frequent_count]}
    kept_days: set[str] = set()
    for path, timestamp in backups[frequent_count:]:
        day = timestamp.strftime("%Y-%m-%d")
        if len(kept_days) < daily_count and day not in kept_days:
            keep.add(path)
            kept_days.add(day)
    removed = []
    for path, _ in backups:
        if path not in keep:
            path.unlink()
            removed.append(path)
    return removed


def create_backup(
    source_db: Path,
    backup_dir: Path,
    *,
    frequent_count: int = DEFAULT_FREQUENT_RETENTION,
    daily_count: int = DEFAULT_DAILY_RETENTION,
    now: datetime | None = None,
) -> tuple[Path, Path]:
    """Create and verify a backup and matching rescue HTML, then publish both."""
    source_db = source_db.resolve()
    backup_dir = backup_dir.resolve()
    if not source_db.is_file():
        raise BackupError(f"Source database does not exist or is not a file: {source_db}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    if source_db.parent == backup_dir:
        raise BackupError("Backup directory must be separate from the live database directory.")
    timestamp = _utc_timestamp(now)
    backup_path = backup_dir / f"{BACKUP_PREFIX}{timestamp}{BACKUP_SUFFIX}"
    suffix = 1
    while backup_path.exists():
        backup_path = backup_dir / f"{BACKUP_PREFIX}{timestamp}-{suffix}{BACKUP_SUFFIX}"
        suffix += 1
    temp_backup = backup_dir / f".{backup_path.name}.tmp"
    rescue_path = backup_dir / RESCUE_FILENAME
    temp_rescue: Path | None = None
    try:
        source = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
        destination = sqlite3.connect(temp_backup)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        verify_sqlite(temp_backup)
        temp_rescue = _write_temp_text(backup_dir, _pending_rescue_html(temp_backup, timestamp))
        _atomic_replace(temp_backup, backup_path)
        _atomic_replace(temp_rescue, rescue_path)
        apply_retention(backup_dir, frequent_count=frequent_count, daily_count=daily_count)
    except Exception:
        temp_backup.unlink(missing_ok=True)
        if temp_rescue is not None:
            temp_rescue.unlink(missing_ok=True)
        raise
    return backup_path, rescue_path


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", default=os.environ.get("PATHOPILOT_BACKUP_SOURCE_DB"))
    parser.add_argument("--backup-dir", default=os.environ.get("PATHOPILOT_BACKUP_DIR"))
    parser.add_argument("--frequent-retention", type=_positive_int,
                        default=int(os.environ.get("PATHOPILOT_BACKUP_FREQUENT_RETENTION", DEFAULT_FREQUENT_RETENTION)))
    parser.add_argument("--daily-retention", type=_positive_int,
                        default=int(os.environ.get("PATHOPILOT_BACKUP_DAILY_RETENTION", DEFAULT_DAILY_RETENTION)))
    args = parser.parse_args(argv)
    if not args.source_db or not args.backup_dir:
        parser.error("--source-db and --backup-dir are required (or set PATHOPILOT_BACKUP_SOURCE_DB and PATHOPILOT_BACKUP_DIR).")
    try:
        backup, rescue = create_backup(
            Path(args.source_db), Path(args.backup_dir),
            frequent_count=args.frequent_retention, daily_count=args.daily_retention,
        )
    except (BackupError, OSError, sqlite3.Error, ValueError) as error:
        print(f"PathoPilot backup failed: {error}", file=sys.stderr)
        return 1
    print(f"Backup verified: {backup}")
    print(f"Pending-Case rescue published: {rescue}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
