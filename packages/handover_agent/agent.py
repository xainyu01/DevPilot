"""Deterministic handover document generator.

The first version is deliberately file-based. It can be called from the CLI, API,
or a future LangGraph node without requiring a model or external service.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from packages.contracts import ProgressSnapshot


class HandoverAgent:
    """Create resumable handover documents from the project's progress source."""

    def __init__(self, project_root: Path, progress: ProgressSnapshot) -> None:
        self.project_root = project_root.resolve()
        self.docs_dir = self.project_root / "docs"
        self.progress = progress

    @classmethod
    def from_workspace(cls, project_root: Path | None = None) -> HandoverAgent:
        root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        progress_path = root / "docs" / "progress.json"
        if not progress_path.is_file():
            raise FileNotFoundError(f"Progress source is missing: {progress_path}")
        data = json.loads(progress_path.read_text(encoding="utf-8"))
        return cls(root, ProgressSnapshot.model_validate(data))

    def collect_workspace_state(self) -> dict[str, Any]:
        """Collect non-sensitive, resumable workspace facts."""
        state: dict[str, Any] = {
            "project_root": str(self.project_root),
            "docs_dir": str(self.docs_dir),
            "git": "not a git repository",
            "tracked_changes": [],
            "top_level_entries": sorted(
                entry.name for entry in self.project_root.iterdir() if entry.name != ".venv"
            ),
        }
        try:
            result = subprocess.run(
                ["git", "status", "--short"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return state
        if result.returncode == 0:
            state["git"] = "clean" if not result.stdout.strip() else "changes present"
            state["tracked_changes"] = [
                line for line in result.stdout.splitlines() if line.strip()
            ]
        return state

    def render_handover(self, reason: str = "requested") -> str:
        """Render a Markdown handover with enough context to resume the task."""
        state = self.collect_workspace_state()
        generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        lines = [
            "# DevPilot 交接文档",
            "",
            f"> 生成原因：{reason}",
            f"> 生成时间：{generated_at}",
            f"> 总体完成度：{self.progress.overall_percent}%",
            "",
            "## 当前状态",
            "",
            f"- 当前批次：`{self.progress.current_batch}`",
            f"- 下一步：{self.progress.next_action}",
            f"- 工作区：`{state['project_root']}`",
            f"- Git 状态：{state['git']}",
            "",
            "## 批次进度",
            "",
            "| 批次 | 状态 | 完成度 | 说明 |",
            "|---|---|---:|---|",
        ]
        for batch in self.progress.batches:
            scope = ", ".join(batch.scope)
            lines.append(
                f"| {batch.id} {batch.title} | {batch.status} | {batch.percent}% | {scope} |"
            )

        lines.extend(["", "## 已完成", ""])
        for item in self._items(kind="completed"):
            lines.append(f"- {item}")
        lines.extend(["", "## 进行中", ""])
        for item in self._items(kind="in_progress"):
            lines.append(f"- {item}")
        lines.extend(["", "## 恢复后优先处理", ""])
        for item in self._items(kind="next_steps"):
            lines.append(f"- [ ] {item}")
        lines.extend(["", "## 阻塞与待确认", ""])
        blockers = self._items(kind="blockers")
        if blockers:
            lines.extend(f"- {item}" for item in blockers)
        else:
            lines.append("- 无")
        lines.extend(["", "## 已确认约束", ""])
        lines.extend(f"- {item}" for item in self.progress.constraints)
        lines.extend(
            [
                "",
                "## 工作区快照",
                "",
                f"- 顶层条目：{', '.join(state['top_level_entries'])}",
                "- 交接文件默认位于 `docs/handovers/`。",
                "- 进度事实来源为 `docs/progress.json`，人类可读视图为 `docs/PROGRESS.md`。",
                "",
                "## 恢复检查清单",
                "",
                "- [ ] 读取本文件与 `docs/PROGRESS.md`。",
                "- [ ] 检查 `docs/progress.json` 是否需要更新。",
                "- [ ] 执行 `uv sync --group dev` 同步环境。",
                "- [ ] 执行 `uv run pytest` 和 `uv run ruff check .`。",
                "- [ ] 完成当前批次后更新进度并重新生成交接文档。",
                "",
                "## 生成方式",
                "",
                "```powershell",
                "uv run devpilot handover write --reason paused",
                "```",
                "",
            ]
        )
        return "\n".join(lines)

    def write_handover(
        self,
        reason: str = "requested",
        output_path: Path | None = None,
    ) -> Path:
        target = output_path or (
            self.docs_dir
            / "handovers"
            / f"HANDOVER-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}.md"
        )
        target = target if target.is_absolute() else self.project_root / target
        target = target.resolve()
        if self.docs_dir.resolve() not in target.parents:
            raise ValueError("Handover output must be inside the docs directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.render_handover(reason=reason), encoding="utf-8")
        return target

    def _items(self, kind: str) -> list[str]:
        items: list[str] = []
        for batch in self.progress.batches:
            items.extend(getattr(batch, kind))
        return items or ["暂无记录"]
