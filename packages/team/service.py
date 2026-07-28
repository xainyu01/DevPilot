"""Deterministic RBAC service; authentication is intentionally outside this boundary."""

from __future__ import annotations

from packages.contracts import SessionPermission, TeamRole
from packages.persistence import TeamRepository


class AccessDeniedError(PermissionError):
    """Raised when a role does not grant the requested operation."""


class TeamService:
    def __init__(self, repository: TeamRepository) -> None:
        self.repository = repository

    def require_team_admin(self, team_id: str, actor_id: str) -> None:
        membership = self.repository.get_team_member(team_id, actor_id)
        if membership is None or membership.role not in {TeamRole.OWNER, TeamRole.ADMIN}:
            raise AccessDeniedError("team admin role is required")

    def require_project_write(self, project_id: str, actor_id: str) -> None:
        membership = self.repository.get_project_member(project_id, actor_id)
        if membership is None or membership.role in {TeamRole.VIEWER}:
            raise AccessDeniedError("project write permission is required")

    def require_project_read(self, project_id: str, actor_id: str) -> None:
        if self.repository.get_project_member(project_id, actor_id) is None:
            raise AccessDeniedError("project membership is required")

    def require_session_permission(
        self, session_id: str, actor_id: str, permission: SessionPermission
    ) -> None:
        share = self.repository.get_session_share(session_id, actor_id)
        if share is None or (
            permission == SessionPermission.COLLABORATE
            and share.permission != SessionPermission.COLLABORATE
        ):
            raise AccessDeniedError("session permission is required")
