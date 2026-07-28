# 批次 B0：阶段 0 基础骨架

## 目标

建立可运行、可验证、可暂停交接的 Python Monorepo 基础。该批次不实现完整 Agent 推理、模型供应商调用、数据库、前端或 Tauri。

## 交付范围

- `apps/api`：FastAPI 应用工厂、健康检查和元数据接口。
- `apps/cli`：`serve`、`progress`、`doctor` 与 `handover` 命令。
- `packages/contracts`：不依赖 FastAPI/数据库的进度契约。
- `packages/handover_agent`：从进度事实源生成 Markdown 交接文档。
- `tests/`：交接 Agent 与基础 API 的单元/集成测试。
- `pyproject.toml` 与 `uv.lock`：唯一 Python 依赖入口。

## 完成定义

1. `uv sync --group dev` 成功。
2. `uv run pytest` 全部通过。
3. `uv run ruff check .` 无错误。
4. `uv run devpilot doctor` 通过。
5. `uv run devpilot handover write --reason paused` 只在 `docs/handovers/` 生成文档。

## 后续批次

完成 B0 后进入阶段 1：LangGraph 状态图、运行生命周期、流式事件、检查点和模型网关接口。供应商真实调用需要显式配置 API 密钥后再做集成测试。
