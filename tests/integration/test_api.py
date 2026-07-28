from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import app, create_app


def authenticate(client: TestClient) -> str:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    token = response.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return token


def test_health_endpoint() -> None:
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "codeassist-api"}


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
    (web_dist / "index.html").write_text("<title>CodeAssist Web</title>", encoding="utf-8")
    web_app = create_app(
        database_url=f"sqlite:///{tmp_path / 'web.db'}",
        workspace_root=tmp_path,
        web_dist_path=web_dist,
    )

    response = TestClient(web_app).get("/")

    assert response.status_code == 200
    assert "CodeAssist Web" in response.text


def test_local_web_first_conversation_and_runtime_logs(tmp_path: Path) -> None:
    web_app = create_app(
        database_url=f"sqlite:///{tmp_path / 'first-chat.db'}", workspace_root=tmp_path
    )

    with TestClient(web_app) as client:
        authenticate(client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "first-project", "root_path": "."},
        )
        assert project.status_code == 201
        project_payload = project.json()
        assert Path(project_payload["root_path"]) == tmp_path.resolve()

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

        logs = client.get("/api/v1/runtime/logs").json()
        events = [item["event"] for item in logs]
        assert "service.initialized" in events
        assert "project.registered" in events
        assert "session.run.completed" in events


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
