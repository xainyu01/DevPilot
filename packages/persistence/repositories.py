"""Repository implementations for conversations, rules, memory and runs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, select

from packages.contracts import (
    ChatMessage,
    Checkpoint,
    CheckpointRef,
    MemoryEntry,
    MemoryRevision,
    MemoryScope,
    ProjectRecord,
    ProjectRule,
    RepositoryProfile,
    RunContext,
    RunEvent,
    RunStatus,
    SessionRecord,
    SessionSnapshot,
    SessionStatus,
    SessionSummary,
    StoredMessage,
    WorkflowRun,
)

from .database import Database
from .models import (
    AgentRunRow,
    CheckpointRow,
    ContentBlockRow,
    MemoryEntryRow,
    MemoryRevisionRow,
    MessageRow,
    ProjectRow,
    ProjectRuleRow,
    RepositoryProfileRow,
    RunEventRow,
    SessionRow,
    SessionSummaryRow,
    WorkflowRunRow,
    utc_now,
)


class ProjectRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, project: ProjectRecord) -> ProjectRecord:
        with self.database.session() as db:
            row = ProjectRow(
                id=project.id,
                name=project.name,
                root_path=project.root_path,
                created_at=project.created_at,
                updated_at=project.updated_at,
            )
            db.add(row)
        return project

    def get(self, project_id: str) -> ProjectRecord | None:
        with self.database.session() as db:
            row = db.get(ProjectRow, project_id)
            return _project_from_row(row) if row is not None else None

    def get_by_root(self, root_path: str) -> ProjectRecord | None:
        with self.database.session() as db:
            row = db.scalar(select(ProjectRow).where(ProjectRow.root_path == root_path))
            return _project_from_row(row) if row is not None else None

    def get_or_create(self, *, name: str, root_path: str) -> ProjectRecord:
        existing = self.get_by_root(root_path)
        if existing is not None:
            return existing
        return self.create(ProjectRecord(name=name, root_path=root_path))

    def list(self) -> list[ProjectRecord]:
        with self.database.session() as db:
            rows = db.scalars(select(ProjectRow).order_by(ProjectRow.name.asc()))
            return [_project_from_row(row) for row in rows]


class SessionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, session: SessionRecord) -> SessionRecord:
        with self.database.session() as db:
            existing = db.scalar(
                select(SessionRow).where(SessionRow.thread_id == session.thread_id)
            )
            if existing is not None:
                return _session_from_row(existing)
            db.add(
                SessionRow(
                    id=session.id,
                    thread_id=session.thread_id,
                    user_id=session.user_id,
                    project_id=session.project_id,
                    title=session.title,
                    status=session.status.value,
                    summary=session.summary,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                )
            )
        return session

    def get(self, session_id: str) -> SessionRecord | None:
        with self.database.session() as db:
            row = db.get(SessionRow, session_id)
            return _session_from_row(row) if row is not None else None

    def get_by_thread(self, thread_id: str) -> SessionRecord | None:
        with self.database.session() as db:
            row = db.scalar(select(SessionRow).where(SessionRow.thread_id == thread_id))
            return _session_from_row(row) if row is not None else None

    def list(
        self,
        *,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> list[SessionRecord]:
        with self.database.session() as db:
            query = select(SessionRow).order_by(SessionRow.updated_at.desc())
            if project_id is not None:
                query = query.where(SessionRow.project_id == project_id)
            if user_id is not None:
                query = query.where(SessionRow.user_id == user_id)
            return [_session_from_row(row) for row in db.scalars(query)]

    def append_message(self, session_id: str, message: ChatMessage) -> StoredMessage:
        with self.database.session() as db:
            if db.get(SessionRow, session_id) is None:
                raise KeyError(f"session not found: {session_id}")
            max_ordinal = db.scalar(
                select(func.max(MessageRow.ordinal)).where(MessageRow.session_id == session_id)
            )
            ordinal = int(max_ordinal or 0) + 1
            stored = StoredMessage(
                session_id=session_id,
                ordinal=ordinal,
                message=message,
            )
            row = MessageRow(
                id=stored.id,
                session_id=session_id,
                ordinal=ordinal,
                role=message.role,
                name=message.name,
                created_at=stored.created_at,
                updated_at=stored.created_at,
            )
            db.add(row)
            db.flush()
            for block_ordinal, block in enumerate(message.content):
                values = block.model_dump(mode="json")
                db.add(
                    ContentBlockRow(
                        id=str(uuid4()),
                        message_id=stored.id,
                        ordinal=block_ordinal,
                        type=values["type"],
                        text=values.get("text"),
                        url=values.get("url"),
                        attachment_id=values.get("attachment_id"),
                        mime_type=values.get("mime_type"),
                        detail=values.get("detail"),
                        filename=values.get("filename"),
                        tool_call_id=values.get("tool_call_id"),
                        content=values.get("content"),
                        is_error=values.get("is_error"),
                    )
                )
            db.execute(
                _update_session_timestamp(session_id)
            )
        return stored

    def list_messages(self, session_id: str, *, limit: int | None = None) -> list[StoredMessage]:
        with self.database.session() as db:
            query = select(MessageRow).where(MessageRow.session_id == session_id).order_by(
                MessageRow.ordinal.asc()
            )
            rows = list(db.scalars(query))
            if limit is not None:
                rows = rows[-limit:]
            blocks = {
                row.id: list(
                    db.scalars(
                        select(ContentBlockRow)
                        .where(ContentBlockRow.message_id == row.id)
                        .order_by(ContentBlockRow.ordinal.asc())
                    )
                )
                for row in rows
            }
            return [_stored_message_from_rows(row, blocks[row.id]) for row in rows]

    def save_summary(self, summary: SessionSummary) -> SessionSummary:
        with self.database.session() as db:
            row = db.scalar(
                select(SessionSummaryRow).where(SessionSummaryRow.session_id == summary.session_id)
            )
            if row is None:
                db.add(
                    SessionSummaryRow(
                        id=summary.session_id + ":summary",
                        session_id=summary.session_id,
                        summary=summary.summary,
                        message_count=summary.message_count,
                        covered_through_ordinal=summary.covered_through_ordinal,
                        created_at=summary.created_at,
                    )
                )
            else:
                row.summary = summary.summary
                row.message_count = summary.message_count
                row.covered_through_ordinal = summary.covered_through_ordinal
                row.created_at = summary.created_at
            db.execute(
                _update_session_timestamp(summary.session_id, summary=summary.summary)
            )
        return summary

    def get_summary(self, session_id: str) -> SessionSummary | None:
        with self.database.session() as db:
            row = db.scalar(
                select(SessionSummaryRow).where(SessionSummaryRow.session_id == session_id)
            )
            if row is None:
                return None
            return SessionSummary(
                session_id=row.session_id,
                summary=row.summary,
                message_count=row.message_count,
                covered_through_ordinal=row.covered_through_ordinal,
                created_at=_as_utc(row.created_at),
            )

    def snapshot(self, session_id: str, *, limit: int | None = None) -> SessionSnapshot:
        session = self.get(session_id)
        if session is None:
            raise KeyError(f"session not found: {session_id}")
        return SessionSnapshot(
            session=session,
            messages=self.list_messages(session_id, limit=limit),
            summary=self.get_summary(session_id),
        )

    def archive(self, session_id: str) -> SessionRecord:
        with self.database.session() as db:
            row = db.get(SessionRow, session_id)
            if row is None:
                raise KeyError(f"session not found: {session_id}")
            row.status = SessionStatus.ARCHIVED.value
            row.updated_at = utc_now()
            return _session_from_row(row)


class MemoryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save(self, entry: MemoryEntry, *, action: str) -> MemoryEntry:
        with self.database.session() as db:
            row = db.get(MemoryEntryRow, entry.id)
            if row is None:
                row = db.scalar(
                    select(MemoryEntryRow).where(
                        MemoryEntryRow.owner_id == entry.owner_id,
                        MemoryEntryRow.scope == entry.scope.value,
                        MemoryEntryRow.key == entry.key,
                    )
                )
                if row is not None and row.deleted_at is not None:
                    db.delete(row)
                    db.flush()
                    row = None
            if row is None:
                row = MemoryEntryRow(id=entry.id)
                db.add(row)
            row.owner_id = entry.owner_id
            row.scope = entry.scope.value
            row.key = entry.key
            row.content = entry.content
            row.source = entry.source
            row.enabled = entry.enabled
            row.revision = entry.revision
            row.created_at = entry.created_at
            row.updated_at = entry.updated_at
            row.deleted_at = entry.deleted_at
            db.add(
                MemoryRevisionRow(
                    id=str(uuid4()),
                    memory_id=entry.id,
                    revision=entry.revision,
                    content=entry.content,
                    action=action,
                )
            )
        return entry

    def get(self, memory_id: str) -> MemoryEntry | None:
        with self.database.session() as db:
            row = db.get(MemoryEntryRow, memory_id)
            return _memory_from_row(row) if row is not None else None

    def list(
        self,
        *,
        owner_id: str | None = None,
        scope: MemoryScope | None = None,
        include_disabled: bool = True,
    ) -> list[MemoryEntry]:
        with self.database.session() as db:
            query = select(MemoryEntryRow).where(MemoryEntryRow.deleted_at.is_(None))
            if owner_id is not None:
                query = query.where(MemoryEntryRow.owner_id == owner_id)
            if scope is not None:
                query = query.where(MemoryEntryRow.scope == scope.value)
            if not include_disabled:
                query = query.where(MemoryEntryRow.enabled.is_(True))
            query = query.order_by(MemoryEntryRow.updated_at.desc())
            return [_memory_from_row(row) for row in db.scalars(query)]

    def revisions(self, memory_id: str) -> list[MemoryRevision]:
        with self.database.session() as db:
            rows = db.scalars(
                select(MemoryRevisionRow)
                .where(MemoryRevisionRow.memory_id == memory_id)
                .order_by(MemoryRevisionRow.revision.asc())
            )
            return [
                MemoryRevision(
                    memory_id=row.memory_id,
                    revision=row.revision,
                    content=row.content,
                    action=row.action,
                    created_at=_as_utc(row.created_at),
                )
                for row in rows
            ]

    def soft_delete(self, memory_id: str, *, revision: int, content: str) -> None:
        entry = self.get(memory_id)
        if entry is None:
            raise KeyError(f"memory entry not found: {memory_id}")
        self.save(
            entry.model_copy(
                update={
                    "revision": revision,
                    "deleted_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                    "content": content,
                    "enabled": False,
                }
            ),
            action="deleted",
        )


class RuleRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def replace(self, project_id: str, rules: list[ProjectRule]) -> list[ProjectRule]:
        with self.database.session() as db:
            db.execute(delete(ProjectRuleRow).where(ProjectRuleRow.project_id == project_id))
            db.add_all(
                [
                    ProjectRuleRow(
                        id=rule.id,
                        project_id=project_id,
                        source_path=rule.source_path,
                        scope_path=rule.scope_path,
                        filename=rule.filename,
                        content=rule.content,
                        priority=rule.priority,
                        source_kind=rule.source_kind,
                        enabled=rule.enabled,
                        discovered_at=rule.discovered_at,
                    )
                    for rule in rules
                ]
            )
        return rules

    def list(self, project_id: str, *, include_disabled: bool = False) -> list[ProjectRule]:
        with self.database.session() as db:
            query = select(ProjectRuleRow).where(ProjectRuleRow.project_id == project_id)
            if not include_disabled:
                query = query.where(ProjectRuleRow.enabled.is_(True))
            query = query.order_by(ProjectRuleRow.priority.asc(), ProjectRuleRow.source_path.asc())
            return [_rule_from_row(row) for row in db.scalars(query)]


class RepositoryProfileRepository:
    """Persist the latest incremental repository snapshot per project."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save(self, profile: RepositoryProfile) -> RepositoryProfile:
        if profile.project_id is None:
            raise ValueError("repository profile must have a project_id before persistence")
        with self.database.session() as db:
            row = db.scalar(
                select(RepositoryProfileRow).where(
                    RepositoryProfileRow.project_id == profile.project_id
                )
            )
            if row is None:
                row = RepositoryProfileRow(id=profile.id, project_id=profile.project_id)
                db.add(row)
            row.id = profile.id
            row.index_version = profile.index_version
            row.profile_json = _json_value(profile)
            row.updated_at = profile.indexed_at
        return profile

    def get(self, project_id: str) -> RepositoryProfile | None:
        with self.database.session() as db:
            row = db.scalar(
                select(RepositoryProfileRow).where(
                    RepositoryProfileRow.project_id == project_id
                )
            )
            return RepositoryProfile.model_validate(row.profile_json) if row is not None else None


