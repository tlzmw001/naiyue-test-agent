# 前台多SubAgent：恢复与批次的框架探针

日期：2026-10-16。用于[Spec086](../specs/086-subagent-foreground.md)的机制核对；假模型、内存动作和InMemorySaver，经stdin运行，不修改产品代码/依赖/真实配置，不访问真实模型/文件业务。不是应用集成通过记录。

## 基线与核对方法

- 工作区：`/Users/zmw/naiyue-test-agent`；产品最近提交db87428，已有未提交文档/课堂记录保留。
- 项目虚拟环境：LangChain1.4.2、langchain-core1.6.6、LangGraph1.2.12、prebuilt1.1.0。
- 使用真实create_agent、官方HumanInTheLoopMiddleware、Command、GraphInterrupt；父工具调用子编译图。
- 自定义假模型按已保存messages选择固定响应；动作只向内存trace追加标记。asyncio.Event确保B在A暂停后再动作，不依赖短sleep猜时序。
- 每个探针由`.venv/bin/python - <<'PY'`执行临时Python定义并以assert验收；本文件保存输入/断言/实际输出摘要，原stdin程序未变成仓库测试，实施仍须形成可重复测试文件。

## P1：A内部暂停，B随后完成，恢复不重进B

构造父一次响应发两个并行subagent调用。A子图的action要HITL；B子图无Ask，其action等A首次抛GraphInterrupt所触发的Event。

首次返回trace：

```text
['A entry', 'B entry', 'A paused', 'B action']
```

公开父StateSnapshot.tasks投影：

```text
tools interrupts=1 result_calls=[]
tools interrupts=0 result_calls=['parent-b']
```

用A实际interrupt ID批准，最终trace：

```text
['A entry', 'B entry', 'A paused', 'B action', 'A entry', 'A action']
```

断言A包装入口2次、A动作1次；B入口1次、B动作1次；父最终回答完成。退出码0。

结论：框架能保留并发已完成任务，即使其ToolMessage尚未归并到root messages；`tasks[].result`是公开读取入口。不能仅由最后AIMessage判断完成，也不必为B再建产品结果缓存。

## P2：串行队列中子任务暂停，后继不抢跑

最小原型保存顺序到AgentState，恢复从公开snapshot完成结果重建位置。当前顺序任务遇GraphInterrupt时设置suspended并notify；尚未进入正文的排队任务抛空GraphInterrupt，原型不把它当工具失败。两组均使用真实父/子图。

| 输入顺序 | 首次trace | 恢复后trace |
|---|---|---|
| A(Ask), tail | `['A entry']` | `['A entry', 'A entry', 'A action', 'tail']` |
| prefix, A(Ask), tail | `['prefix', 'A entry']` | `['prefix', 'A entry', 'A entry', 'A action', 'tail']` |

第一组首次公开tasks为一个真实interrupt、一个无interrupt且无结果的未完成任务。第二组公开完成结果包含prefix。两组均断言动作A/tail各1次、A动作先于tail、父终局完成；第二组prefix仅1次。退出码0。

源码依据：`langgraph/pregel/_runner.py`的GraphInterrupt分支在中断tuple非空时才写INTERRUPT，不将空中断当普通失败；未完成任务在原thread恢复时重入。此为当前固定版本的底层协议，不是官方现成“串行暂停协调器”。

旧候选的约束曾是：该用法隔离在既有batch协调器并补版本回归；普通异常、外部CancelledError、保存/清理错误不能改成空中断。恢复计划来自保存事实，不依赖after_model再跑。未证实现有AsyncBatchPlan支持，当前它仍把BaseException标失败。P4/P5独立子驱动接法不需要此空GraphInterrupt机制，当前086不据本段新增该实现。

## P3：不同子任务相同内部call_id，按interrupt ID恢复

父并行启动A/B，共用一个按调用隔离的子编译图。两子均产生内部call_id=`same-inner-id`的待审批工具；HITL请求附agent_path。父InMemorySaver保存。

实际断言：

1. 首次有两个不同interrupt ID，动作均0次。
2. 只批准A的ID：A动作1次，B仍保留原interrupt ID，B动作0次。
3. 再拒绝B：B动作仍0次，两子/父完成，父结果分别匹配parent-a/parent-b。
4. 包装入口A2次、B3次；A完成后第二次恢复不再进入A。

退出码0。首次临时假模型签名遗漏stop/run_manager导致探针TypeError，修正测试桩签名后上述断言通过；这是探针代码错误，不是产品故障，也未用改断言消除失败。

结论：审批身份不能仅用内部call_id；恢复命令需要精确interrupt ID映射。当时的候选仍整批提交当前全部审批；P4/P5之后的086改为按每个实例当前批独立提交，不再把全树pending集合绑定为一批；这仍不要求逐条工具审批UX。

## 对审批提交时机的限制

P1/P2/P3中顶层ainvoke在兄弟分支完成或暂停后才返回interrupt集合；未验证也不采用对仍运行的同thread并发ainvoke(Command(...))。这只约束该版父子共用中断返回边界的接法，不能推出所有前台子Agent都必须等待兄弟任务。原Spec086据此建议“A批准等B完成”，用户指出体验问题后，该限制撤回；新候选见P4。

