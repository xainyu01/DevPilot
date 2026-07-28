"""Deterministic, dependency-light repository intelligence."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any
from uuid import uuid4

from packages.contracts import ProjectContext, RepositoryFile, RepositoryProfile
from packages.project_context import RuleDiscovery

_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".uv-cache",
    ".devpilot",
    "dist",
    "build",
    "target",
}
_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cs": "csharp",
}
_SYMBOL_PATTERNS = {
    "python": re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", re.MULTILINE),
    "javascript": re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)",
        re.MULTILINE,
    ),
    "typescript": re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)",
        re.MULTILINE,
    ),
    "rust": re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)", re.MULTILINE),
    "go": re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)", re.MULTILINE),
}


class RepositoryScanner:
    """Build a bounded repository profile and reuse unchanged file metadata."""

    def __init__(
        self,
        root: Path,
        *,
        user_home: Path | None = None,
        max_files: int = 20_000,
        max_file_bytes: int = 1_000_000,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.rule_discovery = RuleDiscovery(user_home=user_home)

    def scan(
        self,
        *,
        project_id: str | None = None,
        previous: RepositoryProfile | None = None,
        persist: bool = True,
    ) -> RepositoryProfile:
        if not self.root.is_dir():
            raise NotADirectoryError(self.root)
        previous = previous or load_index(self.root)
        previous_by_path = {item.path: item for item in (previous.files if previous else [])}
        files: list[RepositoryFile] = []
        symbols: dict[str, list[str]] = {}
        file_count = 0
        for path in sorted(self._iter_files(), key=lambda item: item.as_posix().lower()):
            if file_count >= self.max_files:
                break
            relative = path.relative_to(self.root).as_posix()
            stat = path.stat()
            language = _LANGUAGES.get(path.suffix.lower(), "unknown")
            cached = previous_by_path.get(relative)
            if (
                cached is not None
                and cached.size == stat.st_size
                and cached.mtime_ns == stat.st_mtime_ns
            ):
                sha256 = cached.sha256
            else:
                sha256 = _sha256(path)
            record = RepositoryFile(
                path=relative,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                sha256=sha256,
                language=language,
            )
            files.append(record)
            file_count += 1
            if language != "unknown" and stat.st_size <= self.max_file_bytes:
                symbols[relative] = _symbols(path, language)

        current_paths = {item.path for item in files}
        previous_paths = set(previous_by_path)
        changed = sorted(
            item.path
            for item in files
            if previous_by_path.get(item.path) is None
            or previous_by_path[item.path].sha256 != item.sha256
        )
        removed = sorted(previous_paths - current_paths)
        languages = _counts(item.language for item in files if item.language != "unknown")
        frameworks, package_managers = _detect_ecosystem(self.root)
        commands = _discover_commands(self.root, languages, package_managers)
        context: ProjectContext = self.rule_discovery.discover(
            self.root,
            project_id=project_id or "unpersisted-project",
        )
        profile = RepositoryProfile(
            id=previous.id if previous else str(uuid4()),
            project_id=project_id,
            root_path=str(self.root),
            languages=languages,
            frameworks=frameworks,
            package_managers=package_managers,
            commands=commands,
            rules=[rule.source_path for rule in context.rules],
            files=files,
            symbols=symbols,
            git=_git_profile(self.root),
            index_version=(previous.index_version + 1 if previous else 1),
            changed_files=changed,
            removed_files=removed,
        )
        if persist:
            _save_index(self.root, profile)
        return profile

    def _iter_files(self) -> list[Path]:
        paths: list[Path] = []
        for path in self.root.rglob("*"):
            if not path.is_file() or any(
                part in _IGNORED_DIRS for part in path.relative_to(self.root).parts
            ):
                continue
            paths.append(path)
        return paths


def load_index(root: Path) -> RepositoryProfile | None:
    path = root.expanduser().resolve() / ".devpilot" / "repository-index.json"
    if not path.is_file():
        return None
    try:
        return RepositoryProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def _save_index(root: Path, profile: RepositoryProfile) -> None:
    index_path = root / ".devpilot" / "repository-index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _symbols(path: Path, language: str) -> list[str]:
    pattern = _SYMBOL_PATTERNS.get(language)
    if pattern is None:
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return sorted(set(pattern.findall(content)))


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _detect_ecosystem(root: Path) -> tuple[list[str], list[str]]:
    frameworks: set[str] = set()
    managers: set[str] = set()
    dependencies: set[str] = set()
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        managers.add("uv" if (root / "uv.lock").is_file() else "python")
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            project = data.get("project", {})
            dependencies.update(project.get("dependencies", []))
            optional = project.get("optional-dependencies", {})
            for values in optional.values():
                dependencies.update(values)
            for values in data.get("dependency-groups", {}).values():
                dependencies.update(values)
            tool = data.get("tool", {})
            for section in tool.values():
                if isinstance(section, dict):
                    dependencies.update(str(key) for key in section)
        except (OSError, tomllib.TOMLDecodeError):
            pass
    package_json = root / "package.json"
    if package_json.is_file():
        managers.add("npm" if (root / "package-lock.json").is_file() else "node")
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            dependencies.update(data.get("dependencies", {}))
            dependencies.update(data.get("devDependencies", {}))
        except (OSError, json.JSONDecodeError):
            pass
    if (root / "Cargo.toml").is_file():
        managers.add("cargo")
        try:
            dependencies.update(
                tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))
                .get("dependencies", {})
            )
        except (OSError, tomllib.TOMLDecodeError):
            pass
    if (root / "go.mod").is_file():
        managers.add("go")
    dependency_text = " ".join(str(item).lower() for item in dependencies)
    markers = {
        "fastapi": "FastAPI",
        "langgraph": "LangGraph",
        "langchain": "LangChain",
        "pytest": "pytest",
        "pydantic": "Pydantic",
        "sqlalchemy": "SQLAlchemy",
        "react": "React",
        "vite": "Vite",
        "next": "Next.js",
        "django": "Django",
        "flask": "Flask",
    }
    frameworks.update(name for marker, name in markers.items() if marker in dependency_text)
    return sorted(frameworks), sorted(managers)


def _discover_commands(
    root: Path,
    languages: dict[str, int],
    package_managers: list[str],
) -> dict[str, list[str]]:
    commands: dict[str, list[str]] = {}
    if "python" in languages:
        prefix = ["uv", "--cache-dir", ".uv-cache", "run"] if "uv" in package_managers else []
        commands["test"] = [*prefix, "pytest", "-q"]
        if (root / "ruff.toml").is_file() or (root / "pyproject.toml").is_file():
            commands["lint"] = [*prefix, "ruff", "check", "."]
    if "javascript" in languages or "typescript" in languages:
        commands.setdefault("test", ["npm", "test", "--", "--runInBand"])
        if (root / "package.json").is_file():
            commands["build"] = ["npm", "run", "build"]
    if "rust" in languages:
        commands["test"] = ["cargo", "test"]
        commands["build"] = ["cargo", "build"]
    if "go" in languages:
        commands["test"] = ["go", "test", "./..."]
        commands["build"] = ["go", "build", "./..."]
    if (root / "Makefile").is_file():
        commands.setdefault("make", ["make"])
    return commands


def _git_profile(root: Path) -> dict[str, Any]:
    branch = _git(root, ["branch", "--show-current"])
    commit = _git(root, ["rev-parse", "HEAD"])
    status = _git(root, ["status", "--short"])
    worktrees = _git(root, ["worktree", "list", "--porcelain"])
    available = branch is not None or commit is not None
    return {
        "available": available,
        "branch": branch or "",
        "commit": commit or "",
        "clean": not bool(status),
        "status": status.splitlines() if status else [],
        "worktrees": worktrees.splitlines() if worktrees else [],
    }


def _git(root: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


__all__ = ["RepositoryScanner", "load_index"]
