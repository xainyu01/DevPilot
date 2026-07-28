"""Short-lived conversation memory backed by relational message rows."""

from __future__ import annotations

from typing import Protocol

from packages.contracts import (
    ChatMessage,
    SessionRecord,
    SessionSnapshot,
    SessionSummary,
    StoredMessage,
)


class SessionRepositoryProtocol(Protocol):
    def create(self, session: SessionRecord) -> SessionRecord:
        ...

    def get_by_thread(self, thread_id: str) -> SessionRecord | None:
        ...

    def append_message(self, session_id: str, message: ChatMessage) -> StoredMessage:
        ...

    def snapshot(self, session_id: str, *, limit: int | None = None) -> SessionSnapshot:
        ...

    def save_summary(self, summary: SessionSummary) -> SessionSummary:
        ...


class SessionMemoryService:
    """Persist and restore messages without coupling the Agent graph to SQLAlchemy."""

    def __init__(self, repository: SessionRepositoryProtocol) -> None:
        self.repository = repository

    def start(
        self,
        *,
        thread_id: str,
        user_id: str | None = None,
        project_id: str | None = None,
        title: str | None = None,
    ) -> SessionRecord:
        return self.repository.create(
            SessionRecord(
                thread_id=thread_id,
                user_id=user_id,
                project_id=project_id,
                title=title,
            )
        )

    def append(self, thread_id: str, message: ChatMessage) -> StoredMessage:
        session = self.repository.get_by_thread(thread_id)
        if session is None:
            session = self.start(thread_id=thread_id)
        return self.repository.append_message(session.id, message)

    def restore(self, thread_id: str, *, limit: int | None = None) -> SessionSnapshot:
        session = self.repository.get_by_thread(thread_id)
        if session is None:
            raise KeyError(f"session not found for thread: {thread_id}")
        return self.repository.snapshot(session.id, limit=limit)

    def summarize(self, thread_id: str, *, max_characters: int = 4_000) -> SessionSummary:
        snapshot = self.restore(thread_id)
        if max_characters < 100:
            raise ValueError("max_characters must be at least 100")
        lines = [
            f"{stored.message.role}: {stored.message.text_content()}"
            for stored in snapshot.messages
            if stored.message.text_content().strip()
        ]
        text = _compact_lines(lines, max_characters)
        covered = snapshot.messages[-1].ordinal if snapshot.messages else 0
        summary = SessionSummary(
            session_id=snapshot.session.id,
            summary=text,
            message_count=len(snapshot.messages),
            covered_through_ordinal=covered,
        )
        return self.repository.save_summary(summary)

    def restore_messages(self, thread_id: str, *, limit: int | None = None) -> list[ChatMessage]:
        return [item.message for item in self.restore(thread_id, limit=limit).messages]


def _compact_lines(lines: list[str], max_characters: int) -> str:
    if not lines:
        return ""
    full = "\n".join(lines)
    if len(full) <= max_characters:
        return full
    if len(lines) == 1:
        return lines[0][:max_characters]
    head = lines[0]
    tail_budget = max_characters - len(head) - 20
    tail = "\n".join(lines[1:])[-max(tail_budget, 0) :]
    return f"{head}\n[… earlier context compacted …]\n{tail}"[:max_characters]
