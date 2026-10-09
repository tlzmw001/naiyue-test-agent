# Bash 运行中进度与活动取消：对照和本机探针

状态：2026-10-02，下一工具切片的设计调研；**没有改产品运行链路**。参照 Pi 本机提交 `a69bef789`、AgentX 本机提交 `3fcfa0e`；本机 LangChain 1.4.2、LangGraph 1.2.12。已完成 Bash 全文归档见 [Spec 045](../specs/045-workspace-bash-output-artifacts.md)。

## 当前项目事实

- `application/cli.py` 用输入线程/工作线程保持交互；工作线程调用同步 `app.run → agent.invoke`。`bash.py` 用两个线程读 stdout/stderr，只有命令结束才把尾部/归档路径回填模型。运行中的进度尚无从工具到 CLI 的事件通道。
- `:stop` 调用 `SessionService.request_stop_after_turn`，置 `stop_requested`；runtime 的 `before_model` 在安全边界跳 `end`。它让当前模型回复或已发工具批次先完成，**不会中断正在运行的 Bash，也不会取消模型网络请求**。Bash 超时会杀进程组并要求人工核对，这是失败处理，不是用户主动取消。

## 参考项目实际调用链

- Pi `packages/coding-agent/src/core/tools/bash.ts` 的 `createLocalBashOperations.exec` 把 stdout/stderr 数据交给 `onData`；工具层 `handleData → OutputAccumulator.append → scheduleOutputUpdate → onUpdate`，更新按约 100 ms 节流。`packages/agent/src/agent-loop.ts` 的 `executePreparedToolCall` 把工具 `onUpdate` 变成带 `toolCallId` 的 `tool_execution_update` 事件；`agent-session.ts` 再转交扩展/界面。这条链显示进度是**运行时事件**，不是最终 ToolMessage 的增量片段。
- Pi `Agent.abort() → activeRun.abortController.abort()` 把 `AbortSignal` 传给工具；本地 Bash 执行器收到 abort 后杀子进程树。`AgentSession.abort()` 再等待运行空闲。与用户要求当前 turn 完成后停的软停止不同；工具副作用仍可能已经发生。不能把 Pi 的 `onUpdate` 当作其取消机制。
- AgentX `ChatSessionManager.interruptSession` 在所查提交中设置会话中断标志、移出活动表、发送 SSE `interrupt` 并完成连接。本机搜索只找到 `isSessionInterrupted` 的定义，未找到模型/工具消费标志的路径；因此它可作传输通知参考，**不能据此声称模型请求和命令被终止**。其 `WebTerminalService` 是 Docker 交互终端，不等同于 Agent Bash 工具进度。

## LangChain 当前版本的实际探针

官方 [LangChain streaming 文档](https://docs.langchain.com/oss/python/langchain/streaming)区分 `updates`（节点步骤）、`messages`（模型 token）和 `custom`（工具等自定义进度）。本机对同步 `create_agent` 用 `agent.stream(..., stream_mode=['custom','values'], version='v2')` 跑假模型与 `ToolRuntime.stream_writer`：观察到 `values(initial) → values(model tool call) → custom(probe_chunk) → values(tool result) → values(final)`，退出 0。这证明同步 CLI 可先展示工具进度，不要求立刻把所有工具改成 async；没有证明真实模型 token 流或 Web 传输。

审批探针用 `HumanInTheLoopMiddleware`、内存 checkpointer 和 `stream_mode=['custom','values','updates']`：第一次流得到 `updates({'__interrupt__': ...})`，checkpoint 的 `next` 指向审批节点；`Command(resume=approve)` 之后得到工具 `custom` 进度与最终 `values`。所以从 `invoke` 切成 `stream` 时，应用必须明确重建现有 `result['__interrupt__']`、最终消息/停止状态和审批请求；不能只取最后一个 `values` 就宣称兼容。

线程探针更关键：在工具自身线程直接 `runtime.stream_writer` 成功；从工具派生的普通读取线程直接调用时抛 `RuntimeError: Called get_config outside of a runnable context`，没有收到事件。复制 runnable context 后的独立探针能发事件，但把 LangGraph 的 contextvars 约束扩散到每个 Bash reader 线程，须考虑未来运行方式。推荐让 reader 线程只传递**有界输出片段**给工具执行线程，由后者在自身上下文发 `custom` 事件，先做可测的同步进度链路。

## 原候选切片边界（已被后续排期决定替代）

1. **下一片：只做运行中 Bash 进度**。保持审批和 `:stop` 语义。应用入口使用当前版本已验证的同步 `stream(..., version='v2')` 消费 `custom` 进度及 `updates/values`；对外仍返回与现有 `run/resume` 等价的最终结果。Bash reader 将有界数据交给工具线程，由工具线程发带 `run_id/tool_call_id/stream` 的进度事件；CLI 仅展示已获进度，最终 ToolMessage 与工作区归档仍为权威结果。验证无输出、双流、大输出、审批中断/恢复、同轮多工具、Steering/Follow-up、软停止、失败/超时及事件不进日志；不在本片增加模型 token 流和 Web。
2. **再下一片：活动取消**。保留 `:stop` 作为软停止，另设取消命令/接口。需分别验证模型请求、Bash 进程组、其他工具、HITL 审批和同轮批次；取消请求已登记与子进程实际退出分开记录，已开始副作用按未知结果人工核对。同步 `stream` 或取消外层 Python 任务均不足以证明底层已停止；若要保证模型请求取消，需要验证 async/供应商 SDK 及各工具的取消传播后再定实现。优先保持上片事件契约稳定，而不是先宣称端到端取消。

以上是调研时提出的候选方案，没有产品测试红灯、生产代码改动或真实模型/命令活动取消证据。

## 用户确认的排期决定（2026-10-02）

暂不增加同步进度展示或运行中主动取消，保持当前 `agent.invoke`、CLI 和 turn 后 `:stop` 行为。等进入异步流式阶段，再把运行中进度与主动取消放在一起设计、实施和验证。**流式输出并不是取消的技术前提**；这是项目切片与验证范围的取舍。届时仍须分别证明取消信号能传到模型请求、SDK 重试等待和正在执行的工具，并区分请求已登记、执行实际终止及副作用结果未知。上面的同步流式探针保留为研究证据，不代表当前已接线，也不再作为紧接着要实施的切片。