## P4：A可独立审批/恢复，B保持运行（同日审阅补证据）

目标：父前台工具等待子结果，A提出审批时无需等待B完成。沿真实create_agent及官方HumanInTheLoopMiddleware，不修改产品和依赖。

临时探针的变化：父/子共用一个InMemorySaver，但每个子实例使用自己的thread_id；子图显式使用该saver。父工具通过`asyncio.create_task(..., context=contextvars.Context())`建立明确调用边界，子图不继承父图的scratchpad/checkpoint namespace。生产方案需在该边界显式绑定子RunServices并传必要context，本探针未模拟这些应用服务。

子实例驱动函数先await child.ainvoke；A返回真实interrupt后将请求及Future交给模拟审批通道，然后await该Future。外部批准仅让A的驱动函数对A自己的thread调用Command(resume=...)，不重新invoke父图或B；父工具一直await其子任务结果，不把子审批冒泡成父图中断。没有并发恢复同一thread。

B动作设置started事件后等待release_b。模拟用户先等A审批可用和B已开始，然后批准A；等A完成后明确断言B和父均未完成，最后才release_b。实际输出：

```text
TRACE: B started -> A approval ready -> approve A while B running
 -> A action -> A done -> verified A complete before B release
 -> B finished -> B done
PASS: A approved/resumed/completed while B running; parent waited;
      each entry/action once; three checkpoint states complete
```

`.venv/bin/python - <<'PY'`临时stdin程序退出码0。动作及子驱动入口A/B各1次；父结果配对parent-a/parent-b；父、A、B公开checkpoint的next均为空。用事件同步，不靠极短sleep判断并行。

结论：**独立子运行状态+当前子批审批通道，是已验证可行的设计候选。** 批量粒度可继续留在每个Agent自己的工具批次，前台语义仍是父等待最终结果；不需要Agent新进程/后台任务。App可复用角色编译图，独立thread不等于每实例重建图/ModelCatalog/数据库。

相对原Spec需重审：实例记录增加child_thread_id/唯一驱动所有者；当前子批批准通过通道提交，父run持续活动，不能再以session busy拒绝；事件及CLI/Web同时支持运行中审批。子消息/批次由独立checkpoint保存，根必要保存须确认活动子任务收尾。父因子审批重入和恢复协调的部分可简化；一般根审批恢复、停止/关闭、一次性许可、保存失败和同步SDK仍未验证。该候选未因本探针自动成为获批产品架构。

## P5：同一编译子图、整批阻断、拒绝/停止与SQLite（同日继续审阅）

使用一个编译好的子create_agent供A/B并发，显式同一saver、不同thread_id，干净Context中分别绑定子RunServices。子模型首轮各提出Read及action，内部call_id在A/B中相同；A的action要审批、Read默认允许，B不需审批。新增合成Prepare middleware在HITL前写入子state并计数，合成许可Counter检查A批准后只消费一次。

复用项目实际ObservedHumanInTheLoopMiddleware、StopModelMiddleware、StopBoundaryMiddleware、make_model_selection_middleware、RunControl与make_invocation_context；父通过官方ToolErrorMiddleware把明确子ToolException转error结果。子step使用durability='sync'。

四种输入分别在InMemorySaver与AsyncSqliteSaver(':memory:')执行：

| 决定 | 关键实际断言 |
|---|---|
| approve A | B仍运行时A完成；A的Read/action批准前均0，之后各1；合成许可消费1次 |
| reject A action | A的Read执行1次，action/许可消费0；拒绝工具消息同ID/error，A模型继续 |
| stop A | A Read/action均0，二条调用各配对停止error；A不再调用模型；B继续，父收到停止错误后继续 |
| stop root | A同上，B已开始action实际完成；根/子停止标志保留，父没有第二次模型请求 |

所有场景均断言：A/B父委派入口各1次；Prepare A/B各1次，恢复未重跑；同一子图公开checkpoint中的prepared_batch分别为A/B，next/interrupt终结；父结果配对parent-A/parent-B；子RunServices正确绑定/还原，父服务身份不受干净Context影响。

实际输出摘要：

```text
PASS memory/approve
PASS memory/reject
PASS memory/stop-A
PASS memory/stop-root
PASS sqlite/approve
PASS sqlite/reject
PASS sqlite/stop-A
PASS sqlite/stop-root
PASS 8 scenarios; no product mutation, real model, or filesystem business IO
```

`.venv/bin/python - <<'PY'`退出0；过程四条model_call_failed category=cancelled/user_stop来自两后端stop-root中B/父的预期停止门禁，实际模型计数验证没有发送新请求，非探针失败。事件同步控制B释放，不以耗时判断先后。

范围：证明同编译图/独立状态、官方批量阻断、现有停止guard与服务绑定可以组合。Prepare/许可Counter不是085实际Auto/ExecutionAuthorization；未执行真实SessionService/Web/CLI、磁盘SQLite、持久元数据或保存故障/竞争/关闭验证；未证明新方案同步SDK路径。实现仍需全链测试，不能把8组框架探针写成产品功能已完成。

