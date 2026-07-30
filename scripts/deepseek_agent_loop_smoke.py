"""Run a credential-safe real DeepSeek read-only Agent loop."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from packages.agent_core import AgentRuntime
from packages.contracts import ChatMessage, RunRequest
from packages.local_settings import LocalSettingsStore
from packages.model_gateway import ModelGateway, OpenAIAdapter
from packages.tool_runtime import ToolRuntime


async def main() -> None:
    root = Path.cwd()
    endpoint = LocalSettingsStore(root).load().endpoint("deepseek-openai")
    model_name = "deepseek-v4-flash"
    gateway = ModelGateway(
        [
            OpenAIAdapter(
                model=model_name,
                provider_id=endpoint.endpoint_id,
                api_key=endpoint.resolve_api_key(),
                base_url=endpoint.resolve_base_url(),
            )
        ]
    )
    runtime = AgentRuntime(gateway, tool_runtime=ToolRuntime(root))
    result = await runtime.run(
        RunRequest(
            thread_id="deepseek-agent-loop-smoke",
            run_id="deepseek-agent-loop-smoke",
            provider=endpoint.endpoint_id,
            model=model_name,
            messages=[
                ChatMessage.from_text(
                    "system",
                    "Use the available native tools. Do not invent file contents.",
                ),
                ChatMessage.from_text(
                    "user",
                    "Read README.md with the file tool, then state only its first "
                    "Markdown heading.",
                ),
            ],
            metadata={
                "actor_id": "r2-smoke",
                "capabilities": ["workspace.read"],
            },
            max_iterations=4,
            max_tool_calls=4,
            max_tokens=10_000,
            max_wall_time_seconds=120,
        )
    )
    tool_events = [
        {
            "type": event.type,
            "call_id": event.data.get("call_id"),
            "tool_name": event.data.get("tool_name"),
            "status": event.data.get("status"),
        }
        for event in result.events
        if event.type in {"tool.requested", "tool.output"}
    ]
    payload = {
        "status": result.status,
        "stop_reason": result.stop_reason,
        "usage": result.usage.model_dump(),
        "tool_events": tool_events,
        "tool_results": [
            {
                "call_id": item.call_id,
                "tool_name": item.tool_name,
                "status": item.status,
            }
            for item in result.tool_results
        ],
        "final_text": result.final_text,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    if (
        result.status != "completed"
        or not result.tool_results
        or result.tool_results[0].tool_name != "file.read"
        or result.tool_results[0].status != "succeeded"
    ):
        raise RuntimeError("real DeepSeek Agent loop smoke did not complete safely")


if __name__ == "__main__":
    asyncio.run(main())
