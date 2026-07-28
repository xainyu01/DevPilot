# 批次 B2：工具、策略与人工审批

## 目标

建立一个独立于 FastAPI 和数据库的工具运行时。所有工具必须先经过注册表、能力策略和工作区边界检查；高风险工具在执行前进入 LangGraph interrupt，只有明确的人工决定才能恢复执行。

## 已实现范围

- `packages/contracts/tools.py`：工具定义、调用、结果、风险级别、策略决定、审批和审计契约。
- `packages/tool_runtime/`：
  - `ToolRegistry`：显式工具白名单，不动态导入未知工具。
  - `PolicyEngine`：默认拒绝未声明能力，规范化路径并阻止工作区外访问。
  - `ApprovalStore`：仅支持本次、当前会话和命令模式三种短期审批范围，不提供永久授权。
  - `AuditLog`：记录策略、审批和执行生命周期；审批/审计参数会脱敏明显的密钥字段。
  - `ToolRuntime`：统一执行、超时、输出截断、错误归一化和审计。
  - `file.read`、`file.search`、`file.patch`、`shell.exec`、`git.exec` 内置工具。
- Agent 图：有显式 `RunRequest.tool_calls` 时经过 `execute_tools` 节点；高风险工具产生 `approval.required` 事件并暂停，批准后从 LangGraph checkpoint 恢复。
- API：`/api/v1/tools` 返回工具定义，不暴露执行句柄；阶段元数据更新为 2。

## 风险边界

| 工具/操作 | 风险 | 默认能力 | 审批 |
|---|---|---|---|
| `file.read`、`file.search` | 只读 | `workspace.read` | 否 |
| `file.patch` | 可恢复写入 | `workspace.write` | 否，但必须显式授予能力 |
| `git.exec` 的 status/diff/log/branch/worktree_list | 只读 | `git.read` | 否 |
| `shell.exec`、`git.exec` 的 add/commit/push | 高风险 | `shell.execute` 或 `git.write` | 是 |

Shell 只接受参数数组，不接受需要二次解析的命令字符串；可执行文件必须在固定白名单中，环境变量只保留平台运行所需的非秘密变量。Git 工具只生成固定操作对应的参数，路径仍需通过工作区边界检查。

## 验收

- 未注册工具、缺少能力和工作区外路径均不会执行。
- 高风险工具首次调用返回 `pending_approval`，不会启动子进程；审批决定会进入审计日志。
- `AgentRuntime.approve(...)` 将审批记录、事件和 LangGraph resume 组合为一个恢复操作。
- 读文件、搜索、补丁、工具超时、Shell/Git 风险分级和 API 工具目录均有测试覆盖。

## 非目标

- 数据库持久化、跨进程审批和长期审计存储属于 B3。
- MCP 服务器发现和工具级远程授权属于 B2 的后续接入点，不能绕过 `ToolRuntime`。
- Worktree 隔离与研发工作流属于 B4；本批次的 Git 工具不自动提交或推送。