源码核对另发现：CLI的approve分支、EOF/quit先worker.join；Web服务拒绝所有busy审批，前端审批面板受!busy限制。独立子驱动接法必须同时迁移这些消费点，否则可形成等待审批却先等待父结束的死锁。这里是源代码事实，尚未运行应用复现/修复。

## P6：批准与停止时序、生成结果后的checkpoint失败

临时stdin三组探针，真实create_agent/官方HITL及当前项目StopModel/StopBoundary/ObservedHITL：

| 输入/注入位置 | 实际断言 |
|---|---|
| 已形成approve payload，恢复前request stop | 正文/合成副作用均0；同ID停止error；run_stopped，子checkpoint终结，模型仅首次1次 |
| 批准后动作进入，阻塞于受控Event；再停止并释放动作 | 动作/合成副作用各1；保留真实成功ToolMessage，没有未执行标记；停止后无新模型，checkpoint终结 |
| 子模型生成final，InMemorySaver.aput对该final checkpoint抛指定OSError对象 | 原对象经父ToolError的None分支上抛；子completed发布0；无成功父ToolMessage，父模型仅启动1次 |

实际输出`PASS 3 focused probes; fake models, synthetic IO, no product edits`，退出0。保存注入不是磁盘故障；审批payload已形成不等于真实HTTP竞争接收，pending→accepted/invalidated的锁及请求幂等仍须实施验证。副作用为Counter，不是业务写入。

后续审阅纠正：第三组故障只注入A自己的thread，父ToolError被人为配置为原异常上抛；它证明该配置会中止父，不能证明必须中止父，也不能推断根/B保存器不可用。当前Spec改为A局部失败在委派边界收尾后转工具error，B继续；此新合同尚须实施测试。

## P7：A保存失败时B正在等审批，先唤醒再上抛

父图并行A/B：A已生成final，但故障saver等B审批Future登记后才抛指定OSError。A驱动者在上抛前请求根/兄弟停止、唤醒B；B沿其真实HITL停止guard恢复并终结。父使用当前真实_record_tool_call（含await_settled）和官方ToolError，验证收尾等待不会遗留B。

实际trace：

```text
A checkpoint failed while B awaiting approval
 -> driver requests root and sibling stop
 -> B approval wait woken for stop
 -> B stopped and checkpoint terminal
PASS sibling cleanup: real tool await_settled wrapper settled;
original save error retained; waiting sibling woken/stopped;
no B action or next root model
```

退出0；父接收同一个OSError对象，B动作0、父/B模型各1次、B等待Future已完成。停止广播在探针闭包中实现，未使用尚不存在的应用_request_scope_stop；证明接法，不是该生产方法完成。控制来源的新reason字段/故障文案、广播后再登记的竞争也未验证。

后续审阅撤回其默认策略：本探针先选择“根因A而失败”，才产生唤醒/停止B以完成根收尾的需要；这是选择的后果，不是A失败必须影响B的依据。A局部失败应等同父的一次subagent工具失败，不能据本探针广播根失败。只有根本身确定终止时，全体收尾机制才适用。

两条已有应用回归另行运行：

```sh
.venv/bin/python -m pytest -q tests/test_persistent_sessions.py::test_final_save_failure_has_no_completed_and_blocks_uncertain_state tests/test_active_stop.py::test_stop_during_real_write_waits_for_atomic_replace
```

实际`2 passed in 0.54s`。前者对当前根会话finish_run注入失败，检查failed、未确认状态及下一请求被阻断；后者在临时目录真实写入的os.replace入口用Event同步停止，确认已开始替换完成、成功工具消息保留。它们是现有应用证据，不等于新增SubAgent持久化/竞争/界面已接线；没有产品文件修改或真实模型请求。

## 已有证据及未覆盖范围

此前[注册研究第20节](tool-registration-design-2026-10-15.md#20-完整子agent目标与嵌套审批恢复探针2026-10-15)保存单子正常审批、现项目plan_missing、call limit跨恢复/独立实例、只停子/停根的最小探针结果。新P1/P2/P3补多实例/串行/身份，不替代它们，也不声称相应产品已接线。

本轮未验证SessionService多来源审批解析、一次性授权账、Web/CLI协议、SQLite必要保存与关闭竞争、真实进程IO、模型供应商或Windows。Spec验收必须把本探针的动作/入口计数落实为真实应用集成断言；不保证任意副作用exactly-once。

## 一手资料

- [LangGraph中断](https://docs.langchain.com/oss/python/langgraph/interrupts)：同thread恢复、节点重入、多ID映射。
- [LangGraph子图](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)：每次调用状态、继承父checkpoint及工具内动态调用的状态可见性限制。
- 本机源码：`.venv/lib/python3.12/site-packages/langgraph/pregel/_runner.py`、`langchain/agents/factory.py`、`langchain/agents/middleware/human_in_the_loop.py`及`model_call_limit.py`；版本和行为以本轮实际环境为准，升级须重跑正式回归。
