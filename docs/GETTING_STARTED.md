# 初始化与首次运行

本文是 DevPilot 当前发布候选版本的首次启动与真实编程 Agent 使用手册。

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

现有仓库可能包含用户未提交修改。Agent 会把状态与 Diff 放入上下文，并要求先读取再修改；仍应在
开始任务前自行检查 `git status --short`，不要把无关变化交给模型覆盖。

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

## 5. 注册项目并运行编码任务

1. 使用 `admin`、`admin1`、`admin2` 或 `admin3` 登录 Web 工作台。
2. 注册一个已存在且位于允许工作区内的项目目录。
3. 创建绑定该项目的 Session。
4. 选择支持 Tool Calling 且位于管理员 `allowed_models` 范围内的 endpoint/model。
5. 提交任务和可验证的 acceptance criteria。
6. 在时间线检查实际模型、Tool Call/Result、测试、文件变化、Token 与 verification。
7. 若出现审批卡片，核对工具、参数和风险后批准或拒绝；刷新后仍可从持久事件恢复。

Run 只在服务器验证工作区变化、必需文件、未处理工具错误和成功 `test.run` 证据后标记为
`completed`。已有成果但仍未满足条件时为 `partial`，不会伪装成完成。

## 6. 查看进度与生成交接

```powershell
uv --cache-dir .uv-cache run devpilot progress
uv --cache-dir .uv-cache run devpilot handover preview --reason requested
uv --cache-dir .uv-cache run devpilot handover write --reason paused
```

交接文档会生成在 `docs/handovers/`，并包含当前批次、完成项、进行中事项、阻塞项、恢复检查清单和工作区快照。

## 7. 当前边界

B8-R1～R7 的真实 Agent 纠偏已经完成。当前工作站没有 Docker、docker-compose 或 Podman，因此
容器配置和启动健康检查仍是明确外部阻塞，整个 B8 发布批次不会在该验收完成前标记为全部完成。
Linux/macOS 适配器和桌面壳层仍返回计划中声明的未实现状态。
