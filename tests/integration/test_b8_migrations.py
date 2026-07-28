from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_b8_pairing_migration_upgrades_and_downgrades(tmp_path: Path, monkeypatch) -> None:
    database_url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    monkeypatch.setenv("DEVPILOT_DATABASE_URL", database_url)
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    assert "remote_host_pairings" in inspect(create_engine(database_url)).get_table_names()

    command.downgrade(config, "0003_b7_teams")
    assert "remote_host_pairings" not in inspect(create_engine(database_url)).get_table_names()

    command.upgrade(config, "head")
    assert "remote_host_pairings" in inspect(create_engine(database_url)).get_table_names()
