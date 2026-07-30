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
    ModelStopReason,
    ModelStreamEvent,
    ModelToolCall,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
)

from .errors import AdapterNotImplementedError, ToolCallProtocolError
from .gateway import ChatModelAdapter
from .tool_calls import (
    normalize_tool_calls,
    parse_arguments,
    provider_tool_names,
    tool_definition,
    tool_schema_parts,
    validate_tool_arguments,
)


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
    response_metadata = getattr(message, "response_metadata", {}) or {}
    usage = (
        getattr(message, "usage_metadata", None)
        or response_metadata.get("token_usage")
        or response_metadata.get("usage")
        or {}
    )
    input_count = int(usage.get("input_tokens", input_tokens) or input_tokens)
    if "prompt_tokens" in usage:
        input_count = int(usage.get("prompt_tokens") or input_count)
    output_count = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
    input_details = usage.get("input_token_details", {})
    input_details = input_details if isinstance(input_details, dict) else {}
    cache_read = int(
        input_details.get(
            "cache_read",
            usage.get("prompt_cache_hit_tokens", usage.get("cache_read_input_tokens", 0)),
        )
        or 0
    )
    cache_write = int(
        input_details.get(
            "cache_creation",
            usage.get("cache_creation_input_tokens", 0),
        )
        or 0
    )
    total = int(
        usage.get("total_tokens", usage.get("total", input_count + output_count)) or 0
    )
    return TokenUsage(
        input_tokens=input_count,
        output_tokens=output_count,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        total_tokens=total,
    )


def _finish_reason(message: BaseMessage) -> str | None:
    metadata = getattr(message, "response_metadata", {}) or {}
    value = metadata.get("finish_reason") or metadata.get("stop_reason")
    return str(value) if value else None


def _stop_reason(finish_reason: str | None, *, has_tool_calls: bool = False) -> ModelStopReason:
    if has_tool_calls or finish_reason in {"tool_calls", "tool_use"}:
        return ModelStopReason.TOOL_CALLS
    if finish_reason in {"stop", "end_turn", "stop_sequence", "completed"}:
        return ModelStopReason.TEXT_END
    if finish_reason in {"length", "max_tokens", "model_length"}:
        return ModelStopReason.LENGTH_LIMIT
    if finish_reason in {"cancelled", "canceled"}:
        return ModelStopReason.CANCELLED
    if finish_reason in {"error", "provider_error"}:
        return ModelStopReason.PROVIDER_ERROR
    return ModelStopReason.UNKNOWN


