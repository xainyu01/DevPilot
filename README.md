# DevPilot

DevPilot 是一个本地优先的编程 Agent，提供 FastAPI 服务、Typer CLI 和 React/Vite Web 工作台。
模型通过原生 Tool Calling 在注册项目内执行读、写、测试和受审批操作；服务端以工作区与测试证据
独立验证完成状态，不接受模型仅用文字声明成功。

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
Push-Location apps/web
& node_modules/.bin/tsc.cmd -b
& node_modules/.bin/vite.cmd build
Pop-Location
```

本地运行数据位于 `.devpilot/`，不应提交到仓库。完整的标识迁移说明见 [命名迁移](docs/BRANDING_MIGRATION.md)。
本地的模型、用户和空闲自动关闭设置见 [本地运行设置](docs/LOCAL_SETTINGS.md)。

## 模型 Settings 规则

使用 `admin` 登录 Web 工作台，进入“设置 → 运行时”，可以维护多组模型连接。每组连接包含：

- `id`：连接的唯一标识，只能使用小写字母、数字、点、短横线和下划线。
- `name`：页面显示名称。
- `provider`：接口协议，可选 `openai`、`anthropic`、`coding_plan`、`fake` 或 `ollama`。
  `coding_plan` 使用 OpenAI-compatible Chat Completions 协议；仅提供 Anthropic Messages
  地址的 Coding Plan 应选择 `anthropic`。
- `base_url`：自定义 API 根地址；留空时读取环境变量。
- `api_key`：API 密钥；Web 不会回显已经保存的值。留空表示保留原值或读取环境变量，
  勾选“清除已保存 Key”才会删除本地值。
- `models`：该连接允许使用的模型名称数组，同一连接可以填写多个。
- `tool_capability`：原生 Tool Calling 探测状态；未探测为 `unknown`，确认后为
  `supported` 或 `unsupported`。

`default_model` 指定默认连接和模型。`agent_model_policy.mode` 为 `manual` 时使用默认模型，
为 `auto` 时由默认模型先在 `allowed_models` 中建议目标模型，服务端校验建议没有越界后才执行；
选模响应无效时会在允许范围内确定性回退。对话输入框也可以逐次选择“遵循全局策略”“自动选择”
或一个明确模型。

配置保存在已被 Git 忽略的 `.devpilot/settings.json`。当前版本会在这个本地文件中保存所填写的
API Key 明文，因此该目录应只允许当前系统用户访问，且不得复制到提交、日志或共享文件中；
更推荐把 `api_key` 留空并使用环境变量。字段的完整 JSON 格式、变量优先级和示例见
[本地运行设置](docs/LOCAL_SETTINGS.md)。

## 真实编程 Agent

登录 Web 工作台后，先注册一个已存在的项目目录并创建 Session，再选择允许 Tool Calling 的
endpoint/model。运行会持久化计划、逐次模型调用、Tool Call/Result、审批、工作区变化、测试、
Token 和独立 verification；刷新页面或服务重启后仍可查询和恢复。

高风险的 Shell、Git 写入和删除操作必须经过策略与人工审批。每次 Run 还可用
`capability_limit` 进一步收窄当前 RBAC 权限，不能借此扩权。`test.run` 默认只暴露服务器发现的
测试类型；只有管理员配置 allow-list 时才允许模型传入明确命令。

本地 DeepSeek 真实验收可执行：

```powershell
uv --cache-dir .uv-cache run python scripts/deepseek_event_lens_e2e.py
```

脚本从精确的忽略目录创建空项目，通过 FastAPI/WebSocket 让 DeepSeek 自主生成文件并运行测试，
随后在同一 Session 增量修改，再以 Anthropic-compatible endpoint 做只读冒烟。脚本不会向验收
项目代写代码，也不会输出 API Key；脱敏结果保存在 `.devpilot/agent-e2e/results/`。

## 文档

- [进度](docs/PROGRESS.md)
- [真实 Agent 缺口分析](docs/REAL_AGENT_GAP_ANALYSIS.md)
- [真实编程 Agent 完整改造计划](docs/REAL_AGENT_IMPLEMENTATION_PLAN.md)
- [首次运行](docs/GETTING_STARTED.md)
- [发布指南](docs/RELEASE.md)
- [命名迁移](docs/BRANDING_MIGRATION.md)
