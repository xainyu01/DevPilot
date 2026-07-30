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

R5 将在此入口之后增加持久化 Run/Event/Checkpoint、后台控制和恢复 API；R6 将增加独立的服务器
完成验证，不能以模型自报成功替代工作区和测试证据。
