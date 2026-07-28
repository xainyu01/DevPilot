"""Supervisor assignments and bounded AgentRun lifecycle management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from packages.contracts import (
    AgentAssignment,
    AgentProfile,
    AgentRole,
    AgentRunState,
    AgentRunStatus,
    IsolationMode,
)
from packages.model_gateway import ModelRouter


class AgentLimitError(ValueError):
    """Raised when a child assignment would exceed a hard server limit."""


@dataclass(frozen=True)
class AgentLimits:
    max_depth: int = 1
    max_concurrent_children: int = 4
    max_total_children: int = 8
    max_global_active: int = 16


_ACTIVE = {
    AgentRunStatus.PENDING,
    AgentRunStatus.RUNNING,
    AgentRunStatus.PAUSED,
    AgentRunStatus.WAITING_APPROVAL,
}
_TERMINAL = {
    AgentRunStatus.COMPLETED,
    AgentRunStatus.FAILED,
    AgentRunStatus.CANCELLED,
    AgentRunStatus.EXPIRED,
}
_TRANSITIONS = {
    AgentRunStatus.PENDING: {AgentRunStatus.RUNNING, AgentRunStatus.CANCELLED},
    AgentRunStatus.RUNNING: {
        AgentRunStatus.PAUSED,
        AgentRunStatus.WAITING_APPROVAL,
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.EXPIRED,
    },
    AgentRunStatus.PAUSED: {
        AgentRunStatus.RUNNING,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.EXPIRED,
    },
    AgentRunStatus.WAITING_APPROVAL: {
        AgentRunStatus.RUNNING,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.EXPIRED,
    },
}


class AssignmentCompiler:
    """Compile model output into the intersection of parent and role policy."""

    def __init__(self, router: ModelRouter, *, root_path: Path) -> None:
        self.router = router
        self.root_path = root_path.expanduser().resolve()

    def compile(
        self,
        *,
        parent: AgentRunState,
        profile: AgentProfile,
        objective: str,
        input_refs: list[str] | None = None,
        requested_tools: list[str] | None = None,
        requested_capabilities: list[str] | None = None,
        worktree_path: str | None = None,
        max_tokens: int | None = None,
        max_wall_time_seconds: float | None = None,
    ) -> tuple[AgentAssignment, object]:
        tools = requested_tools if requested_tools is not None else profile.allowed_tools
        capabilities = (
            requested_capabilities
            if requested_capabilities is not None
            else profile.capability_ceiling
        )
        if not set(tools).issubset(parent.allowed_tools):
            raise AgentLimitError("assignment requests a tool outside the parent grant")
        if not set(capabilities).issubset(parent.effective_capabilities):
            raise AgentLimitError("assignment requests a capability outside the parent grant")
        selected_path = _validate_child_path(
            self.root_path,
            parent.worktree_path,
            worktree_path,
        )
        token_budget = min(max_tokens or profile.max_tokens, profile.max_tokens, parent.max_tokens)
        wall_budget = min(
            max_wall_time_seconds or profile.max_wall_time_seconds,
            profile.max_wall_time_seconds,
            parent.max_wall_time_seconds,
        )
        selection = self.router.route(
            role=profile.role,
            required_capabilities=set(capabilities),
            allowed_profiles=profile.allowed_model_profiles,
            max_tokens=token_budget,
        )
        assignment = AgentAssignment(
            parent_run_id=parent.id,
            agent_role=profile.role,
            objective=objective,
            input_refs=input_refs or [],
            model_profile=selection.selected.id,
            allowed_tools=sorted(set(tools)),
            capability_ceiling=sorted(set(capabilities)),
            worktree_path=selected_path,
            allowed_paths=[selected_path] if selected_path else [],
            max_tokens=token_budget,
            max_wall_time_seconds=wall_budget,
            max_children=profile.max_children,
            isolation_mode=(
                IsolationMode.WORKTREE if selected_path else IsolationMode.SHARED_READ_ONLY
            ),
        )
        return assignment, selection


class AgentRunManager:
    """In-memory runtime registry; callers persist the returned workflow snapshot."""

    def __init__(self, *, root_path: Path, limits: AgentLimits | None = None) -> None:
        self.root_path = root_path.expanduser().resolve()
        self.limits = limits or AgentLimits()
        self.runs: dict[str, AgentRunState] = {}

    def create_supervisor(
        self,
        *,
        workflow_id: str,
        profile: AgentProfile,
        provider: str,
        model: str,
    ) -> AgentRunState:
        if self._active_count() >= self.limits.max_global_active:
            raise AgentLimitError("global active Agent limit reached")
        run = AgentRunState(
            workflow_id=workflow_id,
            profile_id=profile.id,
            role=AgentRole.SUPERVISOR,
            status=AgentRunStatus.RUNNING,
            depth=0,
            actual_provider=provider,
            actual_model=model,
            effective_capabilities=sorted(profile.capability_ceiling),
            allowed_tools=sorted(profile.allowed_tools),
            max_tokens=profile.max_tokens,
            max_wall_time_seconds=profile.max_wall_time_seconds,
            max_children=profile.max_children,
        )
        self.runs[run.id] = run
        return run

    def create_child(
        self,
        *,
        workflow_id: str,
        parent_id: str,
        profile: AgentProfile,
        assignment: AgentAssignment,
        provider: str,
        model: str,
    ) -> AgentRunState:
        parent = self.get(parent_id)
        if parent.workflow_id != workflow_id:
            raise AgentLimitError("parent run belongs to another workflow")
        if parent.depth >= self.limits.max_depth:
            raise AgentLimitError("maximum Agent recursion depth reached")
        if self._active_count() >= self.limits.max_global_active:
            raise AgentLimitError("global active Agent limit reached")
        children = [item for item in self.runs.values() if item.parent_run_id == parent_id]
        if len(children) >= self.limits.max_total_children:
            raise AgentLimitError("maximum child Agent count reached")
        if parent.max_children and len(children) >= parent.max_children:
            raise AgentLimitError("parent Agent child limit reached")
        active_children = [item for item in children if item.status in _ACTIVE]
        if len(active_children) >= self.limits.max_concurrent_children:
            raise AgentLimitError("maximum concurrent child Agent count reached")
        if assignment.parent_run_id != parent_id:
            raise AgentLimitError("assignment parent does not match requested parent")
        run = AgentRunState(
            workflow_id=workflow_id,
            parent_run_id=parent_id,
            profile_id=profile.id,
            role=profile.role,
            status=AgentRunStatus.RUNNING,
            depth=parent.depth + 1,
            actual_provider=provider,
            actual_model=model,
            assignment=assignment,
            effective_capabilities=assignment.capability_ceiling,
            allowed_tools=assignment.allowed_tools,
            worktree_path=assignment.worktree_path,
            max_tokens=assignment.max_tokens,
            max_wall_time_seconds=assignment.max_wall_time_seconds,
            max_children=profile.max_children,
        )
        self.runs[run.id] = run
        return run

    def get(self, run_id: str) -> AgentRunState:
        try:
            return self.runs[run_id]
        except KeyError as exc:
            raise KeyError(f"agent run not found: {run_id}") from exc

    def transition(
        self,
        run_id: str,
        status: AgentRunStatus,
        *,
        reason: str | None = None,
        checkpoint_ref: str | None = None,
    ) -> AgentRunState:
        run = self.get(run_id)
        if run.status != status and status not in _TRANSITIONS.get(run.status, set()):
            raise AgentLimitError(f"invalid AgentRun transition {run.status.value}->{status.value}")
        now = datetime.now(UTC)
        updated = run.model_copy(
            update={
                "status": status,
                "termination_reason": reason or run.termination_reason,
                "checkpoint_ref": checkpoint_ref or run.checkpoint_ref,
                "updated_at": now,
                "resource_released": status in _TERMINAL,
            }
        )
        self.runs[run_id] = updated
        return updated

    def tree(self, workflow_id: str) -> list[AgentRunState]:
        return sorted(
            (run for run in self.runs.values() if run.workflow_id == workflow_id),
            key=lambda item: (item.depth, item.created_at, item.id),
        )

    def _active_count(self) -> int:
        return sum(run.status in _ACTIVE for run in self.runs.values())


def _validate_child_path(
    root_path: Path,
    parent_path: str | None,
    child_path: str | None,
) -> str | None:
    if child_path is None:
        return parent_path
    path = Path(child_path).expanduser().resolve()
    try:
        path.relative_to(root_path)
        if parent_path is not None:
            path.relative_to(Path(parent_path).expanduser().resolve())
    except ValueError as exc:
        raise AgentLimitError("assignment worktree is outside the parent worktree") from exc
    return str(path)


__all__ = ["AgentLimitError", "AgentLimits", "AgentRunManager", "AssignmentCompiler"]
