import time
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import app, create_app
from packages.contracts import (
    AdapterHealth,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ModelCapabilities,
    ModelStopReason,
    ModelStreamEvent,
    ModelToolCall,
    TokenUsage,
)
from packages.model_gateway import ChatModelAdapter, ModelGateway


class ApprovalToolModel(ChatModelAdapter):
    provider = "fake"
    model = "fake-model"

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
        tool_call = (
            ModelToolCall(
                call_id="api-delete-once",
                name="file.delete",
                arguments={"path": "victim.txt"},
            )
            if self.calls == 1
            else None
        )
        if tool_call is not None:
            yield ModelStreamEvent(
                provider=self.provider,
                model=self.model,
                kind="tool_call_end",
                tool_call=tool_call,
                tool_call_id=tool_call.call_id,
                tool_name=tool_call.name,
                tool_call_complete=True,
            )
        else:
            yield ModelStreamEvent(
                provider=self.provider,
                model=self.model,
                kind="text_delta",
                text="Approved deletion completed.",
            )
        yield ModelStreamEvent(
            provider=self.provider,
            model=self.model,
            kind="message_end",
            done=True,
            usage=TokenUsage(input_tokens=2, output_tokens=2),
            stop_reason=(
                ModelStopReason.TOOL_CALLS
                if tool_call is not None
                else ModelStopReason.TEXT_END
            ),
            finish_reason="tool_calls" if tool_call is not None else "stop",
        )


def authenticate(client: TestClient) -> str:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    token = response.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return token


def test_health_endpoint() -> None:
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "devpilot-api"}


def test_meta_declares_uv_and_stage_eight() -> None:
    response = TestClient(app).get("/api/v1/meta")

    assert response.status_code == 200
    assert response.json()["dependency_manager"] == "uv"
    assert response.json()["stage"] == 8
    assert response.json()["features"]["agent_core"] == "available"
    assert response.json()["features"]["frontend"] == "available"
    assert response.json()["features"]["web_hosting"] == "available_when_built"
    assert response.json()["features"]["desktop_shell"] == "deferred_optional"
    assert response.json()["features"]["project_registration"] == "available_with_path_validation"
    assert response.json()["features"]["runtime_logs"] == "available"
    assert (
        response.json()["features"]["authentication"]
        == "available_with_fixed_b7_accounts_and_signed_jwt"
    )
    assert response.json()["features"]["release_readiness"] == "available"


def test_fastapi_hosts_vite_production_build(tmp_path: Path) -> None:
    web_dist = tmp_path / "web-dist"
    web_dist.mkdir()
    (web_dist / "index.html").write_text("<title>DevPilot Web</title>", encoding="utf-8")
    web_app = create_app(
        database_url=f"sqlite:///{tmp_path / 'web.db'}",
        workspace_root=tmp_path,
        web_dist_path=web_dist,
    )

    response = TestClient(web_app).get("/")

    assert response.status_code == 200
    assert "DevPilot Web" in response.text


def test_local_web_first_conversation_and_runtime_logs(tmp_path: Path) -> None:
    web_app = create_app(
        database_url=f"sqlite:///{tmp_path / 'first-chat.db'}", workspace_root=tmp_path
    )

    with TestClient(web_app) as client:
        authenticate(client)
        project_root = tmp_path / "registered-project"
        project_root.mkdir()
        project = client.post(
            "/api/v1/projects",
            json={"name": "first-project", "root_path": str(project_root)},
        )
        assert project.status_code == 201
        project_payload = project.json()
        assert Path(project_payload["root_path"]) == project_root.resolve()

        session = client.post(
            "/api/v1/sessions",
            json={"thread_id": "first-web-thread", "project_id": project_payload["id"]},
        ).json()
        run = client.post(
            f"/api/v1/sessions/{session['id']}/runs",
            json={
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "hello from the Web workbench"}],
                }
            },
        )
        assert run.status_code == 201
        assert run.json()["final_text"].startswith("Fake response")
        run_id = run.json()["context"]["run_id"]
        bound_runtime = web_app.state.agent_runtimes[("first-web-thread", run_id)]
        assert bound_runtime.tool_runtime.workspace_root == project_root.resolve()

        logs = client.get("/api/v1/runtime/logs").json()
        events = [item["event"] for item in logs]
        assert "service.initialized" in events
        assert "project.registered" in events
        assert "session.run.completed" in events


