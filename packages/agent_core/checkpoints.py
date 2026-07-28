"""In-memory checkpoint references for B1.

Durable database persistence belongs to B3.  Keeping this store explicit lets
the runtime expose a stable checkpoint contract now and replace the backing
store later without changing graph nodes or clients.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from packages.contracts import AgentState, Checkpoint, CheckpointRef, RunStatus


class CheckpointStore:
    """Thread-safe latest-checkpoint store keyed by ``thread_id`` and ``run_id``."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Checkpoint] = {}
        self._lock = RLock()

    def save(
        self,
        *,
        thread_id: str,
        run_id: str,
        state: AgentState | dict[str, Any],
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
            state=deepcopy(dict(state)),
            next_nodes=list(next_nodes),
            status=status,
            updated_at=datetime.now(UTC),
        )
        with self._lock:
            self._items[(thread_id, run_id)] = checkpoint
        return checkpoint

    def get(self, thread_id: str, run_id: str) -> Checkpoint | None:
        with self._lock:
            checkpoint = self._items.get((thread_id, run_id))
            return deepcopy(checkpoint) if checkpoint is not None else None

    def clear(self, thread_id: str, run_id: str) -> None:
        with self._lock:
            self._items.pop((thread_id, run_id), None)
