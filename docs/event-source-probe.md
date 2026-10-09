# Agent 事件来源探针（2026-09-30）

状态：探索性实验与设计建议；后续同步工具日志切片已在 [Spec 015](specs/015-synchronous-tool-events.md) 实施，但尚未实现可供 Web 消费的完整事件系统。本文件记录本机 `langchain==1.4.2`、当前安装的 LangGraph 与项目 `build_runtime_agent` 的行为，不把实验协议当作稳定契约。

## 要解决的问题

现有 `invoke_agent_run` 通过 Python `logging.extra` 记录 run 开始/终局；选模 middleware 记录模型选择、用量和失败。缺少工具执行事件。普通运行没有配置日志 handler/formatter 时，INFO 记录可能被过滤；`extra` 也不会自动成为持久轨迹或 Web 事件。下一切片需要确定可靠的事件来源和脱敏边界。

当前同步路径：`SessionService.run → invoke_agent_run → agent.invoke → create_agent 编译图 → 模型/工具节点`。应用层拥有可信 `session_id` 与内部 `thread_id` 映射，运行层消费这些 ID；项目事件不应暴露 `thread_id`。

## 本地假模型/假工具观察

使用 `FakeMessagesListChatModel` 构造一次 `AIMessage(tool_calls=[{'name': 'read_probe_value', 'args': {}, 'id': 'synthetic-call-1'}])`，工具返回合成值，再由假模型输出最终消息。另一例工具抛含合成敏感正文的 `RuntimeError`。全部通过内存运行，不读取 `.env`，不调用真实模型或待测服务；终端只输出事件类型、字段名和合成标识，不输出输入、工具返回或异常正文。

| 入口 | 成功观察 | 工具失败观察 | 关键边界 |
| --- | --- | --- | --- |
| `agent.stream_events(..., version='v3')` | 8 个底层事件；其中工具事件为 `tool-started → tool-finished` | `tool-started → tool-error`，原 `RuntimeError` 继续抛出 | 工具事件含 `tool_call_id`、工具名及原始 `input`/`output` 或失败 `message`；不能整包写日志。框架运行时发出 `LangChainBetaWarning`，本机源码也标注 v3 API 为 experimental。 |
| `@wrap_tool_call` 注入 `create_agent` | hook 可读 `request.tool_call['name']`、`['id']` 及 `request.runtime.context['run_id']`；观察到 `started → completed` | 观察到 `started → failed`，原 `RuntimeError` 继续抛出 | hook 跟随现有同步 `invoke` 路径，无需改成事件流；仅记录白名单元数据，不读取/记录 `args`、工具输出或异常正文。 |

实验还观察到 v3 事件顺序中，工具开始/结束分别位于模型工具调用消息和最终模型消息之间；这只是本机一次假模型运行结果，不将 8 个事件的数量或具体序号设为项目 API。

## 对下一切片的建议（待 Spec 明确）

首个**同步**工具事件切片优先使用 LangChain 官方支持的 `wrap_tool_call`，保持现有 `invoke_agent_run → agent.invoke` 路径。项目定义小的安全事件结构，至少关联 `run_id`、工具调用 ID、固定事件名与工具名；成功、异常和可能由框架返回错误 `ToolMessage` 的情况分别测试。`run_started/run_completed/run_failed` 增加 `session_id` 的方案应先修订 [Spec 013](specs/013-synchronous-run-events.md) 并保证字段脱敏；不要把内部 `thread_id` 放进普通事件。

未来 Web 实时输出可单独评估 `stream_events` 的投影、背压、失败与取消语义。LangChain [Streaming 文档](https://docs.langchain.com/oss/python/langchain/streaming)推荐新应用使用事件流，并展示 v3 工具调用投影；本机当前 v3 仍有实验性警告。因此现在不为工具事件而整体改写同步入口，也不宣称已具备 UI 事件推送或持久轨迹。

参考：[LangChain 自定义 middleware 文档中的 tool call monitoring](https://docs.langchain.com/oss/python/langchain/middleware/custom#tool-call-monitoring)；本机 Pi `a69bef789` 的 `packages/agent/src/agent-loop.ts` 使用 `AgentEventSink`，`packages/coding-agent/src/core/agent-session.ts` 通过 `_emit`/`subscribe` 对外分发；本机 AgentX `3fcfa0e` 的 `ConversationAppService` 与 `SseMessageTransport` 将应用会话和 SSE 传输分开。这些是职责划分参照，不是 Python 事件 API 的实现依据。
