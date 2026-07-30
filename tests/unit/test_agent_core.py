from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from packages.agent_core import AgentRuntime
from packages.contracts import (
    AdapterHealth,
    AudioBlock,
    CapabilityError,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ImageBlock,
    ModelCapabilities,
    ModelStopReason,
    ModelStreamEvent,
    ModelToolCall,
    RunEventType,
    RunRequest,
    RunStatus,
    TextBlock,
    TokenUsage,
    ToolCall,
    ToolDefinition,
    ToolRisk,
)
from packages.model_gateway import ChatModelAdapter, FakeModel, ModelGateway
from packages.tool_runtime import (
    FilePatchTool,
    FileReadTool,
    Tool,
    ToolExecutionContext,
    ToolRegistry,
    ToolRuntime,
)


class ScriptedToolModel(ChatModelAdapter):
    provider = "scripted"
    model = "tool-model"

    def __init__(self, turns: list[tuple[str, list[ModelToolCall]]]) -> None:
        self.turns = turns
        self.requests: list[ChatRequest] = []
        self.index = 0

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(text=True, tools=True)

    def count_tokens(self, messages: list[ChatMessage]) -> TokenUsage:
        return TokenUsage(input_tokens=len(messages), total_tokens=len(messages))

    def healthcheck(self) -> AdapterHealth:
        return AdapterHealth(
            provider=self.provider,
            model=self.model,
            status="ready",
        )

    async def invoke(self, request: ChatRequest) -> ChatResponse:
        text, calls = self.turns[self.index]
        self.index += 1
        return ChatResponse(
            provider=self.provider,
            model=self.model,
            message=ChatMessage(
                role="assistant",
                content=[TextBlock(text=text)],
                tool_calls=calls,
            ),
            tool_calls=calls,
            usage=TokenUsage(input_tokens=5, output_tokens=2),
            stop_reason=(
                ModelStopReason.TOOL_CALLS if calls else ModelStopReason.TEXT_END
            ),
        )

    async def stream(self, request: ChatRequest):
        self.requests.append(request)
        response = await self.invoke(request)
        if response.text:
            yield ModelStreamEvent(
                provider=self.provider,
                model=self.model,
                kind="text_delta",
                text=response.text,
            )
        for index, call in enumerate(response.tool_calls):
            yield ModelStreamEvent(
                provider=self.provider,
                model=self.model,
                kind="tool_call_end",
                index=index,
                tool_call_index=index,
                tool_call_id=call.call_id,
                tool_name=call.name,
                tool_call=call,
                tool_call_complete=True,
            )
        yield ModelStreamEvent(
            provider=self.provider,
            model=self.model,
            kind="message_end",
            index=len(response.tool_calls) + 1,
            done=True,
            usage=response.usage,
            stop_reason=response.stop_reason,
            finish_reason="tool_calls" if response.tool_calls else "stop",
        )


class TestCheckTool(Tool):
    definition = ToolDefinition(
        name="test.check",
        description="Verify the sample file contains the expected value.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}, "expected": {"type": "string"}},
            "required": ["path", "expected"],
            "additionalProperties": False,
        },
        risk=ToolRisk.READ_ONLY,
        required_capabilities=["workspace.read"],
    )

    async def execute(
        self,
        arguments: dict,
        context: ToolExecutionContext,
    ) -> str:
        text = (context.workspace_root / arguments["path"]).read_text(encoding="utf-8")
        if arguments["expected"] not in text:
            raise ValueError("expected value was not found")
        return "passed"


def model_call(call_id: str, name: str, **arguments) -> ModelToolCall:
    return ModelToolCall(call_id=call_id, name=name, arguments=arguments)


def scripted_request(**updates) -> RunRequest:
    value = RunRequest(
        thread_id="scripted-thread",
        run_id="scripted-run",
        provider="scripted",
        model="tool-model",
        messages=[ChatMessage.from_text("user", "complete the task")],
        metadata={
            "capabilities": ["workspace.read", "workspace.write", "shell.execute"],
            "actor_id": "agent-user",
        },
    )
    return value.model_copy(update=updates)


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


