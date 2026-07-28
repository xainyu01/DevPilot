"""Safe, policy-gated tools for the CodeAssist domain layer."""

from .approvals import ApprovalStore
from .audit import AuditLog
from .context import ToolExecutionContext
from .errors import (
    PolicyDeniedError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolRuntimeError,
    UnsafePathError,
)
from .policy import PolicyEngine
from .registry import Tool, ToolRegistry
from .runtime import ToolRuntime
from .tools import (
    FilePatchTool,
    FileReadTool,
    FileSearchTool,
    GitTool,
    ShellTool,
    create_default_registry,
)

__all__ = [
    "ApprovalStore",
    "AuditLog",
    "FilePatchTool",
    "FileReadTool",
    "FileSearchTool",
    "GitTool",
    "PolicyDeniedError",
    "PolicyEngine",
    "ShellTool",
    "Tool",
    "ToolExecutionContext",
    "ToolExecutionError",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolRuntime",
    "ToolRuntimeError",
    "UnsafePathError",
    "create_default_registry",
]
