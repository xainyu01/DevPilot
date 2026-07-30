from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from packages.contracts import (
    ApprovalDecision,
    ApprovalScope,
    ToolCall,
    ToolRisk,
)
from packages.tool_runtime import TestRunTool, ToolRegistry, ToolRuntime


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
        arguments={"command": [sys.executable, "--version"]},
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
    assert "Python" in completed.output
    assert any(record.event_type == "approval.decided" for record in runtime.audit_log.list())
    assert any(record.event_type == "tool.completed" for record in runtime.audit_log.list())


def test_default_registry_declares_risk_classes(tmp_path: Path) -> None:
    runtime = ToolRuntime(tmp_path)
    definitions = {item.name: item for item in runtime.registry.definitions()}

    assert set(definitions) == {
        "file.delete",
        "file.diff",
        "file.list",
        "file.mkdir",
        "file.read",
        "file.search",
        "file.write",
        "file.patch",
        "git.exec",
        "repo.scan",
        "shell.exec",
        "test.run",
        "workspace.status",
    }
    assert definitions["file.read"].risk == ToolRisk.READ_ONLY
    assert definitions["file.patch"].risk == ToolRisk.RECOVERABLE_WRITE
    assert definitions["shell.exec"].risk == ToolRisk.HIGH_RISK
    assert "command" not in definitions["test.run"].input_schema["properties"]


@pytest.mark.asyncio
async def test_empty_workspace_create_write_status_diff_and_hash_guard(
    tmp_path: Path,
) -> None:
    runtime = ToolRuntime(tmp_path)
    writable = context(runtime, "workspace.read", "workspace.write")

    directory = await runtime.execute(
        ToolCall(name="file.mkdir", arguments={"path": "src/nested"}),
        context=writable,
    )
    created = await runtime.execute(
        ToolCall(
            name="file.write",
            arguments={
                "path": "src/nested/app.py",
                "content": "VALUE = 1\n",
                "create_only": True,
            },
        ),
        context=writable,
    )
    status = await runtime.execute(
        ToolCall(name="workspace.status", arguments={}),
        context=writable,
    )
    diff = await runtime.execute(
        ToolCall(name="file.diff", arguments={"path": "src/nested/app.py"}),
        context=writable,
    )

    assert directory.status == created.status == "succeeded"
    created_payload = json.loads(created.output)
    assert created_payload["created"] is True
    assert json.loads(status.output)["added"] == ["src/nested/app.py"]
    assert "+VALUE = 1" in diff.output

    stale = await runtime.execute(
        ToolCall(
            name="file.write",
            arguments={
                "path": "src/nested/app.py",
                "content": "VALUE = 2\n",
                "overwrite": True,
                "expected_sha256": "0" * 64,
            },
        ),
        context=writable,
    )
    guarded = await runtime.execute(
        ToolCall(
            name="file.write",
            arguments={
                "path": "src/nested/app.py",
                "content": "VALUE = 2\n",
                "overwrite": True,
                "expected_sha256": created_payload["sha256"],
            },
        ),
        context=writable,
    )

    assert stale.status == "failed"
    assert guarded.status == "succeeded"
    assert (tmp_path / "src/nested/app.py").read_text(encoding="utf-8") == "VALUE = 2\n"


@pytest.mark.asyncio
async def test_write_traversal_and_symlink_escape_are_rejected(tmp_path: Path) -> None:
    runtime = ToolRuntime(tmp_path)
    writable = context(runtime, "workspace.read", "workspace.write")
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir(exist_ok=True)

    traversal = await runtime.execute(
        ToolCall(
            name="file.write",
            arguments={"path": "../escape.txt", "content": "no"},
        ),
        context=writable,
    )
    assert traversal.status == "denied"

    link = tmp_path / "outside-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        return
    escaped = await runtime.execute(
        ToolCall(
            name="file.write",
            arguments={"path": "outside-link/escape.txt", "content": "no"},
        ),
        context=writable,
    )
    assert escaped.status == "denied"
    assert not (outside / "escape.txt").exists()


