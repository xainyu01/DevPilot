from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import create_app


def authenticate(client: TestClient) -> None:
    token = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
    ).json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})


def test_b3_api_persists_session_and_discovers_rules_after_app_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "codeassist.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "AGENTS.md").write_text("test rules", encoding="utf-8")

    first_app = create_app(database_url=database_url, workspace_root=tmp_path)
    with TestClient(first_app) as client:
        authenticate(client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "test-project", "root_path": str(project_root)},
        )
        assert project.status_code == 201
        project_id = project.json()["id"]
        session = client.post("/api/v1/sessions", json={"thread_id": "api-thread"})
        assert session.status_code == 201
        session_id = session.json()["id"]
        message = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"role": "user", "content": [{"type": "text", "text": "hello"}]},
        )
        assert message.status_code == 200
        discovery = client.post(f"/api/v1/projects/{project_id}/rules/discover", json={})
        assert discovery.status_code == 200
        assert discovery.json()["rules"][0]["filename"] == "AGENTS.md"

    restarted_app = create_app(database_url=database_url, workspace_root=tmp_path)
    with TestClient(restarted_app) as client:
        authenticate(client)
        snapshot = client.get(f"/api/v1/sessions/{session_id}")
        assert snapshot.status_code == 200
        assert snapshot.json()["messages"][0]["message"]["content"][0]["text"] == "hello"
        assert client.get("/api/v1/projects").json()[0]["id"] == project_id
