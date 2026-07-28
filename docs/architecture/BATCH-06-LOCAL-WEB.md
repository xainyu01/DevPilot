# B6：本地 Web 与跨平台

## 决策

B6 改用 FastAPI + Vite 的 Web 优先模式，不再把 Tauri、Rust 或桌面安装包作为当前版本的
运行前提。CLI 与浏览器继续复用同一套 FastAPI、WebSocket、策略、审批和审计边界。

## 运行方式

- 开发模式：Vite 提供前端热更新，并代理 `/api` 到 `127.0.0.1:8000`。
- 生产模式：先构建 `apps/web/dist`，FastAPI 直接托管首页、静态资源与前端路由回退。
- 本地启动：`devpilot serve --open-browser` 启动 API，并打开默认浏览器。
- 无界面环境：使用 `devpilot serve --no-open-browser`。
- 项目登记：Web 表单或 CLI 提交项目名和路径；服务端规范化路径并拒绝不存在路径或普通文件。
- 运行诊断：`GET /api/v1/runtime/logs` 返回最近的服务初始化、项目登记和会话运行事件。
- 首次对话：Web 表单创建会话后，通过 WebSocket `/api/v1/sessions/{id}/events` 流式接收运行事件。

浏览器不能获得任意本地文件系统或 Shell 权限。项目路径由用户显式登记，并由 FastAPI
执行路径规范化、工作区授权、工具策略和审批检查。

## 验收

```powershell
pnpm --dir apps/web build
uv --cache-dir .uv-cache run pytest
uv --cache-dir .uv-cache run ruff check .
uv --cache-dir .uv-cache run devpilot serve --open-browser
```

Windows 与 Linux 均以相同的 uv、FastAPI 和 Vite 产物运行；macOS 通过 CI 保持 Python 与
前端构建兼容。原生桌面壳层可作为后续可选扩展单独评估。
