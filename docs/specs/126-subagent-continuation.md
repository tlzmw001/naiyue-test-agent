# Spec126：主Agent复用已完成子Agent实例

日期：2026-10-22。状态：126-01—04已完成本机验收；max_model_calls按每次委派计数。执行/生命周期/查找展示见[01](126-01-subagent-continuation.md)、[02](126-02-subagent-continuation-lifecycle.md)、[03](126-03-subagent-discovery-delivery.md)，共享安装worker、耗时和四目标构建见[04](126-04-subagent-acceptance.md)。当前未发布，真实供应商及其他原机效果分别验证。

## 1. 目标与已确认范围

业务例子：主Agent先让tester分析接口，得到结果后继续让同一tester补异常场景；另一项无关分析则新建tester，使用干净上下文。角色名称相同不代表实例相同。

已确认：主Agent决定新建或按完整实例ID继续；结果返回本次父工具调用；前台、一层、同进程执行；保留原侧栏查看/停止。用户直聊子Agent已取消，不列未来待办；独立讨论留给未来会话分叉/新会话，本片不提前实现分叉。

已确认：成功结束并保存的实例可跨用户轮次、跨应用正常重开后显式继续；本次启动前的旧格式实例只读，不推断补齐身份。用户明确max_model_calls仍限制本次调用的模型轮数，不按实例生命周期累计，其余建议按本Spec执行；当前01—04均已完成本机验收，完整结果见各片记录。

## 2. 当前机制与参考证据

现链：subagent工具 → RunServices/调用上下文的ainvoke_subagent → SessionSubagentControls → AgentSession.prepare_subagent_instance → 当前ActiveRun登记 → 唯一子driver → 独立child_thread_id的create_agent图 → 必要checkpoint → 有界报告/父ToolMessage → 父收尾清除活动登记 → 归档和父元数据提交。

实际缺口：每次父批次/工具调用派生新ID；角色定义和子历史已经存在，但没有跨run的可续跑登记。当前历史列表从父调用派生；报告中的ID只在artifact，模型未必能看到。归档采集、重生成、实时投影多处仍默认一次调用就是一个子实例。当前max_model_calls使用thread_limit，每次新实例时等价于单次限制；新增同thread续跑后须显式重置本次计数，避免变成实例累计限制。

参考细节见[调研](../research/子智能体续跑与用户直聊参考调研-2026-10-22.md)：Codex b4ad33d按独立thread ID新建/发送/恢复；Claude官方支持保留子历史后显式继续；Pi a69bef789示例每次--no-session/新进程，链式显式传结果；AgentX 3fcfa0e的已查任务链显式传previousTaskResults，不提供等价持久子实例证据。借鉴身份和消息职责，不照搬Rust/Java执行器或团队平台。

