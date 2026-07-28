import json
from pathlib import Path

import pytest

from packages.handover_agent import HandoverAgent


def make_workspace(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    docs.joinpath("progress.json").write_text(
        json.dumps(
            {
                "overall_percent": 20,
                "current_batch": "B0",
                "next_action": "补齐 API 与 CLI 冒烟测试",
                "constraints": ["只使用 uv 管理 Python 依赖"],
                "batches": [
                    {
                        "id": "B0",
                        "title": "阶段 0 骨架",
                        "status": "进行中",
                        "percent": 20,
                        "scope": ["API", "CLI"],
                        "completed": ["建立目录"],
                        "in_progress": ["交接 Agent"],
                        "next_steps": ["增加测试"],
                        "blockers": [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_renders_progress_and_workspace(tmp_path: Path) -> None:
    agent = HandoverAgent.from_workspace(make_workspace(tmp_path))

    document = agent.render_handover(reason="paused")

    assert "总体完成度：20%" in document
    assert "补齐 API 与 CLI 冒烟测试" in document
    assert "只使用 uv 管理 Python 依赖" in document
    assert "生成原因：paused" in document


def test_writes_only_inside_docs(tmp_path: Path) -> None:
    agent = HandoverAgent.from_workspace(make_workspace(tmp_path))

    target = agent.write_handover(reason="requested")

    assert target.parent == tmp_path / "docs" / "handovers"
    assert target.is_file()


def test_rejects_output_outside_docs(tmp_path: Path) -> None:
    agent = HandoverAgent.from_workspace(make_workspace(tmp_path))

    with pytest.raises(ValueError, match="docs directory"):
        agent.write_handover(output_path=tmp_path / "handover.md")
