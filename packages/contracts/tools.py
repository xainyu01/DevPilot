"""Provider-neutral contracts for tools, policy and human approval.

These objects are intentionally independent from the concrete tool
implementations.  They can be stored in an event log now and moved to the
persistence package in a later batch without changing the public protocol.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class ToolRisk(StrEnum):
    """Side-effect class used by the policy engine."""

    READ_ONLY = "read_only"
    RECOVERABLE_WRITE = "recoverable_write"
    HIGH_RISK = "high_risk"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    risk: ToolRisk
    required_capabilities: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=30, gt=0, le=600)
    max_output_chars: int = Field(default=20_000, ge=1, le=1_000_000)
    idempotent: bool = False


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool_name: str
    status: Literal["succeeded", "denied", "failed", "pending_approval"]
    output: str = ""
    error: dict[str, Any] | None = None
    approval_request: ApprovalRequest | None = None
    duration_ms: int = Field(default=0, ge=0)


class ApprovalScope(StrEnum):
    """The maximum lifetime of an approval decision."""

    ONCE = "once"
    SESSION = "session"
    COMMAND = "command"


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    call_id: str
    tool_name: str
    risk: ToolRisk
    arguments: dict[str, Any] = Field(default_factory=dict)
    required_capabilities: list[str] = Field(default_factory=list)
    reason: str
    session_id: str
    fingerprint: str
    status: Literal["pending", "approved", "denied", "consumed"] = "pending"
    scope: ApprovalScope | None = None
    command_pattern: str | None = None
    decided_by: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    approved: bool
    scope: ApprovalScope = ApprovalScope.ONCE
    decided_by: str = Field(default="user", min_length=1)
    command_pattern: str | None = None


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool = False
    requires_approval: bool = False
    reason: str
    risk: ToolRisk
    approval_request_id: str | None = None
    approval_scope: ApprovalScope | None = None


class AuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: Literal[
        "policy.allowed",
        "policy.denied",
        "approval.requested",
        "approval.decided",
        "tool.started",
        "tool.completed",
        "tool.failed",
    ]
    actor_id: str
    session_id: str
    run_id: str | None = None
    tool_name: str | None = None
    risk: ToolRisk | None = None
    outcome: str
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalScope",
    "AuditRecord",
    "PolicyDecision",
    "ToolCall",
    "ToolDefinition",
    "ToolResult",
    "ToolRisk",
]
