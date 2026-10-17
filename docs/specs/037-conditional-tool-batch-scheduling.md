# Spec 037：在 `create_agent` 内实现按批次的工具执行模式

版本边界：2026-10-16的[090A](090-tool-wait-stop-and-author-contract.md)已移除本文的正常排队60秒期限及NAIYUE_TOOL_BATCH_WAIT_SECONDS读取，并补齐同步放行前停止检查。下文保留初版实现历史，不再作为等待期限的当前合同；批次串并行、机制故障及审批仍保留。

状态：2026-10-02，同步运行层切片已按 TDD 实现并通过本地确定性测试；尚未接入 `write/edit` 真实文件工具。用户已明确否定把读写混合或多写批次整批拒绝；模型的一次多工具请求必须能正常执行。本片只针对当前同步运行路径，沿用 Python + LangChain `create_agent`，不手写完整 StateGraph。

## 目标与当前调用链

目标：每条 `AIMessage.tool_calls` 作为一个批次；若其中没有声明串行的工具，框架保持现有并行；只要有一个工具声明串行，**整批按模型给出的调用顺序**执行。每项仍走当前 `ToolNode → wrap_tool_call → 具体工具 → ToolMessage`，保留调用 ID、参数校验、工作区校验、HITL、事件和 checkpoint。首个拟声明串行的工具是将来的 `write/edit`；它们目前尚未实现。是否让 `bash` 串行在其工具 Spec 再定，不假称 Pi 内置工具已有此标记。

本机 LangChain 1.4.2 的 `create_agent` 在模型回复后为每个未完成调用创建独立 `Send('tools', [call])`；框架没有公开的逐工具执行模式或替换批次路由参数。`max_concurrency=1` 可使**整次 invoke** 的多个工具顺序执行，也能与 HITL 批准恢复工作，但纯只读批次同样串行；前一项未处理异常时后一项仍可能执行。见 [Spec 036](036-tool-batch-scheduling-feasibility.md)及两个可复跑探针。因此它可作为保守后备，却不等于本草案的逐批切换。

## 本片接线契约

1. 具体工具使用现有 `BaseTool.metadata['naiyue_execution_mode']='sequential'` 声明；缺失视为 `parallel`，显式非法值在 Agent 装配时拒绝。运行层建立名称到模式的只读映射，只消费模式，不 import 具体 `write/edit` 类。当前四个只读工具缺省并行，未知工具名由原 ToolNode 返回工具错误；同批有已知 sequential 调用仍整批串行。
2. `after_model` 在可信 `RunContext` 中按需创建**运行范围内**的 `BatchCoordinator`，不写入模型消息或 checkpoint，也不跨会话共享。它保存当前批次的调用 ID 顺序、模式、互斥/等待状态和致命失败标记；最终由这次 invoke 的生命周期释放。审批恢复是新的 invoke，会重新建立协调对象。工具调用 ID 必须是非空且本批次唯一，否则在路由前拒绝执行整个异常模型响应，而不执行部分副作用。
3. 批次规划 middleware 使用 `after_model`，放在 HITL middleware 之前注册，使当前版本的执行顺序为 **HITL 先处理并在必要时中断/恢复 → 规划器读取已审批的 AI/ToolMessage 状态**。规划器找最近 AI 消息、排除已有同 ID `ToolMessage` 的调用，按工具模式决定本批次；拒绝的调用不能占用等待序号。本机纯框架探针已验证：若把规划器注册在 HITL 后，它会在审批前建立计划，首项被拒绝时后项会因等待缺席前项而超时；反向注册并在规划时排除已完成调用，简单拒绝场景可完成。正式实现须用项目审批链路回归，不能仅依赖探针。
4. 同一协调对象由 `wrap_tool_call` 读取。并行批次直接调用下游 `handler(request)`；串行批次按计划 ID 等待轮次，轮到自己时才进入**完整**下游链（现有观测、校验、工具、结果处理），完成后放行下一项。这样模型仍能一次提交多个调用，最终 `ToolMessage.tool_call_id` 仍由框架配对。等待上限由环境变量 `NAIYUE_TOOL_BATCH_WAIT_SECONDS` 设置，未配置默认 60 秒，非法/非正值在 Agent 装配时拒绝；超时记录安全事件并让运行失败，不能无限占用工作线程。不修改 `.env`。
5. 正常的可恢复工具错误 `ToolMessage` 可继续下一个调用；若下游抛出未处理异常，协调对象先标记本批次致命失败，等待中的后续项不得进入有副作用工具体，原异常仍上抛为运行失败。项目现有的 `:stop` 是当前 turn 后停止，不自动取消已发出的整批；要更细的活动取消须另写 Spec。

