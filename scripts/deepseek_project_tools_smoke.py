"""Run a real DeepSeek write-tool smoke in an isolated ignored directory."""

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
    repository = Path.cwd().resolve()
    workspace = (repository / ".devpilot" / "agent-e2e" / "r3-write-smoke").resolve()
    workspace.relative_to((repository / ".devpilot" / "agent-e2e").resolve())
    workspace.mkdir(parents=True, exist_ok=True)
    if any(workspace.iterdir()):
        raise RuntimeError("R3 smoke workspace must be empty before the model run")
    endpoint = LocalSettingsStore(repository).load().endpoint("deepseek-openai")
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
    runtime = AgentRuntime(gateway, tool_runtime=ToolRuntime(workspace))
    result = await runtime.run(
        RunRequest(
            thread_id="deepseek-project-tools-smoke",
            run_id="deepseek-project-tools-smoke",
            provider=endpoint.endpoint_id,
            model=model_name,
            messages=[
                ChatMessage.from_text(
                    "system",
                    "Use native tools for every filesystem operation. Do not merely show code.",
                ),
                ChatMessage.from_text(
                    "user",
                    "Create directory proof, then create proof/note.txt with exact UTF-8 "
                    "content `created by DeepSeek tools\\n`. Inspect workspace.status and "
                    "finish with a concise report.",
                ),
            ],
            metadata={
                "actor_id": "r3-smoke",
                "capabilities": ["workspace.read", "workspace.write"],
            },
            max_iterations=8,
            max_tool_calls=8,
            max_tokens=20_000,
            max_wall_time_seconds=120,
        )
    )
    target = workspace / "proof" / "note.txt"
    payload = {
        "status": result.status,
        "stop_reason": result.stop_reason,
        "usage": result.usage.model_dump(),
        "tool_sequence": [
            {
                "call_id": item.call_id,
                "name": item.tool_name,
                "status": item.status,
            }
            for item in result.tool_results
        ],
        "created_files": [
            path.relative_to(workspace).as_posix()
            for path in workspace.rglob("*")
            if path.is_file()
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    if (
        result.status != "completed"
        or target.read_text(encoding="utf-8") != "created by DeepSeek tools\n"
        or not any(item.tool_name == "file.write" for item in result.tool_results)
    ):
        raise RuntimeError("DeepSeek did not create the proof file through DevPilot tools")


if __name__ == "__main__":
    asyncio.run(main())
