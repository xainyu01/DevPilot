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


class ToolCommandError(ToolExecutionError):
    """A controlled subprocess completed unsuccessfully with captured evidence."""

    def __init__(
        self,
        message: str,
        *,
        output: str = "",
        code: str = "command_failed",
    ) -> None:
        self.output = output
        self.code = code
        super().__init__(message)
