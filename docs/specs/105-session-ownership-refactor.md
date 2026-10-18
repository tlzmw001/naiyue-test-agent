# Spec105：单会话行为归属与应用协调边界

日期：2026-10-18。状态：**105-01—05五片全部完成并验收**；[105-05组合与当前链](105-05-session-lifecycle-acceptance.md#6-实际实施与验收2026-10-18)保存最终证据。[104-01](104-01-application-concurrency.md)现已接后端准入与持久化；104-02 Web及106 E1也已接线，下一步104-03组合验收，105与104的证据分开。

## 1. 顺序与要解决的问题

105迁移前SessionService同时拥有会话集合、共享定义、每会话状态、主任务生命周期和子实例；SessionControlMixin等只是这个对象的文件拆分。停止、审批、子实例创建/收尾都要按sid关联_sessions、_active_runs与_subagent_instances。当前身份检查是有效的，不将组织方式描述为已确认的串会话缺陷。

本轮先把这些真实职责分清，再按新归属实施[Spec104](104-cross-session-concurrency.md)。105负责内部迁移，104负责开放并发；两者的验证结论分开。既有Spec102七片仍为已完成，它提供的typed state、受控入口、原生async、SessionPort及合同测试继续使用；105调整其中“所有内存行为归SessionService”的内部owner。

当前顺序：105五片 → 104三片 → 请求缓存控制M6 → 持久调用用量 → 持久诊断与交付缓存预算 → 103其余界面切片。后四项分别讨论自己的实施合同，不在105预建表、插件接口或通用事件平台。checkpoint修剪、项目模型重试、工具安全重试继续延后；worktree、沙箱、后台/多层子智能体、长期记忆及Windows/PyPI验收仍是独立任务。

目标是让已知功能通过明确边界扩展，减少改动会话底层的频率；不承诺任何未来功能都无需改变底层。worktree改变执行目录、后台子智能体改变任务寿命时，仍要单独审查合同。

## 2. 当前证据与保留的实现

- 对照材料：[本项目/Pi会话所有权分析](../research/session-ownership-comparison-2026-10-18.md)。Pi本地a69bef789bc95abf0acee16f7b4660b70b650bb9，AgentX本地3fcfa0e；分别区分Pi SDK/CLI与experimental server，不把AgentX的SSE关闭当作任务已经取消。
- 本机依赖：langchain 1.4.2、langgraph 1.2.12、langchain-core 1.6.6。现有create_agent、运行context、thread/checkpointer和SessionPort已能表达本次归属，不需要升级框架或新写循环。
- [LangChain Runtime](https://docs.langchain.com/oss/python/langchain/runtime)及[LangGraph Runtime context](https://docs.langchain.com/oss/python/langgraph/graph-api#runtime-context)提供运行依赖注入；不会替项目决定单会话对象、共享客户端的寿命。共享图是否隔离须以本项目根/子scope组合测试核验。
- 当前_arun/aresume均直接await异步图；_afinish_run等待子驱动settle、清理控制并确认保存后释放busy。此顺序、必要操作/观察失败区别、原异常身份、审批整批暂停以及092三次恢复更新均保留。

## 3. 目标持有关系

```text
AsyncApplication（应用入口、拥有操作完成信号及stream producer、最终关闭共享资源）
├─ ModelCatalog / ToolRegistry / 根图 / 角色定义及角色图（共享）
├─ SessionService（多会话协调与SessionPort路由）
│  └─ _sessions[sid] → AgentSession（一个对象只对应一个会话）
│     ├─ SessionState（10项会话事实）
│     ├─ _run → ActiveRun（观察、资源、控制、队列）
│     │  └─ subagent_instances[path]（本次子实例与审批）
│     └─ 配置、运行/压缩/恢复、审批、停止、收尾行为
└─ SessionPersistence / SessionStorage / checkpointer（共享IO实现）
   └─ checkpoint、聊天档案及元数据按原session/thread身份保存

Web RunDelivery：拥有HTTP交付任务、连接、收据和有限重放
Runtime SessionPort：按sid/path读取实际运行所需事实与执行控制操作
```

AgentSession位于application层：它处理会话业务生命周期，调用runtime并借用存储能力。它不实现模型协议、工具算法、SQL、SSE或前端状态；不复制模型客户端、工具定义、编译图、图消息或聊天档案。

| 对象/组件 | 唯一拥有的内容 | 可以借用或读取的内容 |
|---|---|---|
| SessionService | sid登记、共享定义、创建/装载/移除协调、运行准入协调 | 已装载AgentSession；104的总名额判断 |
| AgentSession | 本会话State、当前观察/控制、本会话子实例与待批关联 | catalog/registry/权限设置、共享图与persistence |
| SessionState | 10项会话事实 | 不含瞬时运行字段、客户端、锁、完整聊天或任务对象 |
| ActiveRun | 本次run_id、观察、冻结资源、控制、队列和子实例 | 仅由AgentSession持有至必要保存后释放，不直接序列化 |
| SessionPersistence | 保存/恢复/删除IO、必要事务及保存失败阻断 | 会话拥有者给出的限定快照；不直接改_state |
| RunServices/context | 本次run的显式执行依赖和scope | 不加入整个AgentSession/SessionService，不作为新的会话注册表 |
| Web交付 | HTTP任务、显示投影、连接与收据 | 通过应用API读取/控制，不以页面状态替代执行状态 |

busy、库内running、Web job状态有不同完成时点，继续分别存在；“唯一拥有者”不等于把这些状态强行合并。

## 4. 方法和文件的最终归属

所有路径相对仓库根；本表描述最终目标。AgentSession已在105-01落地，session_execution.py已由105-02接线；子实例和持久化/关闭已分别由105-03/04接线，105-05已增加根/子审批、必要保存及SQLite重开的组合证据，最终全量验收通过。完整105预计涉及十余个生产文件及相应测试/探针，超过8文件，按下节五片分别验收；105-01实际为6个生产文件和12个测试文件。

| 文件 | 迁移后的职责与实际方法 |
|---|---|
| 新增naiyue_test_agent/application/agent_session.py | AgentSession，持有session_id和_state；本会话配置、身份快照、运行状态转换。State不向外暴露 |
| session_state.py | SessionState会话事实及ActiveRun瞬时数据，独立default_factory；不变成第二个行为服务 |
| session_service.py | 保留SessionService名称；创建/装载/移除、共享模型/工具/角色注册、订阅与按sid路由，协调_admit、启动bind_persistence、assemble_runtime和锁外stop_loaded_sessions。删除已转移的本会话实现及全局子实例/观察映射 |
| 新增application/session_execution.py | AgentSession内部执行部分，接管_arun、aresume_tool_approval、_prepare_resources、_on_failure、_cleanup_run、_afinish_run。只拆文件，不是另一套Executor服务 |
| session_controls.py | 从服务mixin改为AgentSession内部控制部分，接管steer/follow_up/request_stop、drain_steering、finish_run_boundary等；创建/登记方法移回协调者 |
| session_subagents.py | 本会话实例准备、单驱动、执行和settle；key改为path，instance中原sid/run/path身份继续校验。共享register_subagent/get_registered_subagents归协调者 |
| session_approval.py | 保留纯解析/校验函数与PendingToolApproval；根和子待批行为归AgentSession。整批恢复协议不改 |
| async_application.py、application/__init__.py、async_persistent.py | 公共API不变；装配共享资源、按sid进入目标对象，关闭顺序以实际任务settle为准 |
| session_persistence.py、session_recovery.py、session_context.py（压缩装配已并入session_execution.py） | 使用拥有者提供的快照/行为；不读锁/可变State；恢复、压缩与档案语义保持 |
| agent_runtime/session_port.py、根/子装配 | 协议仍由SessionService路由，runtime不得import application；方法不因对象迁移而加入通用get_session |
| tests/与scripts/中的实际消费者 | 同片迁移已替换内部入口，保留事件顺序、审批配对、异常身份、停止、保存和作用域断言 |

不为本次迁移新增SessionManager/LiveSession/ExecutionManager等平行服务，不为每个字段建getter/setter。新增内部执行文件是为已有约350行执行逻辑迁移和500行约束服务，不承载空接口。

## 5. 五个实施分片

每片先展示精确实现合同，用户批准后TDD、迁移接线、清理旧消费者和验收，再解释下一片。105-01/02/03已各自记录完整实施与结果；后续片在前片实际落地后核对方法位置并形成自己的实施Spec，不要求一次批准所有代码。

| 顺序 | 本片交付 | 本片结束时的边界 |
|---|---|---|
| 已完成105-01：单会话状态与受控操作 | 实例Map由State变AgentSession；新建/恢复都构造该对象；配置/快照/所有State读写转为本会话操作，见独立Spec | State由对象真实拥有；主执行编排、观察映射和全局子实例暂仍由旧协调者拥有，无重复状态或双写 |
| 已完成105-02：主任务生命周期与控制 | 迁移普通运行/compact/根审批恢复、资源冻结、失败和finish；当前RunObservation归对象；移除服务_active_runs；控制方法在同一对象中读写 | 主任务从准入到必要保存由本会话驱动；全局准入仍由服务协调，不创建每会话图 |
| 已完成105-03：子实例与审批归属 | 子实例Map移入所属对象，移交创建/driver/内部审批/停止/settle；共享角色定义/图留服务 | 子审批直接定位本会话path；旧(sid,path)全局Map删除，一层/前台/批量审批保持 |
| 已完成105-04：ActiveRun及资源接线 | 新建、冷装载、恢复、context/compact、移除、关闭追通新拥有者；消除外部私有穿透和资源关闭歧义 | IO与事务仍独立，创建/保存/删除失败可观测；应用只在全部任务settle后关共享客户端/库 |
| 已完成105-05：组合验收与当前地图 | 验证根/子、停止/取消、压缩、保存失败和真实SQLite重开；删除旧内部别名/失效mixin与消费者，更新当前入口图 | 105收尾；基于实际owner更新104-01精确实现合同，然后再实施并发 |

这里的暂时保留是不同资源的分片迁移，不是保留新旧两条相同行为。105-01不把同一个State复制两份，105-02不保留两套arun，105-03不同时维护两张子实例权威表。每片都应可运行、通过现有测试，且不以“下一片会修复”为由交付红灯。

## 6. 锁、准入与身份

采用现有短锁模式，不引入通用锁服务、重入锁或任务队列。协调者锁保护登记和跨会话操作；AgentSession的短锁保护本会话状态。固定嵌套方向为persistence协调锁 → SessionService登记/准入锁 → AgentSession状态锁；并非每次都要取三把锁，禁止反向取得。

状态锁内不await，不做文件/模型/数据库IO，不投递外部监听器。配置选择的操作锁与短状态锁职责不同：串行化本次配置提交，保持校验 → DB成功 → 更新内存；IO期间不能持有短状态锁。子future唤醒与控制消息flush在状态锁外执行。已有内部观察缓冲接受操作按当前真实实现核对，不把排队误认成外部handler投递。

服务的_admit是有用途的协调边界：登记锁内定位目标AgentSession并调用其admit_run，完成该会话合法性检查和busy/run安装；104在同一外层边界加入全局名额检查。不能“先检查总量、放锁、再启动会话”。105不提前增加上限配置或开放Web并发。

删除/恢复沿现有persistence协调预留，不能把deletion_guard拆成先检查、退出锁、再标记恢复；105各片保持原预留原子性。关闭先拒绝新入口，再请求各活动会话停止，等待根/子driver、关键IO和finish，最后关闭共享资源。不新增会话卸载策略或自动TTL。

session_id、thread_id、run_id、agent_path、approval_batch_id、origin_run_id和执行许可绑定均保留。对象引用不能代替旧请求校验。已移除对象即便仍被某个调用持有，也不能绕过协调者重新开始任务。

## 7. 最终目标正常链：A发送并启动一个子智能体

示意身份：session-a/thread-a/run-a，子path由现有subagent_identity生成。根/子运行归属已在105-03接线，服务在既有调用回调中只解析共享角色并路由；对象自行创建/审批/停止/settle，不再传临时subagents参数。持久化/装载/关闭已由105-04追通；当前准入返回(session,ActiveRun)，控制关闭与必要保存后release保持两个阶段。

```text
Web.start → RunDelivery → AsyncApplication.arun(session-a)
  → SessionService：定位A；协调准入
  → A.admit_run：校验pending/busy等并登记run-a
  → A._arun：persistence.start → 冻结本次资源 → 共享根图(thread-a)
      → SessionPort.resolve_model/active_tools(session-a,root) → 路由A
      → subagent工具 → A.prepare_subagent_instance → A的子实例[path]
      → 共享角色图(child_thread) → 子结果返回根图
  → A._afinish_run：子任务settle → 关闭控制窗口 → persistence.afinish
  → 保存确认后解除A.busy → 终局事件与Web交付
```

如果子智能体内部需要审批，A持有该实例与future，主会话显示审批；提交通过应用按sid/path/origin_run定位，恢复该子批次，父任务继续等待。不是新建根run，也不重新运行Auto。根前置审批则按原合同收尾并保存pending，之后用户提交开启新的审批恢复run。

## 8. 失败调用链：工具异常后必要保存也失败

1. 共享图按现有ToolError/控制边界产生结果或抛原异常；105不扩大工具错误转换范围。
2. A._on_failure按当前规则检查部分工具状态，必要时标manual_review_required；不把B标成需要人工核对。
3. A._afinish_run停止并等待A的子任务、清理A的控制、执行必要保存。若保存再失败，保留原运行异常，记录保存次异常并让持久化阻断该会话；只有保存错误时，它是对外失败。
4. 必要收尾结束后释放A.busy。不能因观察失败覆盖原异常，也不能因busy释放就宣称档案保存成功。共享数据库本身故障仍可能影响其他会话，不承诺隔离基础设施故障。
5. Web按原错误/收据合同结束交付，不重放不确定副作用；重启仍走092/101的显式恢复与档案链。

## 9. 接口、配置与数据影响

- AsyncApplication/AsyncPersistentApplication被CLI/Web使用的公开方法及HTTP/SSE格式不变。SessionPort保留既有实际方法，不新增可变state访问；内部SessionService方法允许按实际消费者迁移，不能以可import为由保留兼容。
- DB schema仍为8，原工作区标识、整库占用锁、checkpoint与聊天档案格式不变，无数据迁移或旧库重绑定。105保持SessionPersistence和Web现有全局活动门禁，待104统一升级。
- 不增加配置项、依赖、供应商调用、账户或密钥，不改真实配置文件。104的max_active_runs默认10已经确认，但在104接线。
- SessionState显式字段映射继续使用；锁、future、run资源、整对象不写入SQLite，不用asdict把瞬时属性一并持久化。
- 新对象增加的是本会话状态/行为引用，不复制共享模型和图。没有基准数据，不承诺性能提升；正确性和可追踪性是本次验收目标。

## 10. 验收和回退边界

每片至少覆盖本会话正常/拒绝/失败、另一会话不受错误控制、持久化与事件顺序；原有审批配对、stop/steer/follow-up、压缩、根/子SessionPort断言必须保留。测试选择具体到每片Spec，不能把当前15项state/port通过当作105实现证据。

最终组合矩阵：普通run/compact/根审批恢复；根/子活动停止；并行子A待批时B仍执行且A可独立恢复；旧run/旧batch拒绝；配置DB失败不变内存；保存失败保持阻断和原异常；三次恢复更新/聊天归档不重放工具；切换页面不停止原任务；应用关闭完整settle；同一个共享图/目录与独立状态身份同时成立。

实施时先写行为红灯，确认失败原因后迁移实现。最终执行：PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q；前端在frontend目录运行npm test -- --run及npm run build；另核查Python编译、依赖、500行、文档链接与git diff --check。105不修改前端行为，但发布前完整构建/回归应保持。

迁移本身不改变外部数据格式；如需回退，仅针对获准代码提交回退，不还原或删除用户SQLite/工作区文件。105-01/02/03已完成状态及根/子运行归属验收，尚未提交；其余两片未实施，不宣称105目标架构已全部验收。


## 11. 105-05收尾接线与当前主链

[105-05](105-05-session-lifecycle-acceptance.md)用真实图/临时SQLite覆盖根待批→恢复→子待批→根必要保存的五类结果，以及根待批冷恢复、子待批关闭重开和压缩/配置/档案重开。控制窗口关闭与_run释放仍分开；子审批是同一driver的多段子run，每次暂停先有interrupted事件。

唯一生产清理是删除失去额外消费者的Service._request_scope_stop包装；public request_stop直接定位Actor.request_scope_stop，签名及原身份/默认user原因保持。Service三个mixin和Actor四个内部部分仍有实际类消费者，作为同一对象的文件拆分保留；不创建Manager，不复制图、消息或客户端。

当前链：公开应用→Service._admit(session_id)→Actor.admit_run/ActiveRun→Actor._arun/aresume→原共享根/角色图→Actor.settle/assert→close_control_window→Persistence.afinish(限定快照)→release_run→终局。新建/冷装载借用同一依赖，恢复仍走三次官方状态更新，App关闭依次等待操作/producer、model、saver、storage。Runtime仍只认识SessionPort与显式RunServices。

旧服务全局观察/子实例Map、State瞬时字段、同步App/IOCall及旧装配/恢复IO入口不再有实际消费者。历史Spec和课堂保留时点，不作为当前入口。最终验证结果见105-05；105完成不代表104的Web/Persistence全局门禁已去掉。
