"""Execution context supplied to every tool."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ToolExecutionContext(BaseModel):
    """Identity, workspace and least-privilege capabilities for one call."""

    model_config = ConfigDict(extra="forbid")

    workspace_root: Path
    actor_id: str = "agent"
    session_id: str = "local-session"
    run_id: str | None = None
    capabilities: set[str] = Field(default_factory=lambda: {"workspace.read"})
    environment: dict[str, str] = Field(default_factory=dict)

    def safe_environment(self) -> dict[str, str]:
        """Return a small non-secret environment for subprocess tools."""
        names = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
        values = {name: os.environ[name] for name in names if name in os.environ}
        values.update(self.environment)
        return values

    def with_run(self, run_id: str) -> ToolExecutionContext:
        return self.model_copy(update={"run_id": run_id})
