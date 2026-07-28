"""Isolated worktree leases for proposed implementation changes."""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from packages.contracts import IsolationMode, WorktreeLease


class WorktreeManager:
    """Create one exact, bounded lease per workflow and always release it."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.lease_root = self.root / ".codeassist" / "worktrees"

    def acquire(self, workflow_id: str) -> WorktreeLease:
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "-", workflow_id)
        path = (self.lease_root / safe_id).resolve()
        path.relative_to(self.lease_root.resolve())
        if path.exists():
            self.release_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self._is_git_repo():
            result = subprocess.run(
                ["git", "-C", str(self.root), "worktree", "add", "--detach", str(path), "HEAD"],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            if result.returncode == 0:
                mode = IsolationMode.WORKTREE
            else:
                self._copy_project(path)
                mode = IsolationMode.WORKTREE
        else:
            self._copy_project(path)
            mode = IsolationMode.WORKTREE
        return WorktreeLease(
            workflow_id=workflow_id,
            path=str(path),
            source_root=str(self.root),
            mode=mode,
        )

    def release(self, lease: WorktreeLease) -> WorktreeLease:
        path = Path(lease.path).expanduser().resolve()
        path.relative_to(self.lease_root.resolve())
        if not lease.released:
            if self._is_git_repo() and (path / ".git").exists():
                subprocess.run(
                    ["git", "-C", str(self.root), "worktree", "remove", "--force", str(path)],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=20,
                )
            self.release_path(path)
        return lease.model_copy(update={"released": True, "released_at": datetime.now(UTC)})

    def apply_text_edit(
        self,
        lease: WorktreeLease,
        *,
        file_path: str,
        old_text: str,
        new_text: str,
        approved: bool = False,
    ) -> str:
        if not approved:
            raise PermissionError("human approval is required before applying a worktree edit")
        root = Path(lease.path).expanduser().resolve()
        target = (
            (root / file_path).resolve()
            if not Path(file_path).is_absolute()
            else Path(file_path).resolve()
        )
        target.relative_to(root)
        content = target.read_text(encoding="utf-8")
        if content.count(old_text) != 1:
            raise ValueError("worktree edit must match exactly one occurrence")
        target.write_text(content.replace(old_text, new_text), encoding="utf-8")
        return target.relative_to(root).as_posix()

    def release_path(self, path: Path) -> None:
        path = path.resolve()
        path.relative_to(self.lease_root.resolve())
        if path.exists():
            shutil.rmtree(path)

    def _is_git_repo(self) -> bool:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _copy_project(self, path: Path) -> None:
        shutil.copytree(
            self.root,
            path,
            ignore=shutil.ignore_patterns(
                ".git",
                ".venv",
                ".uv-cache",
                ".pytest_cache",
                ".ruff_cache",
                ".codeassist",
                "__pycache__",
            ),
        )


__all__ = ["WorktreeManager"]
