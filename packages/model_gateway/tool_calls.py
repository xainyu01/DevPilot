"""Provider-neutral Tool Calling names, parsing, and JSON Schema validation."""

from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from langchain_core.messages import BaseMessage

from packages.contracts import ModelToolCall

from .errors import ToolCallProtocolError


def parse_arguments(value: Any, *, call_id: str, name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise ToolCallProtocolError(
            f"tool call {call_id!r} for {name!r} has non-object arguments"
        )
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ToolCallProtocolError(
            f"tool call {call_id!r} for {name!r} has malformed JSON arguments"
        ) from exc
    if not isinstance(parsed, dict):
        raise ToolCallProtocolError(
            f"tool call {call_id!r} for {name!r} arguments must decode to an object"
        )
    return parsed


def normalize_tool_calls(
    message: BaseMessage,
    provider_to_canonical: dict[str, str],
) -> list[ModelToolCall]:
    invalid = getattr(message, "invalid_tool_calls", None) or []
    if invalid:
        raise ToolCallProtocolError("provider returned one or more invalid tool calls")
    raw_calls = list(getattr(message, "tool_calls", None) or [])
    if not raw_calls and isinstance(message.content, list):
        raw_calls = [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "args": item.get("input"),
            }
            for item in message.content
            if isinstance(item, dict) and item.get("type") == "tool_use"
        ]
    normalized: list[ModelToolCall] = []
    seen: set[str] = set()
    for raw in raw_calls:
        if not isinstance(raw, dict):
            raise ToolCallProtocolError("provider returned a non-object tool call")
        call_id = raw.get("id") or raw.get("call_id")
        provider_name = raw.get("name")
        if not isinstance(call_id, str) or not call_id.strip():
            raise ToolCallProtocolError("provider returned a tool call without an ID")
        if call_id in seen:
            raise ToolCallProtocolError(f"provider returned duplicate tool call ID {call_id!r}")
        if not isinstance(provider_name, str) or not provider_name.strip():
            raise ToolCallProtocolError(f"tool call {call_id!r} is missing a tool name")
        name = provider_to_canonical.get(provider_name)
        if name is None:
            raise ToolCallProtocolError(
                f"provider requested unknown or unavailable tool {provider_name!r}"
            )
        arguments = parse_arguments(
            raw.get("args", raw.get("arguments", {})),
            call_id=call_id,
            name=name,
        )
        seen.add(call_id)
        normalized.append(
            ModelToolCall(call_id=call_id, name=name, arguments=arguments)
        )
    return normalized


def tool_definition(
    raw: dict[str, Any],
    *,
    anthropic: bool,
    provider_name: str | None = None,
) -> dict[str, Any]:
    if raw.get("type") == "function" and isinstance(raw.get("function"), dict):
        function = raw["function"]
        name = function.get("name")
        description = function.get("description", "")
        schema = function.get("parameters", {"type": "object", "properties": {}})
    else:
        name = raw.get("name")
        description = raw.get("description", "")
        schema = raw.get(
            "input_schema",
            raw.get("parameters", {"type": "object", "properties": {}}),
        )
    if not isinstance(name, str) or not name.strip():
        raise ToolCallProtocolError("tool definition is missing a name")
    if not isinstance(description, str):
        raise ToolCallProtocolError(f"tool definition {name!r} has a non-string description")
    if not isinstance(schema, dict):
        raise ToolCallProtocolError(f"tool definition {name!r} has a non-object schema")
    external_name = provider_name or name
    if anthropic:
        return {"name": external_name, "description": description, "input_schema": schema}
    return {
        "type": "function",
        "function": {
            "name": external_name,
            "description": description,
            "parameters": schema,
        },
    }


def tool_schema_parts(raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    normalized = tool_definition(raw, anthropic=True)
    return str(normalized["name"]), normalized["input_schema"]


def provider_tool_names(
    definitions: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str]]:
    canonical_to_provider: dict[str, str] = {}
    provider_to_canonical: dict[str, str] = {}
    for definition in definitions:
        canonical, _ = tool_schema_parts(definition)
        provider_name = re.sub(r"[^A-Za-z0-9_-]", "__", canonical)
        if not provider_name or not re.fullmatch(r"[A-Za-z0-9_-]+", provider_name):
            raise ToolCallProtocolError(f"tool name {canonical!r} cannot be provider-encoded")
        other = provider_to_canonical.get(provider_name)
        if other is not None and other != canonical:
            raise ToolCallProtocolError(
                f"tool names {other!r} and {canonical!r} collide at provider boundary"
            )
        canonical_to_provider[canonical] = provider_name
        provider_to_canonical[provider_name] = canonical
    return canonical_to_provider, provider_to_canonical


def validate_tool_arguments(
    calls: list[ModelToolCall],
    definitions: list[dict[str, Any]],
) -> None:
    schemas: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        name, schema = tool_schema_parts(definition)
        if name in schemas:
            raise ToolCallProtocolError(f"duplicate tool definition {name!r}")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ToolCallProtocolError(f"tool definition {name!r} has an invalid schema") from exc
        schemas[name] = schema
    for call in calls:
        schema = schemas.get(call.name)
        if schema is None:
            raise ToolCallProtocolError(
                f"provider requested unknown or unavailable tool {call.name!r}"
            )
        try:
            Draft202012Validator(schema).validate(call.arguments)
        except ValidationError as exc:
            path = ".".join(str(item) for item in exc.absolute_path) or "<root>"
            raise ToolCallProtocolError(
                f"tool call {call.call_id!r} arguments fail schema validation at {path}"
            ) from exc
