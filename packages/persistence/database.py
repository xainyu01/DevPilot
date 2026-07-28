"""Database engine and transaction helpers."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.pool import StaticPool

from .models import Base


def default_database_url(workspace_root: Path) -> str:
    """Return the local-first SQLite URL used by the API when not configured."""
    data_dir = workspace_root / ".codeassist"
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(data_dir / 'codeassist.db').as_posix()}"


class Database:
    """Small synchronous SQLAlchemy unit-of-work wrapper.

    SQLite is the default for a local process.  PostgreSQL URLs are passed
    directly to SQLAlchemy, so repositories do not contain SQLite-specific
    query logic.
    """

    def __init__(self, url: str | None = None) -> None:
        self.url = url or os.environ.get("CODEASSIST_DATABASE_URL", "sqlite:///:memory:")
        connect_args = {"check_same_thread": False} if self.url.startswith("sqlite") else {}
        engine_kwargs: dict[str, object] = {
            "future": True,
            "connect_args": connect_args,
        }
        if self.url in {"sqlite://", "sqlite:///:memory:"}:
            engine_kwargs["poolclass"] = StaticPool
        self.engine: Engine = create_engine(self.url, **engine_kwargs)
        if self.url.startswith("sqlite"):
            event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

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


def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()
