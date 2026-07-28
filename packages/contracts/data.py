"""Provider-neutral contracts for persisted conversations and memory.

These DTOs deliberately do not import SQLAlchemy.  The persistence package
maps them to relational rows, while the API and domain services can exchange
the same shapes without knowing which database is configured.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .agent import ChatMessage


class SessionStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class MemoryScope(StrEnum):
    USER = "user"
    PROJECT = "project"


class MemoryWriteStatus(StrEnum):
    WRITTEN = "written"
    BLOCKED_SENSITIVE = "blocked_sensitive"


class SessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    thread_id: str = Field(min_length=1)
    user_id: str | None = None
    project_id: str | None = None
    title: str | None = None
    status: SessionStatus = SessionStatus.ACTIVE
    summary: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StoredMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    ordinal: int = Field(default=0, ge=0)
    message: ChatMessage
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    summary: str
    message_count: int = Field(default=0, ge=0)
    covered_through_ordinal: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session: SessionRecord
    messages: list[StoredMessage] = Field(default_factory=list)
    summary: SessionSummary | None = None


class MemoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    owner_id: str
    scope: MemoryScope
    key: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    source: str = "user"
    enabled: bool = True
    revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deleted_at: datetime | None = None


class MemoryRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    memory_id: str
    revision: int = Field(ge=1)
    content: str
    action: Literal["created", "edited", "enabled", "disabled", "deleted"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MemoryWriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: MemoryWriteStatus
    entry: MemoryEntry | None = None
    reason: str | None = None


class ProjectRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str
    source_path: str
    scope_path: str
    filename: str
    content: str
    priority: int = Field(ge=0)
    enabled: bool = True
    source_kind: Literal["user_memory", "agent", "claude", "project", "memory", "other"]
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProjectContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_root: str
    rules: list[ProjectRule] = Field(default_factory=list)

    @property
    def merged_text(self) -> str:
        """Compose broad rules first so later, more specific rules take precedence."""
        sections = [
            f"# Source: {rule.source_path}\n{rule.content}"
            for rule in sorted(self.rules, key=lambda item: (item.priority, item.source_path))
            if rule.enabled and rule.content.strip()
        ]
        return "\n\n---\n\n".join(sections)


class ProjectRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(min_length=1)
    root_path: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


__all__ = [
    "MemoryEntry",
    "MemoryRevision",
    "MemoryScope",
    "MemoryWriteResult",
    "MemoryWriteStatus",
    "ProjectContext",
    "ProjectRecord",
    "ProjectRule",
    "SessionRecord",
    "SessionSnapshot",
    "SessionStatus",
    "SessionSummary",
    "StoredMessage",
]
