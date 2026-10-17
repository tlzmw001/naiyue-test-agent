# Spec086：前台 SubAgent、内部批量审批与可恢复执行

日期：2026-10-16。状态：**供审阅，未实施；本文不构成产品实现授权。** 基于[Spec085](085-tool-system-rebuild.md)的工具、权限与错误合同；本节涉及子任务时覆盖085的早期候选。准确文件/消费者/切片见[文件地图](086-subagent-file-map.md)，框架实验见[探针记录](../research/subagent-framework-probes-2026-10-16.md)。下列新类/函数是拟定接口。

同日补充：正常串行排队固定60秒期限及旧环境读取已由[090A](090-tool-wait-stop-and-author-contract.md)移除，同步停止guard已补；086复用，不再重复实施。090B协作停止和[091进度](091-tool-progress.md)仍未实现；090C已确认默认不限时、timeout省略/0无限及正数可指定。根会话恢复见[092](092-session-restart-recovery.md)，不自动启用子thread跨重启恢复，其余086仍供审阅。等待/执行/清理期限、Pi文件队列与本项目busy区别见[工具能力盘点](../research/tool-system-capability-audit-2026-10-16.md)。

**本版已替换原“A审批等待B完成”的接法。** 用户同意沿独立子运行边界继续细化：父子共用saver、各子实例使用独立逻辑thread_id，子审批立即提交到该实例的唯一驱动者，父继续等待结果。具体API/保存/交付细节仍供审阅；P4与新增P5是框架探针，不是产品实现完成。旧P1/P2/P3仅保留历史证据，不再据其建设父工具重入账或空GraphInterrupt协调器。

## 1. 本次交付及工程建议

已确认：一个通用`subagent(agent, task)`工具；可信代码注册角色；每次委派新实例；仅主Agent→子Agent一层；仅前台、同进程；同级可按父批规则并行；子任务独立消息、完整权限/内部人工审批；保留过程观察、停止与必要保存。父模型等待本批工具结果齐备后继续。

不交付已结束实例续跑、孙Agent、后台任务、角色文件自动发现/热替换、工作区插件、独立进程/队列、跨重启待审批恢复。Bash/PowerShell正常启动命令进程，和Agent实例是否另起进程分别定义。

本Spec提出的工程细节供本轮审阅：默认不额外设置`max_model_calls`；不把正常串行排队的60秒当故障；沿现有事件流携带子来源。审批以实例当前批为提交单位，可在兄弟任务运行时恢复；它们不冒称此前逐项确认的产品值。

最小完整方案复用`create_agent`、官方HITL/`Command(resume=...)`、同一个显式checkpointer、ModelCatalog和现有工具适配器；不手写第二套模型循环。涉及超过8个生产文件，需按附录六个切片实施，不能作为一次大提交。

## 2. 当前缺口与框架依据

当前App持有共享编译图，SessionService持有会话模型/控制/审批；`RunContext`、选模及活动工具读取主要按session_id定位。没有可执行的SubAgent角色/实例入口。当前审批解析假设一个interrupt，并用父最后一条AIMessage匹配动作；子图暂停时这个假设不成立。

当前`ToolBatchMiddleware.after_model`只在运行上下文放计划，恢复未必重走此hook；`AsyncBatchPlan`把所有BaseException当失败，串行等待还有60秒期限。让子中断直接冒泡到父工具的旧接法会出现计划丢失或错误终局；新接法由各子驱动者处理本子中断，父工具保持等待，不沿用该旧恢复结构。当前Web/CLI把所有消息及run_finished当主运行事件，不能原样混入子事件。

本机版本：LangChain1.4.2、core1.6.6、LangGraph1.2.12、prebuilt1.1.0。保持版本，不依赖1.4.6的新per_call功能。新假模型探针确认：

| 已验证框架事实 | 不能据此声称的产品能力 |
|---|---|
| 并行A暂停后B可完成；恢复A不重进B，动作各执行一次 | 当前项目批次/许可已经可恢复 |
| `StateSnapshot.tasks[].result`可提供尚未归并到根messages的完成任务结果 | 可以忽略任务结果，只看父最后一条消息 |
| 保存顺序、重建协调器后，可让串行后续任务无执行地挂起，再恢复 | 现有协调器无需修改或任意IO可exactly-once |
| 两个子任务有相同内部call_id仍有不同interrupt ID；按ID部分恢复可行 | Web现有仅call_id的审批身份足够 |

P4/P5进一步验证：同一编译子图按独立thread并行；A批准/拒绝/停止都不等B，停根不追加父模型请求；子前置prepare跨HITL恢复不重跑。P5在InMemorySaver与AsyncSqliteSaver内存库各四组通过。实际ToolSpec/Auto/许可、Web及必要保存故障仍须产品验收。

