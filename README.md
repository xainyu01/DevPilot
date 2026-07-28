# DevPilot

DevPilot 是一个本地优先的多 Agent 研发助手，提供 FastAPI 服务、Typer CLI 和 React/Vite Web 工作台。

当前版本为 `0.1.0rc1`，重点支持 Windows 本地运行；Linux/macOS 保留平台接口，运行时验收留待后续批次。

## 快速开始

```powershell
uv --cache-dir .uv-cache sync --group dev
uv --cache-dir .uv-cache run devpilot serve
```

服务默认运行在 `http://127.0.0.1:8000`，API 文档位于 `/docs`。

使用固定发布候选用户登录并设置短期令牌：

```powershell
uv --cache-dir .uv-cache run devpilot auth login <user-id>
$env:DEVPILOT_ACCESS_TOKEN = '<access-token>'
uv --cache-dir .uv-cache run devpilot project list
```

## 验证

```powershell
uv --cache-dir .uv-cache run pytest
uv --cache-dir .uv-cache run ruff check .
uv --cache-dir .uv-cache run devpilot doctor
pnpm --dir apps/web build
```

本地运行数据位于 `.devpilot/`，不应提交到仓库。完整的标识迁移说明见 [命名迁移](docs/BRANDING_MIGRATION.md)。
本地的模型、用户和空闲自动关闭设置见 [本地运行设置](docs/LOCAL_SETTINGS.md)。

## 文档

- [进度](docs/PROGRESS.md)
- [首次运行](docs/GETTING_STARTED.md)
- [发布指南](docs/RELEASE.md)
- [命名迁移](docs/BRANDING_MIGRATION.md)