@pytest.mark.asyncio
async def test_model_autonomously_reads_file_and_receives_tool_result(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("project rules", encoding="utf-8")
    model = ScriptedToolModel(
        [
            ("", [model_call("read-1", "file.read", path="README.md")]),
            ("I read the project rules.", []),
        ]
    )
    runtime = AgentRuntime(
        ModelGateway([model]),
        tool_runtime=ToolRuntime(tmp_path),
    )

    result = await runtime.run(scripted_request())

    assert result.status == RunStatus.COMPLETED
    assert result.tool_results[0].output == "project rules"
    assert result.usage.total_tokens == 14
    assert model.requests[0].tools
    assert model.requests[1].messages[-1].role == "tool"
    assert model.requests[1].messages[-1].content[0].tool_call_id == "read-1"
    assert model.requests[0].messages[-1].tool_calls == []
    model_events = [
        event for event in result.events if event.type == RunEventType.MODEL_OUTPUT
    ]
    assert model_events[0].data["tool_calls"][0]["name"] == "file.read"


@pytest.mark.asyncio
async def test_model_can_iterate_read_write_and_test(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("old", encoding="utf-8")
    registry = ToolRegistry([FileReadTool(), FilePatchTool(), TestCheckTool()])
    model = ScriptedToolModel(
        [
            ("", [model_call("read", "file.read", path="sample.txt")]),
            (
                "",
                [
                    model_call(
                        "write",
                        "file.patch",
                        path="sample.txt",
                        old_text="old",
                        new_text="new",
                    )
                ],
            ),
            (
                "",
                [model_call("test", "test.check", path="sample.txt", expected="new")],
            ),
            ("Updated sample.txt and verified it.", []),
        ]
    )
    runtime = AgentRuntime(
        ModelGateway([model]),
        tool_runtime=ToolRuntime(tmp_path, registry=registry),
    )

    result = await runtime.run(scripted_request())

    assert result.status == RunStatus.COMPLETED
    assert [item.tool_name for item in result.tool_results] == [
        "file.read",
        "file.patch",
        "test.check",
    ]
    assert all(item.status == "succeeded" for item in result.tool_results)
    assert (tmp_path / "sample.txt").read_text(encoding="utf-8") == "new"
    assert result.checkpoint.state["iteration"] == 4
    assert result.checkpoint.state["tool_call_count"] == 3


@pytest.mark.asyncio
async def test_multiple_model_tool_calls_keep_call_ids_aligned(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    (tmp_path / "b.txt").write_text("B", encoding="utf-8")
    model = ScriptedToolModel(
        [
            (
                "",
                [
                    model_call("call-a", "file.read", path="a.txt"),
                    model_call("call-b", "file.read", path="b.txt"),
                ],
            ),
            ("Read both files.", []),
        ]
    )
    runtime = AgentRuntime(
        ModelGateway([model]),
        tool_runtime=ToolRuntime(tmp_path),
    )

    result = await runtime.run(scripted_request())

    assert [item.call_id for item in result.tool_results] == ["call-a", "call-b"]
    tool_messages = [
        message
        for message in model.requests[1].messages
        if message.role == "tool"
    ]
    assert [message.content[0].tool_call_id for message in tool_messages] == [
        "call-a",
        "call-b",
    ]


@pytest.mark.asyncio
async def test_tool_failure_is_returned_to_model_for_corrected_retry(tmp_path: Path) -> None:
    (tmp_path / "exists.txt").write_text("ok", encoding="utf-8")
    model = ScriptedToolModel(
        [
            ("", [model_call("bad", "file.read", path="missing.txt")]),
            ("", [model_call("fixed", "file.read", path="exists.txt")]),
            ("Recovered after correcting the path.", []),
        ]
    )
    runtime = AgentRuntime(
        ModelGateway([model]),
        tool_runtime=ToolRuntime(tmp_path),
    )

    result = await runtime.run(scripted_request())

    assert result.status == RunStatus.COMPLETED
    assert [item.status for item in result.tool_results] == ["failed", "succeeded"]
    failed_message = model.requests[1].messages[-1]
    assert failed_message.role == "tool"
    assert failed_message.content[0].is_error is True


@pytest.mark.asyncio
async def test_model_generated_high_risk_call_resumes_same_loop(tmp_path: Path) -> None:
    model = ScriptedToolModel(
        [
            (
                "",
                [
                    model_call(
                        "shell-1",
                        "shell.exec",
                        command=[sys.executable, "-c", "print('ok')"],
                    )
                ],
            ),
            ("The approved command completed.", []),
        ]
    )
    runtime = AgentRuntime(
        ModelGateway([model]),
        tool_runtime=ToolRuntime(tmp_path),
    )

    paused = await runtime.run(scripted_request())

    assert paused.status == RunStatus.PAUSED
    assert paused.pending_approval is not None
    resumed = await runtime.approve(
        "scripted-thread",
        "scripted-run",
        paused.pending_approval.request_id,
        approved=True,
        decided_by="agent-user",
    )

    assert resumed.status == RunStatus.COMPLETED
    assert resumed.tool_results[0].call_id == "shell-1"
    assert resumed.tool_results[0].status == "succeeded"
    assert model.index == 2


@pytest.mark.asyncio
async def test_repeated_tool_call_result_is_safely_terminated(tmp_path: Path) -> None:
    (tmp_path / "same.txt").write_text("same", encoding="utf-8")
    model = ScriptedToolModel(
        [
            ("", [model_call("same-1", "file.read", path="same.txt")]),
            ("", [model_call("same-2", "file.read", path="same.txt")]),
            ("", [model_call("same-3", "file.read", path="same.txt")]),
        ]
    )
    runtime = AgentRuntime(
        ModelGateway([model]),
        tool_runtime=ToolRuntime(tmp_path),
    )

    result = await runtime.run(scripted_request())

    assert result.status == RunStatus.FAILED
    assert result.stop_reason == "repeated_tool_call"
    assert result.error["code"] == "agent_no_progress"
    assert len(result.tool_results) == 3


@pytest.mark.asyncio
async def test_iteration_and_tool_budgets_cannot_be_exceeded(tmp_path: Path) -> None:
    empty_model = ScriptedToolModel([("", []), ("", [])])
    iteration_runtime = AgentRuntime(
        ModelGateway([empty_model]),
        tool_runtime=ToolRuntime(tmp_path),
    )
    iteration_result = await iteration_runtime.run(
        scripted_request(max_iterations=1)
    )

    assert iteration_result.status == RunStatus.FAILED
    assert iteration_result.stop_reason == "max_iterations_exceeded"

    tool_model = ScriptedToolModel(
        [
            (
                "",
                [
                    model_call("one", "file.read", path="a"),
                    model_call("two", "file.read", path="b"),
                ],
            )
        ]
    )
    tool_runtime = AgentRuntime(
        ModelGateway([tool_model]),
        tool_runtime=ToolRuntime(tmp_path),
    )
    tool_result = await tool_runtime.run(
        scripted_request(run_id="tool-budget", max_tool_calls=1)
    )

    assert tool_result.status == RunStatus.FAILED
    assert tool_result.stop_reason == "max_tool_calls_exceeded"
    assert tool_result.tool_results == []
