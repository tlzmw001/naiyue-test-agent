# Spec 061：事件、消息与运行生命周期设计

状态：2026-10-07，设计草案；开发顺序已确认，具体契约待逐段讨论。没有产品实现、TDD 或生产能力声明。

## 1. 目标与阶段边界

用户确认顺序：事件/消息完善 → 最小持久会话 → 工具与上下文强化 → 本地单用户 CLI 产品验收 → 会话扩展、异步运行与界面。完整异步迁移后置；当前设计检查异步接入边界，存储定稿前做小型兼容性验证。

本阶段建立上层可消费的运行事实，明确消息生命周期、事件与控制请求的区别、事件顺序及终局时点。复用 LangChain 消息、create_agent、middleware 与 LangGraph checkpoint。第一实现片只做统一事件基础和应用运行生命周期，再分别迁移模型/工具、消息/turn、控制/审批与压缩观测。各实现片另写可执行 Spec 和失败测试，不能一次整体重构。

本阶段不实现数据库、历史分支、Web、活动取消、token/工具输出增量，也不新增外部消息队列或依靠事件回放重建 Agent 的运行时。没有增量能力时不伪造 message_update/tool_update。

## 2. 当前源码事实

- application/cli.py：主线程接收输入，Thread 执行同步 app.run；返回后按当前 HumanMessage.id 提取新增 AIMessage 文本。
- application/__init__.py：SessionService 保存工作区、选模、忙碌、待审批和消息队列；run 在 runtime 返回后才整理审批结果，finally 清理状态。
- agent_runtime/__init__.py：_invoke_agent_run 调用 agent.invoke 并记录 run 终局；该日志早于应用 finally，不代表应用已清理或磁盘已保存。模型/工具 wrapper 记录独立 logging.extra 字典，无统一消费接口。
- 工具 wrapper 返回 ToolMessage(status=error) 可允许后续模型调用；原异常可能导致整次失败。截断工具调用的短路错误也记录 tool_failed，因此旧日志不能直接证明执行器已经执行。
- HITL 中断、Command 恢复、消息 reducer 和 checkpoint 由框架负责。after_agent 可因待注入消息跳回 model，不能直接作为自然终局的唯一事件来源。
- 摘要/溢出恢复存在独立模型请求，不能把所有底层模型调用都算作新的用户轮次；SDK 内部重试也不等于新的 Agent turn。

旧日志和直接 runtime 入口继续兼容。新应用事件的语义不能靠重命名旧日志获得。

## 3. 参考项目与框架证据

Pi a69bef789：packages/agent/src/types.ts 的 AgentEvent 包含 agent/turn/message/tool 生命周期；agent-loop.ts 在实际循环中生产事件；agent.ts 的 processEvents 更新状态后 await listeners。coding-agent 的 AgentSession 在 message_end 交给 SessionManager 保存普通消息，SessionManager 存在首次写入延迟。message_end 不是无条件磁盘提交证明。

AgentX 3fcfa0e：AbstractMessageHandler 的完成回调保存模型消息并发送结束通知；工具回调保存工具展示消息并调用完成 hook；TracingMessageHandler 另行收集轨迹。该消息保存模式不是 Python LangGraph checkpoint，不把 SSE 或回调直接说成全链路异步。

LangChain 官方 middleware 提供节点与模型/工具 wrapper；LangGraph 提供状态、checkpoint 与流式更新，优先复用。当前本机 langchain 1.4.2 / langgraph 1.2.12 的 stream_events v3 源码仍标 experimental；候选接口经过独立探针后再选择，领域事件不绑定该线协议。

