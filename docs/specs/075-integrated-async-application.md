# Spec 075：工具、应用、持久化与正式流式消费的统一异步切片

状态：2026-10-08，用户明确要求把原计划1–4合并一次交付。以现有Python/create_agent为基础；活动取消与Web后置。本片跨多个模块，先Spec/TDD再逐依赖实施，不把中间阶段当最终完成。

## 现状与数据流

模型/上下文已有双路径（Spec073/074）。工具记录wrapper与批次Condition只同步；同步工具本身可由StructuredTool._arun在线程执行。SessionService拥有准入/控制/审批/清理，事件普通同步订阅；持久应用用SqliteSaver+sqlite3元数据。同步消息观察消费values/updates，无模型文本delta。

统一链：AsyncApplication.arun/astream_run → 同一SessionService状态与生命周期 → 异步runtime消费astream(values/updates/messages) → 已有模型/上下文 → 异步审批hook/批次/工具wrapper → 原工具IO在线程或原生coroutine → 完整消息/控制/上下文/turn观察 → 关键清理 → 异步checkpoint确认+元数据提交 → 唯一终局。临时文本以独立delta事件交付，不能驱动权限或完成确认。

参照：Pi a69bef789 agent-loop的顺序准备/Promise.all执行/按原顺序回填，signal另有停止责任；AgentX3fcfa0e partial/complete/error交MessageTransport且保存/终局分职责。核对本机LangChain1.4.2的ToolNode._afunc/gather、StructuredTool._arun→线程fallback、HITL.aafter_model→after_model，以及LangGraph1.2.12/AsyncSqliteSaver3.1.1；官方middleware/streaming/persistence文档已核对。不换运行时、不升级依赖。

## 1. 工具与IO

- 原同步工厂、工具schema、审批规则、错误ToolMessage和串行metadata保持。record wrapper增加awrap；HITL异步hook将包含文件分类的原after_model放入受控线程，使用原interrupt/resume与context传播，不重复审批观察。
- 异步批次按原pending IDs形成计划：任一sequential则整批原顺序；纯parallel用Semaphore，尊重RunnableConfig.max_concurrency，未给异步默认4。asyncio.Condition在等待中释放锁，不使用Condition.wait阻塞loop；fatal唤醒/跳过剩余，软错误保持可续跑。未知工具仍由框架回填错误。
- read/ls/find/grep/write/edit/Bash保留已验证同步主体，框架异步入口线程卸载，工具链并发上限限制线程负载。原生async自定义工具直接await。Bash仍是线程内宿主进程/原超时/输出归档，不能称原生async subprocess或活动取消。
- 已开始的工具调用采用shield并等待实际settle后清理；父Task.cancel不能让还在运行的写入被当作已停止，也不能提前释放会话。此处安全排空不提供用户活动取消。

## 2. 应用与控制

- 新AsyncApplication、异步build_async_local_application；原LocalApplication/build_local_application/CLI API保持。新增acreate_session/acreate_workspace_session/aselect_model/arun/aresume_tool_approval/astream_run/astream_tool_approval/aclose；既有steer/follow_up/request_stop_after_turn是快速内存控制，复用同一服务。
- aget_messages只读取本会话当前checkpoint并返回深拷贝消息，服务流断开/初始化可用；不是完整历史浏览。异步factory在线程获取资源时使用acquire_blocking：取消后先排空，再关闭尚未交给调用方的资源，防止实例锁或SDK客户端泄漏。
- 同步与异步生命周期共用SessionService有限流程；将阻塞操作作为内部IOCall边界，由同步/异步驱动执行（仅应用生命周期，不是Agent运行时）。run_id每次新生，stream入口预生成run_id以精确过滤，session/thread稳定；同会话busy/待审批/人工核对检查不变。
- 资源/模型配置/审批目标review在线程，短session锁不跨IO等待；选模持久写使用独立串行锁，resolve可读上次已确认模型。
- 失败仍抛原异常并发布安全终局，审批中断返回原结果+审批项；保存失败影响调用/终局。外部Task取消安全排空后用failed/cancelled诊断和人工核对阻断，不新增用户stop模式；after_turn照常。producer与consumer分别拥有生命周期。
- async普通失败后读取实际checkpoint检查最后AI工具调用是否未配对；未配对或无法读取则要求人工核对，避免同进程再发请求带着残缺工具协议。原异常保持，同步入口规则不改变。实际并行fatal测试证明框架退出与工具shield会等待已开始的兄弟任务，因此不另造工具任务注册器。

## 3. 持久化

