"""Shared preparation boundary for REST and WebSocket Agent runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from packages.contracts import (
    ChatMessage,
    MemoryEntry,
    ProjectRecord,
    ProjectRule,
    RepositoryProfile,
    RunRequest,
    SessionSummary,
    StoredMessage,
    ToolDefinition,
)

from .context import ContextAssembler
from .graph import AgentRuntime


@dataclass(frozen=True)
class PreparedRun:
    runtime: AgentRuntime
    request: RunRequest


class RunCoordinator:
    """Build an identical bounded request for every application transport."""

    def __init__(self, context_assembler: ContextAssembler | None = None) -> None:
        self.context_assembler = context_assembler or ContextAssembler()

    def prepare(
        self,
        *,
        runtime: AgentRuntime,
        thread_id: str,
        run_id: str,
        provider: str,
        model: str,
        current_message: ChatMessage,
        history: list[StoredMessage],
        metadata: dict[str, Any],
        acceptance_criteria: list[str],
        project: ProjectRecord | None = None,
        rules: list[ProjectRule] | None = None,
        repository_profile: RepositoryProfile | None = None,
        workspace_diff: str = "",
        memories: list[MemoryEntry] | None = None,
        summary: SessionSummary | None = None,
        capabilities: set[str] | None = None,
        tools: list[ToolDefinition] | None = None,
        model_policy: dict[str, Any] | None = None,
        max_context_tokens: int = 64_000,
        max_run_tokens: int = 200_000,
    ) -> PreparedRun:
        assembly = self.context_assembler.assemble(
            current_message=current_message,
            history=history,
            project=project,
            rules=rules,
            repository_profile=repository_profile,
            workspace_diff=workspace_diff,
            memories=memories,
            summary=summary,
            capabilities=capabilities,
            tools=tools,
            model_policy=model_policy,
            acceptance_criteria=acceptance_criteria,
            remaining_budget={
                "context_tokens": max_context_tokens,
                "run_tokens": max_run_tokens,
            },
            max_tokens=max_context_tokens,
        )
        return PreparedRun(
            runtime=runtime,
            request=RunRequest(
                thread_id=thread_id,
                run_id=run_id,
                provider=provider,
                model=model,
                messages=assembly.messages,
                acceptance_criteria=acceptance_criteria,
                max_tokens=max_run_tokens,
                metadata={**metadata, **assembly.metadata},
            ),
        )


__all__ = ["PreparedRun", "RunCoordinator"]
