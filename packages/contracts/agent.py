"""Provider-neutral contracts for the Agent runtime.

The contracts in this module are deliberately independent from FastAPI,
LangGraph and any model vendor.  LangGraph state can contain these models,
while API and CLI layers can consume the same serialized shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, TypedDict
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .tools import ApprovalRequest, ToolCall, ToolResult


class ContentBlock(BaseModel):
    """Base type for one piece of a chat message."""

    model_config = ConfigDict(extra="forbid")
    type: str


class TextBlock(ContentBlock):
    type: Literal["text"] = "text"
    text: str


class ImageBlock(ContentBlock):
    type: Literal["image"] = "image"
    url: str | None = None
    attachment_id: str | None = None
    mime_type: str = "image/*"
    detail: Literal["auto", "low", "high"] = "auto"

    @model_validator(mode="after")
    def require_source(self) -> ImageBlock:
        if bool(self.url) == bool(self.attachment_id):
            raise ValueError("ImageBlock requires exactly one of url or attachment_id")
        return self


class AudioBlock(ContentBlock):
    type: Literal["audio"] = "audio"
    attachment_id: str
    mime_type: str = "audio/*"


class DocumentBlock(ContentBlock):
    type: Literal["document"] = "document"
    attachment_id: str
    mime_type: str = "application/pdf"
    filename: str | None = None


class ToolResultBlock(ContentBlock):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    content: str
    is_error: bool = False


class ModelToolCall(BaseModel):
    """One provider-neutral tool request emitted by a model."""

    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any]


ContentBlockValue = Annotated[
    TextBlock | ImageBlock | AudioBlock | DocumentBlock | ToolResultBlock,
    Field(discriminator="type"),
]


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: list[ContentBlockValue] = Field(min_length=1)
    name: str | None = None
    tool_calls: list[ModelToolCall] = Field(default_factory=list)

    @classmethod
    def from_text(
        cls,
        role: Literal["system", "user", "assistant", "tool"],
        text: str,
    ) -> ChatMessage:
        return cls(role=role, content=[TextBlock(text=text)])

    def text_content(self) -> str:
        """Return the visible text without discarding non-text blocks in state."""
        pieces: list[str] = []
        for block in self.content:
            if isinstance(block, TextBlock):
                pieces.append(block.text)
            elif isinstance(block, ToolResultBlock):
                pieces.append(block.content)
        return "\n".join(pieces)


class ModelProvider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    FAKE = "fake"


class ModelCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: bool = True
    image: bool = False
    audio: bool = False
    pdf: bool = False
    tools: bool = False
    structured_output: bool = False

    def supports(self, block: ContentBlockValue) -> bool:
        if isinstance(block, TextBlock | ToolResultBlock):
            return self.text
        if isinstance(block, ImageBlock):
            return self.image
        if isinstance(block, AudioBlock):
            return self.audio
        if isinstance(block, DocumentBlock):
            return self.pdf if block.mime_type == "application/pdf" else False
        return False

    def unsupported_blocks(self, messages: list[ChatMessage]) -> list[str]:
        unsupported: list[str] = []
        for message in messages:
            for block in message.content:
                if not self.supports(block):
                    unsupported.append(block.type)
        return sorted(set(unsupported))


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def fill_total(self) -> TokenUsage:
        if self.total_tokens == 0 and (self.input_tokens or self.output_tokens):
            self.total_tokens = self.input_tokens + self.output_tokens
        return self


class ModelStopReason(StrEnum):
    """Normalized reason why a model turn stopped."""

    TEXT_END = "text_end"
    TOOL_CALLS = "tool_calls"
    LENGTH_LIMIT = "length_limit"
    PROVIDER_ERROR = "provider_error"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ModelProvider | str
    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, ge=1)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    response_format: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ModelProvider | str
    model: str
    message: ChatMessage
    tool_calls: list[ModelToolCall] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    finish_reason: str | None = None
    stop_reason: ModelStopReason = ModelStopReason.UNKNOWN
    response_metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.message.text_content()


class ModelStreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ModelProvider | str
    model: str
    kind: Literal[
        "text_delta",
        "tool_call_delta",
        "tool_call_end",
        "message_end",
        "provider_error",
    ] = "text_delta"
    text: str = ""
    index: int = Field(default=0, ge=0)
    tool_call_index: int | None = Field(default=None, ge=0)
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_delta: str = ""
    tool_call: ModelToolCall | None = None
    tool_call_complete: bool = False
    done: bool = False
    usage: TokenUsage | None = None
    finish_reason: str | None = None
    stop_reason: ModelStopReason | None = None
    error: dict[str, Any] | None = None
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdapterHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ModelProvider | str
    model: str
    status: Literal["ready", "configured", "unavailable", "not_implemented"]
    detail: str | None = None


class CapabilityError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["unsupported_capability"] = "unsupported_capability"
    message: str
    provider: ModelProvider | str
    model: str
    unsupported_blocks: list[str] = Field(default_factory=list)


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class RunEventType(StrEnum):
    RUN_STARTED = "run.started"
    RUN_RESUMED = "run.resumed"
    NODE_STARTED = "node.started"
    NODE_COMPLETED = "node.completed"
    PLAN_CREATED = "plan.created"
    MODEL_DELTA = "model.delta"
    MODEL_OUTPUT = "model.output"
    TOOL_REQUESTED = "tool.requested"
    TOOL_OUTPUT = "tool.output"
    APPROVAL_REQUIRED = "approval.required"
    APPROVAL_DECIDED = "approval.decided"
    RUN_PAUSED = "run.paused"
    RUN_CANCELLED = "run.cancelled"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


class RunContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    run_id: str
    provider: ModelProvider | str
    model: str
    attempt: int = Field(default=1, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentState(TypedDict, total=False):
    """Explicit LangGraph state; values are contracts, never formatted prompts."""

    thread_id: str
    run_id: str
    provider: str
    model: str
    metadata: dict[str, Any]
    messages: list[ChatMessage]
    tool_calls: list[ToolCall]
    tool_results: list[ToolResult]
    tool_definitions: list[dict[str, Any]]
    tool_history: list[str]
    pending_approval: ApprovalRequest | None
    context_loaded: bool
    normalized: bool
    plan: list[str]
    response: ChatResponse | None
    final_text: str | None
    usage: TokenUsage
    token_usage: TokenUsage
    iteration: int
    max_iterations: int
    max_tool_calls: int
    max_tokens: int
    max_wall_time_seconds: float
    started_monotonic: float
    tool_call_count: int
    consecutive_no_progress: int
    workspace_snapshot: dict[str, Any]
    acceptance_criteria: list[str]
    verification: dict[str, Any]
    stop_reason: str | None
    status: RunStatus
    pause_reason: str | None
    cancel_requested: bool
    error: CapabilityError | dict[str, Any] | None


class CheckpointRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    run_id: str
    checkpoint_id: str
    sequence: int = Field(default=0, ge=0)


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: CheckpointRef
    state: dict[str, Any]
    next_nodes: list[str] = Field(default_factory=list)
    status: RunStatus
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1)
    run_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    provider: ModelProvider | str
    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=50)
    max_iterations: int = Field(default=20, ge=1, le=20)
    max_tool_calls: int = Field(default=60, ge=0, le=60)
    max_tokens: int = Field(default=200_000, ge=1, le=200_000)
    max_wall_time_seconds: float = Field(default=900, gt=0, le=900)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: RunContext
    status: RunStatus
    final_text: str | None = None
    events: list[RunEvent] = Field(default_factory=list)
    checkpoint: Checkpoint | None = None
    error: CapabilityError | dict[str, Any] | None = None
    tool_results: list[ToolResult] = Field(default_factory=list)
    pending_approval: ApprovalRequest | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    stop_reason: str | None = None


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    sequence: int = Field(ge=1)
    thread_id: str
    run_id: str
    type: RunEventType
    status: RunStatus
    node: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


__all__ = [
    "AdapterHealth",
    "AgentState",
    "AudioBlock",
    "CapabilityError",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "Checkpoint",
    "CheckpointRef",
    "ContentBlock",
    "ContentBlockValue",
    "DocumentBlock",
    "ImageBlock",
    "ModelCapabilities",
    "ModelProvider",
    "ModelStopReason",
    "ModelStreamEvent",
    "ModelToolCall",
    "RunContext",
    "RunEvent",
    "RunEventType",
    "RunRequest",
    "RunResult",
    "RunStatus",
    "TextBlock",
    "TokenUsage",
    "ToolResultBlock",
]
