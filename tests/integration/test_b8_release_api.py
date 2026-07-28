from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import create_app
from packages.security import AuthSettings


def login_headers(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": username}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_signed_jwt_and_resource_authorization_survive_restart(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'release.db').as_posix()}"
    first = create_app(database_url=database_url, workspace_root=tmp_path)
    with TestClient(first) as client:
        admin1_headers = login_headers(client, "admin1")
        admin2_headers = login_headers(client, "admin2")
        project = client.post(
            "/api/v1/projects",
            json={"name": "admin1-project", "root_path": str(tmp_path)},
            headers=admin1_headers,
        ).json()
        memory = client.post(
            "/api/v1/memory",
            json={"key": "private", "content": "only admin1"},
            headers=admin1_headers,
        )
        assert memory.status_code == 201
        assert client.get(
            f"/api/v1/projects/{project['id']}/rules", headers=admin2_headers
        ).status_code == 403
        assert client.get(
            "/api/v1/memory", params={"owner_id": "admin1"}, headers=admin2_headers
        ).status_code == 403

    restarted = create_app(database_url=database_url, workspace_root=tmp_path)
    with TestClient(restarted) as client:
        assert client.get("/api/v1/projects", headers=admin1_headers).status_code == 200
        token = admin1_headers["Authorization"].removeprefix("Bearer ")
        invalid_token = client.get(
            "/api/v1/projects", headers={"Authorization": f"Bearer {token}x"}
        )
        assert invalid_token.status_code == 401
        assert client.get("/readyz").json()["status"] == "ready"


def test_pairing_code_is_persistent_and_one_time_while_host_jwt_survives_restart(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'host.db').as_posix()}"
    first = create_app(database_url=database_url, workspace_root=tmp_path)
    with TestClient(first) as client:
        headers = login_headers(client, "admin")
        team = client.post("/api/v1/teams", json={"name": "Release"}, headers=headers).json()
        host = client.post(
            f"/api/v1/teams/{team['id']}/remote-hosts",
            json={"name": "release-host"},
            headers=headers,
        ).json()

    restarted = create_app(database_url=database_url, workspace_root=tmp_path)
    with TestClient(restarted) as client:
        paired = client.post(
            f"/api/v1/remote-hosts/{host['id']}/pair",
            json={"pairing_code": host["pairing_code"]},
        )
        assert paired.status_code == 200
        assert client.post(
            f"/api/v1/remote-hosts/{host['id']}/pair",
            json={"pairing_code": host["pairing_code"]},
        ).status_code == 401
        host_token = paired.json()["host_token"]

    second_restart = create_app(database_url=database_url, workspace_root=tmp_path)
    with TestClient(second_restart) as client:
        heartbeat = client.post(
            f"/api/v1/remote-hosts/{host['id']}/heartbeat",
            headers={"X-CodeAssist-Host-Token": host_token},
        )
        assert heartbeat.status_code == 200


def test_failed_logins_are_limited_and_attachments_have_a_size_limit(tmp_path: Path) -> None:
    settings = replace(AuthSettings.development(), max_attachment_bytes=1_024)
    app = create_app(database_url="sqlite://", workspace_root=tmp_path, auth_settings=settings)
    with TestClient(app) as client:
        for _ in range(5):
            assert client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "wrong"}
            ).status_code == 401
        assert client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "wrong"}
        ).status_code == 429

    app = create_app(database_url="sqlite://", workspace_root=tmp_path, auth_settings=settings)
    with TestClient(app) as client:
        headers = login_headers(client, "admin")
        session = client.post("/api/v1/sessions", json={"thread_id": "limited"}, headers=headers)
        oversized = client.post(
            f"/api/v1/sessions/{session.json()['id']}/attachments",
            json={
                "filename": "large.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(b"x" * 1_025).decode("ascii"),
            },
            headers=headers,
        )
        assert oversized.status_code == 413