class WorkflowRepository:
    """Store complete workflow snapshots for restart, trace and review views."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save(self, workflow: WorkflowRun) -> WorkflowRun:
        with self.database.session() as db:
            row = db.get(WorkflowRunRow, workflow.id)
            if row is None:
                row = WorkflowRunRow(id=workflow.id, project_id=workflow.project_id)
                db.add(row)
            row.project_id = workflow.project_id
            row.status = workflow.status.value
            row.workflow_json = _json_value(workflow)
            row.updated_at = workflow.updated_at
            if row.created_at is None:
                row.created_at = workflow.created_at
        return workflow

    def get(self, workflow_id: str) -> WorkflowRun | None:
        with self.database.session() as db:
            row = db.get(WorkflowRunRow, workflow_id)
            return WorkflowRun.model_validate(row.workflow_json) if row is not None else None

    def list(self, project_id: str | None = None) -> list[WorkflowRun]:
        with self.database.session() as db:
            query = select(WorkflowRunRow).order_by(WorkflowRunRow.updated_at.desc())
            if project_id is not None:
                query = query.where(WorkflowRunRow.project_id == project_id)
            return [WorkflowRun.model_validate(row.workflow_json) for row in db.scalars(query)]


class RunRepository:
    """Durable run/event records used by API recovery and audit views."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def start_run(self, context: RunContext) -> None:
        with self.database.session() as db:
            row = db.get(AgentRunRow, context.run_id)
            if row is None:
                db.add(
                    AgentRunRow(
                        id=context.run_id,
                        thread_id=context.thread_id,
                        status=RunStatus.RUNNING.value,
                        provider=str(context.provider),
                        model=context.model,
                        metadata_json=_json_value(context.metadata),
                    )
                )

    def save_event(self, event: RunEvent) -> None:
        with self.database.session() as db:
            run = db.get(AgentRunRow, event.run_id)
            if run is None:
                db.add(
                    AgentRunRow(
                        id=event.run_id,
                        thread_id=event.thread_id,
                        status=event.status.value,
                        provider="unknown",
                        model="unknown",
                    )
                )
            else:
                run.status = event.status.value
            if db.scalar(
                select(RunEventRow).where(
                    RunEventRow.run_id == event.run_id,
                    RunEventRow.sequence == event.sequence,
                )
            ) is None:
                db.add(
                    RunEventRow(
                        id=event.event_id,
                        thread_id=event.thread_id,
                        run_id=event.run_id,
                        sequence=event.sequence,
                        type=event.type.value,
                        status=event.status.value,
                        node=event.node,
                        data_json=_json_value(event.data),
                        created_at=event.created_at,
                    )
                )

    def list_events(self, run_id: str) -> list[RunEvent]:
        with self.database.session() as db:
            rows = db.scalars(
                select(RunEventRow)
                .where(RunEventRow.run_id == run_id)
                .order_by(RunEventRow.sequence)
            )
            return [
                RunEvent(
                    event_id=row.id,
                    sequence=row.sequence,
                    thread_id=row.thread_id,
                    run_id=row.run_id,
                    type=row.type,
                    status=row.status,
                    node=row.node,
                    data=row.data_json,
                    created_at=_as_utc(row.created_at),
                )
                for row in rows
            ]


