"""Structured errors exposed by the model gateway."""

from __future__ import annotations

from packages.contracts import CapabilityError


class ModelAdapterError(RuntimeError):
    """Base error for adapter configuration and invocation failures."""


class AdapterNotImplementedError(ModelAdapterError):
    """Raised when a declared provider is intentionally not implemented yet."""


class UnsupportedCapabilityError(ModelAdapterError):
    """Raised before a request would silently lose an unsupported content block."""

    def __init__(self, error: CapabilityError) -> None:
        self.error = error
        super().__init__(error.message)
