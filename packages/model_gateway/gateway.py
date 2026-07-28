"""The small, provider-neutral model gateway used by the Agent graph."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from packages.contracts import (
    AdapterHealth,
    CapabilityError,
    ChatRequest,
    ChatResponse,
    ModelCapabilities,
    ModelStreamEvent,
    TokenUsage,
)

from .errors import ModelAdapterError, UnsupportedCapabilityError


class ChatModelAdapter(ABC):
    """Stable boundary between Agent nodes and a LangChain chat model."""

    provider: str
    model: str

    @abstractmethod
    async def invoke(self, request: ChatRequest) -> ChatResponse:
        """Invoke the selected model and return one normalized response."""

    @abstractmethod
    async def stream(self, request: ChatRequest) -> AsyncIterator[ModelStreamEvent]:
        """Yield normalized deltas and finish information."""
        yield ModelStreamEvent(provider=self.provider, model=self.model)

    @abstractmethod
    def capabilities(self) -> ModelCapabilities:
        """Return the declared capabilities for this configured model."""

    @abstractmethod
    def count_tokens(self, messages: list) -> TokenUsage:
        """Estimate or count tokens without making a provider request."""

    @abstractmethod
    def healthcheck(self) -> AdapterHealth:
        """Return local configuration health without leaking credentials."""

    def validate_request(self, request: ChatRequest) -> None:
        """Reject unsupported content and features before invoking the provider."""
        unsupported = self.capabilities().unsupported_blocks(request.messages)
        if request.tools and not self.capabilities().tools:
            unsupported.append("tools")
        if request.response_format and not self.capabilities().structured_output:
            unsupported.append("structured_output")
        unsupported = sorted(set(unsupported))
        if unsupported:
            message = (
                f"{self.provider}/{self.model} does not support: "
                f"{', '.join(unsupported)}"
            )
            raise UnsupportedCapabilityError(
                error=CapabilityError(
                    message=message,
                    provider=self.provider,
                    model=self.model,
                    unsupported_blocks=unsupported,
                )
            )


class ModelGateway:
    """Registry that lets one thread switch providers without changing Agent state."""

    def __init__(self, adapters: list[ChatModelAdapter] | None = None) -> None:
        self._adapters: dict[tuple[str, str], ChatModelAdapter] = {}
        for adapter in adapters or []:
            self.register(adapter)

    def register(self, adapter: ChatModelAdapter) -> None:
        self._adapters[(adapter.provider, adapter.model)] = adapter

    def get(self, provider: str, model: str) -> ChatModelAdapter:
        adapter = self._adapters.get((provider, model))
        if adapter is None:
            candidates = [item for (name, _), item in self._adapters.items() if name == provider]
            if len(candidates) == 1:
                adapter = candidates[0]
        if adapter is None:
            raise ModelAdapterError(f"No model adapter registered for {provider}/{model}")
        return adapter

    def validate(self, request: ChatRequest) -> ChatModelAdapter:
        adapter = self.get(str(request.provider), request.model)
        adapter.validate_request(request)
        return adapter

    async def invoke(self, request: ChatRequest) -> ChatResponse:
        adapter = self.validate(request)
        return await adapter.invoke(request)

    async def stream(self, request: ChatRequest) -> AsyncIterator[ModelStreamEvent]:
        adapter = self.validate(request)
        async for event in adapter.stream(request):
            yield event

    def healthcheck(self, provider: str, model: str) -> AdapterHealth:
        return self.get(provider, model).healthcheck()

    def providers(self) -> list[tuple[str, str]]:
        return sorted(self._adapters)
