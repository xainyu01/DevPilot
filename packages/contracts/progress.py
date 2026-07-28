"""Progress and handover contracts.

This package intentionally contains no FastAPI, database, or model-provider code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BatchProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    status: str
    percent: int = Field(ge=0, le=100)
    scope: list[str] = Field(default_factory=list)
    completed: list[str] = Field(default_factory=list)
    in_progress: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class ProgressSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_percent: int = Field(ge=0, le=100)
    current_batch: str
    next_action: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    constraints: list[str] = Field(default_factory=list)
    batches: list[BatchProgress] = Field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
