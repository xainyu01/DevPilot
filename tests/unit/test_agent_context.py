from pathlib import Path

import pytest

from packages.agent_core import ContextAssembler, ContextBudgetError, RunCoordinator
from packages.agent_core.graph import AgentRuntime
from packages.contracts import (
    ChatMessage,
    MemoryEntry,
    MemoryScope,
    ProjectRecord,
    ProjectRule,
    RepositoryProfile,
    StoredMessage,
)
from packages.model_gateway import FakeModel, ModelGateway


def _stored(session_id: str, ordinal: int, role: str, text: str) -> StoredMessage:
    return StoredMessage(
        session_id=session_id,
        ordinal=ordinal,
        message=ChatMessage.from_text(role, text),
    )


def test_context_includes_rules_diff_and_hides_absolute_root(tmp_path: Path) -> None:
    root = tmp_path / "secret-host-root"
    project = ProjectRecord(name="sample", root_path=str(root))
    current = ChatMessage.from_text("user", "Implement the requested change.")
    rule = ProjectRule(
        project_id=project.id,
        source_path=str(root / "AGENTS.md"),
        scope_path=str(root),
        filename="AGENTS.md",
        content="Always run the complete pytest suite.",
        priority=10,
        source_kind="agent",
    )

    assembly = ContextAssembler().assemble(
        current_message=current,
        history=[current],
        project=project,
        rules=[rule],
        workspace_diff=" M src/example.py",
        capabilities={"workspace.read"},
    )

    rendered = "\n".join(message.text_content() for message in assembly.messages)
    assert "Always run the complete pytest suite." in rendered
    assert "M src/example.py" in rendered
    assert str(root) not in rendered
    assert "represented to you only as `.`" in rendered


def test_coordinator_restores_prior_conversation() -> None:
    first = _stored("session", 1, "user", "Create src/first.py.")
    assistant = _stored("session", 2, "assistant", "Created src/first.py.")
    current = ChatMessage.from_text("user", "Now update that same file.")
    history = [first, assistant, _stored("session", 3, "user", current.text_content())]
    runtime = AgentRuntime(ModelGateway([FakeModel()]))

    prepared = RunCoordinator().prepare(
        runtime=runtime,
        thread_id="thread",
        run_id="run",
        provider="fake",
        model="fake",
        current_message=current,
        history=history,
        metadata={},
        acceptance_criteria=[],
    )

    rendered = "\n".join(message.text_content() for message in prepared.request.messages)
    assert "Create src/first.py." in rendered
    assert "Created src/first.py." in rendered
    assert "Now update that same file." in rendered


def test_context_trims_old_history_but_keeps_required_sections(tmp_path: Path) -> None:
    root = tmp_path / "project"
    project = ProjectRecord(name="large", root_path=str(root))
    current = ChatMessage.from_text("user", "Current task must remain.")
    history = [
        _stored("session", index, "user", f"old-{index} " + ("x" * 2_000))
        for index in range(1, 20)
    ]
    history.append(_stored("session", 20, "user", current.text_content()))
    rule = ProjectRule(
        project_id=project.id,
        source_path=str(root / "AGENTS.md"),
        scope_path=str(root),
        filename="AGENTS.md",
        content="MANDATORY_RULE",
        priority=10,
        source_kind="agent",
    )
    profile = RepositoryProfile(
        project_id=project.id,
        root_path=str(root),
        files=[
            {
                "path": f"src/{index}.py",
                "size": 10,
                "mtime_ns": index,
                "sha256": "a" * 64,
                "language": "python",
            }
            for index in range(500)
        ],
    )

    assembly = ContextAssembler().assemble(
        current_message=current,
        history=history,
        project=project,
        rules=[rule],
        repository_profile=profile,
        workspace_diff="MANDATORY_DIFF",
        max_tokens=3_000,
    )

    rendered = "\n".join(message.text_content() for message in assembly.messages)
    assert assembly.estimated_tokens <= 3_000
    assert assembly.trimmed_history_messages > 0
    assert "MANDATORY_RULE" in rendered
    assert "MANDATORY_DIFF" in rendered
    assert "Current task must remain." in rendered


def test_required_context_fails_closed_when_it_exceeds_budget(tmp_path: Path) -> None:
    project = ProjectRecord(name="sample", root_path=str(tmp_path))
    current = ChatMessage.from_text("user", "Do it.")

    with pytest.raises(ContextBudgetError, match="required safety rules"):
        ContextAssembler().assemble(
            current_message=current,
            history=[current],
            project=project,
            workspace_diff="diff " * 10_000,
            memories=[
                MemoryEntry(
                    owner_id="user",
                    scope=MemoryScope.USER,
                    key="ignored",
                    content="optional",
                )
            ],
            max_tokens=1_000,
        )
