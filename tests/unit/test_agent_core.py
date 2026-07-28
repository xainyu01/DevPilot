from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from packages.agent_core import AgentRuntime
from packages.contracts import (
    AudioBlock,
    CapabilityError,
    ChatMessage,
    ImageBlock,
    RunEventType,
    RunRequest,
    RunStatus,
    ToolCall,
)
from packages.model_gateway import FakeModel, ModelGateway
from packages.tool_runtime import ToolRuntime


def request(
    *,
    thread_id: str = "thread-1",
    run_id: str = "run-1",
    messages: list[ChatMessage] | None = None,
) -> RunRequest:
    return RunRequest(
        thread_id=thread_id,
        run_id=run_id,
        provider="fake",
        model="fake-model",
        messages=messages or [ChatMessage.from_text("user", "hello")],
    )


@pytest.mark.asyncio
async def test_fake_model_graph_emits_lifecycle_events_and_checkpoint() -> None:
    runtime = AgentRuntime(ModelGateway([FakeModel(response="hello world")]))

    result = await runtime.run(request())

    assert result.status == RunStatus.COMPLETED
    assert result.final_text == "hello world"
    assert result.checkpoint is not None
    assert result.checkpoint.status == RunStatus.COMPLETED
    assert [event.sequence for event in result.events] == list(
        range(1, len(result.events) + 1)
    )
    event_types = {event.type for event in result.events}
    assert {
        RunEventType.RUN_STARTED,
        RunEventType.PLAN_CREATED,
        RunEventType.MODEL_OUTPUT,
        RunEventType.RUN_COMPLETED,
    } <= event_types


@pytest.mark.asyncio
async def test_same_thread_supports_distinct_idempotent_runs() -> None:
    runtime = AgentRuntime(ModelGateway([FakeModel(response="ok")]))

    first = await runtime.run(request(run_id="run-a"))
    second = await runtime.run(request(run_id="run-b"))
    replay = await runtime.run(request(run_id="run-a"))

    assert first.status == second.status == RunStatus.COMPLETED
    assert first.context.thread_id == second.context.thread_id
    assert first.context.run_id != second.context.run_id
    assert replay.model_dump() == first.model_dump()
    assert runtime.checkpoint("thread-1", "run-a") is not None


@pytest.mark.asyncio
async def test_unsupported_content_returns_structured_capability_error() -> None:
    runtime = AgentRuntime(ModelGateway([FakeModel(response="should not run")]))
    image = AudioBlock(attachment_id="audio-1", mime_type="audio/mpeg")

    result = await runtime.run(
        request(messages=[ChatMessage(role="user", content=[image])])
    )

    assert result.status == RunStatus.FAILED
    assert isinstance(result.error, CapabilityError)
    assert result.error.code == "unsupported_capability"
    assert result.error.unsupported_blocks == ["audio"]
    assert RunEventType.MODEL_OUTPUT not in {event.type for event in result.events}


@pytest.mark.asyncio
async def test_pause_resume_reuses_checkpoint_and_does_not_complete_early() -> None:
    runtime = AgentRuntime(ModelGateway([FakeModel(response="one two three", delay=0.02)]))
    task = asyncio.create_task(runtime.run(request()))
    await asyncio.sleep(0.03)

    assert await runtime.pause("thread-1", "run-1", reason="user requested pause")
    paused = await task

    assert paused.status == RunStatus.PAUSED
    assert paused.checkpoint is not None
    assert paused.checkpoint.status == RunStatus.PAUSED
    assert RunEventType.RUN_PAUSED in {event.type for event in paused.events}
    assert RunEventType.RUN_COMPLETED not in {event.type for event in paused.events}

    resumed = await runtime.resume("thread-1", "run-1")

    assert resumed.status == RunStatus.COMPLETED
    assert resumed.final_text == "one two three"
    assert RunEventType.RUN_RESUMED in {event.type for event in resumed.events}
    assert [event.sequence for event in resumed.events] == list(
        range(1, len(resumed.events) + 1)
    )


@pytest.mark.asyncio
async def test_cancelled_run_is_not_reported_as_success() -> None:
    runtime = AgentRuntime(ModelGateway([FakeModel(response="one two three", delay=0.05)]))
    task = asyncio.create_task(runtime.run(request()))
    await asyncio.sleep(0.01)

    assert await runtime.cancel("thread-1", "run-1")
    cancelled = await task

    assert cancelled.status == RunStatus.CANCELLED
    assert cancelled.final_text is None
    assert cancelled.checkpoint is not None
    assert RunEventType.RUN_CANCELLED in {event.type for event in cancelled.events}
    assert RunEventType.RUN_COMPLETED not in {event.type for event in cancelled.events}


@pytest.mark.asyncio
async def test_stream_yields_events_in_sequence_order() -> None:
    runtime = AgentRuntime(ModelGateway([FakeModel(response="streamed output")]))

    events = [event async for event in runtime.stream(request())]

    assert events[-1].type == RunEventType.RUN_COMPLETED
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert any(event.type == RunEventType.MODEL_DELTA for event in events)


def test_content_block_contract_rejects_ambiguous_image_source() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        ImageBlock(url="https://example.com/image.png", attachment_id="attachment-1")


@pytest.mark.asyncio
async def test_high_risk_tool_pauses_graph_and_approval_resumes_it(tmp_path: Path) -> None:
    tool_runtime = ToolRuntime(tmp_path)
    runtime = AgentRuntime(
        ModelGateway([FakeModel(response="tool completed")]),
        tool_runtime=tool_runtime,
    )
    request_value = request()
    request_value = request_value.model_copy(
        update={
            "tool_calls": [
                ToolCall(
                    name="shell.exec",
                    arguments={"command": [sys.executable, "-c", "print('ok')"]},
                )
            ],
            "metadata": {
                "capabilities": ["workspace.read", "shell.execute"],
                "actor_id": "agent-user",
            },
        }
    )

    paused = await runtime.run(request_value)

    assert paused.status == RunStatus.PAUSED
    assert paused.pending_approval is not None
    assert RunEventType.APPROVAL_REQUIRED in {event.type for event in paused.events}

    resumed = await runtime.approve(
        "thread-1",
        "run-1",
        paused.pending_approval.request_id,
        approved=True,
        decided_by="agent-user",
    )

    assert resumed.status == RunStatus.COMPLETED
    assert resumed.final_text == "tool completed"
    assert resumed.tool_results[0].status == "succeeded"
