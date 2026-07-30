"""Database engine and transaction helpers."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.pool import StaticPool

from .models import Base


def default_database_url(workspace_root: Path) -> str:
    """Return the local-first SQLite URL used by the API when not configured."""
    data_dir = workspace_root / ".devpilot"
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(data_dir / 'devpilot.db').as_posix()}"


class Database:
    """Small synchronous SQLAlchemy unit-of-work wrapper.

    SQLite is the default for a local process.  PostgreSQL URLs are passed
    directly to SQLAlchemy, so repositories do not contain SQLite-specific
    query logic.
    """

    def __init__(self, url: str | None = None) -> None:
        self.url = url or os.environ.get("DEVPILOT_DATABASE_URL", "sqlite:///:memory:")
        connect_args = {"check_same_thread": False} if self.url.startswith("sqlite") else {}
        engine_kwargs: dict[str, object] = {
            "future": True,
            "connect_args": connect_args,
            "pool_pre_ping": not self.url.startswith("sqlite"),
        }
        if self.url in {"sqlite://", "sqlite:///:memory:"}:
            engine_kwargs["poolclass"] = StaticPool
        self.engine: Engine = create_engine(self.url, **engine_kwargs)
        if self.url.startswith("sqlite"):
            event.listen(self.engine, "connect", _configure_sqlite_connection)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def ensure_real_agent_columns(self) -> None:
        """Upgrade databases historically created with ``create_all`` before R5."""
        inspector = inspect(self.engine)
        if "agent_runs" not in inspector.get_table_names():
            return
        additions = {
            "request_json": "JSON NOT NULL DEFAULT '{}'",
            "result_json": "JSON",
            "usage_json": "JSON NOT NULL DEFAULT '{}'",
            "verification_json": "JSON NOT NULL DEFAULT '{}'",
            "changes_json": "JSON NOT NULL DEFAULT '[]'",
            "stop_reason": "VARCHAR(100)",
            "provider_request_id": "VARCHAR(200)",
            "pending_approval_json": "JSON",
        }
        existing = {column["name"] for column in inspector.get_columns("agent_runs")}
        with self.engine.begin() as connection:
            for name, declaration in additions.items():
                if name not in existing:
                    connection.execute(
                        text(f"ALTER TABLE agent_runs ADD COLUMN {name} {declaration}")
                    )
        if "approvals" in inspector.get_table_names():
            approval_columns = {
                column["name"] for column in inspect(self.engine).get_columns("approvals")
            }
            if "request_json" not in approval_columns:
                with self.engine.begin() as connection:
                    connection.execute(
                        text(
                            "ALTER TABLE approvals ADD COLUMN request_json "
                            "JSON NOT NULL DEFAULT '{}'"
                        )
                    )

    def drop_all(self) -> None:
        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[DbSession]:
        session = DbSession(self.engine)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _configure_sqlite_connection(dbapi_connection: object, _: object) -> None:
    """Enable durable, contention-tolerant SQLite defaults for local deployments."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()