def test_project_runs_share_rules_history_and_reject_unauthorized_user(
    tmp_path: Path,
) -> None:
    web_app = create_app(
        database_url=f"sqlite:///{tmp_path / 'context.db'}", workspace_root=tmp_path
    )
    project_root = tmp_path / "context-project"
    project_root.mkdir()
    (project_root / "AGENTS.md").write_text(
        "R4_RULE: inspect existing files before editing.",
        encoding="utf-8",
    )

    with TestClient(web_app) as client:
        authenticate(client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "context-project", "root_path": str(project_root)},
        ).json()
        session = client.post(
            "/api/v1/sessions",
            json={"thread_id": "context-thread", "project_id": project["id"]},
        ).json()
        first = client.post(
            f"/api/v1/sessions/{session['id']}/runs",
            json={
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Remember first-turn-marker."}],
                }
            },
        )
        assert first.status_code == 201
        second = client.post(
            f"/api/v1/sessions/{session['id']}/runs",
            json={
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Use the prior context now."}],
                }
            },
        )
        assert second.status_code == 201
        run_id = second.json()["context"]["run_id"]
        runtime = web_app.state.agent_runtimes[("context-thread", run_id)]
        request = runtime._handles[("context-thread", run_id)].request
        rendered = "\n".join(message.text_content() for message in request.messages)
        assert "R4_RULE: inspect existing files before editing." in rendered
        assert "first-turn-marker" in rendered
        assert "Use the prior context now." in rendered
        assert str(project_root.resolve()) not in rendered
        assert runtime.tool_runtime.workspace_root == project_root.resolve()

        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin1", "password": "admin1"},
        )
        denied = client.post(
            f"/api/v1/sessions/{session['id']}/runs",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
            json={
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Read the private project."}],
                }
            },
        )
        assert denied.status_code == 403


def test_websocket_first_conversation_streams_terminal_event(tmp_path: Path) -> None:
    web_app = create_app(
        database_url=f"sqlite:///{tmp_path / 'websocket.db'}", workspace_root=tmp_path
    )

    with TestClient(web_app) as client:
        token = authenticate(client)
        session = client.post(
            "/api/v1/sessions", json={"thread_id": "websocket-thread", "title": "Web chat"}
        ).json()
        with client.websocket_connect(
            f"/api/v1/sessions/{session['id']}/events?access_token={token}"
        ) as websocket:
            websocket.send_json(
                {
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "hello over WebSocket"}],
                    }
                }
            )
            event_types = []
            while "run.completed" not in event_types:
                event_types.append(websocket.receive_json()["type"])

        snapshot = client.get(f"/api/v1/sessions/{session['id']}").json()
        assert "run.completed" in event_types
        assert snapshot["messages"][-1]["message"]["role"] == "assistant"


def test_background_run_query_usage_and_idempotent_reconnect(tmp_path: Path) -> None:
    web_app = create_app(
        database_url=f"sqlite:///{tmp_path / 'background.db'}", workspace_root=tmp_path
    )

    with TestClient(web_app) as client:
        authenticate(client)
        session = client.post(
            "/api/v1/sessions",
            json={"thread_id": "background-thread"},
        ).json()
        payload = {
            "run_id": "background-run",
            "background": True,
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "run in the background"}],
            },
        }
        created = client.post(
            f"/api/v1/sessions/{session['id']}/runs",
            json=payload,
        )
        assert created.status_code == 201
        for _ in range(100):
            record = client.get("/api/v1/runs/background-run")
            if record.json()["status"] == "completed":
                break
            time.sleep(0.01)
        assert record.json()["status"] == "completed"

        events = client.get("/api/v1/runs/background-run/events").json()
        usage = client.get("/api/v1/runs/background-run/usage").json()
        changes = client.get("/api/v1/runs/background-run/changes").json()
        repeated = client.post(
            f"/api/v1/sessions/{session['id']}/runs",
            json=payload,
        )
        after_reconnect = client.get("/api/v1/runs/background-run/events").json()

        assert events[-1]["type"] == "run.completed"
        assert usage["usage"]["total_tokens"] > 0
        assert changes == {"run_id": "background-run", "changes": []}
        assert repeated.json()["id"] == "background-run"
        assert len(after_reconnect) == len(events)


