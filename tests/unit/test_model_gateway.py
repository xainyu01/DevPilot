from __future__ import annotations

import pytest

from packages.contracts import (
    AudioBlock,
    ChatMessage,
    ChatRequest,
    DocumentBlock,
    ImageBlock,
    ModelProvider,
)
from packages.model_gateway import (
    AdapterNotImplementedError,
    AnthropicAdapter,
    FakeModel,
    ModelGateway,
    OllamaAdapter,
    OpenAIAdapter,
    UnsupportedCapabilityError,
)


def text_request(provider: str = "fake", model: str = "fake-model") -> ChatRequest:
    return ChatRequest(
        provider=provider,
        model=model,
        messages=[ChatMessage.from_text("user", "hello")],
    )


def test_provider_capabilities_and_health_are_explicit() -> None:
    openai = OpenAIAdapter()
    anthropic = AnthropicAdapter()
    ollama = OllamaAdapter()

    assert openai.capabilities().image is True
    assert openai.capabilities().pdf is True
    assert anthropic.capabilities().image is True
    assert ollama.healthcheck().status == "not_implemented"
    assert openai.healthcheck().status == "unavailable"


def test_vendor_content_conversion_preserves_attachment_references() -> None:
    request = ChatRequest(
        provider=ModelProvider.OPENAI,
        model="gpt-4o-mini",
        messages=[
            ChatMessage(
                role="user",
                content=[
                    ImageBlock(attachment_id="image-1"),
                    DocumentBlock(attachment_id="pdf-1", filename="guide.pdf"),
                ],
            )
        ],
    )

    openai = OpenAIAdapter(model="gpt-4o-mini")
    anthropic = AnthropicAdapter(model="claude-3-5-sonnet-latest")

    openai_content = openai.to_langchain_messages(request.messages)[0].content
    anthropic_content = anthropic.to_langchain_messages(request.messages)[0].content

    assert openai_content[0]["image_url"]["url"] == "attachment://image-1"
    assert openai_content[1]["file"]["file_id"] == "pdf-1"
    assert anthropic_content[0]["source"]["file_id"] == "image-1"
    assert anthropic_content[1]["source"]["file_id"] == "pdf-1"


@pytest.mark.asyncio
async def test_ollama_methods_return_clear_not_implemented_error() -> None:
    adapter = OllamaAdapter()

    with pytest.raises(AdapterNotImplementedError, match="not implemented"):
        await adapter.invoke(text_request(provider="ollama", model=adapter.model))


@pytest.mark.asyncio
async def test_gateway_switches_provider_without_changing_messages() -> None:
    gateway = ModelGateway(
        [
            FakeModel(model="fake-model", response="fake"),
            FakeModel(model="second-model", response="second"),
        ]
    )

    first = await gateway.invoke(text_request(model="fake-model"))
    second = await gateway.invoke(text_request(model="second-model"))

    assert first.text == "fake"
    assert second.text == "second"
    assert first.provider == second.provider == "fake"
    assert first.model != second.model


def test_audio_is_rejected_before_fake_model_execution() -> None:
    adapter = FakeModel()
    request = ChatRequest(
        provider="fake",
        model=adapter.model,
        messages=[
            ChatMessage(
                role="user",
                content=[AudioBlock(attachment_id="audio-1")],
            )
        ],
    )

    with pytest.raises(UnsupportedCapabilityError, match="audio"):
        adapter.validate_request(request)
