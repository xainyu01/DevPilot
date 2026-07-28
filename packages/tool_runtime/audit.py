"""In-memory audit sink used until the persistence batch adds a repository."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock

from packages.contracts import AuditRecord


class AuditLog:
    """Thread-safe append-only audit log with copy-on-read semantics."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []
        self._lock = RLock()

    def record(self, event: AuditRecord) -> AuditRecord:
        with self._lock:
            self._records.append(event)
        return event

    def list(
        self,
        *,
        session_id: str | None = None,
        tool_name: str | None = None,
    ) -> list[AuditRecord]:
        with self._lock:
            records = [
                item
                for item in self._records
                if (session_id is None or item.session_id == session_id)
                and (tool_name is None or item.tool_name == tool_name)
            ]
            return deepcopy(records)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
