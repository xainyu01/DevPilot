"""Built-in file, search, patch, shell and Git tools.

The classes in this module do not decide whether a call is safe.  They only
validate their own arguments and execute after ``ToolRuntime`` has authorized
the call.  This keeps policy decisions auditable and testable in one place.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packages.contracts import ToolDefinition, ToolRisk

from .context import ToolExecutionContext
from .errors import ToolCommandError
from .policy import PolicyEngine
from .registry import Tool, ToolRegistry

_READ_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}


def _path(context: ToolExecutionContext, raw: Any, *, must_exist: bool = True) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("path must be a non-empty string")
    try:
        return PolicyEngine().validate_path(context, raw, allow_missing=not must_exist)
    except (ValueError, FileNotFoundError) as exc:
        raise ValueError(str(exc)) from exc


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root.resolve()).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_write_path(
    context: ToolExecutionContext,
    raw: Any,
    *,
    must_exist: bool,
) -> Path:
    path = _path(context, raw, must_exist=must_exist)
    relative = path.relative_to(context.workspace_root.resolve())
    if any(part.casefold() in {".git", ".devpilot"} for part in relative.parts):
        raise ValueError("writes to .git or .devpilot are not allowed")
    return path


def _lexical_path(context: ToolExecutionContext, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("path must be a non-empty string")
    PolicyEngine().validate_path(context, raw, allow_missing=True)
    path = Path(raw)
    lexical = path if path.is_absolute() else context.workspace_root / path
    absolute = Path(os.path.abspath(lexical))
    absolute.relative_to(context.workspace_root.resolve())
    relative = absolute.relative_to(context.workspace_root.resolve())
    if any(part.casefold() in {".git", ".devpilot"} for part in relative.parts):
        raise ValueError("writes to .git or .devpilot are not allowed")
    return absolute


@dataclass(frozen=True)
class _SnapshotEntry:
    sha256: str
    size: int
    text: str | None


class WorkspaceTracker:
    """Bounded baseline used by status/diff tools for one ToolRuntime."""

    def __init__(
        self,
        root: Path,
        *,
        max_files: int = 20_000,
        max_text_bytes: int = 1_000_000,
    ) -> None:
        self.root = root.resolve()
        self.max_files = max_files
        self.max_text_bytes = max_text_bytes
        self.baseline = self.snapshot()

    def snapshot(self) -> dict[str, _SnapshotEntry]:
        result: dict[str, _SnapshotEntry] = {}
        ignored = {
            ".git",
            ".devpilot",
            ".venv",
            ".uv-cache",
            "node_modules",
            "__pycache__",
        }
        for path in sorted(self.root.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if len(result) >= self.max_files:
                break
            try:
                resolved = path.resolve()
                resolved.relative_to(self.root)
            except (OSError, ValueError):
                continue
            relative = resolved.relative_to(self.root)
            if any(part in ignored for part in relative.parts) or not resolved.is_file():
                continue
            try:
                size = resolved.stat().st_size
                text = (
                    resolved.read_text(encoding="utf-8")
                    if size <= self.max_text_bytes
                    else None
                )
                result[relative.as_posix()] = _SnapshotEntry(
                    sha256=_sha256(resolved),
                    size=size,
                    text=text,
                )
            except (OSError, UnicodeDecodeError):
                continue
        return result

    def status(self) -> dict[str, list[str]]:
        current = self.snapshot()
        before = set(self.baseline)
        after = set(current)
        return {
            "added": sorted(after - before),
            "modified": sorted(
                path
                for path in before & after
                if self.baseline[path].sha256 != current[path].sha256
            ),
            "deleted": sorted(before - after),
        }

    def diff(self, path_filter: str | None = None) -> str:
        current = self.snapshot()
        status = self.status()
        changed = status["added"] + status["modified"] + status["deleted"]
        if path_filter is not None:
            changed = [path for path in changed if path == path_filter]
        output: list[str] = []
        for path in sorted(changed):
            before = self.baseline.get(path)
            after = current.get(path)
            if (before and before.text is None) or (after and after.text is None):
                output.append(f"Binary or oversized file changed: {path}\n")
                continue
            before_lines = (before.text if before else "").splitlines(keepends=True)
            after_lines = (after.text if after else "").splitlines(keepends=True)
            output.extend(
                difflib.unified_diff(
                    before_lines,
                    after_lines,
                    fromfile=f"a/{path}" if before else "/dev/null",
                    tofile=f"b/{path}" if after else "/dev/null",
                )
            )
        return "".join(output)


class FileReadTool(Tool):
    definition = ToolDefinition(
        name="file.read",
        description="Read a UTF-8 text file inside the configured workspace.",
        input_schema={
            **_READ_SCHEMA,
            "properties": {**_READ_SCHEMA["properties"], "max_bytes": {"type": "integer"}},
        },
        risk=ToolRisk.READ_ONLY,
        required_capabilities=["workspace.read"],
        timeout_seconds=10,
        max_output_chars=1_000_000,
        idempotent=True,
    )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        path = _path(context, arguments.get("path"))
        max_bytes = arguments.get("max_bytes", 1_000_000)
        if not isinstance(max_bytes, int) or not 1 <= max_bytes <= 10_000_000:
            raise ValueError("max_bytes must be between 1 and 10000000")
        if not path.is_file():
            raise ValueError("path is not a file")
        data = path.read_bytes()
        truncated = len(data) > max_bytes
        text = data[:max_bytes].decode("utf-8", errors="replace")
        if truncated:
            text += "\n[content truncated]"
        return text


class FileSearchTool(Tool):
    definition = ToolDefinition(
        name="file.search",
        description="Search text files below a workspace path without invoking a shell.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string"},
                "glob": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        risk=ToolRisk.READ_ONLY,
        required_capabilities=["workspace.read"],
        timeout_seconds=30,
        max_output_chars=200_000,
        idempotent=True,
    )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        query = arguments.get("query")
        if not isinstance(query, str) or not query:
            raise ValueError("query must be a non-empty string")
        root = _path(context, arguments.get("path", "."), must_exist=True)
        if not root.is_dir():
            raise ValueError("search path is not a directory")
        pattern = arguments.get("glob", "**/*")
        max_results = arguments.get("max_results", 50)
        if not isinstance(pattern, str) or not isinstance(max_results, int):
            raise ValueError("glob must be a string and max_results an integer")
        if not 1 <= max_results <= 500:
            raise ValueError("max_results must be between 1 and 500")
        ignored = {".git", ".venv", ".uv-cache", "__pycache__", ".pytest_cache", ".ruff_cache"}
        matches: list[dict[str, Any]] = []
        candidates = root.glob(pattern) if pattern != "**/*" else root.rglob("*")
        for candidate in candidates:
            if len(matches) >= max_results:
                break
            if not candidate.is_file() or any(part in ignored for part in candidate.parts):
                continue
            try:
                lines = candidate.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for line_number, line in enumerate(lines, start=1):
                if query in line:
                    matches.append(
                        {
                            "path": _relative(candidate, context.workspace_root),
                            "line": line_number,
                            "text": line,
                        }
                    )
                    if len(matches) >= max_results:
                        break
        return json.dumps({"query": query, "matches": matches}, ensure_ascii=False)


class FilePatchTool(Tool):
    definition = ToolDefinition(
        name="file.patch",
        description="Apply one exact text replacement or a unified diff inside the workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "patch": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        risk=ToolRisk.RECOVERABLE_WRITE,
        required_capabilities=["workspace.write"],
        timeout_seconds=20,
        max_output_chars=20_000,
        idempotent=False,
    )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        path = _path(context, arguments.get("path"), must_exist=True)
        if not path.is_file():
            raise ValueError("path is not a file")
        original = path.read_text(encoding="utf-8")
        old_text = arguments.get("old_text")
        new_text = arguments.get("new_text")
        if isinstance(old_text, str) and isinstance(new_text, str):
            count = original.count(old_text)
            if count == 0:
                raise ValueError("old_text was not found")
            if count > 1 and not arguments.get("replace_all", False):
                raise ValueError("old_text matched multiple locations")
            updated = original.replace(old_text, new_text)
        else:
            patch = arguments.get("patch")
            if not isinstance(patch, str) or not patch.strip():
                raise ValueError("provide old_text/new_text or a unified patch")
            updated = _apply_unified_patch(original, patch)
        if updated == original:
            raise ValueError("patch would not change the file")
        path.write_text(updated, encoding="utf-8")
        return json.dumps(
            {"path": _relative(path, context.workspace_root), "changed": True}, ensure_ascii=False
        )


class FileListTool(Tool):
    definition = ToolDefinition(
        name="file.list",
        description="List a bounded project file tree with type and size metadata.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_depth": {"type": "integer", "minimum": 0, "maximum": 10},
                "max_entries": {"type": "integer", "minimum": 1, "maximum": 2000},
            },
            "additionalProperties": False,
        },
        risk=ToolRisk.READ_ONLY,
        required_capabilities=["workspace.read"],
        idempotent=True,
    )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        root = _path(context, arguments.get("path", "."), must_exist=True)
        if not root.is_dir():
            raise ValueError("list path is not a directory")
        max_depth = arguments.get("max_depth", 3)
        max_entries = arguments.get("max_entries", 500)
        if not isinstance(max_depth, int) or not 0 <= max_depth <= 10:
            raise ValueError("max_depth must be between 0 and 10")
        if not isinstance(max_entries, int) or not 1 <= max_entries <= 2000:
            raise ValueError("max_entries must be between 1 and 2000")
        entries: list[dict[str, Any]] = []
        for candidate in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if len(entries) >= max_entries:
                break
            try:
                resolved = candidate.resolve()
                resolved.relative_to(context.workspace_root.resolve())
            except (OSError, ValueError):
                continue
            relative_to_list = resolved.relative_to(root)
            if len(relative_to_list.parts) > max_depth:
                continue
            if any(part in {".git", ".devpilot"} for part in relative_to_list.parts):
                continue
            kind = "directory" if resolved.is_dir() else "file" if resolved.is_file() else "other"
            entries.append(
                {
                    "path": _relative(resolved, context.workspace_root),
                    "type": kind,
                    "size": resolved.stat().st_size if kind == "file" else None,
                }
            )
        return json.dumps(
            {"path": _relative(root, context.workspace_root), "entries": entries},
            ensure_ascii=False,
        )


class FileWriteTool(Tool):
    definition = ToolDefinition(
        name="file.write",
        description=(
            "Create or explicitly overwrite one UTF-8 project file. Set expected_sha256 only "
            "when an exact hash came from a prior Tool Result; otherwise omit it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "create_only": {"type": "boolean"},
                "overwrite": {"type": "boolean"},
                "expected_sha256": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        risk=ToolRisk.RECOVERABLE_WRITE,
        required_capabilities=["workspace.write"],
        timeout_seconds=20,
        max_output_chars=20_000,
    )
    max_bytes = 1_000_000

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        path = _safe_write_path(
            context,
            arguments.get("path"),
            must_exist=False,
        )
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_bytes:
            raise ValueError(f"file content exceeds {self.max_bytes} bytes")
        existed = path.exists()
        if existed:
            if not path.is_file():
                raise ValueError("write path is not a regular file")
            if arguments.get("create_only", False):
                raise ValueError("file already exists")
            if not arguments.get("overwrite", False):
                raise ValueError("overwrite=true is required for an existing file")
            expected = arguments.get("expected_sha256")
            if expected is not None:
                if not isinstance(expected, str) or _sha256(path) != expected:
                    raise ValueError("expected_sha256 does not match the current file")
        elif arguments.get("expected_sha256") is not None:
            raise ValueError("expected_sha256 cannot be used for a missing file")
        if not path.parent.is_dir():
            raise ValueError("parent directory does not exist; call file.mkdir first")
        path.write_text(content, encoding="utf-8")
        return json.dumps(
            {
                "path": _relative(path, context.workspace_root),
                "created": not existed,
                "sha256": _sha256(path),
                "size": len(encoded),
            },
            ensure_ascii=False,
        )


class FileMkdirTool(Tool):
    definition = ToolDefinition(
        name="file.mkdir",
        description="Create a project directory, optionally including missing parents.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "parents": {"type": "boolean"},
                "exist_ok": {"type": "boolean"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        risk=ToolRisk.RECOVERABLE_WRITE,
        required_capabilities=["workspace.write"],
        idempotent=True,
    )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        path = _safe_write_path(context, arguments.get("path"), must_exist=False)
        path.mkdir(
            parents=bool(arguments.get("parents", True)),
            exist_ok=bool(arguments.get("exist_ok", True)),
        )
        return json.dumps(
            {"path": _relative(path, context.workspace_root), "created": True},
            ensure_ascii=False,
        )


class FileDeleteTool(Tool):
    definition = ToolDefinition(
        name="file.delete",
        description="Delete one file, link, or empty directory after explicit approval.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        risk=ToolRisk.HIGH_RISK,
        required_capabilities=["workspace.delete"],
    )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        path = _lexical_path(context, arguments.get("path"))
        if path == context.workspace_root.resolve():
            raise ValueError("cannot delete the workspace root")
        if path.is_symlink():
            path.unlink()
        elif path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
        else:
            raise ValueError("delete target does not exist")
        return json.dumps(
            {"path": path.relative_to(context.workspace_root.resolve()).as_posix()},
            ensure_ascii=False,
        )


class WorkspaceStatusTool(Tool):
    def __init__(self, tracker: WorkspaceTracker) -> None:
        self.tracker = tracker

    definition = ToolDefinition(
        name="workspace.status",
        description="Show files added, modified, or deleted since this Agent runtime started.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        risk=ToolRisk.READ_ONLY,
        required_capabilities=["workspace.read"],
        idempotent=True,
    )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        return json.dumps(self.tracker.status(), ensure_ascii=False)


class FileDiffTool(Tool):
    def __init__(self, tracker: WorkspaceTracker) -> None:
        self.tracker = tracker

    definition = ToolDefinition(
        name="file.diff",
        description="Return a unified diff since this Agent runtime started.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "additionalProperties": False,
        },
        risk=ToolRisk.READ_ONLY,
        required_capabilities=["workspace.read"],
        max_output_chars=500_000,
        idempotent=True,
    )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        path_filter = arguments.get("path")
        if path_filter is not None:
            path = _path(context, path_filter, must_exist=False)
            path_filter = _relative(path, context.workspace_root)
        return self.tracker.diff(path_filter)


class RepositoryScanTool(Tool):
    definition = ToolDefinition(
        name="repo.scan",
        description="Scan bounded repository languages, files, rules, commands, and Git state.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        risk=ToolRisk.READ_ONLY,
        required_capabilities=["workspace.read"],
        max_output_chars=500_000,
        idempotent=True,
    )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        from packages.repo_intel import RepositoryScanner

        profile = RepositoryScanner(context.workspace_root).scan(persist=False)
        payload = profile.model_dump(mode="json")
        payload["root_path"] = "."
        return json.dumps(payload, ensure_ascii=False)


def _validate_command_paths(command: list[str], context: ToolExecutionContext) -> None:
    for index, argument in enumerate(command[1:], start=1):
        if index > 1 and command[index - 1] in {"-c", "-Command", "/c"}:
            raise ValueError("inline shell or interpreter commands are not allowed")
        candidate = argument.split("=", 1)[1] if "=" in argument else argument
        if candidate.startswith("-") or candidate in {".", ".."}:
            if candidate in {".", ".."}:
                PolicyEngine().validate_path(context, candidate)
            continue
        path = Path(candidate)
        if path.is_absolute() or "/" in candidate or "\\" in candidate:
            PolicyEngine().validate_path(context, candidate)


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=10,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def _create_windows_job(process: subprocess.Popen[str]) -> Any:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    information = ExtendedLimitInformation()
    information.BasicLimitInformation.LimitFlags = 0x00002000
    configured = kernel32.SetInformationJobObject(
        job,
        9,
        ctypes.byref(information),
        ctypes.sizeof(information),
    )
    assigned = configured and kernel32.AssignProcessToJobObject(
        job,
        wintypes.HANDLE(int(process._handle)),
    )
    if not assigned:
        kernel32.CloseHandle(job)
        return None
    return job


def _close_windows_job(job: Any, *, terminate: bool) -> None:
    if job is None or os.name != "nt":
        return
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if terminate:
        kernel32.TerminateJobObject(job, 1)
    kernel32.CloseHandle(job)


def _run_controlled_process(
    command: list[str],
    *,
    context: ToolExecutionContext,
    timeout: float,
    max_output_chars: int,
) -> dict[str, Any]:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=context.workspace_root,
        env=context.safe_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        start_new_session=os.name != "nt",
        creationflags=creationflags,
    )
    windows_job = _create_windows_job(process)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if windows_job is not None:
            _close_windows_job(windows_job, terminate=True)
            windows_job = None
        else:
            _terminate_process_tree(process)
        stdout, stderr = process.communicate()
        payload = json.dumps(
            {
                "command": command,
                "returncode": process.returncode,
                "stdout": stdout[:max_output_chars],
                "stderr": stderr[:max_output_chars],
                "timed_out": True,
            },
            ensure_ascii=False,
        )
        raise ToolCommandError(
            f"command timed out after {timeout:g}s",
            output=payload,
            code="command_timeout",
        ) from exc
    finally:
        _close_windows_job(windows_job, terminate=False)
    return {
        "command": command,
        "returncode": process.returncode,
        "stdout": stdout[:max_output_chars],
        "stderr": stderr[:max_output_chars],
        "timed_out": False,
    }


class TestRunTool(Tool):
    __test__ = False
    def __init__(
        self,
        root: Path,
        *,
        allowed_commands: list[list[str]] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.allowed_commands = allowed_commands or []
        if not self.allowed_commands:
            # The model may select a server-discovered kind, but it must not
            # repurpose the test tool as an arbitrary command runner. Explicit
            # commands are exposed only when an administrator configured them.
            self.definition = self.definition.model_copy(deep=True)
            self.definition.input_schema["properties"].pop("command", None)

    definition = ToolDefinition(
        name="test.run",
        description="Run a repository-discovered or administrator-approved test command.",
        input_schema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "Discovered command kind such as test; prefer this field.",
                },
                "command": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Exact test command previously returned by repo.scan or configured "
                        "by an administrator; never use this for arbitrary CLI diagnostics."
                    ),
                },
                "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 300},
            },
            "additionalProperties": False,
        },
        risk=ToolRisk.READ_ONLY,
        required_capabilities=["test.execute"],
        timeout_seconds=300,
        max_output_chars=200_000,
    )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        from packages.repo_intel import RepositoryScanner

        profile = RepositoryScanner(context.workspace_root).scan(persist=False)
        discovered = list(profile.commands.values())
        allowed = [*discovered, *self.allowed_commands]
        requested = arguments.get("command")
        if requested is None:
            kind = arguments.get("kind", "test")
            requested = profile.commands.get(kind)
        if (
            not isinstance(requested, list)
            or not requested
            or not all(isinstance(item, str) and item for item in requested)
        ):
            raise ValueError("no discovered test command matches the request")
        if requested not in allowed:
            raise ValueError("test command was not discovered or administrator-approved")
        _validate_command_paths(requested, context)
        timeout = arguments.get("timeout_seconds", self.definition.timeout_seconds)
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise ValueError("timeout_seconds must be between 0 and 300")
        result = _run_controlled_process(
            requested,
            context=context,
            timeout=float(timeout),
            max_output_chars=self.definition.max_output_chars,
        )
        payload = json.dumps(result, ensure_ascii=False)
        if result["returncode"] != 0:
            raise ToolCommandError(
                f"test command exited with code {result['returncode']}",
                output=payload,
                code="test_failed",
            )
        return payload


class ShellTool(Tool):
    definition = ToolDefinition(
        name="shell.exec",
        description="Run one allow-listed parameterized command in the workspace after approval.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "array", "items": {"type": "string"}},
                "timeout_seconds": {"type": "number"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        risk=ToolRisk.HIGH_RISK,
        required_capabilities=["shell.execute"],
        timeout_seconds=120,
        max_output_chars=100_000,
        idempotent=False,
    )

    allowed_executables = {
        "git",
        "python",
        "python3",
        "pytest",
        "ruff",
        "uv",
    }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        command = arguments.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(item, str) and item for item in command)
        ):
            raise ValueError("command must be a non-empty list of strings")
        executable = Path(command[0]).name.lower()
        if executable.endswith(".exe"):
            executable = executable[:-4]
        if executable not in self.allowed_executables:
            raise ValueError(f"executable is not allow-listed: {executable}")
        _validate_command_paths(command, context)
        timeout = arguments.get("timeout_seconds", self.definition.timeout_seconds)
        if (
            not isinstance(timeout, (int, float))
            or not 0 < timeout <= self.definition.timeout_seconds
        ):
            raise ValueError("timeout_seconds exceeds the tool limit")
        result = _run_controlled_process(
            command,
            context=context,
            timeout=float(timeout),
            max_output_chars=self.definition.max_output_chars,
        )
        payload = json.dumps(result, ensure_ascii=False)
        if result["returncode"] != 0:
            raise ToolCommandError(
                f"command exited with code {result['returncode']}",
                output=payload,
            )
        return payload


class GitTool(Tool):
    definition = ToolDefinition(
        name="git.exec",
        description="Run a fixed Git operation in the workspace; writes require approval.",
        input_schema={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "status",
                        "diff",
                        "log",
                        "branch",
                        "worktree_list",
                        "add",
                        "commit",
                        "push",
                    ],
                },
                "path": {"type": "string"},
                "message": {"type": "string"},
                "remote": {"type": "string"},
                "branch": {"type": "string"},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
        risk=ToolRisk.READ_ONLY,
        required_capabilities=["git.read"],
        timeout_seconds=60,
        max_output_chars=100_000,
        idempotent=False,
    )

    read_operations = {"status", "diff", "log", "branch", "worktree_list"}
    write_operations = {"add", "commit", "push"}

    def risk_level(self, arguments: dict[str, Any]) -> ToolRisk:
        operation = arguments.get("operation")
        if operation in self.write_operations:
            return ToolRisk.HIGH_RISK
        return ToolRisk.READ_ONLY

    def required_capabilities(self, arguments: dict[str, Any]) -> list[str]:
        operation = arguments.get("operation")
        return ["git.write"] if operation in self.write_operations else ["git.read"]

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> str:
        operation = arguments.get("operation")
        if operation not in self.read_operations | self.write_operations:
            raise ValueError("unsupported git operation")
        command = ["git"]
        if operation == "status":
            command += ["status", "--short"]
        elif operation == "diff":
            command += ["diff"]
            if arguments.get("path"):
                command += [
                    "--",
                    _relative(_path(context, arguments["path"]), context.workspace_root),
                ]
        elif operation == "log":
            command += ["log", "--oneline", "-n", "20"]
        elif operation == "branch":
            command += ["branch", "--show-current"]
        elif operation == "worktree_list":
            command += ["worktree", "list", "--porcelain"]
        elif operation == "add":
            raw_path = arguments.get("path", ".")
            _path(context, raw_path, must_exist=True)
            command += ["add", "--", _relative(_path(context, raw_path), context.workspace_root)]
        elif operation == "commit":
            message = arguments.get("message")
            if not isinstance(message, str) or not message.strip():
                raise ValueError("commit requires a non-empty message")
            command += ["commit", "-m", message]
        else:
            remote = arguments.get("remote", "origin")
            branch = arguments.get("branch")
            command += ["push", remote]
            if branch:
                command.append(branch)
        result = subprocess.run(
            command,
            cwd=context.workspace_root,
            env=context.safe_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout=self.definition.timeout_seconds,
        )
        return json.dumps(
            {
                "operation": operation,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            ensure_ascii=False,
        )


def _apply_unified_patch(original: str, patch: str) -> str:
    """Apply a small single-file unified diff with strict context checks."""
    source = original.splitlines(keepends=True)
    patch_lines = patch.splitlines(keepends=True)
    hunks: list[tuple[int, int, list[str]]] = []
    current: tuple[int, int, list[str]] | None = None
    for line in patch_lines:
        if line.startswith("@@"):
            if current is not None:
                hunks.append(current)
            header = line.split("@@", 2)[1].strip()
            old_range = header.split(" ", 1)[0].lstrip("-")
            start = int(old_range.split(",", 1)[0])
            current = (start, int(old_range.split(",", 1)[1]) if "," in old_range else 1, [])
        elif current is not None and (
            line.startswith((" ", "+", "-"))
            or line == "\\ No newline at end of file\n"
        ):
            current[2].append(line)
    if current is not None:
        hunks.append(current)
    if not hunks:
        raise ValueError("patch does not contain a unified hunk")

    output: list[str] = []
    source_index = 0
    for start, _old_count, lines in hunks:
        hunk_index = start - 1
        if hunk_index < source_index or hunk_index > len(source):
            raise ValueError("patch hunk position is invalid")
        output.extend(source[source_index:hunk_index])
        for line in lines:
            if line.startswith("\\"):
                continue
            marker, content = line[0], line[1:]
            if marker in {" ", "-"}:
                if source_index >= len(source) or source[source_index] != content:
                    raise ValueError("patch context does not match file")
                if marker == " ":
                    output.append(content)
                source_index += 1
            elif marker == "+":
                output.append(content)
    output.extend(source[source_index:])
    return "".join(output)


def create_default_registry(root: Path | None = None) -> ToolRegistry:
    workspace = (root or Path.cwd()).resolve()
    tracker = WorkspaceTracker(workspace)
    return ToolRegistry(
        [
            FileReadTool(),
            FileSearchTool(),
            FileListTool(),
            FileWriteTool(),
            FileMkdirTool(),
            FileDeleteTool(),
            FilePatchTool(),
            FileDiffTool(tracker),
            WorkspaceStatusTool(tracker),
            RepositoryScanTool(),
            TestRunTool(workspace),
            ShellTool(),
            GitTool(),
        ]
    )


__all__ = [
    "FileDeleteTool",
    "FileDiffTool",
    "FileListTool",
    "FileMkdirTool",
    "FilePatchTool",
    "FileReadTool",
    "FileSearchTool",
    "FileWriteTool",
    "GitTool",
    "RepositoryScanTool",
    "ShellTool",
    "TestRunTool",
    "WorkspaceStatusTool",
    "WorkspaceTracker",
    "create_default_registry",
]
