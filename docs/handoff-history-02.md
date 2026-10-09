# Naiyue Test Agent：开发学习交接历史（二）

承接[历史（一）](handoff-history-01.md)，保存第 56–61 节。最新状态见[当前交接](handoff.md)。

## 56. Agent 运行层命名、包归类与职责收敛（2026-09-30）

- 用户确认理解 checkpoint，要求判断是否进入上一层、命名并归入模块，同时讨论循环插话、停止、上下文、事件和持久会话的取舍。沿既有四层命名选 `agent_runtime`：项目没有重写 `create_agent` 的标准模型—工具循环，故不用暗示自写循环的 `agent_core`。按 [Spec 012](specs/012-agent-runtime-package.md) 把原 `agent_runtime.py` 原样移入 `agent_runtime/__init__.py`，公开导入路径和函数签名均未改变，也没有添加占位模块。
- 基线 `.venv/bin/python -m pytest -q` 输出 `84 passed in 3.74s`；移动后全量输出 `84 passed in 3.63s`；`compileall` 退出码 0，`pip check` 输出 `No broken requirements found.`；公开导入探针确认模块实际加载自 `agent_runtime/__init__.py`。纯结构变更未制造 TDD 红灯或声称新功能完成；`.env`、依赖、业务代码和测试断言均未改。
- [运行架构草案](agent-runtime-architecture-draft.md)新增职责表：框架循环归 LangChain；项目运行层负责框架接线、可观察事件、消息安全注入边界、停止与上下文处理；应用层拥有会话归属、选模提交、内部 `thread_id` 映射和并发队列；checkpointer 只保存 Agent 状态。Pi `a69bef789` 的 steering/follow-up 是两层队列，工具结果回填后才发 `turn_end`；LangChain `after_model` 在工具前，不能直接照搬。AgentX `3fcfa0e` 的已查停止路径主要设置标记并结束 SSE，未找到该标记在执行路径被调用，不能据此证明模型/工具真的取消。
- 建议而非已确认的后续顺序：先最小运行事件/终局状态，再可信会话映射与单会话规则；正式副作用工具前验证停止。steering/follow-up、压缩与跨重启恢复分独立切片，不要求全部先于第一个 HTTP 测试闭环。当前仅有同步局部日志与内存历史验证，没有新增消息队列、取消、压缩或持久会话功能。
- 学习证据：用户明确表示理解 checkpoint；本轮运行层职责由助手整理并完成包归类，尚未收到用户对新职责划分的复述或独立实现证据。唯一下一步：与用户核对运行层职责和优先级；若认可，写最小运行事件 Spec 并按 TDD 实现，不一次性铺开所有控制能力。

## 57. 同步运行事件与终局状态最小切片（2026-09-30）

- 用户同意按运行层建议推进。按 [Spec 013](specs/013-synchronous-run-events.md)新增 `invoke_agent_run(agent, messages, *, session_id, thread_id, run_id)`；入口先校验三个由上层提供的非空标识，记结构化 `run_started`，调用原 `agent.invoke`，成功记 `run_completed` 并返回原结果，失败记 `run_failed` 并原样上抛。`RunContext.run_id` 可选；新入口调用时，现有模型选型/用量/失败日志附加 run ID，未使用新入口的旧调用字段保持不变。事件不含提示词、回答、内部线程 ID 或异常正文。
- 基线 `.venv/bin/python -m pytest -q` 为 `84 passed in 3.59s`；5 个新测试先因缺少 `invoke_agent_run` 输出 `5 failed in 0.60s`，实现后定向 `5 passed in 0.53s`。加强完整事件顺序断言后，最终全量 `.venv/bin/python -m pytest -q` 输出 `89 passed in 3.80s`；`compileall` 退出码 0，`pip check` 输出 `No broken requirements found.`；运行层 153 行、相关测试 475 行，均低于 500 行。
- L1/L2/L3：新函数和日志字段存在；确实通过同线程两次假模型运行以及合成模型失败产生预期事件；测试穿过项目装配、middleware 和真实 LangChain `create_agent`，断言原异常对象、日志脱敏和旧路径兼容。当前正式 `application` 尚未创建，折扣策略与诊断脚本仍直接 `invoke`，所以未完成所有入口的统一接线；没有持久轨迹、工具事件、异步/流式或取消状态，也没有真实模型运行证据。
- 代码由助手完成，用户尚未逐段阅读、复述或独立编写此切片；学习状态仅记功能由确定性测试验证，不能记为用户已独立实现或能应对面试追问。唯一下一步：先用一条成功/失败示例讲 `run_id` 如何贯穿入口、middleware、终局事件；之后设计最小应用会话服务，确保可信 `session_id`→内部 `thread_id` 和同会话运行规则，再让应用入口实际调用新运行入口。

