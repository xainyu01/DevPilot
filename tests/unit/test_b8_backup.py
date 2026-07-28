from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from packages.persistence import backup_sqlite_database


def test_sqlite_backup_creates_consistent_non_overwriting_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE notes (value TEXT)")
        connection.execute("INSERT INTO notes VALUES ('release candidate')")

    target = backup_sqlite_database(f"sqlite:///{source.as_posix()}", tmp_path / "backup.db")

    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT value FROM notes").fetchone() == ("release candidate",)
    with pytest.raises(FileExistsError):
        backup_sqlite_database(f"sqlite:///{source.as_posix()}", target)
