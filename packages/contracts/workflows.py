"""Structured contracts for repository intelligence and development workflows."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class WorkflowStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class AgentRole(StrEnum):
    SUPERVISOR = "supervisor"
    REPOSITORY_SCANNER = "repository_scanner"
    BUG_LOCATOR = "bug_locator"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    TESTER = "tester"
    RESEARCHER = "researcher"


class EvidenceKind(StrEnum):
    ISSUE = "issue"
    LOG = "log"
    FAILED_TEST = "failed_test"
    REPOSITORY = "repository"
    SEARCH = "search"
    TRACEBACK = "traceback"
    COMMAND = "command"
    DIFF = "diff"
    TEST_RESULT = "test_result"


class HypothesisStatus(StrEnum):
    OPEN = "open"
    VERIFIED = "verified"
    REJECTED = "rejected"
    UNRESOLVED = "unresolved"


class AgentRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class IsolationMode(StrEnum):
    SHARED_READ_ONLY = "shared_read_only"
    WORKTREE = "worktree"


class TestTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class ReviewStatus(StrEnum):
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"


class RepositoryFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    size: int = Field(ge=0)
    mtime_ns: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    language: str = "unknown"


class RepositoryProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str | None = None
    root_path: str = Field(min_length=1)
    languages: dict[str, int] = Field(default_factory=dict)
    frameworks: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    commands: dict[str, list[str]] = Field(default_factory=dict)
    rules: list[str] = Field(default_factory=list)
    files: list[RepositoryFile] = Field(default_factory=list)
    symbols: dict[str, list[str]] = Field(default_factory=dict)
    git: dict[str, Any] = Field(default_factory=dict)
    index_version: int = Field(default=1, ge=1)
    changed_files: list[str] = Field(default_factory=list)
    removed_files: list[str] = Field(default_factory=list)
    indexed_at: datetime = Field(default_factory=utc_now)


class IssueContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    logs: str = ""
    failing_tests: list[str] = Field(default_factory=list)
    attachments: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: EvidenceKind
    source: str = Field(min_length=1)
    locator: str | None = None
    content: str = ""
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    created_at: datetime = Field(default_factory=utc_now)


class BugHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    root_cause: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    status: HypothesisStatus = HypothesisStatus.OPEN
    file_path: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    symbol: str | None = None
    verification_steps: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


class AgentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    role: AgentRole
    prompt_ref: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    capability_ceiling: list[str] = Field(default_factory=list)
    allowed_model_profiles: list[str] = Field(default_factory=list)
    max_tokens: int = Field(default=8_000, ge=1)
    max_wall_time_seconds: float = Field(default=300, gt=0)
    max_children: int = Field(default=0, ge=0)


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=lambda: ["text"])
    max_tokens: int = Field(default=8_000, ge=1)
    cost_per_1k_tokens: float = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    quality_rank: int = Field(default=0, ge=0)
    fallback_rank: int = Field(default=0, ge=0)
    healthy: bool = True
    allowed_roles: list[AgentRole] = Field(default_factory=list)
    privacy_level: str = "standard"


class ModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected: ModelProfile
    fallback_candidates: list[str] = Field(default_factory=list)
    reason: str


class AgentAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    parent_run_id: str
    agent_role: AgentRole
    objective: str = Field(min_length=1)
    input_refs: list[str] = Field(default_factory=list)
    model_profile: str
    allowed_tools: list[str] = Field(default_factory=list)
    capability_ceiling: list[str] = Field(default_factory=list)
    worktree_path: str | None = None
    allowed_paths: list[str] = Field(default_factory=list)
    max_tokens: int = Field(ge=1)
    max_wall_time_seconds: float = Field(gt=0)
    max_children: int = Field(default=0, ge=0)
    approval_requirements: list[str] = Field(default_factory=list)
    isolation_mode: IsolationMode = IsolationMode.SHARED_READ_ONLY


class AgentRunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    agent_id: str = Field(default_factory=lambda: str(uuid4()))
    workflow_id: str
    parent_run_id: str | None = None
    profile_id: str
    role: AgentRole
    status: AgentRunStatus = AgentRunStatus.PENDING
    depth: int = Field(default=0, ge=0)
    actual_provider: str | None = None
    actual_model: str | None = None
    assignment: AgentAssignment | None = None
    effective_capabilities: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    worktree_path: str | None = None
    max_tokens: int = Field(default=8_000, ge=1)
    max_wall_time_seconds: float = Field(default=300, gt=0)
    max_children: int = Field(default=0, ge=0)
    termination_reason: str | None = None
    resource_released: bool = False
    checkpoint_ref: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WorktreeLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    workflow_id: str
    path: str
    source_root: str
    mode: IsolationMode
    released: bool = False
    acquired_at: datetime = Field(default_factory=utc_now)
    released_at: datetime | None = None


class DevelopmentTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    workflow_id: str
    objective: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    worktree_path: str | None = None
    worktree_lease_id: str | None = None
    worktree_released: bool = False
    approval_required: bool = True
    subtask_ids: list[str] = Field(default_factory=list)
    budget_tokens: int = Field(default=8_000, ge=1)
    budget_wall_time_seconds: float = Field(default=300, gt=0)


class TestTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    command: list[str] = Field(min_length=1)
    kind: str = "custom"
    depends_on: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=300, gt=0)
    max_retries: int = Field(default=0, ge=0)
    resource_locks: list[str] = Field(default_factory=list)
    status: TestTaskStatus = TestTaskStatus.PENDING


class TestPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    tasks: list[TestTask] = Field(default_factory=list)
    parallelism: int = Field(default=2, ge=1)
    selected_from: list[str] = Field(default_factory=list)


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: str
    path: str
    media_type: str = "text/plain"
    source_ref: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class TestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    status: TestTaskStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = Field(default=0, ge=0)
    attempts: int = Field(default=1, ge=1)
    timed_out: bool = False
    failed_cases: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)


class PRDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    summary: str
    body: str
    changed_files: list[str] = Field(default_factory=list)
    test_result_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    rollback: str = "Revert the generated change set and restore the previous worktree."
    checklist: list[str] = Field(default_factory=list)
    review_status: ReviewStatus = ReviewStatus.DRAFT
    markdown_path: str | None = None
    generated_at: datetime = Field(default_factory=utc_now)


class WorkflowEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class WorkflowRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    issue: IssueContext
    repository_profile_id: str | None = None
    supervisor_run_id: str | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    hypotheses: list[BugHypothesis] = Field(default_factory=list)
    development_task: DevelopmentTask | None = None
    leases: list[WorktreeLease] = Field(default_factory=list)
    agent_profiles: list[AgentProfile] = Field(default_factory=list)
    agent_runs: list[AgentRunState] = Field(default_factory=list)
    test_plan: TestPlan | None = None
    test_results: list[TestResult] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    pr_document: PRDocument | None = None
    events: list[WorkflowEvent] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


__all__ = [
    "AgentAssignment",
    "AgentProfile",
    "AgentRole",
    "AgentRunState",
    "AgentRunStatus",
    "Artifact",
    "BugHypothesis",
    "DevelopmentTask",
    "EvidenceItem",
    "EvidenceKind",
    "HypothesisStatus",
    "IssueContext",
    "IsolationMode",
    "ModelProfile",
    "ModelSelection",
    "PRDocument",
    "RepositoryFile",
    "RepositoryProfile",
    "ReviewStatus",
    "TestPlan",
    "TestResult",
    "TestTask",
    "TestTaskStatus",
    "WorkflowEvent",
    "WorkflowRun",
    "WorkflowStatus",
    "WorktreeLease",
]
