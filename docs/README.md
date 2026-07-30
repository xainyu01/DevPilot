# DevPilot 文档中心

所有本次重写产生的说明、架构决策、进度和交接文档统一放在本目录。建议先读初始化指南，再读下一步执行指南。

## 目录约定

- `PROGRESS.md`：当前进度的人类可读视图。
- `progress.json`：交接 Agent 使用的结构化进度事实来源。
- `GETTING_STARTED.md`：环境初始化、启动、验证和交接命令。
- `NEXT_STEPS.md`：批次推进顺序、完成定义、暂停和恢复流程。
- `DOCUMENTATION.md`：docs 目录、命名和更新规范。
- `REAL_AGENT_GAP_ANALYSIS.md`：原生工具现状、真实端到端失败证据和核心缺口。
- `REAL_AGENT_IMPLEMENTATION_PLAN.md`：R1～R7 连续纠偏任务、验收标准和新对话提示词。
- `architecture/`：架构与阶段设计。
- `adr/`：架构决策记录。
- `handovers/`：暂停或用户要求时生成的交接文档。

## 当前批次

当前执行 B8 发布前纠偏。多模型配置、Web 工作台、策略和工具基础已经存在，但真实端到端
测试证明模型还不能自主产生 Tool Call、写文件或运行测试。先按
[真实编程 Agent 完整改造计划](REAL_AGENT_IMPLEMENTATION_PLAN.md) 完成 R1～R7，再执行
剩余容器发布验收。

## 推荐阅读顺序

1. [GETTING_STARTED.md](GETTING_STARTED.md)
2. [PROGRESS.md](PROGRESS.md)
3. [真实 Agent 缺口分析](REAL_AGENT_GAP_ANALYSIS.md)
4. [真实编程 Agent 完整改造计划](REAL_AGENT_IMPLEMENTATION_PLAN.md)
5. [NEXT_STEPS.md](NEXT_STEPS.md)
6. 当前批次的架构说明与 ADR
7. 最新的 `handovers/HANDOVER-*.md`

## 发布候选

- [B8 稳定化与发布设计](architecture/BATCH-08-STABILIZATION-RELEASE.md)
- [部署、升级与回滚指南](RELEASE.md)
