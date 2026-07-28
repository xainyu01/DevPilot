"""FastAPI entry point for the B4 persistence and development workflow boundary."""

import asyncio
import base64
import hashlib
import os
import secrets
import signal
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from packages.agent_core import AgentRuntime
from packages.contracts import (
    ChatMessage,
    IssueContext,
    MemoryScope,
    ProjectMember,
    ProjectRecord,
    RemoteHost,
    ReviewStatus,
    RunRequest,
    SessionPermission,
    SessionRecord,
    SessionShare,
    TeamMember,
    TeamRecord,
    TeamRole,
    UserRecord,
)
from packages.dev_workflows import DevelopmentWorkflowService
from packages.handover_agent import HandoverAgent
from packages.local_settings import LocalSettings, LocalSettingsStore, LocalUser
from packages.memory import LongTermMemoryStore
from packages.model_gateway import (
    AnthropicAdapter,
    FakeModel,
    ModelGateway,
    OllamaAdapter,
    OpenAIAdapter,
)
from packages.persistence import (
    Database,
    MemoryRepository,
    ProjectRepository,
    RepositoryProfileRepository,
    RuleRepository,
    SessionRepository,
    TeamRepository,
    WorkflowRepository,
    default_database_url,
)
from packages.project_context import ProjectContextService
from packages.repo_intel import RepositoryScanner
from packages.security import (
    AuthenticatedUser,
    AuthenticationService,
    AuthSettings,
    LoginRateLimiter,
    hash_password,
)
from packages.team import AccessDeniedError, TeamService
from packages.tool_runtime import ToolRuntime


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    root_path: str = Field(min_length=1, max_length=2048)


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1)
    user_id: str | None = None
    project_id: str | None = None
    title: str | None = None


class MemoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    source: str = "user"
    scope: MemoryScope = MemoryScope.USER
    owner_id: str | None = None


class MemoryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None
    key: str | None = Field(default=None, min_length=1, max_length=200)


class RuleDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_dir: str | None = None


class WorkflowCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    logs: str = ""
    failing_tests: list[str] = Field(default_factory=list)
    attachments: list[str] = Field(default_factory=list)
    execute_tests: bool = False
    create_worktree: bool = False
    full_tests: bool = False


class ReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReviewStatus


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: ChatMessage
    provider: str | None = None
    model: str | None = None
    run_id: str | None = None


class AttachmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1)


class UserCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    display_name: str = Field(min_length=1, max_length=200)


class TeamCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)


class TeamMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1)
    role: TeamRole


class ProjectMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1)
    role: TeamRole


class SessionShareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient_id: str = Field(min_length=1)
    permission: SessionPermission


class RemoteHostRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    capabilities: list[str] = Field(default_factory=list)


class RemoteHostPairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pairing_code: str = Field(min_length=16, max_length=256)


class LoginRequest(BaseModel):
    """Fixed B7 account login backed by a signed JWT session."""

    model_config = ConfigDict(extra="forbid")
    username: str
    password: str


class RuntimeSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idle_shutdown_minutes: int = Field(ge=1, le=1_440)
    model_provider: str = Field(min_length=1, max_length=100)
    model_name: str = Field(min_length=1, max_length=200)


class LocalUserCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=1024)


