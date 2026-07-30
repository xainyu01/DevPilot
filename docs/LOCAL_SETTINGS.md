# 本地运行设置

DevPilot 将机器相关设置保存到工作区 `.devpilot/settings.json`。该目录已被 Git 忽略，适用于
Windows、Linux 和 macOS，不依赖 `.env` 文件。固定 `admin` 用户可以在 Web 工作台的
“设置 → 运行时”中编辑模型连接、默认模型和 Agent 允许范围。

## 模型连接

一个 `model endpoint` 表示一家 API 或一个独立网关。同一家协议可以配置多组连接，每组可以有
不同 URL、Key 和模型列表；例如可以同时保存两个 OpenAI-compatible 网关，也可以同时使用
OpenAI 和 Anthropic 协议。

| 字段 | 格式 | 含义 |
|---|---|---|
| `id` | `^[a-z0-9][a-z0-9_.-]{0,99}$` | 连接唯一标识，也会记录为运行事件中的 provider |
| `name` | 非空字符串 | Web 显示名称 |
| `provider` | 见下表 | 连接使用的接口协议 |
| `base_url` | `http://` 或 `https://` URL，可为 `null` | 自定义 API 根地址 |
| `api_key` | 字符串，可为 `null` | 本地保存的密钥 |
| `models` | 字符串数组 | 此连接提供的模型名称，可以有多项 |
| `enabled` | 布尔值 | 是否注册连接及其模型 |
| `tool_capability` | `supported` / `unsupported` / `unknown` | 原生 Tool Calling 探测结果 |

协议值：

| `provider` | 行为 |
|---|---|
| `openai` | OpenAI 或 OpenAI-compatible Chat Completions |
| `anthropic` | Anthropic Messages-compatible |
| `coding_plan` | Coding Plan 的 OpenAI-compatible 接口；URL 由用户填写 |
| `fake` | 不访问网络的确定性测试模型 |
| `ollama` | 仅保留配置契约；当前调用明确返回未实现 |

“Coding Plan”不是单一厂商品牌。若服务商提供 OpenAI-compatible URL，选择 `coding_plan`；
若只提供 Anthropic Messages URL，选择 `anthropic`。不要把一种协议的 URL 配给另一种协议。

## 完整格式

以下示例配置两个 API 连接，每个连接暴露多个模型：

```json
{
  "idle_shutdown_minutes": 5,
  "models": {
    "endpoints": [
      {
        "id": "coding-plan-a",
        "name": "Coding Plan A",
        "provider": "coding_plan",
        "base_url": "https://api.example.com/v1",
        "api_key": null,
        "models": ["coder-fast", "coder-pro"],
        "enabled": true,
        "tool_capability": "unknown"
      },
      {
        "id": "anthropic-gateway",
        "name": "Anthropic Gateway",
        "provider": "anthropic",
        "base_url": "https://anthropic.example.com",
        "api_key": null,
        "models": ["claude-compatible-model"],
        "enabled": true,
        "tool_capability": "supported"
      }
    ],
    "default": {
      "endpoint_id": "coding-plan-a",
      "model": "coder-fast"
    },
    "agent": {
      "mode": "auto",
      "allowed_models": [
        {"endpoint_id": "coding-plan-a", "model": "coder-fast"},
        {"endpoint_id": "coding-plan-a", "model": "coder-pro"},
        {"endpoint_id": "anthropic-gateway", "model": "claude-compatible-model"}
      ]
    }
  },
  "users": []
}
```

填写规则：

- `models.default` 必须指向一个已启用连接中的有效模型。
- `models.agent.allowed_models` 不能为空，且每一项都必须属于已启用连接。
- 同一连接的模型名称自动去重；名称区分大小写。
- `models.agent.mode` 只能是 `manual` 或 `auto`。
- `tool_capability=unknown` 允许管理员执行显式冒烟；确认不支持后应保存为 `unsupported`，
  编程 Agent 不再把任务派给该连接。
- Web 中 API Key 输入框留空不会覆盖已有值；“清除已保存 Key”会删除本地值并恢复环境变量回退。
- 旧版 `{ "model": { "provider": "...", "name": "..." } }` 文件仍可读取，下一次通过 Web 保存后
  会转换为新格式。