class CheckpointRepository:
    """A ``CheckpointStore``-compatible durable checkpoint backend."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save(
        self,
        *,
        thread_id: str,
        run_id: str,
        state: dict[str, Any],
        next_nodes: tuple[str, ...] | list[str] = (),
        status: RunStatus,
        sequence: int = 0,
    ) -> Checkpoint:
        checkpoint = Checkpoint(
            ref=CheckpointRef(
                thread_id=thread_id,
                run_id=run_id,
                checkpoint_id=str(uuid4()),
                sequence=sequence,
            ),
            state=_json_value(state),
            next_nodes=list(next_nodes),
            status=status,
            updated_at=datetime.now(UTC),
        )
        with self.database.session() as db:
            row = db.scalar(
                select(CheckpointRow).where(
                    CheckpointRow.thread_id == thread_id,
                    CheckpointRow.run_id == run_id,
                )
            )
            if row is None:
                row = CheckpointRow(id=str(uuid4()), thread_id=thread_id, run_id=run_id)
                db.add(row)
            row.checkpoint_id = checkpoint.ref.checkpoint_id
            row.sequence = sequence
            row.state_json = checkpoint.state
            row.next_nodes_json = checkpoint.next_nodes
            row.status = status.value
            row.updated_at = checkpoint.updated_at
        return checkpoint

    def get(self, thread_id: str, run_id: str) -> Checkpoint | None:
        with self.database.session() as db:
            row = db.scalar(
                select(CheckpointRow).where(
                    CheckpointRow.thread_id == thread_id,
                    CheckpointRow.run_id == run_id,
                )
            )
            if row is None:
                return None
            return Checkpoint(
                ref=CheckpointRef(
                    thread_id=row.thread_id,
                    run_id=row.run_id,
                    checkpoint_id=row.checkpoint_id,
                    sequence=row.sequence,
                ),
                state=row.state_json,
                next_nodes=row.next_nodes_json,
                status=row.status,
                updated_at=_as_utc(row.updated_at),
            )

    def clear(self, thread_id: str, run_id: str) -> None:
        with self.database.session() as db:
            db.execute(
                delete(CheckpointRow).where(
                    CheckpointRow.thread_id == thread_id,
                    CheckpointRow.run_id == run_id,
                )
            )


def _project_from_row(row: ProjectRow) -> ProjectRecord:
    return ProjectRecord(
        id=row.id,
        name=row.name,
        root_path=row.root_path,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )


def _session_from_row(row: SessionRow) -> SessionRecord:
    return SessionRecord(
        id=row.id,
        thread_id=row.thread_id,
        user_id=row.user_id,
        project_id=row.project_id,
        title=row.title,
        status=row.status,
        summary=row.summary,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )


def _stored_message_from_rows(row: MessageRow, blocks: list[ContentBlockRow]) -> StoredMessage:
    content = []
    for block in blocks:
        values: dict[str, Any] = {"type": block.type}
        for name in (
            "text",
            "url",
            "attachment_id",
            "mime_type",
            "detail",
            "filename",
            "tool_call_id",
            "content",
            "is_error",
        ):
            value = getattr(block, name)
            if value is not None:
                values[name] = value
        content.append(values)
    return StoredMessage(
        id=row.id,
        session_id=row.session_id,
        ordinal=row.ordinal,
        message=ChatMessage(role=row.role, name=row.name, content=content),
        created_at=_as_utc(row.created_at),
    )


def _memory_from_row(row: MemoryEntryRow) -> MemoryEntry:
    return MemoryEntry(
        id=row.id,
        owner_id=row.owner_id,
        scope=row.scope,
        key=row.key,
        content=row.content,
        source=row.source,
        enabled=row.enabled,
        revision=row.revision,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        deleted_at=_as_utc(row.deleted_at) if row.deleted_at else None,
    )


def _rule_from_row(row: ProjectRuleRow) -> ProjectRule:
    return ProjectRule(
        id=row.id,
        project_id=row.project_id,
        source_path=row.source_path,
        scope_path=row.scope_path,
        filename=row.filename,
        content=row.content,
        priority=row.priority,
        source_kind=row.source_kind,
        enabled=row.enabled,
        discovered_at=_as_utc(row.discovered_at),
    )


def _update_session_timestamp(session_id: str, *, summary: str | None = None) -> Any:
    from sqlalchemy import update

    values: dict[str, Any] = {"updated_at": utc_now()}
    if summary is not None:
        values["summary"] = summary
    return update(SessionRow).where(SessionRow.id == session_id).values(**values)


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


__all__ = [
    "CheckpointRepository",
    "MemoryRepository",
    "ProjectRepository",
    "RuleRepository",
    "RunRepository",
    "SessionRepository",
]
