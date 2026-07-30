"""Drive the required real DeepSeek coding E2E without writing target project code."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.main import create_app

MODEL = "deepseek-v4-flash"
OPENAI_ENDPOINT = "deepseek-openai"
ANTHROPIC_ENDPOINT = "deepseek-anthropic"
TERMINAL_EVENTS = {
    "run.completed",
    "run.partial",
    "run.failed",
    "run.cancelled",
    "run.paused",
}


def _isolated_paths(repository: Path) -> tuple[Path, Path]:
    base = (repository / ".devpilot" / "agent-e2e").resolve()
    target = (base / "deepseek-event-lens").resolve()
    results = (base / "results").resolve()
    if target != (base / "deepseek-event-lens").resolve() or target.parent != base:
        raise RuntimeError("refusing to use an unexpected E2E target")
    return target, results


def _prepare_empty_target(target: Path) -> None:
    if target.exists():
        if target.name != "deepseek-event-lens" or target.parent.name != "agent-e2e":
            raise RuntimeError("refusing to remove an unexpected directory")
        shutil.rmtree(target)
    target.mkdir(parents=True)
    if any(target.iterdir()):
        raise RuntimeError("E2E target is not empty")


def _require_ok(response: Any, expected: int = 200) -> dict[str, Any]:
    if response.status_code != expected:
        raise RuntimeError(
            f"API request failed with HTTP {response.status_code}: {response.text[:1000]}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("API response was not an object")
    return payload


def _run_over_websocket(
    client: TestClient,
    *,
    session_id: str,
    token: str,
    run_id: str,
    endpoint_id: str,
    task: str,
    acceptance_criteria: list[str],
) -> list[dict[str, Any]]:
    selected_events: list[dict[str, Any]] = []
    with client.websocket_connect(
        f"/api/v1/sessions/{session_id}/events?access_token={token}"
    ) as websocket:
        websocket.send_json(
            {
                "run_id": run_id,
                "endpoint_id": endpoint_id,
                "model": MODEL,
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": task}],
                },
                "acceptance_criteria": acceptance_criteria,
                "capability_limit": [
                    "workspace.read",
                    "workspace.write",
                    "test.execute",
                ],
                "max_tokens": 200_000,
            }
        )
        while True:
            event = websocket.receive_json()
            event_type = event.get("type")
            if event_type != "model.delta":
                selected_events.append(event)
            if event_type in TERMINAL_EVENTS:
                break
    if selected_events[-1].get("type") == "run.paused":
        _approve_safe_diagnostics(client, run_id)
    persisted = client.get(f"/api/v1/runs/{run_id}/events")
    if persisted.status_code != 200:
        raise RuntimeError(f"could not reload events for {run_id}")
    return [
        event
        for event in persisted.json()
        if event.get("type") != "model.delta"
    ]


def _approve_safe_diagnostics(client: TestClient, run_id: str) -> None:
    for _ in range(3):
        run = _require_ok(client.get(f"/api/v1/runs/{run_id}"))
        if run.get("status") != "paused":
            return
        pending = run.get("pending_approval")
        if not isinstance(pending, dict) or pending.get("tool_name") != "shell.exec":
            raise RuntimeError("real E2E paused for a non-diagnostic operation")
        arguments = pending.get("arguments", {})
        command = arguments.get("command") if isinstance(arguments, dict) else None
        if (
            not isinstance(command, list)
            or not command
            or command[0] not in {"python", "python3", "pytest"}
            or "-c" in command
            or "pip" in command
            or any(".." in str(item).replace("\\", "/").split("/") for item in command)
        ):
            raise RuntimeError(f"refusing unsafe diagnostic approval: {command!r}")
        response = client.post(
            f"/api/v1/runs/{run_id}/approvals/{pending['request_id']}",
            json={"approved": True, "scope": "once"},
        )
        _require_ok(response)
    run = _require_ok(client.get(f"/api/v1/runs/{run_id}"))
    if run.get("status") == "paused":
        raise RuntimeError("real E2E exceeded safe diagnostic approval limit")


def _tool_evidence(events: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    requested = [
        event["data"]["tool_name"]
        for event in events
        if event.get("type") == "tool.requested"
    ]
    outputs = [
        event["data"]
        for event in events
        if event.get("type") == "tool.output"
    ]
    return requested, outputs


def _assert_completed(run: dict[str, Any], label: str) -> None:
    if run.get("status") != "completed":
        raise RuntimeError(
            f"{label} did not complete: status={run.get('status')} "
            f"stop_reason={run.get('stop_reason')} verification={run.get('verification')}"
        )


def _assert_test_success(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    matching = [
        item
        for item in outputs
        if item.get("tool_name") == "test.run" and item.get("status") == "succeeded"
    ]
    if not matching:
        raise RuntimeError("no successful test.run Tool Result was persisted")
    raw = matching[-1].get("output")
    try:
        payload = json.loads(raw) if isinstance(raw, str) else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError("test.run output was not JSON") from exc
    if payload.get("returncode") != 0:
        raise RuntimeError("test.run did not return exit code zero")
    return payload


def _assert_cli(target: Path, *, top: int | None = None) -> dict[str, Any]:
    command = [
        sys.executable,
        str(target / "event_lens.py"),
        str(target / "examples" / "events.jsonl"),
    ]
    if top is not None:
        command.extend(["--top", str(top)])
    completed = subprocess.run(
        command,
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload.get("total"), int) or payload["total"] < 3:
        raise RuntimeError("Event Lens CLI returned an invalid total")
    if not isinstance(payload.get("by_level"), dict) or not isinstance(
        payload.get("by_event"), dict
    ):
        raise RuntimeError("Event Lens CLI omitted required summary maps")
    if top is not None and len(payload["by_event"]) > top:
        raise RuntimeError("Event Lens --top did not limit event counts")
    return payload


def _run_summary(
    client: TestClient,
    *,
    run_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    run = _require_ok(client.get(f"/api/v1/runs/{run_id}"))
    usage = _require_ok(client.get(f"/api/v1/runs/{run_id}/usage"))
    changes = _require_ok(client.get(f"/api/v1/runs/{run_id}/changes"))
    tools, outputs = _tool_evidence(events)
    request_ids = [
        call.get("provider_request_id")
        for call in usage.get("model_calls", [])
        if call.get("provider_request_id")
    ]
    return {
        "run_id": run_id,
        "status": run.get("status"),
        "stop_reason": run.get("stop_reason"),
        "endpoint_id": usage.get("provider"),
        "model": usage.get("model"),
        "provider_request_ids": request_ids,
        "usage": usage.get("usage"),
        "metrics": usage.get("metrics"),
        "tool_sequence": tools,
        "tool_results": [
            {
                "call_id": item.get("call_id"),
                "tool_name": item.get("tool_name"),
                "status": item.get("status"),
                "error": item.get("error"),
            }
            for item in outputs
        ],
        "changes": changes.get("changes"),
        "verification": run.get("verification"),
    }


def main() -> None:
    repository = Path.cwd().resolve()
    target, results = _isolated_paths(repository)
    _prepare_empty_target(target)
    results.mkdir(parents=True, exist_ok=True)
    e2e_id = uuid4().hex
    database_path = results / f"runtime-{e2e_id}.sqlite"
    app = create_app(
        database_url=f"sqlite:///{database_path.as_posix()}",
        workspace_root=repository,
    )
    create_run_id = f"event-lens-create-{e2e_id}"
    increment_run_id = f"event-lens-increment-{e2e_id}"
    anthropic_run_id = f"event-lens-anthropic-{e2e_id}"

    with TestClient(app) as client:
        login = _require_ok(
            client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "admin"},
            )
        )
        token = login["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        project = _require_ok(
            client.post(
                "/api/v1/projects",
                json={"name": f"DeepSeek Event Lens {e2e_id}", "root_path": str(target)},
            ),
            expected=201,
        )
        session = _require_ok(
            client.post(
                "/api/v1/sessions",
                json={
                    "thread_id": f"event-lens-session-{e2e_id}",
                    "project_id": project["id"],
                    "title": "DeepSeek Event Lens real E2E",
                },
            ),
            expected=201,
        )

        create_events = _run_over_websocket(
            client,
            session_id=session["id"],
            token=token,
            run_id=create_run_id,
            endpoint_id=OPENAI_ENDPOINT,
            task=(
                "Build a complete Event Lens command-line project in this empty directory. "
                "Use native DevPilot tools for every filesystem operation; do not paste code "
                "as a substitute for tool calls. The Python CLI must use only the standard "
                "library, read a JSONL file whose objects contain level and event fields, and "
                "print deterministic JSON with total, by_level, and by_event counts. Create "
                "event_lens.py, README.md with usage, examples/events.jsonl with at least "
                "three records, and tests/test_event_lens.py written with unittest. Create "
                "both needed directories together in one turn using file.mkdir, then write "
                "all four files together in one turn using file.write. Invoke test.run with "
                "kind='test' and no command; do not use shell.exec for tests. If a tool or "
                "test fails, inspect the evidence, fix the project, and run test.run again "
                "before reporting completion. Batch independent Tool Calls in one response "
                "to stay within the server's hard Token budget. Do not manually invoke the "
                "new CLI through test.run; its unittest suite and the external E2E verifier "
                "will validate CLI behavior."
            ),
            acceptance_criteria=[
                "Create `event_lens.py` using only the Python standard library.",
                "Create `README.md`, `examples/events.jsonl`, and `tests/test_event_lens.py`.",
                "Use model-generated file.mkdir and file.write Tool Calls for project files.",
                "Run the discovered tests with test.run and obtain exit code zero.",
            ],
        )
        create_run = _require_ok(client.get(f"/api/v1/runs/{create_run_id}"))
        _assert_completed(create_run, "initial DeepSeek coding run")
        create_tools, create_outputs = _tool_evidence(create_events)
        required_tools = {"file.mkdir", "file.write", "test.run"}
        if not required_tools.issubset(create_tools):
            raise RuntimeError(
                f"initial run missed required model Tool Calls: {sorted(required_tools)}"
            )
        initial_test = _assert_test_success(create_outputs)
        expected_files = {
            "event_lens.py",
            "README.md",
            "examples/events.jsonl",
            "tests/test_event_lens.py",
        }
        actual_files = {
            path.relative_to(target).as_posix()
            for path in target.rglob("*")
            if path.is_file()
            and not {".pytest_cache", "__pycache__", ".devpilot"}.intersection(
                path.relative_to(target).parts
            )
        }
        if not expected_files.issubset(actual_files):
            raise RuntimeError(f"DeepSeek did not create required files: {sorted(actual_files)}")
        initial_cli = _assert_cli(target)

        increment_events = _run_over_websocket(
            client,
            session_id=session["id"],
            token=token,
            run_id=increment_run_id,
            endpoint_id=OPENAI_ENDPOINT,
            task=(
                "Incrementally extend the existing Event Lens project. First inspect the "
                "current implementation, tests, and README with file.read. Add a --top N "
                "option that limits by_event to the N most frequent events, ordered by count "
                "descending and then event name ascending. Preserve all existing behavior and "
                "files. Update event_lens.py, unittest coverage, and README through file.write "
                "with overwrite=true and omit expected_sha256 because no exact hash was "
                "returned, then run test.run and fix any failure."
                " Use test.run with kind='test' and no command, never shell.exec, and batch "
                "independent file.read/file.write calls in the same model response. Never "
                "send a manual event_lens.py command to test.run; the unittest suite and "
                "external E2E verifier will exercise the CLI."
            ),
            acceptance_criteria=[
                "Update `event_lens.py`, `tests/test_event_lens.py`, and `README.md`.",
                "The CLI supports --top N with deterministic count/name ordering.",
                "Preserve existing behavior and run test.run successfully.",
            ],
        )
        increment_run = _require_ok(client.get(f"/api/v1/runs/{increment_run_id}"))
        _assert_completed(increment_run, "incremental DeepSeek coding run")
        increment_tools, increment_outputs = _tool_evidence(increment_events)
        if not {"file.read", "file.write", "test.run"}.issubset(increment_tools):
            raise RuntimeError("incremental run did not inspect, write, and test through tools")
        increment_test = _assert_test_success(increment_outputs)
        increment_cli = _assert_cli(target, top=1)

        anthropic_events = _run_over_websocket(
            client,
            session_id=session["id"],
            token=token,
            run_id=anthropic_run_id,
            endpoint_id=ANTHROPIC_ENDPOINT,
            task=(
                "Use file.read to inspect README.md. Do not modify any file. Report the first "
                "Markdown heading and confirm that the documented --top option exists. You "
                "must base the answer on the Tool Result."
            ),
            acceptance_criteria=[],
        )
        anthropic_run = _require_ok(client.get(f"/api/v1/runs/{anthropic_run_id}"))
        _assert_completed(anthropic_run, "Anthropic-compatible read-only run")
        anthropic_tools, _ = _tool_evidence(anthropic_events)
        if "file.read" not in anthropic_tools:
            raise RuntimeError("Anthropic-compatible run did not emit a file.read Tool Call")

        summary = {
            "schema_version": 1,
            "executed_at": datetime.now(UTC).isoformat(),
            "transport": "FastAPI TestClient WebSocket",
            "project_root_alias": ".",
            "project_files": sorted(actual_files),
            "initial_cli": initial_cli,
            "increment_cli": increment_cli,
            "initial_test": {
                "returncode": initial_test.get("returncode"),
                "stdout": str(initial_test.get("stdout", ""))[-4000:],
                "stderr": str(initial_test.get("stderr", ""))[-4000:],
            },
            "increment_test": {
                "returncode": increment_test.get("returncode"),
                "stdout": str(increment_test.get("stdout", ""))[-4000:],
                "stderr": str(increment_test.get("stderr", ""))[-4000:],
            },
            "runs": [
                _run_summary(client, run_id=create_run_id, events=create_events),
                _run_summary(client, run_id=increment_run_id, events=increment_events),
                _run_summary(client, run_id=anthropic_run_id, events=anthropic_events),
            ],
            "session_usage": _require_ok(
                client.get(f"/api/v1/sessions/{session['id']}/usage")
            ),
        }
        result_path = results / f"{create_run_id}.json"
        result_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "result": "passed",
                    "summary_path": result_path.relative_to(repository).as_posix(),
                    "run_ids": [
                        create_run_id,
                        increment_run_id,
                        anthropic_run_id,
                    ],
                    "tool_sequences": [
                        item["tool_sequence"] for item in summary["runs"]
                    ],
                    "usage": summary["session_usage"]["usage"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
