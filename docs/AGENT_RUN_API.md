# Agent Run API

所有端点都需要 JWT Bearer token，并沿用 Session 与 Project 的资源级 RBAC。调用者不能通过
已知 `run_id` 绕过会话或项目权限。

## 创建与后台执行

`POST /api/v1/sessions/{session_id}/runs` 接收 `RunCreateRequest`。默认等待终态；设置
`background: true` 后立即返回已持久化的 Run 记录。客户端应自己生成并复用 `run_id`：服务端对
同一 Session 的相同 ID 幂等返回已有记录，不会再次执行工具或追加重复消息。

可选限制包括 `context_max_tokens`、`max_tokens` 和 `acceptance_criteria`。模型 endpoint/model
仍通过 R4 的统一选择与上下文协调器确定。

## 查询

- `GET /api/v1/runs/{run_id}`：状态、实际 endpoint/model、停止原因和恢复信息；
- `GET /api/v1/runs/{run_id}/events?after_sequence=N`：按严格递增序号读取事件；
- `GET /api/v1/runs/{run_id}/changes`：本次 Run 基线之后的文件变化和 textual diff；
- `GET /api/v1/runs/{run_id}/usage`：已记录 Token 与 provider request ID。

事件包括计划、模型调用、Tool Call 的脱敏参数、Tool Result、审批与终态。包含
`api_key`、`token`、`secret` 或 `password` 的事件字段会在持久化前替换为 `[redacted]`。

## 控制与审批

- `POST /api/v1/runs/{run_id}/cancel`：取消运行中或等待中的 Run；
- `POST /api/v1/runs/{run_id}/resume`：从持久 checkpoint 继续安全暂停的 Run；
- `POST /api/v1/runs/{run_id}/approvals/{request_id}`：批准或拒绝高风险调用。

审批请求体至少包含 `approved`，可选 `scope` 为 `once`、`session` 或 `command`。服务器再次计算
审批者当前项目 capabilities；普通成员不能批准超出其项目角色的操作。

## 恢复

RunRequest、Run/Event、结果、usage、workspace changes、stop reason、审批和应用层 checkpoint
存入主数据库；LangGraph 的通道 checkpoint 存入工作区忽略目录
`.devpilot/agent-graph.sqlite`。服务启动时将上次中断的 `running` Run 标记为可恢复暂停状态。
重建 Runtime 后使用同一复合 thread/run checkpoint 继续，已经成功且具有相同 call ID 的工具结果
不会重复执行。
