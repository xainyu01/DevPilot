from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import create_app


def login_headers(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": username}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_b7_team_rbac_session_share_and_remote_host_declaration(tmp_path: Path) -> None:
    app = create_app(database_url="sqlite://", workspace_root=tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/v1/projects").status_code == 401
        rejected = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "wrong"}
        )
        assert rejected.status_code == 401
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        owner = client.post(
            "/api/v1/users", json={"id": "owner", "display_name": "Owner"}, headers=headers
        )
        collaborator = client.post(
            "/api/v1/users",
            json={"id": "collaborator", "display_name": "Collaborator"},
            headers=headers,
        )
        assert owner.status_code == 201
        assert collaborator.status_code == 201

        team = client.post("/api/v1/teams", json={"name": "Core"}, headers=headers)
        assert team.status_code == 201
        team_id = team.json()["id"]

        member = client.put(
            f"/api/v1/teams/{team_id}/members",
            json={"user_id": "collaborator", "role": "member"},
            headers=headers,
        )
        assert member.status_code == 200

        session = client.post(
            "/api/v1/sessions", json={"thread_id": "shared", "user_id": "admin"}, headers=headers
        )
        session_id = session.json()["id"]
        share = client.put(
            f"/api/v1/sessions/{session_id}/shares",
            json={"recipient_id": "collaborator", "permission": "collaborate"},
            headers=headers,
        )
        assert share.status_code == 200
        assert share.json()["permission"] == "collaborate"

        host = client.post(
            f"/api/v1/teams/{team_id}/remote-hosts",
            json={"name": "build-host", "capabilities": ["shell"]},
            headers=headers,
        )
        assert host.status_code == 201
        assert host.json()["status"] == "pairing_required"

        paired = client.post(
            f"/api/v1/remote-hosts/{host.json()['id']}/pair",
            json={"pairing_code": host.json()["pairing_code"]},
        )
        assert paired.status_code == 200
        assert paired.json()["status"] == "paired"
        heartbeat = client.post(
            f"/api/v1/remote-hosts/{host.json()['id']}/heartbeat",
            headers={"X-DevPilot-Host-Token": paired.json()["host_token"]},
        )
        assert heartbeat.json()["status"] == "accepted"


def test_fixed_users_enforce_project_ownership_and_session_permissions(tmp_path: Path) -> None:
    app = create_app(database_url="sqlite://", workspace_root=tmp_path)
    with TestClient(app) as client:
        admin1 = login_headers(client, "admin1")
        admin2 = login_headers(client, "admin2")

        project = client.post(
            "/api/v1/projects",
            json={"name": "admin1-project", "root_path": str(tmp_path)},
            headers=admin1,
        )
        assert project.status_code == 201
        assert client.get("/api/v1/projects", headers=admin2).json() == []

        session = client.post(
            "/api/v1/sessions", json={"thread_id": "admin1-thread"}, headers=admin1
        ).json()
        assert client.get(f"/api/v1/sessions/{session['id']}", headers=admin2).status_code == 403

        shared = client.put(
            f"/api/v1/sessions/{session['id']}/shares",
            json={"recipient_id": "admin2", "permission": "view"},
            headers=admin1,
        )
        assert shared.status_code == 200
        assert client.get(f"/api/v1/sessions/{session['id']}", headers=admin2).status_code == 200
        denied_write = client.post(
            f"/api/v1/sessions/{session['id']}/messages",
            json={"role": "user", "content": [{"type": "text", "text": "blocked"}]},
            headers=admin2,
        )
        assert denied_write.status_code == 403

        client.put(
            f"/api/v1/sessions/{session['id']}/shares",
            json={"recipient_id": "admin2", "permission": "collaborate"},
            headers=admin1,
        )
        allowed_write = client.post(
            f"/api/v1/sessions/{session['id']}/messages",
            json={"role": "user", "content": [{"type": "text", "text": "allowed"}]},
            headers=admin2,
        )
        assert allowed_write.status_code == 200
