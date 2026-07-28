"""FastAPI entry point for the B4 persistence and development workflow boundary."""

import base64
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from packages.agent_core import AgentRuntime
from packages.contracts import (
    ChatMessage,
    IssueContext,
    MemoryScope,
    ProjectMember,
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
from packages.memory import LongTermMemoryStore
from packages.model_gateway import FakeModel, ModelGateway
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
    owner_id: str = "local-user"


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
    provider: str = "fake"
    model: str = "fake-model"
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
    """Demo-only fixed administrator login; replace in B8 before deployment."""

    model_config = ConfigDict(extra="forbid")
    username: str
    password: str


def create_app(
    *,
    database_url: str | None = None,
    workspace_root: Path | None = None,
    web_dist_path: Path | None = None,
) -> FastAPI:
    workspace = (workspace_root or project_root()).expanduser().resolve()
    configured_url = database_url or os.environ.get("CODEASSIST_DATABASE_URL")
    database = Database(configured_url or default_database_url(workspace))
    app = FastAPI(
        title="CodeAssist 2.0 API",
        version="0.1.0",
        description="Local-first Agent service boundary for the LangGraph core.",
    )
    app.state.database = database
    app.state.workspace_root = workspace
    app.state.agent_runtime = AgentRuntime(ModelGateway([FakeModel()]))
    app.state.runtime_logs: list[dict[str, object]] = []
    app.state.demo_tokens: dict[str, str] = {}
    app.state.host_pairing_codes: dict[str, str] = {}
    app.state.host_tokens: dict[str, str] = {}
    _database(app)
    account_names = ("admin", "admin1", "admin2", "admin3")
    for account_name in account_names:
        TeamRepository(database).create_user(
            UserRecord(id=account_name, display_name=account_name)
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
        return app.state.demo_tokens.get(token or "")

    def _authenticated_actor(authorization: str | None = Header(default=None)) -> str:
        """Authenticate the demo bearer token without exposing an actor-id request field."""
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="bearer token is required")
        token = authorization.removeprefix("Bearer ")
        actor_id = _actor_for_token(token)
        if actor_id is None:
            raise HTTPException(status_code=401, detail="invalid or expired bearer token")
        return actor_id

    @app.middleware("http")
    async def require_demo_auth(request, call_next):
        """Protect every HTTP API except service metadata and the demo login exchange."""
        public_paths = {"/healthz", "/api/v1/meta", "/api/v1/auth/login", "/api/v1/progress"}
        host_path = request.url.path.startswith("/api/v1/remote-hosts/")
        if (
            request.url.path.startswith("/api/v1/")
            and request.url.path not in public_paths
            and not host_path
        ):
            authorization = request.headers.get("authorization")
            if authorization is None or not authorization.startswith("Bearer "):
                return Response(status_code=401, content="bearer token is required")
            if _actor_for_token(authorization.removeprefix("Bearer ")) is None:
                return Response(status_code=401, content="invalid or expired bearer token")
        return await call_next(request)

    @app.get("/healthz", tags=["system"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "codeassist-api"}

    @app.get("/api/v1/meta", tags=["system"])
    def meta() -> dict[str, object]:
        return {
            "name": "codeassist-next",
            "version": "0.1.0",
            "stage": 6,
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
                "demo_auth": "available_with_fixed_admin_credentials",
                "team_rbac": "available_for_authenticated_admin",
                "session_sharing": "available_with_explicit_permission",
                "remote_agent_host": "declared_not_connected",
            },
        }

    @app.get("/api/v1/runtime/logs", tags=["system"])
    def runtime_logs(limit: int = Query(default=100, ge=1, le=200)) -> list[dict[str, object]]:
        """Return recent local service events for the Web diagnostics panel."""
        return list(app.state.runtime_logs[-limit:])

    @app.post("/api/v1/auth/login", tags=["auth"])
    def login(payload: LoginRequest) -> dict[str, str]:
        # TODO（后续 B8）：固定测试账号仅用于多人验收；预期改为可配置凭据与正式认证。
        if payload.username not in account_names or not secrets.compare_digest(
            payload.password, payload.username
        ):
            raise HTTPException(status_code=401, detail="invalid username or password")
        token = secrets.token_urlsafe(32)
        app.state.demo_tokens[token] = payload.username
        return {"access_token": token, "token_type": "bearer", "user_id": payload.username}

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
        project = repository.get_or_create(name=payload.name, root_path=str(root_path))
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

    @app.post("/api/v1/users", status_code=status.HTTP_201_CREATED, tags=["teams"])
    def create_user(
        payload: UserCreateRequest, actor_id: str = Depends(_authenticated_actor)
    ) -> dict[str, object]:
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
        pairing_code = secrets.token_urlsafe(24)
        app.state.host_pairing_codes[host.id] = pairing_code
        return {**host.model_dump(mode="json"), "pairing_code": pairing_code}

    @app.post("/api/v1/remote-hosts/{host_id}/pair", tags=["remote-host"])
    def pair_remote_host(host_id: str, payload: RemoteHostPairRequest) -> dict[str, object]:
        expected_code = app.state.host_pairing_codes.get(host_id)
        if expected_code is None or not secrets.compare_digest(expected_code, payload.pairing_code):
            raise HTTPException(status_code=401, detail="invalid or expired pairing code")
        app.state.host_pairing_codes.pop(host_id, None)
        host_token = secrets.token_urlsafe(32)
        app.state.host_tokens[host_token] = host_id
        try:
            host = TeamRepository(_database(app)).set_remote_host_status(host_id, "paired")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {**host.model_dump(mode="json"), "host_token": host_token}

    @app.post("/api/v1/remote-hosts/{host_id}/heartbeat", tags=["remote-host"])
    def remote_host_heartbeat(
        host_id: str,
        x_codeassist_host_token: str | None = Header(default=None),
    ) -> dict[str, str]:
        if app.state.host_tokens.get(x_codeassist_host_token or "") != host_id:
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
        try:
            content = base64.b64decode(payload.content_base64, validate=True)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="attachment content must be base64"
            ) from exc
        attachment_id = str(uuid4())
        directory = workspace / ".codeassist" / "attachments" / session_id
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
        request = RunRequest(
            thread_id=session.thread_id,
            run_id=payload.run_id or str(uuid4()),
            provider=payload.provider,
            model=payload.model,
            messages=[payload.message],
        )
        result = await _runtime(app).run(request)
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
                SessionRepository(_database(app)).append_message(session_id, payload.message)
                request = RunRequest(
                    thread_id=session.thread_id,
                    run_id=payload.run_id or str(uuid4()),
                    provider=payload.provider,
                    model=payload.model,
                    messages=[payload.message],
                )
                final_text = None
                async for event in _runtime(app).stream(request):
                    await websocket.send_json(event.model_dump(mode="json"))
                    if event.type.value == "run.completed":
                        final_text = event.data.get("text")
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
    def list_project_rules(project_id: str) -> list[dict[str, object]]:
        rules = RuleRepository(_database(app)).list(project_id)
        return [rule.model_dump(mode="json") for rule in rules]

    @app.post("/api/v1/projects/{project_id}/rules/discover", tags=["project"])
    def discover_project_rules(
        project_id: str,
        payload: RuleDiscoveryRequest | None = None,
    ) -> dict[str, object]:
        project = ProjectRepository(_database(app)).get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"project not found: {project_id}")
        current_dir = Path(payload.current_dir) if payload and payload.current_dir else None
        context = ProjectContextService().discover_and_store(
            project_id=project_id,
            project_root=Path(project.root_path),
            current_dir=current_dir,
            repository=RuleRepository(_database(app)),
        )
        return {**context.model_dump(mode="json"), "merged_text": context.merged_text}

    @app.post("/api/v1/projects/{project_id}/repository/scan", tags=["workflows"])
    def scan_repository(project_id: str) -> dict[str, object]:
        database = _database(app)
        project = ProjectRepository(database).get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"project not found: {project_id}")
        try:
            profile = RepositoryScanner(Path(project.root_path)).scan(project_id=project_id)
        except (NotADirectoryError, OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        RepositoryProfileRepository(database).save(profile)
        return profile.model_dump(mode="json")

    @app.get("/api/v1/projects/{project_id}/repository-profile", tags=["workflows"])
    def get_repository_profile(project_id: str) -> dict[str, object]:
        profile = RepositoryProfileRepository(_database(app)).get(project_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="repository profile not found")
        return profile.model_dump(mode="json")

    @app.post("/api/v1/workflows", status_code=status.HTTP_201_CREATED, tags=["workflows"])
    def create_workflow(payload: WorkflowCreateRequest) -> dict[str, object]:
        database = _database(app)
        project = ProjectRepository(database).get(payload.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"project not found: {payload.project_id}")
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
    def list_workflows(project_id: str | None = Query(default=None)) -> list[dict[str, object]]:
        workflows = WorkflowRepository(_database(app)).list(project_id=project_id)
        return [workflow.model_dump(mode="json") for workflow in workflows]

    @app.get("/api/v1/workflows/{workflow_id}", tags=["workflows"])
    def get_workflow(workflow_id: str) -> dict[str, object]:
        workflow = WorkflowRepository(_database(app)).get(workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail=f"workflow not found: {workflow_id}")
        return workflow.model_dump(mode="json")

    @app.get("/api/v1/workflows/{workflow_id}/agent-tree", tags=["workflows"])
    def get_agent_tree(workflow_id: str) -> dict[str, object]:
        workflow = WorkflowRepository(_database(app)).get(workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail=f"workflow not found: {workflow_id}")
        return {
            "workflow_id": workflow.id,
            "supervisor_run_id": workflow.supervisor_run_id,
            "agent_runs": [run.model_dump(mode="json") for run in workflow.agent_runs],
        }

    @app.get("/api/v1/workflows/{workflow_id}/pr", tags=["workflows"])
    def get_workflow_pr(workflow_id: str) -> dict[str, object]:
        workflow = WorkflowRepository(_database(app)).get(workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail=f"workflow not found: {workflow_id}")
        if workflow.pr_document is None:
            raise HTTPException(status_code=404, detail="PR document not generated")
        return workflow.pr_document.model_dump(mode="json")

    @app.patch("/api/v1/workflows/{workflow_id}/pr/review", tags=["workflows"])
    def review_workflow_pr(
        workflow_id: str,
        payload: ReviewDecisionRequest,
    ) -> dict[str, object]:
        repository = WorkflowRepository(_database(app))
        workflow = repository.get(workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail=f"workflow not found: {workflow_id}")
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
        owner_id: str = Query(default="local-user"),
        scope: MemoryScope | None = Query(default=None),
        include_disabled: bool = Query(default=True),
    ) -> list[dict[str, object]]:
        entries = MemoryRepository(_database(app)).list(
            owner_id=owner_id,
            scope=scope,
            include_disabled=include_disabled,
        )
        return [entry.model_dump(mode="json") for entry in entries]

    @app.post("/api/v1/memory", status_code=status.HTTP_201_CREATED, tags=["memory"])
    def add_memory(payload: MemoryCreateRequest) -> dict[str, object]:
        store = _memory_store(app, payload.scope, payload.owner_id)
        result = store.write_candidate(
            key=payload.key,
            content=payload.content,
            source=payload.source,
        )
        if result.entry is None:
            raise HTTPException(status_code=422, detail=result.reason or "memory write blocked")
        return result.entry.model_dump(mode="json")

    @app.patch("/api/v1/memory/{memory_id}", tags=["memory"])
    def update_memory(memory_id: str, payload: MemoryUpdateRequest) -> dict[str, object]:
        repository = MemoryRepository(_database(app))
        entry = repository.get(memory_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"memory entry not found: {memory_id}")
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
    def delete_memory(memory_id: str) -> Response:
        repository = MemoryRepository(_database(app))
        entry = repository.get(memory_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"memory entry not found: {memory_id}")
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
    database.create_all()
    return database


def _runtime(app: FastAPI) -> AgentRuntime:
    return app.state.agent_runtime


def _memory_store(app: FastAPI, scope: MemoryScope, owner_id: str) -> LongTermMemoryStore:
    database = _database(app)
    if scope == MemoryScope.PROJECT:
        project = ProjectRepository(database).get(owner_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"project not found: {owner_id}")
        path = Path(project.root_path) / ".codeassist" / "MEMORY.md"
    else:
        path = Path.home() / ".codeassist" / "MEMORY.md"
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
