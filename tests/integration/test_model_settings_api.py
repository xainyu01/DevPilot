from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import create_app


def admin_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_multiple_model_connections_are_saved_redacted_and_selectable(tmp_path: Path) -> None:
    app = create_app(database_url="sqlite://", workspace_root=tmp_path)
    with TestClient(app) as client:
        headers = admin_headers(client)
        updated = client.put(
            "/api/v1/settings",
            headers=headers,
            json={
                "idle_shutdown_minutes": 8,
                "model_endpoints": [
                    {
                        "id": "router",
                        "name": "Router",
                        "provider": "fake",
                        "models": ["controller"],
                        "enabled": True,
                    },
                    {
                        "id": "coding-plan",
                        "name": "Coding Plan",
                        "provider": "coding_plan",
                        "base_url": "https://coding.example.test/v1",
                        "api_key": "unit-test-key",
                        "models": ["coder-fast", "coder-quality"],
                        "enabled": True,
                        "tool_capability": "supported",
                    },
                ],
                "default_model": {"endpoint_id": "router", "model": "controller"},
                "agent_model_policy": {
                    "mode": "auto",
                    "allowed_models": [
                        {"endpoint_id": "router", "model": "controller"},
                        {"endpoint_id": "coding-plan", "model": "coder-quality"},
                    ],
                },
            },
        )

        assert updated.status_code == 200
        payload = updated.json()
        coding = payload["model_endpoints"][1]
        assert "api_key" not in coding
        assert coding["api_key_configured"] is True
        assert coding["api_key_source"] == "saved"
        assert coding["tool_capability"] == "supported"
        options = client.get("/api/v1/model-options", headers=headers).json()
        assert options["agent_model_policy"]["mode"] == "auto"
        assert len(options["models"]) == 3

        session = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={"thread_id": "manual-model"},
        ).json()
        run = client.post(
            f"/api/v1/sessions/{session['id']}/runs",
            headers=headers,
            json={
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "hello"}],
                },
                "model_mode": "manual",
                "endpoint_id": "router",
                "model": "controller",
            },
        )
        assert run.status_code == 201
        assert run.json()["context"]["provider"] == "router"
        assert run.json()["context"]["model"] == "controller"

        rejected = client.post(
            f"/api/v1/sessions/{session['id']}/runs",
            headers=headers,
            json={
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "hello"}],
                },
                "model_mode": "manual",
                "endpoint_id": "coding-plan",
                "model": "coder-fast",
            },
        )
        assert rejected.status_code == 422
        snapshot = client.get(
            f"/api/v1/sessions/{session['id']}", headers=headers
        ).json()
        assert len(snapshot["messages"]) == 2

    saved_text = (tmp_path / ".devpilot" / "settings.json").read_text(encoding="utf-8")
    assert "unit-test-key" in saved_text
