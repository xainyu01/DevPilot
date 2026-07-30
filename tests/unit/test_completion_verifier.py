from pathlib import Path

import pytest

from packages.agent_core import AgentRuntime, CompletionVerifier
from packages.contracts import (
    AdapterHealth,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ModelCapabilities,
    ModelStopReason,
    ModelStreamEvent,
    ModelToolCall,
    RunRequest,
    RunStatus,
    TokenUsage,
    ToolResult,
)
from packages.model_gateway import ChatModelAdapter, ModelGateway
from packages.tool_runtime import ToolRuntime


class CorrectionModel(ChatModelAdapter):
    provider = "scripted"
    model = "correction-model"

    def __init__(self, turns: list[tuple[str, list[ModelToolCall]]]) -> None:
        self.turns = turns
        self.index = 0

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(text=True, tools=True)

    def count_tokens(self, messages: list[ChatMessage]) -> TokenUsage:
        return TokenUsage(input_tokens=len(messages), total_tokens=len(messages))

    def healthcheck(self) -> AdapterHealth:
        return AdapterHealth(provider=self.provider, model=self.model, status="ready")

    async def invoke(self, request: ChatRequest) -> ChatResponse:
        raise NotImplementedError

    async def stream(self, request: ChatRequest):
        text, calls = self.turns[self.index]
        self.index += 1
        if text:
            yield ModelStreamEvent(
                provider=self.provider,
                model=self.model,
                kind="text_delta",
                text=text,
            )
        for call in calls:
            yield ModelStreamEvent(
                provider=self.provider,
                model=self.model,
                kind="tool_call_end",
                tool_call=call,
                tool_call_id=call.call_id,
                tool_name=call.name,
                tool_call_complete=True,
            )
        yield ModelStreamEvent(
            provider=self.provider,
            model=self.model,
            kind="message_end",
            done=True,
            usage=TokenUsage(input_tokens=3, output_tokens=2),
            stop_reason=(
                ModelStopReason.TOOL_CALLS if calls else ModelStopReason.TEXT_END
            ),
            finish_reason="tool_calls" if calls else "stop",
        )


def call(call_id: str, name: str, **arguments) -> ModelToolCall:
    return ModelToolCall(call_id=call_id, name=name, arguments=arguments)


def test_empty_directory_and_claimed_tests_do_not_verify(tmp_path: Path) -> None:
    runtime = ToolRuntime(tmp_path)
    report = CompletionVerifier().verify(
        task_text="Create a tested CLI.",
        final_text="Everything is complete and tests passed.",
        acceptance_criteria=["Create `cli.py`.", "Run unittest tests."],
        tool_results=[],
        tool_runtime=runtime,
    )

    assert not report.satisfied
    assert "no workspace change" in " ".join(report.issues)
    assert "no successful test.run evidence" in " ".join(report.issues)
    assert report.evidence["missing_required_paths"] == ["cli.py"]


def test_success_requires_real_change_test_and_required_paths(tmp_path: Path) -> None:
    runtime = ToolRuntime(tmp_path)
    (tmp_path / "cli.py").write_text("print('ok')", encoding="utf-8")
    report = CompletionVerifier().verify(
        task_text="Create a tested CLI.",
        final_text="Created and verified the CLI.",
        acceptance_criteria=["Create `cli.py`.", "Run tests."],
        tool_results=[
            ToolResult(
                call_id="test",
                tool_name="test.run",
                status="succeeded",
                output='{"exit_code": 0}',
            )
        ],
        tool_runtime=runtime,
    )

    assert report.satisfied
    assert report.outcome == "completed"
    assert report.evidence["workspace"]["added"] == ["cli.py"]


