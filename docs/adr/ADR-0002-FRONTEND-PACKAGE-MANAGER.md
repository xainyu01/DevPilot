# ADR-0002：Web 前端依赖管理

## 决策

阶段 5 起，Python 依赖仍仅由 `uv` 与 `uv.lock` 管理；`apps/web` 的 React、TypeScript 和 Vite 依赖由 pnpm 与根目录 `pnpm-lock.yaml` 独立锁定。

## 原因

Web 工作台需要 React + TypeScript + Vite，不能可靠地通过 Python 包管理器安装或锁定。将前端限制在 `apps/web`，可避免混淆后端与前端依赖边界，并为后续 Tauri 复用 UI 留出稳定表面。

## 后果

- Python 命令始终使用 `uv --cache-dir .uv-cache`。
- Web 命令使用 `pnpm --dir apps/web dev`、`build` 和 `lint`。
- 仅根目录 `pnpm-lock.yaml` 是前端锁文件；不引入 npm 的锁文件。
