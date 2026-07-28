"""LangChain-backed provider adapters plus a deterministic FakeModel."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import SecretStr

from packages.contracts import (
    AdapterHealth,
    AudioBlock,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    DocumentBlock,
    ImageBlock,
    ModelCapabilities,
    ModelStreamEvent,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
)

from .errors import AdapterNotImplementedError
from .gateway import ChatModelAdapter


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    pieces: list[str] = []
    for item in content:
        if isinstance(item, str):
            pieces.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            pieces.append(item["text"])
    return "".join(pieces)


def _usage_from_message(message: BaseMessage, *, input_tokens: int = 0) -> TokenUsage:
    usage = getattr(message, "usage_metadata", None) or {}
    input_count = int(usage.get("input_tokens", input_tokens) or input_tokens)
    output_count = int(usage.get("output_tokens", 0) or 0)
    total = int(usage.get("total_tokens", input_count + output_count) or 0)
    return TokenUsage(input_tokens=input_count, output_tokens=output_count, total_tokens=total)


class LangChainAdapter(ChatModelAdapter):
    """Shared request conversion and response normalization for providers."""

    provider: str
    model: str
    api_key_env: str

    def __init__(
        self,
        *,
        model: str,
        chat_model: BaseChatModel | None = None,
        api_key: str | SecretStr | None = None,
        capabilities: ModelCapabilities,
    ) -> None:
        self.model = model
        self._chat_model = chat_model
        self._api_key = api_key
        self._capabilities = capabilities

    def capabilities(self) -> ModelCapabilities:
        return self._capabilities.model_copy(deep=True)

    def _configured(self) -> bool:
        if self._chat_model is not None:
            return True
        if isinstance(self._api_key, SecretStr):
            return bool(self._api_key.get_secret_value())
        return bool(self._api_key or os.getenv(self.api_key_env))

    def healthcheck(self) -> AdapterHealth:
        if self._chat_model is not None:
            return AdapterHealth(
                provider=self.provider,
                model=self.model,
                status="ready",
                detail="injected LangChain model",
            )
        if self._configured():
            return AdapterHealth(
                provider=self.provider,
                model=self.model,
                status="configured",
                detail=f"credentials available through {self.api_key_env}",
            )
        return AdapterHealth(
            provider=self.provider,
            model=self.model,
            status="unavailable",
            detail=f"set {self.api_key_env} or inject a LangChain model",
        )

    def count_tokens(self, messages: list[ChatMessage]) -> TokenUsage:
        input_tokens = sum(max(1, len(message.text_content()) // 4) for message in messages)
        input_tokens += sum(
            32
            for message in messages
            for block in message.content
            if not isinstance(block, TextBlock | ToolResultBlock)
        )
        return TokenUsage(input_tokens=input_tokens, total_tokens=input_tokens)

    def _ensure_model(self) -> BaseChatModel:
        if self._chat_model is None:
            self._chat_model = self._build_chat_model()
        return self._chat_model

    def _build_chat_model(self) -> BaseChatModel:
        raise NotImplementedError

    def _content_for_block(self, block: Any) -> str | dict[str, Any]:
        if isinstance(block, TextBlock):
            return block.text
        if isinstance(block, ImageBlock):
            source = block.url or f"attachment://{block.attachment_id}"
            return {
                "type": "image_url",
                "image_url": {"url": source, "detail": block.detail},
            }
        if isinstance(block, DocumentBlock):
            return {
                "type": "file",
                "file": {"file_id": block.attachment_id},
                "filename": block.filename,
            }
        if isinstance(block, AudioBlock):
            return {
                "type": "input_audio",
                "input_audio": {"file_id": block.attachment_id, "format": block.mime_type},
            }
        if isinstance(block, ToolResultBlock):
            return block.content
        raise TypeError(f"Unsupported content block: {type(block).__name__}")

    def to_langchain_messages(self, messages: list[ChatMessage]) -> list[BaseMessage]:
        converted: list[BaseMessage] = []
        for message in messages:
            parts = [self._content_for_block(block) for block in message.content]
            content: str | list[str | dict[str, Any]]
            if len(parts) == 1 and isinstance(parts[0], str):
                content = parts[0]
            else:
                content = parts
            if message.role == "system":
                converted.append(SystemMessage(content=content, name=message.name))
            elif message.role == "assistant":
                converted.append(AIMessage(content=content, name=message.name))
            elif message.role == "tool":
                tool_id = next(
                    (
                        block.tool_call_id
                        for block in message.content
                        if isinstance(block, ToolResultBlock)
                    ),
                    "unknown",
                )
                converted.append(
                    ToolMessage(content=content, tool_call_id=tool_id, name=message.name)
                )
            else:
                converted.append(HumanMessage(content=content, name=message.name))
        return converted

    async def invoke(self, request: ChatRequest) -> ChatResponse:
        self.validate_request(request)
        message = await self._ensure_model().ainvoke(
            self.to_langchain_messages(request.messages),
            config={"metadata": request.metadata},
        )
        if not isinstance(message, BaseMessage):
            message = AIMessage(content=str(message))
        text = _message_text(message)
        response_message = ChatMessage.from_text("assistant", text)
        return ChatResponse(
            provider=self.provider,
            model=self.model,
            message=response_message,
            usage=_usage_from_message(
                message,
                input_tokens=self.count_tokens(request.messages).input_tokens,
            ),
            finish_reason=(
                str(message.response_metadata.get("finish_reason"))
                if getattr(message, "response_metadata", {}).get("finish_reason")
                else None
            ),
            response_metadata=getattr(message, "response_metadata", {}),
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ModelStreamEvent]:
        self.validate_request(request)
        input_tokens = self.count_tokens(request.messages).input_tokens
        index = 0
        async for chunk in self._ensure_model().astream(
            self.to_langchain_messages(request.messages),
            config={"metadata": request.metadata},
        ):
            text = _message_text(chunk)
            if text:
                yield ModelStreamEvent(
                    provider=self.provider,
                    model=self.model,
                    text=text,
                    index=index,
                )
                index += 1
        yield ModelStreamEvent(
            provider=self.provider,
            model=self.model,
            index=index,
            done=True,
            usage=TokenUsage(input_tokens=input_tokens),
        )


class OpenAIAdapter(LangChainAdapter):
    """OpenAI adapter backed by ``langchain-openai``."""

    provider = "openai"
    api_key_env = "OPENAI_API_KEY"

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        *,
        chat_model: BaseChatModel | None = None,
        api_key: str | SecretStr | None = None,
        base_url: str | None = None,
    ) -> None:
        self._base_url = base_url
        super().__init__(
            model=model,
            chat_model=chat_model,
            api_key=api_key,
            capabilities=ModelCapabilities(
                text=True,
                image=True,
                audio=False,
                pdf=True,
                tools=True,
                structured_output=True,
            ),
        )

    def _build_chat_model(self) -> BaseChatModel:
        from langchain_openai import ChatOpenAI

        api_key: str | None
        if isinstance(self._api_key, SecretStr):
            api_key = self._api_key.get_secret_value()
        else:
            api_key = self._api_key
        kwargs: dict[str, Any] = {"model": self.model}
        if api_key:
            kwargs["api_key"] = api_key
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return ChatOpenAI(**kwargs)


class AnthropicAdapter(LangChainAdapter):
    """Anthropic adapter backed by ``langchain-anthropic``."""

    provider = "anthropic"
    api_key_env = "ANTHROPIC_API_KEY"

    def __init__(
        self,
        model: str = "claude-3-5-sonnet-latest",
        *,
        chat_model: BaseChatModel | None = None,
        api_key: str | SecretStr | None = None,
    ) -> None:
        super().__init__(
            model=model,
            chat_model=chat_model,
            api_key=api_key,
            capabilities=ModelCapabilities(
                text=True,
                image=True,
                audio=False,
                pdf=True,
                tools=True,
                structured_output=True,
            ),
        )

    def _build_chat_model(self) -> BaseChatModel:
        from langchain_anthropic import ChatAnthropic

        api_key: str | None
        if isinstance(self._api_key, SecretStr):
            api_key = self._api_key.get_secret_value()
        else:
            api_key = self._api_key
        kwargs: dict[str, Any] = {"model": self.model}
        if api_key:
            kwargs["api_key"] = api_key
        return ChatAnthropic(**kwargs)

    def _content_for_block(self, block: Any) -> str | dict[str, Any]:
        if isinstance(block, ImageBlock):
            if block.url:
                return {
                    "type": "image",
                    "source": {"type": "url", "url": block.url},
                }
            return {
                "type": "image",
                "source": {"type": "file", "file_id": block.attachment_id},
            }
        if isinstance(block, DocumentBlock):
            return {
                "type": "document",
                "source": {"type": "file", "file_id": block.attachment_id},
                "title": block.filename,
            }
        return super()._content_for_block(block)


# TODO（后续模型批次）：Ollama 仅保留配置与能力接口，当前批次不实现本地推理。
class OllamaAdapter(ChatModelAdapter):
    """Capability/configuration placeholder; local inference is a later batch."""

    provider = "ollama"

    def __init__(self, model: str = "llama3.2") -> None:
        self.model = model

    # TODO（后续模型批次）：接入 Ollama 客户端后再实现调用；当前必须返回明确未实现错误。
    async def invoke(self, request: ChatRequest) -> ChatResponse:
        raise AdapterNotImplementedError(
            "Ollama adapter is declared but not implemented in this batch"
        )

    # TODO（后续模型批次）：接入 Ollama 流式协议后再实现事件转换。
    async def stream(self, request: ChatRequest) -> AsyncIterator[ModelStreamEvent]:
        raise AdapterNotImplementedError(
            "Ollama adapter is declared but not implemented in this batch"
        )
        yield ModelStreamEvent(provider=self.provider, model=self.model)

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(text=True)

    # TODO（后续模型批次）：接入 Ollama tokenizer 后再实现精确 token 统计。
    def count_tokens(self, messages: list[ChatMessage]) -> TokenUsage:
        raise AdapterNotImplementedError(
            "Ollama token counting is not implemented in this batch"
        )

    # TODO（后续模型批次）：Ollama 服务可用性检查应在真实客户端接入后实现。
    def healthcheck(self) -> AdapterHealth:
        return AdapterHealth(
            provider=self.provider,
            model=self.model,
            status="not_implemented",
            detail="Ollama inference is reserved for a later batch",
        )


class FakeModel(ChatModelAdapter):
    """Deterministic model used for graph tests and local smoke runs."""

    provider = "fake"

    def __init__(
        self,
        model: str = "fake-model",
        response: str | None = None,
        delay: float = 0,
    ) -> None:
        self.model = model
        self.response = response
        self.delay = delay

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            text=True,
            image=True,
            audio=False,
            pdf=True,
            tools=False,
            structured_output=False,
        )

    def count_tokens(self, messages: list[ChatMessage]) -> TokenUsage:
        input_tokens = sum(max(1, len(message.text_content()) // 4) for message in messages)
        return TokenUsage(input_tokens=input_tokens, total_tokens=input_tokens)

    def healthcheck(self) -> AdapterHealth:
        return AdapterHealth(
            provider=self.provider,
            model=self.model,
            status="ready",
            detail="deterministic fake model",
        )

    def _response_text(self, request: ChatRequest) -> str:
        if self.response is not None:
            return self.response
        text = " ".join(message.text_content() for message in request.messages).strip()
        return f"Fake response: {text}" if text else "Fake response"

    async def invoke(self, request: ChatRequest) -> ChatResponse:
        self.validate_request(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        text = self._response_text(request)
        output_tokens = max(1, len(text) // 4)
        return ChatResponse(
            provider=self.provider,
            model=self.model,
            message=ChatMessage.from_text("assistant", text),
            usage=TokenUsage(
                input_tokens=self.count_tokens(request.messages).input_tokens,
                output_tokens=output_tokens,
            ),
            finish_reason="stop",
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ModelStreamEvent]:
        self.validate_request(request)
        text = self._response_text(request)
        for index, word in enumerate(text.split(" ")):
            if self.delay:
                await asyncio.sleep(self.delay)
            if index:
                word = f" {word}"
            yield ModelStreamEvent(
                provider=self.provider,
                model=self.model,
                text=word,
                index=index,
            )
        yield ModelStreamEvent(
            provider=self.provider,
            model=self.model,
            index=len(text.split(" ")),
            done=True,
            usage=TokenUsage(
                input_tokens=self.count_tokens(request.messages).input_tokens,
                output_tokens=max(1, len(text) // 4),
            ),
        )
