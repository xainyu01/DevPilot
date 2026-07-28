from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import create_app


def test_b4_api_scans_and_restores_workflow_and_pr_document(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'b4.db').as_posix()}"
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("def broken():\n    return False\n", encoding="utf-8")
    app = create_app(database_url=database_url, workspace_root=tmp_path)
    with TestClient(app) as client:
        project = client.post(
            "/api/v1/projects",
            json={"name": "b4-project", "root_path": str(root)},
        ).json()
        profile = client.post(f"/api/v1/projects/{project['id']}/repository/scan")
        assert profile.status_code == 200
        assert profile.json()["index_version"] == 1
        workflow = client.post(
            "/api/v1/workflows",
            json={
                "project_id": project["id"],
                "description": "broken returns false",
                "failing_tests": ["test_broken"],
            },
        )
        assert workflow.status_code == 201
        workflow_id = workflow.json()["id"]
        assert workflow.json()["status"] == "completed"
        tree = client.get(f"/api/v1/workflows/{workflow_id}/agent-tree")
        assert len(tree.json()["agent_runs"]) == 3
        review = client.patch(
            f"/api/v1/workflows/{workflow_id}/pr/review",
            json={"status": "pending"},
        )
        assert review.status_code == 200
        assert review.json()["review_status"] == "pending"

    restarted = create_app(database_url=database_url, workspace_root=tmp_path)
    with TestClient(restarted) as client:
        restored = client.get(f"/api/v1/workflows/{workflow_id}")
        assert restored.status_code == 200
        assert restored.json()["pr_document"]["review_status"] == "pending"
