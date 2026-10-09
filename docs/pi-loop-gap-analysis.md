# Pi loop 对照：运行中消息与停止语义

状态：2026-09-30 设计调研，**不是实现 Spec**；2026-10-01 的同步 Steering、Follow-up 和 turn 后停止落地证据分别见 [Spec 022](specs/022-synchronous-steering.md)、[Spec 023](specs/023-synchronous-follow-up.md)、[Spec 024](specs/024-stop-after-current-turn.md)。最小 CLI 已按 [Spec 017](specs/017-local-cli-entry.md)完成；活动取消仍按独立 Spec 推进。参照版本：本机 Pi `a69bef789`、AgentX `3fcfa0e`；项目环境 LangChain `1.4.2`、LangGraph `1.2.12`。未来升级时重新核对源码。下文历史探针没有写入当时的产品代码或测试文件。

## 当前链路和职责边界

项目的 `LocalApplication.run → SessionService.run → invoke_agent_run → agent.invoke` 是同步调用。`create_agent` 已负责模型→工具→工具结果→下一次模型调用的标准循环；项目已有会话模型选择、内存 checkpoint、run/model/tool 日志。`SessionService` 拒绝同会话第二次活动 `run`。现有进程内 Steering、Follow-up 队列和 turn 后优雅停止接线；尚无活动运行取消接口。一次运行结束后再次 `invoke` 添加消息仍不是同一次 loop 内的 follow-up。

因此本项目的 `agent_runtime` 对标 Pi **loop 边界的适配与控制职责**，不对标“必须亲自重写 Pi 的 while 循环”。静态依赖仍是应用层注入回调/队列接口给运行层；运行层不导入应用层。队列所有权、可信会话与外部请求授权应由应用层决定，运行层负责在框架边界安全消费消息并记录结果。

## Pi 源码事实与本项目差距

| 机制 | Pi `a69bef789` 的调用链 | 当前项目和待定点 |
| --- | --- | --- |
| steering | `Agent.steer()` 写 steering queue；`runLoop` 在当前 assistant 回合的工具结果已加入上下文、`turn_end` 已发出后读取队列，在下一次模型响应前追加消息。若已无后续模型调用但收到 steering，内层 loop 可继续。 | [Spec 022](specs/022-synchronous-steering.md)已实现同步进程内队列与安全边界注入，并用假模型验证顺序、自然结束续跑、关窗和失败清理；真实模型与持久交付未验证。steering **不会抢断已经开始的模型输出或跳过已发出的工具调用**。 |
| follow-up | `Agent.followUp()` 写独立 queue；外层 loop 在无待执行工具与 steering、原本要结束时才读取，若有消息继续一个 turn。 | [Spec 023](specs/023-synchronous-follow-up.md)已在同步进程内应用用独立队列实现自然终局逐条交付与 Steering 优先；假模型验证同一 run，真实模型/持久交付未验证。 |
| 模型停止原因 | `pi-ai` 的 `AssistantMessage.stopReason` 统一为 `pending/stop/length/toolUse/error/aborted/deferred`，供应商适配器从原生原因映射；loop 对 `error/aborted` 终止，`length` 下的工具调用不执行。 | [Spec 018](specs/018-model-stop-reason-and-truncated-tool-calls.md) 与 [Spec 019](specs/019-claude-context-truncation-and-tool-call-binding.md) 已归一已知字段，并在同步工具边界拒绝明确截断的调用；[Spec 020](specs/020-model-response-rejection-before-tool-routing.md) 又在模型 middleware 返回前拒绝明确 `error` 和含 `invalid_tool_calls` 的整条消息，记录 `run_failed`。LangChain 实际路由仍看 `AIMessage.tool_calls`，不消费项目统一原因。`unknown`、普通文本截断和用户活动取消另待处理。 |
| 主动结束/取消 | Pi 的 `shouldStopAfterTurn` 在模型响应和工具结果正常完成后的 `turn_end` 之后结束 loop；`Agent.abort()` 触发活动 `AbortController`，signal 传给模型流与工具，属于另一条取消路径。 | [Spec 024](specs/024-stop-after-current-turn.md)已在同步统一应用实现当前 turn 后停止，假模型验证 `run_stopped` 与失败/自然完成分开；无活动任务取消、真实服务停止或已开始工具副作用恢复证据。 |