参考：[middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom)、[streaming](https://docs.langchain.com/oss/python/langchain/streaming)、[persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。

## 4. 三类对象与权威状态

| 对象 | 含义 | 谁拥有/消费 |
| --- | --- | --- |
| 模型消息 | HumanMessage、AIMessage、ToolMessage 等实际内容与调用关系 | LangGraph 消息状态；模型适配器转换为供应商请求 |
| 事件 | 已发生的运行/消息/工具事实，以及阶段进度 | runtime/application 产生；CLI、日志、后续评测消费 |
| 控制请求 | steering、follow-up、停止、审批决定 | 应用验证并接受；runtime 在现有安全边界执行 |

接受控制请求不等于已经注入消息或停止。事件可以携带消息快照或引用，但事件不会自动回填模型消息。工具错误必须继续通过同 ID ToolMessage 回流；Python 终局错误继续传播，事件不替代返回结果或原异常。

权威状态：应用会话元数据 + 框架图状态/checkpoint。事件是观测接口；普通日志、UI 或评测 observer 不作为唯一状态持久化路径。首次同步事件基础不额外保存第二套对话历史。

## 5. 消息的三个时点

1. 内容结束/结果形成：响应或工具结果已经完整，但未必通过外层门禁，也未必写入图状态。
2. 图状态可见：框架已应用消息更新，在状态观察点可见；与磁盘保存分开。
3. 持久化确认：指定 saver 的持久策略确认相应 checkpoint 可恢复。

拟定义 message_completed 表示完整消息已经通过对应结果检查、可作为本次执行输出；其 payload 不宣称 persisted。模型返回后仍被响应门禁拒绝时不得发布成功消息。graph state 观测、恢复中去重和持久化确认要通过后续探针找到可靠时点后接线。

首次事件基础不实现 message_completed；消息切片必须先验证外层 middleware、Command 替换、结构化输出、HITL 拒绝和摘要恢复，避免在过早的 wrapper 层报告成功。输入入队只报 accepted，直到交付/状态观察时才记录相应消息事实。

增量内容不是完整消息；未来取消前显示了部分文本不等于完整 AIMessage 已完成或已经保存。

## 6. 运行与 turn 的建议契约

一次应用 run 对应一个 run_id；审批恢复沿用当前行为分配新 run_id。session_id 不变。暂不添加持久分支 ID。

新接口使用 run_started 和 run_finished；run_finished.status 为 completed / stopped / interrupted / failed。错误分类、停止类型、manual_review_required 作为受控详情；不要把副作用未知报告为已回滚。活动取消接入前不生产 cancelled 状态。

新应用终局在审批结果或失败状态已经确定、finally 清理已完成后报告，且每个已开始 run 正好一个终局。启动前被 busy/参数校验拒绝不产生 run_started。清理失败不发 completed，不因终局报告失败而覆盖原运行异常。

上述唯一终局约束适用于进程存活且事件发布基础正常的路径；进程被强制结束、发布器自身不可用时不能保证消费者收到终局，后续持久会话须独立处理悬挂运行。observer 失败与发布器/事件构造缺陷分开，不能为满足终局计数而伪造成功。

图层返回是内部事实，旧 run_completed 等日志仍沿用旧语义。新终局和旧日志必须通过文档/来源区分，不向新消费者重复投递两个终局。

turn 的建议定义参照 Pi：一次助手输出以及对应客户端工具批次的处理。steering/follow-up 可使同一 run 发生多 turn。摘要请求、SDK 重试不新增普通 turn。turn 事件独立切片验证跨审批、部分工具结果和失败边界，首片不凭模型请求数量伪造 turn_end。

## 7. 事件基础与字段建议

模块建议位置 agent_runtime/events.py：仅容纳事件值对象、生产/消费的最小协议及关联约束；应用传入每 run 的事件发布器。下层不得 import application/CLI/DB，也不得根据界面类型做分支。日志适配和 UI 展示由应用组合。

公共 envelope 建议字段：event_type、session_id、run_id、sequence、timestamp、data。模型/工具关联 ID 按事件内容增加，不给无此含义的事件伪造字段。每 run 的 sequence 从 1 递增，由发布点统一分配；它表示项目观测顺序，不表示并发工具严格执行顺序。事件身份可由 run_id + sequence 表达，暂不另加 UUID。

thread_id/checkpoint_id 先保持内部恢复字段，不进入通用日志；会话持久化若需调试接口，另定可见范围。message_id 与 tool_call_id 是不同标识，不能混用。

受信任本地 CLI 的内容事件可携带完整消息的独立快照；日志只映射白名单元数据，不写消息正文、工具参数/结果、原始异常、模型凭证配置。事件内容也不应发送给未经授权的外部 observer。序列化范围和快照方法在消息切片明确，frozen dataclass 本身不能保证嵌套内容不可变。

跨会话发布器隔离；同 run 并发生产时安全分配序号。观测事件不得修改框架原消息或重新调用工具。

## 8. 工具、审批与控制事件

工具阶段必须区分：已请求、允许调度/进入执行链、执行器实际进入、结果返回、框架消息可见。首次工具迁移保持现有执行边界并明确命名；不能把审批请求或短路拒绝描述为已执行。

工具结果可区分 success / error_result / not_executed / exception / outcome_unknown，具体类型在工具事件切片固定。error_result/not_executed 可经 ToolMessage 回流并继续；exception 沿已有策略处理，outcome_unknown 保留人工核对阻断。unknown 原因用当前安全诊断记录。

审批：用户批准只是应用接受决定；恢复后工具仍按一次性 grant 重验。run_finished(interrupted) 不代表 turn 已执行完成。停止：stop_requested 与 run_finished(stopped) 分开；保持当前 turn 后停止语义。steering/follow-up 的 accepted、交付与丢弃分开；失败清理不伪装成消息已经完成。

上下文事件继续记录预算、摘要请求目的和恢复阶段；compaction_completed 不能直接推导对应 replacement 已持久保存。

## 9. 消费失败与异步边界

首片只有进程内同步发布与轻量收集/日志消费者，无网络 observer。消费者不得调用模型、工具或回入应用运行，也不得承担授权决策。

可选 observer 普通异常：记录安全类型，隔离当前 run 内故障 observer，其他消费者和运行继续；不能吞掉业务或 saver 异常。关键状态 IO 仍在其正常调用路径失败，不放进可忽略 observer。进程/框架控制异常不按普通观测失败吞掉。过慢 observer 的硬超时/异步背压不由同步 callback 虚假保证。

同步发布可能阻塞调用方，首片只能接已知轻量消费者。对慢/网络消费者，接入前另做有界异步传输与背压 Spec；必须明确关闭、容量耗尽、异常和不能丢的终局事件，不把无限列表当成长期缓存。不在当前阶段增加异步事件平台。

未来异步路径复用事件语义；改变等待/传输实现。异步 probe 独立于产品入口验证 hook、工具与取消，不以 asyncio.to_thread 包住整轮运行证明原生取消。状态 checkpoint 的 durability 与同步/异步入口分别决策。

## 10. 一个成功场景的拟定时序

下表只表示待实现契约；不是当前日志轨迹。选择用户请求 read，模型随后回答。

| 顺序 | 事实 | 消息/状态 |
| --- | --- | --- |
| 1 | 应用接受运行，run_started | busy 开启，已建立 run_id |
| 2 | 用户输入交给图 | HumanMessage 可在对应状态观察点确认 |
| 3 | 模型请求/响应通过检查 | AIMessage 包含 read 的 tool_call_id |
| 4 | 批次完成审批与调度，read 执行链返回 | 形成同 ID ToolMessage |
| 5 | 框架合并工具结果并再次请求模型 | 模型看到 ToolMessage |
| 6 | 最终 AI 响应通过检查 | 图状态包含完整回答 |
| 7 | 图返回，应用整理结果并清理 | busy=False，队列清理 |
| 8 | run_finished(completed) | 上层拿到结果；不额外宣称磁盘持久化 |

错误工具结果仍可能走完第 5–8 步；未处理异常走应用 failed 清理与终局；HITL 走 pending 状态安装、清理与 interrupted 终局；stop 是安全边界结束后 stopped。具体事件名/消息发出时点在相应接线 Spec 和探针确定。

## 11. 分片实施与验证门槛

1. 统一事件基础 + 应用 run 生命周期：定义值对象与发布器、受控 observer、完整终局和旧日志兼容。先验证成功/异常/停止/审批/启动拒绝/双会话关联、finally 先于终局、消费者故障及无敏感内容日志。
2. 模型与工具事件：复用 wrapper，区分请求目的、工具短路/错误结果/异常；验证混合批次与并发配对、SDK 重试不可观察时不伪造尝试事件。
3. 消息与 turn：先用框架状态/流式探针确认可见点，验证多工具、Command 压缩替换、结构化响应、审批恢复、输入与已接受队列交付；再让 CLI 使用统一内容事件。
4. 运行控制、审批和上下文观测迁移：按已确认机制接线，消除旧日志和新接口的双终局/双消息，并做受控异步兼容性 probe。

第一片字段、订阅、终局接线与必要模块拆分见 [Spec 062](062-application-run-event-contract.md)，2026-10-08 已按用户授权实施并验证。其余模型/工具、消息/turn、控制/上下文订阅仍未实现，阶段设计不能作为这些能力的完成声明。

2026-10-08 后续：[Spec 063](063-model-tool-event-integration.md)已接入第二片模型/工具同步元数据事件，共用同一发布器和 run 序号，包含 HITL 拒绝与批次跳过。消息/turn 和控制/上下文订阅仍未完成；accepted/工具处理结束均不等于消息持久化。

本设计不修改现有公开 API 签名。新增事件入口、可选参数或装配接口的具体兼容方式由第一实现 Spec 明确；不能为了删除旧函数顺手做 breaking change。新增代码超过已有单文件上限时沿模块职责拆分。

每片按 TDD 观察预期红灯再实现；确定性事件/屏障测试不靠短 sleep。完整验证命令：.venv/bin/python -m pytest -q；.venv/bin/python -m compileall -q naiyue_test_agent tests scripts；.venv/bin/python -m pip check。真实模型验证使用现有授权和配置，单独记录，不用单次调用代替边界测试。

当前只读核对与文档结构检查，没有新增运行验证。旧 551 例不作为新事件实现证据。设计本身的验收是概念、来源、时点、责任和分片可追溯。

2026-10-08 实施进度更新：Spec062–067已分别接run/模型工具/完整消息/turn/控制审批/上下文；[Spec068](068-cli-event-display.md)已完成CLI统一消费，最终749例与有界真实两回答/纯工具/Steering/唯一终局通过。上文草案中各阶段“尚未实现”描述属于当时快照，当前事实以各片Spec和最新handoff为准。CLI不从旧诊断日志重复显示，审批详情仍用原应用返回；最小持久会话、异步兼容probe/正式迁移、活动取消和界面另验收。

## 12. 异常传播与终局事件补充（2026-10-08）

用户要求特别核对参考项目异常设计，以下为建议契约，尚未改变当前产品接口。

Pi a69bef789 的 api/openai-responses.ts 在 catch 中写 stopReason=error/aborted 并结束响应流；agent-loop.ts 消费此原因后报告 turn_end/agent_end 并返回。agent.ts 的 runWithLifecycle 捕获 executor 异常，由 handleRunFailure 构造空内容的 assistant 错误消息，再报告 message/turn/agent 结束，finally 清理。错误标记不等于成功回答；handleRunFailure 自身或 listener 再失败仍可能向外传播，不能宣称 Pi 吞掉所有异常。工具 execute 的 catch 通常生成 isError=true 的结果，这与模型终局是两条路径。

AgentX 3fcfa0e 的 processSyncChat catch 和流式 onError 在应用处理处发送结束错误并报告失败/追踪，属于其调用接口选择。不能照搬原始异常文字进入本项目事件或日志。

建议保持现有 Python 公共接口：业务/运行关键异常继续原样传播；应用先确定失败状态、执行清理，报告唯一 run_finished(failed)，再使调用方收到原异常。事件是通知，异常是调用的失败信号，二者不驱动两次恢复。CLI/未来 Web 在入口捕获并展示；迁移事件展示时只由一个展示路径输出错误，异常仍用于结束任务和保留诊断。当前不加另一套 Result/Outcome 返回接口，也不构造用于替代基础设施异常的 AIMessage。

| 情况 | 运行行为 | 事件语义 |
| --- | --- | --- |
| 已确认可恢复的工具参数/读取/权限等错误 | 同 ID error ToolMessage，允许模型继续 | 工具错误结果或未执行；不直接产生 failed 终局 |
| 已被当前摘要回退/明确溢出恢复处理的异常 | 沿既有规则继续；失败后才上抛 | 记录对应尝试/恢复事实；不提前结束整个 run |
| SDK 重试及项目限定恢复耗尽、模型响应拒绝、框架/关键状态 IO 异常 | 停止本次运行并保留原异常 | failed；stage/category/reason 使用已知来源，不凭异常名称伪造根因 |
| WriteOutcomeUnknown / BashOutcomeUnknown | manual_review_required 保持，禁止盲目重放副作用 | failed + 安全的副作用未知详情 |
| HITL 中断 | 交回框架暂停/恢复机制 | interrupted；不得转成 ToolMessage 或普通 failed |
| 当前 turn 后停止 | 已发出的模型/工具工作完成后按边界结束 | stopped，stop_mode=after_turn；无活动取消声明 |
| 可选 observer 的普通异常 | 安全记录并隔离 observer；不覆盖业务结果 | 不因纯展示/观测失败改变业务终局 |

failed 详情建议只含已确认的 category/reason、观察阶段、安全 error_type 和 manual_review_required；原异常对象、traceback、凭证及原始错误正文不进入普通事件，调用链仍保留原异常用于受控调试。未分类异常保留 unknown 安全诊断，不自动继续或重试。

存在主运行异常时，后续 observer/终局报告或清理异常不得覆盖主异常；次级问题须安全记录。只有清理失败时也不能发 completed。事件基础本身故障需要内部诊断，不能像可选 observer 一样静默隔离。恢复或取消引发的框架控制异常需按对应协议处理，禁止 catch BaseException 后统一转换失败结果。

未来原生异步任务取消遵循 CancelledError 清理后继续传播，不在本片实现；只有已验证取消链路才能报告 cancelled，并明确部分消息/副作用状态。CancelledError 的出现不单独证明由用户请求引发，取消来源需来自实际控制上下文。日志/内容消费者都不负责发起自动重试。

第一实现片增加验收：原异常对象身份保留；failed 事件和异常表示同一 run；观察者出错不掩盖原异常；失败时无 completed；error ToolMessage 后可正常完成；HITL 不被误归类；副作用未知仍阻断；入口不重复展示。新终局路径与现有重试策略分别验证。
