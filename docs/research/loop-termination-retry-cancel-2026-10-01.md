# 运行终止、错误、重试与用户停止的调用链复核

日期：2026-10-01。静态源码版本：Pi `a69bef789bc95abf0acee16f7b4660b70b650bb9`、AgentX `3fcfa0e5c224fc64ca63aa60fd871e5d2f25159f`、项目本机 LangChain `1.4.2` / LangGraph `1.2.12`。另有本地假模型探针；没有真实故障供应商、真实取消或真实截断样本。本记录是设计调研，不是实现 Spec。

## Pi 的实际判断

`packages/agent/src/agent-loop.ts:193-274`：先收到适配器统一的 `AssistantMessage`。`error/aborted` 立即发 `turn_end/agent_end` 并退出；其他原因下从 `message.content` 提取真正的 `toolCall`，有调用就执行或在 `length` 时为每个调用生成失败工具结果，然后检查 `prepareNextTurn`、`shouldStopAfterTurn`、steering。没有待执行工具与 steering 后，外层检查 follow-up；无消息才自然结束。判断工具执行的关键是内容中的 `toolCall`，不是仅凭 `stopReason=toolUse`。工具批次也可能按 `terminate` 结束；工具失败不必然等于模型/运行硬停。

`packages/agent/src/agent.ts:318-320,486-535` 为每个活动 run 建 `AbortController`，`abort()` 发 signal；运行失败/取消生成分别标为 `error/aborted` 的事件并在 `finally` 清理。`packages/ai/src/api/anthropic-messages.ts:565-582,770-793` 和 `openai-responses.ts:140-160,165-192` 在请求/流边界将异常或 signal 变成 `error/aborted`。`packages/ai/src/utils/provider-retry.ts:105-125` 将 SDK 原生重试设为 0 后接管可重试请求，并在重试等待期间观察 signal。因此 Pi 的请求重试位于一次模型调用之内，不等于重跑整个 agent turn；其可中断等待也是明确接线，不是 SDK 默认行为。

## AgentX 的可迁移与限制

`AgentX/.../AbstractMessageHandler.java:337-433` 把 LangChain4j fork 的 `TokenStream` 接到 `onError/onCompleteResponse/onToolExecuted`，表明应用消费的是不同终局/工具事件；`TaskExecutionHandler.java:110-145` 的 `AiServices.builder(...).toolProvider(...).build()` 将部分 Agent 工具循环交给框架。不能由其事件消费代码推断它自己重写了 loop，或推断 Python LangChain 的 stop reason 行为。

`ChatSessionManager.java:94-131` 的 interrupt 设内存 flag、移出活动表、发送并关闭 SSE；本机搜索 `isSessionInterrupted` 仅发现定义，未找到模型/工具执行路径消费它的调用点。此源码只证实传输层中断，不证实远端模型请求和工具实际取消。`application.yml` 的 RabbitMQ listener retry 与模型请求重试无关；所核对的对话处理路径未见明确的模型请求重试策略，不能声称 AgentX 没有任何底层重试。

## 本项目与当前 LangChain 的真实边界

`model_access` 构造在线模型时传 `timeout=60` 秒、`max_retries=2`（均可配置），本地 HTTP mock 已验证可重试 503 最多请求 3 次；Ollama 只配置 timeout。`agent_runtime.make_model_selection_middleware` 的 `handler(request.override(model=model))` 只接收到最终模型响应或最终抛出的异常。异常走 `classify_model_failure → model_call_failed → run_failed`，原异常向上传；项目没有装配 LangChain `ModelRetryMiddleware`，没有重复调用整个 `agent.invoke`。此重试不覆盖解析之后的异常、工具错误或“成功 HTTP 响应但业务上失败”的 `AIMessage`，60 秒也不是整次 run 的墙钟预算。

