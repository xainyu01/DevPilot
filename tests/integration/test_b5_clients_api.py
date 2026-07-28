from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import create_app


def authenticate(client: TestClient) -> None:
    token = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
    ).json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})


def test_b5_session_run_preserves_message_and_event_order(tmp_path) -> None:
    app = create_app(
        database_url=f"sqlite:///{(tmp_path / 'b5.db').as_posix()}", workspace_root=tmp_path
    )
    with TestClient(app) as client:
        authenticate(client)
        session = client.post(
            "/api/v1/sessions", json={"thread_id": "b5-thread", "title": "B5 session"}
        ).json()
        response = client.post(
            f"/api/v1/sessions/{session['id']}/runs",
            json={
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "inspect this change"}],
                }
            },
        )
        assert response.status_code == 201
        events = response.json()["events"]
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        snapshot = client.get(f"/api/v1/sessions/{session['id']}").json()
        assert [item["message"]["role"] for item in snapshot["messages"]] == ["user", "assistant"]
        assert snapshot["messages"][1]["message"]["content"][0]["text"].startswith("Fake response")


def test_project_directory_picker_is_limited_to_workspace(tmp_path) -> None:
    selectable = tmp_path / "selectable"
    selectable.mkdir()
    (selectable / "nested").mkdir()
    (tmp_path / ".hidden").mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside-project"
    outside.mkdir()
    app = create_app(
        database_url=f"sqlite:///{(tmp_path / 'directory-picker.db').as_posix()}",
        workspace_root=tmp_path,
    )

    with TestClient(app) as client:
        authenticate(client)
        root_listing = client.get("/api/v1/project-directories")
        assert root_listing.status_code == 200
        assert {item["name"] for item in root_listing.json()["directories"]} >= {"selectable"}
        assert ".hidden" not in {item["name"] for item in root_listing.json()["directories"]}

        nested_listing = client.get("/api/v1/project-directories", params={"path": selectable})
        assert nested_listing.status_code == 200
        assert nested_listing.json()["parent_path"] == str(tmp_path.resolve())
        assert nested_listing.json()["directories"] == [
            {"name": "nested", "path": str((selectable / "nested").resolve())}
        ]

        outside_listing = client.get("/api/v1/project-directories", params={"path": outside})
        assert outside_listing.status_code == 422
