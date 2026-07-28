"""Recoverable SQLite backup support for the B8 release workflow."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import unquote


def backup_sqlite_database(database_url: str, destination: Path) -> Path:
    """Create a consistent SQLite snapshot without overwriting an existing backup.

    PostgreSQL backup/restore remains the responsibility of its database server
    tooling and is documented in the deployment runbook.  A SQLite backup is a
    safe, local-first operation and is intentionally the only implemented path.
    """
    source = _sqlite_path(database_url)
    target = destination.expanduser().resolve()
    if target.exists():
        raise FileExistsError(f"backup destination already exists: {target}")
    if not source.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with (
            sqlite3.connect(source) as source_connection,
            sqlite3.connect(target) as target_connection,
        ):
            source_connection.backup(target_connection)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def _sqlite_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("only file-backed sqlite:/// database URLs support CodeAssist backup")
    raw_path = unquote(database_url.removeprefix(prefix))
    if raw_path in {":memory:", ""}:
        raise ValueError("an in-memory SQLite database cannot be backed up")
    return Path(raw_path).expanduser().resolve()
