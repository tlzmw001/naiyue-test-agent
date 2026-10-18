# Spec102：会话状态封装与运行生命周期收敛

日期：2026-10-17，2026-10-18更新。状态：102-01/02已实现第1—3项SessionState/受控入口、直接async和共享运行阶段并通过验收；第6项轻量准入/显式装载/GET只读与第9项DeliveredRun整类拆文件已随101-02接线；102-03已完成第5项中的旧工厂/空回调及消费者迁移；102-04已完成第4项最小SessionPort及显式根控制边界；102-05已完成第5项文本投影合并；102-06已完成第8项设置异常处理收敛；102-07已完成第10项顺序注释及生产链验收，7个已确认分片均完成；第7项跨会话并发已转入104草案，用户随后确认先做105会话行为归属。下方讨论保留设计时点，当前102交付范围以本行及第15—27节与分片结果为准。

| 用户问题 | 决定状态 |
|---|---|
| 1. dict状态与跨对象私有读写 | 已实现102-01：SessionState＋受控行为入口，持久化职责独立；见第16节 |
| 2. 普通/恢复运行流程重复 | 已实现102-02：共享准入、资源准备、失败处理与既有收尾，保留操作差异 |
| 3. IOCall单驱动间接 | 已实现102-02：应用生命周期直接async，删除IOCall/arun_steps，保留IO/关键写入取消收尾 |
| 4. Runtime零散回调 | 已完成102-04：最小SessionPort协议及根/子实际装配，沿用现有作用域 |
| 5. 无生产消费者及重复文本入口 | 已完成102-03/05：清理旧工厂/空回调、统一完整/增量投影并迁移消费者 |
| 6. GET恢复副作用及重读取 | 已完成101-02：轻量准入/只读查询、显式装载与工作区过滤下推 |
| 7. Web全局单活动run | 104草案承担能力升级；先完成105架构收敛，随后Web/持久化/关闭全链路按会话协调，不仅改active |
| 8. 三处设置操作异常模板 | 已完成102-06：局部_apply_setting，保留各操作准入、错误码、日志与成功结果边界 |
| 9. run_delivery职责与文件拆分 | 已完成101-02：DeliveredRun整类独立文件，不额外拆多个状态对象 |
| 10. middleware顺序说明 | 已完成102-07：装配点顺序说明、根子真实生产链断言及原框架/权限/turn/停止组合验收 |

