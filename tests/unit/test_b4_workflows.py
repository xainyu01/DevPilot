from __future__ import annotations

import sys
from pathlib import Path

import pytest

from packages.contracts import (
    AgentProfile,
    AgentRole,
    AgentRunStatus,
    IssueContext,
    ModelProfile,
)
from packages.contracts import (
    TestTask as WorkflowTestTask,
)
from packages.contracts import (
    TestTaskStatus as WorkflowTestTaskStatus,
)
from packages.dev_workflows import (
    AgentLimitError,
    AgentLimits,
    AgentRunManager,
    AssignmentCompiler,
    DevelopmentWorkflowService,
)
from packages.model_gateway import ModelRouter, ModelRoutingError
from packages.persistence import (
    Database,
    ProjectRepository,
    RepositoryProfileRepository,
    WorkflowRepository,
)
from packages.repo_intel import RepositoryScanner
from packages.test_orchestrator import (
    SubprocessTestExecutor,
)
from packages.test_orchestrator import (
    TestOrchestrator as WorkflowTestOrchestrator,
)
from packages.test_orchestrator import (
    TestPlanner as WorkflowTestPlanner,
)


def test_repository_scanner_builds_profile_and_incremental_changes(tmp_path: Path) -> None:
    root = tmp_path / "sample"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\ndependencies=['fastapi>=0.1', 'pytest>=8']\n", encoding="utf-8"
    )
    (root / "src" / "bug.py").write_text("def broken():\n    return 1\n", encoding="utf-8")
    (root / "tests" / "test_bug.py").write_text(
        "def test_broken():\n    assert broken() == 2\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("keep changes focused", encoding="utf-8")
    user_home = tmp_path / "user"

    scanner = RepositoryScanner(root, user_home=user_home)
    first = scanner.scan(project_id="project-1")

    assert first.languages["python"] == 2
    assert first.frameworks == ["FastAPI", "pytest"]
    assert first.commands["test"][-2:] == ["pytest", "-q"]
    assert first.rules == [str(root / "AGENTS.md")]
    assert set(first.changed_files) == {
        "AGENTS.md",
        "pyproject.toml",
        "src/bug.py",
        "tests/test_bug.py",
    }

    (root / "src" / "bug.py").write_text("def broken():\n    return 2\n", encoding="utf-8")
    second = scanner.scan(project_id="project-1", previous=first)
    assert second.index_version == first.index_version + 1
    assert second.changed_files == ["src/bug.py"]
    assert second.removed_files == []


def test_model_router_is_deterministic_and_rejects_budget_bypass() -> None:
    router = ModelRouter(
        [
            ModelProfile(
                id="slow-quality",
                provider="fake",
                model="quality",
                capabilities=["text"],
                max_tokens=8_000,
                cost_per_1k_tokens=0.2,
                latency_ms=500,
                quality_rank=10,
            ),
            ModelProfile(
                id="cheap-fallback",
                provider="fake",
                model="cheap",
                capabilities=["text"],
                max_tokens=8_000,
                cost_per_1k_tokens=0.1,
                latency_ms=100,
                quality_rank=1,
                fallback_rank=1,
            ),
        ]
    )

    selection = router.route(
        role=AgentRole.TESTER,
        required_capabilities={"text"},
        max_tokens=4_000,
    )
    assert selection.selected.id == "slow-quality"
    assert selection.fallback_candidates == ["cheap-fallback"]
    with pytest.raises(ModelRoutingError) as error:
        router.route(role=AgentRole.TESTER, max_tokens=9_000)
    assert error.value.code == "model_budget_exceeded"


def test_assignment_compiler_intersects_parent_tools_and_paths(tmp_path: Path) -> None:
    router = ModelRouter(
        [
            ModelProfile(
                id="m",
                provider="fake",
                model="fake",
                capabilities=["text", "workspace.read"],
            )
        ]
    )
    supervisor_profile = AgentProfile(
        role=AgentRole.SUPERVISOR,
        allowed_tools=["repository.read"],
        capability_ceiling=["workspace.read"],
        max_children=1,
    )
    manager = AgentRunManager(root_path=tmp_path, limits=AgentLimits(max_depth=1))
    parent = manager.create_supervisor(
        workflow_id="workflow",
        profile=supervisor_profile,
        provider="fake",
        model="fake",
    )
    child_profile = AgentProfile(
        role=AgentRole.BUG_LOCATOR,
        allowed_tools=["repository.read"],
        capability_ceiling=["workspace.read"],
    )
    assignment, _ = AssignmentCompiler(router, root_path=tmp_path).compile(
        parent=parent,
        profile=child_profile,
        objective="locate",
        requested_tools=["repository.read"],
        worktree_path=str(tmp_path),
    )
    child = manager.create_child(
        workflow_id="workflow",
        parent_id=parent.id,
        profile=child_profile,
        assignment=assignment,
        provider="fake",
        model="fake",
    )
    assert child.depth == 1
    assert child.allowed_tools == ["repository.read"]
    with pytest.raises(AgentLimitError):
        AssignmentCompiler(router, root_path=tmp_path).compile(
            parent=parent,
            profile=child_profile,
            objective="escape",
            requested_tools=["shell.execute"],
        )
    with pytest.raises(AgentLimitError):
        manager.create_child(
            workflow_id="workflow",
            parent_id=child.id,
            profile=child_profile,
            assignment=assignment.model_copy(update={"parent_run_id": child.id}),
            provider="fake",
            model="fake",
        )
    manager.transition(child.id, AgentRunStatus.COMPLETED, reason="done")
    assert manager.get(child.id).resource_released


class FlakyExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, task: WorkflowTestTask, root: Path):
        from packages.contracts import TestResult

        self.calls += 1
        return TestResult(
            task_id=task.id,
            status=(
                WorkflowTestTaskStatus.FAILED
                if self.calls == 1
                else WorkflowTestTaskStatus.PASSED
            ),
            exit_code=1 if self.calls == 1 else 0,
            stderr="FAILED sample" if self.calls == 1 else "",
        )


