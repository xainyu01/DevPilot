"""Discover scoped Agent instructions without depending on a web framework."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from packages.contracts import ProjectContext, ProjectRule


class RuleRepositoryProtocol(Protocol):
    def replace(self, project_id: str, rules: list[ProjectRule]) -> list[ProjectRule]:
        ...


class RuleDiscovery:
    """Find the B3 rule files and assign deterministic precedence.

    A larger priority means a more specific rule.  Results are returned from
    broad to specific, which makes the merged text easy to inspect and lets
    later rules override earlier guidance.
    """

    def __init__(self, *, user_home: Path | None = None, max_bytes: int = 256_000) -> None:
        self.user_home = (user_home or Path.home()).expanduser().resolve()
        self.max_bytes = max_bytes

    def discover(
        self,
        project_root: Path,
        *,
        current_dir: Path | None = None,
        project_id: str = "unpersisted-project",
        include_user_memory: bool = True,
    ) -> ProjectContext:
        root = project_root.expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(root)
        current = (current_dir or root).expanduser().resolve()
        try:
            current.relative_to(root)
        except ValueError as exc:
            raise ValueError("current_dir must be inside project_root") from exc

        candidates: list[tuple[Path, int]] = []
        if include_user_memory:
            candidates.append((self.user_home / ".codeassist" / "MEMORY.md", 0))

        ancestors = list(reversed([current, *current.parents]))
        ancestors = [
            directory
            for directory in ancestors
            if directory == root or root in directory.parents
        ]
        for distance, directory in enumerate(ancestors):
            priority = 10 + distance * 10
            for filename in ("AGENTS.md", "CLAUDE.md"):
                candidates.append((directory / filename, priority))
            codeassist_dir = directory / ".codeassist"
            if codeassist_dir.is_dir():
                for path in sorted(codeassist_dir.glob("*.md"), key=lambda item: item.name.lower()):
                    candidates.append((path, priority + 5))

        rules: list[ProjectRule] = []
        seen: set[Path] = set()
        for path, priority in candidates:
            path = path.resolve()
            if path in seen or not path.is_file() or path.stat().st_size > self.max_bytes:
                continue
            seen.add(path)
            rules.append(
                ProjectRule(
                    project_id=project_id,
                    source_path=str(path),
                    scope_path=str(path.parent),
                    filename=path.name,
                    content=path.read_text(encoding="utf-8", errors="replace"),
                    priority=priority,
                    source_kind=_source_kind(path),
                )
            )
        rules.sort(key=lambda item: (item.priority, item.source_path))
        return ProjectContext(project_root=str(root), rules=rules)


class ProjectContextService:
    """Combine filesystem discovery with the database rule index."""

    def __init__(self, discovery: RuleDiscovery | None = None) -> None:
        self.discovery = discovery or RuleDiscovery()

    def discover_and_store(
        self,
        *,
        project_id: str,
        project_root: Path,
        repository: RuleRepositoryProtocol,
        current_dir: Path | None = None,
    ) -> ProjectContext:
        context = self.discovery.discover(
            project_root,
            current_dir=current_dir,
            project_id=project_id,
        )
        repository.replace(project_id, context.rules)
        return context


def _source_kind(path: Path) -> str:
    filename = path.name.upper()
    if filename == "AGENTS.MD":
        return "agent"
    if filename == "CLAUDE.MD":
        return "claude"
    if filename == "PROJECT.MD":
        return "project"
    if filename == "MEMORY.MD":
        return "user_memory" if path.parent.name == ".codeassist" else "memory"
    return "other"


__all__ = ["ProjectContextService", "RuleDiscovery"]
