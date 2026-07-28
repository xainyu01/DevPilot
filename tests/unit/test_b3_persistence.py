from __future__ import annotations

from pathlib import Path

import pytest

from packages.agent_core import AgentRuntime
from packages.contracts import (
    ChatMessage,
    RunContext,
    RunEvent,
    RunEventType,
    RunRequest,
    RunStatus,
    SessionRecord,
)
from packages.memory import LongTermMemoryStore, SessionMemoryService
from packages.model_gateway import FakeModel, ModelGateway
from packages.persistence import (
    CheckpointRepository,
    Database,
    MemoryRepository,
    ProjectRepository,
    RuleRepository,
    RunRepository,
    SessionRepository,
)
from packages.project_context import ProjectContextService, RuleDiscovery


def test_session_messages_and_checkpoint_survive_new_repository_instances() -> None:
    database = Database("sqlite://")
    database.create_all()

    sessions = SessionRepository(database)
    session = sessions.create(SessionRecord(thread_id="thread-b3"))
    memory = SessionMemoryService(sessions)
    memory.append("thread-b3", ChatMessage.from_text("user", "remember this"))
    memory.append("thread-b3", ChatMessage.from_text("assistant", "restored"))
    summary = memory.summarize("thread-b3")

    checkpoint_store = CheckpointRepository(database)
    checkpoint_store.save(
        thread_id="thread-b3",
        run_id="run-b3",
        state={"messages": [ChatMessage.from_text("user", "paused")], "status": RunStatus.PAUSED},
        next_nodes=["call_model"],
        status=RunStatus.PAUSED,
        sequence=4,
    )

    restored_sessions = SessionRepository(database)
    restored = SessionMemoryService(restored_sessions).restore("thread-b3")
    restored_checkpoint = CheckpointRepository(database).get("thread-b3", "run-b3")

    assert restored.session.id == session.id
    assert [item.message.text_content() for item in restored.messages] == [
        "remember this",
        "restored",
    ]
    assert restored.summary is not None
    assert restored.summary.summary == summary.summary
    assert restored_checkpoint is not None
    assert restored_checkpoint.status == RunStatus.PAUSED
    assert restored_checkpoint.next_nodes == ["call_model"]
    assert restored_checkpoint.state["messages"][0]["content"][0]["text"] == "paused"


def test_long_term_memory_is_editable_and_blocks_credential_shaped_candidates(
    tmp_path: Path,
) -> None:
    database = Database("sqlite://")
    database.create_all()
    repository = MemoryRepository(database)
    store = LongTermMemoryStore(
        tmp_path / "MEMORY.md",
        repository=repository,
    )

    blocked = store.write_candidate(key="credential", content="api_key=sk-123456789012345678901234")
    created = store.add(key="style", content="Prefer small focused functions.")
    edited = store.edit(created.entry.id, content="Prefer small, focused functions.")
    disabled = store.set_enabled(edited.id, False)
    store.set_enabled(disabled.id, True)
    store.delete(edited.id)

    assert blocked.entry is None
    assert blocked.status.value == "blocked_sensitive"
    assert store.list_entries() == []
    assert repository.list(owner_id="local-user") == []
    assert "DevPilot Long-Term Memory" in (tmp_path / "MEMORY.md").read_text(encoding="utf-8")


def test_rule_discovery_records_scope_precedence_and_database_index(tmp_path: Path) -> None:
    root = tmp_path / "project"
    child = root / "src" / "feature"
    child.mkdir(parents=True)
    (root / "AGENTS.md").write_text("root rules", encoding="utf-8")
    (root / "CLAUDE.md").write_text("compat rules", encoding="utf-8")
    (root / ".devpilot").mkdir()
    (root / ".devpilot" / "PROJECT.md").write_text("project facts", encoding="utf-8")
    (child / "AGENTS.md").write_text("feature rules", encoding="utf-8")
    user_home = tmp_path / "user"
    (user_home / ".devpilot").mkdir(parents=True)
    (user_home / ".devpilot" / "MEMORY.md").write_text("user preference", encoding="utf-8")

    database = Database("sqlite://")
    database.create_all()
    project = ProjectRepository(database).get_or_create(
        name="sample",
        root_path=str(root),
    )
    context = ProjectContextService(RuleDiscovery(user_home=user_home)).discover_and_store(
        project_id=project.id,
        project_root=root,
        current_dir=child,
        repository=RuleRepository(database),
    )
    persisted = RuleRepository(database).list(project.id)

    assert {rule.filename for rule in context.rules} == {
        "MEMORY.md",
        "AGENTS.md",
        "CLAUDE.md",
        "PROJECT.md",
    }
    assert context.rules[0].source_kind == "user_memory"
    assert context.rules[-1].content == "feature rules"
    assert context.rules[0].priority < context.rules[-1].priority
    assert len(persisted) == len(context.rules)
    assert "feature rules" in context.merged_text


def test_run_events_are_stored_separately_from_checkpoint_state() -> None:
    database = Database("sqlite://")
    database.create_all()
    runs = RunRepository(database)
    context = RunContext(
        thread_id="thread-events",
        run_id="run-events",
        provider="fake",
        model="fake-model",
    )
    runs.start_run(context)
    runs.save_event(
        RunEvent(
            sequence=1,
            thread_id=context.thread_id,
            run_id=context.run_id,
            type=RunEventType.RUN_STARTED,
            status=RunStatus.RUNNING,
        )
    )

    events = runs.list_events(context.run_id)
    table_names = set(database.engine.dialect.get_table_names(database.engine.connect()))

    assert events[0].type == RunEventType.RUN_STARTED
    assert "run_events" in table_names
    assert "checkpoints" in table_names


@pytest.mark.asyncio
async def test_agent_runtime_can_use_durable_b3_checkpoint_and_event_backends() -> None:
    database = Database("sqlite://")
    database.create_all()
    checkpoints = CheckpointRepository(database)
    runs = RunRepository(database)
    runtime = AgentRuntime(
        ModelGateway([FakeModel(response="persisted")]),
        checkpoint_store=checkpoints,
        run_repository=runs,
    )

    result = await runtime.run(
        RunRequest(
            thread_id="durable-thread",
            run_id="durable-run",
            provider="fake",
            model="fake-model",
            messages=[ChatMessage.from_text("user", "hello")],
        )
    )

    assert result.status == RunStatus.COMPLETED
    assert CheckpointRepository(database).get("durable-thread", "durable-run") is not None
    assert runs.list_events("durable-run")[-1].type == RunEventType.RUN_COMPLETED
