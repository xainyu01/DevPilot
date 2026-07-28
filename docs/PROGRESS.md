# CodeAssist 2.0 实施进度

> 最后更新：2026-07-29 01:40（Asia/Shanghai）
> 当前批次：B8  0%（未开始）

进度事实源为 [`docs/progress.json`](progress.json)。

| 批次 | 状态 | 完成度 | 内容 |
|---|---|---:|---|
| B0 | 已完成 | 100% | Monorepo、FastAPI、CLI、契约和交接 Agent |
| B1 | 已完成 | 100% | LangGraph 内核、模型网关、运行生命周期 |
| B2 | 已完成 | 100% | 工具、策略、Shell/Git、人工审批和审计 |
| B3 | 已完成 | 100% | 数据库、会话记忆、项目规则和长期记忆 |
| B4 | 已完成 | 100% | 仓库分析、证据链、Agent 执行树、测试和 PR 文档 |
| B5 | 已完成 | 100% | CLI、React/Vite 工作台、会话事件和研发视图 |
| B6 | 已完成 | 100% | Windows-first FastAPI/Vite 本地 Web、平台端口和浏览器启动 |
| B7 | 已完成 | 100% | 团队、RBAC、Demo 认证、会话共享、受保护 API/WebSocket 与远程 Host 配对 |
| B8 | 未开始 | 0% | 稳定化与发布 |

## B6 验收结果

- [x] 根目录计划书已修订为 FastAPI + Vite Web 优先路线，并保留原计划快照。
- [x] FastAPI 托管 `apps/web/dist`，支持静态资源和 SPA 路由回退。
- [x] `codeassist serve` 支持 `--open-browser/--no-open-browser`、端口预检和明确启动错误。
- [x] 项目登记执行绝对路径规范化、存在性校验和目录校验。
- [x] `/api/v1/runtime/logs` 提供本地运行日志入口。
- [x] Web 工作台支持登记项目、创建会话和 WebSocket 首次对话。
- [x] 已删除未完成的 Tauri、Rust、Sidecar 和桌面打包依赖。
- [x] Windows 本地服务冒烟通过：`healthz`、Vite 首页、元数据和运行日志入口均可访问。
- [x] 完整验收：`43 passed`、Ruff、Vite build、`codeassist doctor` 和 `git diff --check` 通过。
- [x] Linux/macOS 兼容边界已抽取为平台端口和能力声明；具体适配器作为后续 TODO，不阻塞当前 Windows-first 批次。

## 下一阶段

进入 B8：性能、恢复、安全、部署和发布验收。固定 Demo 凭据与本地 Host 通道在 B8 替换为发布级安全配置。

## 统一验收命令

```powershell
uv --cache-dir .uv-cache run pytest
uv --cache-dir .uv-cache run ruff check .
uv --cache-dir .uv-cache run codeassist doctor
pnpm --dir apps/web build
```
