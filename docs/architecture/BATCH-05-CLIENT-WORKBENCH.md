# B5：CLI 与 Web 工作台

## 范围

B5 将既有 REST、会话、事件和研发工作流契约提供给两个客户端：Typer/Rich CLI 与 React/Vite Web 工作台。桌面端、团队共享和真实供应商凭据配置仍属于后续批次。

## 依赖边界

Python 依赖继续由 `uv` 管理。Web 仅位于 `apps/web`，由 pnpm 锁定；理由和命令见 [ADR-0002](../adr/ADR-0002-FRONTEND-PACKAGE-MANAGER.md)。此边界不改变 Python 的安装、测试或发布流程。

## 客户端与协议

- CLI 提供项目列出/注册/扫描、会话创建/列出/聊天及工作流列表命令，所有业务请求都经由 HTTP API。
- `POST /api/v1/sessions/{session_id}/runs` 追加用户消息、运行 Agent，并将最终助手消息持久化。
- `WS /api/v1/sessions/{session_id}/events` 为 Web 和其他客户端提供相同的递增序列 `RunEvent`；不会在客户端生成或重排事件。
- `POST /api/v1/sessions/{session_id}/attachments` 接受 base64 内容，拒绝路径型文件名，并将附件隔离到工作区 `.codeassist/attachments/<session_id>/`。
- Web 工作台显示项目、会话历史、模型输出/工具/审批事件、工作流阶段与证据数量、PR 审核状态。高风险动作仍由 B2 策略与审批事件控制，UI 不提供绕过入口。

## 验收

- `uv --cache-dir .uv-cache run pytest`：会话执行的事件序列与持久化消息顺序由 API 契约测试覆盖。
- `uv --cache-dir .uv-cache run ruff check .`：通过。
- `pnpm --dir apps/web build`：生成生产 Web 包。
- `uv --cache-dir .uv-cache run codeassist doctor`：通过。

## 启动

在两个终端分别运行：

```powershell
uv --cache-dir .uv-cache run codeassist serve
pnpm --dir apps/web dev
```

然后打开 Vite 输出的本地地址。CLI 示例：

```powershell
uv --cache-dir .uv-cache run codeassist project list
uv --cache-dir .uv-cache run codeassist session chat <session-id> "分析这个问题"
```
