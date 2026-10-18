# Spec104：共享工作区内的跨会话并发

状态：104-01/02/03均已实施验收。2026-10-18。当前证据见[104-03收尾](104-03-concurrency-acceptance.md#6-实际结果与104收尾)；下文实施前安排保留来源边界。

前置依赖：用户已确认先完成[Spec105会话行为归属](105-session-ownership-refactor.md)五片，再实施本Spec三片。目标为SessionService协调多会话、AgentSession拥有本会话状态/运行/控制/子实例。本Spec保留用户行为与默认10的决定，按105目标更新归属；105收尾后再用实际代码形成104-01精确实施合同，不直接按旧owner修改。105-01—05及104三片已全部验收，默认10根并发已接线；下一步讨论[M6](107-model-request-prompt-cache.md)。

来源：Spec102原问题第7项、future-work进入103前的盘点，以及本轮共享目录/文件占用/Claude与worktree讨论。用户接受共享真实文件与现有Write/Edit busy，并明确max_active_runs默认10；本Spec建议先完成同服务并发，worktree单独设计。其他行为和分片仍供审阅，不把讨论当成实现授权。

104-01现已按[实施合同](104-01-application-concurrency.md)接线应用额度与按会话持久化；[104-02](104-02-web-session-concurrency.md)现已接Web与106 E1；[104-03](104-03-concurrency-acceptance.md)组合验收已完成。以下实施前问题保留来源边界，当前链见三片实际结果。

## 1. 目标与边界

- 一个应用内，不同会话可以同时运行；同一会话仍只有一个活动主任务。
- 复用现有Agent图、独立thread、RunServices、根/子审批和归档链，不为每个会话另建应用/模型客户端。
- 同一服务继续绑定同一个workspace_root和同一个工作区SQLite；数据库整库进程占用锁保持。
- 文件工具、Bash及子智能体继续访问共享真实目录。Write/Edit执行期目标busy、匹配与提交检查保持；不加入文件排队、整任务文件锁、跨进程锁或自动合并。
- 不增加worktree、沙箱、后台子智能体或多层委派；不增加项目模型重试和执行失败后的工具安全重放。
- 上限只限制主任务数量，不宣称限制整棵Agent树的模型/工具总并发。单批工具max_concurrency及串并行合同保持。

## 2. 104实施前主链与两个服务级门禁

当前正常链：WebService.start → RunDelivery._launch → DeliveredRun/_produce → AsyncApplication.arun/_execute → SessionService.arun/_admit → AgentSession._arun → SessionPersistence.start → Runtime/图 → AgentSession._afinish_run → SessionPersistence.afinish → Web任务收尾。

104实施前限制：

1. RunDelivery.active是单个任务；_launch、control、WebService.recover及close按它判断。
2. SessionPersistence._active是(session_id,run_id)单值，_recovering是session_id单值；start、恢复/删除/安装及finish依赖这些值。
3. AgentSession._run持有ActiveRun；busy由_run存在推导、run_id来自记录，服务按sid路由；DeliveredRun.observe已按session_id/run_id过滤事件。
4. AgentSession.close_control_window只关闭控制；_run保留到必要保存后release_run，因此104可用is_busy统计包含保存的活动名额。不要统计controls_open或应用操作完成信号；旧Service._active_runs已删除。
5. 前端切换会话取消的是显示订阅；后端任务独立持有。现有草稿按会话保存，发送成功后才清空。

## 3. 建议的用户行为

| 情况 | 结果 |
|---|---|
| A运行中，B空闲且还有名额 | B立即开始，A继续 |
| 同一A再次启动普通任务/compact/另一恢复任务 | 拒绝同会话重入，不创建第二个主任务 |
| 普通执行名额已满 | 返回run_capacity_exceeded，保留草稿/待审批状态，不排队 |
| 根前置审批暂停 | 当前执行清理、保存完成后释放名额；保留该会话待审批状态 |
| 根审批批准或拒绝后需要继续图 | 重新准入；满额时不消费审批决定、不改变许可，稍后可再提交 |
| 父任务仍在等待子智能体审批 | 父任务仍占名额；提交这个子审批属于原任务控制，不再申请名额、不重跑Auto |
| 请求停止正在运行的任务 | 按会话/run定位，立即接受控制；直到清理和保存结束才释放名额 |
| 结束已经暂停的根审批 | 复用现有纯停止路径，允许在名额满时收尾；不启动新的模型/实际工具 |
| A运行，恢复/删除空闲B | 仅检查B的运行/恢复/历史操作，不因A运行而禁止；B的关联子thread仍按原归属清理 |
| A保存失败 | A保持未确认并受阻，释放执行名额；健康B继续。整个数据库不可用属于共享基础设施故障，不能承诺B仍可保存 |
| 关闭服务 | 禁止新准入，停止并等待全部根/子任务与必要保存，再关闭模型及数据库 |

用户确认在现有settings.toml总配置顶层增加max_active_runs，默认10，严格正整数。缺省使用10；值非法记录警告并回退10，沿已有额度配置的降级方式，不静默使用非法值。启动时读取，不热更新；不修改用户真实配置，只更新获准范围内的样例。10限制根任务，不能解读为整个应用最多10个模型/工具请求。

名额由SessionService在登记/准入短锁内检查和登记，通过AgentSession.is_busy读取其拥有的busy；合法性和本会话busy/run安装在AgentSession.admit_run内完成。外层准入锁覆盖总量判断与安装，不能放锁后再启动。无需第二套Semaphore、排队器或ExecutionManager。compact也占执行名额。纯停止根审批只豁免总额检查，同会话busy检查仍有效；其短暂收尾也保守计入busy，不能因此提前释放正在停止的普通任务。

## 4. 修改的类、字段和方法

### 4.1 应用准入与配置

- tools/settings.py：ToolSettings增加max_active_runs；load_tool_settings接读取、校验、日志回退。虽然旧类名为ToolSettings，它已是storage/permissions/subagents等启动总配置入口；本片不重命名或新增平行配置加载链。
- application/errors.py：增加RunCapacityExceededError，区分同会话忙与应用名额满；__init__.py按当前方式重导出。
- SessionService.__init__：接收启动准备的max_active_runs，保存在服务实例；不改变arun/审批等公开调用签名。
- SessionService._admit与AgentSession.admit_run：服务协调总量与登记；对象完成本会话状态/决策校验和busy/run安装。104由服务在准入锁内统计额度，将capacity_available这个已确定事实交给admit_run；对象先按原顺序校验本会话，再检查额度并安装run。不为总量检查重复调用一遍本会话校验，也不传通用回调。105不提前添加这个尚未使用的参数。满额发生在observation.start、mark_running和模型/工具调用之前，不改变待审批决定和临时授权；内部函数精确位置以105收尾为准。
- AgentSession._cleanup_run/_afinish_run：保留控制关闭与子任务settle顺序；仅在必要清理/保存完成后解除busy。错误收尾也必须释放本会话名额，不能替其他会话清理。105后不恢复服务级_active_runs作为额度来源。
- build_async_local_application/open_async_persistent_application：沿现有settings对象把上限注入同一个SessionService，不在Web复制额度判断。

### 4.2 持久化按会话协调

- SessionPersistence._active改为session_id → run_id；_recovering改为session_id集合。沿用现有短锁与每会话_history_lock，不新建协调框架。
- start：只阻止同session活动/恢复/受阻；短锁内预留这个session/run，锁外mark_running，失败按身份撤销并保留原异常。执行链等待成功后才执行图。
- afinish：按session/run核对所有者，只保存及释放本条映射；失败只把本session加入_blocked。
- arecover/arestore/_delete/_install_session：检查/预留/释放本session，不因其他session执行或恢复而禁止。
- SessionPersistence._read_history：同步改为按session查询活动与恢复；实际三次恢复更新、缺失ToolMessage补齐及旧任务终止合同保持。
- SessionStorage表结构和工作区绑定保持；104-01组合验收发现写事务升级冲突，save_chat_updates/reorder/delete/finish_run改BEGIN IMMEDIATE。已有元数据RLock、官方AsyncSqliteSaver短操作锁与SQLite写协调继续使用。不在整次模型/工具调用期间持有数据库锁，不新增数据库队列。

### 4.3 Web多任务交付与控制

- RunDelivery.active改为active_by_session；runs仍按run_id保存，latest仍按session_id保存。
- _launch：重复成功收据优先返回；只拒绝本session已有Web任务；新任务调用应用层准入。live_child审批从目标session定位父job，再校验origin_run_id，不重新启动父任务。
- _produce：完成、失败、取消均只移除属于该job的活动映射；原运行异常与必要保存优先级保持。
- 准入前明确拒绝（例如额度满）且没有创建run时：清理pending_receipts与活动占位，不留成功收据，稍后同request_id可以重新申请。已开始/结果未知的任务仍保留现有防重放边界，不借此增加安全重试能力。
- control：通过run_id取得job，再核对active_by_session[job.session_id]及当前身份；停止/Steer/Follow-up不会发给另一个会话。
- forget_session：只移除目标会话的任务、索引、收据；不能清空其他活动任务。
- close：等待全部活动交付任务；应用已有_close_resources负责停止全部已装载会话和等待_owned_operation完成信号和本应用创建的producer；不等待外部调用方后续工作。即使关闭报错也完成交付任务收尾，保留主异常并记录次异常。
- WebService.recover：移除全服务活动检查，保留本session的运行/恢复检查及checkpoint/action校验。
- web/errors.py与frontend/src/api.ts：映射RunCapacityExceededError → HTTP409/run_capacity_exceeded；session_busy改为仅描述当前会话忙。保留现有草稿、审批卡片与失败提示流程。
- DeliveredRun.observe及前端watch当前已有session/run过滤，优先保持实现，通过并发测试验证；不引入浏览器为所有会话同时订阅的后台总线。

原现状方案涉及12个生产文件、配置样例及测试/文档，超过8文件。105之后单会话执行文件归属改变，实施时据其实际落点更新文件清单；本Spec不再承担105的对象迁移，也不新增运行服务、第三方依赖、数据库表或持久状态字段。

## 5. 改后正常数据流

为便于说明，下例显式配置max_active_runs为2，不是默认值：同一工作区，A(thread-a)、B(thread-b)、C(thread-c)。实际默认10时，第11个普通执行请求才因额度被拒绝。

```text
A发送 → A的Web job → 应用登记A busy → DB登记A/run-a → 图(thread-a)
B发送 → B的Web job → 应用登记B busy → DB登记B/run-b → 图(thread-b)
                                       ↓
                         每个run使用自己的控制/事件/子实例
                                       ↓
                         checkpoint按thread分别保存
                                       ↓
                         finish按session分别归档/收尾
C发送 → 应用发现2个busy → 409名额满；无run/checkpoint/模型调用
```

此时应用会话A/B的busy都为true；Web分别持有A/B两个job，持久化映射分别持有A/run-a、B/run-b。共享图不共享messages；模型客户端可以复用，请求配置沿当前调用准备。

A遇根审批：图返回interrupt → 保存A待批状态 → A解除busy → C可开始。批准A时如B/C仍运行，返回名额满，A审批仍在；有空位后再批准，产生新的执行run继续原thread。

A的子智能体Ask：父图调用仍在等待 → A保持busy；用户提交审批 → 在原run中交给对应子驱动继续，另一个会话B不参与这次恢复。

## 6. 失败数据流与文件边界

- A的工具busy：工具执行包装转换为A的ToolMessage，A模型决定后续；B既有执行不回滚、不停止。
- A正常完成但finish保存失败：A标记保存未确认/受阻，Web显示未确认而非完成；A释放执行名额，不清除B的活动登记或收据。B正常保存必须有确定性测试证明。
- A停止：只向A本run及其子作用域传播；B继续输出。A在子任务/进程清理或保存未结束时仍占名额。
- B恢复/删除失败：只释放B的恢复预留，保留B原失败合同，不把A标记为恢复中或删除A的thread。
- 两个会话顺序修改同文件仍可能互相影响；busy仅覆盖重叠Write/Edit，不是读取后到编辑前的长期锁。现有Edit旧文本匹配/提交复核保持，不许把它描述为跨会话事务。

## 7. 实施顺序与每片交付

前置：105-01—05全部收尾，公开应用/持久化/审批合同回归通过。下列三片再按实际AgentSession/Service方法位置逐片出实施合同；不能跳过前置依赖先改旧服务运行链。

1. **104-01 应用与持久化**：配置/异常/准入，两个工厂接线，持久化及恢复按会话协调；用真实临时SQLite和事件同步模型验证两个root重叠、同会话拒绝、保存隔离及满额零调用。Web此片仍可能保持旧全局门禁，不能宣称浏览器已支持。
2. **104-02 Web与界面接线**：活动job映射、控制/审批/恢复/删除/关闭、失败收据清理与文案；HTTP/SSE和前端验证会话切换、草稿与审批保留。
3. **104-03 组合验收**：真实图+SQLite+Web，根/子审批、停止、保存失败、重开恢复、文件busy及同请求幂等；只修本Spec闭环必要缺陷，同步当前文档并完整回归。

各片在用户确认后逐片TDD，先展示正确失败，再最小实现和全量回归；不为了旧全局门禁测试保留同步或单活动兼容分支。旧测试要迁移准入范围，保留原异常身份/保存/配对/控制断言。

## 8. 验收与证据边界

必验：A/B真实模型节点同时到达事件栅栏；各自thread/消息/用量事件不串；同会话重入；默认10可准入且第11个拒绝、显式小额度及非法配置回退10；收尾保存期间名额未释放；限额拒绝无模型/工具/checkpoint写、同request_id稍后重试；根审批释放/重新准入；子审批不重新申请；满额停止可用；A停止不影响B；A保存失败B可结束；其他会话运行时恢复/删除空闲会话；重复成功收据不重复执行；断开SSE继续；关闭等待全部清理；临时库重开恢复；共享Write/Edit busy不变。

事件同步/屏障控制先后，不使用紧sleep窗口；测试不接真实供应商，不修改真实配置/用户库。单进程两个session不等于两个应用共享同库，原storage_in_use合同须保留。

分片完成时运行目标测试与完整后端、前端、构建、diff/500行/接线检查。计划命令：

- `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q`
- `npm --prefix frontend test -- --run`
- `npm --prefix frontend run build`
- `git diff --check`

本轮只复跑已有内存服务隔离用例：`tests/test_application_session.py::test_same_session_busy_rejected_other_session_runs_and_failure_releases_gate`，1 passed in1.31s。它使用假Agent，只证明既有内存准入分支，不证明本Spec持久化/Web并发已实现。未跑全量、不新增生产验收结论。

## 9. 来源、风险与回退

- 当前仓库主链与本机安装的AsyncSqliteSaver源码是实施依据：saver实例有async锁，checkpoint带thread_id/namespace；不修改框架实现。
- [LangGraph官方持久化](https://docs.langchain.com/oss/python/langgraph/persistence)：checkpoint以thread划分。该事实不能自动证明本项目全部共享对象都支持并发，须按第8节验证。
- Pi本地源码的两条独立链及可迁移部分见9.1；AgentX本地ConversationAppService.chat → handler.chat及ChatSessionManager按sessionId登记中断状态。只借鉴会话范围，未据此宣称它们使用本项目的SQLite协调或相同上限。
- Claude官方共享目录/worktree与Edit行为已在对话核对。本片复用项目现有工具检查，不宣称存在Claude全局文件锁。
- 主要假设：单应用、有限根任务、现有模型适配器与图中可变对象按请求/会话隔离。若并发验收发现共享状态串扰，先用最小测试定位真实所有者并修正；不直接通过每会话重建整套应用掩盖问题。
- SQLite写入会短暂排队，不能承诺写并行或高负载吞吐；当前不为无规模证据更换数据库或加写服务。
- 无数据库迁移。必要时将max_active_runs设为1并重启，恢复单任务容量；新代码仍保留正确的按会话状态。回退代码前先停止活动任务，不删除会话数据。

### 9.1 Pi定向源码核对与本项目取舍（2026-10-18）

核对本地Pi提交a69bef789bc95abf0acee16f7b4660b70b650bb9。以下区分现行SDK/Agent路径与独立server包，不能把接口适配误说成已经接通的同一条生产链。server/package.json标注为experimental，版本0.84.2；只参考职责，不引入其依赖或运行时。

**SDK路径**：coding-agent/src/core/sdk.ts的createAgentSession创建独立Agent和AgentSession；AgentSession.prompt对本会话isStreaming分支报错或按明确选项提交Steer/Follow-up。agent/src/agent.ts的Agent.prompt/runWithLifecycle检查实例activeRun，不是所有会话共用一个activeRun；finishRun在执行器及其被await的事件处理完成后清理。AgentSession._handleAgentEvent在message_end调用本sessionManager.appendMessage；SessionManager默认使用该会话的JSONL文件。它不需要本项目的共享SQLite收尾协调。

**server路径**：server/src/server.ts的handleRequest → LiveSessionManager.executeCommand；create/attach经acquire从service.createSession/openSession取得PiSessionRuntime；prompt/steer/abort按command.sessionId找到live.runtime再调用相应方法。具体runtime实现由PiServerService提供，不能直接声称它一定是上述AgentSession；server测试使用内存测试runtime。

| Pi实际机制 | 本项目对应与决定 |
|---|---|
| LiveSessionManager.liveSessions为按ID的Map | RunDelivery按session持有活动job，SessionPersistence按session持有run；二者职责不同，不合并成新的大管理器 |
| openingSessions为按ID的Promise映射，同一会话装载复用进行中的申请 | 复用既有SessionPersistence._history_lock与恢复预留，装载同会话串行、不同会话独立；不额外加Promise/Future缓存 |
| PiSessionRuntime合同要求冲突操作拒绝而非自动排队 | 同会话忙继续明确拒绝普通任务；Steer/Follow-up作为既有控制入口；整体满额也不排队 |
| prompt尚未返回时仍处理steer/abort | start_lock仅用于短请求登记，不覆盖模型执行；停止和子审批进入原任务控制，不能等待空执行名额 |
| progress/snapshot只发给对应live.connections | 复用DeliveredRun.observe按session/run过滤；不把全局活动对象用于停止或事件归属 |
| disconnect后仅在无连接、无进行中操作且runtime idle时dispose | 断开浏览器/SSE不停止活动任务；本项目保留现有会话装载策略，不额外建设Pi式空闲卸载 |
| close遍历所有已装载runtime释放 | Web关闭等待所有job，应用停止全部根/子作用域后再关闭共享资源 |
| Agent保持activeRun直到await的处理完成 | 本项目按busy保留名额至必要清理/保存完成，不能看到最终消息或run_finished观察信号就提前释放 |

在上述PiServerOptions、LiveSessionManager及SDK调用链中未发现max_active_runs等全服务根任务总上限；宿主service仍可能自加策略。默认10是本项目用户确认的应用额度，不称Pi默认，也不为对齐Pi而删除该配置。

已阅读server/test/sessions.test.ts中同会话busy、prompt期间steer/abort、多个连接共享单一runtime、断开后继续及重开等测试。当前本地未安装Pi的Vitest入口，本轮只做源码与测试断言核对，不安装依赖、不宣称这些测试已运行，更不作为本项目并发验收证据。本Spec实施时将相应行为迁为项目自己的真实图/临时SQLite/HTTP确定性验证。

## 10. 后续worktree方向

worktree是项目归属与会话执行目录的额外能力：需要创建/恢复/目录标识/本地环境/改动整合和清理的完整设计。预计独立4—5片；不在本Spec提前添加execution_root、空接口或管理器。非Git目录继续共享模式。后续引入worktree不应替代本Spec的任务准入和按会话控制。


## 11. Spec106的定向补充

[Spec106](106-concurrency-events-rate-limits-and-observation.md)的E1放入104-02：沿现有订阅入口加可选session范围，保留全局消费者、publisher快照、顺序/故障合同，不新增总线。R1放入104-03：离线注入根/子429及Auto回退、停止/保存隔离，不新增重试或模型限流器，实网另定预算。P1保留观察CPU基线与同ID原地修改正确性，出现瓶颈证据后单独设计，不阻挡105/104。接口扩展和精确接线在对应片批准后实施。
