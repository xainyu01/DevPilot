# ADR-0001：Python 依赖统一由 uv 管理

- 状态：已采纳
- 日期：2026-07-28

## 背景

用户要求“只有 uv 管理依赖”。计划书原本同时列出 Python 的 uv 和前端的 pnpm；这会造成多个依赖入口和锁文件，增加批次交接与环境复现成本。

## 决策

当前重写仓库只使用 `pyproject.toml`、`uv.lock` 和 `uv run`/`uv sync` 管理 Python 依赖。第一批不创建需要 `pnpm` 或 `npm install` 的前端依赖树。

## 影响

- 阶段 0 先交付 Python API、CLI、领域契约和交接 Agent。
- React/Tauri 仍保留在架构路线中，但进入后续批次前需要确定“uv-only”是否仅约束 Python，或是否接受前端生态自身的包管理器。
- 不使用 `requirements.txt`、手工 `pip install` 或未锁定的运行依赖。
