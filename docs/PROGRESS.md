# CodeAssist 2.0 实施进度

> 最后更新：2026-07-28
> 当前批次：B4
> 总体完成度：70%

## 进度规则

- 进度以 `docs/progress.json` 为事实来源，本文件用于人类快速查看。
- 每个批次都要有范围、完成定义、验证结果和下一步。
- 暂停或用户要求时，运行交接命令生成 `docs/handovers/HANDOVER-*.md`。

## 批次状态

| 批次 | 状态 | 批次完成度 | 内容 |
|---|---|---:|---|
| B0 | 已完成（本批次） | 100% | Monorepo、FastAPI、CLI、契约、交接 Agent |
| B1 | 已完成（本批次） | 100% | LangGraph 内核、模型网关、多模态、运行生命周期 |
| B2 | 已完成（本批次） | 100% | 工具注册、策略、文件/搜索/补丁、Shell/Git、审批、审计 |
| B3 | 已完成（本批次） | 100% | 数据库、会话记忆、项目规则、长期记忆 |
| B4 | 已完成（本批次） | 100% | 仓库解析、增量索引、证据链、Agent 执行树、测试调度、Worktree、PR 文档 |
| B5-B8 | 未开始 | 0% | CLI/Web、桌面端、团队共享、稳定化发布 |

## B0 已完成

- [x] 建立 `apps/`、`packages/`、`tests/`、`docs/` 分层。
- [x] 建立 FastAPI 健康检查和元数据接口。
- [x] 建立结构化进度事实源。
- [x] 实现可从 CLI 或 API 未来调用的交接 Agent。
- [x] 初始化 Git 仓库。
- [x] 补充初始化、下一步和 docs 管理指南。

## B0 验收结果

- [x] 使用 uv 生成 `uv.lock` 并同步开发依赖。
- [x] `uv run pytest`：5 passed。
- [x] `uv run ruff check .`：通过。
- [x] `uv run codeassist doctor`：通过。
- [x] `uv run codeassist handover write --reason requested`：成功生成交接文档。

## 计划书阶段 0 的剩余项

- [ ] React Web 与 Tauri 连通骨架尚未开始；因当前约束为 uv-only，后续批次需先确定前端依赖的处理边界。

## B1 已完成

- [x] 锁定 LangGraph、LangChain OpenAI/Anthropic 依赖并同步 `uv.lock`。
- [x] 定义 `AgentState`、`RunContext`、`RunEvent`、`Checkpoint`、`TokenUsage` 和多模态内容块契约。
- [x] 实现 `load_context` → `normalize_input` → `plan` → `call_model` → `finalize` 最小状态图。
- [x] 实现确定性的 `FakeModel`、OpenAI/Anthropic 适配器和明确未实现的 Ollama 适配器。
- [x] 实现能力检测、结构化能力错误、事件序列、暂停、恢复、取消和幂等运行。
- [x] 更新 API 阶段元数据并补充 B1 单元/契约测试。

## B1 验收结果

- [x] `uv run pytest`：17 passed（2 个上游弃用警告）。
- [x] `uv run ruff check .`：通过。
- [x] `uv run codeassist doctor`：通过。
- [x] FakeModel 可完成确定性文本运行并产生开始、计划、模型输出和完成事件。
- [x] 同一会话可切换不同 `run_id`；暂停/恢复使用 checkpoint；取消不会报告为成功。

## B2 已完成

- [x] 定义工具、风险、策略、审批、结果和审计契约。
- [x] 实现显式 `ToolRegistry`、默认内置工具和 `/api/v1/tools` 目录。
- [x] 实现工作区边界、能力检查、风险分级、超时和输出限制。
- [x] 实现文件读取、搜索、精确文本补丁和统一补丁。
- [x] 实现参数化白名单 Shell 与固定操作 Git 工具。
- [x] 实现本次/会话/命令范围审批、脱敏审计、LangGraph interrupt 恢复。
- [x] Agent 有显式工具调用时经过 `execute_tools` 节点并保留工具结果。

