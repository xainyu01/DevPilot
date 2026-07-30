from __future__ import annotations

import pytest

from packages.contracts import ChatMessage
from packages.local_settings import (
    AgentModelPolicy,
    LocalSettings,
    ModelEndpoint,
    ModelTarget,
)
from packages.model_gateway import (
    FakeModel,
    ModelChoiceError,
    ModelChoiceService,
    ModelGateway,
)


def selection_settings() -> LocalSettings:
    return LocalSettings(
        model_endpoints=(
            ModelEndpoint("router", "Router", "fake", ("controller",)),
            ModelEndpoint("worker", "Worker", "fake", ("fast", "quality")),
        ),
        default_model=ModelTarget("router", "controller"),
        agent_model_policy=AgentModelPolicy(
            mode="auto",
            allowed_models=(
                ModelTarget("router", "controller"),
                ModelTarget("worker", "quality"),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_routing_model_can_choose_only_an_allowed_target() -> None:
    settings = selection_settings()
    gateway = ModelGateway(
        [
            FakeModel(
                model="controller",
                provider_id="router",
                response=(
                    '{"endpoint_id":"worker","model":"quality",'
                    '"reason":"best for a repository refactor"}'
                ),
            ),
            FakeModel(model="quality", provider_id="worker"),
        ]
    )

    selected = await ModelChoiceService(gateway, settings).choose(
        messages=[ChatMessage.from_text("user", "Refactor the repository")],
    )

    assert selected.target == ModelTarget("worker", "quality")
    assert selected.selector == ModelTarget("router", "controller")
    assert selected.fallback_used is False


@pytest.mark.asyncio
async def test_manual_choice_outside_policy_is_rejected() -> None:
    settings = selection_settings()
    gateway = ModelGateway([FakeModel(model="controller", provider_id="router")])

    with pytest.raises(ModelChoiceError, match="outside"):
        await ModelChoiceService(gateway, settings).choose(
            messages=[ChatMessage.from_text("user", "task")],
            requested=ModelTarget("worker", "fast"),
        )


@pytest.mark.asyncio
async def test_per_run_allowed_range_cannot_expand_global_policy() -> None:
    settings = selection_settings()
    gateway = ModelGateway([FakeModel(model="controller", provider_id="router")])

    with pytest.raises(ModelChoiceError, match="exceeds"):
        await ModelChoiceService(gateway, settings).choose(
            messages=[ChatMessage.from_text("user", "task")],
            allowed=(ModelTarget("worker", "fast"),),
        )


@pytest.mark.asyncio
async def test_invalid_selector_output_falls_back_inside_allowed_range() -> None:
    settings = selection_settings()
    gateway = ModelGateway(
        [FakeModel(model="controller", provider_id="router", response="not-json")]
    )

    selected = await ModelChoiceService(gateway, settings).choose(
        messages=[ChatMessage.from_text("user", "task")],
    )

    assert selected.target == ModelTarget("router", "controller")
    assert selected.fallback_used is True
