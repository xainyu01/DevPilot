"""FastAPI entry point for the B4 persistence and development workflow boundary."""

import asyncio
import base64
import hashlib
import os
import secrets
import signal
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

import aiosqlite
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
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from packages.agent_core import (
    AgentRuntime,
    ContextBudgetError,
    PreparedRun,
    RunCoordinator,
)
from packages.contracts import (
    ApprovalRequest,
    ApprovalScope,
    ChatMessage,
    IssueContext,
    MemoryScope,
    ModelProfile,
    ProjectMember,
    ProjectRecord,
    RemoteHost,
    ReviewStatus,
    RunRequest,
    RunStatus,
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
from packages.local_settings import (
    AgentModelPolicy,
    LocalSettings,
    LocalSettingsError,
    LocalSettingsStore,
    LocalUser,
    ModelEndpoint,
    ModelTarget,
)
from packages.memory import LongTermMemoryStore
from packages.model_gateway import (
    AnthropicAdapter,
    FakeModel,
    ModelChoiceError,
    ModelChoiceService,
    ModelGateway,
    ModelRouter,
    OllamaAdapter,
    OpenAIAdapter,
)
from packages.persistence import (
    ApprovalRepository,
    AuditRepository,
    CheckpointRepository,
    Database,
    MemoryRepository,
    PersistentApprovalStore,
    ProjectRepository,
    RepositoryProfileRepository,
    RuleRepository,
    RunRepository,
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
    model_mode: Literal["manual", "auto"] | None = None
    endpoint_id: str | None = None
    provider: str | None = None
    model: str | None = None
    allowed_models: list["ModelTargetRequest"] = Field(default_factory=list)
    run_id: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=50)
    context_max_tokens: int = Field(default=64_000, ge=1_000, le=128_000)
    max_tokens: int = Field(default=200_000, ge=1, le=200_000)
    background: bool = False


class RunResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: dict[str, object] = Field(default_factory=lambda: {"action": "resume"})


class RunApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    scope: ApprovalScope = ApprovalScope.ONCE
    command_pattern: str | None = None


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


class ModelTargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint_id: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)


class ModelEndpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    provider: Literal["fake", "openai", "anthropic", "coding_plan", "ollama"]
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, max_length=8192)
    clear_api_key: bool = False
    models: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True
    tool_capability: Literal["supported", "unsupported", "unknown"] = "unknown"


class AgentModelPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["manual", "auto"]
    allowed_models: list[ModelTargetRequest] = Field(min_length=1)


class RuntimeSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idle_shutdown_minutes: int = Field(ge=1, le=1_440)
    model_endpoints: list[ModelEndpointRequest] = Field(min_length=1, max_length=50)
    default_model: ModelTargetRequest
    agent_model_policy: AgentModelPolicyRequest


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
    app.state.agent_runtimes: dict[tuple[str, str], AgentRuntime] = {}
    app.state.run_coordinator = RunCoordinator()
    app.state.graph_checkpointer = None
    app.state.graph_checkpoint_connection = None
    app.state.background_runs: dict[str, asyncio.Task[object]] = {}
    app.state.runtime_logs: list[dict[str, object]] = []
    app.state.authentication = authentication
    app.state.auth_settings = resolved_auth_settings
    app.state.login_rate_limiter = LoginRateLimiter()
    app.state.settings_store = settings_store
    app.state.local_settings = local_settings
    app.state.active_run_count = 0
    app.state.last_user_activity = time.monotonic()
    database.create_all()
    database.ensure_real_agent_columns()
    RunRepository(database).mark_interrupted()
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
        checkpoint_dir = workspace / ".devpilot"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(checkpoint_dir / "agent-graph.sqlite")
        app.state.graph_checkpoint_connection = connection
        app.state.graph_checkpointer = AsyncSqliteSaver(connection)
        app.state.idle_shutdown_task = asyncio.create_task(_idle_shutdown_monitor(app))

    @app.on_event("shutdown")
    async def stop_idle_shutdown_monitor() -> None:
        task = getattr(app.state, "idle_shutdown_task", None)
        if task is not None:
            task.cancel()
        background = list(getattr(app.state, "background_runs", {}).values())
        for run_task in background:
            if not run_task.done():
                run_task.cancel()
        if background:
            await asyncio.gather(*background, return_exceptions=True)
        connection = getattr(app.state, "graph_checkpoint_connection", None)
        if connection is not None:
            await connection.close()

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

    @app.get("/api/v1/model-options", tags=["settings"])
    def get_model_options(actor_id: str = Depends(_authenticated_actor)) -> dict[str, object]:
        del actor_id
        settings: LocalSettings = app.state.local_settings
        endpoint_names = {
            endpoint.endpoint_id: endpoint.name
            for endpoint in settings.model_endpoints
            if endpoint.enabled
        }
        return {
            "models": [
                {
                    "endpoint_id": target.endpoint_id,
                    "endpoint_name": endpoint_names[target.endpoint_id],
                    "model": target.model,
                }
                for target in settings.available_targets()
            ],
            "default_model": _target_to_dict(settings.default_model),
            "agent_model_policy": {
                "mode": settings.agent_model_policy.mode,
                "allowed_models": [
                    _target_to_dict(target)
                    for target in settings.agent_model_policy.allowed_models
                ],
            },
        }

    @app.put("/api/v1/settings", tags=["settings"])
    def update_runtime_settings(
        payload: RuntimeSettingsRequest, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
        _require_local_administrator(actor_id)
        current: LocalSettings = app.state.local_settings
        try:
            updated = _settings_from_request(payload, current)
            _model_gateway(updated)
        except (LocalSettingsError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        app.state.settings_store.save(updated)
        app.state.local_settings = updated
        app.state.agent_runtime = AgentRuntime(_model_gateway(updated))
        app.state.agent_runtimes.clear()
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
            model_endpoints=current.model_endpoints,
            default_model=current.default_model,
            agent_model_policy=current.agent_model_policy,
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
        if payload.run_id:
            existing = RunRepository(_database(app)).get(payload.run_id)
            if existing is not None:
                if existing["metadata"].get("session_id") != session.id:
                    raise HTTPException(status_code=409, detail="run_id belongs to another session")
                return existing
        try:
            prepared = await _prepare_session_run(app, session, payload, actor_id)
        except (ContextBudgetError, ModelChoiceError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if payload.background:
            task = asyncio.create_task(_execute_prepared_run(app, session, prepared))
            app.state.background_runs[prepared.request.run_id] = task
            task.add_done_callback(
                lambda _: app.state.background_runs.pop(prepared.request.run_id, None)
            )
            await asyncio.sleep(0)
            record = RunRepository(_database(app)).get(prepared.request.run_id)
            return record or {
                "id": prepared.request.run_id,
                "thread_id": session.thread_id,
                "status": RunStatus.PENDING.value,
            }
        result = await _execute_prepared_run(app, session, prepared)
        return result.model_dump(mode="json")

    @app.get("/api/v1/runs/{run_id}", tags=["runs"])
    def get_run(
        run_id: str,
        actor_id: str = Depends(_authenticated_actor),
    ) -> dict[str, object]:
        return _require_run_access(app, run_id, actor_id)

    @app.get("/api/v1/runs/{run_id}/events", tags=["runs"])
    def get_run_events(
        run_id: str,
        after_sequence: int = Query(default=0, ge=0),
        actor_id: str = Depends(_authenticated_actor),
    ) -> list[dict[str, object]]:
        _require_run_access(app, run_id, actor_id)
        return [
            event.model_dump(mode="json")
            for event in RunRepository(_database(app)).list_events(
                run_id, after_sequence=after_sequence
            )
        ]

    @app.post("/api/v1/runs/{run_id}/cancel", tags=["runs"])
    async def cancel_run(
        run_id: str,
        actor_id: str = Depends(_authenticated_actor),
    ) -> dict[str, object]:
        runtime, request, _ = _recover_run_runtime(app, run_id, actor_id)
        cancelled = await runtime.cancel(request.thread_id, request.run_id)
        if not cancelled:
            raise HTTPException(status_code=409, detail="run cannot be cancelled")
        return _require_run_access(app, run_id, actor_id)

    @app.post("/api/v1/runs/{run_id}/resume", tags=["runs"])
    async def resume_run(
        run_id: str,
        payload: RunResumeRequest | None = None,
        actor_id: str = Depends(_authenticated_actor),
    ) -> dict[str, object]:
        runtime, request, _ = _recover_run_runtime(app, run_id, actor_id)
        try:
            result = await runtime.resume(
                request.thread_id,
                request.run_id,
                value=(payload.value if payload else {"action": "resume"}),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @app.post("/api/v1/runs/{run_id}/approvals/{request_id}", tags=["runs"])
    async def decide_run_approval(
        run_id: str,
        request_id: str,
        payload: RunApprovalRequest,
        actor_id: str = Depends(_authenticated_actor),
    ) -> dict[str, object]:
        runtime, request, capabilities = _recover_run_runtime(app, run_id, actor_id)
        record = _require_run_access(app, run_id, actor_id)
        pending = record.get("pending_approval")
        if not isinstance(pending, dict) or pending.get("request_id") != request_id:
            raise HTTPException(status_code=404, detail="pending approval not found")
        required = set(pending.get("required_capabilities", []))
        if not required.issubset(capabilities):
            raise HTTPException(
                status_code=403,
                detail="actor cannot approve capabilities outside current project role",
            )
        if runtime.tool_runtime is None:
            raise HTTPException(status_code=409, detail="run has no tool runtime")
        if runtime.tool_runtime.approvals.get(request_id) is None:
            runtime.tool_runtime.approvals.create(
                ApprovalRequest.model_validate(pending)
            )
        try:
            result = await runtime.approve(
                request.thread_id,
                request.run_id,
                request_id,
                approved=payload.approved,
                scope=payload.scope,
                decided_by=actor_id,
                command_pattern=payload.command_pattern,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @app.get("/api/v1/runs/{run_id}/changes", tags=["runs"])
    def get_run_changes(
        run_id: str,
        actor_id: str = Depends(_authenticated_actor),
    ) -> dict[str, object]:
        record = _require_run_access(app, run_id, actor_id)
        return {"run_id": run_id, "changes": record["changes"]}

    @app.get("/api/v1/runs/{run_id}/usage", tags=["runs"])
    def get_run_usage(
        run_id: str,
        actor_id: str = Depends(_authenticated_actor),
    ) -> dict[str, object]:
        record = _require_run_access(app, run_id, actor_id)
        return {
            "run_id": run_id,
            "provider": record["provider"],
            "model": record["model"],
            "provider_request_id": record["provider_request_id"],
            "usage": record["usage"],
        }

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
                prepared = await _prepare_session_run(app, session, payload, actor_id)
                final_text = None
                app.state.active_run_count += 1
                try:
                    async for event in prepared.runtime.stream(prepared.request):
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
                            "run_id": prepared.request.run_id,
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
            router=_workflow_model_router(app.state.local_settings),
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
    settings.validate()
    adapters = []
    for endpoint in settings.model_endpoints:
        if not endpoint.enabled:
            continue
        for model in endpoint.resolve_models():
            common = {"model": model, "provider_id": endpoint.endpoint_id}
            if endpoint.provider == "fake":
                adapter = FakeModel(**common)
            elif endpoint.provider in {"openai", "coding_plan"}:
                adapter = OpenAIAdapter(
                    **common,
                    api_key=endpoint.resolve_api_key(),
                    base_url=endpoint.resolve_base_url(),
                )
            elif endpoint.provider == "anthropic":
                adapter = AnthropicAdapter(
                    **common,
                    api_key=endpoint.resolve_api_key(),
                    base_url=endpoint.resolve_base_url(),
                )
            elif endpoint.provider == "ollama":
                # TODO（后续模型批次）：Ollama 仅保留显式未实现适配器。
                adapter = OllamaAdapter(**common)
            else:
                raise ValueError(f"unsupported model provider protocol: {endpoint.provider}")
            adapters.append(adapter)
    return ModelGateway(adapters)


def _workflow_model_router(settings: LocalSettings) -> ModelRouter:
    permitted = settings.agent_model_policy.allowed_models
    profiles = [
        ModelProfile(
            id=target.key,
            provider=target.endpoint_id,
            model=target.model,
            capabilities=["text", "workspace.read"],
            max_tokens=200_000,
            fallback_rank=0 if target == settings.default_model else index + 1,
            quality_rank=max(1, len(permitted) - index),
        )
        for index, target in enumerate(permitted)
    ]
    return ModelRouter(profiles)


def _public_runtime_settings(settings: LocalSettings) -> dict[str, object]:
    return {
        "idle_shutdown_minutes": settings.idle_shutdown_minutes,
        # Keep these two fields for older CLI/client display code.
        "model_provider": settings.model_provider,
        "model_name": settings.model_name,
        "model_endpoints": [
            {
                "id": endpoint.endpoint_id,
                "name": endpoint.name,
                "provider": endpoint.provider,
                "base_url": endpoint.base_url,
                "effective_base_url": endpoint.resolve_base_url(),
                "api_key_configured": bool(endpoint.resolve_api_key()),
                "api_key_source": endpoint.api_key_source(),
                "models": list(endpoint.models),
                "effective_models": list(endpoint.resolve_models()),
                "enabled": endpoint.enabled,
                "tool_capability": endpoint.tool_capability,
                "environment": endpoint.environment_names(),
            }
            for endpoint in settings.model_endpoints
        ],
        "default_model": _target_to_dict(settings.default_model),
        "agent_model_policy": {
            "mode": settings.agent_model_policy.mode,
            "allowed_models": [
                _target_to_dict(target)
                for target in settings.agent_model_policy.allowed_models
            ],
        },
        "users": [
            {"id": user.user_id, "display_name": user.display_name} for user in settings.users
        ],
    }


def _settings_from_request(
    payload: RuntimeSettingsRequest, current: LocalSettings
) -> LocalSettings:
    current_endpoints = {
        endpoint.endpoint_id: endpoint for endpoint in current.model_endpoints
    }
    endpoints: list[ModelEndpoint] = []
    for value in payload.model_endpoints:
        endpoint_id = value.id.strip().lower()
        existing = current_endpoints.get(endpoint_id)
        if value.clear_api_key:
            api_key = None
        elif value.api_key is not None and value.api_key.strip():
            api_key = value.api_key.strip()
        else:
            api_key = existing.api_key if existing else None
        endpoints.append(
            ModelEndpoint(
                endpoint_id=endpoint_id,
                name=value.name.strip(),
                provider=value.provider,
                base_url=(
                    value.base_url.strip()
                    if value.base_url and value.base_url.strip()
                    else None
                ),
                api_key=api_key,
                models=tuple(
                    dict.fromkeys(model.strip() for model in value.models if model.strip())
                ),
                enabled=value.enabled,
                tool_capability=value.tool_capability,
            )
        )
    updated = LocalSettings(
        idle_shutdown_minutes=payload.idle_shutdown_minutes,
        model_endpoints=tuple(endpoints),
        default_model=_target_from_request(payload.default_model),
        agent_model_policy=AgentModelPolicy(
            mode=payload.agent_model_policy.mode,
            allowed_models=tuple(
                _target_from_request(target)
                for target in payload.agent_model_policy.allowed_models
            ),
        ),
        users=current.users,
    )
    return updated.validate()


async def _select_runtime_model(app: FastAPI, payload: RunCreateRequest):
    settings: LocalSettings = app.state.local_settings
    if (
        payload.endpoint_id
        and payload.provider
        and payload.endpoint_id.strip().lower() != payload.provider.strip().lower()
    ):
        raise ModelChoiceError("endpoint_id and legacy provider must match when both are set")
    requested_endpoint = payload.endpoint_id or payload.provider
    if bool(requested_endpoint) != bool(payload.model):
        raise ModelChoiceError("manual model selection requires both endpoint_id and model")
    requested = (
        ModelTarget(requested_endpoint.strip().lower(), payload.model.strip())
        if requested_endpoint and payload.model
        else None
    )
    allowed = (
        tuple(_target_from_request(target) for target in payload.allowed_models)
        if payload.allowed_models
        else None
    )
    return await ModelChoiceService(_runtime(app).gateway, settings).choose(
        messages=[payload.message],
        mode=payload.model_mode,
        requested=requested,
        allowed=allowed,
    )


async def _prepare_session_run(
    app: FastAPI,
    session: SessionRecord,
    payload: RunCreateRequest,
    actor_id: str,
) -> PreparedRun:
    """Build one project-bound request for both HTTP and WebSocket transports."""
    selection = await _select_runtime_model(app, payload)
    repository = SessionRepository(_database(app))
    repository.append_message(session.id, payload.message)
    run_id = payload.run_id or str(uuid4())
    runtime, capabilities = _runtime_for_session(
        app,
        session,
        actor_id,
        run_id=run_id,
    )
    project = None
    rules = []
    profile = None
    workspace_diff = ""
    memories = MemoryRepository(_database(app)).list(
        owner_id=actor_id,
        scope=MemoryScope.USER,
        include_disabled=False,
    )
    if session.project_id is not None:
        project = _require_project_access(app, session.project_id, actor_id, write=False)
        root = Path(project.root_path)
        context = ProjectContextService().discover_and_store(
            project_id=project.id,
            project_root=root,
            repository=RuleRepository(_database(app)),
        )
        rules = context.rules
        profile_repository = RepositoryProfileRepository(_database(app))
        profile = RepositoryScanner(root).scan(
            project_id=project.id,
            previous=profile_repository.get(project.id),
            persist=False,
        )
        profile_repository.save(profile)
        workspace_diff = _workspace_diff(root)
        memories.extend(
            MemoryRepository(_database(app)).list(
                owner_id=project.id,
                scope=MemoryScope.PROJECT,
                include_disabled=False,
            )
        )
    tools = []
    if runtime.tool_runtime is not None:
        tools = [
            definition
            for definition in runtime.tool_runtime.registry.definitions()
            if set(definition.required_capabilities).issubset(capabilities)
        ]
    selection_metadata = {
        "mode": selection.mode,
        "reason": selection.reason,
        "fallback_used": selection.fallback_used,
        "selector": (
            {
                "endpoint_id": selection.selector.endpoint_id,
                "model": selection.selector.model,
            }
            if selection.selector
            else None
        ),
    }
    return app.state.run_coordinator.prepare(
        runtime=runtime,
        thread_id=session.thread_id,
        run_id=run_id,
        provider=selection.target.endpoint_id,
        model=selection.target.model,
        current_message=payload.message,
        history=repository.list_messages(session.id),
        metadata={
            "actor_id": actor_id,
            "session_id": session.id,
            "project_id": session.project_id,
            "capabilities": sorted(capabilities),
            "model_selection": selection_metadata,
        },
        acceptance_criteria=payload.acceptance_criteria,
        project=project,
        rules=rules,
        repository_profile=profile,
        workspace_diff=workspace_diff,
        memories=memories,
        summary=repository.get_summary(session.id),
        capabilities=capabilities,
        tools=tools,
        model_policy={
            "selected": {
                "endpoint_id": selection.target.endpoint_id,
                "model": selection.target.model,
            },
            **selection_metadata,
        },
        max_context_tokens=payload.context_max_tokens,
        max_run_tokens=payload.max_tokens,
    )


async def _execute_prepared_run(
    app: FastAPI,
    session: SessionRecord,
    prepared: PreparedRun,
):
    app.state.active_run_count += 1
    try:
        result = await prepared.runtime.run(prepared.request)
    finally:
        app.state.active_run_count -= 1
    if result.final_text:
        SessionRepository(_database(app)).append_message(
            session.id, ChatMessage.from_text("assistant", result.final_text)
        )
    changes: list[dict[str, object]] = []
    if prepared.runtime.tool_runtime is not None:
        status = prepared.runtime.tool_runtime.workspace_status()
        for kind in ("added", "modified", "deleted"):
            changes.extend({"kind": kind, "path": path} for path in status[kind])
        diff = prepared.runtime.tool_runtime.workspace_diff()
        if diff:
            changes.append({"kind": "diff", "content": diff})
    RunRepository(_database(app)).save_changes(prepared.request.run_id, changes)
    _record_runtime_log(
        app,
        event="session.run.completed",
        message="Session run completed",
        data={
            "session_id": session.id,
            "run_id": prepared.request.run_id,
            "status": str(result.status),
        },
    )
    return result


def _require_run_access(
    app: FastAPI,
    run_id: str,
    actor_id: str,
) -> dict[str, object]:
    record = RunRepository(_database(app)).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    metadata = record.get("metadata", {})
    session_id = metadata.get("session_id") if isinstance(metadata, dict) else None
    session_repository = SessionRepository(_database(app))
    session = (
        session_repository.get(str(session_id))
        if session_id
        else session_repository.get_by_thread(str(record["thread_id"]))
    )
    if session is None:
        raise HTTPException(status_code=404, detail="run session not found")
    _require_session_access(app, session, actor_id, SessionPermission.VIEW)
    if session.project_id is not None:
        _require_project_access(app, session.project_id, actor_id, write=False)
    return record


def _recover_run_runtime(
    app: FastAPI,
    run_id: str,
    actor_id: str,
) -> tuple[AgentRuntime, RunRequest, set[str]]:
    record = _require_run_access(app, run_id, actor_id)
    request_data = record.get("request")
    if not isinstance(request_data, dict) or not request_data:
        raise HTTPException(status_code=409, detail="run request is not recoverable")
    request = RunRequest.model_validate(request_data)
    session_id = request.metadata.get("session_id")
    session_repository = SessionRepository(_database(app))
    session = (
        session_repository.get(str(session_id))
        if session_id
        else session_repository.get_by_thread(request.thread_id)
    )
    if session is None:
        raise HTTPException(status_code=404, detail="run session not found")
    capabilities = _capabilities_for_session(app, session, actor_id)
    runtime = app.state.agent_runtimes.get((request.thread_id, request.run_id))
    if runtime is None:
        runtime, capabilities = _runtime_for_session(
            app,
            session,
            actor_id,
            run_id=request.run_id,
        )
        events = RunRepository(_database(app)).list_events(run_id)
        status_value = RunStatus(str(record["status"]))
        runtime.restore(
            request,
            status=(
                RunStatus.PAUSED if status_value == RunStatus.RUNNING else status_value
            ),
            event_sequence=events[-1].sequence if events else 0,
        )
    return runtime, request, capabilities


def _workspace_diff(root: Path) -> str:
    """Return bounded Git status and diff without exposing the absolute project root."""
    commands = (
        ["git", "status", "--short", "--untracked-files=all"],
        ["git", "diff", "--no-ext-diff", "--"],
    )
    sections: list[str] = []
    for command in commands:
        result = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            sections.append(result.stdout.strip())
    return "\n\n".join(sections)


def _target_from_request(value: ModelTargetRequest) -> ModelTarget:
    return ModelTarget(value.endpoint_id.strip().lower(), value.model.strip())


def _target_to_dict(target: ModelTarget) -> dict[str, str]:
    return {"endpoint_id": target.endpoint_id, "model": target.model}


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


def _runtime_for_session(
    app: FastAPI,
    session: SessionRecord,
    actor_id: str,
    *,
    run_id: str,
) -> tuple[AgentRuntime, set[str]]:
    """Bind one coding runtime to the registered project, never the DevPilot repo."""
    run_repository = RunRepository(_database(app))
    checkpoint_repository = CheckpointRepository(_database(app))
    graph_checkpointer = getattr(app.state, "graph_checkpointer", None)
    if session.project_id is None:
        runtime = AgentRuntime(
            _model_gateway(app.state.local_settings),
            checkpoint_store=checkpoint_repository,
            graph_checkpointer=graph_checkpointer,
            run_repository=run_repository,
        )
        app.state.agent_runtimes[(session.thread_id, run_id)] = runtime
        return runtime, set()
    project = _require_project_access(
        app,
        session.project_id,
        actor_id,
        write=False,
    )
    capabilities = _capabilities_for_session(app, session, actor_id)
    runtime = AgentRuntime(
        _model_gateway(app.state.local_settings),
        checkpoint_store=checkpoint_repository,
        graph_checkpointer=graph_checkpointer,
        tool_runtime=ToolRuntime(
            Path(project.root_path),
            approvals=PersistentApprovalStore(
                ApprovalRepository(_database(app)),
                session_id=session.thread_id,
            ),
            audit_log=AuditRepository(_database(app)),
        ),
        run_repository=run_repository,
    )
    app.state.agent_runtimes[(session.thread_id, run_id)] = runtime
    return runtime, capabilities


def _capabilities_for_session(
    app: FastAPI,
    session: SessionRecord,
    actor_id: str,
) -> set[str]:
    if session.project_id is None:
        return set()
    membership = TeamRepository(_database(app)).get_project_member(
        session.project_id,
        actor_id,
    )
    capabilities = {"workspace.read", "git.read"}
    if membership is not None and membership.role != TeamRole.VIEWER:
        capabilities.update(
            {
                "workspace.write",
                "workspace.delete",
                "test.execute",
                "shell.execute",
                "git.write",
            }
        )
    return capabilities


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
