# 批次 B3：数据、记忆与项目上下文

## 目标

为会话、消息、LangGraph checkpoint、运行事件、项目规则和长期记忆建立可重启恢复的持久化边界。领域 DTO 位于 `packages/contracts`，数据库实现集中在 `packages/persistence`，因此后续可以把 SQLite 替换为 PostgreSQL。

## 已实现范围

- `packages/persistence/models.py`：SQLAlchemy 2 relational schema。消息与内容块、事件和 checkpoint 分表保存。
- `packages/persistence/repositories.py`：项目、会话、消息、摘要、规则、记忆、运行事件和 checkpoint 仓储。
- `packages/memory/session.py`：会话消息追加、重启恢复和确定性摘要。
- `packages/memory/long_term.py`：人类可读 `MEMORY.md`，支持新增、编辑、启用/禁用、删除和历史版本索引。
- `packages/project_context/discovery.py`：发现用户记忆、`AGENTS.md`、`CLAUDE.md` 和 `.codeassist/*.md`，记录来源、作用域和优先级。
- `migrations/`：Alembic 初始迁移；通过 `CODEASSIST_DATABASE_URL` 可切换 SQLite/PostgreSQL。

## 记忆安全边界

长期记忆写入先经过候选检查。常见 API key、token、password 和 secret 形态默认拒绝；API 密钥和一次性错误不会被自动写入 Markdown。调用方若要处理特殊受控环境，必须显式选择允许敏感内容，并承担审查责任。

## 使用方式

```powershell
uv run alembic upgrade head
uv run pytest
uv run ruff check .
```

默认 API 数据库为工作区 `.codeassist/codeassist.db`。测试可以把 `create_app(database_url="sqlite://")` 传入内存数据库。`SessionMemoryService.restore()` 通过 `thread_id` 重新加载会话消息，数据库进程重启不会清空这些行。

## 非目标

- 本批次不实现向量数据库；规则和记忆检索先使用来源、优先级和关键词可替换接口。
- 本批次不实现多用户认证和团队 RBAC；模型保留 `user_id`、`project_id` 外键入口，权限校验由后续批次接入。
- 本批次不把 LangGraph 的内部 saver 替换成供应商专属存储；对外 checkpoint 合约已持久化，后续可接入原生 durable saver。