@pytest.mark.asyncio
async def test_delete_is_approval_gated_and_never_recursive(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("delete", encoding="utf-8")
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "keep.txt").write_text("keep", encoding="utf-8")
    runtime = ToolRuntime(tmp_path)
    current = context(runtime, "workspace.read", "workspace.delete")
    call = ToolCall(name="file.delete", arguments={"path": "target.txt"})

    pending = await runtime.execute(call, context=current)
    assert pending.status == "pending_approval"
    runtime.decide_approval(
        ApprovalDecision(
            request_id=pending.approval_request.request_id,
            approved=True,
            decided_by="tester",
        ),
        actor_id="tester",
        session_id="session-1",
    )
    deleted = await runtime.execute(call, context=current)
    assert deleted.status == "succeeded"
    assert not target.exists()

    directory_call = ToolCall(name="file.delete", arguments={"path": "nonempty"})
    directory_pending = await runtime.execute(directory_call, context=current)
    runtime.decide_approval(
        ApprovalDecision(
            request_id=directory_pending.approval_request.request_id,
            approved=True,
            decided_by="tester",
        ),
        actor_id="tester",
        session_id="session-1",
    )
    rejected = await runtime.execute(directory_call, context=current)
    assert rejected.status == "failed"
    assert (nonempty / "keep.txt").exists()


@pytest.mark.asyncio
async def test_repo_scan_and_file_list_are_bounded_and_project_relative(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    runtime = ToolRuntime(tmp_path)
    current = context(runtime, "workspace.read")

    listing = await runtime.execute(
        ToolCall(name="file.list", arguments={"max_depth": 2, "max_entries": 10}),
        context=current,
    )
    scan = await runtime.execute(
        ToolCall(name="repo.scan", arguments={}),
        context=current,
    )

    assert json.loads(listing.output)["entries"][0]["path"] == "app.py"
    scan_payload = json.loads(scan.output)
    assert scan_payload["root_path"] == "."
    assert scan_payload["languages"] == {"python": 1}
    assert not (tmp_path / ".devpilot").exists()


@pytest.mark.asyncio
async def test_test_run_nonzero_exit_is_failed_with_evidence(tmp_path: Path) -> None:
    script = tmp_path / "failing.py"
    script.write_text(
        "import sys\nprint('expected failure')\nsys.exit(3)\n",
        encoding="utf-8",
    )
    command = [sys.executable, "failing.py"]
    registry = ToolRegistry([TestRunTool(tmp_path, allowed_commands=[command])])
    runtime = ToolRuntime(tmp_path, registry=registry)

    result = await runtime.execute(
        ToolCall(name="test.run", arguments={"command": command}),
        context=context(runtime, "test.execute"),
    )

    assert result.status == "failed"
    assert result.error["code"] == "test_failed"
    evidence = json.loads(result.output)
    assert evidence["returncode"] == 3
    assert "expected failure" in evidence["stdout"]


@pytest.mark.asyncio
async def test_test_timeout_terminates_child_process_tree(tmp_path: Path) -> None:
    marker = tmp_path / "child-finished.txt"
    child = tmp_path / "child.py"
    child.write_text(
        "import pathlib, time\n"
        "time.sleep(0.5)\n"
        "pathlib.Path('child-finished.txt').write_text('bad', encoding='utf-8')\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, 'child.py'])\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )
    command = [sys.executable, "parent.py"]
    registry = ToolRegistry([TestRunTool(tmp_path, allowed_commands=[command])])
    runtime = ToolRuntime(tmp_path, registry=registry)

    result = await runtime.execute(
        ToolCall(
            name="test.run",
            arguments={"command": command, "timeout_seconds": 0.1},
        ),
        context=context(runtime, "test.execute"),
    )
    time.sleep(0.8)

    assert result.status == "failed"
    assert result.error["code"] == "command_timeout"
    assert not marker.exists()


@pytest.mark.asyncio
async def test_shell_rejects_inline_code_and_outside_path_arguments(tmp_path: Path) -> None:
    runtime = ToolRuntime(tmp_path)
    current = context(runtime, "shell.execute")

    inline_call = ToolCall(
        name="shell.exec",
        arguments={"command": [sys.executable, "-c", "print('unsafe')"]},
    )
    outside_call = ToolCall(
        name="shell.exec",
        arguments={"command": ["git", "-C", str(tmp_path.parent), "status"]},
    )
    inline = await runtime.execute(
        inline_call,
        context=current,
    )
    outside = await runtime.execute(
        outside_call,
        context=current,
    )

    assert inline.status == "pending_approval"
    assert outside.status == "pending_approval"
    for pending, call in (
        (inline, inline_call),
        (outside, outside_call),
    ):
        runtime.decide_approval(
            ApprovalDecision(
                request_id=pending.approval_request.request_id,
                approved=True,
                decided_by="tester",
            ),
            actor_id="tester",
            session_id="session-1",
        )
        rejected = await runtime.execute(call, context=current)
        assert rejected.status == "failed"