@pytest.mark.asyncio
async def test_verification_feedback_drives_write_and_test_correction(
    tmp_path: Path,
) -> None:
    model = CorrectionModel(
        [
            ("Everything is complete and tests passed.", []),
            (
                "",
                [
                    call(
                        "write-code",
                        "file.write",
                        path="solution.py",
                        content="def add(a, b):\n    return a + b\n",
                        mode="create_only",
                    ),
                    call(
                        "write-test",
                        "file.write",
                        path="test_solution.py",
                        content=(
                            "from solution import add\n\n"
                            "def test_add():\n"
                            "    assert add(2, 3) == 5\n"
                        ),
                        mode="create_only",
                    ),
                    call("run-tests", "test.run", kind="test"),
                ],
            ),
            ("Created both files and test.run passed.", []),
        ]
    )
    runtime = AgentRuntime(
        ModelGateway([model]),
        tool_runtime=ToolRuntime(tmp_path),
    )
    result = await runtime.run(
        RunRequest(
            thread_id="verify-thread",
            run_id="verify-run",
            provider=model.provider,
            model=model.model,
            messages=[ChatMessage.from_text("user", "Create and test the add function.")],
            acceptance_criteria=[
                "Create `solution.py`.",
                "Create `test_solution.py`.",
                "Run tests with test.run.",
            ],
            metadata={
                "actor_id": "admin",
                "capabilities": ["workspace.read", "workspace.write", "test.execute"],
            },
        )
    )

    assert result.status == RunStatus.COMPLETED
    assert result.verification["satisfied"] is True
    assert result.verification["evidence"]["successful_test_runs"] == 1
    assert model.index == 3


@pytest.mark.asyncio
async def test_repeated_false_completion_stops_as_failed(tmp_path: Path) -> None:
    model = CorrectionModel(
        [
            ("Tests passed.", []),
            ("Tests passed.", []),
        ]
    )
    result = await AgentRuntime(
        ModelGateway([model]),
        tool_runtime=ToolRuntime(tmp_path),
    ).run(
        RunRequest(
            thread_id="repeat-thread",
            run_id="repeat-run",
            provider=model.provider,
            model=model.model,
            messages=[ChatMessage.from_text("user", "Create a tested program.")],
            acceptance_criteria=["Create `program.py` and run tests."],
            metadata={
                "capabilities": ["workspace.read", "workspace.write", "test.execute"]
            },
        )
    )

    assert result.status == RunStatus.FAILED
    assert result.stop_reason == "verification_repeated_without_progress"
    assert result.verification["satisfied"] is False


@pytest.mark.asyncio
async def test_failed_test_is_fixed_and_next_test_run_verifies(tmp_path: Path) -> None:
    model = CorrectionModel(
        [
            (
                "",
                [
                    call(
                        "broken-code",
                        "file.write",
                        path="maths.py",
                        content="def add(a, b):\n    return a - b\n",
                        mode="create_only",
                    ),
                    call(
                        "maths-test",
                        "file.write",
                        path="test_maths.py",
                        content=(
                            "from maths import add\n\n"
                            "def test_add():\n"
                            "    assert add(2, 3) == 5\n"
                        ),
                        mode="create_only",
                    ),
                    call("failing-test", "test.run", kind="test"),
                ],
            ),
            (
                "",
                [
                    call(
                        "fix-code",
                        "file.patch",
                        path="maths.py",
                        old_text="return a - b",
                        new_text="return a + b",
                    ),
                    call("passing-test", "test.run", kind="test"),
                ],
            ),
            ("Fixed the bug and the second test.run passed.", []),
        ]
    )
    result = await AgentRuntime(
        ModelGateway([model]),
        tool_runtime=ToolRuntime(tmp_path),
    ).run(
        RunRequest(
            thread_id="repair-thread",
            run_id="repair-run",
            provider=model.provider,
            model=model.model,
            messages=[ChatMessage.from_text("user", "Implement and test add.")],
            acceptance_criteria=[
                "Create `maths.py` and `test_maths.py`.",
                "Run tests.",
            ],
            metadata={
                "capabilities": ["workspace.read", "workspace.write", "test.execute"]
            },
        )
    )

    assert result.status == RunStatus.COMPLETED
    assert result.verification["evidence"]["successful_test_runs"] == 1
    test_results = [
        item for item in result.tool_results if item.tool_name == "test.run"
    ]
    assert [item.status for item in test_results] == ["failed", "succeeded"]
