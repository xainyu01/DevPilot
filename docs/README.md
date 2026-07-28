# DevPilot 文档中心

所有本次重写产生的说明、架构决策、进度和交接文档统一放在本目录。建议先读初始化指南，再读下一步执行指南。

## 目录约定

- `PROGRESS.md`：当前进度的人类可读视图。
- `progress.json`：交接 Agent 使用的结构化进度事实来源。
- `GETTING_STARTED.md`：环境初始化、启动、验证和交接命令。
- `NEXT_STEPS.md`：批次推进顺序、完成定义、暂停和恢复流程。
- `DOCUMENTATION.md`：docs 目录、命名和更新规范。
- `architecture/`：架构与阶段设计。
- `adr/`：架构决策记录。
- `handovers/`：暂停或用户要求时生成的交接文档。

## 当前批次

当前先执行阶段 0 的后端/CLI 基础骨架。React/Tauri 客户端在后续批次加入，暂不引入 `pnpm`，以遵守“只使用 uv 管理依赖”的约束。

## 推荐阅读顺序

1. [GETTING_STARTED.md](GETTING_STARTED.md)
2. [PROGRESS.md](PROGRESS.md)
3. [NEXT_STEPS.md](NEXT_STEPS.md)
4. 当前批次的架构说明与 ADR
5. 最新的 `handovers/HANDOVER-*.md`

## 发布候选

- [B8 稳定化与发布设计](architecture/BATCH-08-STABILIZATION-RELEASE.md)
- [部署、升级与回滚指南](RELEASE.md)
