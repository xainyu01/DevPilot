# 仓库协作指南

## 项目结构与模块组织

- `apps/api/`：FastAPI 应用和 HTTP 入口。
- `apps/cli/`：Typer/Rich 命令行入口。
- `packages/contracts/`：与框架无关的 Pydantic 契约。
- `packages/handover_agent/`：确定性的进度和交接文档生成器。
- `tests/unit/` 与 `tests/integration/`：单元测试和 API 集成测试。
- `docs/`：架构、ADR、进度、交接和开发指南。
- `pyproject.toml` 与 `uv.lock`：唯一的 Python 依赖定义文件。

领域包必须独立于 FastAPI、数据库和供应商专属模型代码。普通项目文档放在 `docs/`；根目录的本文件是仓库级贡献者和 Agent 指南。

## 计划书依据

实施必须以根目录的 `项目重写计划书.md` 为主要依据。按阶段 0～8 分批推进，每批先明确范围和验收标准，再实现代码、测试和文档；不得把后续阶段能力提前标记为完成。当前 B0 基础骨架已完成，下一步是 B1 LangGraph 内核。遵守以下已确认约束：Python 依赖只由 uv 管理，新产生的普通文档放在 `docs/`，高风险工具必须经过策略和审批，未实现能力必须返回明确状态。

## 构建、测试与开发命令

所有 Python 环境操作都使用 uv。当前机器的系统缓存路径存在冲突，因此命令显式指定缓存目录：

```powershell
uv --cache-dir .uv-cache sync --group dev
uv --cache-dir .uv-cache run pytest
uv --cache-dir .uv-cache run ruff check .
uv --cache-dir .uv-cache run devpilot serve
uv --cache-dir .uv-cache run devpilot progress
uv --cache-dir .uv-cache run devpilot handover write --reason paused
```

按照当前仓库的依赖策略，不要使用 `pip install`、`requirements.txt`、npm 或 pnpm。

## 编码风格与命名约定

目标 Python 版本为 3.11 及以上。使用四个空格缩进、类型注解和 UTF-8 源文件。模块、函数和变量使用 `snake_case`；类和 Pydantic 模型使用 `PascalCase`。Ruff 负责导入排序，并检查 `E`、`F`、`I` 和 `UP` 规则；单行长度限制为 100 个字符。

## 测试规范

使用 pytest。测试文件必须命名为 `test_*.py`，测试函数使用 `test_*`。交接前必须运行完整测试套件。计划书要求领域代码覆盖率至少 80%，安全、路径、审批和凭据代码覆盖率至少 90%。

## Commit 与 Pull Request 规范

本仓库目前没有历史提交记录。提交消息使用简洁的祈使句和明确范围，例如 `feat: add run event contract`、`fix: reject unsafe handover path` 或 `docs: update B1 guide`。PR 必须说明变更范围、测试命令及结果、进度和文档更新、安全影响以及关联 Issue；涉及界面变更时附上截图，任何情况下都不得提交密钥。

### 批次完成提交要求

- 每完成一个明确批次（例如 B0、B1、B2、B3），必须创建一个对应的 Git 提交；不得只更新工作区而不提交。
- 批次提交前必须完成该批次的代码、测试和文档，更新 `docs/progress.json`、`docs/PROGRESS.md`，并生成最新交接文档。
- 提交前必须运行完整测试、Ruff 和该批次要求的验收命令；验收失败时不得把批次标记为完成，也不得创建完成批次提交。
- 批次提交应包含该批次相关的代码、测试、`docs/` 文档和依赖锁文件变更；提交消息使用明确范围，例如 `feat: complete B3 data and memory`。
- 提交前检查 `git status` 和 `git diff --check`，确认没有密钥、凭据、运行时数据库或无关文件；批次完成后再次确认工作区状态。
- 中途暂停或交接时，如果批次尚未完成，只生成交接文档并明确记录未完成项，不得将其描述为已完成批次。

## Agent 专用工作流

开始工作前先阅读项目计划书、`docs/PROGRESS.md` 和 `docs/handovers/` 中最新的交接文档。每次只处理一个明确批次；完成后更新 `docs/progress.json` 和 `docs/PROGRESS.md`，运行测试与 Ruff，并在暂停或移交工作前生成交接文档。
## 未来计划与 TODO 标注规则

- 仅实现当前批次和当前计划书验收范围；未来阶段暂不需要实现的能力必须保留接口或契约，并在代码、函数定义、类定义和接口定义附近添加中文 `TODO` 注释。
- TODO 注释必须说明“暂不实现的原因、对应未来批次或计划章节、预期返回状态”，不得把未实现能力伪装成成功或静默降级。
- 对未来平台适配，优先抽取端口/协议/适配器接口；Linux、macOS、桌面壳层、远程控制、真实供应商等未到当前批次的实现只保留接口、能力声明和 TODO，不得把平台分支散落到领域核心。
- 新增 TODO 时同步更新 `docs/progress.json`、`docs/PROGRESS.md` 或相关架构文档；完成 TODO 前先补测试和验收标准。
- 注释示例：`# TODO（后续 B7）：暂不实现 Linux 适配器，当前仅保留 PlatformAdapter 接口并返回 declared_not_implemented。`

## GitHub 公开仓库与本地学习资料规则

- `master` 是公开代码分支，对应 GitHub 远程 `origin/main`；公开提交不得包含 `docs/learn/`。
- `local-with-learn` 是本地资料分支，保留 `docs/learn/` 的版本历史；禁止向任何远程仓库推送该分支。
- `docs/learn/` 已加入 `.gitignore`。如果必须在本地资料分支追踪新学习文件，只能在 `local-with-learn` 上显式暂存，不能在 `master` 上使用 `git add -f`。
- 公开代码提交流程必须在 `master` 上完成，并使用 `git push origin master:main`；推送前检查 `git ls-tree -r --name-only HEAD docs/learn` 不应有输出。
- 如果发现公开分支历史包含 `docs/learn/`，必须先保留本地备份分支，再过滤公开历史；不得直接删除本地学习资料或覆盖 `local-with-learn`。

## B7 固定用户与发布前认证约束

- `admin`、`admin1`、`admin2`、`admin3` 是 B7 至最终公开上线前的正式固定用户；服务启动时必须通过正常仓储写入 `users` 表，JWT 的 `sub` 必须对应这些用户 ID。
- 这些用户继续走数据库中的团队成员、项目成员和会话共享/RBAC 授权链路；不得使用绕过用户、成员关系或资源级权限检查的演示分支。
- 在最终公开上线前保留既有账号和密码，不另行替换为外部凭据配置。认证令牌可升级为有过期时间和签名校验的 JWT；最终上线前的凭据替换必须作为独立、安全审计过的变更推进。
