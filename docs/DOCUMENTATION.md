# docs 文档管理规范

## 目录职责

```text
docs/
├── README.md                         # 文档入口
├── GETTING_STARTED.md                # 初始化与首次运行
├── NEXT_STEPS.md                     # 下一批实施顺序与恢复流程
├── DOCUMENTATION.md                  # 本规范
├── PROGRESS.md                       # 进度的人类可读视图
├── progress.json                     # 进度结构化事实源
├── architecture/                     # 批次设计、架构说明和验收标准
├── adr/                              # 不可逆或重要技术决策
├── api/                              # REST、WebSocket、DTO 和错误码
├── security/                         # 威胁模型、权限、审批和安全验证
└── handovers/                        # 暂停或用户要求时生成的交接文档
```

`api/` 和 `security/` 当前为空目录规划，相关能力实现到对应批次时再添加内容。

## 文档事实来源

- 计划范围以根目录现有的《项目重写计划书》为需求来源。
- 当前进度以 `docs/progress.json` 为机器可读事实来源。
- `docs/PROGRESS.md` 是给人阅读的同步视图。
- 批次范围、非目标和验收标准放在 `docs/architecture/`。
- 重要技术取舍写入 `docs/adr/ADR-xxxx-*.md`。
- 暂停、交接、换人或用户要求时生成 `docs/handovers/HANDOVER-*.md`。

## 命名规则

- 架构批次：`BATCH-NN-SHORT-NAME.md`。
- ADR：`ADR-NNNN-SHORT-NAME.md`，编号只递增不复用。
- 交接：`HANDOVER-YYYYMMDD-HHMMSS.md`，由 CLI 自动生成。
- 文档标题使用中文，代码符号、命令、路径和协议字段保持原文。

## 更新规则

1. 新文档只能放在 `docs/` 或其子目录，不能在根目录新增说明文档。
2. 修改依赖必须同时更新 `pyproject.toml` 和 `uv.lock`。
3. 修改 API、事件或领域契约时，先更新对应契约/架构文档，再更新代码和测试。
4. 每次批次验收后同步 `progress.json` 与 `PROGRESS.md`。
5. 交接文档是状态快照，不手工改写历史交接；需要新状态就生成新文件。
6. 不在文档中写入 API 密钥、密码、长期 Token 或未经脱敏的日志。

## 交接文档最低内容

交接 Agent 生成的文档必须包含：

- 生成原因和时间。
- 总体进度、当前批次和下一步。
- 每个批次的状态与完成度。
- 已完成、进行中、恢复后优先处理和阻塞项。
- 依赖管理和其他已确认约束。
- 工作区快照、验证命令和恢复命令。

如果进度源缺失或结构不合法，交接 Agent 应失败并提示修复 `docs/progress.json`，不能生成看似完整但无事实来源的文档。
