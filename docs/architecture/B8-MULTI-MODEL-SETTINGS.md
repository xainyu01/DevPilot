# B8 扩展：多模型连接与受限自动选模

## 范围

本扩展响应发布候选阶段的实际接入需求，在不改变 Agent 状态契约和供应商无关边界的前提下完成：

- 多组 API 连接，每组独立保存协议、URL、API Key 和多个模型名；
- OpenAI、Anthropic、Coding Plan（OpenAI-compatible）、Fake 与 Ollama 声明；
- 本地设置优先、endpoint 专属环境变量次之、协议通用环境变量最后的回退链；
- Web 管理、API Key 非回显、对话逐次指定模型；
- 默认模型、Agent 自动/手动模式和允许模型范围；
- 模型提出选择、服务端执行越界校验和确定性回退；
- 旧单模型设置的读取兼容。

Ollama 仍遵循原计划 TODO：只保留配置和能力声明，调用返回 `not_implemented`。

## 配置模型

```text
LocalSettings
├── model_endpoints[]: ModelEndpoint
│   ├── endpoint_id / name / provider
│   ├── base_url / api_key
│   └── models[] / enabled
├── default_model: ModelTarget
└── agent_model_policy
    ├── mode: manual | auto
    └── allowed_models[]: ModelTarget
```

`endpoint_id + model` 是运行时唯一键。协议与连接身份分离：例如两个 endpoint 都可以使用
OpenAI-compatible 协议，但拥有不同 URL、Key 和模型列表。

## 选择边界

1. 服务端先计算已启用、已配置的模型集合。
2. 用户策略的 `allowed_models` 必须是该集合的非空子集。
3. 手动选择必须属于允许集合。
4. 自动选择时，默认模型（若被允许）或允许集合第一项承担 selector 调用。
5. selector 只收到任务文本和允许候选，必须返回 JSON 目标和理由。
6. 服务端再次进行精确集合校验；无效 JSON、调用失败或越界结果使用允许范围内的 selector
   作为显式回退。
7. 最终选择信息写入 Run metadata，包括 mode、reason、selector 和 fallback 状态。

研发工作流当前仍是确定性编排；其 Supervisor/子 Agent 的模型 profile 从同一个允许集合生成，
继续按角色、能力和预算过滤，不会因模型建议扩大权限。

## 密钥处理

- Settings API 不返回 Key 原文，只返回是否已配置及来源。
- Web 发送空 Key 表示保留原值；只有 `clear_api_key=true` 才删除保存值。
- 本地 `.devpilot/settings.json` 被 Git 忽略，但当前版本保存的 Key 是明文；推荐生产和共享机器
  使用环境变量或容器 Secret。
- 配置、日志、运行事件和测试夹具不得包含真实凭据。

## 验收

- 旧配置可迁移读取，多 endpoint 能保存并重启加载。
- URL 只允许无 userinfo 的 HTTP(S) 地址。
- API Key 不在 GET 响应中出现。
- 手动越界选择返回 422。
- 自动选择只接受允许集合成员，异常时显式回退。
- React production build、83 项完整 pytest、设置/凭据模块 90% 覆盖率、Ruff 与 DeepSeek
  两种兼容协议最小调用通过。
