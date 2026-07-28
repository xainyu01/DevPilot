# 批次 B1：模型与 LangGraph 内核

## 目标

实现一个可确定性测试的 LangGraph Agent 运行内核，支持运行生命周期、流式事件、检查点引用和暂停/恢复/取消的协议基础。

## 本批次范围

- Agent 状态和运行上下文的 Pydantic/TypedDict 契约。
- `thread_id`、`run_id`、事件序列号和统一事件元数据。
- 供应商无关的 `ChatModelAdapter` 接口。
- OpenAI、Anthropic 和 Ollama 适配器的接口/能力声明；真实调用按凭据和集成测试开关执行。
- 最小 LangGraph 主图和 FakeModel。
- 文本内容块，以及为图片/PDF 预留的统一内容块契约。
- 暂停、恢复、取消和幂等状态的单元测试。

## 非目标

- 工具执行、Shell、MCP 和人工审批的实际授权逻辑；这些属于 B2。
- 数据库持久化和完整 checkpoint 存储；B3 接入 persistence 后实现。
- Web/Tauri 客户端；客户端只在后续批次消费公开契约。
- Ollama 实际推理调用。

## 推荐目录

```text
packages/
├── agent_core/       # 状态、图、节点、运行策略
├── model_gateway/    # ChatModelAdapter 与供应商适配器
└── contracts/        # 状态、事件、内容块和错误码
tests/
├── unit/             # 状态、节点、能力判断
└── contract/         # 供应商消息转换与事件协议
```

## 验收标准

1. FakeModel 可以完成一次确定性文本运行，并产生开始、计划、模型输出和完成事件。
2. 同一个 `thread_id` 可以创建不同的 `run_id`，事件序列从 1 单调递增。
3. 运行被标记为暂停后，不再执行后续节点；恢复时从保存的节点状态继续。
4. 取消运行后输出明确的取消状态，不伪装成成功完成。
5. 不支持的多模态能力在调用前返回结构化能力错误，不静默丢弃内容块。
6. `uv run pytest`、`uv run ruff check .` 和 `uv run devpilot doctor` 通过。
7. 更新 `docs/progress.json`、`docs/PROGRESS.md`，并生成 B1 交接文档。

## 完成后的下一步

B2 接入工具注册、PolicyEngine、文件/搜索/补丁/Shell/Git 工具，以及带审计的人工审批恢复流程。