def create_app(
    *,
    database_url: str | None = None,
    workspace_root: Path | None = None,
    web_dist_path: Path | None = None,
    auth_settings: AuthSettings | None = None,
) -> FastAPI:
    workspace = (workspace_root or project_root()).expanduser().resolve()
    settings_store = LocalSettingsStore(workspace)
    local_settings = settings_store.load()
    configured_url = database_url or os.environ.get("DEVPILOT_DATABASE_URL")
    database = Database(configured_url or default_database_url(workspace))
    configured_users = _configured_users(local_settings)
    resolved_auth_settings = auth_settings or AuthSettings.from_environment(
        additional_users=configured_users
    )
    authentication = AuthenticationService(resolved_auth_settings)
    app = FastAPI(
        title="DevPilot API",
        version="0.1.0rc1",
        description="Local-first Agent service boundary for the LangGraph core.",
    )
    app.state.database = database
    app.state.workspace_root = workspace
    app.state.agent_runtime = AgentRuntime(_model_gateway(local_settings))
    app.state.runtime_logs: list[dict[str, object]] = []
    app.state.authentication = authentication
    app.state.auth_settings = resolved_auth_settings
    app.state.login_rate_limiter = LoginRateLimiter()
    app.state.settings_store = settings_store
    app.state.local_settings = local_settings
    app.state.active_run_count = 0
    app.state.last_user_activity = time.monotonic()
    database.create_all()
    for user in authentication.users:
        TeamRepository(database).create_user(
            UserRecord(id=user.user_id, display_name=user.display_name)
        )
    _record_runtime_log(
        app,
        event="service.initialized",
        message="FastAPI service initialized",
        data={
            "workspace_root": str(workspace),
            "web_dist": str(web_dist_path or workspace / "apps" / "web" / "dist"),
        },
    )

    def _actor_for_token(token: str | None) -> str | None:
        return authentication.authenticate_access_token(token)

    def _authenticated_actor(authorization: str | None = Header(default=None)) -> str:
        """Authenticate a signed bearer token without exposing an actor-id request field."""
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="bearer token is required")
        token = authorization.removeprefix("Bearer ")
        actor_id = _actor_for_token(token)
        if actor_id is None:
            raise HTTPException(status_code=401, detail="invalid or expired bearer token")
        return actor_id

    @app.middleware("http")
    async def enforce_api_security(request: Request, call_next):
        """Protect the API and add browser-safe headers at one release boundary."""
        public_paths = {"/healthz", "/api/v1/meta", "/api/v1/auth/login", "/api/v1/progress"}
        host_path = request.url.path.startswith("/api/v1/remote-hosts/")
        if (
            request.url.path.startswith("/api/v1/")
            and request.url.path not in public_paths
            and not host_path
        ):
            authorization = request.headers.get("authorization")
            if authorization is None or not authorization.startswith("Bearer "):
                return _apply_security_headers(
                    Response(status_code=401, content="bearer token is required")
                )
            if _actor_for_token(authorization.removeprefix("Bearer ")) is None:
                return _apply_security_headers(
                    Response(status_code=401, content="invalid or expired bearer token")
                )
        response = await call_next(request)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and response.status_code < 400:
            app.state.last_user_activity = time.monotonic()
        return _apply_security_headers(response)

    @app.on_event("startup")
    async def start_idle_shutdown_monitor() -> None:
        app.state.idle_shutdown_task = asyncio.create_task(_idle_shutdown_monitor(app))

    @app.on_event("shutdown")
    async def stop_idle_shutdown_monitor() -> None:
        task = getattr(app.state, "idle_shutdown_task", None)
        if task is not None:
            task.cancel()

    @app.get("/healthz", tags=["system"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "devpilot-api"}

    @app.get("/readyz", tags=["system"])
    def readyz() -> dict[str, str]:
        """Readiness probe that confirms the configured database is reachable."""
        try:
            with database.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise HTTPException(status_code=503, detail="database is unavailable") from exc
        return {"status": "ready", "service": "devpilot-api"}

    @app.get("/api/v1/meta", tags=["system"])
    def meta() -> dict[str, object]:
        return {
            "name": "devpilot",
            "version": "0.1.0rc1",
            "stage": 8,
            "dependency_manager": "uv",
            "features": {
                "agent_core": "available",
                "model_gateway": "available",
                "fake_model": "available",
                "openai_adapter": "available_with_credentials",
                "anthropic_adapter": "available_with_credentials",
                "ollama_adapter": "declared_not_implemented",
                "handover_agent": "available",
                "tool_runtime": "available",
                "policy_engine": "available",
                "approvals": "available_in_memory",
                "persistence": "available",
                "session_memory": "available",
                "project_context": "available",
                "long_term_memory": "available_with_policy_gate",
                "repository_intelligence": "available",
                "development_workflows": "available",
                "test_orchestrator": "available",
                "pr_documents": "available_with_review_gate",
                "frontend": "available",
                "web_hosting": "available_when_built",
                "desktop_shell": "deferred_optional",
                "project_registration": "available_with_path_validation",
                "runtime_logs": "available",
                "authentication": "available_with_fixed_b7_accounts_and_signed_jwt",
                "team_rbac": "available_with_resource_authorization",
                "session_sharing": "available_with_explicit_permission",
                "remote_agent_host": "available_with_persistent_pairing",
                "release_readiness": "available",
            },
        }

    @app.get("/api/v1/runtime/logs", tags=["system"])
    def runtime_logs(limit: int = Query(default=100, ge=1, le=200)) -> list[dict[str, object]]:
        """Return recent local service events for the Web diagnostics panel."""
        logs = list(app.state.runtime_logs[-limit:])
        if resolved_auth_settings.environment == "development":
            return logs
        return [{key: value for key, value in item.items() if key != "data"} for item in logs]

    @app.post("/api/v1/auth/login", tags=["auth"])
    def login(payload: LoginRequest, request: Request) -> dict[str, str]:
        client_key = request.client.host if request.client is not None else "unknown"
        retry_after = app.state.login_rate_limiter.retry_after(client_key)
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="too many failed login attempts",
                headers={"Retry-After": str(retry_after)},
            )
        user = authentication.authenticate_password(payload.username, payload.password)
        if user is None:
            app.state.login_rate_limiter.record_failure(client_key)
            raise HTTPException(status_code=401, detail="invalid username or password")
        app.state.login_rate_limiter.clear(client_key)
        token = authentication.issue_access_token(user.user_id)
        return {"access_token": token, "token_type": "bearer", "user_id": user.user_id}

    @app.get("/api/v1/settings", tags=["settings"])
    def get_runtime_settings(actor_id: str = Depends(_authenticated_actor)) -> dict[str, object]:
        _require_local_administrator(actor_id)
        return _public_runtime_settings(app.state.local_settings)

    @app.put("/api/v1/settings", tags=["settings"])
    def update_runtime_settings(
        payload: RuntimeSettingsRequest, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        _require_local_administrator(actor_id)
        current: LocalSettings = app.state.local_settings
        updated = LocalSettings(
            idle_shutdown_minutes=payload.idle_shutdown_minutes,
            model_provider=payload.model_provider.strip().lower(),
            model_name=payload.model_name.strip(),
            users=current.users,
        )
        try:
            _model_gateway(updated)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        app.state.settings_store.save(updated)
        app.state.local_settings = updated
        app.state.agent_runtime = AgentRuntime(_model_gateway(updated))
        app.state.last_user_activity = time.monotonic()
        return _public_runtime_settings(updated)

    @app.post("/api/v1/settings/users", status_code=status.HTTP_201_CREATED, tags=["settings"])
    def add_local_user(
        payload: LocalUserCreateRequest, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        _require_local_administrator(actor_id)
        current: LocalSettings = app.state.local_settings
        reserved_ids = {"admin", "admin1", "admin2", "admin3"}
        if payload.id in reserved_ids or any(user.user_id == payload.id for user in current.users):
            raise HTTPException(status_code=409, detail="user id is already configured or reserved")
        updated = LocalSettings(
            idle_shutdown_minutes=current.idle_shutdown_minutes,
            model_provider=current.model_provider,
            model_name=current.model_name,
            users=(
                *current.users,
                LocalUser(payload.id, payload.display_name, hash_password(payload.password)),
            ),
        )
        app.state.settings_store.save(updated)
        app.state.local_settings = updated
        authentication.replace_users((*_fixed_auth_users(), *_configured_users(updated)))
        TeamRepository(_database(app)).create_user(
            UserRecord(id=payload.id, display_name=payload.display_name)
        )
        app.state.last_user_activity = time.monotonic()
        return {"id": payload.id, "display_name": payload.display_name}

    @app.get("/api/v1/progress", tags=["project"])
    def progress() -> dict[str, object]:
        agent = HandoverAgent.from_workspace()
        return agent.progress.to_public_dict()

    @app.get("/api/v1/tools", tags=["tools"])
    def tools() -> list[dict[str, object]]:
        """Expose registered tool schemas without exposing execution handles."""
        runtime = ToolRuntime(project_root())
        return [definition.model_dump(mode="json") for definition in runtime.registry.definitions()]

    @app.post("/api/v1/projects", status_code=status.HTTP_201_CREATED, tags=["project"])
    def create_project(
        payload: ProjectCreateRequest, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        db = _database(app)
        repository = ProjectRepository(db)
        try:
            root_path = _normalize_project_root(payload.root_path, base_dir=workspace)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        existing = repository.get_by_root(str(root_path))
        if existing is not None:
            _require_project_access(app, existing.id, actor_id, write=False)
            return existing.model_dump(mode="json")
        project = repository.create(ProjectRecord(name=payload.name, root_path=str(root_path)))
        TeamRepository(db).set_project_member(
            ProjectMember(project_id=project.id, user_id=actor_id, role=TeamRole.OWNER)
        )
        _record_runtime_log(
            app,
            event="project.registered",
            message="Project directory registered",
            data={"project_id": project.id, "root_path": project.root_path},
        )
        return project.model_dump(mode="json")

    @app.get("/api/v1/projects", tags=["project"])
    def list_projects(actor_id: str = Depends(_authenticated_actor)) -> list[dict[str, object]]:
        database = _database(app)
        allowed_ids = TeamRepository(database).list_project_ids_for_user(actor_id)
        projects = [
            project for project in ProjectRepository(database).list() if project.id in allowed_ids
        ]
        return [project.model_dump(mode="json") for project in projects]

    @app.get("/api/v1/project-directories", tags=["project"])
    def list_project_directories(
        path: str | None = Query(default=None), actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        """List selectable directories without exposing paths outside the workspace."""
        del actor_id
        try:
            return _workspace_directory_listing(path, workspace)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/users", status_code=status.HTTP_201_CREATED, tags=["teams"])
    def create_user(
        payload: UserCreateRequest, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        if not resolved_auth_settings.allow_user_provisioning:
            raise HTTPException(status_code=403, detail="user provisioning is disabled")
        user = UserRecord(id=payload.id or str(uuid4()), display_name=payload.display_name)
        return TeamRepository(_database(app)).create_user(user).model_dump(mode="json")

    @app.post("/api/v1/teams", status_code=status.HTTP_201_CREATED, tags=["teams"])
    def create_team(
        payload: TeamCreateRequest, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        try:
            team = TeamRepository(_database(app)).create_team(
                TeamRecord(name=payload.name), actor_id
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return team.model_dump(mode="json")

    @app.put("/api/v1/teams/{team_id}/members", tags=["teams"])
    def set_team_member(
        team_id: str, payload: TeamMemberRequest, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        repository = TeamRepository(_database(app))
        try:
            TeamService(repository).require_team_admin(team_id, actor_id)
            member = repository.set_team_member(
                TeamMember(team_id=team_id, user_id=payload.user_id, role=payload.role)
            )
        except AccessDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return member.model_dump(mode="json")

    @app.put("/api/v1/projects/{project_id}/members", tags=["teams"])
    def set_project_member(
        project_id: str,
        payload: ProjectMemberRequest,
        actor_id: str = Depends(_authenticated_actor),
    ) -> dict[str, object]:
        repository = TeamRepository(_database(app))
        if ProjectRepository(_database(app)).get(project_id) is None:
            raise HTTPException(status_code=404, detail=f"project not found: {project_id}")
        try:
            TeamService(repository).require_project_write(project_id, actor_id)
            member = repository.set_project_member(
                ProjectMember(project_id=project_id, user_id=payload.user_id, role=payload.role)
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return member.model_dump(mode="json")

    @app.post("/api/v1/sessions", status_code=status.HTTP_201_CREATED, tags=["sessions"])
    def create_session(
        payload: SessionCreateRequest, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        if payload.project_id is not None:
            _require_project_access(app, payload.project_id, actor_id, write=False)
        session = SessionRepository(_database(app)).create(
            SessionRecord(
                thread_id=payload.thread_id,
                user_id=actor_id,
                project_id=payload.project_id,
                title=payload.title,
            )
        )
        return session.model_dump(mode="json")

    @app.get("/api/v1/sessions", tags=["sessions"])
    def list_sessions(
        project_id: str | None = Query(default=None),
        user_id: str | None = Query(default=None),
        actor_id: str = Depends(_authenticated_actor),
    ) -> list[dict[str, object]]:
        if user_id is not None and user_id != actor_id:
            raise HTTPException(status_code=403, detail="cannot list another user's sessions")
        if project_id is not None:
            _require_project_access(app, project_id, actor_id, write=False)
        database = _database(app)
        shared_ids = TeamRepository(database).list_shared_session_ids(actor_id)
        sessions = [
            session
            for session in SessionRepository(database).list(project_id=project_id)
            if session.user_id == actor_id or session.id in shared_ids
        ]
        return [session.model_dump(mode="json") for session in sessions]

    @app.get("/api/v1/sessions/{session_id}", tags=["sessions"])
    def get_session(
        session_id: str, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        repository = SessionRepository(_database(app))
        try:
            snapshot = repository.snapshot(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _require_session_access(app, snapshot.session, actor_id, SessionPermission.VIEW)
        return snapshot.model_dump(mode="json")

    @app.put("/api/v1/sessions/{session_id}/shares", tags=["teams"])
    def share_session(
        session_id: str, payload: SessionShareRequest, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        session = SessionRepository(_database(app)).get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        if session.user_id != actor_id:
            raise HTTPException(status_code=403, detail="only the session owner can share it")
        try:
            share = TeamRepository(_database(app)).set_session_share(
                SessionShare(
                    session_id=session_id,
                    recipient_id=payload.recipient_id,
                    permission=payload.permission,
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return share.model_dump(mode="json")

    @app.post(
        "/api/v1/teams/{team_id}/remote-hosts",
        status_code=status.HTTP_201_CREATED,
        tags=["teams"],
    )
    def declare_remote_host(
        team_id: str, payload: RemoteHostRequest, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        repository = TeamRepository(_database(app))
        try:
            TeamService(repository).require_team_admin(team_id, actor_id)
        except AccessDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        host = repository.create_remote_host(
            RemoteHost(team_id=team_id, name=payload.name, capabilities=payload.capabilities)
        )
        pairing_code = secrets.token_urlsafe(32)
        pairing_expires_at = datetime.now(UTC) + timedelta(minutes=10)
        repository.save_remote_host_pairing(
            host_id=host.id,
            code_hash=_secret_digest(pairing_code),
            expires_at=pairing_expires_at,
        )
        return {
            **host.model_dump(mode="json"),
            "pairing_code": pairing_code,
            "pairing_expires_at": pairing_expires_at.isoformat(),
        }

    @app.post("/api/v1/remote-hosts/{host_id}/pair", tags=["remote-host"])
    def pair_remote_host(host_id: str, payload: RemoteHostPairRequest) -> dict[str, object]:
        repository = TeamRepository(_database(app))
        if not repository.consume_remote_host_pairing(
            host_id=host_id,
            code_hash=_secret_digest(payload.pairing_code),
            now=datetime.now(UTC),
        ):
            raise HTTPException(status_code=401, detail="invalid or expired pairing code")
        try:
            host = repository.set_remote_host_status(host_id, "paired")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        host_token = authentication.issue_host_token(host_id)
        return {**host.model_dump(mode="json"), "host_token": host_token}

    @app.post("/api/v1/remote-hosts/{host_id}/heartbeat", tags=["remote-host"])
    def remote_host_heartbeat(
        host_id: str,
        x_devpilot_host_token: str | None = Header(default=None),
    ) -> dict[str, str]:
        if not authentication.authenticate_host_token(x_devpilot_host_token, host_id):
            raise HTTPException(status_code=401, detail="invalid host token")
        return {"status": "accepted", "host_id": host_id}

    @app.post("/api/v1/sessions/{session_id}/messages", tags=["sessions"])
    def append_session_message(
        session_id: str,
        message: ChatMessage,
        actor_id: str = Depends(_authenticated_actor),
    ) -> dict[str, object]:
        session = SessionRepository(_database(app)).get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        _require_session_access(app, session, actor_id, SessionPermission.COLLABORATE)
        try:
            stored = SessionRepository(_database(app)).append_message(session_id, message)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return stored.model_dump(mode="json")

    @app.post("/api/v1/sessions/{session_id}/attachments", status_code=status.HTTP_201_CREATED)
    def create_attachment(
        session_id: str,
        payload: AttachmentCreateRequest,
        actor_id: str = Depends(_authenticated_actor),
    ) -> dict[str, str]:
        session = SessionRepository(_database(app)).get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        _require_session_access(app, session, actor_id, SessionPermission.COLLABORATE)
        filename = Path(payload.filename).name
        if filename != payload.filename or not filename:
            raise HTTPException(
                status_code=422,
                detail="attachment filename must not include a path",
            )
        encoded_limit = (resolved_auth_settings.max_attachment_bytes * 4 // 3) + 8
        if len(payload.content_base64) > encoded_limit:
            raise HTTPException(status_code=413, detail="attachment exceeds configured size limit")
        try:
            content = base64.b64decode(payload.content_base64, validate=True)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="attachment content must be base64"
            ) from exc
        if len(content) > resolved_auth_settings.max_attachment_bytes:
            raise HTTPException(status_code=413, detail="attachment exceeds configured size limit")
        attachment_id = str(uuid4())
        directory = workspace / ".devpilot" / "attachments" / session_id
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / attachment_id
        target.write_bytes(content)
        return {
            "id": attachment_id,
            "filename": filename,
            "mime_type": payload.mime_type,
            "size": str(len(content)),
        }

    @app.post("/api/v1/sessions/{session_id}/runs", status_code=status.HTTP_201_CREATED)
    async def create_run(
        session_id: str,
        payload: RunCreateRequest,
        actor_id: str = Depends(_authenticated_actor),
    ) -> dict[str, object]:
        session = SessionRepository(_database(app)).get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        _require_session_access(app, session, actor_id, SessionPermission.COLLABORATE)
        SessionRepository(_database(app)).append_message(session_id, payload.message)
        configured: LocalSettings = app.state.local_settings
        request = RunRequest(
            thread_id=session.thread_id,
            run_id=payload.run_id or str(uuid4()),
            provider=payload.provider or configured.model_provider,
            model=payload.model or configured.model_name,
            messages=[payload.message],
        )
        app.state.active_run_count += 1
        try:
            result = await _runtime(app).run(request)
        finally:
            app.state.active_run_count -= 1
        if result.final_text:
            SessionRepository(_database(app)).append_message(
                session_id, ChatMessage.from_text("assistant", result.final_text)
            )
        _record_runtime_log(
            app,
            event="session.run.completed",
            message="Session run completed",
            data={"session_id": session_id, "run_id": request.run_id, "status": str(result.status)},
        )
        return result.model_dump(mode="json")

    @app.websocket("/api/v1/sessions/{session_id}/events")
    async def session_events(websocket: WebSocket, session_id: str) -> None:
        token = websocket.query_params.get("access_token")
        actor_id = _actor_for_token(token)
        if actor_id is None:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        session = SessionRepository(_database(app)).get(session_id)
        if session is None:
            await websocket.send_json({"type": "error", "detail": "session not found"})
            await websocket.close(code=4404)
            return
        try:
            _require_session_access(app, session, actor_id, SessionPermission.COLLABORATE)
        except HTTPException:
            await websocket.close(code=4403)
            return
        try:
            while True:
                payload = RunCreateRequest.model_validate(await websocket.receive_json())
                app.state.last_user_activity = time.monotonic()
                SessionRepository(_database(app)).append_message(session_id, payload.message)
                configured: LocalSettings = app.state.local_settings
                request = RunRequest(
                    thread_id=session.thread_id,
                    run_id=payload.run_id or str(uuid4()),
                    provider=payload.provider or configured.model_provider,
                    model=payload.model or configured.model_name,
                    messages=[payload.message],
                )
                final_text = None
                app.state.active_run_count += 1
                try:
                    async for event in _runtime(app).stream(request):
                        await websocket.send_json(event.model_dump(mode="json"))
                        if event.type.value == "run.completed":
                            final_text = event.data.get("text")
                finally:
                    app.state.active_run_count -= 1
                if final_text:
                    SessionRepository(_database(app)).append_message(
                        session_id, ChatMessage.from_text("assistant", str(final_text))
                    )
                    _record_runtime_log(
                        app,
                        event="session.run.completed",
                        message="WebSocket session run completed",
                        data={
                            "session_id": session_id,
                            "run_id": request.run_id,
                            "transport": "websocket",
                        },
                    )
        except WebSocketDisconnect:
            return
        except ValueError as exc:
            await websocket.send_json({"type": "error", "detail": str(exc)})

    @app.post("/api/v1/sessions/{session_id}/summarize", tags=["sessions"])
    def summarize_session(
        session_id: str,
        max_characters: int = Query(default=4_000, ge=100, le=100_000),
        actor_id: str = Depends(_authenticated_actor),
    ) -> dict[str, object]:
        from packages.memory import SessionMemoryService

        repository = SessionRepository(_database(app))
        session = repository.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        _require_session_access(app, session, actor_id, SessionPermission.COLLABORATE)
        summary = SessionMemoryService(repository).summarize(
            session.thread_id,
            max_characters=max_characters,
        )
        return summary.model_dump(mode="json")

    @app.get("/api/v1/projects/{project_id}/rules", tags=["project"])
    def list_project_rules(
        project_id: str, actor_id: str = Depends(_authenticated_actor)
    ) -> list[dict[str, object]]:
        _require_project_access(app, project_id, actor_id, write=False)
        rules = RuleRepository(_database(app)).list(project_id)
        return [rule.model_dump(mode="json") for rule in rules]

    @app.post("/api/v1/projects/{project_id}/rules/discover", tags=["project"])
    def discover_project_rules(
        project_id: str,
        payload: RuleDiscoveryRequest | None = None,
        actor_id: str = Depends(_authenticated_actor),
    ) -> dict[str, object]:
        project = _require_project_access(app, project_id, actor_id, write=True)
        current_dir = Path(payload.current_dir) if payload and payload.current_dir else None
        context = ProjectContextService().discover_and_store(
            project_id=project_id,
            project_root=Path(project.root_path),
            current_dir=current_dir,
            repository=RuleRepository(_database(app)),
        )
        return {**context.model_dump(mode="json"), "merged_text": context.merged_text}

    @app.post("/api/v1/projects/{project_id}/repository/scan", tags=["workflows"])
    def scan_repository(
        project_id: str, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        database = _database(app)
        project = _require_project_access(app, project_id, actor_id, write=True)
        try:
            profile = RepositoryScanner(Path(project.root_path)).scan(project_id=project_id)
        except (NotADirectoryError, OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        RepositoryProfileRepository(database).save(profile)
        return profile.model_dump(mode="json")

    @app.get("/api/v1/projects/{project_id}/repository-profile", tags=["workflows"])
    def get_repository_profile(
        project_id: str, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        _require_project_access(app, project_id, actor_id, write=False)
        profile = RepositoryProfileRepository(_database(app)).get(project_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="repository profile not found")
        return profile.model_dump(mode="json")

    @app.post("/api/v1/workflows", status_code=status.HTTP_201_CREATED, tags=["workflows"])
    def create_workflow(
        payload: WorkflowCreateRequest, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        database = _database(app)
        project = _require_project_access(app, payload.project_id, actor_id, write=True)
        workflow = DevelopmentWorkflowService(
            project_id=project.id,
            project_root=Path(project.root_path),
            profile_store=RepositoryProfileRepository(database),
            workflow_store=WorkflowRepository(database),
        ).run(
            IssueContext(
                description=payload.description,
                logs=payload.logs,
                failing_tests=payload.failing_tests,
                attachments=payload.attachments,
            ),
            execute_tests=payload.execute_tests,
            create_worktree=payload.create_worktree,
            full_tests=payload.full_tests,
        )
        return workflow.model_dump(mode="json")

    @app.get("/api/v1/workflows", tags=["workflows"])
    def list_workflows(
        project_id: str | None = Query(default=None),
        actor_id: str = Depends(_authenticated_actor),
    ) -> list[dict[str, object]]:
        if project_id is not None:
            _require_project_access(app, project_id, actor_id, write=False)
        allowed_projects = TeamRepository(_database(app)).list_project_ids_for_user(actor_id)
        workflows = [
            workflow
            for workflow in WorkflowRepository(_database(app)).list(project_id=project_id)
            if workflow.project_id in allowed_projects
        ]
        return [workflow.model_dump(mode="json") for workflow in workflows]

    @app.get("/api/v1/workflows/{workflow_id}", tags=["workflows"])
    def get_workflow(
        workflow_id: str, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        workflow = WorkflowRepository(_database(app)).get(workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail=f"workflow not found: {workflow_id}")
        _require_project_access(app, workflow.project_id, actor_id, write=False)
        return workflow.model_dump(mode="json")

    @app.get("/api/v1/workflows/{workflow_id}/agent-tree", tags=["workflows"])
    def get_agent_tree(
        workflow_id: str, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        workflow = WorkflowRepository(_database(app)).get(workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail=f"workflow not found: {workflow_id}")
        _require_project_access(app, workflow.project_id, actor_id, write=False)
        return {
            "workflow_id": workflow.id,
            "supervisor_run_id": workflow.supervisor_run_id,
            "agent_runs": [run.model_dump(mode="json") for run in workflow.agent_runs],
        }

    @app.get("/api/v1/workflows/{workflow_id}/pr", tags=["workflows"])
    def get_workflow_pr(
        workflow_id: str, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        workflow = WorkflowRepository(_database(app)).get(workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail=f"workflow not found: {workflow_id}")
        _require_project_access(app, workflow.project_id, actor_id, write=False)
        if workflow.pr_document is None:
            raise HTTPException(status_code=404, detail="PR document not generated")
        return workflow.pr_document.model_dump(mode="json")

    @app.patch("/api/v1/workflows/{workflow_id}/pr/review", tags=["workflows"])
    def review_workflow_pr(
        workflow_id: str,
        payload: ReviewDecisionRequest,
        actor_id: str = Depends(_authenticated_actor),
    ) -> dict[str, object]:
        repository = WorkflowRepository(_database(app))
        workflow = repository.get(workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail=f"workflow not found: {workflow_id}")
        _require_project_access(app, workflow.project_id, actor_id, write=True)
        if workflow.pr_document is None:
            raise HTTPException(status_code=404, detail="PR document not generated")
        updated = workflow.model_copy(
            update={
                "pr_document": workflow.pr_document.model_copy(
                    update={"review_status": payload.status}
                )
            }
        )
        repository.save(updated)
        return updated.pr_document.model_dump(mode="json")

    @app.get("/api/v1/memory", tags=["memory"])
    def list_memory(
        owner_id: str | None = Query(default=None),
        scope: MemoryScope | None = Query(default=None),
        include_disabled: bool = Query(default=True),
        actor_id: str = Depends(_authenticated_actor),
    ) -> list[dict[str, object]]:
        if scope == MemoryScope.PROJECT:
            if owner_id is None:
                raise HTTPException(status_code=422, detail="project memory requires owner_id")
            _require_project_access(app, owner_id, actor_id, write=False)
        else:
            if owner_id is not None and owner_id != actor_id:
                raise HTTPException(status_code=403, detail="cannot list another user's memory")
            owner_id = actor_id
            scope = MemoryScope.USER
        entries = MemoryRepository(_database(app)).list(
            owner_id=owner_id,
            scope=scope,
            include_disabled=include_disabled,
        )
        return [entry.model_dump(mode="json") for entry in entries]

    @app.post("/api/v1/memory", status_code=status.HTTP_201_CREATED, tags=["memory"])
    def add_memory(
        payload: MemoryCreateRequest, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        if payload.scope == MemoryScope.PROJECT:
            if payload.owner_id is None:
                raise HTTPException(status_code=422, detail="project memory requires owner_id")
            _require_project_access(app, payload.owner_id, actor_id, write=True)
            owner_id = payload.owner_id
        else:
            if payload.owner_id is not None and payload.owner_id != actor_id:
                raise HTTPException(status_code=403, detail="cannot write another user's memory")
            owner_id = actor_id
        store = _memory_store(app, payload.scope, owner_id)
        result = store.write_candidate(
            key=payload.key,
            content=payload.content,
            source=payload.source,
        )
        if result.entry is None:
            raise HTTPException(status_code=422, detail=result.reason or "memory write blocked")
        return result.entry.model_dump(mode="json")

    @app.patch("/api/v1/memory/{memory_id}", tags=["memory"])
    def update_memory(
        memory_id: str,
        payload: MemoryUpdateRequest,
        actor_id: str = Depends(_authenticated_actor),
    ) -> dict[str, object]:
        repository = MemoryRepository(_database(app))
        entry = repository.get(memory_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"memory entry not found: {memory_id}")
        _require_memory_access(app, entry.scope, entry.owner_id, actor_id)
        store = _memory_store(app, entry.scope, entry.owner_id)
        try:
            if payload.content is not None or payload.key is not None:
                entry = store.edit(
                    memory_id,
                    content=payload.content or entry.content,
                    key=payload.key,
                )
            if payload.enabled is not None:
                entry = store.set_enabled(memory_id, payload.enabled)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return entry.model_dump(mode="json")

    @app.delete(
        "/api/v1/memory/{memory_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["memory"],
    )
    def delete_memory(
        memory_id: str, actor_id: str = Depends(_authenticated_actor)
    ) -> Response:
        repository = MemoryRepository(_database(app))
        entry = repository.get(memory_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"memory entry not found: {memory_id}")
        _require_memory_access(app, entry.scope, entry.owner_id, actor_id)
        try:
            _memory_store(app, entry.scope, entry.owner_id).delete(memory_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    web_dist = (web_dist_path or project_root() / "apps" / "web" / "dist").resolve()
    assets_dir = web_dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="web-assets")

    if (web_dist / "index.html").is_file():

        @app.get("/", include_in_schema=False)
        def web_index() -> FileResponse:
            return FileResponse(web_dist / "index.html")

        @app.get("/{web_path:path}", include_in_schema=False)
        def web_fallback(web_path: str) -> FileResponse:
            if web_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API route not found")
            target = (web_dist / web_path).resolve()
            if target.is_relative_to(web_dist.resolve()) and target.is_file():
                return FileResponse(target)
            return FileResponse(web_dist / "index.html")

    return app


def project_root() -> Path:
    """Resolve the repository root without coupling the domain package to FastAPI."""
    return Path(__file__).resolve().parents[2]


def _normalize_project_root(raw_path: str, *, base_dir: Path) -> Path:
    """Resolve an explicitly registered project and require an existing directory."""
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"project root is not accessible: {raw_path}") from exc
    if not resolved.is_dir():
        raise ValueError(f"project root must be an existing directory: {resolved}")
    return resolved


def _workspace_directory_listing(raw_path: str | None, workspace: Path) -> dict[str, object]:
    """Return direct child directories only when the requested path stays in the workspace."""
    root = workspace.resolve()
    candidate = root if raw_path is None else Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("selected directory is not accessible") from exc
    if not resolved.is_dir():
        raise ValueError("selected path must be a directory")
    if not resolved.is_relative_to(root):
        raise ValueError("selected directory must be inside the workspace")

    directories: list[dict[str, str]] = []
    try:
        children = sorted(resolved.iterdir(), key=lambda child: child.name.casefold())
    except OSError as exc:
        raise ValueError("selected directory cannot be listed") from exc
    for child in children:
        if child.name.startswith("."):
            continue
        try:
            child_path = child.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if child_path.is_dir() and child_path.is_relative_to(root):
            directories.append({"name": child.name, "path": str(child_path)})
        if len(directories) == 100:
            break

    parent = resolved.parent if resolved.parent.is_relative_to(root) else None
    return {
        "path": str(resolved),
        "parent_path": str(parent) if parent is not None and parent != resolved else None,
        "directories": directories,
    }


def _require_project_access(
    app: FastAPI, project_id: str, actor_id: str, *, write: bool
) -> ProjectRecord:
    """Resolve a project only after the actor holds the required project role."""
    database = _database(app)
    project = ProjectRepository(database).get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project not found: {project_id}")
    service = TeamService(TeamRepository(database))
    try:
        if write:
            service.require_project_write(project_id, actor_id)
        else:
            service.require_project_read(project_id, actor_id)
    except AccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return project


def _require_memory_access(
    app: FastAPI, scope: MemoryScope, owner_id: str, actor_id: str
) -> None:
    """Enforce ownership for user memory and membership for project memory."""
    if scope == MemoryScope.PROJECT:
        _require_project_access(app, owner_id, actor_id, write=True)
        return
    if owner_id != actor_id:
        raise HTTPException(status_code=403, detail="memory ownership is required")


def _secret_digest(value: str) -> str:
    """Return a database-safe digest for short-lived one-time pairing material."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _apply_security_headers(response: Response) -> Response:
    """Keep bearer credentials and Web assets isolated from common browser attacks."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
        "form-action 'self'; object-src 'none'; connect-src 'self' ws: wss:"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def _record_runtime_log(
    app: FastAPI,
    *,
    event: str,
    message: str,
    data: dict[str, object] | None = None,
) -> None:
    logs: list[dict[str, object]] = app.state.runtime_logs
    logs.append(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": "info",
            "event": event,
            "message": message,
            "data": data or {},
        }
    )
    del logs[:-200]


def _database(app: FastAPI) -> Database:
    database: Database = app.state.database
    return database


def _configured_users(settings: LocalSettings) -> tuple[AuthenticatedUser, ...]:
    return tuple(
        AuthenticatedUser(
            user_id=user.user_id, display_name=user.display_name, password_hash=user.password_hash
        )
        for user in settings.users
    )


def _fixed_auth_users() -> tuple[AuthenticatedUser, ...]:
    """Keep B7's required fixed accounts alongside user-managed local accounts."""
    return AuthSettings.development().users


def _model_gateway(settings: LocalSettings) -> ModelGateway:
    provider = settings.model_provider
    if provider == "fake":
        adapter = FakeModel(model=settings.model_name)
    elif provider == "openai":
        adapter = OpenAIAdapter(model=settings.model_name)
    elif provider == "anthropic":
        adapter = AnthropicAdapter(model=settings.model_name)
    elif provider == "ollama":
        # TODO（后续模型批次）：Ollama 仅保留显式未实现适配器，调用时返回明确状态。
        adapter = OllamaAdapter(model=settings.model_name)
    else:
        raise ValueError("model provider must be one of: fake, openai, anthropic, ollama")
    return ModelGateway([adapter])


def _public_runtime_settings(settings: LocalSettings) -> dict[str, object]:
    return {
        "idle_shutdown_minutes": settings.idle_shutdown_minutes,
        "model_provider": settings.model_provider,
        "model_name": settings.model_name,
        "users": [
            {"id": user.user_id, "display_name": user.display_name} for user in settings.users
        ],
    }


def _require_local_administrator(actor_id: str) -> None:
    if actor_id != "admin":
        raise HTTPException(status_code=403, detail="the fixed admin account is required")


async def _idle_shutdown_monitor(app: FastAPI) -> None:
    """Stop the local process after user inactivity once no model output is in flight."""
    while True:
        await asyncio.sleep(1)
        settings: LocalSettings = app.state.local_settings
        idle_seconds = settings.idle_shutdown_minutes * 60
        idle_elapsed = time.monotonic() - app.state.last_user_activity
        if app.state.active_run_count or idle_elapsed < idle_seconds:
            continue
        _record_runtime_log(
            app,
            event="service.idle_shutdown",
            message="No user activity or active model output; stopping local process",
            data={"idle_shutdown_minutes": settings.idle_shutdown_minutes},
        )
        callback = getattr(app.state, "shutdown_callback", None)
        if callback is not None:
            callback()
        else:
            os.kill(os.getpid(), signal.SIGTERM)
        return


def _runtime(app: FastAPI) -> AgentRuntime:
    return app.state.agent_runtime


def _memory_store(app: FastAPI, scope: MemoryScope, owner_id: str) -> LongTermMemoryStore:
    database = _database(app)
    if scope == MemoryScope.PROJECT:
        project = ProjectRepository(database).get(owner_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"project not found: {owner_id}")
        path = Path(project.root_path) / ".devpilot" / "MEMORY.md"
    else:
        path = app.state.workspace_root / ".devpilot" / "users" / owner_id / "MEMORY.md"
    return LongTermMemoryStore(
        path,
        owner_id=owner_id,
        scope=scope,
        repository=MemoryRepository(database),
    )


def _require_session_access(
    app: FastAPI,
    session: SessionRecord,
    actor_id: str,
    permission: SessionPermission,
) -> None:
    if session.user_id == actor_id:
        return
    try:
        TeamService(TeamRepository(_database(app))).require_session_permission(
            session.id, actor_id, permission
        )
    except AccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


app = create_app()
