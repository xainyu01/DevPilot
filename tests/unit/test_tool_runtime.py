from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from packages.contracts import (
    ApprovalDecision,
    ApprovalScope,
    ToolCall,
    ToolRisk,
)
from packages.tool_runtime import ToolRuntime


def context(runtime: ToolRuntime, *capabilities: str):
    return runtime.default_context(
        actor_id="tester",
        session_id="session-1",
        capabilities=set(capabilities) or {"workspace.read"},
    )


@pytest.mark.asyncio
async def test_file_read_and_search_stay_inside_workspace(tmp_path: Path) -> None:
    source = tmp_path / "sample.txt"
    source.write_text("first line\nneedle here\n", encoding="utf-8")
    runtime = ToolRuntime(tmp_path)

    read = await runtime.execute(
        ToolCall(name="file.read", arguments={"path": "sample.txt"}),
        context=context(runtime),
    )
    search = await runtime.execute(
        ToolCall(name="file.search", arguments={"query": "needle"}),
        context=context(runtime),
    )

    assert read.status == "succeeded"
    assert "first line" in read.output
    assert json.loads(search.output)["matches"][0]["line"] == 2

    outside = await runtime.execute(
        ToolCall(name="file.read", arguments={"path": str(tmp_path.parent / "sample.txt")}),
        context=context(runtime),
    )
    assert outside.status == "denied"
    assert outside.error is not None
    assert outside.error["code"] == "policy_denied"


@pytest.mark.asyncio
async def test_patch_requires_write_capability_and_applies_exact_change(tmp_path: Path) -> None:
    source = tmp_path / "sample.txt"
    source.write_text("before\n", encoding="utf-8")
    runtime = ToolRuntime(tmp_path)
    call = ToolCall(
        name="file.patch",
        arguments={"path": "sample.txt", "old_text": "before", "new_text": "after"},
    )

    denied = await runtime.execute(call, context=context(runtime))
    allowed = await runtime.execute(
        call,
        context=context(runtime, "workspace.read", "workspace.write"),
    )

    assert denied.status == "denied"
    assert allowed.status == "succeeded"
    assert source.read_text(encoding="utf-8") == "after\n"


@pytest.mark.asyncio
async def test_high_risk_shell_requires_approval_and_audits_decision(tmp_path: Path) -> None:
    runtime = ToolRuntime(tmp_path)
    call = ToolCall(
        name="shell.exec",
        arguments={"command": [sys.executable, "-c", "print('approved')"]},
    )
    current = context(runtime, "workspace.read", "shell.execute")

    pending = await runtime.execute(call, context=current)
    assert pending.status == "pending_approval"
    assert pending.approval_request is not None
    assert not any(record.event_type == "tool.completed" for record in runtime.audit_log.list())

    runtime.decide_approval(
        ApprovalDecision(
            request_id=pending.approval_request.request_id,
            approved=True,
            scope=ApprovalScope.ONCE,
            decided_by="tester",
        ),
        actor_id="tester",
        session_id="session-1",
    )
    completed = await runtime.execute(call, context=current)

    assert completed.status == "succeeded"
    assert "approved" in completed.output
    assert any(record.event_type == "approval.decided" for record in runtime.audit_log.list())
    assert any(record.event_type == "tool.completed" for record in runtime.audit_log.list())


def test_default_registry_declares_risk_classes(tmp_path: Path) -> None:
    runtime = ToolRuntime(tmp_path)
    definitions = {item.name: item for item in runtime.registry.definitions()}

    assert set(definitions) == {
        "file.read",
        "file.search",
        "file.patch",
        "shell.exec",
        "git.exec",
    }
    assert definitions["file.read"].risk == ToolRisk.READ_ONLY
    assert definitions["file.patch"].risk == ToolRisk.RECOVERABLE_WRITE
    assert definitions["shell.exec"].risk == ToolRisk.HIGH_RISK
