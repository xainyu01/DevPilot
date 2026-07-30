"""Run credential-safe, real DeepSeek tool-calling protocol smokes."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from packages.contracts import ChatMessage, ChatRequest
from packages.local_settings import LocalSettingsStore
from packages.model_gateway import AnthropicAdapter, OpenAIAdapter

READ_TOOL = {
    "name": "file.read",
    "description": "Read one UTF-8 text file from the registered project.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Project-relative file path.",
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    },
}


async def run_smoke(endpoint_id: str, model_name: str) -> dict[str, object]:
    settings = LocalSettingsStore(Path.cwd()).load()
    endpoint = settings.endpoint(endpoint_id)
    common = {
        "model": model_name,
        "provider_id": endpoint.endpoint_id,
        "api_key": endpoint.resolve_api_key(),
        "base_url": endpoint.resolve_base_url(),
    }
    if endpoint.provider == "openai":
        adapter = OpenAIAdapter(**common)
    elif endpoint.provider == "anthropic":
        adapter = AnthropicAdapter(**common)
    else:
        raise RuntimeError(f"{endpoint_id} is not an OpenAI/Anthropic-compatible endpoint")
    response = await adapter.invoke(
        ChatRequest(
            provider=endpoint.endpoint_id,
            model=model_name,
            messages=[
                ChatMessage.from_text(
                    "system",
                    "You are testing native tool calling. You must call the provided tool; "
                    "do not answer with text or JSON. Tool names may be transport-encoded.",
                ),
                ChatMessage.from_text(
                    "user",
                    "Call the provided file-reading tool exactly once with path README.md.",
                ),
            ],
            tools=[READ_TOOL],
            temperature=0,
            max_tokens=256,
            metadata={"purpose": "real-tool-call-smoke"},
        )
    )
    if len(response.tool_calls) != 1:
        raise RuntimeError(
            f"{endpoint_id}/{model_name} returned {len(response.tool_calls)} tool calls"
        )
    call = response.tool_calls[0]
    if call.name != "file.read" or call.arguments != {"path": "README.md"}:
        raise RuntimeError(
            f"{endpoint_id}/{model_name} returned an unexpected normalized tool call"
        )
    metadata = response.response_metadata
    return {
        "endpoint_id": endpoint.endpoint_id,
        "protocol": endpoint.provider,
        "requested_model": model_name,
        "provider_model": metadata.get("provider_model"),
        "provider_request_id": metadata.get("provider_request_id"),
        "finish_reason": response.finish_reason,
        "stop_reason": response.stop_reason,
        "usage": response.usage.model_dump(),
        "tool_call": {
            "call_id": call.call_id,
            "name": call.name,
            "arguments": call.arguments,
            "source": "model_response",
        },
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--endpoint",
        action="append",
        dest="endpoints",
        help="Endpoint ID to test; defaults to both configured DeepSeek protocols.",
    )
    parser.add_argument("--model", default="deepseek-v4-flash")
    args = parser.parse_args()
    endpoint_ids = args.endpoints or ["deepseek-openai", "deepseek-anthropic"]
    results = [await run_smoke(endpoint_id, args.model) for endpoint_id in endpoint_ids]
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
