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

## 文档

- [进度](docs/PROGRESS.md)
- [真实 Agent 缺口分析](docs/REAL_AGENT_GAP_ANALYSIS.md)
- [真实编程 Agent 完整改造计划](docs/REAL_AGENT_IMPLEMENTATION_PLAN.md)
- [首次运行](docs/GETTING_STARTED.md)
- [发布指南](docs/RELEASE.md)
- [命名迁移](docs/BRANDING_MIGRATION.md)
