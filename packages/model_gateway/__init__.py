"""Provider-neutral model gateway and LangChain-backed adapters."""

from .adapters import AnthropicAdapter, FakeModel, OllamaAdapter, OpenAIAdapter
from .errors import AdapterNotImplementedError, ModelAdapterError, UnsupportedCapabilityError
from .gateway import ChatModelAdapter, ModelGateway
from .router import ModelRouter, ModelRoutingError, default_model_router
from .selection import ModelChoiceError, ModelChoiceService, RuntimeModelSelection

__all__ = [
    "AdapterNotImplementedError",
    "AnthropicAdapter",
    "ChatModelAdapter",
    "FakeModel",
    "ModelAdapterError",
    "ModelGateway",
    "ModelRouter",
    "ModelRoutingError",
    "ModelChoiceError",
    "ModelChoiceService",
    "RuntimeModelSelection",
    "default_model_router",
    "OpenAIAdapter",
    "OllamaAdapter",
    "UnsupportedCapabilityError",
]
