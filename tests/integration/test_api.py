from fastapi.testclient import TestClient

from apps.api.main import app


def test_health_endpoint() -> None:
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "codeassist-api"}


def test_meta_declares_uv_and_stage_three() -> None:
    response = TestClient(app).get("/api/v1/meta")

    assert response.status_code == 200
    assert response.json()["dependency_manager"] == "uv"
    assert response.json()["stage"] == 3
    assert response.json()["features"]["agent_core"] == "available"


def test_tools_endpoint_exposes_policy_gated_definitions() -> None:
    response = TestClient(app).get("/api/v1/tools")

    assert response.status_code == 200
    tools = {item["name"]: item for item in response.json()}
    assert tools["shell.exec"]["risk"] == "high_risk"
    assert tools["file.patch"]["risk"] == "recoverable_write"
