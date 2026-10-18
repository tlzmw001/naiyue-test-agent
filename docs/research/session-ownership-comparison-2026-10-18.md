# 会话架构对照：当前项目与 Pi

日期：2026-10-18。性质：源码对照与设计依据。用户确认先做[Spec105](../specs/105-session-ownership-refactor.md)，105-01/02及[105-03](../specs/105-03-session-subagents.md#8-实际实现与验收2026-10-18)已完成State与根/子生命周期移交；共享角色/图仍归服务，105-04/05待讨论。104默认10且依赖105收尾；下文现状/候选图为迁移前快照。ActiveRun建议不视为Pi全量同构，见105-03评估。

当前入口更新：[105-05组合验收与地图收尾](../specs/105-05-session-lifecycle-acceptance.md)已接，最终全量验收通过。以下架构图与候选保留迁移前研究时点；当前为Service登记→AgentSession(State/ActiveRun)→独立Persistence，104仍待逐片确认实施。

## 1. 判断与比较边界

针对未来会话功能扩展和理解成本，建议借鉴Pi的单会话行为归属：多会话协调与单会话运行生命周期分开。当前项目保留Python/create_agent、共享编译图、共享模型目录/工具定义，以及独立存储/恢复/显示职责。它们与是否设置单会话行为对象是独立选择。

这不是“现有架构不能扩展”或“Pi每个会话的Agent一定更省资源”。当前service＋state能够实现104；更清晰的单会话封装是另一项有收益也有迁移成本的内部设计选择。之前“不必为104新增AgentSession”指最小并发功能不受阻，不代表现状已是长期最佳。

Spec102确认SessionService是内存状态转换的拥有者、SessionState是受控数据。若采用本文方向，会调整这个已经确认的内部职责边界，须先讨论具体所有权/迁移方案，不能当作bug修复直接改动。

## 2. 一手证据范围

- 当前项目：SessionService、SessionState及controls/approval/subagents三个mixin；AsyncApplication与持久化工厂；SessionPersistence、恢复/归档；SessionPort、图装配、RunServices和调用context；Web活动任务机制。
- 本地Pi提交a69bef789bc95abf0acee16f7b4660b70b650bb9：core/sdk.ts、agent-session.ts、agent-session-runtime.ts、agent-session-services.ts、session-manager.ts；agent/src/agent.ts；server/src/sessions.ts及types.ts。Pi源码无本地跟踪改动，仅有未跟踪docs目录；本轮不修改参考仓库。
- Pi传统SDK/CLI与实验server分别追链，不假定server的PiSessionRuntime一定由传统AgentSession实现。server包标为experimental，不以接口/测试替代生产成熟度证据。
- AgentX本地3fcfa0e：ConversationAppService.chat → handler.chat及ChatSessionManager的按sessionId控制登记。只说明集中服务＋请求/会话上下文也是实际选择，不能由其SSE关闭推出底层任务已经取消。
- [LangChain Runtime官方文档](https://docs.langchain.com/oss/python/langchain/runtime)：调用时可通过context向工具/中间件注入依赖。[LangGraph Runtime context](https://docs.langchain.com/oss/python/langgraph/graph-api#runtime-context)：执行依赖与图state可分开。因此框架没有强制本项目使用全局SessionService管理全部单会话行为。

## 3. 当前对象持有关系

### 当前项目

```text
AsyncApplication
├─ ModelCatalog / ToolRegistry / 根编译图（共享）
├─ SessionService（一个，管理多个会话并驱动单会话行为）
│  ├─ _sessions[sid] → SessionState（配置、队列、busy、待批等）
│  ├─ _active_runs[sid] → RunObservation（本次控制、观察）
│  ├─ _subagents[role] → 角色定义和共享角色图
│  └─ _subagent_instances[(sid,path)] → 子实例/驱动/审批
└─ SessionPersistence / SessionStorage（共享持久化实现）
   └─ checkpoint与聊天归档按session/thread保存
```

Web另外持有DeliveredRun等显示和交付状态。它们不属于会话图状态的权威来源，也不因为引入单会话对象就应被塞进去。

### Pi的SDK/CLI路径

```text
宿主 / AgentSessionRuntime（持有当前会话及cwd相关服务）
└─ AgentSession（当前一个会话的行为）
   ├─ Agent（该实例消息、模型/工具和当前执行）
   ├─ SessionManager（该会话JSONL/消息树）
   ├─ SettingsManager / ResourceLoader / ModelRuntime
   └─ 控制队列、扩展、压缩/重试等生命周期逻辑
```

sdk.createAgentSession创建Agent及AgentSession；AgentSession.prompt检查本会话运行状态；Agent.runWithLifecycle持有实例activeRun并在执行及被等待的处理结束后释放。AgentSessionRuntime.switchSession/newSession会停止并settle旧会话，发shutdown并dispose，然后创建/应用新runtime。不能把这个CLI中的“替换当前会话”直接照搬成Web切换选中会话，否则会停止本应继续的另一个任务。

Pi的独立server路径：LiveSessionManager.liveSessions[sid] → LiveSession；后者保存本会话runtime接口、客户端连接、操作计数和释放状态。create/attach经acquire调用service取得runtime；prompt/steer/abort按sid路由。此处是多会话宿主，具体runtime由接口实现者提供。

## 4. 正常与失败链路的实际差别

### 4.1 运行一次任务

当前：AsyncApplication.arun(sid) → SessionService._admit查_sessions并登记_active_runs → _arun准备资源 → ainvoke_agent_run传thread与context → 编译图 → _afinish_run settle子任务/保存 → 解除busy。

Pi SDK：AgentSession.prompt → 该实例的Agent.prompt/runWithLifecycle → 循环及事件 → AgentSession._handleAgentEvent把message_end记录到该SessionManager → 执行/续接settle后回到空闲。

我们把运行依赖、持久化和显示明确分开，恢复节点由框架保存；Pi更容易从当前会话对象追到模型、工具和资源。这两项优点可以同时保留。

### 4.2 停止一个有子任务的会话A

当前SessionControlMixin._request_scope_stop需要在同一对象中关联：_sessions[A]、_active_runs[A]、_subagent_instances中sid为A且root_run_id匹配的实例，然后请求各control、唤醒待批future、保留队列。

当前代码有明确身份检查，不能据此宣称存在串会话bug。但调用者和维护者必须理解几张映射之间的关系；同类会话范围关联也出现在prepare_subagent_instance和validate_approval_submission。

候选单会话封装的收益：A的行为对象直接拥有A的state、当前运行记录和子实例集合；停止/审批/settle在这个范围内完成。run_id、agent_path、许可绑定与旧请求校验仍须保留，不能把对象引用当授权或幂等证明。

### 4.3 保存失败

我们：SessionPersistence.afinish检查保存状态，失败阻断本会话；_afinish_run维护原异常/次异常、子任务清理与busy释放顺序。Pi的对象布局不能替代本项目的checkpoint/pending writes/归档事务合同。

即使单会话对象采用Pi式组织，仍只向持久化组件提供必要快照/请求；不能把SQL、checkpoint更新、聊天归档、Web SSE一起搬入对象，也不能直接序列化其锁、future、运行控制或共享客户端。

## 5. 从后续真实场景比较

| 场景 | 当前布局 | Pi式单会话边界的收益与限制 |
|---|---|---|
| 会话切模型、工具、权限模式 | 已有按sid受控入口，下一模型/批次读取，能力不受阻 | 入口更直观，但不能因此取消当前取值时点、冻结与审批许可校验 |
| 跨会话并发 | 104改全服务单值门禁即可闭环；不必先换对象模型 | 单会话重入/收尾归属更直接；全局10个名额和SQLite写协调仍由宿主管理 |
| Worktree/同应用不同执行目录 | 每个state已有root，但项目归属/执行目录在多处混合 | cwd相关资源的创建/销毁归属更清楚；仍须拆项目身份与执行目录、调整DB/Web绑定和代码生命周期 |
| 动态注册工具、不同活动工具集 | 共享ToolRegistry＋会话活动名字已经适用，简单且不重复定义 | 不应把共享定义复制到每会话；以后会话私有插件进程、订阅和资源才适合归单会话所有 |
| 子Agent审批、停止、未来实例续接 | 多张全局映射按sid/path关联，已接一层委派和审批 | 父会话拥有子实例更易理解和清理；不自动获得嵌套恢复、多层委派或进程隔离 |
| 会话装载/卸载、可关闭资源 | 当前状态和任务生命周期由多处协作 | 可以对单个会话建立明确open/settle/close边界；共享模型目录、客户端和数据库不可随会话关闭 |
| 长期/跨会话记忆 | 独立存储及索引设计仍待做 | 两种对象布局都不能替代记忆设计，不因此改变已确认的SQLite方案 |
| SDK/CLI/Web复用 | 同一个AsyncApplication公开入口，已有受控状态和SessionPort | 单会话操作可独立复用；不能照搬Pi CLI切会话先停止旧runtime的行为到多会话Web |

## 6. 哪些现状应当保留

1. 共享根/角色编译图，只要middleware/工具可变数据按请求/会话归属；thread区分图状态。不是“每会话一个对象”就必须“每会话编译一份图”。
2. 共享模型目录、借用客户端、工具定义与角色定义；单会话对象可借用它们，不必复制或独占连接池。没有测量，不声称Pi每会话创建Agent的开销很高，或我们共享图已经有显著性能优势。
3. SessionState的有类型数据和显式持久字段映射；消息/checkpoint与应用状态不同，运行控制/future与持久事实不同。
4. SessionPort显式边界、当前run的RunServices及context，根/子scope绑定和必要保存/观察失败区分。
5. 持久化、恢复和Web交付组件按真实职责分工；其中保存协调的单值门禁是待升级范围，不是跨层分工本身有错。

## 7. 两边都存在的清晰度成本

- 当前SessionService同时管理共享基础设施、多会话集合、单会话运行和子实例；三个mixin只是把同一个对象拆到文件里，不形成独立所有权。可搜索、单文件少于500行有价值，但不能代替边界清晰。
- Pi单会话入口更集中，但agent-session.ts当前3469行，含工具/提示词、扩展、模型变更、压缩、队列、重试与消息树相关行为；此外还有AgentSessionRuntime、Services、SessionManager、LiveSession等。不能把它描述成“一个类就解决一切”。行数不作质量排名，也不把其大类搬入本项目。
- 有类型的state＋service是一种可用组织方式，不天然落后；对象有方法也不天然更可扩展。真实收益取决于是否减少跨会话状态关联、是否有一个完整生命周期拥有者。
- 多个映射不自动等于重复状态：会话busy、持久库running和Web job结束各有语义。迁移必须保留这些边界，不能以“只留一份状态”为由删除保存确认和显示投影。

## 8. 推荐的目标边界（方向已确认，具体分片见105）

```text
应用 / 多会话协调者
├─ 共享模型目录、工具与角色定义、编译图、存储
├─ 会话登记、创建/装载/移除、全局max_active_runs=10
└─ 单会话执行对象 A / B / ...
   ├─ SessionState（本会话应用数据）
   ├─ 当前运行及控制关联
   ├─ 本会话子实例与待批关联
   └─ 本会话运行、审批、停止及settle行为

持久化组件：继续处理实际保存/恢复IO和事务
Web交付组件：继续处理HTTP任务、连接、显示和有界重放
```

单会话对象不独占整套模型客户端、SQLite或图，不保存第二份消息历史，也不承载尚未定义的worktree/插件接口。多会话管理入口按sid定位后调用该对象；框架需要按sid/path解析的地方仍由明确边界路由，不保证所有ID查找消失。

新增对象必须实际接管会话状态转换和生命周期，迁走对应旧所有者；如果只是把所有方法转发回SessionService，或加一层session.run → service.run(sid)，不建议实施。

## 9. 选择、成本与104的关系

**最小方案**：沿现有service＋state实现104，补齐按会话门禁和组合测试。当前没有源码证据说明这条路线不能支撑并发、动态工具或worktree；成本较低，候选Spec已存在。

**长期推荐**：如果本轮目标包含单会话生命周期的架构清晰度，先细化并确认一次有边界的单会话行为收敛，再实施104。理由来自当前停止、审批、子实例创建/settle三类实际关联，不是只为未来能力预建框架。

这不是只新增几十行包装：需调整SessionService及controls/approval/subagents、应用调用、持久化快照/恢复、SessionPort适配和相关测试，预计十余个生产/测试文件，须独立迁移Spec。范围设计要先明确全局额度与会话准入的原子边界、锁顺序和run/子实例所有权；不能仅换类名就宣布并发安全。

若采纳，迁移保持公开AsyncApplication API、DB schema、图/checkpoint语义和用户行为；在同一切片删除已转移的旧内部入口，更新调用地图。不把“重构”和“默认10个并发”的行为升级混在同一验证结论中。

后续用户已确认采用“先会话架构、再并发功能”的顺序；[Spec105](../specs/105-session-ownership-refactor.md)给出五片及最终持有关系，105-01/02/03随后获批并完成状态与根/子执行归属验收，当前结果以105-03为准。104仍供审阅并依赖105余下两片，默认10已确认，不重复询问；下节15项测试保留调研时点，不作为105实施结果。

## 10. 本轮验证与未证明项

命令：`.venv/bin/python -m pytest -q tests/test_session_state.py tests/test_session_port.py --tb=short`。

实际：15 passed in0.99s。覆盖当前state隔离、受控快照、恢复瞬态重置、SessionPort根/子接线与相关scope行为。它证明当前相应合同仍通过，不证明新对象设计已实现，也不证明10个持久根任务已通过验收。

其余证据为源码调用链核对；没有新性能/内存基准、Pi运行测试、真实模型请求或Windows原机结果。不根据类数量、文件行数或一次测试给总体可靠性评分。
