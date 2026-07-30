from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from packages.contracts import (
    AudioBlock,
    ChatMessage,
    ChatRequest,
    DocumentBlock,
    ImageBlock,
    ModelProvider,
    ModelStopReason,
)
from packages.model_gateway import (
    AdapterNotImplementedError,
    AnthropicAdapter,
    FakeModel,
    ModelGateway,
    OllamaAdapter,
    OpenAIAdapter,
    ToolCallProtocolError,
    UnsupportedCapabilityError,
)


class ToolSpyModel:
    def __init__(self, response, *, chunks=None) -> None:
        self.response = response
        self.chunks = chunks or []
        self.bound_tools = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages, config=None):
        return self.response

    async def astream(self, messages, config=None):
        for chunk in self.chunks:
            yield chunk


def read_tool() -> dict:
    return {
        "name": "file.read",
        "description": "Read one project file",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    }


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


@pytest.mark.asyncio
async def test_openai_tool_call_is_bound_and_normalized() -> None:
    model = ToolSpyModel(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "file__read",
                    "args": {"path": "README.md"},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
            response_metadata={
                "finish_reason": "tool_calls",
                "model_name": "deepseek-v4-flash",
            },
            usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
        )
    )
    adapter = OpenAIAdapter(
        model="deepseek-v4-flash",
        provider_id="deepseek-openai",
        chat_model=model,
    )
    request = ChatRequest(
        provider="deepseek-openai",
        model="deepseek-v4-flash",
        messages=[ChatMessage.from_text("user", "Read README.md")],
        tools=[read_tool()],
    )

    response = await adapter.invoke(request)

    assert model.bound_tools[0]["function"]["name"] == "file__read"
    assert response.tool_calls[0].arguments == {"path": "README.md"}
    assert response.stop_reason == ModelStopReason.TOOL_CALLS
    assert response.response_metadata["provider_model"] == "deepseek-v4-flash"
    assert response.usage.total_tokens == 13


@pytest.mark.asyncio
async def test_anthropic_tool_use_block_is_normalized() -> None:
    model = ToolSpyModel(
        AIMessage(
            content=[
                {
                    "type": "tool_use",
                    "id": "toolu-1",
                    "name": "file__read",
                    "input": {"path": "AGENTS.md"},
                }
            ],
            response_metadata={"stop_reason": "tool_use"},
        )
    )
    adapter = AnthropicAdapter(model="deepseek-chat", chat_model=model)
    response = await adapter.invoke(
        ChatRequest(
            provider="anthropic",
            model="deepseek-chat",
            messages=[ChatMessage.from_text("user", "Read the rules")],
            tools=[read_tool()],
        )
    )

    assert model.bound_tools[0]["input_schema"]["type"] == "object"
    assert response.tool_calls[0].call_id == "toolu-1"
    assert response.stop_reason == ModelStopReason.TOOL_CALLS


@pytest.mark.asyncio
async def test_streamed_tool_arguments_are_strictly_merged() -> None:
    model = ToolSpyModel(
        AIMessage(content=""),
        chunks=[
            AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                    "name": "file__",
                        "args": '{"pa',
                        "id": "call-2",
                        "index": 0,
                        "type": "tool_call_chunk",
                    }
                ],
            ),
            AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "read",
                        "args": 'th":"README.md"}',
                        "id": None,
                        "index": 0,
                        "type": "tool_call_chunk",
                    }
                ],
                response_metadata={"finish_reason": "tool_calls"},
                usage_metadata={"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
            ),
        ],
    )
    adapter = OpenAIAdapter(model="tool-model", chat_model=model)
    request = ChatRequest(
        provider="openai",
        model="tool-model",
        messages=[ChatMessage.from_text("user", "Read README")],
        tools=[read_tool()],
    )

    events = [event async for event in adapter.stream(request)]

    completed = next(event for event in events if event.kind == "tool_call_end")
    assert completed.tool_call.arguments == {"path": "README.md"}
    assert events[-1].kind == "message_end"
    assert events[-1].stop_reason == ModelStopReason.TOOL_CALLS
    assert events[-1].usage.total_tokens == 12


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        AIMessage(
            content="",
            invalid_tool_calls=[
                {
                        "name": "file__read",
                    "args": "{",
                    "id": "bad-json",
                    "error": "invalid JSON",
                    "type": "invalid_tool_call",
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "", "args": {}, "id": "missing-name", "type": "tool_call"}
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "file__read", "args": {"path": "a"}, "id": "same"},
                {"name": "file__read", "args": {"path": "b"}, "id": "same"},
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "file__read",
                    "args": {"path": 123},
                    "id": "bad-schema",
                    "type": "tool_call",
                }
            ],
        ),
    ],
)
async def test_malformed_or_unsafe_tool_calls_are_rejected(message: AIMessage) -> None:
    adapter = OpenAIAdapter(model="tool-model", chat_model=ToolSpyModel(message))

    with pytest.raises(ToolCallProtocolError):
        await adapter.invoke(
            ChatRequest(
                provider="openai",
                model="tool-model",
                messages=[ChatMessage.from_text("user", "read")],
                tools=[read_tool()],
            )
        )