Pi 源码入口：[agent-loop.ts](https://github.com/earendil-works/pi/blob/a69bef789/packages/agent/src/agent-loop.ts)、[agent.ts](https://github.com/earendil-works/pi/blob/a69bef789/packages/agent/src/agent.ts)、[agent types.ts](https://github.com/earendil-works/pi/blob/a69bef789/packages/agent/src/types.ts)、[pi-ai types.ts](https://github.com/earendil-works/pi/blob/a69bef789/packages/ai/src/types.ts)。Pi 的本机 checkout 与网上 `main` 可能已有差异，本分析以该 commit 为准。

AgentX `3fcfa0e` 的 `ChatSessionManager.interruptSession` 将活动会话标为 interrupted、移出活动表、发送 SSE interrupt 并结束连接；本机搜索没有找到模型/工具执行路径消费该标志的调用点。因此它能作为**会话传输边界**参照，不能据此声称其模型请求或工具一定已取消。[AgentX 会话管理源码](https://github.com/lucky-aeon/AgentX/blob/3fcfa0e/AgentX/src/main/java/org/xhy/application/conversation/service/ChatSessionManager.java)。

## LangChain 现有能力与本机探针

官方[自定义 middleware 文档](https://docs.langchain.com/oss/python/langchain/middleware/custom)说明 `before_model`/`after_model`/`after_agent` 可更新消息状态，hook 可 `jump_to='model'` 或 `jump_to='end'`；[LangGraph interrupt 文档](https://docs.langchain.com/oss/python/langgraph/interrupts)中的 `interrupt()` 是暂停并等待批准/输入、之后用 `Command(resume=...)` 恢复，**不是**用户点“停止”时取消活动模型请求的同义词。[LangChain streaming 文档](https://docs.langchain.com/oss/python/langchain/streaming)提供模型/工具过程的流式事件。

在当前安装版本上，三个不落盘的确定性探针分别观察到：

1. 假模型请求工具，工具把合成 steering 消息入队；`before_model` 在工具 `ToolMessage` 已加入状态后返回 `HumanMessage`，最终消息顺序为 `human → ai(tool call) → tool → human(steering) → ai`。这证明一个**注入点**，未证明并发消息队列的提交/确认语义。
2. `@after_agent(can_jump_to=['model'])` 在假模型首个最终回答后追加一条合成 follow-up 并跳回模型；结果为 `initial → first → queued follow-up → second`，该 hook 被调用两次。说明本机版本存在同次 `invoke` 继续的可能，但须验证 hook 重入、递归上限、同一 run 事件和队列竞争，不能直接宣称与 Pi 外层 loop 等价。
3. `agent.ainvoke` 的任务在一个异步假工具等待期间被 `task.cancel()`；任务返回 `CancelledError`，工具也收到取消。仅证明这个异步假工具路径；同步工具、真实供应商网络请求、checkpoint 完整性和不可逆副作用均未验证。

本机 `create_agent` 的工具路由依据最近 `AIMessage.tool_calls` 与对应 `ToolMessage`，未在该路由函数检查模型结束元数据。因此需要单独验证截断时项目是否应拦截可能不完整的工具调用；不能因为 Pi 已保护这条路径就认定框架也做了同样保护。项目 middleware 收到的是 LangChain 适配后的 `AIMessage`，不是原始 HTTP JSON。当前安装版本的 OpenAI Chat Completions/Anthropic 适配包分别可在 `response_metadata` 中报告 `finish_reason`/`stop_reason`；OpenAI Responses API 可用 `status/incomplete_details`，并非统一字段或所有响应必有。已有网关正常响应探针只观察到 `finish_reason=stop`，尚无真实截断样本。

## 实现前先固定三个不同概念

| 概念 | 例子 | 作用 |
| --- | --- | --- |
| 模型响应原因 | 正常结束、请求工具、输出截断、供应商错误；原始值按供应商保留 | 判断这一条 assistant 响应是否完整、可否执行工具；不等于整个 Agent 运行已结束。 |
| 运行终局 | 正常完成、按策略在安全边界停止、用户取消、失败 | 给入口和轨迹一个不含敏感正文的结果；由运行控制层记录，不能伪装成模型自然回答。 |
| 停止请求 | 用户现在取消、请求当前 turn 结束后停 | 决定何时阻止下一次模型/工具调度；已开始的外部副作用可能处于未知状态。 |

## 建议的小切片顺序与验收门槛

1. **已完成最小 CLI**：复用 `LocalApplication`，以假模型验证多轮与切换，以已有私有配置验证两轮真实合成文本。CLI 的同步链路仍只承担基础诊断；不因 CLI 存在就宣称支持运行中控制。
2. **运行语义与异步控制基础**：固定一次 run/turn 的边界，记录供应商原始模型结束原因与项目运行终局；用假模型覆盖正常回答、工具调用、截断、异常。建立可观察的异步/流式活动运行句柄，验证事件关联及同会话 busy；不预设真实请求已经可取消。
3. **已完成同步 steering 单切片**：应用层持有每会话队列，运行层在工具结果回填后、下一次模型调用前消费；已用受控假模型和线程 Event 验证无工具回答正准备结束时的竞争、顺序与失败时剩余消息处理。取消后的剩余消息语义和真实模型路径仍待各自切片。
4. **已完成同步 Follow-up 单切片**：自然终局逐条消费，Steering 优先；同一 run、递归上限和关窗均有假模型验证，见 [Spec 023](specs/023-synchronous-follow-up.md)。
5. **已完成同步 turn 后停止单切片；活动取消待做**：当前模型回答或已发出工具批次结束后不再发下一模型请求，独立 `run_stopped` 终局有假模型验证，见 [Spec 024](specs/024-stop-after-current-turn.md)。活动模型/异步工具取消、真实供应商请求及外部副作用未知状态另做；不能只靠关闭 CLI/Web 输出通道。

上述顺序是设计建议，不是已批准的接口或实现承诺。每个功能切片先写对应 Spec、失败测试，再接现有 `create_agent`；若探针显示 hook 无法可靠满足语义，先报告证据与取舍，不立即手写完整 Agent loop。上下文压缩、重试预算、长期会话恢复和工具权限随后分别排期；这些也是 Pi loop 周边可参考的能力，但当前不与消息注入/停止混成一个实现单元。

2026-10-01 复核补充：模型 `error` 元数据、无效工具调用、SDK 请求重试和用户取消的实际接线与受控探针，详见[运行终止、错误、重试与用户停止调研](research/loop-termination-retry-cancel-2026-10-01.md)。本文件前面保留 2026-09-30 的探针历史；其“当前项目未规范化停止原因”表述已在上表按后来完成的 Spec 018/019 更新。

2026-10-01 实施补充：[Spec 020](specs/020-model-response-rejection-before-tool-routing.md) 已修复调研探针中“明确错误/无效调用仍可能完成”的同步路径，新增判定表见 [Lesson 11](lessons/lesson-11.md)。上面的旧探针输出是修复前证据，不表示目前行为。