## 58. 进程内应用会话服务最小切片（2026-09-30）

- 用户要求继续，按 [Spec 014](specs/014-in-memory-session-service.md) 建立 `application.SessionService`。服务持有同一 `ModelCatalog` 快照，生成外部 `session_id` 和独立内部 `thread_id`，校验并保存所选模型；`run()` 只通过既有 `invoke_agent_run` 传内部线程与新 `run_id`。`agent_runtime` 只调用注入的 `service.resolve_model` 回调，不静态导入应用层。
- 本切片规则：同一会话已有活动运行时，第二次 `run()` 抛 `SessionBusyError` 且不调用 Agent；其他会话能运行。忙碌期间可提交模型切换，下一次模型调用边界读取新选型，不影响已开始的请求。锁只保护会话表和状态，不跨 `agent.invoke` 持有；成功或失败都在 `finally` 清 busy。`session_id` 只适用于可信本地调用，不是 Web 身份凭证。
- 参考事实：本地 Pi `a69bef789` 的 `AgentSession.setModel` 修改会话选型并维护活跃运行状态；本地 AgentX `3fcfa0e` 的 `ConversationAppService.prepareEnvironmentWithModel` 在请求前按 user ID 取会话。当前 LangChain [短期记忆文档](https://docs.langchain.com/oss/python/langchain/short-term-memory)将 `checkpointer` 与 `thread_id` 用于线程历史；这些参照只说明边界，不证明本项目已有外部授权或持久会话。
- TDD：实施前全量 `.venv/bin/python -m pytest -q` 为 `89 passed in 3.92s`。新增 4 个应用测试后，定向 `.venv/bin/python -m pytest -q tests/test_application_session.py` 因模块不存在在收集阶段报 `ModuleNotFoundError`；最小实现后 `4 passed in 0.58s`。最终全量 `.venv/bin/python -m pytest -q` 为 `93 passed in 3.60s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` 退出码 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。应用实现 61 行、测试 169 行。
- L1/L2/L3：新包和方法真实存在；假模型经 `SessionService.run → invoke_agent_run → create_agent` 验证同会话历史延续、不同会话检查点隔离、两次运行间 A→B，以及工具结果回填后运行中 A→B；Event 控制的并发测试验证 busy 拦截、另一会话可运行、失败后可再运行。原始失败对象透传；内部线程 ID 与合成敏感文字不进入事件日志。下层目录搜索未发现静态导入 `application`。
- 已知边界：还没有 CLI/Web 组合入口、访问令牌/会话归属、多进程协调、跨重启恢复、持久选模记录或真实供应商跨模型历史兼容。现有诊断脚本和固定折扣实验仍直接 `invoke`，本切片只证明新服务路径已接线，不能说全项目入口已统一。
- 学习状态：本切片由助手写代码，机制在收尾时只给数据流概览，用户尚未逐行学习、复述或独立修改；不记录为能独立实现或应对面试追问。唯一下一步：沿 `create_session → run → resolve_model → invoke_agent_run` 用一组具体 ID 和两次输入逐段讲清状态与异常路径，检查理解后再讨论统一应用组合入口。

## 59. 会话服务代码课堂：创建会话与同步运行（2026-09-30）

- 用户指出尚未读代码，不能先回答锁和 `finally` 的设计问题。先按真实 [Lesson 5](lessons/lesson-5.md) 与 `tests/test_application_session.py` 讲 `create_session` 的输入、会话表形状及先校验后写入的异常路径。用户正确回答：未知模型在 `ModelCatalog.get` 抛错，后续不会增加会话记录。该局部判断记为讲解后理解，不等于独立实现。
- 继续读 `run` 的同会话 busy 状态、内部 thread ID、run ID、释放锁后调用运行层和 `finally` 清理。用户正确回答模型调用失败时“异常，false”，即原异常继续传给调用方、busy 清回 False；此为讲解后理解，尚非独立实现。本轮只读与记录课堂笔记，没有修改生产代码、配置或依赖，也不把之前的 `93 passed` 当作本轮新验证。
- 随后讲 `invoke_agent_run → agent.invoke` 的输入、`context`、`config` 和开始/终局事件，理解反馈待收集。唯一下一步：先确认用户能区分 `session_id`、`thread_id`、`run_id` 在这次调用中的用途；再追 middleware 如何从会话服务选 A/B。

## 60. 事件来源的只读探针（2026-09-30）

- 用户确认继续上一轮事件系统设计；本轮先做[事件来源探针](event-source-probe.md)，未修改生产代码、测试、`.env` 或依赖。用假模型和假工具走项目 `build_runtime_agent`：同步 `stream_events(version='v3')` 成功路径给出 `tool-started → tool-finished`，工具抛 `RuntimeError` 时给出 `tool-started → tool-error` 并继续抛原异常；本机有 `LangChainBetaWarning`，LangGraph 源码标注 v3 实验性。底层事件含原始输入/输出/错误字段，不能原样持久化。
- 另用一次不落盘的 `@wrap_tool_call` 假模型探针验证：`request.tool_call` 可取工具名及调用 ID，`request.runtime.context` 可取当前 `run_id`；成功记录 `started → completed`，异常记录 `started → failed` 且同类原异常上抛。此实验是下一同步切片的技术可行性证据，不是项目级事件实现或失败脱敏的全量验收。
- 已更新后续清单 C5；当前日志在默认 Python 日志配置下未形成可消费的完整事件通道。无新功能，故没有伪造 TDD 红灯。文档修改后 `.venv/bin/python -m pytest -q` 输出 `93 passed in 3.68s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` 退出码 0；`git diff --check` 退出码 0。学习状态：事件生产/消费分层已讨论，用户未独立编写或解释新的工具事件代码。
- 唯一下一步：针对现有同步路径写小型事件契约 Spec，先确定 `session_id`、`run_id`、工具调用 ID、脱敏字段及失败/错误 `ToolMessage` 语义，再按 TDD 实现一个工具事件切片；Web 实时流与持久化后置。

## 61. 同步运行路径中的工具边界事件与运行方式结论（2026-09-30）

- 用户确认：现有 `SessionService.run → invoke_agent_run → agent.invoke` 是阶段性同步验证入口，不是最终唯一入口；未来本地 Web 要为运行中反馈和停止另行验证流式/异步路径，继续复用 `create_agent` 的循环，现有同步入口可保留给测试/简单 CLI。Pi 本地 `a69bef789` 的异步 `Agent.prompt` 与事件订阅仅作职责参照，不把其运行方式或错误语义说成与当前项目相同。结论已写入[架构草案](agent-runtime-architecture-draft.md)和[总体计划](project-plan.md)。
- 按 [Spec 015](specs/015-synchronous-tool-events.md) 在 `agent_runtime` 接入同步 `wrap_tool_call`：工具进入时记 `tool_started`，handler 正常返回记 `tool_completed`，抛异常或返回 `ToolMessage(status='error')` 记 `tool_failed`；原返回值/异常继续交给框架。事件只含固定事件名、工具名、调用 ID 和可选 run ID，不记提示词、工具参数/输出或异常正文。`run_started/run_completed/run_failed` 另补应用层提供的 `session_id`，不含内部 `thread_id`。这仍是结构化 Python 日志，未接事件消费者、Web 流或持久审计。
- TDD：基线全量 `93 passed in 3.66s`；先新增 4 个真实 `create_agent` + 假模型/假工具测试，定向 `4 failed in 0.93s`，预期失败为没有工具事件。最小实现后旧的 2 个精确 run 日志断言因新增字段而失败，仅同步断言的新字段并保留原行为检查。定向运行层/应用测试 `29 passed in 0.63s`；补应用服务工具事件关联断言后，最终 `.venv/bin/python -m pytest -q` 为 `97 passed in 4.00s`，`compileall` 退出码 0，`pip check` 为 `No broken requirements found.`。项目文件当前整体 untracked，`git diff --check` 退出码 0 不能覆盖这些文件；未提交代码。无真实模型调用，未修改 `.env`、依赖或已有公开 API 签名。
- L1/L2/L3：生产 hook、装配和新测试均存在；测试穿过 `build_runtime_agent → create_agent → invoke_agent_run` 及 `SessionService.run`，成功事件顺序、原异常身份、错误 `ToolMessage` 后正常 run 完成、应用层 run 关联以及直接 invoke 无 run ID 的兼容均已验证。单文件最大 475 行。用户此前正确理解同步与异步入口不是框架能力限制；本次工具事件代码由助手编写，尚未逐行讲解、让用户独立修改或完成面试追问。下一课堂入口见 [Lesson 6](lessons/lesson-6.md)。
- 唯一下一步：先沿一条 `AIMessage.tool_calls → wrap_tool_call → handler → ToolMessage → 终局` 的具体值逐段讲解并检查理解；再独立设计事件消费者/实时 UI 路径，不能把当前日志直接称为完整事件系统。