本机 `.venv/.../langchain/agents/factory.py:1468-1496,1929-1978,2004-2040`：模型适配器先返回 `AIMessage`；路由看 `AIMessage.tool_calls` 与已回填的 `ToolMessage`，不消费项目 `naiyue_stop_reason`。有 pending 有效工具调用时进入 `ToolNode`；无有效工具调用时通常退出。还有 `jump_to`、结构化输出工具、`return_direct` 等路径，不能把“tool use 就必定继续、stop/length 就必定结束”当完整状态机。当前项目用 `naiyue_stop_reason=length` 在 `wrap_tool_call` 阻止明确截断的工具执行，但 `error` 只是元数据，尚未阻止工具或使 run 失败；`unknown` 仍允许正常工具路径。

本机 `langchain_openai/chat_models/base.py:218-257` 对不能解析的原始工具调用放入 `AIMessage.invalid_tool_calls`；路由只看 `tool_calls`。对该适配器 `_convert_dict_to_message` 直接输入合成无效参数 `'{bad'`，实际得到 `AIMessage`、有效工具调用 0、无效工具调用 1，ID `broken-call` 保留。本地不落盘假模型 Agent 探针实际观察：`finish_reason=content_filter` + 有效工具调用时工具执行 1 次、终局 `run_completed`；`status=failed` + 无工具时终局 `run_completed`；只含 `invalid_tool_calls` 时工具 0 次、终局 `run_completed`。这证明当前代码不能靠 AIMessage 元数据中的 `error` 兜住所有失败；更不能兜住发生在消息构造之前的解析/网络异常。后者若抛 `Exception` 会被现有模型/run 异常路径记录并继续抛出。个别工具错误可转为错误 `ToolMessage` 后让模型继续，也不是一律硬停。

当前 `application.SessionService.run` 与 CLI 均是同步 `invoke`；没有活动运行句柄、用户停止接口、steering/follow-up 队列。`:quit` 只在下一次读输入时生效。现有异步假工具探针曾观察 `ainvoke` 任务取消可传播到该工具，但同步 middleware 在异步模型调用上未实现，因此不能宣称当前应用支持取消。AgentX 式关闭 SSE 也不能当取消执行的证明。

## 建议的最小后续顺序

1. 先给运行终局确立契约，并做一个拒绝式错误切片：在模型响应进入框架工具路由前，对明确 `error` 与仅有 `invalid_tool_calls` 的消息阻止工具执行和正常完成；异常保持原样向上抛，入口生成安全失败展示。用假模型覆盖错误+工具、错误无工具、解析失败工具、正常工具、普通文本截断；并核对 checkpoint 与事件。对未知元数据、部分有效/部分无效工具调用需在 Spec 中作明确选择，避免假装已经全部覆盖。
2. 再单独做安全边界停止：以 `run_id` 识别活动运行，请求停止后阻止下一次模型调用与尚未开始的工具；已开始的工具结果可能未知。决定停止后 pending 工具调用/会话 checkpoint 的合法状态，再写并发测试。
3. 真正活动取消需要先打通异步 middleware/入口，再验证模型、工具和重试等待均接收到取消，记录独立的 `cancelled` 终局。用事件同步和本地 HTTP mock，不用极短 sleep；不对不可逆副作用作“已取消”的推断。

当前建议只针对已经发现的行为缺口，不重写 `create_agent` 循环，也不把 SDK 重试提高为整个 run 的重试。若后续要加模型级 retry middleware，须明确与现有 SDK 重试的乘法关系、总预算、取消优先级，并保持终局失败抛异常。

实施状态（2026-10-01）：第 1 项中的明确 `error`、无效工具调用、混合有效/无效调用和矛盾的 `tool_use` 已按 [Spec 020](../specs/020-model-response-rejection-before-tool-routing.md) 在同步路径实现并通过确定性测试。以上本机探针记录的是修复前行为；`unknown`、普通文本截断、用户取消与真实供应商失败样本仍待处理。
