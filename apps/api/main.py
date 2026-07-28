"""FastAPI entry point for the B3 persistence and memory service boundary."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from packages.contracts import ChatMessage, MemoryScope, SessionRecord
from packages.handover_agent import HandoverAgent
from packages.memory import LongTermMemoryStore
from packages.persistence import (
    Database,
    MemoryRepository,
    ProjectRepository,
    RuleRepository,
    SessionRepository,
    default_database_url,
)
from packages.project_context import ProjectContextService
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


def create_app(
    *,
    database_url: str | None = None,
    workspace_root: Path | None = None,
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

    @app.get("/healthz", tags=["system"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "codeassist-api"}

    @app.get("/api/v1/meta", tags=["system"])
    def meta() -> dict[str, object]:
        return {
            "name": "codeassist-next",
            "version": "0.1.0",
            "stage": 3,
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
                "frontend": "planned",
                "tauri": "planned",
            },
        }

    @app.get("/api/v1/progress", tags=["project"])
    def progress() -> dict[str, object]:
        agent = HandoverAgent.from_workspace()
        return agent.progress.to_public_dict()

    @app.get("/api/v1/tools", tags=["tools"])
    def tools() -> list[dict[str, object]]:
        """Expose registered tool schemas without exposing execution handles."""
        runtime = ToolRuntime(project_root())
        return [
            definition.model_dump(mode="json") for definition in runtime.registry.definitions()
        ]

    @app.post("/api/v1/projects", status_code=status.HTTP_201_CREATED, tags=["project"])
    def create_project(payload: ProjectCreateRequest) -> dict[str, object]:
        db = _database(app)
        repository = ProjectRepository(db)
        project = repository.get_or_create(name=payload.name, root_path=payload.root_path)
        return project.model_dump(mode="json")

    @app.get("/api/v1/projects", tags=["project"])
    def list_projects() -> list[dict[str, object]]:
        projects = ProjectRepository(_database(app)).list()
        return [project.model_dump(mode="json") for project in projects]

    @app.post("/api/v1/sessions", status_code=status.HTTP_201_CREATED, tags=["sessions"])
    def create_session(payload: SessionCreateRequest) -> dict[str, object]:
        session = SessionRepository(_database(app)).create(
            SessionRecord(
                thread_id=payload.thread_id,
                user_id=payload.user_id,
                project_id=payload.project_id,
                title=payload.title,
            )
        )
        return session.model_dump(mode="json")

    @app.get("/api/v1/sessions", tags=["sessions"])
    def list_sessions(
        project_id: str | None = Query(default=None),
        user_id: str | None = Query(default=None),
    ) -> list[dict[str, object]]:
        sessions = SessionRepository(_database(app)).list(project_id=project_id, user_id=user_id)
        return [session.model_dump(mode="json") for session in sessions]

    @app.get("/api/v1/sessions/{session_id}", tags=["sessions"])
    def get_session(session_id: str) -> dict[str, object]:
        repository = SessionRepository(_database(app))
        try:
            snapshot = repository.snapshot(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return snapshot.model_dump(mode="json")

    @app.post("/api/v1/sessions/{session_id}/messages", tags=["sessions"])
    def append_session_message(session_id: str, message: ChatMessage) -> dict[str, object]:
        try:
            stored = SessionRepository(_database(app)).append_message(session_id, message)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return stored.model_dump(mode="json")

    @app.post("/api/v1/sessions/{session_id}/summarize", tags=["sessions"])
    def summarize_session(
        session_id: str,
        max_characters: int = Query(default=4_000, ge=100, le=100_000),
    ) -> dict[str, object]:
        from packages.memory import SessionMemoryService

        repository = SessionRepository(_database(app))
        session = repository.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
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

    return app


def project_root() -> Path:
    """Resolve the repository root without coupling the domain package to FastAPI."""
    return Path(__file__).resolve().parents[2]


def _database(app: FastAPI) -> Database:
    database: Database = app.state.database
    database.create_all()
    return database


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


app = create_app()
