# 工具批次调度与分层对照（2026-10-02）

状态：保留 2026-10-02 开发前的源码调研与设计建议；本项目后续已按 [Spec 037](specs/037-conditional-tool-batch-scheduling.md)实现同步批次调度，并按 [Spec 046](specs/046-tool-approval-registration-boundary.md)整理审批注册边界。以下带“当时”的项目描述是历史快照，不代表当前代码。参考 Pi 为本机 `a69bef789`，AgentX 为本机 `3fcfa0e`，Python LangChain 为本项目安装的 `1.4.2`。

## Pi 的实际三段调用链

1. `packages/coding-agent/src/core/tools/` 定义具体 `read/write/edit/grep/find/ls/bash`；`tool-definition-wrapper.ts:5-19` 把工具定义中的可选 `executionMode` 传给 `AgentTool`。核对这七个内置工具定义，当前没有显式设置 `executionMode`，因此按全局默认走并行批次；扩展工具可以声明 `sequential`。
2. `packages/agent/src/agent-loop.ts:411-425` 在每条模型消息的工具批次开始前检查 `config.toolExecution === 'sequential'` 或批次里任一工具 `executionMode === 'sequential'`。任一命中使**整个批次**走 `executeToolCallsSequential`，否则走 `executeToolCallsParallel`。
3. 串行批次在 `:433-487` 按原顺序对每项完成准备、执行、结果后处理、工具事件和消息生成，才进入下一项。并行批次在 `:489-554` 先用 `for/await` 逐项完成 `prepareToolCall`，然后对获准执行的项 `Promise.all`；每项的 `executePreparedToolCall` 与 `finalizeExecutedToolCall`（含 `afterToolCall`）都在其并发任务中。因此不能说并行模式的“调用后处理”全局串行；**工具结果消息的生成与发出**在 `Promise.all` 之后按原工具调用顺序进行。
4. `prepareToolCall` 在 `:600-668` 查工具、准备/校验参数、运行 `beforeToolCall` 并可阻断；`executePreparedToolCall` 在 `:670-711` 调用真实工具并捕获错误；`finalizeExecutedToolCall` 在 `:713-758` 给 `afterToolCall` 改写结果的机会；`:777-790` 构造带原调用 ID 的工具结果消息。`packages/coding-agent/src/core/agent-session.ts:484-530` 把扩展 `tool_call/tool_result` 接到这些前后钩子。单个 `tool_result` 的扩展处理器在 runner 中顺序遍历，不意味着不同并发工具的后处理全局串行。

Pi 的逻辑边界是：`pi-agent-core` 定义通用 AgentTool 契约及批次调度；`pi-coding-agent` 定义文件/命令工具、会话装配与扩展钩子。`toolExecution` 是全局开关，`executionMode` 是可选逐工具声明；不是每个内置工具都显式写了一个 mode。

## AgentX 与本项目框架

AgentX 的 `AbstractMessageHandler.java:469-488` 从 `BuiltInToolRegistry` 取得工具定义/执行器并交给 `AiServices`；`AbstractBuiltInToolProvider.java:39-117` 统一参数解析、执行错误和 executor 委托。这条已核对链路支持“具体工具与装配分开”的参考，但未发现与 Pi 同义的逐工具模式和整批次仲裁；不能据此推断 AgentX 也实现了 Pi 的调度规则。

当时本项目 `agent_runtime/__init__.py` 用 `create_agent` 装配工具、`wrap_tool_call` 和 HITL；同时按具体 `SessionReadInput/SessionLsInput/...` 识别工具并硬编码审批规则。具体文件工具在 `agent_runtime/tools/`，应用 CLI 选择它们。那时静态依赖没有“下层 import 上层”，但通用运行装配依赖具体工具类型，职责比 Pi 的 `pi-agent-core` 更混合。当前已由 Spec 046 改为读取工具审批描述，通用装配不再 import 这些具体工具。

安装版 LangChain `agents/factory.py:1942-1964` 对同一模型消息中的每个待执行工具分别发出 `Send('tools', [tool_call])`；`wrap_tool_call` 是**单次调用**钩子。框架没有读取我们自定义的 `executionMode` 并按整批次切换串/并行的现成入口。`HumanInTheLoopMiddleware.after_model` 在路由前逐个判断哪些调用要审批、一次中断并收集决定，但不负责批次串行。官方 middleware 与 HITL 文档分别说明这些 hook 时点与审批行为：<https://docs.langchain.com/oss/python/langchain/middleware/custom>、<https://docs.langchain.com/oss/python/langchain/human-in-the-loop>。

2026-10-02 补充：框架并非完全不能串行。公开 `RunnableConfig.max_concurrency` 是并行调用上限（<https://reference.langchain.com/python/langchain-core/runnables/config/RunnableConfig>）；本机 LangGraph 1.2.12 的 `BackgroundExecutor` 从该配置建立同步执行器。本机 `create_agent` 假模型及 HITL 中断/恢复探针表明 `max_concurrency=1` 使同轮工具按原顺序执行，详见 [Spec 036](specs/036-tool-batch-scheduling-feasibility.md)。但这个配置从 invoke 开始作用于整次图运行，不根据当前模型回复里有没有 sequential 工具动态切换；纯读批次也串行。另一个探针表明第一工具未处理异常仍可能让第二工具执行；独立的两个 invoke 即使各自上限为 1，仍可同时进入同一目标工具。官方 `create_agent` 参数中没有工具批次调度器或替换工具路由的参数（<https://reference.langchain.com/python/langchain/agents/factory/create_agent>）；官方将自定义 `ToolNode`/图工作流用于需要精细工具执行控制的情况（<https://reference.langchain.com/python/langgraph/agents>）。因此“无法满足 Pi 式逐批串/并行”只针对已核对的公开 `create_agent` 扩展面，不应扩大成“LangGraph 无法顺序执行工具”。

## 建议的实施顺序与未定点

`read` 改名是独立契约迁移，已按 Spec 035 实施。`write/edit` 接入前，先做一个**最小批次调度 Spec 与探针**：至少用合成工具验证全并行、单个串行触发整批次顺序、调用前阻断、审批恢复、结果 ID/顺序和跨会话同目标冲突。先确认在当前 `create_agent` 可用的 middleware/工具包装边界能否实现这些语义，不能只给 `BaseTool` 增加一个无人消费的属性，也不能仅用每调用一个锁就宣称实现了 Pi 的原顺序批次调度。

分层建议是让 `agent_runtime` 只消费通用工具注册信息和执行策略，具体文件工具及其文件权限判定作为独立基础能力模块；应用/测试策略层决定暴露哪些工具、审批交互与会话归属。下一 Spec 再选择最小迁移方式，避免这次调研时先造一个未经验证的通用调度器。写入内容预览、执行时重验、副作用失败语义及同目标跨会话冲突须与调度契约一起验收；源码调研本身不等于这些能力已实现。
