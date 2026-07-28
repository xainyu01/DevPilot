"""Errors raised by the policy-gated tool runtime."""


class ToolRuntimeError(RuntimeError):
    """Base class for tool registration, policy and execution errors."""


class ToolNotFoundError(ToolRuntimeError):
    """The requested tool is not registered."""


class PolicyDeniedError(ToolRuntimeError):
    """A registered tool failed the policy check."""


class UnsafePathError(PolicyDeniedError):
    """A path escaped the configured workspace boundary."""


class ToolExecutionError(ToolRuntimeError):
    """A tool received invalid input or failed while executing."""