后续版本边界：用户确认先做[Spec105](105-session-ownership-refactor.md)。105-01/02及[105-03](105-03-session-subagents.md#8-实际实现与验收2026-10-18)已让AgentSession拥有State及根/子运行、审批、停止和收尾；Service保留协调/共享定义和SessionPort路由，原全局观察/子实例Map删除。102成果保留，当前后端2439/前端186及构建通过；余105-04/05先逐片讨论，再实施104。下文当前事实保留102设计时点。

## 1. 当前事实

- SessionService._create_session当前初始化18个键的dict。SessionPersistence._install_session另写一份恢复构造，字段和瞬时默认值存在两处维护点。
- SessionPersistence、session_recovery、session_context、AsyncApplication、AsyncPersistentApplication跨对象访问_sessions/_lock/_persistence；不仅是字典没有类型，还包含锁、读写时点和生命周期责任外泄。
- SessionControlMixin等以同一个self操作SessionService内部状态，属于同一对象按文件拆分，不等同外部对象直接取得可变状态。本次不为每个内部字段增加getter/setter。
- SessionService当前481行。把持久化/恢复代码全部搬回同一文件将违反500行约束，也混合存储实现与内存生命周期。
- _run_steps当前92行，_resume_steps102行，存在共同准入、RunObservation构造、资源准备、异常核对和finally；未量化用户估计的70%重复比例。
- AST核对两函数中的review_required均只有一次赋值、没有读取；两处review_required = False可在同一重构中删除。_cleanup_run/_afinish_run中的同名变量有用途，不能机械删掉。

## 2. 已确认A：SessionState与行为边界

用户已确认用内部SessionState dataclass显式表达现有18个字段，新建和恢复共用初始化/default_factory。列表、deque等可变默认值按实例创建；不借改类型改变默认权限、恢复或状态语义。

SessionService保持内存状态和状态转换的拥有者。跨对象消费者通过有用途的操作访问：读取限定配置/执行身份快照、验证并安装恢复状态、取得必要保存快照、在受控边界移除会话等。方法按真实消费者收敛，不预建通用会话管理平台，不对每个字段公开setter，也不返回可变SessionState或锁供外部继续修改。

快照只包含消费者需要的字段，含列表/嵌套授权时不得仅靠浅复制泄漏可变引用；不为取少数字段深复制整棵运行状态。读取后跨await的上下文预览仍要执行版本/当前状态复核，不能因为改用dataclass就删除竞态保护。

持久化组件继续拥有SQL/事务、checkpoint保存确认和必要恢复IO；SessionService统一内存准入与状态变更入口，应用薄转发使用这些行为接口或明确的持久化入口，不穿透service._persistence.storage。无需把所有IO方法和SQL搬进service文件；沿已有职责拆文件，保持单文件≤500行。

锁必须由状态拥有者管理。恢复/删除的persistence锁与service状态转换现有原子边界需显式设计，不能简单把with拆成几个公开方法，形成先检查再修改的竞态；锁顺序一致，网络/文件/模型IO不持有短状态锁，不擅自换RLock掩盖嵌套锁问题。

不直接asdict(session)写SQLite；沿原持久字段显式映射，队列、观察对象、一次性许可、运行资源等瞬时内容不因类型迁移意外持久化。不增加__getitem__或双dict/dataclass写入作为无期限兼容。

## 3. 已确认B：共享阶段，保留三种操作语义

用户已确认复用既有_afinish_run，提取有实际重复消费者的_admit、_prepare_resources、_on_failure（最终命名按实现职责核验）。两个入口保留可直接阅读的普通运行/压缩与审批恢复执行段，不新增RunStrategy、Operation基类、通用回调流水线或大量布尔参数控制器。是否需要进一步共用外层try/finally以实际重复和可读性判断，不为删几行隐藏异常边界。

| 阶段 | 可共用部分 | 必须保留的差异 |
|---|---|---|
| 准入 | 人工核对/busy、生成身份、策略与观察器、安装活动运行 | 普通run拒绝pending；resume要求pending，校验decisions/remembered或审批停止，恢复原turn |
| 资源准备 | prepare_run_resources及本次冻结prompt/审查指令 | 普通消息来源标记；resume准备grant与remembered规则的现有时点；compact关闭steering |
| 执行 | 原事件绑定与当前异步入口 | 新输入、compact_session、Command恢复、审批停止分别走原路径 |
| 结果提交 | 保存pending的必要事实与通知边界 | resume替换/清空旧pending及resume_start_index，普通run新增pending |
| 失败 | 原异常分类、部分工具状态核对、必要人工核对标记与日志 | 普通run可产生ManualCompactionCommitError，不能收敛时漏掉；GraphBubbleUp/取消按原合同 |
| 收尾 | 已有_afinish_run | 子任务settle、保存确认、busy释放与终局顺序保持 |

共享准入中的检查与busy/run安装仍在同一原子边界；无效审批输入必须在新run开始与持久化之前拒绝。不要先检查、释放锁再通过另一方法安装run。

失败处理完成后由原调用处继续raise原异常，不能包装/替换身份。仅应被核对的异常读取部分状态，框架控制信号不按普通工具故障处理。保留primary与secondary故障优先级，必要清理或保存失败不能降为观察故障。普通/恢复对ManualCompactionCommitError的可达性在实现时明确，不用无用途回调传同一规则。

## 4. 已确认C：应用生命周期改原生async

事实：application/io_steps.py现在只有arun_steps驱动IOCall；其send/throw还负责把返回值/异常送回生成器，finally关闭生成器。IOCall分别表达同步阻塞调用、critical=True的原生异步、critical=False的原生异步，不是所有调用都应转run_blocking。

历史校正：Spec093第5/6节及文件表明确保留生成器和arun_steps，只删除同步驱动与伪fallback；本次建议与异步收敛方向一致，但删除剩余生成器属于新决定，不能表述为093已经承诺却遗漏的实现。

用户已确认将session_service的普通运行/恢复/部分状态核对、session_persistence的保存/历史/恢复/安装准备、session_recovery的history_steps/recovery_steps全部迁入普通async函数调用。保持try/except/finally在真实await周围，返回值直接返回、异常自然向上；按实际消费者删除IOCall/arun_steps及仅服务它们的包装，不留新的通用执行器。

| 原操作 | 直接async后的写法合同 |
|---|---|
| 阻塞存储、文件准备等call | await run_blocking(call, ...)，保留内部await_settled等待线程IO完成 |
| critical=False图/模型执行 | 直接await原async调用，仍可取消，不整体shield整个run |
| critical=True保存/恢复写入/收尾 | await await_settled(original_async_call(...))，不因去生成器提前释放所有权 |
| 纯快速校验/数据装配 | 核对无IO与取消副作用后可直接普通函数调用，不机械新开线程 |

关键收尾_afinish_run必须仍由await_settled保护；恢复的原多次写入顺序、取消传播对象、IO失败优先级、子任务settle与busy释放时点保持。反复取消、子任务自身取消、已开始IO失败不能因直接await而改变成静默成功或覆盖主异常。

第2项已确认的共享阶段直接用普通函数/async函数实现，不先新造生成器helper再迁一次。Runtime摘要的_compaction_steps/arun_summary_steps是另一个算法/执行边界，本条不凭名称自动删除；若后续提出再按实际消费者单独判断。

验收迁移tests/test_architecture_semantics.py中原IOCall结构断言到正式async调用链，保留“关键写入完成→收尾完成→取消向外传播”、blocking结果/异常与原身份等行为。不能因旧类删除而删掉这些保护断言。保留test_session_recovery的取消恢复写入后再允许重试、pending writes以及保存失败测试。

## 5. 已确认D：最小SessionPort与显式作用域

事实：application._assemble_application向Runtime传入约十余个会话回调/协作者；_assemble_interactive_runtime的before_model消费停止/Steering，after_agent消费finish_run_boundary；模型选择、toolset、压缩和权限middleware各持有部分resolver。子图build_subagent_runtime也转传模型/工具/权限/prompt等resolver。

已有作用域：resolve_model、resolve_active_tool_names、resolve_permission_context已接受agent_path并查询子实例；prompt_parts当前按session共享。不能把已有隔离说成没有，也不能仅合并参数就宣称全部子作用域已解决。根队列的drain_steering/finish_run_boundary有写副作用，不能对任意子路径默认套用根行为。

用户已确认在Runtime消费边界定义标准库typing.Protocol的最小SessionPort，应用SessionService按结构实现；Runtime依赖协议，不import应用具体类。Port不返回SessionState、私有锁、存储或整个应用；不定义get_service/get(name)等字符串服务定位器。静态配置、ToolRegistry、checkpointer、事件/RunServices仍按原职责传递，不把所有参数塞进一个万能对象。

方法按真实消费者列明模型解析、工具活动集合、权限事实、prompt资源以及根控制边界。会变化的模型/权限/工具仍在原取值时点读取；可信调用context显式提供session_id和agent_path，需要核对当前运行身份的动作保留run_id边界，不用进程全局“当前session/agent”，不在共享编译图上写可变当前作用域。

优先直接使用明确身份参数，不额外引入per-agent工厂/缓存/Scope层；若未来有真实独立生命周期才再讨论。协议签名和根/子可调用方法表在确认方向后细化；不因接口整合扩展子Agent的Steering、结束续跑、多层或权限能力。

先检查record_selection的正式路径空lambda、resolve_project_instructions与prompt_parts旧转发/回退等实际消费者；不用给Port补无意义空方法以原样搬运历史签名。仍有独立低层消费者的能力按真实合同迁移，不能只因正式装配空实现就跳过消费者迁移；第5项记录本轮清理建议。

_assemble_interactive_runtime为内部装配入口，可随确认方案收敛；build_runtime_agent/build_configured_runtime_agent仍被实际测试/脚本使用，不默认等同完全无人使用。第5项建议将无生产消费者的configured工厂迁出并删除；其余按真实消费者核对。原外部应用API保持，内部调用方同片迁移、无无期限双入口；真实受支持公开签名若需改变先明确范围再确认。

验收沿实际应用注册/建图/运行到middleware消费追踪，防止Protocol只声明没接线。覆盖两个会话、两个兄弟子实例、主模型切换与子模型快照、工具/权限来源、共享prompt的现有语义、根队列不被子图消耗、审批恢复/停止/终局不串作用域；测试替身只实现该测试真实依赖的方法，不加通用动态转发壳。

参考边界：Pi本机a69bef789的runAgentLoop→runLoop通过AgentLoopConfig取model/Steering/Follow-up等方法；这只是集中声明依赖的参照，内部仍为回调。AgentX本机3fcfa0e的AbstractMessageHandler使用ChatContext中model/history等组装请求，属于Java请求数据对象，不等于本项目行为Port。两者不证明新增框架或作用域平台必要，Python实现继续沿create_agent/middleware。

## 6. 已确认E：清理非生产入口与文本重复

用户已确认本节清理与行为迁移边界，暂不实施。

build_configured_runtime_agent：当前生产只有定义/导入辅助，没有CLI/Web正式调用；实际消费者为test_model_access/test_model_limits、probe_configured_tool_loop和README等。建议将配置装配验证迁到正式应用装配入口、模型配置本身继续通过ModelCatalog验证，再删除旧工厂及仅服务它的导入/包装；不是删除对应选模、错误、上限、工具配对和配置失败测试。历史Spec/课堂保留并注明版本，当前README迁移到仍可运行的诊断入口；旧测试/可import不自动构成长期兼容合同。

record_selection：主应用装配及子图都传空lambda，middleware._prepare却每次执行。部分测试及probe_configured_tool_loop、probe_message_events、probe_persistent_session用它计数并在下一模型调用前拒绝超额，是真实诊断/请求预算消费者。建议将这些检查迁到测试/探针自己的发前边界，复用现有框架限次或验收SDK发送guard的合适层，不以模型完成后事件代替发前限额；确认模型重试、摘要/Auto/子调用各预算口径后删除生产record_selection参数、调用、空lambda，不把空方法搬进SessionPort。不顺便改生产事件wire或业务重试。

文本函数核对：model_access.messages.content_text接受字符串、列表字符串、text块，并为图片添加有限元数据说明，strict=True拒绝未知结构；cli_display._text接受列表字符串/text块，但忽略图片；async_message_observation._text只接受字符串或列表中的text块，服务流式delta；web.serialization.text_content只是content_text的转发别名。

建议直接删除Web纯别名并迁移serialization/run_delivery/agent_projection导入；统一完整内容投影的基础解析，CLI与Web按确认的图片说明合同复用content_text，终端_safe_text等显示安全处理保留。流式delta只产生真实文本，不用包含图片占位的完整投影直接替换；可共用纯文本提取基础并显式保留“完整内容附图片说明/增量只取文本”的少数差异。不为统一名字默默改变字符串列表、未知块、strict拒绝或非文本delta合同；具体公共函数参数在方向确认后按样例收敛，不增加通用解析器平台。

验收覆盖原配置/选模/限次行为，所有诊断入口不因删回调失去预算；text/string-list/混合块/图片/未知结构/strict以及流式碎片分别断言，纯图片不伪造文本delta，Web不泄露编码像素，CLI转义保持。当前工作区另有async_message_observation和Responses流身份回归修改，实施前重读，不能用本次清理覆盖或回退其他会话改动。

## 7. 已确认F：Web轻量准入、只读视图与查询范围

用户已确认本节轻量读取、GET只读与显式装载方向，暂不实施。

与Spec101本轮同步决定联动：101-01先接持久化归档基础，101-02与本节共同实现明确的打开/历史准备动作，ready冷装载和异常恢复分别保持原合同；归档落后时在历史交付前补录。当前selectSession先GET显示再条件recover的顺序须同片迁移，不能让落后档案等到下一次模型调用后才更新。准备/补录是明确写操作，分页及状态GET只读；活动run打开/重连只同步已冻结的保存边界，后续仍由实时显示交付。归档完成不代表会话已获执行许可。

事实：WebService.session先调用sessions→全表list_sessions，再按ID匹配；ready且未加载时调用arestore_session，取得生命周期占用并安装内存会话。这不是启动模型，但会改变has_session与后续操作可见的运行对象。正常缓存读取并非一概禁止，建议移出的是会话激活/恢复这类业务生命周期变更。

approve当前通常调用两次session：入口一次，RunDelivery._launch在start_lock内执行check_current再次调用；不是静态代码每次固定三次。第二次承担复核当前审批批次的必要语义，不能为了减查询删掉，不能将锁外旧快照用于授权。首次鉴权/工作区确认与锁内状态复核应分别轻量化；重复request_id先处理回执的原顺序保持。

建议拆分：require_session按ID读取必要元数据并验证可信工作区归属，不读历史、不恢复、不序列化消息；session_view只组合状态、分页历史与有界live信息，不安装可运行会话。工作区不匹配继续按已有对外404隔离，不通过要求客户端提交工作区/thread来信任归属。真正运行准入仍在原锁/应用生命周期内复核busy、恢复、人工核对及审批，不把轻量存在性检查当作执行许可。

冷会话装载/恢复移到明确POST或执行操作准备阶段，复用SessionService受控恢复入口和原persistence独占边界。为保持“选择会话后可正常继续”的体验，前端可在用户选择后自动调用明确POST，不要求额外手工点击；ready安全装载与失败/中断恢复的动作合同保持区分，不直接将ready塞进当前只处理异常恢复的POST实现。具体端点/幂等在确认方向后细化。跨恢复/删除、取消和配置失效的原保护保持，GET失败不偷偷重试写操作。

列表查询在SessionStorage用参数化工作区条件及必要列投影，保留pinned/sort_order/created_at/session_id原排序，处理root=None的既有语义；按ID准入直接查单行再核对归属，不再先列所有会话。CLI和已有list调用方按其真实范围迁移，不因优化Web而丢失合法会话，也不预建缓存/索引平台。

本项与Spec101是同一轻量准入/有界显示边界，实施时共享同一require_session/session_view路径，不在两个Spec各建一套。独立聊天档案分页不等于GET可以启动图或恢复执行。

验收：重复GET不安装/激活会话、不启动模型工具、不改变checkpoint/元数据业务状态；明确POST/执行准备能安全装载，恢复/删除竞态不复活已删会话；审批锁内重新读当前批次，过期/重复请求不重放；大历史操作无完整消息序列化；查询只返回当前工作区且排序一致，失败记录有界诊断。现有Web恢复、历史、管理测试迁移时保留“不重跑旧工具、跨区隔离、保存/取消”断言，不保留仅为旧GET副作用服务的实现。

## 8. 讨论G：跨会话并发是能力升级

当前RunDelivery只有一个active；A会话运行时，B会话也被_launch拒绝。独立临时SQLite＋StreamingToolModel探针通过真实HTTP启动A（202），等待模型进入后启动B返回409 session_busy，随后事件释放A并等待收尾；无供应商请求。夹具另有PowerShell不可用及fake响应未知结束原因诊断，不将探针称为无诊断完整验收。

历史边界：Spec077第2/3节明确单worker/单应用/单持久活动run，其他会话只浏览/编辑草稿；Spec087确认不同工作目录可各开独立服务、同库一个应用持有。一个应用拥有数据库不必然要求所有会话串行，但当前SessionPersistence.start确实以全局_active和_recovering阻断并发，finish也只处理一个(session_id, run_id)。单会话内多个工具/兄弟子Agent并行不等于多个根会话同时运行。

判断：限制真实存在，符合旧合同；若用户需要A等待模型/工具时B也能运行，应作为独立跨会话并发升级，而非只把Web.active换dict的局部修复。尚未把升级目标、默认并发数或排队策略标为已确认。

若选择升级，建议同一服务active_by_session、同会话仍只有一个活动根run，并有可配置全局根run上限；根run含其子Agent执行/等待，不能用这个数冒称全树模型请求或工具并发上限。达到上限优先明确拒绝并保留草稿，不新增后台任务队列或自动重发，具体上限待定。

全链路检查：SessionPersistence按会话维护活动/恢复/阻断与正确run所有权，完成A不得清除B；官方SQLite保存器和元数据事务实测独立thread交错保存，保留实际写事务串行及同库单应用锁，不因目标先换数据库。恢复/删除占用、重启状态、模型/权限与资源快照、RunServices/事件/控制归属、Web活动子审批、停根/单子、回执/缓存淘汰、应用/交付close全部核对，不能留下仅等待最后active的关闭路径。

审批暂停已结束的根run应按约定释放活动额度，恢复需重新准入；仍在等待子审批的活动父run继续占用自己的额度，live_child决定在原run内提交不误算新根run。失败/取消/保存未确认也须按最终settle结果释放额度，不能只在业务事件run_finished时提前释放。

验收使用事件同步控制A/B：不同会话可同时进入模型/工具，同会话拒绝重入，达到总上限不发请求；停A不影响B，A审批不消耗B授权，A保存失败不清掉B活动；独立checkpoint与全量关闭、恢复/删除竞态保持。相同目标Write/Edit继续原冲突合同，并发不等于对工作区文件隔离。首片不扩展多worker、多进程共享任务或SaaS。

## 9. 已确认H：三个设置操作复用异常映射

本节已按102-06实施，见[分片合同与实际结果](102-06-web-setting-errors.md#6-实际结果2026-10-18)。

select_thinking/select_tools/select_permission_mode都有准入、await应用操作、ValueError→特定422、安全诊断＋public_error、成功结果装配，达到第三次同职责重复。建议提取一个WebService内局部异步辅助函数，仅复用操作执行与异常映射；外层保留require_session/恢复门禁、具体调用参数与成功后确认值。

不同的错误码与诊断标签显式传递，工具/权限成功值仍取应用确认结果。保留取消和其他BaseException自然传播，不做重试、空catch或把所有错误统一422；不引入装饰器注册、操作字符串getattr或通用设置平台。结合第6项将准入改轻量读取，测试三项各自422/失败诊断/成功结果与取消，复用既有Web控件与模型选择测试。

## 10. 已确认I：DeliveredRun与RunDelivery文件边界

用户已确认本节最小文件拆分，已随101-02接线；下面416行等描述保留拆分前证据，当前类分别在delivered_run.py与run_delivery.py。

run_delivery.py当前416行，包含两个对象。DeliveredRun约负责前半部分：单run身份/started握手、跨线程事件接收、序号/有限重放队列、消息/工具/子Agent实时显示、快照与SSE唤醒；RunDelivery管理多run集合、启动/生产任务、全局准入、回执幂等、淘汰、控制及关闭。它们不执行Agent循环，也不拥有权威聊天档案或决定工具是否授权。

消息投影已复用application.StreamingMessageProjection，子投影已有web.agent_projection；不应再复制。建议最小拆分是将现有DeliveredRun整类移至web/delivered_run.py（实施前查重），run_delivery保留RunDelivery并直接导入。单run的投影、事件队列与wake/started状态继续在一个对象/同一短锁边界内，避免再增加一个RunProjection再转发全部属性，或为五种表面职责引入五个管理器。

这项主要改善文件阅读边界，不以416行本身超标为理由；最终是否继续拆单类，以Spec101分页调整后仍有独立职责/消费者为准。迁移时更新已有测试的导入，不保留仅为旧路径兼容的重导出壳。重放/丢帧/快照不完整、快完成注册、线程通知、终局状态、工具配对、子命名空间和HTTP断开不取消生产的合同不变。新聊天档案的必要保存属于Spec101，不交给DeliveredRun可降级观察器代管。

## 11. 已确认J：middleware顺序注释与行为验收

102-07已按[分片合同与实际结果](102-07-middleware-contracts.md#5-全量验收与spec102收尾2026-10-18)完成顺序注释与生产链验收；下方保留原合同。

当前_build_runtime_agent以单list注册多个hook/wrapper。既有test_agent_middleware_order已经用A/B中间件证明before按列表正向、after反向、wrapper首项在外层、返回反向；它不是当前生产列表语义的全部验收。核对本机LangChain factory中首wrapper在外层的真实装配，以及现有权限/批次行为测试，不因list外观猜执行顺序。

建议两种保护同时做：装配点用简短顺序表说明注册顺序不等于所有hook执行顺序，标出有业务含义的约束；复用现有测试并为实际生产链缺少的关键顺序补确定性断言，不写一条只比完整类名list的自证测试。

关键合同：权限准备先于审批中断，审批未决不进入工具执行；执行时授权核对在真实工具体前；工具普通错误在现有ToolError边界回填，控制/机制异常按既有合同；turn就绪标记在本轮after_model检查之后，缺配对结果不能完成turn；请求观察看到当前实际模型/工具/prompt。停止、中断、异常和jump会跳过部分hook，测试覆盖正常/批准/拒绝/停止及错误返回，不要求每个hook每次都出现。

不为写注释改变当前middleware排序；发现实测违约时另按bug复现与用户范围处理。低层/正式应用/子图注册差异都在注释和验收入口注明。

## 12. 正常与失败链的验收材料

正常：AsyncApplication.arun→service原子准入→冻结资源→原agent执行→安装pending或完成结果→子任务settle→持久确认→释放busy→终局。审批恢复另显示pending/turn→决定与grant→同thread新run→执行/再次审批，不能只展示公共helper。

失败：资源准备失败保留等待审批的原turn；工具阶段缺失结果仍进入人工核对阻断；取消等待已开始IO/子任务收尾；保存故障不报告ready；清理后旧run终局使用原身份快照，不清掉新run状态。接口封装不能减少这些行为断言。

## 13. 影响文件与验证计划

预计超过8个文件：session_service/session_controls/session_subagents/session_approval、session_persistence/session_recovery/session_context、async_application/async_persistent/session_storage及聚焦测试，必要时新增内部状态模块；先搜索实际消费者，不因引入类型顺便改子实例dict、重写整个Agent运行层或合并Spec101存储迁移。

迁移现有测试与必要内部探针到当前行为入口，保留断言，不因旧测试依赖私有属性保留并行旧状态模型。新公共行为返回限定快照，原外部应用API/事件wire/数据库字段不由本次重构擅自更改。

重点复用tests/test_async_application.py、test_turn_failure_boundaries.py、test_session_recovery.py、test_session_recovery_crash.py、test_recovery_pending_writes.py、test_context_inspector.py、test_web_session_management.py及已有子Agent/停止/持久化测试；状态默认值隔离、新建/恢复一致与跨对象不可修改等缺口补行为测试。后续实现先运行基线/必要红灯，再全量后端、前端及构建与diff检查；本轮只有源码/AST核对，无测试或构建完成声明。

第3/4项已确认，追加io_steps及Runtime/application装配、subagents、模型选择/压缩/toolset/权限的实际消费者与相关脚本/测试；不只把构造入口签名合并、内部仍散传旧参数。协议定义文件先按现有模块边界安放，不放入仅允许标准库事实的contracts后再引模型SDK依赖。第5/6项已确认，追加旧工厂/回调/文本、Web只读与查询迁移清单，复用Spec101接口；第8—10项已确认，追加局部异常映射、DeliveredRun现有类迁移及middleware关键行为测试。第7项仍待范围决定，当前均不实施。

## 14. 与Spec101的关系及下一步

Spec101是用户已确认的长会话可用性/历史独立保存目标；本稿第1—6、8—10项已确认，第7是范围待决定的并发能力升级，不当成已确认缺陷修复。具体分片与跨Spec关系见[Spec101](101-web-history-pagination.md#14-101-02实施合同本轮授权)。101-02已共同交付第6项，并将DeliveredRun整类移至web/delivered_run.py、迁移真实测试导入；run_delivery只直接消费该类，不设置旧路径转发壳。第8项三个设置异常辅助及其余重构仍未启动。

实施顺序：101-01归档/事务/恢复接线→101-02与本稿第6项共同交付轻量准入、显式打开和只读分页→101-03前端→101-04组合验收。第8/9项随实际文件协调，避免先改一遍全量view再改分页；DeliveredRun即使拆文件仍是缓存，不拥有聊天档案。第1—4项及第5/10项按其自己的切片推进，不作为历史修复的全量前置；归档进度保存在DB，SessionState只承载原会话生命周期事实，SessionPort不新增可降级归档回调。

后续移除IOCall/arun_steps时，直接复用101新增async整理和既有持久化行为，保留关键写入取消收尾，不再设计第二套归档链。当前全局单活动run保持；第7项若升级须核验归档按session/thread隔离、准备与finish不互相回退进度、完成A不更新B，但不在101中顺便开启并发。

Spec103 U1回复归属与U7显示重连依赖101的身份/来源和覆盖协议，独立Composer U5保持；请求侧供应商缓存沿M6独立，checkpoint修剪继续后排，修剪前须保证所需聊天已归档。聊天原文、调用usage与可淘汰诊断各有保留规则，不在本次生命周期重构中合并。

2026-10-18实施前基线：Spec101四片已收尾，[101-04结果](101-web-history-pagination.md#21-101-04实际结果与spec101收尾2026-10-18)为当前后端2266、前端186与TS/Vite210模块；恢复合并getter与无调用预览标记已回归保护。下一步先讲解第1项SessionState/行为拥有者的具体小片，再按确认推进；本行不构成剩余重构或跨会话并发已经实施。原归档、同scope协调及三次恢复链保持作为迁移验收基线。

## 15. 102-01实施合同（2026-10-18用户授权）

剩余已确认范围拆为7片：102-01 SessionState/受控入口；102-02原生async与共享运行阶段同时收敛；102-03旧工厂/record_selection及诊断预算消费者迁移；102-04最小SessionPort；102-05完整内容/流式文本投影；102-06 Web设置异常映射；102-07 middleware顺序说明及组合验收。原第6/9项已随101完成，不重复实现；原第7项跨会话并发仍未授权，不计入这7片。本次只实施102-01。

### 15.1 数据、拥有者与接口

新增application/session_state.py的内部SessionState dataclass，原18字段不增减；session_id仍为映射键。所有列表、deque按实例default_factory创建，不包含消息/checkpoint/聊天归档，不增加dict兼容、公开可变State或字段setter。SessionService._sessions改为dict[str, SessionState]，SessionControlMixin/SessionApprovalControls/SessionSubagentControls属于同一个self，内部属性访问及原锁边界保留。

复用SessionControlMixin安放新建/查询/受控安装行为，将现有create_session/create_workspace_session/_create_session/has_session/describe_session移入该内部拆分；SessionService运行/收尾留原文件，两个文件均≤500行，不新建第二个状态管理器。新增入口：get_thread_id、loaded_session_ids、snapshot_for_finish（thread_id/manual_review_required）、snapshot_for_context及context_snapshot_is_current（原预览所需配置/门禁事实）、approval_seed_identity（thread/batch或None）、install_restored_session（已规范化配置、可选pending）、deletion_guard、child_thread_ids、remove_session。

外部消费者不得访问_sessions或_lock。快照只投影所需字段，配置中的活动工具为tuple，公开describe保留list但不泄漏内部引用；pending安装与授权快照防御复制嵌套可变内容。上下文跨await后保留checkpoint config及当前配置/门禁复核，不用旧快照代替执行准入。

SessionPersistence继续负责存储/必要IO/_active/_recovering/_blocked/_prepared及checkpoint确认。增加astatus，原AsyncPersistentApplication状态组装移入拥有者并隔离prepared嵌套数据。内部工厂将同一个Persistence和Storage引用明确接入AsyncPersistentApplication（不改已有对外构造/应用API签名）；普通查询使用直接的Storage引用，生命周期使用Persistence，不穿透service._persistence.storage，不新建逐查询转发平台。SessionStorage.create仍接显式字段映射，SQL/schema8/归档格式不变，不asdict整个State。

### 15.2 调用链与原子边界

新建：acreate_workspace_session→run_blocking(create_workspace_session)→校验模型/工作区→构造SessionState→锁内安装→原Storage.create显式元数据→返回ID；必要保存失败撤销该次安装并raise原异常。

运行：arun→_execute→SessionService.arun→原arun_steps/_run_steps→同一锁内门禁检查及busy/run登记→原资源/图调用→原待审批安装→_afinish_run→子任务settle→Persistence.snapshot_for_finish取必要事实→原checkpoint/归档/元数据确认→释放busy→原终局。第一片保留IOCall及普通/恢复两个生成器，异常分类、控制信号、取消等待、保存失败阻断、原异常身份不变。

装载：aprepare→arestore/_restore_steps→原配置/工作区校验与checkpoint身份/终结性核对→Persistence._install_session在自身锁内核对恢复所有权→Service.install_restored_session在服务锁内核对重复安装并使用同一State构造器安装。异常恢复仍arecover→recovery_steps，三次图更新/审批验证/必要保存完成之后才安装；正常重启恢复沿原thread和保存的选择，瞬时队列/session_allow/run资源清空。失败不安装可运行State，不自动重放旧工具。

锁顺序保持Persistence→Service。deletion_guard是仅保护删除准入的短contextmanager：调用方先持Persistence锁，在guard内检查busy并登记_recovering后退出，guard不返回State/锁；DB/图IO在退出guard后执行。最终移除由Service.remove_session在服务锁内一次清理根状态和该会话子实例，Persistence在自身锁内保持恢复占用及blocked/prepared清理。恢复检查与安装同样不能拆成has_session后无锁写入，不改RLock，不持服务短锁做模型/文件/数据库IO。

### 15.3 文件、迁移和验收

生产修改超过8文件：session_state（新增）、session_service/session_controls/session_approval/session_subagents、session_persistence/session_recovery/session_context、async_application/async_persistent；Storage映射按原字段核对，不做数据库迁移。迁移实际测试/探针对旧dict的观察/故障注入：优先describe/线程身份/权限事实入口；只有专门验证内部State不变量或必要故障注入才使用State属性，不能保留__getitem__或旧dict作为测试兼容层。锁可用性专项测试可直接验证锁，但不使生产消费者获得锁。

先补并运行红灯：字段/default_factory隔离、有限快照、上下文过期复核、恢复安装去重与pending隔离、删除busy检查/短临界区、生产消费者无状态/锁穿透。复用真实临时SQLite新建/运行/重新装载，验证保存选择、thread、消息和临时状态复位；必要保存故障与原审批/取消/崩溃恢复断言保留。之后全后端、全前端、TS/Vite构建、AST/≤500行/compileall及diff检查。本次不发真实供应商请求，不改用户配置或已有用户库，不提交/push。


## 16. 102-01实际结果与下一片（2026-10-18）

### 16.1 实际接线与边界

新增的SessionState保持原18字段，只有新建与受控恢复安装两处构造；default_factory隔离列表/队列，无dict兼容、双状态写入或asdict持久化。SessionService仍拥有映射、锁和转换；SessionControlMixin是同一个self的文件拆分，现381行；session_service现421行，运行/审批恢复生成器及IOCall本片保留。

实际入口位于application/session_controls.py：新建/describe、线程身份、loaded IDs、finish/context快照及复核、approval seed身份、恢复安装、删除guard/子thread快照/移除。SessionPersistence._install_session只保留持久化占用检查，再调用install_restored_session；_finish_steps取两字段快照；_delete在Persistence→Service的短guard内登记占用，IO退出guard后执行。配置、权限、审批、子图、停止与收尾的根State访问均改为属性；子实例/图状态/SQL row继续各用原结构。

AsyncPersistentApplication通过内部工厂接入同一个Persistence/Storage引用；状态组装移入Persistence.astatus且prepared嵌套结果防御复制，查询直接使用Storage，恢复/准备/保存使用Persistence。AsyncApplication读取thread、关闭枚举和冷子历史不再穿透会话映射/锁/持久化对象。context_view先取有限快照，读取资源后检查当前状态和原checkpoint config，保持无模型调用的只读估算边界。

新建正常链：acreate_workspace_session→run_blocking(create_workspace_session)→SessionState→显式五字段元数据→Storage.create→ID；保存异常撤回安装并原样raise。运行链：arun→_execute→Service.arun→arun_steps/_run_steps→原子门禁/busy登记→资源/原图→pending/结果→_afinish_run→子任务settle→Persistence.afinish/_finish_steps→snapshot_for_finish→checkpoint/档案/元数据确认→释放busy/终局。正常装载：aprepare/arestore→原配置及checkpoint核对→_install_session→install_restored_session；异常恢复仍经recovery_steps的三次更新和必要保存后安装，或经根审批校验后恢复pending。失败不因dataclass迁移绕过原阻断、恢复分类和异常优先级。

旧消费者已迁移：通用busy/review/model/thread/permission观察走已有或新受控查询；专门的生命周期不变量/故障注入测试保留明确State属性和必要锁可用性断言，不保留旧字典。docs/examples/probe_session_recovery_design.py原dict复制/重装由专项架构复核发现并迁移到install_restored_session，原无提前工具执行、结果顺序、重开及新授权断言保留。生产代码扫描未发现外部穿透Service映射/锁/持久化的旧访问；装配工厂本身负责协作者接线。

### 16.2 验证与证据范围

基线命令（application_session/persistent_sessions/session_recovery/context_inspector）58 passed in2.18s。先新增test_session_state合同测试，旧代码10 failed/1 passed（缺少受控接口/状态和既有穿透），日志/tmp/naiyue-10201-red.log；随后实现与迁移，新增11项覆盖默认值隔离、有限快照/反向修改、上下文失效、pending防御复制、重复安装、删除guard及真实SQLite重开接线。

最终执行：

```bash
PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q --tb=short
npm --prefix frontend test -- --run
npm --prefix frontend run build
PATH="$HOME/.pi/agent/bin:$PATH" PYTHONPATH=tests:. .venv/bin/python docs/examples/probe_session_recovery_design.py
.venv/bin/python -m compileall -q naiyue_test_agent tests scripts docs/examples
.venv/bin/python -m pip check
git diff --check
```

后端2277 passed in99.87s（/tmp/naiyue-10201-backend-final.log），前端26文件186 passed（/tmp/naiyue-10201-frontend.log），TS/Vite210模块成功（/tmp/naiyue-10201-build.log）。新State/CLI搜索/恢复专项40 passed；最终所有权/事件/恢复专项43 passed in1.21s（/tmp/naiyue-10201-ownership-final.log）。恢复设计三个探针分别PASS：continue、pending writes、approval transport（/tmp/naiyue-10201-recovery-probe.log）。假模型仍会有stop_reason_unknown诊断；这些结果证明本机确定性链路，不替代供应商或生产业务验收。

验证期间第一次全后端未带此前验收记录的Pi bin目录，30项Find相关失败均因fd不可用，2247通过；已核对现有$HOME/.pi/agent/bin/fd且按上述项目命令重跑全绿，不安装工具、不改用户环境配置。AST检查误用系统Python3.9后已改为项目.venv Python3.12，380份Python的AST/≤500行、compileall、pip check和diff检查通过。架构与安全独立复核完成；文档探针遗留发现已关闭并再次复核无新增发现。

无依赖/配置/SQLite schema/事件wire/公开应用签名修改，无真实供应商请求或用户库操作，未提交/push；既有100/101/103工作树保留。用户学习位置和掌握记录不因助手实现更新。

### 16.3 下一片102-02（待讲解和实施授权）

将Service._run_steps/_resume_steps/_partial_review_steps、Persistence._finish_steps/_restore_steps/_recover_steps及Recovery.history_steps/recovery_steps迁到直接async调用；共享实际重复的准入登记、资源准备和失败核对，继续复用_afinish_run，不引入Operation/RunStrategy或新驱动平台。完成真实消费者迁移后删除application/io_steps.py及其专属旧测试入口，保留取消/已开始写入完成/异常身份等行为测试。

同步阻塞调用使用run_blocking，原critical=False图调用直接await，原critical=True必要异步写入/恢复/收尾继续await_settled。审批输入校验仍在新run与持久化开始之前；共同准入仍锁内完成。保留正常/compact/审批恢复的执行差异、三次恢复写入、子任务settle、ManualCompactionCommitError、GraphBubbleUp、重复取消和主/次异常优先级。Runtime摘要自己的steps不是本次应用驱动，不能按名字一并删除。102-03—07及跨会话并发均未实施。


## 17. 102-02实施合同（2026-10-18用户授权）

本片同时完成应用生命周期原生async和共有阶段收敛，不先新建生成器helper再迁移。沿102-01的SessionState/所有权/锁边界，不改公开应用API、SQLite schema、权限算法、事件wire或三次恢复顺序；不处理Runtime摘要steps、SessionPort、旧工厂、文本投影、跨会话并发和103 UI。

### 17.1 接口与文件

SessionService._run_steps→async _arun（普通消息/compact的已有公共执行体）；_resume_steps体并入既有aresume_tool_approval；_partial_review_steps并入既有_areview_partial_state。增加_admit（原子准入/登记）、async _prepare_resources（阻塞准备后冻结资源）、async _on_failure（原故障记录与部分状态核对）。_afinish_run保持既有收尾体。_admit只处理准入阶段的三种已有mode，不构造Operation/RunStrategy或回调流水线；检查与busy/run安装在同一个锁内，审批决定/remembered先校验再登记run，resume恢复原turn/接受control仍在busy安装之前。普通运行清空队列，resume保留原队列处理时点。

_on_failure共用取消/故障记录、部分工具状态核对和人工核对标记；普通入口传入已确定的ManualCompactionCommitError需人工核对这一事实，resume不扩大该异常的原特殊合同。入口except保存primary_error、await公共处理后原地bare raise，保留异常对象。删除两处无读取的review_required=False；_cleanup_run/_afinish_run的同名变量保留。

Persistence._finish_steps并入afinish且保留history lock；_recover_steps并入arecover且保留history lock/恢复占用；_restore_steps并入arestore；_configuration_steps→async _aprepare_saved_configuration。Recovery.history_steps→async read_session_history，recovery_steps→async recover_session。按真实消费者更新aprepare/ahistory及相关调用；最后删除application/io_steps.py，无新通用驱动或兼容转发。

### 17.2 await与取消合同

阻塞存储/文件准备→await run_blocking；原critical=False图或compact执行→直接await；原critical=True异步操作（含checkpoint读取、归档整理、恢复写入、部分状态核对与最终收尾）→await await_settled。纯计算的快速校验/结果解析按同步直接调用，保持在原try/except/finally内，不包装线程或另建执行器；validate_saved_approval会重算真实路径，保留run_blocking。资源准备helper自身直接await，内部run_blocking只保护已开始的阻塞调用；不能将整个run/recovery整体shield成取消后仍自动执行余下步骤。

正常：arun→_arun→_admit原子登记→observation.start→run_blocking(persistence.start)→_prepare_resources→原生图await→原pending/结果安装→finally await_settled(_afinish_run)→子任务settle/必要保存→释放busy/终局。审批恢复：aresume_tool_approval→_admit校验pending/decisions/remembered并恢复turn→start→原grant与session_allow时点→_prepare_resources→原Command恢复→next_pending/resume_start_index→同一收尾。

失败：图抛错→原观察记录/必要部分状态核对→原manual_review判定→bare raise原对象；finally保护已开始的清理/保存。反复取消不提前释放busy/关闭库，内层异步任务自己取消继续传播；等待中的IO失败按原await_settled优先级诊断并传播取消。必要保存失败仍阻断；GraphBubbleUp/进程控制不当普通工具故障。

恢复：arecover占用→recover_session原身份/配置核对→逐步await_settled原checkpoint操作，保持END→tools→END及每次身份/marker复核→原归档和元数据确认→安装State；取消等待当前已开始的步骤完成后退出并释放占用，不继续重放工具，后续可根据已保存phase重试。

### 17.3 验证与旧消费者迁移

先增加原生async/无Yield/无IOCall的合同红灯；把test_architecture_semantics原IOCall结构测试迁为正式Service运行/收尾取消测试，保留“写入完成→终局/清理完成→取消传播”的检查，并覆盖写失败/取消优先级、run_blocking返回值与原异常身份。复用恢复取消、pending writes、重复取消、主/次失败、无效审批不启动、普通/compact/审批恢复及子任务settle现有用例。

源代码预期3文件修改、io_steps删除，新增聚焦测试/迁移既有架构测试；必要当前文档更新，历史Spec/课堂只标版本边界。按既有PATH运行相关基线、红灯、专项与全量后端，前端全量及构建、恢复设计探针、compileall/AST/500行/pip check/diff检查。无真实供应商请求、用户配置修改或用户库操作，保留已有脏树，不提交push。


## 18. 102-02实际结果与下一片（2026-10-18）

### 18.1 当前主链及删除项

SessionService._admit在一个服务锁内完成manual/pending/busy检查、审批输入校验、原turn/control准备及busy/run安装，返回该服务内部使用的State/run_id/observation；只有既有message/compact/approval_resume三种调用。_prepare_resources直接await run_blocking文件准备，成功后短锁冻结资源；_on_failure共用原观察、部分状态核对与人工核对标记，普通入口传入已经确定的ManualCompactionCommitError事实，恢复入口保留原判定。两个入口各自bare raise原异常，并在finally await_settled(_afinish_run(...))。

当前正常链：AsyncApplication.arun→_execute→Service.arun→_arun→_admit→run_blocking(Persistence.start)→_prepare_resources→直接await原图→原pending/结果→finally保护_afinish_run→settle/assert子任务→_cleanup_run→Persistence.afinish（history lock）→checkpoint读取/归档整理/SQLite必要确认→释放busy→终局。compact共用_arun但直接await compact_session并保持steering关闭。

审批链：aresume_tool_approval→_admit在新run开始前校验decisions/remembered并恢复turn→start→原一次性grant/session_allow时点→_prepare_resources→直接await aresume_agent_run_with_resources→next_pending/resume_start_index→共同收尾。必要保存前的检查、拒绝/停止控制效果与原错误优先级不变。

冷装载：arestore→run_blocking读取元数据→_aprepare_saved_configuration→await_settled读取checkpoint→原身份/结束校验→_install_session→Service受控安装。异常恢复：arecover持history lock并登记恢复占用→recover_session逐步读取/核对→END→tools→END及每步marker/身份检查→归档/元数据确认→安装；取消等待当前已开始操作完成后退出并释放占用，后续按已有phase继续。浏览历史走ahistory→read_session_history，仍只描述事实。

删除application/io_steps.py及IOCall/arun_steps，删除Service._run_steps/_resume_steps/_partial_review_steps、Persistence._finish_steps/_restore_steps/_recover_steps/_configuration_steps、Recovery.history_steps/recovery_steps。无兼容别名/新通用驱动/新Operation对象，实际生产/测试/脚本/文档探针Python消费者扫描无遗留。Runtime摘要steps与子智能体已有async _subagent_steps保留，不以名字删除。Service379行、Persistence215行、Recovery321行，底层async_utils未改。

### 18.2 验证结果与边界

相关基线57 passed in1.65s。新增直接async/无Yield/无IOCall合同，旧代码3 failed/1 passed（/tmp/naiyue-10202-red.log）。旧IOCall结构测试先迁到正式Service保存取消链，旧实现2 passed：重复取消仍等待保存和终局，保存故障在取消后有诊断并保持取消优先级；原IOCall无效构造校验随接口删除，blocking返回值42与原异常身份改由生产run_blocking行为测试覆盖。保留现有恢复取消、pending writes、主/次错误、审批、停止与子任务配对断言。删除驱动前的第一次专项仅剩预期的旧文件存在断言失败，消费者核对后删除文件，最终62 passed in1.61s。

实际执行：

```bash
PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q --tb=short
npm --prefix frontend test -- --run
npm --prefix frontend run build
PATH="$HOME/.pi/agent/bin:$PATH" PYTHONPATH=tests:. .venv/bin/python docs/examples/probe_session_recovery_design.py
.venv/bin/python -m compileall -q naiyue_test_agent tests scripts docs/examples
.venv/bin/python -m pip check
git diff --check
```

后端2282 passed in99.99s（/tmp/naiyue-10202-backend.log）；前端26文件186 passed（/tmp/naiyue-10202-frontend.log）；TypeScript/Vite210模块成功（/tmp/naiyue-10202-build.log）。最终用事件循环屏障确认第一次取消已投递，再发送不同消息的第二次取消，明确检查仍传播第一次取消；该加强后的测试在独立进程导入只读实施前备份也2 passed（/tmp/naiyue-10202-cancel-old-verified.log），没有切换/还原工作树。当前加强后的完整专项62 passed in1.43s（/tmp/naiyue-10202-final-contract.log）。恢复设计continue/pending writes/approval transport三探针PASS（/tmp/naiyue-10202-recovery-probe.log），固定假模型有既有stop_reason_unknown诊断。380份Python的AST/单文件500行、compileall、pip check、diff与当前文档本地文件链接检查通过。架构与安全独立专项对照实施前文件核对锁、授权时点、逐项await/取消和恢复顺序，无新增确证发现。

仅3个生产文件修改及io_steps删除，新增聚焦测试并迁移架构测试；当前README/交接/计划/实施路线与085/086文件地图同步入口，历史Spec/课堂保留原版本边界。无新依赖、用户环境配置、SQLite schema、事件wire或公开应用签名修改，无真实供应商请求或用户库操作，未提交push，原100/101/102-01/103工作保留。测试与本机探针不替代供应商/Windows/Linux原机或用户掌握证据。

### 18.3 下一片102-03（当时建议，现已获本轮授权）

先迁移build_configured_runtime_agent的模型配置/选模/连续消息/预算失败测试到正式应用装配及独立ModelCatalog入口，再删旧工厂及仅服务它的导入。record_selection当前主应用/子图为空lambda，低层测试和若干探针仍使用；移除前迁移实际消费者。选模观察用真实resolver/现有model_selected日志或已有事件，真实探针预算优先复用scripts/provider_acceptance_budget的RequestBudget/guard_catalog在HTTP发送前计数，离线假模型在自身调用入口限次。保留两次/一次等各探针预算，明确SDK重试/摘要/Auto/子调用口径，不改生产重试或业务循环限额。

之后从make_model_selection_middleware/_ModelSelectionMiddleware及Runtime内部装配和根/子应用接线删除record_selection参数/调用/空lambda，不保留转发壳，也不把空方法搬到后续SessionPort；README诊断命令随实际入口迁移。删除真实API若超出已确认内部消费者范围须先明确，不假设Python可import即永久外部合同。文本投影、SessionPort、Web设置、middleware顺序分别留102-04—07；跨会话并发仍待定。至此7片中已完成2片，剩余5片。

## 19. 102-03实施合同（2026-10-18用户授权）

本片只迁移旧工厂/选模回调的实际消费者并删除旧入口；不引入SessionPort、不改变业务选模/审批/重试语义。当前正式根/子装配给record_selection的都是空lambda；测试用其观察选模，诊断脚本还用其限制调用次数。旧工厂自行读配置但没有应用会话及资源生命周期，因此不能继续作为当前装配入口。

### 19.1 修改位置与调用链

- agent_runtime/__init__.py删除build_configured_runtime_agent及专用load_model_catalog导入；build_runtime_agent、_assemble_interactive_runtime、_build_runtime_agent删除record_selection参数/转发。call_observation.make_model_selection_middleware及_ModelSelectionMiddleware只接收resolve_model，_prepare仍检查session/model、解析模型、发布原有日志/事件并调用handler。application/__init__.py与agent_runtime/subagents.py删除空lambda。
- 配置＋工具后切换/连续历史测试迁到build_async_local_application→acreate_session→arun/aselect_model→aclose；独立预算观察测试用load_model_catalog→build_runtime_agent→ainvoke，明确由测试关闭catalog，保留原错误身份、预算字段、日志脱敏及配对断言。低层选模断言在已有resolver中记录；原“回调报错不归类模型故障”迁到必要的resolver报错，仍检查原异常及无模型故障日志，不保留空观察接口。
- probe_configured_tool_loop改为正式异步应用，probe_message_events/probe_persistent_session不再替换Runtime装配器。三者复用provider_acceptance_budget中RequestBudget/guard_catalog；同一职责第三次出现，在该现有脚本模块增加guard_application_catalog异步上下文管理器，只在诊断作用域包装应用loader：配置读出后安装HTTP预算，再交给正式应用负责关闭；安装失败产生的catalog由管理器补偿关闭。没有生产hook或新业务抽象。离线持久化模型在_generate入口reserve。
- probe_async_model保留其原生异步/摘要状态流的低层验证入口，直接给独占catalog安装相同HTTP预算并finally关闭；通过既有model_call_started事件观察选模，probe_tool_batch移除空参数。当前README的诊断入口指引同步迁移。

正常例子：探针预算2→应用loader→独占catalog的SDK客户端HTTP前置hook→正式应用创建会话→第1次HTTP→工具执行/ToolMessage→第2次HTTP→验证配对与结果→应用关闭catalog。模型解析、预检不消耗额度。摘要、Auto、子调用只要经过这些客户端都计数；流式chunk不额外计数。

失败例子：工具后模型意外要求第3次HTTP，hook先reserve时抛AcceptanceBudgetStop，传输层没有第3次发送；原异常沿图传播，既有finish/close执行。诊断独占catalog的SDK重试设0，防止一次逻辑调用隐含多次发送；生产配置重试不改。安装预算失败时管理器关闭已取得的catalog并保留原异常，清理失败要有诊断，不覆盖主异常。

### 19.2 验收范围

先跑相关基线，再以缺失旧入口/无回调签名及探针接线/失败清理测试确认红灯；迁移旧测试/helper仅因入口签名退役，保留行为断言，不放宽/skip。Mock HTTP检查探针实际预算安装、1/2次预算、摘要共享与第三次发送前阻断，离线探针验证两进程连续会话；所有请求都为Mock或假模型，不使用真实配置/供应商。各探针既有进程期限不变。最后完整后端/前端/构建、compileall/pip check/diff及消费者/500行检查。102-04仅讲方案，不实施。


补充边界（架构复核后、扩展守卫实现前更新）：原guard_catalog仅接受纯OpenAI目录，会导致混合配置中选OpenAI的诊断也失败。应扩展到项目已经支持的ChatOpenAI、ChatAnthropic、ChatOllama三类适配器，核验catalog-owned同步/异步HTTP客户端后安装同一发送前预算。OpenAI/Anthropic的模型及SDK重试只在诊断独占catalog设0；Ollama SDK本身不做重试，直接HTTP调用计数。未知适配器仍在发送前报错，不做不受预算保护的回退。增加混合目录与Anthropic/Ollama Mock HTTP测试，不增加供应商/依赖或修改生产重试。此调整是保留现有探针支持范围，非产品新增能力。

测试中的Fake/Echo适配器没有HTTP，以tests/probe_budget_helpers.guard_fake_catalog在框架模型生成开始前消费预算，raise_error=True；只替换脚本预算安装器，实际应用/探针、摘要及CLI显示链仍执行。新增该测试helper是三个既有探针测试的共同迁移需要，未改conftest或全局测试配置。HTTP Mock另独立验证真实SDK钩子和关闭，不能用Fake计数替代HTTP证据。

Anthropic的SDK会把不属于AnthropicError的Exception包装为连接故障；因此仅HTTP钩子还不够。AcceptanceBudgetStop在原OpenAIError/GraphBubbleUp之外继承现有AnthropicError，保持预算停止本身穿过SDK及Auto，不误判成网络失败或Ask；不新增异常类。对应Mock测试检查原预算停止类型、实际发送次数及SDK重试0。

全量消费者核对补充：make_model_selection_middleware还有两份故障/拒绝测试以第二个位置参数传空回调（而非record_selection关键字），同片迁移且保留原断言；独立probe_context_compaction也有该位置参数，先运行确认TypeError后移除该空参数，保留成功摘要/工具配对/旧checkpoint及摘要失败不调用B模型的原实验断言。最终扫描同时按AST核对关键字与调用参数个数，不能只搜名称。

## 20. 102-03实际结果与下一片（2026-10-18）

### 20.1 实际接线与删除边界

四个生产文件修改：Runtime __init__删除build_configured_runtime_agent、专用配置导入及record_selection参数转发；call_observation删除该回调，只通过resolve_model取模型，原model_selected日志及模型事件保持；根application与子Runtime删除空lambda。未新增生产文件/类，无兼容别名。build_runtime_agent与make_model_selection_middleware仍是低层模型/框架验证入口，未删除实际能力。

配置装配、工具后切换、连续历史两项测试迁至test_configured_application，经正式异步App创建/运行/切模/关闭；test_model_limits保留独立catalog＋Runtime预算观察路径。原选模顺序由必要resolver或已有日志/事件检查；解析失败检查原异常身份与无模型故障误记。其余生产、测试/helper、脚本及docs/examples按AST核对，无旧工厂调用、record_selection关键字或middleware第二位置参数消费者。

六个探针脚本随入口迁移；其中configured/events/真实持久化走正式App＋局部guard_application_catalog，native async诊断直接保护并关闭catalog；离线持久化自身_generate消费一次预算，tool_batch/context_compaction仅移除空参数。共用RequestBudget对OpenAI/兼容、Anthropic、Ollama的独占SDK HTTP客户端发送前计数，包括摘要/Auto/子调用，不数解析/预检或流chunk。OpenAI/Anthropic仅诊断重试归零；Anthropic预算停止保持GraphBubbleUp和SDK异常身份。loader安装失败由管理器关闭已取得catalog，清理错误有诊断、原异常优先；正常catalog仍由App关闭。

### 20.2 验证与实际结果

基线183 passed in6.97s。新增删除入口/无回调签名及guard装载/清理合同先3 failed（/tmp/naiyue-10203-red.log）；首轮专项109 passed，迁移探针77 passed。第一轮全量暴露两份测试以第二位置参数传空回调，4 failed/2288 passed，迁移保留原拒绝/诊断异常断言；最终AST扫描还发现独立摘要实验的同类空参数，先直接运行确认TypeError，再移除，原实验断言全部通过。架构复核指出混合目录回归，先新增6 failed（/tmp/naiyue-10203-mixed-red.log），扩展到三种既有SDK并补齐Anthropic控制异常传递后，相关71 passed（/tmp/naiyue-10203-review-regression.log）；独立架构/安全复核均无确证遗留发现。

最终实际命令：

```bash
PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q --tb=short
npm --prefix frontend test -- --run
npm --prefix frontend run build
PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m scripts.probe_persistent_session
PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m scripts.probe_tool_batch
.venv/bin/python -m scripts.probe_context_compaction
.venv/bin/python -m compileall -q naiyue_test_agent tests scripts docs/examples
.venv/bin/python -m pip check
git diff --check
```

后端 **2298 passed in100.99s**（/tmp/naiyue-10203-backend-final.log）；前端 **26文件186 passed**（/tmp/naiyue-10203-frontend.log）；TypeScript/Vite **210模块构建成功**（/tmp/naiyue-10203-build.log）。相较片前新增16项后端回归，原行为测试迁移并增强异常/事件断言，无skip/放宽检查。新测试helper只为没有HTTP的Fake/Echo探针模型计数，未改conftest/依赖。HTTP Mock分别检查正式App/CLI/native async/压缩/真实持久化worker的1/2次实际发送、第三次发前拒绝、客户端关闭、混合目录及Anthropic/Ollama同步/异步限额；不能等同供应商真实验收。

离线两进程持久化PASS，sid/thread稳定、run更新、checkpoint前进且历史被下一进程实际使用（/tmp/naiyue-10203-persistence-probe.log）。tool_batch并行互见、配对及执行前拦截断言通过（/tmp/naiyue-10203-batch-probe.log）；摘要实验成功切模/旧checkpoint及失败不调用B模型断言通过（/tmp/naiyue-10203-compaction-probe.log）。386份Python AST/≤500行及退役消费者扫描、compileall/pip check/diff通过；README当前诊断入口和交接/计划同步。无用户配置、SQLite schema/用户库、依赖或公开App API变动；无真实供应商请求，Windows/Linux原机无新增证据。未提交/push，原100/101/102-01/02/103工作保留。本片助手实现/验收不新增用户学习掌握声明。

### 20.3 唯一下一步

审阅下一节102-04的最小SessionPort协议、根/子装配与作用域例子，再获准实施。7片已完成3片，剩余04—07共4片；跨会话并发与缓存/usage/诊断/checkpoint修剪及103其余UI各自独立。

## 21. 102-04准备方案（当时供审阅，现已获第22节实施授权）

目标仅是收束应用→Runtime装配边界的真实会话依赖。新增agent_runtime/session_port.py，标准库typing.Protocol列出现有行为；SessionService按结构实现，不强制继承、不加运行时服务注册表。不创建第二份SessionService、SessionState副本、每agent适配对象或缓存。

| 协议行为 | 当前生产者/输出 | 实际消费者及作用域 |
|---|---|---|
| resolve_model(session_id, agent_path) | SessionService；(model_id, BaseChatModel) | 选模与压缩；根读当前选择，子读实例模型快照 |
| resolve_active_tool_names(session_id, agent_path) | SessionControlMixin；tuple[str, ...] | ToolSet；根读活动集合，子读冻结工具集合 |
| resolve_permission_context(session_id, agent_path) | SessionControlMixin；模式/session_allow/审查项目约束 | 权限middleware；保留当前规则和批次取值时点 |
| resolve_prompt_parts(session_id) | SessionService；list[dict] | 提示词与预算；当前根/子共享已准备资源，不宣称新隔离 |
| get_registered_subagents() | SessionSubagentControls；tuple[SubAgentSpec, ...] | 活动subagent工具的角色短描述 |
| is_stop_requested(session_id) | SessionControlMixin；bool | 根interactive hook；子停止仍走既有RunControl |
| drain_steering(session_id) | SessionControlMixin；list[str]并消费根队列 | 根before_model，子图不注册这个hook |
| finish_run_boundary(session_id) | SessionControlMixin；(stopped, messages)并选择根队列 | 根after_agent，子图不注册这个hook |
| prepare_control_messages(session_id, messages) | SessionControlMixin；HumanMessage列表及可信人类标记 | 根控制注入，保持控制事件/消息身份 |

修改application._assemble_application为向_assemble_interactive_runtime传session_port=sessions；SessionSubagentControls.register_subagent向build_subagent_runtime也传同一port。根Runtime在自己的hook取必要方法；根/子装配把具体的port.resolve_model/resolve_active_tool_names等交给现有middleware。middleware仍只接自己需要的方法，不要求它们认识整个应用或State。独立build_runtime_agent与单项middleware探针的既有真实resolver接口可保留，它们是低层独立消费者，不是应用旧回调转发壳。

ToolRegistry、默认模型/catalog、system_prompt、response_format、checkpointer、权限静态设置/reviewer仍按原职责传参；事件/RunServices仍走原可信运行上下文，不塞进Port。当前正式应用以prompt_parts组装资源，resolve_project_instructions的独立测试/探针消费者先迁到真实parts入口再删除interactive旧参数；独立ToolSet低层消费者按真实用途决定，不能直接移除行为。该迁移的具体列表在实施前再次核对。

身份仍来自每次调用的可信context：session_id、agent_path及已有run_id。协议整合不把当前身份存进共享图。根专用控制hook显式限定root，子图不获得根队列消费行为；已有run_id核验与控制/事件身份保护保持，不重做授权或新建Scope框架。若需新增运行身份检查，须在实施合同中列出具体调用点和失败行为，不能默默扩展本片语义。

正常例子：会话s选模型A，子路径root/tester/i启动并冻结A/工具集合；用户切根模型B，下一根请求port.resolve_model(s, root)返回B，已有子请求port.resolve_model(s, root/tester/i)仍返回A。根Steering仅由根下一模型前消费，子请求不取根队列。失败例子：不认识的子路径按已有subagent_instance检查失败，不回落root模型/工具；根/子错误、停止、审批恢复、关键保存保持原传播和时点。

验收重点为实际接线而不是Protocol存在：两个会话、两个兄弟子实例、根切模型/子快照、动态工具/权限与prompt来源、根队列不被子消耗、原审批/停止/异常身份。测试替身仅实现当前场景真正会调用的行为，不创建万能空对象；生产Runtime不import应用具体类。102-05文本、06设置、07顺序及跨会话并发不在04实施范围。

## 22. 102-04实施合同（2026-10-18用户授权）

落实第21节的9方法SessionPort，只收束应用→Runtime根/子装配入口。新增agent_runtime/session_port.py的typing.Protocol，方法使用现有名称/签名（显式传session_id，模型/工具/权限另有agent_path，prompt_parts当前按会话共享）。SessionService按结构实现，无强制继承/运行时registry/第二份State；Runtime不导入具体应用。默认模型/catalog、ToolRegistry、system_prompt、response_format、checkpointer、observe_turns与权限静态设置/reviewer保留原参数。

根_assemble_interactive_runtime必传session_port，删除9类独立会话回调参数；hook使用port的停止/队列方法，toolset、compaction与_build_runtime_agent仍接所需的具体bound method。application._assemble_application直接传sessions，SessionSubagentControls.register_subagent也将self交给build_subagent_runtime；子装配只取模型/工具/权限/prompt，不注册根控制hook。独立build_runtime_agent、单项middleware的真实resolver入口保留，不变成新旧应用双链。

当前旧项目指令消费者只有tests/test_async_context_chain.graph_for与scripts/probe_async_model的interactive分支，同片改为显式project提示词资源列表。它们用各自局部SimpleNamespace列出真实需要的方法，沿原假模型/合成输入/有限预算执行，不新增通用空Port或生产适配类。ToolSetMiddleware没有独立的resolve_project_instructions消费者；删除该参数、字段与旧fallback，仅保留有实际低层消费者的“未提供prompt_parts时空列表”语义，roles/parts参数改为显式关键字。当前Runtime装配/探针/测试全链路同步，不保留旧签名兼容。

根控制边界新增一个直接检查：interactive的reset_run_control、inject_before_model、inject_at_natural_end调用同一个_root_session_id(runtime)检查可信agent_path为root，非root抛ValueError('Interactive runtime requires root agent_path')，之后才进行根停止/消费队列。此函数只校验当前root hook输入，不保存作用域或解析权限；同一检查有三个真实消费者。其他既有model/tool/permission的子路径检查可能更早失败，同样不得消费根队列。子图仍用原RunControl/RunServices及父run绑定；不新增run_id协议参数、不改授权检查或重放合同。

正常链：AsyncApplication建图→SessionService作为SessionPort→根/子各自装配→middleware按当前context的session_id/agent_path调用bound resolver→取得根实时选择或子实例冻结模型/工具→原handler执行。根Steering/Follow-up仍在原hook时点取出、准备可信HumanMessage、原事件配对；项目资源仍为本次运行准备好的parts。失败链：未知子路径在原实例查找抛原KeyError，不回落根；把interactive根图误用于非root时，在控制方法之前拒绝；普通模型/工具失败、控制信号、关键保存及取消身份保持。

验收先跑相关基线，再补协议/实际根子装配及非root控制拒绝的失败测试。已有跨会话、继承模型冻结、兄弟子历史/工具/审批/停止/压缩用例沿用；新增组合用例用事件屏障同时挂起两个子请求，修改根模型/活动工具/权限并排入Steering/Follow-up，检查子快照和prompt来源、根队列未被消费、随后根按新选择续跑及另一会话独立。不得用紧sleep或只检查接口存在代替真实运行。修改旧test_runtime_entry_cleanup的签名断言为新Port与其根边界合同，保留“唯一自然结束入口”约束。新增模块/类、各消费者接线与旧函数/分支删除按L1/L2/L3核验；最后完整后端、前端、构建/依赖/AST/500行与消费者检查。无真实请求/用户配置/依赖/schema/用户库修改，102-05仅讲解不实施。


## 23. 102-04实际结果与下一片（2026-10-18）

新增32行agent_runtime/session_port.py声明9个现有行为，SessionService按结构实现，未改Service签名/状态。根application._assemble_application和SessionSubagentControls.register_subagent均传同一个sessions/self；根_assemble_interactive_runtime、子build_subagent_runtime接收Port，并把模型/工具/权限/提示词所需bound method交给原middleware。静态设置、registry、saver与RunServices沿原路径；未引入包装类、Scope缓存或服务定位器。Runtime协议无application导入。

_root_session_id由根reset/before_model/after_agent三hook调用，在停止状态/取队列之前拒绝非root；子图不注册根hook。模型、工具、权限按sid/path查询的原快照/取值时点保持，未知子路径仍抛原KeyError。两处低层interactive消费者迁为各自显式Port方法；旧resolve_project_instructions参数/字段/fallback已无真实消费者并删除，prompt内容/压缩预算不变。纯build_runtime_agent及单项middleware仍接必要resolver，有实际消费者，不是兼容转发。

当前主链：CLI/Web→AsyncApplication→SessionService→根/子装配(session_port=sessions)→middleware按可信context取sid/path→原模型/工具/权限/提示词处理。根控制仍在原hook消费、准备HumanMessage并发原事件，子控制仍用原RunControl。6个生产文件（含新协议）、3份测试和1个探针修改，无其他产品行为切片；Spec/README/交接/计划/地图同步，102-05未实施。

基线114 passed in2.35s。新增4项合同/组合用例，旧实现3 failed/1 passed（/tmp/naiyue-10204-red.log）：协议/根子接线/非root拒绝缺失为预期红灯，两个子同时阻塞期间切根模型/工具/权限及Steer/Follow-up的原行为先通过。迁移后专项74 passed in1.94s；完整后端 **2302 passed in101.72s**（/tmp/naiyue-10204-backend.log）。前端 **26文件186 passed**（/tmp/naiyue-10204-frontend.log），TypeScript/Vite **210模块成功**（/tmp/naiyue-10204-build.log）。

实际命令：`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q --tb=short`、`npm --prefix frontend test -- --run`、`npm --prefix frontend run build`；另执行`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts docs/examples`、`.venv/bin/python -m pip check`与`git diff --check`均通过。388份Python AST/500行及Port真实调用/退役参数扫描通过，协议依赖无应用导入。离线两进程持久化PASS（/tmp/naiyue-10204-persistence-probe.log）；恢复continue/pending-writes/approval-transport三探针PASS，含已保存结果、不重放、混合串行批及新一次授权（/tmp/naiyue-10204-recovery-probe.log），假模型有原stop_reason_unknown诊断。架构及安全独立复核无确证遗留发现，安全独立62项通过。

无依赖、公开App API、事件wire、SQLite schema、用户配置/用户库修改或真实供应商请求；Windows/Linux原机无新增证据。未提交/push，原100/101/102-01—03/103工作保留。本轮为助手实现/验证，不新增用户掌握声明。7片已完成4片，剩余05—07共3片；唯一下一步审阅第24节文本投影方案，跨会话并发及103其他UI/缓存/usage/诊断/checkpoint修剪各自独立。

## 24. 102-05准备方案（供审阅，尚未实施）

目标收敛完整内容与流式文本的重复解析，保留两者已有语义。当前model_access.messages.content_text处理字符串、列表字符串、text块及有限图片说明，strict拒绝未知结构；CLI._text也接列表字符串但跳过图片；async_message_observation._text仅接字符串或text块，跳过列表字符串/图片/其他块；Web.serialization.text_content仅转发content_text。

建议只扩展现有content_text(content, *, strict=False, delta=False)：默认完整投影保持原语义；delta=True保留当前流式合同，字符串直接返回，列表仅拼合法text块，忽略列表字符串/图片/非文本块，不生成图片说明。未知结构在strict=True时仍拒绝，图片作为已识别内容不泄露像素/URL；不引入投影类/策略注册表。该新增关键字待本片确认，旧调用默认输出保持。

删除CLI._text与Runtime._text，分别直接调用content_text默认模式和delta=True；CLI的_safe_text、非文本提示/去重与事件账本保留，完整图片消息增加与Web一致的有限图片说明。删除Web.text_content纯别名；当前实际消费者为serialization自身、agent_projection及chat_history，同片直接导入content_text，run_delivery只调用message_view无需改动。摘要/子报告仍用原默认/strict调用，canonical消息和图片请求适配不改。

例子：`['开头', {'type':'text','text':'正文'}, 图片块]`在完整投影为“开头正文＋图片元数据说明”，在delta投影仅为“正文”；纯图片不生成message_delta。未知块宽松跳过、strict拒绝；文本中的终端控制字符仍由CLI._safe_text转义。Responses空首chunk的ID锚定、增量序号及最终消息归并保持，不能因统一解析跳过ID登记或制造空/图片delta。

准备修改model_access/messages.py、application/cli_display.py、agent_runtime/async_message_observation.py、web/serialization.py、web/agent_projection.py、web/chat_history.py及对应测试；不新增生产文件/类。先以字符串/字符串列表/text/图片/未知/strict矩阵明确红灯，再验证根子Web/CLI、摘要、完整快照哈希/正文预算、流式ID/配对和图像像素不进文本；最后完整回归。102-06设置异常与07middleware顺序另片推进。

## 25. 102-05实施合同与结果索引（2026-10-18用户授权）

[102-05独立实施合同](102-05-content-projection.md)承接第24节：扩展现有content_text的delta模式，迁移CLI/Runtime/Web实际消费者后删除重复解析和纯转发，保留strict、图片说明、终端转义及Responses ID/序号。主Spec接近500行，测试/调用链与实际结果集中到该分片；已完成，后端2345/前端186及210模块构建通过，新增43项先红后绿。此为102-05收尾时点；当前进度见第26节。


## 26. 102-06设置异常映射（已实施）

本节保留实施前合同，已按[102-06分片](102-06-web-setting-errors.md#6-实际结果2026-10-18)完成并验收。改前WebService.select_thinking/select_tools/select_permission_mode各自先_session_status做轻量身份/工作区检查、拒绝recovery，再try/await应用写操作；ValueError映射各自422，其他Exception记录安全类型并交public_error，成功后组装各自结果。这是同职责第三次重复，局限在web/service.py。

建议在WebService新增async _apply_setting(operation, *, invalid_code, invalid_event, failure_event)，直接await已确定的应用协程；不传操作字符串解析、不新建回调/装饰器/设置平台。三个外层方法保留原_session_status/recovery门禁，在其之后创建并传协程；helper只处理await和异常。按原顺序先捕ValueError（含当前ModelConfigError继承关系），记录指定warning并抛对应422；其他Exception记录指定错误标签/类型，再public_error；CancelledError等BaseException自然传播，无任务包装/重试/额外shield。

错误码与日志保持：thinking_unavailable / web_thinking_selection_invalid / web_thinking_selection_failed；invalid_tool_selection / web_tool_selection_invalid / web_tool_selection_failed；permission_mode_unavailable / web_permission_mode_unavailable / web_permission_mode_failed。成功结果仍在外层：thinking回model_id/level，tools和mode从应用getter读确认值；不把成功getter异常或前置门禁塞进新捕获范围。select_model只有另一种映射合同，本片不合并；web/settings.py配置对象也不修改。

例子：PATCH tools→鉴权/请求schema→select_tools→_session_status及recovery检查→_apply_setting(awaitable)→原aset_active_tools→注册表校验/必要保存/会话更新→外层get_active_tools返回。未知工具仍422 invalid_tool_selection；保存异常仍由public_error给安全公开码，日志不含输入/异常正文；取消保持原传播。保留动态设置生效时点和已有审批，未新增busy门禁或更改授权。

本片只改web/service.py、增加聚焦失败/取消测试并复用HTTP设置/推理档位用例。先固定三项各自成功结果、422/日志、保存失败映射及取消身份；门禁拒绝时不创建/执行应用协程，鉴权/跨工作区/恢复、原审批和下一批生效按现有HTTP测试验收。完成后完整回归；102-07及跨会话并发独立。 已通过后端2407/前端186及210模块构建；此为102-06收尾时点；102-07现已完成，见第27节。

## 27. 102-07实施与Spec102收尾（2026-10-18）

[102-07合同/调用链/完整结果](102-07-middleware-contracts.md#5-全量验收与spec102收尾2026-10-18)：两处装配只补注释、AST与排序不变，新增8项真实根子生产链验收，修正探针实例绑定后后端2415/前端186及210模块构建通过。102-01—07七片均完成，第6/9项随101落地。此前收尾下一步原定103；用户随后调整为先105会话行为归属五片，再104跨会话并发三片，之后逐项处理缓存/用量/诊断再回103；精确入口见本文顶部版本边界。