def test_approval_api_resumes_high_risk_tool(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = ApprovalToolModel()
    monkeypatch.setattr(
        "apps.api.main._model_gateway",
        lambda _settings: ModelGateway([model]),
    )
    web_app = create_app(
        database_url=f"sqlite:///{tmp_path / 'approval.db'}", workspace_root=tmp_path
    )
    project_root = tmp_path / "approval-project"
    project_root.mkdir()
    victim = project_root / "victim.txt"
    victim.write_text("delete after approval", encoding="utf-8")

    with TestClient(web_app) as client:
        authenticate(client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "approval", "root_path": str(project_root)},
        ).json()
        session = client.post(
            "/api/v1/sessions",
            json={"thread_id": "approval-thread", "project_id": project["id"]},
        ).json()
        paused = client.post(
            f"/api/v1/sessions/{session['id']}/runs",
            json={
                "run_id": "approval-run",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Delete victim.txt."}],
                },
            },
        )
        assert paused.status_code == 201
        assert paused.json()["status"] == "paused"
        approval = paused.json()["pending_approval"]
        assert victim.exists()

        decided = client.post(
            f"/api/v1/runs/approval-run/approvals/{approval['request_id']}",
            json={"approved": True, "scope": "once"},
        )

        assert decided.status_code == 200
        assert decided.json()["status"] == "completed"
        assert not victim.exists()
        events = client.get("/api/v1/runs/approval-run/events").json()
        assert [event["type"] for event in events].count("tool.output") == 1
        assert "approval.decided" in [event["type"] for event in events]


def test_server_rejects_false_completion_and_persists_verification(
    tmp_path: Path,
) -> None:
    web_app = create_app(
        database_url=f"sqlite:///{tmp_path / 'verification.db'}", workspace_root=tmp_path
    )
    project_root = tmp_path / "empty-verification-project"
    project_root.mkdir()

    with TestClient(web_app) as client:
        authenticate(client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "verification", "root_path": str(project_root)},
        ).json()
        session = client.post(
            "/api/v1/sessions",
            json={"thread_id": "verification-thread", "project_id": project["id"]},
        ).json()
        result = client.post(
            f"/api/v1/sessions/{session['id']}/runs",
            json={
                "run_id": "false-completion-run",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Create the tested program."}],
                },
                "acceptance_criteria": [
                    "Create `required.py`.",
                    "Run tests with test.run.",
                ],
            },
        )
        persisted = client.get("/api/v1/runs/false-completion-run")

        assert result.status_code == 201
        assert result.json()["status"] == "failed"
        assert result.json()["stop_reason"] == "verification_repeated_without_progress"
        assert result.json()["verification"]["satisfied"] is False
        assert persisted.json()["verification"] == result.json()["verification"]
        assert list(project_root.iterdir()) == []


def test_project_registration_rejects_missing_or_file_path(tmp_path: Path) -> None:
    web_app = create_app(
        database_url=f"sqlite:///{tmp_path / 'paths.db'}", workspace_root=tmp_path
    )
    file_path = tmp_path / "not-a-directory.txt"
    file_path.write_text("file", encoding="utf-8")

    with TestClient(web_app) as client:
        authenticate(client)
        missing = client.post(
            "/api/v1/projects",
            json={"name": "missing", "root_path": str(tmp_path / "missing")},
        )
        regular_file = client.post(
            "/api/v1/projects",
            json={"name": "file", "root_path": str(file_path)},
        )

    assert missing.status_code == 422
    assert "not accessible" in missing.json()["detail"]
    assert regular_file.status_code == 422
    assert "existing directory" in regular_file.json()["detail"]


def test_tools_endpoint_exposes_policy_gated_definitions() -> None:
    with TestClient(app) as client:
        authenticate(client)
        response = client.get("/api/v1/tools")

    assert response.status_code == 200
    tools = {item["name"]: item for item in response.json()}
    assert tools["shell.exec"]["risk"] == "high_risk"
    assert tools["file.patch"]["risk"] == "recoverable_write"
