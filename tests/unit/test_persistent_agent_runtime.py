from pathlib import Path

import aiosqlite
import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from packages.agent_core import AgentRuntime
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
)
from packages.model_gateway import ChatModelAdapter, ModelGateway
from packages.persistence import (
    ApprovalRepository,
    AuditRepository,
    CheckpointRepository,
    Database,
    PersistentApprovalStore,
    RunRepository,
)
from packages.tool_runtime import ToolRuntime


class DeleteThenFinishModel(ChatModelAdapter):
    provider = "scripted"
    model = "delete-model"

    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(text=True, tools=True)

    def count_tokens(self, messages: list[ChatMessage]) -> TokenUsage:
        return TokenUsage(input_tokens=len(messages), total_tokens=len(messages))

    def healthcheck(self) -> AdapterHealth:
        return AdapterHealth(provider=self.provider, model=self.model, status="ready")

    async def invoke(self, request: ChatRequest) -> ChatResponse:
        raise NotImplementedError

    async def stream(self, request: ChatRequest):
        self.calls += 1
        calls = (
            [
                ModelToolCall(
                    call_id="delete-once",
                    name="file.delete",
                    arguments={"path": "victim.txt"},
                )
            ]
            if self.calls == 1
            else []
        )
        text = "" if calls else "Deletion was approved and verified."
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


@pytest.mark.asyncio
async def test_waiting_approval_survives_runtime_restart(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "victim.txt").write_text("delete me", encoding="utf-8")
    database = Database(f"sqlite:///{tmp_path / 'runs.db'}")
    database.create_all()
    database.ensure_real_agent_columns()
    request = RunRequest(
        thread_id="restart-thread",
        run_id="restart-run",
        provider="scripted",
        model="delete-model",
        messages=[ChatMessage.from_text("user", "Delete victim.txt after approval.")],
        metadata={
            "actor_id": "admin",
            "session_id": "session",
            "capabilities": ["workspace.read", "workspace.delete"],
        },
    )
    model = DeleteThenFinishModel()
    graph_path = tmp_path / "graph.sqlite"

    first_connection = await aiosqlite.connect(graph_path)
    first_runtime = AgentRuntime(
        ModelGateway([model]),
        checkpoint_store=CheckpointRepository(database),
        graph_checkpointer=AsyncSqliteSaver(first_connection),
        tool_runtime=ToolRuntime(
            workspace,
            approvals=PersistentApprovalStore(
                ApprovalRepository(database), session_id=request.thread_id
            ),
            audit_log=AuditRepository(database),
        ),
        run_repository=RunRepository(database),
    )
    paused = await first_runtime.run(request)
    await first_connection.close()

    assert paused.status == RunStatus.PAUSED
    assert paused.pending_approval is not None
    assert (workspace / "victim.txt").exists()

    second_connection = await aiosqlite.connect(graph_path)
    second_runtime = AgentRuntime(
        ModelGateway([model]),
        checkpoint_store=CheckpointRepository(database),
        graph_checkpointer=AsyncSqliteSaver(second_connection),
        tool_runtime=ToolRuntime(
            workspace,
            approvals=PersistentApprovalStore(
                ApprovalRepository(database), session_id=request.thread_id
            ),
            audit_log=AuditRepository(database),
        ),
        run_repository=RunRepository(database),
    )
    events = RunRepository(database).list_events(request.run_id)
    second_runtime.restore(
        request,
        status=RunStatus.PAUSED,
        event_sequence=events[-1].sequence,
    )
    result = await second_runtime.approve(
        request.thread_id,
        request.run_id,
        paused.pending_approval.request_id,
        approved=True,
        decided_by="admin",
    )
    await second_connection.close()

    assert result.status == RunStatus.COMPLETED
    assert not (workspace / "victim.txt").exists()
    persisted = RunRepository(database).get(request.run_id)
    assert persisted is not None
    assert persisted["status"] == "completed"
    tool_outputs = [
        event
        for event in RunRepository(database).list_events(request.run_id)
        if event.type.value == "tool.output"
    ]
    assert len(tool_outputs) == 1
