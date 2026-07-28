# 批次 B4：研发工作流与仓库解析

## 目标

建立一个不依赖供应商模型即可验证的研发助手 MVP：扫描仓库、建立增量索引、从 Issue/日志/失败测试生成证据链和 Bug 假设，并把测试、隔离 Worktree、Agent 执行树和 PR 文档串成可恢复的结构化工作流。

## 设计边界

- `packages/repo_intel` 只负责文件、生态、命令、规则、符号和 Git 元数据；索引缓存写入项目 `.codeassist/`，不把运行数据提交到仓库。
- `packages/dev_workflows` 只依赖契约和领域服务，不依赖 FastAPI 或 SQLAlchemy；持久化通过 Protocol 注入。
- Supervisor 与子 Agent 通过 `AgentAssignment` 传递权限，工具、能力、路径、模型和预算取父任务与角色策略的交集。
- 子 Agent 默认深度为 1，服务端硬限制并发子 Agent、总子 Agent、Token 和墙钟时间；终态运行标记资源已回收。
- 测试执行器只接受显式 argv，不调用 shell；测试输出和产物在 `.codeassist/artifacts/`，敏感形态脱敏。
- Worktree 修改必须使用明确的文件、旧文本、新文本和人工批准；工作流本身只生成建议，不自动合并或推送。
- PR 文档由结构化证据、变更文件和测试结果生成，并有独立人工审核状态。

## 公开接口

- `POST /api/v1/projects/{project_id}/repository/scan`
- `GET /api/v1/projects/{project_id}/repository-profile`
- `POST /api/v1/workflows`
- `GET /api/v1/workflows/{workflow_id}`
- `GET /api/v1/workflows/{workflow_id}/agent-tree`
- `GET /api/v1/workflows/{workflow_id}/pr`
- `PATCH /api/v1/workflows/{workflow_id}/pr/review`

工作流快照保存在 `workflow_runs`，仓库索引保存在 `repository_profiles`；两者均可在新应用进程中恢复。

## 验收标准

1. 对样例缺陷仓库可得到语言、框架、包管理器、命令、规则、符号和 Git 概况。
2. 第二次扫描复用未变化文件哈希，只报告变化和删除文件。
3. Issue、日志和失败测试可映射到证据项、文件/行号和带置信度的 Bug 假设；无证据时必须明确标记未解决。
4. Agent 执行树可展示 Supervisor、按需子 Agent、父子关系、状态、权限、预算和资源回收。
5. 模型路由先过滤能力、健康、角色、隐私和预算，再以固定规则排序，并提供 fallback 候选。
6. 测试计划支持依赖、并行上限、资源锁、超时、重试、取消和 stdout/stderr 产物。
7. Worktree 租约可创建、批准后编辑、释放；PR Markdown 只能导出草稿并保留证据与测试引用。
8. `pytest`、Ruff、Alembic `upgrade head`/`downgrade base` 和 API 重启恢复测试通过。
