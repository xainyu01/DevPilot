"""Deterministic test selection, parallel execution and artifact capture."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Protocol

from packages.contracts import (
    Artifact,
    RepositoryProfile,
    TestPlan,
    TestResult,
    TestTask,
    TestTaskStatus,
)


class TestExecutor(Protocol):
    def execute(self, task: TestTask, root: Path) -> TestResult:
        ...


class TestPlanner:
    """Select the smallest useful plan from repository commands and changes."""

    def plan(
        self,
        profile: RepositoryProfile,
        *,
        changed_files: list[str] | None = None,
        full: bool = False,
        parallelism: int = 2,
    ) -> TestPlan:
        changed_files = changed_files if changed_files is not None else profile.changed_files
        selected: list[str] = []
        tasks: list[TestTask] = []
        has_python_change = any(path.endswith(('.py', '.pyi')) for path in changed_files)
        for kind in ("test", "lint"):
            command = profile.commands.get(kind)
            if not command:
                continue
            if kind == "lint" and not full and not has_python_change:
                continue
            selected.append(kind)
            tasks.append(
                TestTask(
                    name=f"{kind} ({'full' if full else 'selected'})",
                    command=command,
                    kind=kind,
                    timeout_seconds=600 if kind == "test" else 300,
                    max_retries=1 if kind == "test" else 0,
                    resource_locks=["python-environment"] if kind in {"test", "lint"} else [],
                )
            )
        return TestPlan(tasks=tasks, parallelism=max(1, parallelism), selected_from=selected)


class SubprocessTestExecutor:
    """Execute an explicitly discovered argv without invoking a shell."""

    def __init__(self, *, max_output_chars: int = 200_000) -> None:
        self.max_output_chars = max_output_chars

    def execute(self, task: TestTask, root: Path) -> TestResult:
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                task.command,
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=task.timeout_seconds,
                env=_safe_environment(),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _redact(_text(exc.stdout))[: self.max_output_chars]
            stderr = _redact(_text(exc.stderr))[: self.max_output_chars]
            return TestResult(
                task_id=task.id,
                status=TestTaskStatus.TIMED_OUT,
                stdout=stdout,
                stderr=stderr,
                duration_ms=_duration(started),
                attempts=1,
                timed_out=True,
                environment={"python": sys.version.split()[0]},
            )
        except OSError as exc:
            return TestResult(
                task_id=task.id,
                status=TestTaskStatus.FAILED,
                exit_code=127,
                stderr=str(exc),
                duration_ms=_duration(started),
                environment={"python": sys.version.split()[0]},
            )
        stdout = _redact(completed.stdout)[: self.max_output_chars]
        stderr = _redact(completed.stderr)[: self.max_output_chars]
        failed_cases = sorted(set(re.findall(r"(?:FAILED|ERROR)\s+([^\s]+)", stderr + stdout)))
        return TestResult(
            task_id=task.id,
            status=(TestTaskStatus.PASSED if completed.returncode == 0 else TestTaskStatus.FAILED),
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=_duration(started),
            attempts=1,
            failed_cases=failed_cases,
            environment={"python": sys.version.split()[0]},
        )


class TestOrchestrator:
    """Run independent tasks concurrently while honoring dependencies and locks."""

    def __init__(
        self,
        root: Path,
        *,
        executor: TestExecutor | None = None,
        artifact_dir: Path | None = None,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.executor = executor or SubprocessTestExecutor()
        self.artifact_dir = (artifact_dir or self.root / ".devpilot" / "artifacts").resolve()
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self, plan: TestPlan) -> tuple[list[TestResult], list[Artifact]]:
        results: dict[str, TestResult] = {}
        artifacts: list[Artifact] = []
        pending = {task.id: task for task in plan.tasks}
        locks = {name: threading.Lock() for task in plan.tasks for name in task.resource_locks}
        while pending:
            if self._cancelled.is_set():
                for task in pending.values():
                    results[task.id] = TestResult(task_id=task.id, status=TestTaskStatus.CANCELLED)
                break
            ready: list[TestTask] = []
            for task in pending.values():
                if all(dep in results for dep in task.depends_on):
                    if any(
                        results[dep].status not in {TestTaskStatus.PASSED}
                        for dep in task.depends_on
                    ):
                        results[task.id] = TestResult(
                            task_id=task.id,
                            status=TestTaskStatus.SKIPPED,
                        )
                    else:
                        ready.append(task)
            for task_id in list(pending):
                if task_id in results:
                    pending.pop(task_id)
            if not ready:
                if pending:
                    raise ValueError("test plan contains a dependency cycle")
                break
            with ThreadPoolExecutor(max_workers=plan.parallelism) as pool:
                futures = {
                    pool.submit(self._run_task, task, locks): task
                    for task in ready
                }
                for future in as_completed(futures):
                    result = future.result()
                    results[result.task_id] = result
            for task_id in list(pending):
                if task_id in results:
                    pending.pop(task_id)
        ordered = [results[task.id] for task in plan.tasks]
        for result in ordered:
            artifacts.extend(self._save_artifacts(result))
            result.artifact_ids = [
                artifact.id for artifact in artifacts if artifact.source_ref == result.id
            ]
        return ordered, artifacts

    def _run_task(self, task: TestTask, locks: dict[str, threading.Lock]) -> TestResult:
        acquired = [locks[name] for name in sorted(task.resource_locks)]
        for lock in acquired:
            lock.acquire()
        try:
            result: TestResult | None = None
            for attempt in range(1, task.max_retries + 2):
                if self._cancelled.is_set():
                    return TestResult(
                        task_id=task.id,
                        status=TestTaskStatus.CANCELLED,
                        attempts=attempt,
                    )
                result = self.executor.execute(task, self.root)
                result.attempts = attempt
                if result.status == TestTaskStatus.PASSED:
                    break
            return result or TestResult(task_id=task.id, status=TestTaskStatus.FAILED)
        finally:
            for lock in reversed(acquired):
                lock.release()

    def _save_artifacts(self, result: TestResult) -> list[Artifact]:
        if not result.stdout and not result.stderr:
            return []
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        artifacts: list[Artifact] = []
        for suffix, content in (("stdout", result.stdout), ("stderr", result.stderr)):
            path = self.artifact_dir / f"test-{result.task_id}-{suffix}.log"
            path.write_text(content, encoding="utf-8")
            artifacts.append(
                Artifact(
                    kind=f"test_{suffix}",
                    path=str(path),
                    source_ref=result.id,
                )
            )
        return artifacts


def _duration(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _redact(value: str) -> str:
    return re.sub(
        r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s]+",
        r"\1=[REDACTED]",
        value,
    )


def _safe_environment() -> dict[str, str]:
    names = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    return {name: os.environ[name] for name in names if name in os.environ}


__all__ = ["SubprocessTestExecutor", "TestExecutor", "TestOrchestrator", "TestPlanner"]