class LangChainAdapter(ChatModelAdapter):
    """Shared request conversion and response normalization for providers."""

    provider: str
    model: str
    api_key_env: str

    def __init__(
        self,
        *,
        model: str,
        provider_id: str | None = None,
        chat_model: BaseChatModel | None = None,
        api_key: str | SecretStr | None = None,
        capabilities: ModelCapabilities,
    ) -> None:
        if provider_id:
            self.provider = provider_id
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

    def _tool_schemas(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        canonical_to_provider, _ = provider_tool_names(tools)
        return [
            tool_definition(
                tool,
                anthropic=False,
                provider_name=canonical_to_provider[tool_schema_parts(tool)[0]],
            )
            for tool in tools
        ]

    def _bound_model(self, request: ChatRequest) -> Any:
        model = self._ensure_model()
        if not request.tools:
            return model
        return model.bind_tools(self._tool_schemas(request.tools))

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

    def to_langchain_messages(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> list[BaseMessage]:
        canonical_to_provider, _ = provider_tool_names(tools or [])
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
                provider_calls = []
                for call in message.tool_calls:
                    provider_name = canonical_to_provider.get(call.name)
                    if provider_name is None:
                        raise ToolCallProtocolError(
                            f"assistant history references unavailable tool {call.name!r}"
                        )
                    provider_calls.append(
                        {
                            "id": call.call_id,
                            "name": provider_name,
                            "args": call.arguments,
                            "type": "tool_call",
                        }
                    )
                converted.append(
                    AIMessage(
                        content=content,
                        name=message.name,
                        tool_calls=provider_calls,
                    )
                )
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
                    ToolMessage(
                        content=content,
                        tool_call_id=tool_id,
                        name=(
                            canonical_to_provider.get(message.name, message.name)
                            if message.name
                            else None
                        ),
                    )
                )
            else:
                converted.append(HumanMessage(content=content, name=message.name))
        return converted

    async def invoke(self, request: ChatRequest) -> ChatResponse:
        self.validate_request(request)
        message = await self._bound_model(request).ainvoke(
            self.to_langchain_messages(request.messages, request.tools),
            config={"metadata": request.metadata},
        )
        if not isinstance(message, BaseMessage):
            message = AIMessage(content=str(message))
        text = _message_text(message)
        _, provider_to_canonical = provider_tool_names(request.tools)
        tool_calls = normalize_tool_calls(message, provider_to_canonical)
        validate_tool_arguments(tool_calls, request.tools)
        response_message = ChatMessage.from_text("assistant", text or "")
        metadata = dict(getattr(message, "response_metadata", {}) or {})
        if message.id:
            metadata.setdefault("provider_request_id", message.id)
        returned_model = metadata.get("model_name") or metadata.get("model")
        if returned_model:
            metadata.setdefault("provider_model", returned_model)
        finish_reason = _finish_reason(message)
        return ChatResponse(
            provider=self.provider,
            model=self.model,
            message=response_message,
            tool_calls=tool_calls,
            usage=_usage_from_message(
                message,
                input_tokens=self.count_tokens(request.messages).input_tokens,
            ),
            finish_reason=finish_reason,
            stop_reason=_stop_reason(finish_reason, has_tool_calls=bool(tool_calls)),
            response_metadata=metadata,
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ModelStreamEvent]:
        self.validate_request(request)
        input_tokens = self.count_tokens(request.messages).input_tokens
        index = 0
        tool_parts: dict[int, dict[str, Any]] = {}
        usage = TokenUsage(input_tokens=input_tokens)
        response_metadata: dict[str, Any] = {}
        finish_reason: str | None = None
        async for chunk in self._bound_model(request).astream(
            self.to_langchain_messages(request.messages, request.tools),
            config={"metadata": request.metadata},
        ):
            if not isinstance(chunk, BaseMessage):
                chunk = AIMessage(content=str(chunk))
            chunk_usage = _usage_from_message(chunk, input_tokens=input_tokens)
            usage = TokenUsage(
                input_tokens=max(usage.input_tokens, chunk_usage.input_tokens),
                output_tokens=max(usage.output_tokens, chunk_usage.output_tokens),
                cache_read_tokens=max(
                    usage.cache_read_tokens,
                    chunk_usage.cache_read_tokens,
                ),
                cache_write_tokens=max(
                    usage.cache_write_tokens,
                    chunk_usage.cache_write_tokens,
                ),
                total_tokens=max(usage.total_tokens, chunk_usage.total_tokens),
            )
            metadata = dict(getattr(chunk, "response_metadata", {}) or {})
            if chunk.id:
                metadata.setdefault("provider_request_id", chunk.id)
            returned_model = metadata.get("model_name") or metadata.get("model")
            if returned_model:
                metadata.setdefault("provider_model", returned_model)
            response_metadata.update(metadata)
            finish_reason = _finish_reason(chunk) or finish_reason
            text = _message_text(chunk)
            if text:
                yield ModelStreamEvent(
                    provider=self.provider,
                    model=self.model,
                    kind="text_delta",
                    text=text,
                    index=index,
                )
                index += 1
            for raw in getattr(chunk, "tool_call_chunks", None) or []:
                if not isinstance(raw, dict):
                    raise ToolCallProtocolError("provider returned a non-object tool call chunk")
                call_index = raw.get("index")
                if not isinstance(call_index, int) or call_index < 0:
                    raise ToolCallProtocolError("streamed tool call chunk is missing its index")
                current = tool_parts.setdefault(
                    call_index,
                    {"id": "", "name": "", "arguments": ""},
                )
                call_id = raw.get("id")
                name = raw.get("name")
                arguments_delta = raw.get("args", "")
                if call_id:
                    if current["id"] and current["id"] != call_id:
                        raise ToolCallProtocolError(
                            f"streamed tool call index {call_index} changed call ID"
                        )
                    current["id"] = str(call_id)
                if name:
                    current["name"] += str(name)
                if arguments_delta:
                    current["arguments"] += str(arguments_delta)
                yield ModelStreamEvent(
                    provider=self.provider,
                    model=self.model,
                    kind="tool_call_delta",
                    index=index,
                    tool_call_index=call_index,
                    tool_call_id=current["id"] or None,
                    tool_name=current["name"] or None,
                    arguments_delta=str(arguments_delta or ""),
                )
                index += 1
        completed_calls: list[ModelToolCall] = []
        seen_ids: set[str] = set()
        for call_index, raw in sorted(tool_parts.items()):
            call_id = raw["id"]
            name = raw["name"]
            if not call_id:
                raise ToolCallProtocolError(
                    f"streamed tool call index {call_index} is missing a call ID"
                )
            if call_id in seen_ids:
                raise ToolCallProtocolError(
                    f"provider returned duplicate streamed tool call ID {call_id!r}"
                )
            if not name:
                raise ToolCallProtocolError(
                    f"streamed tool call {call_id!r} is missing a tool name"
                )
            _, provider_to_canonical = provider_tool_names(request.tools)
            canonical_name = provider_to_canonical.get(name)
            if canonical_name is None:
                raise ToolCallProtocolError(
                    f"provider requested unknown or unavailable tool {name!r}"
                )
            call = ModelToolCall(
                call_id=call_id,
                name=canonical_name,
                arguments=parse_arguments(
                    raw["arguments"] or "{}",
                    call_id=call_id,
                    name=canonical_name,
                ),
            )
            validate_tool_arguments([call], request.tools)
            completed_calls.append(call)
            seen_ids.add(call_id)
            yield ModelStreamEvent(
                provider=self.provider,
                model=self.model,
                kind="tool_call_end",
                index=index,
                tool_call_index=call_index,
                tool_call_id=call.call_id,
                tool_name=call.name,
                tool_call=call,
                tool_call_complete=True,
            )
            index += 1
        normalized_reason = _stop_reason(
            finish_reason,
            has_tool_calls=bool(completed_calls),
        )
        yield ModelStreamEvent(
            provider=self.provider,
            model=self.model,
            kind="message_end",
            index=index,
            done=True,
            usage=usage,
            finish_reason=finish_reason,
            stop_reason=normalized_reason,
            response_metadata=response_metadata,
        )


class OpenAIAdapter(LangChainAdapter):
    """OpenAI adapter backed by ``langchain-openai``."""

    provider = "openai"
    api_key_env = "OPENAI_API_KEY"

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        *,
        provider_id: str | None = None,
        chat_model: BaseChatModel | None = None,
        api_key: str | SecretStr | None = None,
        base_url: str | None = None,
    ) -> None:
        self._base_url = base_url
        super().__init__(
            model=model,
            provider_id=provider_id,
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
        provider_id: str | None = None,
        chat_model: BaseChatModel | None = None,
        api_key: str | SecretStr | None = None,
        base_url: str | None = None,
    ) -> None:
        self._base_url = base_url
        super().__init__(
            model=model,
            provider_id=provider_id,
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
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return ChatAnthropic(**kwargs)

    def _tool_schemas(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        canonical_to_provider, _ = provider_tool_names(tools)
        return [
            tool_definition(
                tool,
                anthropic=True,
                provider_name=canonical_to_provider[tool_schema_parts(tool)[0]],
            )
            for tool in tools
        ]

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

    def __init__(self, model: str = "llama3.2", *, provider_id: str | None = None) -> None:
        if provider_id:
            self.provider = provider_id
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
        *,
        provider_id: str | None = None,
    ) -> None:
        if provider_id:
            self.provider = provider_id
        self.model = model
        self.response = response
        self.delay = delay

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            text=True,
            image=True,
            audio=False,
            pdf=True,
            tools=True,
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
