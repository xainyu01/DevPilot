# 真实 Agent 运行架构

## R4 上下文边界

`RunCoordinator` 是 REST 与 WebSocket 创建 Agent Run 的统一应用层入口。它先通过既有模型选择
服务确定 endpoint/model，再把当前用户、Session、Project 和 RBAC capabilities 绑定到本次运行。
两种传输不再各自拼装 `RunRequest`。

`ContextAssembler` 是供应商无关的上下文组装器，按以下优先级构建模型消息：

1. 不可裁剪：系统安全规则、有效 capabilities、审批边界、项目规则、当前工作区状态与 Diff；
2. 可选：会话摘要、启用的用户/项目记忆、有限 RepositoryProfile；
3. 按预算从新到旧保留：完整的已持久化会话消息；
4. 不可裁剪：当前用户任务与服务器验收条件。

若不可裁剪内容本身超过预算，运行以明确的上下文预算错误关闭，不会丢弃安全规则、项目规则或
当前 Diff 后继续。RepositoryProfile 不包含符号全文，模型必须使用 `repo.scan`、`file.list` 和
`file.read` 按需获取源文件。

## 路径与现有变更保护

应用层始终使用 `ProjectRecord.root_path` 创建该 Run 的 `ToolRuntime`，并在执行工具前再次走项目
成员和会话权限检查。模型侧只看见根目录别名 `.`；规则来源转换为项目相对路径，RepositoryProfile
的根路径也转换为 `.`。

上下文明确把仓库文件、工具输出、记忆和附件视为不可信数据，不能借 Prompt Injection 扩大工具、
模型或 capability 范围。Git status 与 textual diff 作为必需上下文进入模型，系统指令要求读取后
再修改，并保护无关的用户未提交变化。

## 会话与记忆

用户消息在准备 Run 时持久化，随后读取整个 Session 历史和已有摘要。第二轮及后续运行会看到前面
的用户消息、Assistant 回复和 Tool Result。启用的用户记忆和项目记忆作为明确标注的不可信上下文
加入；附件仍以契约中的安全 ID 引用，不向模型传递本地附件路径。

## R5 持久化运行与恢复

真实 `AgentRuntime` 注入 `RunRepository`、`CheckpointRepository`、持久审批仓储和持久审计仓储。
主数据库保存原始 RunRequest、结果、事件、审批、usage、workspace changes 与 stop reason；
LangGraph 通道状态使用 `langgraph-checkpoint-sqlite` 存在工作区忽略目录中。事件 `(run_id,
sequence)` 唯一，同一 `run_id` 重连只读取既有记录。

服务重启时，上次中断的 `running` 记录转为可恢复的 `paused`；API 从持久请求重建相同项目根和当前
RBAC capabilities，再从复合 thread/run checkpoint 继续。审批仓储恢复原 request/fingerprint，
批准或拒绝后才会执行被中断的工具。

Web 工作台按事件显示计划、实际 endpoint/model、Tool Call 脱敏参数、Tool Result、审批卡片、
Token、文件变化和终态，并从事件 API 恢复刷新前的时间线。R6 将增加独立的服务器完成验证，不能以
模型自报成功替代工作区和测试证据。

## R6 服务器端完成验证

`CompletionVerifier` 在模型停止调用工具后独立检查工作区变化、未恢复的工具错误、验收条件要求的
文件，以及真实 `test.run` 成功证据。测试要求只由用户任务和服务器验收条件决定；模型报告属于
不可信输入，既不能用“测试通过”的文字替代工具证据，也不能凭空制造测试要求。

验证失败会把结构化 issues/evidence 作为系统反馈送回同一 Agent 循环。服务端最多允许两个有进展
的修复回合；相同证据指纹连续出现时立即停止，避免只重复成功声明。没有成果的运行进入 `failed`，
已有可用工作区或工具成果但仍未满足条件的运行进入独立的 `partial` 终态，均不会映射为
`completed`。

最终 `verification` 与 Run 结果一起持久化。API、WebSocket 和 Web 时间线都识别 `run.partial`；
因此刷新或重连后看到的终态、问题列表、测试次数和工作区证据与服务器最终判定一致。

## R7 调用可观测性与真实验收

每个模型回合先发出 `model.call.started`，随后以 `model.output` 闭合。持久事件包含 endpoint ID、
协议、请求与供应商返回模型、provider request ID、input/output/cache Token、首包与总耗时、
finish/stop reason、selector、重试/错误类型、Tool Call 数和费用占位。失败回合不保存供应商错误
正文，未知费用恒为 `null`。Run 与 Session Usage API 从持久事件聚合，不依赖内存状态。

Run 创建请求可用 `capability_limit` 进一步收窄 RBAC 权限。默认 `test.run` schema 不向模型暴露
任意 `command`，只允许服务器发现的测试 kind；只有管理员配置精确 allow-list 时才开放命令字段。
并行失败工具按一个模型批次计为一次无进展，使模型能获得整批错误并修复，而不会因单轮三个错误被
提前终止。

真实 E2E 驱动只负责创建/清理精确隔离目录、调用 FastAPI/WebSocket 和验证结果。Event Lens 的
Python、unittest、README 和 JSONL 示例全部由 DeepSeek 的原生 Tool Calls 写入。验收还在同一
Session 完成增量修改，并由 Anthropic-compatible endpoint 执行独立只读 Tool Call。

供应商 Tool Call 的名称映射、参数解析与 JSON Schema 校验集中在
`packages/model_gateway/tool_calls.py`，与供应商 SDK 转换层解耦。针对畸形 JSON、非对象参数、
缺失调用 ID、未知工具、名称碰撞和非法/重复 Schema 的独立覆盖率为 99%，避免由大适配器文件
的非解析分支掩盖安全门禁。
