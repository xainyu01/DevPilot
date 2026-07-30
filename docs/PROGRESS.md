# DevPilot 实施进度

> 最后更新：2026-07-31（Asia/Shanghai）
> 当前批次：B8（仅余 Docker 发布环境验收）

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
| B8 | 进行中 | 96% | 稳定化、安全、真实 Agent 闭环、恢复、部署与发布 |
| B8-R1 | 已完成 | 100% | ModelToolCall 契约、OpenAI/Anthropic 工具协议与 DeepSeek 真实冒烟 |
| B8-R2 | 已完成 | 100% | LangGraph 自主工具循环、预算、重复检测与审批恢复 |
| B8-R3 | 已完成 | 100% | 项目级编程工具、路径/进程安全与专用测试 |
| B8-R4 | 已完成 | 100% | ContextAssembler、会话/规则/仓库/记忆与统一协调器 |
| B8-R5 | 已完成 | 100% | Run/Event/Checkpoint 持久化、审批恢复、控制 API 与 Web 工作台 |
| B8-R6 | 已完成 | 100% | CompletionVerifier、证据验收、partial 终态与自动纠错 |
| B8-R7 | 已完成 | 100% | 模型调用可观测性、DeepSeek Event Lens E2E 与发布纠偏 |

## 真实 Agent 纠偏结论

[完整改造计划](REAL_AGENT_IMPLEMENTATION_PLAN.md) 的 R1～R7 已连续完成。每个子批次均包含实现、
测试、文档和独立本地 Git 提交；真实 DeepSeek 已通过原生 Tool Calling 在空目录建立并测试项目，
不是由 DevPilot 或 Codex 向验收目录代写源码。默认不执行 `git push`，远程推送仍需用户明确授权。

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

R3 已新增空目录编程所需的目录、写入、删除、Diff、状态、扫描和专用测试工具；写入具有
create-only/显式覆盖/hash 并发保护，测试非零退出形成失败 Tool Result，Windows 超时通过 Job Object
终止整个进程树。FastAPI 为每次 Project Session Run 把 ToolRuntime 精确绑定到注册项目路径，并按
RBAC 下发能力。真实 DeepSeek 已自主调用 `file.mkdir → file.write → file.read → workspace.status`
在忽略的隔离空目录创建证明文件，文件内容并非 Codex 手工写入。

R4 已增加供应商无关 `ContextAssembler` 和 REST/WebSocket 共用的 `RunCoordinator`。每次运行
恢复持久化会话历史，并按 Token 预算组装安全规则、项目 `AGENTS.md`、验收条件、Git 状态/Diff、
有限仓库画像及启用的用户/项目记忆。安全约束、项目规则、当前 Diff 和当前任务不可被裁剪；若必需
上下文本身超预算则明确拒绝运行。模型只看到项目根别名 `.`，工具根仍精确绑定注册项目路径。

R5 已把 RunRequest、Run/Event、结果、Token、工作区变化、审批、审计和 checkpoint 接入真实
AgentRuntime；LangGraph 通道状态使用持久 SQLite checkpointer。后台 Run 可查询、取消、恢复和审批，
同一 `run_id` 重连不重复执行工具；服务重启后等待审批仍可恢复。Web 工作台显示计划、实际模型、
Tool Call/Result、审批和 Token 卡片，刷新后从持久事件 API 恢复。

R6 已新增独立 `CompletionVerifier`：空目录、缺失文件、未恢复 Tool Error 以及用户要求测试但没有
成功 `test.run` 的运行都不能完成。验证问题和证据会结构化回注模型，最多允许两个有进展修复回合，
相同证据重复则提前停止。已有成果但仍未满足条件的运行使用 `partial/run.partial`，无成果则
`failed`；终态和 verification 会持久化，并由 REST、WebSocket 与 Web 时间线一致展示。确定性测试
覆盖伪完成拒绝、首轮测试失败后修复通过、重复失败终止及 API 持久证据，完整回归为 `119 passed`。

R7 已补齐逐次模型调用可观测性：每个调用均保存请求/实际模型、协议、选择器、首 Token 和总延迟、
输入/输出/缓存 Token、工具调用数、错误类型和供应商请求 ID；Run 返回逐次调用与汇总指标，
Session Usage 跨 Run 聚合，未配置价格时成本保持 `null`。调用方提供的 `capability_limit` 只能收窄
RBAC 能力，默认 `test.run` Schema 不再向模型暴露管理员未允许的任意命令入口。

真实验收使用本地 DeepSeek 配置和真实 FastAPI/WebSocket 运行，验收驱动没有向目标目录写源码：

- 创建 Run `event-lens-create-2b033f7561534992a584423c217c91d1` 由模型依次调用
  `file.list`、`repo.scan`、`file.mkdir`、`file.write`、`test.run`、`file.read`，自主创建
  `README.md`、`event_lens.py`、`examples/events.jsonl` 和 `tests/test_event_lens.py`，
  15 项测试通过；
- 同一 Session 的增量 Run `event-lens-increment-2b033f7561534992a584423c217c91d1`
  读取原文件后通过 `file.write` 修改并再次执行 `test.run`，25 项测试通过；
- Run `event-lens-anthropic-2b033f7561534992a584423c217c91d1` 使用
  `deepseek-anthropic` 协议独立产生 `file.read` Tool Call 并完成复核；
- 三个 Run 全部进入 `completed`。Session 汇总 Usage 为输入 201652、输出 10550、
  缓存读取 158464、总计 212202 Token，逐次模型与工具证据均已持久化。

最终自动门禁为 `153 passed`、Ruff、`devpilot doctor` 和 Vite production build 全部通过。
领域相关模块总覆盖率为 85.81%；纯 Tool Call 解析模块 99%、路径/策略 94%、审批 94%、
凭据设置 92%、恢复运行时 90%、CompletionVerifier 97%，满足计划中的覆盖率门槛。

- 问题分析：[REAL_AGENT_GAP_ANALYSIS.md](REAL_AGENT_GAP_ANALYSIS.md)
- 完整纠偏计划：[REAL_AGENT_IMPLEMENTATION_PLAN.md](REAL_AGENT_IMPLEMENTATION_PLAN.md)

R1～R7 的发布门槛已经满足；B8 目前只剩 Docker 环境中的容器发布验收。

## 其他待完成的发布验收

当前工作站未安装 Docker、`docker-compose` 或 Podman，因而未能运行容器配置解析和启动验收。
B8 仍不可标记为全部完成，直到在 Docker 可用环境执行：

```powershell
docker compose config
docker compose up --build -d
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
```

详见 [B8 设计](architecture/BATCH-08-STABILIZATION-RELEASE.md) 与[发布指南](RELEASE.md)。
