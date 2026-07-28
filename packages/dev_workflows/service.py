"""End-to-end deterministic workflow MVP joining B4 domain services."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from packages.contracts import (
    AgentProfile,
    AgentRole,
    AgentRunStatus,
    Artifact,
    DevelopmentTask,
    IssueContext,
    WorkflowEvent,
    WorkflowRun,
    WorkflowStatus,
)
from packages.model_gateway import ModelRouter, default_model_router
from packages.pr_docs import PRDocumentGenerator
from packages.repo_intel import RepositoryScanner
from packages.test_orchestrator import TestOrchestrator, TestPlanner

from .lifecycle import AgentLimits, AgentRunManager, AssignmentCompiler
from .locator import BugLocator
from .worktree import WorktreeManager


class ProfileStore(Protocol):
    def get(self, project_id: str):
        ...

    def save(self, profile):
        ...


class WorkflowStore(Protocol):
    def save(self, workflow: WorkflowRun) -> WorkflowRun:
        ...


class DevelopmentWorkflowService:
    """Run repository scan, evidence collection, tests and PR draft generation."""

    def __init__(
        self,
        *,
        project_id: str,
        project_root: Path,
        profile_store: ProfileStore | None = None,
        workflow_store: WorkflowStore | None = None,
        router: ModelRouter | None = None,
        limits: AgentLimits | None = None,
    ) -> None:
        self.project_id = project_id
        self.project_root = project_root.expanduser().resolve()
        self.profile_store = profile_store
        self.workflow_store = workflow_store
        self.router = router or default_model_router()
        self.limits = limits or AgentLimits()
        self.manager: AgentRunManager | None = None
        self._leases: dict[str, WorktreeManager] = {}

    def run(
        self,
        issue: IssueContext,
        *,
        execute_tests: bool = False,
        create_worktree: bool = False,
        full_tests: bool = False,
    ) -> WorkflowRun:
        workflow = WorkflowRun(
            project_id=self.project_id,
            issue=issue,
            status=WorkflowStatus.RUNNING,
        )
        manager = AgentRunManager(root_path=self.project_root, limits=self.limits)
        self.manager = manager
        self._event(workflow, "workflow.started", {"workflow_id": workflow.id})
        self._save(workflow)
        lease_manager = WorktreeManager(self.project_root)
        try:
            previous = self.profile_store.get(self.project_id) if self.profile_store else None
            profile = RepositoryScanner(self.project_root).scan(
                project_id=self.project_id,
                previous=previous,
            )
            if self.profile_store:
                self.profile_store.save(profile)
            workflow.repository_profile_id = profile.id
            self._event(
                workflow,
                "repository.indexed",
                {
                    "profile_id": profile.id,
                    "index_version": profile.index_version,
                    "changed_files": profile.changed_files,
                    "removed_files": profile.removed_files,
                },
            )

            supervisor_profile = AgentProfile(
                role=AgentRole.SUPERVISOR,
                allowed_tools=["workflow.read", "workflow.delegate", "repository.read"],
                capability_ceiling=["workspace.read"],
                max_tokens=12_000,
                max_wall_time_seconds=600,
                max_children=4,
            )
            workflow.agent_profiles.append(supervisor_profile)
            supervisor_selection = self.router.route(
                role=AgentRole.SUPERVISOR,
                required_capabilities={"text", "workspace.read"},
                max_tokens=supervisor_profile.max_tokens,
            )
            supervisor = manager.create_supervisor(
                workflow_id=workflow.id,
                profile=supervisor_profile,
                provider=supervisor_selection.selected.provider,
                model=supervisor_selection.selected.model,
            )
            workflow.supervisor_run_id = supervisor.id
            workflow.agent_runs = manager.tree(workflow.id)
            self._event(workflow, "agent.created", {"run_id": supervisor.id, "role": "supervisor"})

            compiler = AssignmentCompiler(self.router, root_path=self.project_root)
            scanner_profile = AgentProfile(
                role=AgentRole.REPOSITORY_SCANNER,
                allowed_tools=["repository.read"],
                capability_ceiling=["workspace.read"],
                max_tokens=8_000,
                max_wall_time_seconds=300,
            )
            workflow.agent_profiles.append(scanner_profile)
            assignment, selection = compiler.compile(
                parent=supervisor,
                profile=scanner_profile,
                objective="Index repository files, ecosystem metadata, commands and rules.",
                input_refs=[profile.id],
            )
            scanner_run = manager.create_child(
                workflow_id=workflow.id,
                parent_id=supervisor.id,
                profile=scanner_profile,
                assignment=assignment,
                provider=selection.selected.provider,
                model=selection.selected.model,
            )
            self._event(
                workflow,
                "agent.started",
                {"run_id": scanner_run.id, "role": scanner_run.role.value},
            )
            manager.transition(
                scanner_run.id,
                AgentRunStatus.COMPLETED,
                reason="repository profile indexed",
            )
            self._event(workflow, "agent.completed", {"run_id": scanner_run.id})

            locator_profile = AgentProfile(
                role=AgentRole.BUG_LOCATOR,
                allowed_tools=["repository.read"],
                capability_ceiling=["workspace.read"],
                max_tokens=8_000,
                max_wall_time_seconds=300,
            )
            workflow.agent_profiles.append(locator_profile)
            assignment, selection = compiler.compile(
                parent=supervisor,
                profile=locator_profile,
                objective="Locate the reported bug and return evidence-backed hypotheses.",
                input_refs=[item.id for item in workflow.evidence],
            )
            locator_run = manager.create_child(
                workflow_id=workflow.id,
                parent_id=supervisor.id,
                profile=locator_profile,
                assignment=assignment,
                provider=selection.selected.provider,
                model=selection.selected.model,
            )
            self._event(
                workflow,
                "agent.started",
                {"run_id": locator_run.id, "role": locator_run.role.value},
            )
            locator = BugLocator(self.project_root)
            evidence, hypotheses = locator.locate(issue, profile)
            workflow.evidence = evidence
            workflow.hypotheses = hypotheses
            manager.transition(
                locator_run.id,
                AgentRunStatus.COMPLETED,
                reason="hypothesis generated",
            )
            self._event(
                workflow,
                "bug.hypothesis.created",
                {
                    "hypothesis_ids": [item.id for item in hypotheses],
                    "evidence_count": len(evidence),
                },
            )

            if create_worktree:
                lease = lease_manager.acquire(workflow.id)
                self._leases[workflow.id] = lease_manager
                workflow.leases.append(lease)
                workflow.development_task = DevelopmentTask(
                    workflow_id=workflow.id,
                    objective="Review and, after approval, apply the proposed fix in isolation.",
                    status=WorkflowStatus.COMPLETED,
                    worktree_path=lease.path,
                    worktree_lease_id=lease.id,
                    approval_required=True,
                    budget_tokens=12_000,
                    budget_wall_time_seconds=600,
                )
                self._event(
                    workflow,
                    "worktree.acquired",
                    {"lease_id": lease.id, "path": lease.path},
                )

            test_plan = TestPlanner().plan(
                profile,
                changed_files=profile.changed_files,
                full=full_tests,
            )
            workflow.test_plan = test_plan
            self._event(
                workflow,
                "test.plan.created",
                {
                    "task_ids": [task.id for task in test_plan.tasks],
                    "selected_from": test_plan.selected_from,
                },
            )
            if execute_tests and test_plan.tasks:
                results, artifacts = TestOrchestrator(self.project_root).run(test_plan)
                workflow.test_results = results
                workflow.artifacts.extend(artifacts)
                self._event(
                    workflow,
                    "test.completed",
                    {"results": [result.model_dump(mode="json") for result in results]},
                )
            document = PRDocumentGenerator(self.project_root).generate(
                workflow,
                profile=profile,
                test_results=workflow.test_results,
            )
            document = PRDocumentGenerator(self.project_root).export(document)
            workflow.pr_document = document
            if document.markdown_path:
                workflow.artifacts.append(
                    Artifact(
                        kind="pr_document",
                        path=document.markdown_path,
                        media_type="text/markdown",
                        source_ref=document.id,
                    )
                )
            self._event(workflow, "pr_document.generated", {"document_id": document.id})
            if workflow.leases:
                workflow.leases[-1] = lease_manager.release(workflow.leases[-1])
                if workflow.development_task:
                    workflow.development_task = workflow.development_task.model_copy(
                        update={"worktree_released": True}
                    )
                self._event(workflow, "worktree.released", {"lease_id": workflow.leases[-1].id})
            manager.transition(supervisor.id, AgentRunStatus.COMPLETED, reason="workflow completed")
            workflow.agent_runs = manager.tree(workflow.id)
            workflow.status = WorkflowStatus.COMPLETED
            self._event(workflow, "workflow.completed", {"pr_document_id": document.id})
        except Exception as exc:
            if workflow.leases and not workflow.leases[-1].released:
                workflow.leases[-1] = lease_manager.release(workflow.leases[-1])
            workflow.error = str(exc)
            workflow.status = WorkflowStatus.FAILED
            self._event(workflow, "workflow.failed", {"error": str(exc)})
            if workflow.supervisor_run_id and workflow.supervisor_run_id in manager.runs:
                manager.transition(
                    workflow.supervisor_run_id,
                    AgentRunStatus.FAILED,
                    reason=str(exc),
                )
            workflow.agent_runs = manager.tree(workflow.id)
        workflow.updated_at = datetime.now(UTC)
        self._save(workflow)
        return workflow

    def apply_approved_edit(
        self,
        workflow: WorkflowRun,
        *,
        file_path: str,
        old_text: str,
        new_text: str,
        approved: bool = False,
    ) -> WorkflowRun:
        if not workflow.leases:
            raise ValueError("workflow has no isolated worktree lease")
        manager = self._leases.get(workflow.id) or WorktreeManager(self.project_root)
        manager.apply_text_edit(
            workflow.leases[-1],
            file_path=file_path,
            old_text=old_text,
            new_text=new_text,
            approved=approved,
        )
        self._event(workflow, "fix.applied", {"file_path": file_path})
        workflow.updated_at = datetime.now(UTC)
        self._save(workflow)
        return workflow

    def _event(self, workflow: WorkflowRun, event_type: str, data: dict) -> None:
        workflow.events.append(
            WorkflowEvent(sequence=len(workflow.events) + 1, type=event_type, data=data)
        )
        workflow.updated_at = datetime.now(UTC)

    def _save(self, workflow: WorkflowRun) -> None:
        if self.workflow_store:
            self.workflow_store.save(workflow)


__all__ = ["DevelopmentWorkflowService"]
