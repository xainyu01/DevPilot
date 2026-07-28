"""Tool registration and lookup."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any

from packages.contracts import ToolCall, ToolDefinition, ToolRisk

from .context import ToolExecutionContext
from .errors import ToolNotFoundError


class Tool(ABC):
    """Minimal domain interface implemented by every tool."""

    definition: ToolDefinition

    @abstractmethod
    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        """Execute already-authorized arguments."""

    def risk_level(self, arguments: dict[str, Any]) -> ToolRisk:
        return self.definition.risk

    def required_capabilities(self, arguments: dict[str, Any]) -> list[str]:
        return list(self.definition.required_capabilities)


class ToolRegistry:
    """Explicit allow-list of tools; unknown names are never dynamically imported."""

    def __init__(self, tools: Iterable[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        name = tool.definition.name
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"Tool is not registered: {name}") from exc

    def definitions(self) -> list[ToolDefinition]:
        return [self._tools[name].definition for name in sorted(self._tools)]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def risk_level(self, call: ToolCall) -> ToolRisk:
        return self.get(call.name).risk_level(call.arguments)

    def required_capabilities(self, call: ToolCall) -> list[str]:
        return self.get(call.name).required_capabilities(call.arguments)