数据流示意：

```text
模型 AIMessage([read#1, write#2, read#3])
  → HITL（必要时中断、审批、恢复；拒绝项先获 ToolMessage）
  → after_model 形成运行范围批次计划：sequential，待执行 ID 顺序
  → create_agent 仍为每个待执行调用 Send 到 ToolNode
  → wrap_tool_call 按 #1 → #2 → #3 放行完整工具链
  → 各自 ToolMessage 按原调用 ID 回填 → 下一次模型调用
```

## 影响范围与验证门槛

- 新模块 `naiyue_test_agent/agent_runtime/tool_batch.py`：只放批次计划与调度 middleware。现有 `agent_runtime/__init__.py` 负责装配/模型/运行事件，不把协调逻辑堆进该文件。
- `agent_runtime/__init__.py`：将协调对象放进每次同步调用上下文，按验证过的 middleware 顺序装配；不改变已有公开函数签名。若需暴露新模式设置，只增加可选参数或工具注册元数据，不做破坏性改名。
- 工具定义与应用装配：仅在首个实际有副作用工具落地时声明模式；本草案不提前添加 `write/edit` 空模块。现有只读工具是否需要显式并行标签，按测试能否证明默认值再决定。
- 新测试：先红灯验证双只读重叠、读+写与写+写按原顺序、同 ID 结果回填、参数/可恢复错误继续、致命异常后后项不执行、HITL 批准/拒绝/恢复、双会话隔离及有界等待。用事件同步，不用极短 `sleep`。先在合成工具上跑，再与真实文件写工具和工作区审批组合；全量 pytest、compileall、pip check 均需通过。

## 仍需解决的风险和不在本片的能力

`Send` 任务是在图执行器中并发排队的；本草案通过等待约束实际工具执行，但不能仅凭一次探针就宣称任何版本、任何并发上限都不会发生工作线程饥饿。须在当前固定版本中用 `max_concurrency=1/2`、多调用、审批跳过与并发会话做确定性验证，并设置有界等待；若无法证明进度和顺序，就转向自定义工具路由图，不用悄悄加大线程池掩盖问题。

运行范围协调**不解决跨会话同一目标**的并发写入；文件操作层仍须有资源级冲突/原子写入约束，并通过两个会话同目标测试。它也不解决进程崩溃中途的副作用恢复：运行对象不持久化，未来持久 checkpoint 上线前必须明确部分工具已执行时的去重/人工复核语义。当前项目尚无异步中间件链；未来 `ainvoke/astream` 必须单独实现非阻塞的 async 协调并验证，不能直接复用同步条件变量。

本方案遵循当前四层边界：应用决定可用工具与可信会话，运行层决定每次模型回复的工具调度，具体工具保留参数/权限/资源校验，模型接入层不处理文件冲突。用户已选择逐批协调，不以整运行串行代替。

## 本片实现与验证（2026-10-02）

- `ToolBatchMiddleware` 在运行层装配时读取工具 `metadata['naiyue_execution_mode']`，缺省 `parallel`；非法值直接拒绝装配。`after_model` 建立运行上下文中的计划，`wrap_tool_call` 在含串行工具的批次按调用 ID 放行完整下游链。缺失计划时拒绝放行含串行工具的批次；重复或空调用 ID 在分发前拒绝。等待上限默认 60 秒，可由 `NAIYUE_TOOL_BATCH_WAIT_SECONDS` 配置；未修改 `.env`。
- 红灯先观察到混合批次越序、致命异常后后项仍执行、非法 mode 不拒绝（`3 failed, 3 passed`）；补充缺失计划回归时再观察 `1 failed, 9 passed`。实现后的定向测试为 `14 passed in 2.60s`。其中覆盖纯读重叠、读写读顺序、可恢复错误继续、致命异常阻止后项、审批批准/拒绝、重复 ID、同 Agent 双会话隔离、`max_concurrency=1/2` 及非法等待配置。
- `.venv/bin/python -m pytest -q` 实测 `312 passed in 6.79s`；`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` 退出 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。这是当前版本的合成工具与内存审批验证，尚无真实文件写入、真实供应商混合工具请求、跨进程恢复或异步执行证据。