def test_test_orchestrator_retries_captures_artifacts_and_times_out(tmp_path: Path) -> None:
    flaky = FlakyExecutor()
    task = WorkflowTestTask(name="flaky", command=["fake"], max_retries=1)
    results, artifacts = WorkflowTestOrchestrator(tmp_path, executor=flaky).run(
        WorkflowTestPlanner().plan(
            profile=type(
                "Profile",
                (),
                {"commands": {"test": ["fake"]}, "changed_files": ["x.py"]},
            )()
        ).model_copy(update={"tasks": [task]})
    )
    assert results[0].status == WorkflowTestTaskStatus.PASSED
    assert results[0].attempts == 2
    assert artifacts == []

    timeout_task = WorkflowTestTask(
        name="timeout",
        command=[sys.executable, "-c", "import time; time.sleep(0.05)"],
        timeout_seconds=0.001,
    )
    timeout = SubprocessTestExecutor().execute(timeout_task, tmp_path)
    assert timeout.status == WorkflowTestTaskStatus.TIMED_OUT
    assert timeout.timed_out


def test_workflow_mvp_is_traceable_and_releases_worktree(tmp_path: Path) -> None:
    root = tmp_path / "bug-repo"
    root.mkdir()
    (root / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "test_app.py").write_text(
        "from app import add\n\ndef test_add():\n    assert add(2, 1) == 3\n", encoding="utf-8"
    )
    database = Database("sqlite://")
    database.create_all()
    from packages.contracts import ProjectRecord

    project = ProjectRepository(database).create(
        ProjectRecord(name="bug-repo", root_path=str(root))
    )
    workflow = DevelopmentWorkflowService(
        project_id=project.id,
        project_root=root,
        profile_store=RepositoryProfileRepository(database),
        workflow_store=WorkflowRepository(database),
    ).run(
        IssueContext(description="add returns the wrong result", failing_tests=["test_add"]),
        create_worktree=True,
    )

    assert workflow.status.value == "completed"
    assert workflow.hypotheses[0].file_path in {"app.py", "test_app.py"}
    assert len(workflow.agent_runs) == 3
    assert all(run.status in {AgentRunStatus.COMPLETED} for run in workflow.agent_runs)
    assert all(run.resource_released for run in workflow.agent_runs)
    assert workflow.leases[0].released
    assert workflow.pr_document is not None
    assert Path(workflow.pr_document.markdown_path or "").is_file()
    restored = WorkflowRepository(database).get(workflow.id)
    assert restored is not None
    assert [event.sequence for event in restored.events] == list(range(1, len(restored.events) + 1))
