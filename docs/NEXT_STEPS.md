# 下一步执行指南

## 当前状态

- 当前总体进度：60%。
- B0：已完成（基础骨架与交接机制）。
- B1：已完成（模型与 LangGraph 内核）。
- B2：已完成（工具、策略与人工审批）。
- 下一批：B4，阶段 4“研发工作流与仓库解析”。
- 事实来源：[progress.json](progress.json)。

计划书阶段 0 的 Web/Tauri 客户端仍是待办事项。由于当前约束是“只有 uv 管理依赖”，下一批先完成不依赖前端包管理器的 Agent 内核与契约，再单独处理客户端依赖边界。

## B1 已完成

文本 Agent 运行已具备明确状态、事件、暂停、恢复、取消和模型适配器边界，为后续工具审批和研发工作流提供稳定基础。

详细范围与验收标准见 [BATCH-01-LANGGRAPH-CORE.md](architecture/BATCH-01-LANGGRAPH-CORE.md)。

## B1 验收摘要

- `packages/contracts/` 提供状态、事件、checkpoint、TokenUsage 和多模态内容块契约。
- `packages/model_gateway/` 提供供应商无关适配器、能力检测、结构化能力错误和供应商转换。
- `packages/agent_core/` 提供最小 LangGraph 主图、FakeModel 运行、事件流和内存 checkpoint 引用。
- 测试覆盖确定性运行、事件序列、同会话多 run、能力拒绝、暂停/恢复、取消和流式事件。

## B3 已完成

数据库、会话消息/摘要、项目规则发现和长期记忆已经落地。`SessionMemoryService.restore()` 可以从数据库恢复会话消息；`RuleDiscovery` 保留来源、作用域和优先级；`LongTermMemoryStore` 默认拒绝凭据形态内容。

详细范围与验收标准见 [BATCH-03-DATA-MEMORY.md](architecture/BATCH-03-DATA-MEMORY.md)。

## B4 推荐执行顺序

1. 定义仓库 profile、证据、Issue、Bug 假设和开发任务契约。
2. 实现仓库文件树、语言/框架/命令识别和目录规则增量索引。
3. 接入 Issue、日志和失败测试输入，建立可追踪证据链。
4. 在 Worktree 边界内实现 Bug 定位、修复任务和回归测试闭环。
5. 完成 pytest、Ruff、doctor 和 B4 进度/交接文档。

## 每批次固定流程

每个批次都按以下闭环执行：

1. 阅读计划书、当前进度和上一份交接文档。
2. 在 `docs/architecture/` 写本批次范围、非目标和验收条件。
3. 只实现本批次范围内的代码与测试。
4. 用 `uv` 锁定并同步依赖，禁止引入未记录的安装步骤。
5. 运行测试、静态检查和最小启动验证。
6. 更新进度事实源与人类可读进度文档。
7. 生成交接文档；暂停时尤其要记录恢复命令和未完成项。

## 什么时候可以标记完成

一个批次只有在以下条件全部满足时才能标记为完成：

- 范围内的代码和测试已提交到工作区。
- 验收命令全部通过，或失败项已明确记录为阻塞。
- 没有把后续阶段能力标成当前阶段完成。
- 文档可以让另一个开发者从当前状态继续。
- `docs/progress.json`、`docs/PROGRESS.md` 和交接文档内容一致。

## 暂停与恢复

暂停前执行：

```powershell
uv --cache-dir .uv-cache run codeassist handover write --reason paused
```

恢复时按顺序执行：

```powershell
Get-Content docs\PROGRESS.md -Encoding UTF8
Get-ChildItem docs\handovers -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
uv --cache-dir .uv-cache sync --group dev
uv --cache-dir .uv-cache run pytest
```

然后从交接文档“恢复后优先处理”第一项继续，不要跳过进度源更新。
