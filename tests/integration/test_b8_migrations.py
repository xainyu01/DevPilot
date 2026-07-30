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
    inspector = inspect(create_engine(database_url))
    assert "remote_host_pairings" in inspector.get_table_names()
    assert {
        "request_json",
        "result_json",
        "usage_json",
        "verification_json",
        "changes_json",
        "stop_reason",
        "provider_request_id",
        "pending_approval_json",
    }.issubset({column["name"] for column in inspector.get_columns("agent_runs")})

    command.downgrade(config, "0003_b7_teams")
    inspector = inspect(create_engine(database_url))
    assert "remote_host_pairings" not in inspector.get_table_names()
    assert "request_json" not in {
        column["name"] for column in inspector.get_columns("agent_runs")
    }

    command.upgrade(config, "head")
    assert "remote_host_pairings" in inspect(create_engine(database_url)).get_table_names()
