# 初始化与首次运行

本文是 DevPilot 当前工作区的首次启动手册。当前已完成 B0 基础骨架，下一步是 B1 LangGraph 内核。

## 1. 环境要求

- Python 3.11 或更高版本。
- `uv`。
- Windows 首发；Linux 也应使用相同的 Python 命令验证。

本项目的 Python 依赖只通过 `pyproject.toml` 和 `uv.lock` 管理。不使用 `pip install`、`requirements.txt`、Poetry、npm 或 pnpm 安装当前批次的依赖。

## 2. 初始化工作区

在项目根目录执行：

```powershell
uv --cache-dir .uv-cache sync --group dev
```

Git 仓库已经初始化，但尚未替用户创建首个提交。确认文件内容后可自行执行：

```powershell
git status --short
git add .
git commit -m "Initialize DevPilot scaffold"
```

如果希望先审阅变更，只执行 `git status --short` 和 `git diff --cached`，不要直接提交。

当前机器的系统 uv 缓存路径存在冲突，因此命令显式使用工作区内的 `.uv-cache`。这仍然是 uv 的缓存，不是另一套依赖管理器。

如果环境中的 uv 缓存路径已经修复，也可以使用：

```powershell
uv sync --group dev
```

不要手动修改 `uv.lock`。需要变更依赖时，编辑 `pyproject.toml`，再运行：

```powershell
uv --cache-dir .uv-cache lock
uv --cache-dir .uv-cache sync --group dev
```

## 3. 首次验证

```powershell
uv --cache-dir .uv-cache run pytest
uv --cache-dir .uv-cache run ruff check .
uv --cache-dir .uv-cache run devpilot doctor
```

预期结果：测试通过、Ruff 无错误、doctor 的四项检查全部为 PASS。

## 4. 启动 API

```powershell
uv --cache-dir .uv-cache run devpilot serve
```

另开一个终端验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8000/api/v1/meta
Invoke-RestMethod http://127.0.0.1:8000/api/v1/progress
```

API 文档地址：`http://127.0.0.1:8000/docs`。

## 5. 查看进度与生成交接

```powershell
uv --cache-dir .uv-cache run devpilot progress
uv --cache-dir .uv-cache run devpilot handover preview --reason requested
uv --cache-dir .uv-cache run devpilot handover write --reason paused
```

交接文档会生成在 `docs/handovers/`，并包含当前批次、完成项、进行中事项、阻塞项、恢复检查清单和工作区快照。

## 6. 当前边界

B0 已完成 API、CLI、contracts、进度源和交接 Agent。计划书阶段 0 中的 React Web/Tauri 连通骨架尚未实现；它不会被当前 uv-only 批次伪装成已完成。详见 [下一步执行指南](NEXT_STEPS.md) 与 [B1 设计说明](architecture/BATCH-01-LANGGRAPH-CORE.md)。