- 新open_async_persistent_application(config_path, *, database_path, tools, system_prompt, response_format)异步上下文管理器，复用SessionStorage文件权限/实例锁/schema1和SessionPersistence规则，官方AsyncSqliteSaver独立async连接。元数据sqlite3经受控线程执行，RLock/check_same_thread=False已核对，关键操作shield排空。
- 同文件独立事务不宣称原子；仍running→清理→确认安全checkpoint→ready。failed/interrupted/running/review_required跨重启阻断。async finish/restore使用aget_state与同步规则共用，模型选择提交失败保持原内存选择；没有迁移schema/修改用户DB配置。
- factory owns storage/saver/模型SDK客户端，关闭先等待所属后台运行再关资源；异常退出仍清理/日志，保持主异常。保持同库本地单实例/持久单活动run。
- 本机OpenAI/Anthropic默认HTTP池会跨模型共享，独立构造探针和两应用测试确认；不能关闭借用的全局池。异步factory在本次新目录模型上用SDK with_options绑定独立httpx客户端（保留SDK请求配置/超时/显式代理），catalog记录拥有的HTTP资源，只关闭这些；Ollama原客户端为模型独占。同步loader/配置格式不变，外部自定义模型资源仍由其提供方管理。Anthropic的cached SDK属性适配按当前固定版本验证。
- 当前Anthropic SDK使用已安装httpx2，不能给它传httpx对象；独立资源创建按供应商选择已有传输包，不新增/升级依赖。

## 4. 流式与展示协议

- runtime直接消费astream(values/updates/messages, version=v2)，完整状态沿用MessageStateObserver/Turn/Control/Context观察与失败recovery_view；不重写reducers。baseline与恢复读取aget_state，审批interrupt收集去重，结果兼容原dict。
- 新MessageDeltaData/message_delta：message_id、文本delta、从1递增的index，永远provisional。仅主model节点AI文本；摘要调用打专用tag过滤，工具参数和reasoning不当文本显示。完整snapshot按ID替换草稿，不能再追加完整文本；failed时未确认草稿标为unconfirmed。
- 新StreamingMessageProjection是应用层展示数据，保持live历史与模型压缩删除分开，不提供新的全历史DB仓库；初始化/恢复只使用实际checkpoint，不伪造旧消息。
- 新有界AsyncEventStream：同步publisher回调仅入有界线程安全队列与唤醒loop，async消费不阻塞执行；溢出显式抛EventStreamOverflow，不静默丢消息。断开/关闭流不取消生产任务，AsyncApplication持有并记录其结果，aclose等待排空。任意旧同步订阅者仍要求快速。
- astream_run/approval在执行前订阅并按预生成session/run过滤，执行结束后排空并抛原运行错误；consumer异常/断开只关闭自己的订阅，应用保留后台任务。不增加HTTP/SSE/页面，不改变CLI输入光标或默认同步运行。

## 文件与接口范围

扩展call_observation/tool_batch/message_observation/events、context_summary_io（摘要tag）及resource_invocation；按≤500行需要新async_tool_batch/async_invocation/async_message_observation/stream_events。应用新增io_steps/async_session/async_application/async_persistent/async_event_stream/stream_projection，复用并必要拆分SessionService/SessionPersistence。模型目录新增SDK资源关闭方法/模块。旧公开签名保持；新字段同步注册/日志/投影/测试，delta不进checkpoint/会话DB。

预期超过8个文件，属于用户明确授权的大切片；按职责拆分，避免单文件>500。测试覆盖实际框架和临时文件/DB，不改conftest/依赖/.env/关键知识点。文档同步Spec/交接/计划/待办/README。

## TDD与验收

先红：原生async模型/工具通过正式arun；混合批次顺序与纯并行重叠/有限并发，fatal/软错误/截断；七个内置工具在临时工作区真实IO、审批批准/拒绝/连续中断；模型等待期间Steering/Follow-up/after_turn、busy/跨会话、失败清理。

流式：模型delta在完成前可消费，摘要不泄漏，最终snapshot校准无重复，失败草稿unconfirmed；有界队列溢出显式、断开生产继续且完成保存、后台异常安全记录与订阅退出、admission失败不挂起。

持久：临时DB跨独立进程恢复session/thread稳定、新run、消息/摘要保留、checkpoint推进；同库锁/文件权限、受限恢复、保存/模型选择失败、主次异常、资源释放和在提交前无completed。工具线程取消先排空再放busy，无法确定的状态阻断，不宣称活动中止。

运行定向/全量pytest、compileall、pip check及显式结构检查；同进程fake外加独立进程offline。真实模型用有界独立probe验证delta→纯随机工具→Steering→完整消息→持久确认→重启继续，最多首进程2次+恢复1次逻辑调用，无用户文件/真实业务副作用，输出仅统计。真实SDK部分输出失败/取消、生产压力另验。

## 交付边界与后续

一次交付覆盖1–4，不以中间单元通过提前收尾；最终提供接线、验证与限制。剩余活动取消和Web各独立Spec，成熟权限/高级恢复/测试业务和整体学习顺序沿用户约定。没有发布/部署/提交/用户DB迁移。

## 实施与实际验证（2026-10-08）