本项目langchain1.4.2/langgraph1.2.12。[官方持久化](https://docs.langchain.com/oss/python/langgraph/persistence)提供thread状态延续；离线框架探针已验证同thread追加、新thread隔离，以及新委派重置计数/审批恢复保留计数的可行性，不证明产品入口已经接通。证据见第13节。

## 3. 已确认的产品规则

| 决定点 | 已确认选择 | 理由 |
| --- | --- | --- |
| 新建或继续 | 原subagent工具增加可选agent_id；省略/null新建，给完整ID继续 | 保留现有agent/task调用，避免再造第二条执行链 |
| 可继续状态 | 上次成功、必要保存确认、清洁checkpoint；失败/停止/待审批/运行中/未确认均拒绝 | 首期做新任务延续，不做故障状态修复或审批绕过 |
| 作用域 | 同一个逻辑父会话、同一工作区，禁止跨父会话复用 | 现有权限和归档均以该归属为边界 |
| 并发 | 同实例同一时刻一份委派，重复并发直接返回busy，不排队 | 不新增队列和等待协议；不同实例仍可按原规则并行 |
| 次数预算 | max_model_calls限制本次委派的模型调用轮数；每次新委派重新计数，省略仍无限 | 保留单次调用上限的含义；同一委派的审批暂停/恢复不重置 |
| 模型/角色 | 延续首次解析的model_id；当前角色执行定义必须与首次指纹一致 | 根会话切模不会悄悄改变子实例，角色变化显式要求新建 |
| 重开 | 只恢复已成功提交的实例定位，等待新的显式委派 | 不复活协程、旧审批、旧工具或未确认副作用 |

拒绝继续时返回明确原因，由主Agent决定是否新建；不把未知ID、角色变化或脏历史静默转换为新实例。

例如max_model_calls=10：第一次委派使用3轮后成功，第二次委派同一实例仍有10轮；第二次先使用3轮再等待审批，恢复后只剩7轮。正常重开后的新委派也是10轮。单次达到10轮且已产出成功报告，不妨碍以后再次委派；若还需要第11轮，则本次失败并按失败实例规则处理。这里只限制当前模型循环轮数，沿现有计数口径，不改成SDK网络尝试数或实例总用量上限。

## 4. 对象、身份与权威状态

继续复用已冻结SubAgentSpec/编译角色图；不为每个实例编译新图。

- 角色：名称、提示词、工具名、显式模型和max_model_calls；当前应用启动/可信登记拥有。
- 可续跑记录：AgentSession拥有内存模式记录；持久模式由SessionStorage中的小表拥有。保存稳定身份、执行合同指纹和最新安全状态，不保存历史正文副本。
- 活动委派：继续由ActiveRun.subagent_instances持有本次控制器、driver、审批、父调用绑定；本次结束仍释放。新增已准入父调用身份集合，防止同批次/同call重放，不把它变成持久任务平台。
- 子历史：仍由原checkpointer和聊天归档拥有；可续跑记录仅保存引用。事件/用量/界面是投影，不能决定是否允许执行。

身份合同：

| 字段 | 来源与用途 |
| --- | --- |
| agent_id | 新建时沿用subagent_identity(session_id, parent_batch_id, parent_tool_call_id)的64位小写十六进制摘要；此后保持 |
| agent_path/child_thread_id | 由已确认角色及agent_id构造并登记；不接受模型传入路径、thread ID或checkpoint ID |
| delegation_id | 每次父调用的session/batch/call摘要；用于该次输入/回填及准入去重，不等同稳定agent_id |
| seed/reply_id | 首次保持原seed身份；续跑使用本次delegation_id派生新的消息ID/reply_id，不能覆盖首次任务 |
| agent_run_id | 每个真实子执行片段新ID；审批继续可另起片段，parent_run/call仍绑定本次委派 |

ID只是定位符。执行前还须读取记录，验证会话/真实工作区、角色、当前父run/batch/call/许可、状态、预算、checkpoint及排他占用。

## 5. 模型工具与外部接口

### 5.1 原subagent工具

保持agent和task必填；新增agent_id，可省略/null或完整64位ID。给ID时agent必须与记录角色一致；不支持短ID、前缀、名字猜测或任意thread输入。默认行为仍为新实例。

父工具许可按当前保存批次中的真实参数绑定。新增可选字段后，校验不能再硬比较旧的两字段字典；缺省/null按schema含义处理，但执行许可仍绑定原批次/原始调用参数。prepare和driver之间均检查当前run及本实例控制器未停止，不能只靠工具描述或模型提供的角色。

模型可见的成功结果改为有界JSON信封，字段固定：agent_id、agent、status、report、truncated。原artifact保留既有字段并增加agent_id、delegation_id；usage只统计本次reply_id下的新AI消息，不能将旧历史的usage再次相加。限额计数只属于当前委派；实例历史用量仍由原观察/档案汇总，不能拿来扣减下一次委派额度。

max_result_bytes覆盖完整信封序列化后的UTF-8字节，max_result_lines继续约束report行数；在截断report前预留固定元数据。预算连固定元数据都放不下时执行前拒绝，不调用子模型；完整正文仍在子历史中。CLI/Web工具卡同步消费该结果合同，不因新信封隐藏报告。

### 5.2 只读subagent_list工具

实际消费者：主Agent在父历史被压缩后找回旧实例ID，不在每次模型请求自动塞全量实例索引。

参数：limit可省略、before可省略或当前会话已有实例的完整ID；limit为正整数且不超过配置的max_list_items。登记实例按created_at和agent_id稳定倒序分页；legacy没有时间戳，按档案首次出现的倒序排在登记实例之后，不伪造创建时间。before必须在当前会话内可核对，不能仅解码后信任外部游标。

返回：items、next_before、truncated。每项只含agent_id、agent、最近task的有界预览、最后状态、resume_available/resume_reason；不返回历史正文、凭证、任意磁盘路径。resume_available只是登记/角色可用性的候选提示，不承诺下一次调用一定获准，不为列一页扫描全部子正文；实际续跑重新检查checkpoint/当前角色配置/当前许可，不检查实例累计余额。达到条数或字节预算都按完整项截断并给下一页；第一项预览过长时先缩短预览，固定元数据仍超预算则显式预算错误，不返回空页死循环。

新工具经builtin注册、权限/并行计划开放。2026-10-22用户确认一个“子智能体”能力开关：用户选择只保存subagent；请求内实际工具视图在根启用subagent时派生subagent_list，模型定义与执行门禁使用同一视图。控制选项不单列list；禁止单独选择list，默认注册全工具时规范为用户选择。不改写旧会话active_tools或真实配置，旧会话已启用subagent即获得配套查询能力，不自动执行查询。子角色不允许登记subagent_list，实际入口也检查agent_path=root。

沿现有RunServices增加一个alist_subagents可调用能力，生产者为AgentSessionSubagents，消费者仅此只读工具；经现有RunObservation.bind和make_invocation_context投影。直接向启动时工具构造器传SessionService不可行，因为工具先于应用装配；复用现有运行服务对象优于新ContextVar、全局注册表或通用RPC入口。只投影给匹配的根run。

### 5.3 配置与兼容

现有tools.subagent.max_result_lines=2000、max_result_bytes=65536保持默认。新增只读列表预算：max_list_items=20、max_list_bytes=8192、task_preview_chars=160，均严格正整数；03已接配置加载、会话构造和实际响应预算。默认全工具可发现但仍按现有会话工具开关启用。

现有Python应用查询方法、CLI命令和HTTP路由保持；查询记录增加可选agent_id续跑元数据、resume_available/resume_reason及最新父绑定。request_stop和HTTP控制体增加可选agent_run_id/approval_batch_id，绑定观察到的子执行/审批；省略时保留显式停止当前目标的旧行为。持久旧记录没有新登记时仍可读历史，但续跑原因明确为legacy_instance。新工具公开导出/默认注册只做相应增量，不保留无消费者旧内部分支。

## 6. 执行与准入

```text
主模型生成父工具调用（新建 / 带agent_id）
  → 原参数/活动工具/权限门禁，核对当前batch和执行许可
  → AgentSession锁下预占本实例、本次父调用；锁外读取登记和checkpoint
  → 核对作用域、角色指纹、保存状态、本次次数配置及消息/工具对完整性
  → 持久模式用记录version作条件claim；重新检查父run/停止标记
  → 同一角色图、所选child_thread_id追加本次delegated_task输入，新委派重置本次计数
  → 原模型—工具循环 / 内部审批 / 停止 / sync checkpoint
  → 子完成确认并保留本次父调用绑定
  → 有界结果回当前父ToolMessage；本次driver/审批/连接释放
  → 父必要保存事务确认归档及登记；此后下一父run才可继续
```

同一父run内，子本次成功并完成自身必要保存/资源收尾后，可以在后续父模型步骤再次使用；同批次同时对同ID发两次委派，一份执行、一份busy，不能用后到请求覆盖先到控制器。批次/call去重和实例占用分别负责，不能用agent_path存在就永久拒绝该run的第二次合法委派。

开始执行前失败不污染上一次已确认状态：预占/claim失败释放当前槽；尚未追加子输入就收到停止时按本次身份撤销claim并保留原记录。输入已经写入后出现失败则记录失败/未确认，不能回滚成旧ready假装可继续。

每次续跑创建新RunControl、审批Future和调用上下文。真实用户授权来源从当前父消息重新投影；delegated_task仍不是真人授权。旧子审批只服务原活动driver，不能用续跑绕过或复用旧grants。

计数沿现有ModelCallLimitMiddleware的检查/递增逻辑，只在子Agent专用的abefore_agent中按应用可信上下文标记重置thread_model_call_count：新建或按ID新委派标记为新调用，审批Command恢复不标记。内部字段因此承载本次计数，不再解释成实例累计统计。不修改共享角色图的限额对象，不新增数据库计数列，也不另做一次aupdate_state。不能只改成run_limit：它按图调用重置，会让同一委派的审批恢复重新获得额度；直接在普通输入字典写私有计数字段也不会生效。子限额中间件放在原model_middleware段，利用after_model反向执行顺序在审批中断前记入已经发生的模型轮数；不改变原权限/审批彼此顺序。具体接线须在126-01用产品入口测试确认。

## 7. 保存、重开与最小数据变更

新增一张naiyue_subagent_instances表，schema13→14，沿现有预迁移备份/验证/短事务升级。已有库只建空表，不重扫聊天正文伪造旧实例权限或合同；旧实例继续只读可见。

字段合同：session_id、agent_id（联合主键），agent_path、child_thread_id（唯一），agent、role_fingerprint、model_id、last_task、state、version、checkpoint_ns/checkpoint_id、last_delegation_id、last_parent_run_id、last_parent_batch_id、last_parent_tool_call_id、last_parent_reply_id、created_at、updated_at。role_fingerprint仅覆盖名称/角色提示词/工具名/显式模型ID/max_model_calls的规范序列化，不存密钥/供应商完整配置。无独立角色表、调用历史表或任务服务。

每次委派只更新同一实例行；完整每次调用/结果仍存在现有父子消息档案中。临时模式由AgentSession内存持有相同形状，应用关闭即结束，不给它伪造持久保证。持久模式直接以存储行为权威，不再保留一份可自行改变状态的长期缓存。

状态：claim后的running → 子成功sync保存且收尾确认后的pending_parent → 父必要保存事务中的ready。子失败/停止为failed/stopped，运行中或pending_parent遇到崩溃保留未确认事实；retired用于已被重生成丢弃的实例。pending_parent仅能由同一活动父run在确认子checkpoint后再次调用；其他run/重开不允许借用。

写入顺序：先记录claim，再运行子图；子终局需核对无next/tasks/interrupts、消息工具配对齐全、无run_stopped且最后为成功报告。子checkpoint与登记无法跨saver连接做同一事务，依靠上述不对称状态保守拒绝；绝不宣称跨连接原子提交。

父finish_run的原事务同时提交父状态、聊天归档游标和本run已完成实例的ready状态，逐项核对child head与登记checkpoint一致。父保存失败不得先标ready；故障仍走原blocked/manual-review合同。活动driver可释放，但必要保存需保留本run触碰的实例引用，直到该事务完成。

重开时仅加载元数据，不执行模型/旧工具、不恢复Future。新父输入显式给ID后，重新核对记录与最新checkpoint完全一致、角色指纹一致、模型仍可用、本次次数配置以及当前权限；获准的新委派重新计数。旧运行中/未确认实例拒绝继续，不通过“看起来最后有AI消息”推断成功。

126-02补充：内部finish_run仅在活动父afinish显式传入且事务重验subagent_run_id后确认子ready；普通恢复与恢复审批均不传，不提升崩溃遗留pending_parent。子委派的逻辑父reply_id来自可信父图active_reply_id（兼容回退当前run.reply_id），跨根审批恢复仍绑定原回复，后续followup绑定新回复。

角色提示词/工具名/显式模型/次数配置变化导致指纹不同，拒绝旧实例并提示新建；描述文案变化不影响执行合同。根会话切模后旧子实例仍用首次解析model_id；凭证正常轮换不保存在指纹中，实际MCP当前定义/可用性继续由原权限和调用门禁判断。

## 8. 档案、压缩、重生成与修剪

当前subagent_records两处推导函数扩展为读取真实创建/续跑调用目标，不能把续跑call摘要再造为新agent_id；同实例合并为一条记录，状态/当前父绑定取最新委派，旧ToolMessage不改写。公共读路径优先新登记，历史旧记录沿当前档案查询合并，不强制反序列化全部旧正文。重生成调用的特定root_thread引用查询必须仍只返回该thread实际创建/续跑引用，不能被全会话登记列表替代，否则前缀保留判定失效。

collect_chat_updates增加本次实际触碰的已登记child scopes，不能只从未被压缩的父messages中发现；按原chat_head增量同步，未变化的旧实例不重复扫描整份历史。先收集子历史，后同事务确认ready。

父上下文压缩不删除实例登记/child head；subagent_list可找回ID。子自身压缩仍保留同thread和本次委派计数，压缩/审批恢复不得重置额度；新委派只重置计数，不重置压缩状态或复制父完整历史。

最后任务重新生成：在现有activate事务中识别被丢弃reply实际触碰的实例，将其标retired，禁止再次复用，即使更早父消息也引用过该ID。保留前缀的子上下文不能悄悄混入被丢弃回复的后续工作。新模型若请求旧ID，明确拒绝并要求新建；不自动fork/倒写旧子checkpoint。完全只属于被丢弃部分的实例按原obsolete_children规则删除档案/退役，保留前缀引用的子历史仍可查看，但续跑不可用。

checkpoint修剪增加登记的非空checkpoint指针作为保护引用；同会话唯一归属检查覆盖新表，running/pending_parent/未确认的指针不得误删。父正常保存后才修剪，原单批/字节预算保持。删除父会话时在原事务删除登记，并把登记的child threads纳入原checkpoint删除集合，包括尚无chat_head的失败新建。无自动实例淘汰或新TTL。

## 9. 实时交付与查询

稳定agent_id/path不变，每次agent_run_id/current parent绑定更新；审批恢复保持本次委派绑定。沿原AgentEventSource五字段和run_started.mode，不新增独立消息总线或复制一套事件。

后端observe_agent、前端applyAgentEvent在mode=message的新委派开始时重设当前草稿/工具态并重新定位parent_batch_id；approval_resume保留同次历史。旧请求/旧run响应仍按现有scope/Abort/序号隔离，不能用稳定path把旧终局覆盖新执行。CLI按原source显示本次结果；停止始终携当前父run，旧停止/旧审批不能命中新委派。

Web保持现有列表/详情/消息分页路由，列表按agent_id聚合为同一实例，追加resume_available/resume_reason；详情显示同thread的完整多次委派历史。侧栏不增加输入框或续跑按钮，只显示当前状态/不可续跑原因并保留现有停止。现有上下文/诊断字段继续按实际子run查询，不把累计usage当作本次usage。

## 10. 错误合同与范围限制

| 场景 | 行为 |
| --- | --- |
| 未知/其他会话ID、角色不符 | 安全ToolException回本次父call，子模型/工具0次，不泄漏另一会话信息 |
| busy/待审批/停止中 | 不排队、不自动中断目标，原driver/审批保持 |
| failed/stopped/legacy/retired/状态未确认 | 不修复旧任务、不重放；返回不可续跑原因 |
| 本次模型轮数耗尽 | 阻止超额模型调用，本次失败且不回滚已执行工具；审批恢复不能重置。以前成功委派的用量不限制新的委派 |
| 角色改变/模型不可用 | 执行前拒绝；旧历史/上次成功状态保持，可新建别的实例 |
| 子保存/资源收尾失败 | 按必要操作失败处理，不报成功或重新开放继续 |
| 父必要保存失败/崩溃 | 原保存阻断；新登记不标ready，恢复原根时不执行旧子任务 |
| 普通观察投递故障 | 原诊断降级，不改变已确认业务结果；必要保存故障不能归观察处理 |

不包含：用户直聊、后台子Agent、多层、跨父会话复用、失败/停止任务自动修复、隐式父历史复制、会话分叉、单实例独立关闭/删除工具、新模型选择器、新权限模式、队列/多进程/新依赖/新密钥。该片无外部账号新增依赖；沿用户既有配置模型和工具权限运行。

## 11. 影响文件与实施切片

这是跨执行、持久化、查询与呈现的复杂修改，预计涉及二十余个生产/脚本/前端文件；逐片按实际职责修改，不新增服务。精确职责、真实消费者与同步检查见[文件地图](126-subagent-continuation-file-map.md)。

| 分片 | 可独立验证的交付 | 必须完成的边界 |
| --- | --- | --- |
| 126-01 | 同应用内新建/按ID续跑的完整主链，内存与持久记录真实读写接入 | 参数/许可、同ID排他、唯一driver、新seed/父绑定、有界信封、每次委派次数上限及审批共享计数、单表迁移及备份校验；不暴露空回调 |
| 126-02 | 跨重开、历史压缩/归档/重生成和修剪的组合闭环 | 父保存事务ready、旧格式只读、崩溃不接管、触碰范围、退役与删除、子head/登记指针一致 |
| 126-03 | 主Agent查找实例以及CLI/Web多次委派呈现 | subagent_list及配置/服务接线、稳定ID聚合、新委派/审批事件区分、当前停止/审批、迟到隔离，无新侧栏结构 |
| 126-04 | 全链与标准安装收尾 | 真文件/SQLite/假模型跨轮与源外重开、故障注入/真实进程退出、性能原始数据、四目标构建与本机安装；清理被替代消费者 |

每片先写行为失败测试，确认失败原因后最小实现；不要让后续片掩盖当前片的必要保存/权限/迁移缺口。后续片组合完善相应产品范围，不把未验范围提前报告完成。方案已确认，分片仍须另行授权实施。

## 12. 验收与回退

确定性验收：

1. 新建A得到模型可见ID；后续父步骤及下一用户轮按ID看到A原历史；省略ID创建B，B没有A历史；老消息ID/原父ToolMessage不变。
2. 相同ID并发两次仅一份进入图，其他实例照常并行；同一次调用不能重放，合法后续步骤能再次使用。
3. 跨会话/伪路径/角色不符/旧审批/旧停止均拒绝；当前Deny/Ask/Auto和真实用户意图重验，授权不从旧seed复制。
4. 同实例不同委派分别从0计数，正常重开后新委派同样重置；同一次委派审批暂停/恢复及压缩共用额度，耗尽后新增模型调用0次。恰好用完上限但成功产出报告仍可再次委派；省略上限仍无限；每次报告usage不重复统计旧历史。
5. 刷新/压缩/正常关闭重开可列出新格式成功实例并显式继续；迁移旧记录只读；恢复过程中旧模型/工具/Auto和Future均0次。
6. 真实子进程分别在claim后、子checkpoint后/父提交前、父事务提交后退出；不重放工具、不把未确认记录改成ready，已落地文件内容及执行次数不重复。
7. 注入子checkpoint、归档、登记写入、父提交和MCP收尾失败；验证原异常/错误回填/必要保存阻断，不把失败检查器吞成成功。
8. 修剪后A仍可继续；重生成丢弃部分触碰的A退役，未触碰B保留；删除父会话连登记/子checkpoint/档案一起处理，原备份还原后引用一致。
9. 同一path第二次委派的UI状态/父批次/草稿正确，审批恢复不误清空；大报告保留完整子档案/有界信封；切会话和迟到响应不串入。

拟验证命令（实施后执行，当前不称通过）：

- `.venv/bin/python -m pytest -q tests/test_subagent_continuation.py tests/test_subagent_continuation_persistence.py tests/test_subagent_continuation_boundaries.py tests/test_subagent_discovery.py tests/test_subagent_events.py tests/test_web_subagent_stop.py`
- `.venv/bin/python -m pytest -q`（按当前项目原生fd/rg测试路径准备环境）与`npm --prefix frontend test -- --run`。
- `npm --prefix frontend run build`、Python AST/compile/500行/依赖及`git diff --check`。
- 既有scripts.build_release四目标标准构建，Twine/源码与前端字节核对；scripts.verify_wheel_install在Mac ARM64源外干净安装，其他平台原机单列。
- 真实浏览器桌面/375px，用正式图和临时库产生两次同实例记录，检查状态、正文、审批/停止与无横向溢出；只读界面不触发模型。

性能：固定模型/临时库分开测新建与续跑的总耗时、元数据claim/finish、列表在0/100/1000条记录下的P50/P95/max；大规模场景只测合成登记元数据，不冒称同时运行1000个子图。记录环境、次数、原始结果，不以单次模型网络耗时证明产品无开销。复用原性能测量/安装模式，不新增基准服务。

回退：代码未运行迁移前可撤销本片；迁移后旧程序不支持schema14，不能只降代码强开库。按既有显式离线备份还原流程处理，保留新库现场并确认备份之后数据损失；不自动降级/删除列。工具已产生文件/网络效果不会随数据库还原回滚。

## 13. 规划轮实际验证与证据限制

规划期间未改产品、真实配置/用户库、依赖或版本；没有新功能TDD红灯，也没有实施新续跑工具。本轮文档结构/diff检查单列，不计应用构建。

当前基线命令：`.venv/bin/python -m pytest -q tests/test_subagent_history.py tests/test_subagent_execution.py tests/test_subagent_approval_resume.py tests/test_subagent_restart.py`，实际结果：**23 passed in3.47s**。证明原历史/执行/审批和崩溃不重放合同，不能证明Spec126已实现。

初次框架探针用当前安装包的create_agent、InMemorySaver、固定模型和thread_limit=2；同thread前缀保留、消息2→4，新thread只有2条，计数分别2/1；第三次同thread在模型前被限额阻止，固定模型总调用3、真实供应商0。该结果说明原中间件直接重用会累计，不是已确认的预算规则。失败调用确实可留下pending state，支持本期不直接恢复失败实例。JSON：build/spec126-plan-J4ECMH4T/framework-probe.json。

计数修订的离线框架探针：专用abefore_agent只在新委派重置，同thread两次委派均为1轮→审批→2轮；历史消息4→8，第二次未丢前缀。上限1时审批恢复不能再进模型，ModelCallLimitExceededError符合预期；真实供应商0。最初直接写普通输入私有字段的方案已证伪；固定模型每次响应须使用独立消息/工具ID，避免重复ID覆盖历史。JSON：build/spec126-plan-J4ECMH4T/per-delegation-budget-probe.json。这是框架可行性证据，尚未接产品的审批/压缩/持久入口。

规划轮之后用户已授权126-01，当前实现/验收见首片记录；下一片126-02。后台/多层/分叉仍各自独立，不恢复已取消的用户直聊需求。

## 14. 126-01实施边界（2026-10-22）

用户授权讲清实施后执行126-01，完成再介绍下一片。为保证首片新记录不悬空，提前接入续跑ID的原历史解析、当前run登记范围归档、父finish原事务ready确认，以及新表引用的基础修剪保护/会话删除；这是首片必要一致性，126-02仍负责重开/压缩/重生成的组合闭环和故障验收。原storage_backup_fixture必须随schema14新增真实版本降级步骤，历史版本去掉新表后再迁移，不能仅把版本号改回13。没有新真实配置/依赖/发布动作。

首片复核补充：显式agent_id在历史查询、归档、重生成引用及删除链中也须与当前会话可信登记的角色/path/thread一致；父工具参数出现ID不是归属凭据。claim写入在取消时排空，并且只有实际写入成功后才发布活动实例所有权，使输入前取消能撤销自己的claim，保持旧ready。登记失败且尚无driver时同样进入finalize，防止父停止把已结束实例改为stopping并遗留busy。历史schema测试的统一fixture随新增表移除步骤迁移，原身份/异常/恢复断言保留。

## 15. Spec126收尾（2026-10-22）

[04实际结果](126-04-subagent-acceptance.md)完成源内/源码外共用续跑验收，含同ID三次委派、正常重开零重放、fresh隔离、检查器及installer拒绝路径，4223后端/415前端/四目标同源码构建及Mac干净安装通过。纯元数据和完整固定模型路径各120样本见04；没有性能SLA或真实模型业务效果声明。四片无下一本机实现片，后台子Agent按后续独立讨论，不恢复用户直聊需求。