## B2 验收结果

- [x] `uv run pytest`：23 passed（2 个上游弃用警告）。
- [x] `uv run ruff check .`：通过。
- [x] `uv run codeassist doctor`：通过。
- [x] 未授权能力、工作区外路径和未注册工具不会执行。
- [x] 高风险 Shell/Git 写操作首次调用只产生审批请求；批准后可从 checkpoint 恢复并记录审计。

## B3 已完成

- [x] 建立 SQLAlchemy 2 数据模型、SQLite 默认连接和 Alembic 初始迁移。
- [x] 实现项目、会话、消息、内容块、摘要、记忆、规则、运行事件和 checkpoint 仓储。
- [x] 实现会话消息重启恢复、确定性摘要和独立的事件/checkpoint 持久化。
- [x] 发现并合并用户 `MEMORY.md`、`AGENTS.md`、`CLAUDE.md` 和 `.codeassist/*.md`，记录来源、作用域和优先级。
- [x] 实现长期记忆 Markdown 的新增、编辑、启用/禁用、删除和数据库修订索引。
- [x] 默认阻止 API key、token、password 和 secret 形态写入长期记忆。
- [x] 增加项目、会话、消息、规则发现和长期记忆 API。

## B3 验收结果

- [x] `uv --cache-dir .uv-cache run pytest`：28 passed（2 个上游弃用警告）。
- [x] `uv --cache-dir .uv-cache run ruff check .`：通过。
- [x] `uv --cache-dir .uv-cache run alembic downgrade base`：通过。
- [x] `uv --cache-dir .uv-cache run alembic upgrade head`：通过。
- [x] API 集成测试验证新建应用进程后仍可恢复会话消息和项目索引。
- [x] 长期记忆敏感信息拒绝、编辑、启用/禁用、删除和规则优先级均有测试覆盖。

## B4 已完成

- [x] 实现语言、框架、包管理器、命令、规则、符号和 Git 元数据扫描。
- [x] 实现基于文件哈希和 stat 元数据的增量索引并保存 `RepositoryProfile`。
- [x] 定义 `IssueContext`、`EvidenceItem`、`BugHypothesis` 和 `WorkflowRun` 结构化契约。
- [x] 实现 Issue/日志/失败测试的证据收集和带置信度 Bug 假设。
- [x] 实现 Supervisor、按需子 Agent、Assignment 权限交集、深度/并发/数量/预算限制和资源回收。
- [x] 实现确定性 `ModelRouter`、测试计划/并行/超时/重试/产物和 Worktree 租约。
- [x] 实现 PR Markdown 生成、导出和人工审核状态 API。
- [x] 新增 `repository_profiles`、`workflow_runs` 持久化和 B4 Alembic 迁移。

## B4 验收结果

- [x] `uv --cache-dir .uv-cache run pytest`：35 passed（2 个上游弃用警告）。
- [x] `uv --cache-dir .uv-cache run ruff check .`：通过。
- [x] 临时 SQLite 数据库完成 `alembic upgrade head`、`downgrade base`、`upgrade head` 往返，最终为 `0002_b4_workflows (head)`。
- [x] B4 专项测试验证增量索引、权限边界、模型预算、测试重试/超时、Worktree 回收和 PR 文档追溯。
- [x] API 集成测试验证仓库扫描、工作流 Agent 执行树、PR 审核状态和新进程恢复。
- [x] B4 文档见 `docs/architecture/BATCH-04-DEV-WORKFLOWS.md`。

## 验收命令

```powershell
uv sync --group dev
uv run pytest
uv run ruff check .
uv run codeassist doctor
uv run codeassist handover write --reason paused
```

## 下一批次

B5 将建立 CLI 与 Web 工作台，并消费 B1～B4 的 REST、事件、审批和研发工作流契约。