[官方子图文档](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)说明默认每次调用独立状态并继承父checkpoint以支持同次调用中断；工具内动态调用子图不保证可由get_state(subgraphs=True)静态发现。[中断文档](https://docs.langchain.com/oss/python/langgraph/interrupts)要求恢复同thread，节点/调用入口可能重入，多个中断用ID映射。

Pi本地a69bef789的示例使用角色定义、独立输入和等待子任务完成/并行汇总，子任务通过CLI进程执行；本项目借鉴定义与结果职责，采用同进程子图。AgentX本地3fcfa0e的已核对模型/消息处理链没有提供本方案所需的等价嵌套HITL证据，不能拿它证明恢复成立。

## 3. 三种对象：定义、实例、执行片段

| 数据 | 生产者/所有者 | 消费者与寿命 |
|---|---|---|
| `SubAgentSpec`：角色定义 | 用户可信代码；App共享名称表 | 注册校验/子图装配/父提示词；登记后不可变 |
| 实例记录：角色/任务绑定、agent_path、child_thread_id、model_id、工具名、状态/唯一驱动者 | SessionService按父批次/调用建立 | 同任务子图恢复、权限与停止；本会话当前任务必要范围 |
| 子图messages、ToolExecutionState、模型计数 | 子图节点/官方checkpoint | 子模型、审批恢复；父模型不接收整份历史 |
| 一次执行片段的run_id、RunServices/观察对象 | 应用每次首次运行或审批恢复建立 | 本次事件、停止及IO；恢复后新建，实例路径保持 |

不新增`AgentScope`类或`SubAgentSession`。已有RunContext增加`agent_path`、`parent_tool_call_id`、`parent_run_id`、`root_run_id`；根路径固定`root`。已有读取函数增加内部可选参数`agent_path='root'`，调用时显式传递：

- `resolve_model(session_id, agent_path='root')`：根读会话当前模型，子读实例启动时冻结模型。
- `resolve_active_tool_names(session_id, agent_path='root')`：根读会话活动集合，子读角色明确的工具名。
- `resolve_permission_context(session_id, agent_path='root')`：同一会话规则/模式/session_allow；返回值附带当前实例身份。

路径示例：`root/tester/7a...`和`root/tester/9b...`是同角色不同实例；它不是磁盘目录、cwd、thread_id或checkpoint_ns。实例ID以父批次ID+父tool_call_id稳定派生，恢复校验原角色/任务摘要和调用绑定，同任务恢复不会创建第二个实例；活动父工具不会因子审批重入。

模型切换对根下一次请求生效；已启动子任务继续使用启动时模型，不在审批恢复时重新继承。角色定义不变但会话模式可在下一子批次读取新值；已暂停批次仍用保存决定。

## 4. 定义、登记与启动输入

`tools/subagent.py::SubAgentSpec`是冻结dataclass，字段如下；这是工具接入数据，不含App/图/凭证句柄。

| 字段 | 合同 |
|---|---|
| name | 非空安全名称，沿现有工具标识字符约束；共享表唯一 |
| description | 非空短说明；父提示词的角色索引使用 |
| system_prompt | 非空角色提示词；仅该子实例请求使用 |
| tool_names | 有序tuple，无重复；空tuple表示无工具；不得含subagent |
| model_id | 可选；填值必须存在于同一个ModelCatalog |
| max_model_calls | 可选正整数；省略表示不加额外主模型轮数上限 |

角色代码先调用`app.register_tool(...)`登记所需工具，再调用`app.register_subagent(spec)`。异步App对应`aregister_subagent`；现有CLI异步facade转交同一入口。登记先校验字段、工具及模型，装配好角色图，再在锁下复核名称并整体发布；失败不暴露半个定义。只支持新增，不覆盖旧角色或重载Python模块。

App共享名称表的每项直接保存定义+对应编译图；不另建一套SubAgentRegistry或每会话复制表。编译图按不可变角色复用，调用状态按父任务隔离。运行中登记新角色只改变下一次父模型请求的角色索引；运行中/待审批实例沿原定义。

工具输入只有`agent: str`、`task: str`，工具schema校验非空。`create_subagent_tool()`返回通用ToolSpec：execution_mode=parallel、side_effects=unknown；角色可能写文件，不能默认标read_only。受信已登记角色的启动默认Allow，仍检查subagent及args.agent的Deny；未知角色产生具体ToolException，正文不启动图。

父ToolSet动态提示词只加入角色名/短说明，以及“每次新任务、前台等待”的使用约定；不重复加入完整角色system_prompt。父停用subagent后不能猜名字执行。角色工具列表和父当前活动列表独立，从共同ToolRegistry解析；全会话Deny仍约束父子。

启动输入为：角色system_prompt + 父传入task + 可信工作区/执行环境/实际工具信息 + 按现有机制准备的项目指令及Skill索引。不默认复制父历史；任务中所需背景由父写进task。task虽作为子HumanMessage输入，应用来源标签必须标明`delegated_task`，不能变成真实用户授权；seed消息ID由实例稳定生成，再入使用同一ID/原内容，不能审批恢复时追加第二份任务。

子图不装根的steering/follow-up注入及根会话终局选择hook；沿现有prompt、上下文保护、模型/工具观察和权限适配。根steering不自动广播给子；本Spec不引入向子任务发新提示词的交互入口。

代码登记示意（拟定接口，不是当前可运行产品）：

```python
app.register_subagent(SubAgentSpec(
    name='tester', description='检查修改并运行测试',
    system_prompt='根据任务检查代码，用实际测试证据报告结果。',
    tool_names=('read', 'bash'),
))
```

模型调用形状为`{'name': 'subagent', 'id': 'delegate-a', 'args': {'agent': 'tester', 'task': '检查这次修改并运行相关测试'}}`。应用将父batch_id与delegate-a绑定到唯一实例；再次恢复delegate-a不等于再次登记或创建tester。

## 5. 模块与函数怎样连接

新增三个生产文件，其余扩展085已有/拟新增模块：

```text
应用装配/可信注册
 → SessionSubagentControls.register_subagent → build_subagent_runtime → create_agent
 → App共享角色表（SubAgentSpec + compiled graph）

父模型 subagent(agent='tester', task='检查修改并运行测试')
 → ToolSet → Batch → Observation → ToolError → ExecutionAuthorization
 → tools.subagent的BaseTool
 → ToolRuntime.context.ainvoke_subagent(agent, task, tool_call_id)
 → SessionSubagentControls.ainvoke_subagent → _subagent_steps：创建实例及唯一驱动者
 → agent_runtime.subagents.ainvoke_subagent_step：绑定子RunServices，child.ainvoke
 → 若返回interrupt：register_live_approval → 等本实例决定 → 同thread恢复下一step
 → 子模型 → Permission → BatchApproval → Batch → 具体工具
 → 子最终消息 → _finish_subagent_result → 父同call_id ToolMessage
```

`tools/subagent.py`只通过RunContext上的调用端口委派，不import application/agent_runtime；这对BaseTool是必要的跨层接点，不传整个SessionService。对应同步端口`invoke_subagent`供真实同步SDK消费者，调用child.invoke；异步调用child.ainvoke。共用实例准备/结果/状态算法，不在现有事件循环内嵌套asyncio.run，不另建子线程事件循环。正式CLI复用已有AsyncCliApplication。

`application/session_subagents.py::SessionSubagentControls`负责登记表、实例身份/状态、当前控制及子审批驱动；`agent_runtime/subagents.py`的invoke_subagent_step/ainvoke_subagent_step负责一次图执行、子RunServices绑定和结果/控制分类。_subagent_steps复用现有run_steps/arun_steps与IOCall共享同步/异步生命周期；模型—工具循环始终由create_agent执行。已有SessionService通过mix-in拆分，防止473行现文件继续膨胀；它们不各自保存另一份权威会话状态。

`build_subagent_runtime`复用现有运行层middleware装配：按角色提示词/可选call limit和子作用域参数创建图。继续使用085的ToolSet/Permission/Approval/Batch/ToolError，不复制权限算法。根/子差别由装配时明确选择hook表达，不在每个hook到处判断“是不是子Agent”。子额外装`SubagentModelFailureMiddleware`，理由和位置见下一节。

## 6. 正常调用、普通失败和预算

例：父调用tester，子提出Read和Bash。子Permission一次准备当前批，Bash默认Ask；此时Read/Bash均未执行。官方HITL返回带子来源的批量请求。批准恢复后不重跑Auto，执行Read/Bash，子处理真实工具结果并输出报告。报告只作为父`subagent`结果，父再决定如何答用户。

内部文件不存在/命令非零/拒绝操作等先通过085统一错误链交给子模型，允许子修正或在报告说明失败。测试执行完整且发现失败测试属于正常检查报告；不能把业务失败统一写成委派机制故障。

终止反馈：

| 情况 | 父工具结果/传播 |
|---|---|
| 子正常最终回答 | content为有界报告；可带JSON artifact，ToolMessage success |
| 未知角色或明确无法启动 | 具体ToolException → 统一ToolError → 同ID error消息 |
| 可识别模型调用终局失败 | 仅在子模型实际请求边界识别，生成具体ToolException；模型门禁/上下文/保存故障不进入此转换 |
| 主模型轮数超过配置 | 精确捕获ModelCallLimitExceededError，转具体ToolException；不再调一次模型生成摘要 |
| 只停止该子任务 | 内容标明已停止/未完成，必要时引用已有结果；同ID error结果交父继续 |
| GraphInterrupt / 根UserStop / 外部CancelledError | 保留控制效果，不能由通用ToolError变文本 |
| A自身checkpoint等局部执行失败 | 结束A并完成其必要收尾，在委派边界转具体ToolException；父收到同ID error，B继续，不把A标为成功 |
| 根保存失败，或确实使根/共享控制无法继续的故障 | 由所属层处理并上抛，根结束时才停止/唤醒其余活动子；不能仅凭A一次失败推断共享设施全局不可用 |

具体接线：在子图模型wrapper最内侧、ModelInput及现有输入恢复的内侧装`SubagentModelFailureMiddleware.wrap_model_call/awrap_model_call`。它只包框架实际模型handler，调用已有`classify_model_failure`；已识别请求失败在本次子RunContext的`_subagent_model_failure`槽保存原异常对象引用和固定category/reason，再原样raise，外层原日志/分类仍看到同一异常。下一次模型handler进入即清空旧槽。这个非序列化槽只为确定失败来源，不是可选观察记录，不读诊断标注作控制。

runner在child.invoke退出时，只有收到与该槽同一异常对象才走模型请求失败提示并保留cause；未知异常或身份不同不走该模型分支。ModelResponseRejected由外层响应门禁抛，checkpoint写入在模型handler外，均不能命中；不以异常名字TimeoutError猜测它来自模型还是SQLite。此边界状态每次子执行独立，不放共享middleware实例或checkpoint。

父通用ToolError对subagent只转换明确ToolException；其他未处理异常返回None上抛。子委派边界先结束失败实例、作废其自己的pending并确认已开始IO收尾；A自身checkpoint失败也应转为明确的ToolException，说明子任务失败/记录未确认，保留原cause供诊断。此转换不请求根停止、不触碰B的控制或审批，不要求先保存成功A才能反馈A失败。根保存/共享控制故障和控制信号仍按各自合同处理；不能用`except Exception: ToolException(str(error))`掩盖未收尾任务，也不增加错误类/提示白名单。普通叶子工具保持085统一反馈。

有界报告使用`tools.subagent.max_result_lines`和`max_result_bytes`；工程建议沿现有Read/Shell为2000行/65536字节。截断保留完整前行并明确报告不完整；单行按UTF-8安全边界截断。由既有输出格式器扩展，不另建永久记录平台。

artifact可保存agent_path、角色、完成状态、model_id、可用usage；未知用量保留空值，不报0。必要模型反馈必须在content。完整子历史留在官方checkpoint，不复制到artifact/根消息，不承诺独立永久查询接口。失败仍用ToolException，无新增错误结果类。

`max_model_calls=N`用官方ModelCallLimitMiddleware(thread_limit=N, exit_behavior='error')，计数放独立子图state；同任务HITL恢复不重置，不同实例不共享。第N次回答若已正常结束则成功；需要第N+1次才报超限。不能使用每次invoke重置的run_limit。

这个预算只统计主循环响应次数，不是全树token/费用/所有实际HTTP请求上限。Auto、SDK重试、现有摘要策略仍按各自合同；每次请求和工具执行的超时仍有效。首版不设包含人工等待的整段墙钟期限。[设计配置样例](../examples/tool-runtime.example.toml)已补输出预算注释；本轮不改实际配置，当前程序尚未加载这些设置。

## 7. 权限、真人意图与本会话不再询问

每个子批次照085执行：参数能否形成审查对象→显式Deny/Allow→保守默认策略→模式→必要Auto→保存PreparedToolCall→官方批量HITL。显式Allow不能覆盖工具能力/目标一致性限制；子任务没有自己的Bypass模式。

父子使用同会话Deny/Allow和SessionService内存session_allow；不写TOML、DB或ToolSpec属性，重启清空。界面“不再询问”从被批准的保存审查对象生成限定规则，不接受客户端随意传Allow文本。

一次性批准精确绑定session_id/agent_path/batch_id/call_id/参数/真实目标/执行环境，不跨实例复用；角色启动允许不授予其内部动作。环境来自应用，首版host+真实platform；模型不能指定权限环境。

Auto只使用可核验真实人类消息的有限投影和当前动作/参数；不读任何工具结果、文件正文、父助手解释、模型摘要、Skill/AGENTS正文，也不把delegated_task当真人意图。父运行的当前canonical消息提供同一真人投影给子审查；无足够证据回Ask，不能用父写的task补授权。沿085既定边界：当前调用中的写入/脚本参数可见，审查器不额外读取文件。

本次不建设不受压缩影响的授权历史库。根消息压缩后已不在可信人类投影中的口头约束不能假装仍完整保留，长期禁止配置Deny；此限制与085一致。

## 8. 子图状态、唯一驱动者和当前批审批

App仍共享角色定义/编译图、ToolRegistry、ModelCatalog、saver；不为实例新建图、SDK或数据库。每个实例在应用记录中持有稳定child_thread_id，独立保存其messages、ToolExecutionState、模型计数和checkpoint。该ID是图状态键，不是系统线程，不由模型输入，不与父thread_id相同；一次委派一个ID，恢复沿用，结束后不提供重开接口。

每个实例持有同一个RunControl直到子任务结束；每个执行片段可新建RunObservation，但通过可选stop_control参数注入该control，恢复不能丢失等待期间的停止标志。turn观察在暂停时用现有snapshot(pending=True)、恢复时restore；它是可降级观察，不参与审批/许可决策。模型轮数仍由官方子state保存，不从观察计数恢复。

正式异步入口以干净Context创建一个受App拥有的Task，显式传子config并绑定子RunServices；避免继承父graph的scratchpad/checkpoint namespace。同步SDK入口用干净Context运行同一生命周期步骤，不新建事件循环。不能只改thread_id而继续无意继承父内部配置；日志/追踪需要的关联由显式source传递。

一个实例只有一个_subagent_steps驱动者：它调用本子图，遇返回的__interrupt__则登记请求并等待，收到决定再用Command恢复本子图。它不直接调模型/工具，不重写Agent循环。父subagent工具始终等待这个驱动者的最终结果；子中断不会传播为父工具中断，父run保持活动，B继续运行。

`session_approval.py::PendingToolApproval`保存当前请求绑定、状态和决定；register_live_approval创建它并登记到当前会话/实例。现有SessionService锁保护状态，登记完成后才发布审批事件。异步等待用Future、同步SDK等待用Event；两者只选择当前驱动实际所需的一种通知，不增加消息队列/审批服务器。决定先在锁下写入，锁外通过所属loop.call_soon_threadsafe或Event.set通知。

以下是拟定数据形状，不是当前产品输出：

```text
实例: {agent_path, child_thread_id, parent_tool_call_id, root_run_id,
       agent_name, model_id, tool_names, status}
待审批次: {approval_batch_id, kind: live_child, session_id, agent_path,
           batch_id, interrupt_id, origin_run_id, items, status: pending}
待审行: {approval_id, tool_call_id, tool_name, target, workspace,
         execution_environment, prepared_call}
```

BatchApproval从已保存PreparedToolCall生成官方HITL请求，附实例/批次及JSON prepared_call；子图返回interrupt并完成本次checkpoint后，应用据真实payload登记批次。Web只显示有界目标/参数，不输出整个内部prepared_call。每个批次的approval_batch_id和每行approval_id由服务端生成；模型call_id仍只用于工具消息配对。

## 9. 提交决定怎样只恢复A

完整链路如下；方法均为拟定目标入口：

```text
A的ainvoke_subagent_step → A自己的官方HITL返回interrupt
 → SessionApprovalControls.register_live_approval → PendingToolApproval等待
 → 领域审批事件/会话查询 → CLI/Web立即显示A当前批
用户提交A这一批的决定
 → validate_approval_submission → submit_subagent_approval
 → 原子保存决定/生成本批精确许可 → 唤醒A唯一驱动者
 → A驱动者调用ainvoke_subagent_step(Command(resume=...), A原thread_id)
 → 官方HITL应用approve/reject → Batch → 具体工具 → A下一模型请求
```

validate_approval_submission共用根/子当前对象、会话/来源run、批次与行唯一性、完整性及“不再询问”校验。submit_subagent_approval只投递给已存在的驱动者，不执行graph.ainvoke，不新建父run，也不重跑Permission/Auto。不允许对运行中的同一个thread另起恢复调用。

一次提交覆盖**一个Agent当前批中全部待审行**，不要求覆盖其他实例的待审行；同批默认Allow的其他调用仍须等该批审批完成才执行。A/B均待审时，界面分别提交A批、B批；同实例后续新批产生新身份，旧按钮不能批准新批。不同实例即使call_id相同也不会混合。

Web现有`POST /api/sessions/{session_id}/approvals`增加严格approval_batch_id，每项改用approval_id；仍带request_id/origin_run_id。服务端按保存kind分派：

| 审批目标 | 应用入口与返回 |
|---|---|
| live_child：父run仍活动 | 新submit_subagent_approval(session_id, *, approval_batch_id, decisions, origin_run_id, remember_approved=())；只回accepted及当前root run_id，原SSE继续 |
| paused_root：根图原先已返回 | 现有resume_tool_approval/aresume_tool_approval按保存顺序恢复根，保留返回结果/新run行为；Web仍用既有RunDelivery生产者 |

kind由服务端记录决定，客户端不能选择。根/子复用校验、规则及官方HITL，投递入口区别服务现有根SDK合同与活动子驱动者；不同时改掉根run/resume公开合同。新增submit入口供LocalApplication、AsyncApplication及CLI facade使用，共用一份锁/校验；异步facade按既有命名提供asubmit_subagent_approval并返回接收回执。

请求幂等复用现有request_id机制；相同request重试返回原回执，新request重复/跨批/旧run决定报stale，不第二次发许可。remember_approved使用本批approval_id集合，从保存审查对象生成同会话限定规则，不接收任意Allow文本。

CLI单批保留现有approve/review列表用法；多批时必须以`:review <approval_batch_id> approve reject ...`明确选批，列表覆盖该批全部待审行，不默认选第一个实例。当前批ID随审批列表打印。Web按组提交同一合同，不要求模型提供这些控制ID。

## 10. 串并行与许可：删除旧接法的额外机制

父批执行前发现已知Ask，仍先整批审批；本批含sequential时仍按模型顺序执行。subagent本身parallel不改变这条规则。

串行prefix→A→tail时，A等待用户期间父工具Task保持活动，tail保持普通串行排队；批准A后A完成再前进。无需把tail抛空GraphInterrupt，也无需由父恢复重建prefix/A/tail；并行B先完成则其父工具任务结果自然保留。父模型始终等本批结果齐备。

因此不新增ExecutionEntry/entered-suspended账、父工具暂停重入放行器或空GraphInterrupt协调分支。父启动授权消费一次，子内部每个调用各消费自己的许可；A审批仅恢复A尚未执行的本批动作。OneUseApprovalGrants按session/agent_path/batch/call/参数/目标/环境绑定，不能复制一份供重入或跨子消费。

子Permission→Approval→Batch顺序保持：Permission已完成并写state，HITL暂停；恢复仅重入审批节点，之后Batch首次准备本批执行计划。不再次分类/Auto，不在恢复时重新解析目标；执行前真实目标一致性检查仍保留。原始参数变更需要新调用。

正常父批串行排队可能包含子任务执行/人工等待，移除60秒排队故障阈值及对应旧env读取。图/顺序/许可绑定缺失仍是机制错误；模型请求、命令执行及进程清理仍各有期限。设计样例已取消execution.batch_wait_seconds。沿当前max_concurrency按批限制，不承诺全树总并发等于单批值。

根HITL的现有审批恢复继续按085核验；本次验证范围是前置BatchApproval，不扩张为任意工具体内中断或崩溃后副作用exactly-once的保证。未处理GraphBubbleUp/取消仍保留控制，不生成普通成功/失败消息。

## 11. 拒绝、停止与竞争

| 动作 | A如何处理 | 对父/B的影响 |
|---|---|---|
| 拒绝A当前批某动作 | 官方HITL生成该动作error消息；本批其他获准调用执行，A模型可继续 | B不受影响，父继续等A最终结果 |
| 停止A实例 | A控制标志置位；旧待审批次作废，唤醒A并沿原图停止检查配对未开始调用 | A保存/收尾后返回ToolException给父；B继续 |
| 停止根整轮 | 根及全部活动子control置位；唤醒所有等待审批的子驱动者，阻止新启动 | 等已开始IO收尾；根不再请求主模型 |

活动`request_stop(session_id, *, run_id=None, agent_path='root')`默认停根。锁下核对当前root run/实例，标stopping并使目标pending批失效；锁外request其RunControl并唤醒待审批对象。对活动子无需等B、无需新root run或新停止producer；唯一驱动者收到服务端内部stop决定后用本子interrupt ID控制恢复，BatchApproval的stop guard优先返回finish_stopped_batch，不把stop值交给官方approve/reject校验。

若批准先被接受而停止紧随其后，停止仍可作用于实例：尚未通过工具入口停止检查的调用被阻止；已放行的调用按真实结果settle。不撤改已经发出的批准回执，也不承诺批准后任意时刻停止均等于未执行。若停止先使批次失效，之后的批准必须拒绝。每批只有一个人工决定可生效，实例停止可以晚于一次批准；锁与校验保护竞争，不以Future是否done代替授权判断。

具体状态边界：PendingToolApproval在会话锁下只能从pending转为accepted或invalidated，决定写入后不再改写。submit检查目标实例/root未请求停止，request_stop使尚pending批次失效；通知在锁外。批准回执仅证明决定被接收，不证明工具已执行。若批准已接收，之后的停止仍可阻止尚未放行的调用，不把批准历史改成拒绝。

继续复用ToolBatch取得串行槽/并发槽后、交给工具执行链前的停止检查；异步当前已有guard，同步入口须对齐。通过这个检查、进入执行链的调用视为已经放行，此后停止为协作式收尾，不保证底层IO尚未开始；排队或尚未放行的调用才可生成未执行消息。必要许可核对仍在ExecutionAuthorization，不新增执行账/回滚层，也不把整个IO锁在会话锁里。

实例已进入应用finalize或已终结时，不再接收新的实例停止，回执说明已收尾/结束。若停止与图最后一步交错，以实际run_stopped、真实工具结果及保存结果确认终局；不能仅看到control.requested就把已经完成的结果改成未完成。根停止仍可结束其他活动实例，已完成子结果不回滚。

根已返回paused_root且当前无活动生产者时，保留拟定astop_tool_approval及stream/同步薄入口：新root恢复run以控制唤醒原根审批、配对并保存；该路径不用于仍活动的子审批。Web活动controls可携agent_path，缺省root；暂停根approval-stop路由只接受服务端确认的paused_root。

实例状态running/awaiting_approval/stopping/completed/stopped/failed；GraphInterrupt不是failed。停止accepted与完成分开，只有子checkpoint和必要IO收尾成功后才发布子stopped。根整轮停止还须等全部子驱动者收尾及根必要保存。已经完成的B不重跑；已经开始的写入不能套未执行文本。

根因外部CancelledError或根层故障终止时，也必须结束拥有关系：父不能遗留等待Future的孤儿子任务。先请求相关子停止/唤醒待审，再通过既有await_settled等候已开始IO及子驱动者收尾，保留原异常。A局部失败则只收尾A并反馈工具错误；B仍在执行或等审批是正常活动，不是需要消除的孤儿等待。普通观察异常仍隔离。

已有session_controls职责内拟新增的_request_scope_stop(reason='failure')只在根已确定终止时用于整轮收尾：标根/活动子停止、作废未决批、唤醒等待。用户根停止、根层故障终止和App关闭共用该入口；_subagent_steps不能因A自身保存/执行失败就调用根广播。register_live_approval同时检查根/子停止，防止真正根停止后再登记一个永远不被唤醒的Future。局部失败只作废A自己的pending，不改B决定。

RunControl.request增加可选可信reason=user/failure/close，缺省user；首次停止来源保留，由应用填写，不从模型/HTTP自由文本取值。finish_stopped_batch/stop_tool_message据此生成固定停止提示，避免故障收尾沿用“用户停止”文本；当前UserStop控制类型及已有user_stop消息默认保持。P7验证已有guard的唤醒/异常链，新来源字段与文案须实施测试。

## 12. 事件、界面、必要保存和关闭

一个root run一条sequence不变。RuntimeEvent.source（冻结AgentEventSource）携agent_path/agent_id/agent_run_id/parent_run_id/parent_tool_call_id。子审批恢复产生新agent_run_id，但root run_id及外层SSE保持；根本身原审批恢复才产生新root run。RunEventPublisher.for_agent共享根队列/handler/序号，子RunServices独立绑定并finally还原。

审批登记是必要控制状态，事件只是投影：先保存子checkpoint及登记PendingToolApproval，再发审批事件；事件丢失可从同一会话pending查询重取，不能依赖订阅者成功才允许审批。恢复前不向根消息写审查对话。子message/delta不追加根聊天，子run_finished不结束根；按agent_path+call_id路由，子阶段暂停不等于实例终局。

现有approval_requested载荷增加approval_batch_id，前端收到后读取该会话当前pending列表并更新分组；不能等root run_finished才刷新。提交回执移除/禁用已接收批，失败或重连通过同一查询校准，跨页/重复决定按服务端状态拒绝。子来源事件须路由审批/进度，不能被统一忽略。

现有入口须明确修改：

- CLI审批分支当前先worker.join()，活动子审批必须改为直接投递；不能等待父worker结束。现有输入线程与async facade继续使用；非TTY也须让输入/运行独立，EOF/quit先停止并唤醒待审再join/close，不能无限等待审批Future。
- Web服务当前以busy/state=running拒绝全部审批；改为按保存kind核对。live_child只允许本活动root的目标pending批，不放开普通消息或第二个父运行。
- 前端ApprovalPanel当前受!busy限制；允许运行中显示子待审批次。批准回当前run_id时更新批次回执并继续原订阅，不重置已有SSE序号或重新开始父运行。
- 子普通完成、暂停和父完成分别展示；复用现有工具详情/审批组，不增加Agent树/子历史浏览器。

子每次图执行建议使用公开durability='sync'，P5已在本机内存/SQLite内存库验证；checkpoint写入确认后才发布待审或成功终局。子正常completed/stopped要求自身无未决interrupt/next；局部执行/保存失败终止为failed，不提供失败实例再入。自身保存未确认时不能宣布子成功，但可向父反馈明确的失败消息；不能把成功持久化失败实例作为反馈其失败的前提。

子保存的是自己thread的状态；父只收到content/artifact，不把子完整记录复制入根messages。SessionPersistence仍只对根start/finish，不给子新增同一会话active元数据行或另开数据库。根finish前新增assert_subagents_settled：没有活动子驱动者、待审批或未收尾IO；A已结束且有明确error反馈时，即使其自身checkpoint未确认也视为已收尾，不因此阻断根ready。检查基于应用权威实例记录，不靠可丢失事件；根自己的checkpoint/元数据必要保存仍须成功。子成功不等于根已保存，根失败不能倒改已确认子结果。

保存失败按发生边界处理：

| 边界 | 反馈与后续 |
|---|---|
| A已生成最后文字，但A checkpoint未确认 | A结束为failed，不发布A completed；向父返回同ID error，明确子任务记录未确认。B继续或继续等待自己的审批；父等本批收齐后可继续。不自动重试/恢复A，不请求整轮停止 |
| A结果已确认，后续根checkpoint或会话元数据保存失败 | A已执行/已保存事实保留；根显示保存未确认/failed，不宣布整轮成功，不自动重跑A/整轮 |
| 保存成功、仅事件/界面投递失败 | 按观察合同诊断/查询校准；不把真实执行成功改成保存失败或重跑工具 |

生成文本/工具副作用与checkpoint提交是不同事实；保存失败不回滚真实写入。已显示文字可保留但标保存未确认，父不把它当成功报告；明确的A失败反馈仍可进入父消息。不通过再次invoke子图补保存。原异常/cause保留供诊断，收尾次级错误不覆盖主异常。P6/P7仅证明此前所选上抛/广播路径可运行，不能证明A局部失败必须停止整轮；新隔离合同须按下节实施验收。

当前只恢复ready会话，进程退出时活动父/子审批均不能跨重启恢复；独立child_thread_id不自动增加完整恢复能力。保留当前受限状态保护；不为本次新增子会话DB表或结束后历史API。

关闭顺序：阻止新启动→对活动根/子请求停止、唤醒待审批次→等已有owned tasks/IO收尾→确认必要保存→关拥有的模型/存储资源。子不能关闭共享ModelCatalog/SDK。异步审批等待不经不可取消的无限shield：复用IOCall时等待标critical=False，取消先进入生命周期清理；已开始IO/必要保存再按原critical/await_settled合同处理。

## 13. 改前改后及验收

| 项目 | 当前 | 目标 |
|---|---|---|
| 注册 | 无角色入口 | 共享不可变定义+按角色复用编译图 |
| 会话作用域 | 主要session_id | 同会话下明确agent_path，模型/消息/控制隔离 |
| 审批 | 单interrupt、父AI消息匹配 | 每实例当前批登记/立即投递，由唯一驱动者恢复；根旧合同保留 |
| 批次 | 子中断冒泡会丢父plan | 子驱动者处理子暂停，父工具持续等待，串行普通排队 |
| 许可 | 尚无子scope | 启动一次、子动作各一次；scope精确绑定，无新增重入许可账 |
| 事件 | 全部当主运行 | 同一外层流、子source分流 |
| 保存 | 根ready可重启，pending受限 | 保持边界；同进程多子暂停可恢复 |

必须覆盖正常单子/无工具角色、无效登记/未知角色、一层限制、动态登记下一请求可见、模型冻结/独立历史、父子session_allow/单次许可隔离、真人来源、内容截断与artifact往返、预算跨审批/独立实例、参数错误不泄runtime字段。

恢复必须有真实create_agent确定性测试：B仍运行时A审批/恢复/完成；B先完成时也不重复；A/B同内部call_id；全部批准/混合拒绝；串行prefix/A/tail；当前批及新批模式边界；父启动许可仅消费一次且父工具不因子审批重入；Auto调用次数；旧审批/交叉实例/重复提交竞争。

停止必须覆盖活动模型、活动工具、A待审/B完成、A/B均待审仅停A、根全停、多调用未执行配对、approve/stop竞争、必要保存/清理失败和关闭。另测A自身checkpoint失败时B仍运行/待审、B可正常批准、父最终收到A error/B success并继续；A不重放，根可保存明确失败事实。正常测试显式断言事件及观察无意外故障；根机制异常不能被局部ToolError吞掉。

实现后运行附录逐片命令、全量后端/前端与构建；同步SDK、异步CLI/Web、SQLite必要保存都要接线。macOS假模型通过不等于Windows原生运行验证；WIN1仍在工具建设后完成。

本轮证据仅为源码核对、假模型与内存/SQLite内存库框架实验及文档校验，没有产品SubAgent、真实模型/文件动作、服务端集成或Windows实测。实施前先审阅本Spec工程细节，按附录逐片TDD，不以写完文档代替功能验收。
