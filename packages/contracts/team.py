"""Team, access-control, session-sharing and remote-host contracts for B7."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class TeamRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class SessionPermission(StrEnum):
    VIEW = "view"
    COLLABORATE = "collaborate"


class HostStatus(StrEnum):
    PAIRING_REQUIRED = "pairing_required"
    PAIRED = "paired"


class UserRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: str(uuid4()))
    display_name: str = Field(min_length=1, max_length=200)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TeamRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(min_length=1, max_length=200)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TeamMember(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_id: str
    user_id: str
    role: TeamRole
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProjectMember(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    user_id: str
    role: TeamRole
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionShare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    recipient_id: str
    permission: SessionPermission
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RemoteHost(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: str(uuid4()))
    team_id: str
    name: str = Field(min_length=1, max_length=200)
    capabilities: list[str] = Field(default_factory=list)
    status: HostStatus = HostStatus.PAIRING_REQUIRED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


__all__ = [
    "HostStatus",
    "ProjectMember",
    "RemoteHost",
    "SessionPermission",
    "SessionShare",
    "TeamMember",
    "TeamRecord",
    "TeamRole",
    "UserRecord",
]
