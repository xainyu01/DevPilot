from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import create_app


def test_b5_session_run_preserves_message_and_event_order(tmp_path) -> None:
    app = create_app(
        database_url=f"sqlite:///{(tmp_path / 'b5.db').as_posix()}", workspace_root=tmp_path
    )
    with TestClient(app) as client:
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