1–4已接线：AsyncApplication正式arun/审批恢复/事件流共用SessionService准入、控制及有限IO流程；异步工具批次与wrapper、七工具真实临时IO、HITL线程context传播、官方AsyncSqliteSaver及元数据线程IO均通过。原LocalApplication/CLI签名与默认路径保持。模型目录异步factory独立HTTP资源，OpenAI与当前Anthropic/httpx2分别适配；默认共享池不由catalog关闭，自定义未知模型的资源仍由提供方负责。

正式流消费messages/updates/values，摘要专用tag过滤，完整snapshot/turn/control/context沿原观察器；message_delta是第27种事件，包含临时文本/消息ID/索引，不进DB/checkpoint，默认日志不记正文。旧上下文注册测试从26更新为精确27并断言delta映射，四种context集合原断言保留，属于已授权新增事件的契约同步，不放宽失败判据。展示projection保留live历史、最终snapshot替换草稿，失败recovery_view不能确认本次partial，SDK复用ID也已有回归。

TDD：应用6例、工具调度5例、持久化4例先红；资源关闭覆盖主异常/取消获取泄漏实例锁两例先红修复，另目录装配关闭错误覆盖原异常先红修复；不同行为补测中已有框架shield保证兄弟工具settle，但残缺协议未阻断为红灯，补实际checkpoint评估；独立SDK探针确认默认池共享，两应用隔离2例红灯→独立拥有传输，Anthropic首次适配HTTPX类型错误按当前SDK改用已安装httpx2；独立进程probe模块不存在先红转绿；缓存ID失败草稿误确认先红转绿。其余追加验证通过，不称全部36例都先红。未改conftest，未放宽旧断言；一次误写不存在的旧调度测试路径未执行测试，rg定位后纠正。

- 关键阶段定向：应用/旧调度/会话26通过；持久化/旧持久/应用/审批34通过；资源/持久/应用17通过；最终流式交付/上下文流9通过。各阶段输出保存在交接摘要，不代替最终全量。
- `.venv/bin/python -m pytest -q` → `882 passed in 23.30s`（此前846，新36；一条既有全局注册数量测试按新契约精确同步）。
- `.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` → exit0；`.venv/bin/python -m pip check` → `No broken requirements found.`；git diff --check exit0；全部Python≤500，未跟踪文件另显式检查。无独立应用打包目标。
- `.venv/bin/python -m pytest -q tests/test_async_process_probe.py --tb=short` → `1 passed in 1.62s`，执行两个独立离线Python进程、临时真实SQLite，非同进程伪重启。
- `.venv/bin/python -m scripts.probe_async_application --timeout-seconds 180` → `async_application_probe=PASS mode=real two_processes=True delta=verified tool=1 steering=verified terminal_after_save=verified history_used=True session_id=stable thread_id=stable run_id=new checkpoint=advanced`。现有默认模型、首次2次与恢复1次逻辑调用，纯随机工具/合成事实，临时DB和私有handoff，不读用户文件/业务系统；callback模型预算在请求前阻断超额，180秒父总截止。统计不含正文/凭证，不统计SDK HTTP尝试/真实货币费用。

验证覆盖：并行重叠/限额/回填顺序，串行完整链/软错误/fatal/截断；八次调用涵盖七工具的临时文件/rg/Bash，并核对命令审批前无写副作用；模型等待Steering/Follow-up/after_turn、同会话busy/跨会话/切模；审批批准/拒绝；delta在完成前可见、最终校准、失败partial、缓存ID、摘要隐藏、context applied/recovered；溢出/断开不取消生产、线程入队/退订；checkpoint/元数据确认前无completed、失败指针保持/重启阻断、资源与实例锁释放、主异常优先、SDK隔离；真实跨进程完整正常链路。

限制：同步工具使用框架线程fallback，Bash仍线程内进程，不是原生async subprocess；开始的IO被排空后才释放busy，不能称活动取消。现有CLI仍同步、不做逐token终端光标刷新。CPU估算/序列化/旧同步订阅者仍须快速，背压队列以事件数有界、未做SaaS压力/多模态大payload评测。审批与failed/running/review_required跨重启仍阻断；当前读取API为活跃checkpoint，不是全历史浏览。SDK HTTP socket自定义选项、特殊自定义HTTP传输与更多真实供应商的目录资源适配须独立验收；本次JSON配置未增加这些字段。真实部分输出失败、SDK重试/内部取消、子进程活动停止及副作用unknown仍待下一片。

Spec/README/计划/待办/交接已同步；未改.env/依赖/关键知识点，未提交部署。核心新增代码未逐行学习，独立实现/面试追问无新证据。唯一下一步：设计活动停止的范围与完成判据，分别确认模型/重试、工具排空与Bash子进程终止、checkpoint部分状态和人工核对；不移除shield后就宣称已停止。Web随后独立Spec。
