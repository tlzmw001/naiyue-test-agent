# Spec 036：工具批次调度可行性与写入前决策

状态：2026-10-02，完成源码核对与本机假模型探针；**尚未选定或实现项目批次调度策略**。用户明确否定“读写混合或多写批次整批拒绝”：模型一次请求多个工具是正常行为，不应把顺序执行的责任推回模型。下面保留原探针结果，并把该候选方案标为已否决。没有改变产品代码、工具权限或公开 API。

## 当前数据流

一条模型消息可携带多个 `tool_calls`。本项目模型边界先统一结束原因、拒绝明确失败或无效调用；`HumanInTheLoopMiddleware.after_model` 可在工具路由前发起审批。安装版 LangChain 1.4.2 的 `create_agent` 随后从未配对结果的调用生成多个独立 `Send('tools', [tool_call])`。每个任务各自进入 `ToolNode → wrap_tool_call → 具体工具 → ToolMessage`，再把结果合并到消息状态。`wrap_tool_call` 不接收整个批次。

本机同步假模型探针让两个工具分别等待对方进入执行体，两者均观察到对方，证明当前批次可重叠执行；最终 `ToolMessage` 顺序与原调用顺序一致，ID 分别配对。探针并未覆盖审批中断/恢复、异常、跨会话资源冲突或异步/流式路径。

另一段纯框架探针在 `after_model` 对双调用一次性追加两条同 ID 的错误 `ToolMessage`：两个工具体都未执行，下一模型调用能看到错误后继续。它只说明“执行前拒绝整批并回填”的 hook 路线在简单同步场景可行；本项目尚未接线，也未证明与 HITL、停止和 checkpoint 恢复组合时安全。

## Pi 对照与项目目标

Pi `a69bef789` 在 `agent-loop.ts:411-425` 先看全局 `toolExecution` 和批次中任一工具的 `executionMode='sequential'`，再选择整个批次串行或并行；并行批次的准备阶段逐项执行，实际执行和结果后处理可重叠，结果消息按模型调用顺序发出。**Pi 当前七种内置文件/命令工具没有显式设置 sequential**；“`write/edit` 必须串行”是我们拟定的安全策略，并非 Pi 内置工具的事实。

本项目需要区分两种要求：同轮工具顺序，以及不同会话对同一文件/工作区的冲突。整批顺序不自动解决跨会话冲突，资源锁也不自动保证整批原顺序。模型返回的多次工具调用、审批拒绝、执行异常和用户停止都必须有明确结果；不能只凭函数名或工具属性宣称具备调度能力。

## 候选方案与未定接口

**已否决的候选**：若同轮包含 `write/edit` 就整批拒绝，让模型一次只重发一个。虽然简单的 `after_model` 探针证明这个 hook 可以回填错误且不执行工具，但该策略不满足用户要求。探针只证明框架能力，不作为产品方案。

**框架可直接提供的保守执行方式**：`RunnableConfig.max_concurrency=1` 从 `agent.invoke(config=...)` 传入当前同步图执行器。在 LangChain 1.4.2 / LangGraph 1.2.12 的本机探针中，同一回复的两个工具按原顺序完成，模型仍可一次提交多个工具；带 HITL 的批次先整体中断，批准恢复后也按顺序执行。这是**整次 invoke 的并发上限**，不是按批次判断“纯只读并行、含串行工具才串行”的 Pi 策略；即使同一回复只有只读工具也会顺序执行。并发上限是公开配置项，但原顺序执行仅有本机版本/探针证据，尚需纳入版本锁定与回归测试。不同会话的两个 invoke 各有自己的上限，仍可同时访问同一目标。

因此，若首个写工具只要求“同轮多个调用可正常执行且本运行内不重叠”，可以先用 `max_concurrency=1` 作为范围最小的正确性基线，明确牺牲纯读并发，并在写入前另验跨会话资源冲突。若要求**现在**实现 Pi 式逐批切换，则本机 `create_agent` 没有公开的批次执行策略钩子，必须由本项目设计批次协调或自定义图的工具路由；这属于更大的运行层切片。不能把 `parallel_tool_calls=False` 当作替代：它让供应商限制模型的多调用能力，且不同模型支持不一致。

若要求与 Pi 相同的“含 sequential 工具时整批按原顺序**执行**”，现有 `create_agent` 没有批次调度开关。仅在每个 `wrap_tool_call` 上加锁会按抢锁顺序执行，无法保证模型调用顺序；审批拒绝可能根本不进入该 wrapper，使等待前序调用的后项卡住。自行接管 `after_model` 执行、维护待执行队列，或替换图的工具路由，都将明显扩大运行时职责，并可能重复 HITL/checkpoint/停止逻辑，须作为独立架构决策和较大 Spec。

`max_concurrency=1` 也不是“前一个工具异常则停止后续工具”的保证。本机探针中首个工具抛未处理 `RuntimeError`，第二个工具体仍开始执行，整次 invoke 最终抛错。这与单纯的互斥/顺序是两种契约；有副作用工具必须明确失败后是否继续、如何标记结果未知以及审批/停止后的剩余调用。

## 后续验收，尚未执行

选定策略后先写失败测试：多工具回复无需重发即可执行并按 ID 回填；若选逐批策略，双只读可重叠、读+写和写+写严格按原顺序；若选整运行串行，则明确接受双只读也串行。审批批准/拒绝/恢复后无死锁、重复执行或漏结果；工具参数错误、普通执行失败、结果后处理失败进入合适的 `ToolMessage`；跨会话同目标冲突不产生交错写或不明副作用；停止/模型失败不把未知副作用记为成功。之后才实现并做全量测试、构建和受控真实模型验证。

## 已运行的可行性验证

`.venv/bin/python -m scripts.probe_tool_batch` 退出 0：两个合成工具用 `threading.Event.wait(5)` 相互等待；`observations={'first_saw_second': True, 'second_saw_first': True}`，`tool_messages=[('call-1','first-result'),('call-2','second-result')]`。独立的 `after_model` 探针得到 `gate_executions=[]`、`gate_tool_messages=[('gate-1','error'),('gate-2','error')]`。结果限 Python LangChain 1.4.2 的简单同步装配；未证明未来版本或全部异常路径。可复跑脚本见 `scripts/probe_tool_batch.py`。

`.venv/bin/python -m scripts.probe_tool_serial_config` 退出 0：`serial_trace` 与 `hitl_resume_trace` 都是 `first:start → first:saw_second=False → first:end → second:start → second:end`；`failure_trace=['first:failed','second:started']`。另一个双 invoke 同目标屏障探针得到 `both_runs_entered_shared_tool=True`，说明每次 invoke 的并发上限不是跨会话锁。上述是本机同步实现证据，不是纯读并行+混合批次串行的实现，也未验证真实写文件副作用。
