"""Built-in file, search, patch, shell and Git tools.

The classes in this module do not decide whether a call is safe.  They only
validate their own arguments and execute after ``ToolRuntime`` has authorized
the call.  This keeps policy decisions auditable and testable in one place.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from packages.contracts import ToolDefinition, ToolRisk

from .context import ToolExecutionContext
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
        "cmd",
        "git",
        "powershell",
        "pwsh",
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
        timeout = arguments.get("timeout_seconds", self.definition.timeout_seconds)
        if (
            not isinstance(timeout, (int, float))
            or not 0 < timeout <= self.definition.timeout_seconds
        ):
            raise ValueError("timeout_seconds exceeds the tool limit")
        result = subprocess.run(
            command,
            cwd=context.workspace_root,
            env=context.safe_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        return json.dumps(
            {
                "command": command,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            ensure_ascii=False,
        )


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


def create_default_registry() -> ToolRegistry:
    return ToolRegistry(
        [FileReadTool(), FileSearchTool(), FilePatchTool(), ShellTool(), GitTool()]
    )


__all__ = [
    "FilePatchTool",
    "FileReadTool",
    "FileSearchTool",
    "GitTool",
    "ShellTool",
    "create_default_registry",
]
