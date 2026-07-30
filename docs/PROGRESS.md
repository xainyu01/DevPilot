# DevPilot 实施进度

> 最后更新：2026-07-31（Asia/Shanghai）
> 当前批次：B8-R3（项目级编程工具与安全边界）

进度事实源为 [`progress.json`](progress.json)。

| 批次 | 状态 | 完成度 | 内容 |
|---|---|---:|---|
| B0 | 已完成 | 100% | 基础骨架与交接机制 |
| B1 | 已完成 | 100% | LangGraph 内核、模型网关与生命周期 |
| B2 | 已完成 | 100% | 工具、策略、审批与审计 |
| B3 | 已完成 | 100% | 数据、会话/长期记忆与项目上下文 |
| B4 | 已完成 | 100% | 仓库分析、工作流、测试与 PR 文档 |
| B5 | 已完成 | 100% | CLI、React/Vite 工作台与会话事件 |
| B6 | 已完成 | 100% | Windows-first 本地 Web 与平台端口 |
| B7 | 已完成 | 100% | 用户、团队、RBAC、共享与远程 Host |
| B8 | 进行中 | 78% | 稳定化、安全、真实 Agent 闭环、恢复、部署与发布 |
| B8-R1 | 已完成 | 100% | ModelToolCall 契约、OpenAI/Anthropic 工具协议与 DeepSeek 真实冒烟 |
| B8-R2 | 已完成 | 100% | LangGraph 自主工具循环、预算、重复检测与审批恢复 |
| B8-R3 | 进行中 | 0% | 项目级编程工具、路径/进程安全与专用测试 |

## 下一阶段已确认

用户已确认进入 B8-R1 真实 Agent 纠偏阶段。下一对话必须按照
[完整改造计划](REAL_AGENT_IMPLEMENTATION_PLAN.md) 从 R1 连续执行到 R7：

- 每个 R 子批次完成代码、测试、文档和验收后自动创建本地 Git 提交；
- 每次提交后立即继续下一子批次，不等待用户逐批确认；
- 最终必须完成 DeepSeek 自主 Tool Calling、空目录写项目和真实测试 E2E；
- 默认不执行 `git push`，远程推送仍需用户明确授权。

## B8 已完成

- B7 固定用户 `admin`、`admin1`、`admin2`、`admin3` 会通过 `TeamRepository` 写入 `users` 表；JWT 的 `sub` 与用户 ID 对应，随后继续使用项目成员、团队成员和会话共享的正常 RBAC 链路。
- 认证令牌与 Host token 使用带过期时间、用途声明和 HS256 签名的三段式 JWT；服务重启后使用相同签名密钥仍可验证。
- 项目规则、仓库扫描、工作流、Agent 树、PR 文档和用户/项目记忆均增加资源级授权检查。
- 远程 Host 配对码只保存摘要，十分钟后失效且成功配对后不可重放；迁移、SQLite 备份、readiness、附件限制、登录限流和浏览器安全响应头已经落地。
- 已提交 Dockerfile、Docker Compose、发布候选版本 `0.1.0rc1` 及部署、升级和回滚指南。
- 已通过 `53 passed`、`ruff check .`、`devpilot doctor`、Vite production build、数据库升级/降级回归和 `git diff --check`。
- 完成 B8 多模型设置扩展：支持多组 URL/API Key/模型、Coding Plan、环境变量回退、Web 管理、
  逐次指定模型及受允许范围约束的 LLM 自动选模。
- 多模型扩展已通过 `83 passed`、Ruff、Vite production build；新增设置/凭据模块覆盖率 90%，
  使用临时 DeepSeek Key 对
  Anthropic/OpenAI 兼容协议及自动选模完成最小真实验证，凭据仅在本地忽略配置中。

## 真实 Agent 端到端纠偏

2026-07-31 使用 `deepseek-openai / deepseek-v4-flash` 通过真实 FastAPI 会话测试空目录建项目。
模型调用成功并返回完整代码文本，但 Run 被错误标记为 `completed`，实际 Tool 事件、文件变化和
测试执行均为 0。

代码审计确认：原生 `file.read`、`file.search`、`file.patch`、`shell.exec`、`git.exec`
及策略/审批基础已经存在，但模型适配器未绑定工具，Agent Graph 没有模型驱动的 Tool Calling
循环，API 也没有为项目创建 ToolRuntime。当前产品因此尚不能作为真实编程 Agent 发布。

R1 已完成供应商无关 Tool Calling：OpenAI/Anthropic 适配器会原生绑定工具，严格校验 JSON
和 JSON Schema，流式合并参数，并在供应商边界安全编码 `file.read` 这类内部点号名称。
`deepseek-openai` 与 `deepseek-anthropic` 的 `deepseek-v4-flash` 已分别真实返回模型生成的
`file.read({"path":"README.md"})`，不是请求预填充或本地硬编码结果。

R2 已把模型 Tool Call 接入 LangGraph 自主循环：工具结果带原 call ID 返回模型，支持单回合多工具、
失败修正、审批 interrupt/checkpoint 恢复，并由服务器限制模型回合、工具数、Token、墙钟时间以及
重复/无进展循环。`deepseek-openai/deepseek-v4-flash` 已真实完成
“读取 `README.md` → ToolRuntime 返回内容 → 模型回答首个标题”的流式闭环。

- 问题分析：[REAL_AGENT_GAP_ANALYSIS.md](REAL_AGENT_GAP_ANALYSIS.md)
- 完整纠偏计划：[REAL_AGENT_IMPLEMENTATION_PLAN.md](REAL_AGENT_IMPLEMENTATION_PLAN.md)

下一步必须连续完成计划中的 R3～R7，并以 DeepSeek 自主产生 Tool Call、创建 Event Lens 文件、
执行测试和形成持久化证据作为发布门槛。不得由 DevPilot/Codex 手工代写验收项目代码。

## 其他待完成的发布验收

当前工作站未安装 Docker、`docker-compose` 或 Podman，因而未能运行容器配置解析和启动验收。
在真实 Agent 纠偏完成后，B8 仍不可标记为全部完成，直到在 Docker 可用环境执行：

```powershell
docker compose config
docker compose up --build -d
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
```

详见 [B8 设计](architecture/BATCH-08-STABILIZATION-RELEASE.md) 与[发布指南](RELEASE.md)。