## 环境变量回退

URL、API Key 或模型列表没有保存在 endpoint 中时，按以下顺序读取：

1. endpoint 专属变量；
2. 协议通用变量；
3. 没有可用值时返回明确的未配置错误。

endpoint 专属变量由 ID 生成：转成大写，并把非字母数字字符替换为下划线。例如
`deepseek-main` 对应：

```text
DEVPILOT_MODEL_DEEPSEEK_MAIN_API_KEY
DEVPILOT_MODEL_DEEPSEEK_MAIN_BASE_URL
DEVPILOT_MODEL_DEEPSEEK_MAIN_MODELS
```

`*_MODELS` 使用逗号分隔。协议通用变量为：

| 协议 | API Key | URL | 模型 |
|---|---|---|---|
| `openai` | `OPENAI_API_KEY` | `OPENAI_BASE_URL` | `OPENAI_MODEL` |
| `anthropic` | `ANTHROPIC_API_KEY` | `ANTHROPIC_BASE_URL` | `ANTHROPIC_MODEL` |
| `coding_plan` | `CODING_PLAN_API_KEY` | `CODING_PLAN_BASE_URL` | `CODING_PLAN_MODELS` |
| `ollama` | `OLLAMA_API_KEY` | `OLLAMA_BASE_URL` | `OLLAMA_MODEL` |

示例：

```powershell
$env:DEVPILOT_MODEL_CODING_PLAN_A_API_KEY = "<temporary-key>"
$env:DEVPILOT_MODEL_CODING_PLAN_A_BASE_URL = "https://api.example.com/v1"
$env:DEVPILOT_MODEL_CODING_PLAN_A_MODELS = "coder-fast,coder-pro"
uv --cache-dir .uv-cache run devpilot serve
```

## Agent 选模行为

过去的普通对话只使用一组全局 `provider/model`；研发工作流的 `ModelRouter` 先按角色、能力、健康状态、
Token 上限和隐私级别过滤，再按 fallback、成本、延迟和质量进行固定排序，它不是由 LLM 自主选择。

现在有三种调用方式：

1. **遵循全局策略**：`manual` 使用默认模型；`auto` 触发受限自动选模。
2. **自动选择**：允许范围内的控制模型收到任务摘要和候选列表，只返回目标连接、模型和理由；服务端
   验证结果必须完全匹配 `allowed_models`。越界或无效 JSON 不会执行，会回退到允许模型。
3. **指定模型**：用户在对话输入框选择明确模型；服务端仍验证该模型属于允许范围。

研发工作流创建的 Supervisor 和子 Agent 也只会获得 `allowed_models` 中的模型配置。Agent 或用户输入
不能扩展这个上限。

## 原生 Tool Calling

OpenAI-compatible 与 Anthropic-compatible 连接使用各自原生 tools/tool_use 协议。模型响应会统一转换为
`ModelToolCall(call_id, name, arguments)`；参数必须是合法 JSON 对象并通过对应 JSON Schema，否则整次
模型回合失败，不会容错执行。内部工具继续使用 `file.read` 等点号名称；适配器只在供应商传输边界编码
为其允许的名称，并在响应后无损还原。

可用本地忽略配置做凭据安全的只读冒烟：

```powershell
uv --cache-dir .uv-cache run python scripts/deepseek_tool_call_smoke.py
```

脚本只输出 endpoint、模型、usage、请求标识和模型生成的工具调用，不输出 API Key。

## 密钥与用户安全

Web 和 `GET /api/v1/settings` 只返回 `api_key_configured` 与来源，不返回 API Key 原文。当前版本为了满足
本地可编辑与重启复用，会把填写的 Key 明文保存在 `.devpilot/settings.json`；应限制该目录的系统权限，
不要把它复制到 Git、Issue、日志或交接文档。更高安全要求下应仅使用环境变量或容器 Secret。

本地新增用户的密码会立即转换为 PBKDF2 哈希后写入配置，并通过正常的 users、JWT 与 RBAC 链路认证。
配置文件不能填写明文用户密码。`admin`、`admin1`、`admin2`、`admin3` 是发布前固定用户，不能被本地
配置覆盖。
