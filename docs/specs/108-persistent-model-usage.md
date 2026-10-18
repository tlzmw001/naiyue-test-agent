# Spec108：模型调用用量事实与持久记录

日期：2026-10-18。状态：**108-01/02/03已实施并完成离线验收**。本片继承Spec107/M6-02的四项用量口径。保留原脏树，不改真实模型配置、用户库、依赖或既有公开应用API；108-03只新增只读查询，0次真实供应商请求，不提交/push。

## 1. 分片及当前链

| 分片 | 交付边界 |
|---|---|
| 108-01 | 框架单次模型调用身份、有限开始/结束事实、主子/摘要/Auto归属及当前事件/日志接线 |
| 108-02（已完成） | 工作区SQLite独立账目、开始/结束保存、幂等及重启未确认状态 |
| 108-03（已完成） | 有界查询、分类汇总、未知完整性及展示，累计消耗与上下文占用分别表达 |

当前：SessionService装配根/子图和Auto；根/子普通调用经选模middleware，摘要及Auto有独立ainvoke入口。model_call_started/finished表达逻辑模型调用；上下文溢出时一个逻辑调用可包含首次请求、摘要和重发三次框架调用。M6-02的日志/上下文快照不是完整累计账目。

已有零网络证据见[Spec107第9节](107-model-request-prompt-cache.md#9-m6-02当前实现与下一块准备)：四类调用8次start/end且作用域匹配；溢出恢复3个框架ID而逻辑开始只有1次。本片不把框架ID等同SDK内部每次HTTP尝试或完整计费。

参照：Pi本地a69bef789由供应商适配器产usage、AgentSession.getSessionStats遍历全会话及摘要条目，不仅统计当前压缩上下文；AgentX本地3fcfa0e在onCompleteResponse→buildModelCallInfo记录输入/输出。两者提供返回后保留事实的参照，不据此声称都有独立失败尝试账目。本机LangChain的on_chat_model_start/on_llm_end/on_llm_error及Model.callbacks已有统一入口，无需新模型包装层或替换循环。

## 2. 108-01事实合同

新增事件model_invocation_started/finished，与逻辑model_call事件分别保留。RuntimeEvent原有外层session_id/run_id/source/sequence/timestamp保持：外层run_id是根运行，source.agent_run_id是子运行；框架回调run_id转为data.invocation_id。并行事件按该ID配对，不要求相邻。

ModelInvocationStartedData：invocation_id（有限非空ID）、model_id（配置身份或None）、purpose（agent/summary/auto_fast/auto_detailed，无法确定时None）。模型身份由_build_model写入naiyue_model_id元数据，不用供应商model_name猜别名；缺失身份为未知并记录固定诊断。purpose由当前受控tags判断，普通调用为agent；摘要沿用SUMMARY_TAG；Auto入口标注快审/详审，即使使用同一模型也能区分。歧义/非法用途信息不得猜成普通调用或使Agent退出。

ModelInvocationFinishedData：invocation_id、status（returned/failed/cancelled）、usage_status（final/partial/unavailable）、input_tokens/output_tokens/cache_read_tokens/cache_write_tokens（可空非负整数）、error_type（有限类型或None）。返回结果中的有效usage为final，缺失为unavailable；异常附带的部分统计为partial，否则unavailable。final不代表所有四项齐全或完整账单；不从文本/请求/耗时推断消耗。计数复用normalize_model_usage，不重复加缓存。数据类冻结并校验ID/枚举/计数/错误类型。

流式失败格式经真实astream零网络测试核验：本机框架把合并后的ChatGenerationChunk与异常元数据分别放在两组generations；只在失败/取消且首组为单个合并chunk时按该明确格式取首组usage，不读取异常元数据组的正文/headers。其他多组结果保持未知。初版将两组一律视为歧义的测试先红灯；单点修正后真实流式失败、带异常元数据及取消均通过。

开始已有模型与用途，结束只带调用ID及结果，不重复选择。回调不保存messages/serialized/原始响应/完整metadata，不新增ContextVar、模型调用包装或服务。当前RunServices.publisher自动提供根/子归属；回调只同步整理并发布事实，不主动create_task，不执行数据库IO。

实现探针修正：当前LangChain的agenerate在await gather期间被取消，会直接离开，未到后续on_llm_error分支。最小零网络探针只有started回调、task.cancelled=True；本片定向停止红灯亦缺少结束事件。为保留已批准的取消配对合同，回调增加仅覆盖在途调用的有限记录（调用ID、开始时publisher、所属task和done回调），不保存输入输出。正常结束/异常hook先移除记录与done回调；所属task取消/失败而框架未回调时补对应终态，双方通过原子移除保证最多一次。已结调用立即清理，跨线程同步fallback用锁保护；不会用任务正常完成伪造模型返回。此处替换原方案“完全无在途状态”的假设，控制循环、SDK及依赖保持。

## 3. 新增、修改与实际消费者

| 文件 | 类/方法及改动 |
|---|---|
| model_access/__init__.py | MODEL_ID_METADATA_KEY和_build_model的配置身份元数据，无新用户配置 |
| agent_runtime/model_usage_observation.py（新增） | ModelUsageCallback的三个框架hook；install_model_usage_callback(catalog)，保留已有callbacks，重复安装幂等、同模型对象去重 |
| agent_runtime/events.py | 两个冻结数据类、EventData联合及_PAYLOADS注册，保留原逻辑事件 |
| agent_runtime/tool_execution/permissions.py | _review_steps把stage连同model/messages交给areview，config加明确阶段tag，不改审查提示词/供应商参数/结论 |
| application/session_service.py | __init__在创建审查器之前统一安装，内存/持久应用和后续子图复用 |
| application/run_events.py | _log_event名单增加invocation_id/usage_status；现有日志和订阅是本片实际消费者 |
| tests及必要现有探针 | 回调/SDK/作用域/失败/停止/顺序接线，迁移精确事件序号和消费者，不删除原断言含义 |

生产计划六份文件，其中一个新模块；加Spec/测试超过八份。RunServices结构、主子控制链、消息/工具展示及DB schema保持。Web通用序列化传递新事实，前端原有未知事件序号消费须验收。现有测试checker补invocation配对，不能把新增事件过滤掉来掩盖序号错误。脚本/旧观察消费者按真实语义同步：累计以invocation为唯一入口，逻辑model_call/上下文快照仍有其消费者。

## 4. 调用链与失败

正常：load_model_catalog→_build_model准备身份→SessionService安装回调→原装配。运行时RunObservation.bind→原模型调用→on_chat_model_start发布开始→SDK原执行/流式→on_llm_end从LLMResult的AIMessage取usage→normalize_model_usage→结束事件→现有日志/订阅→原响应门禁/工具循环。绑定工具、摘要输出上限和推理档位副本继承callbacks；注册不增加任何模型请求。

例：根run-R启动root/tester/child-C，子模型一次框架ID为call-X；开始/结束外层仍是同一session/run-R，source携child-C/父工具ID，data以call-X配对。input8200/read7000/write1000/output30保持总输入8200。另一会话同模型调用call-Y不会复用call-X或其来源。

失败：网络异常无usage→failed/unavailable且原异常继续；流式部分统计→failed/partial；取消→cancelled且原停止效果保持。模型返回后响应门禁拒绝，invocation仍是returned、逻辑调用可failed，已发生消耗不丢。观察普通异常只记固定诊断，不改成功结果或覆盖原异常，不记录异常正文/原始数据。无RunServices的独立模型使用不伪造会话事件。未配对终态代表观察不完整，不能制造返回或补零；跨重启处理在108-02单独讨论。

## 5. 验收与回退

先确认新测试因缺少注册/事件合同而红灯，再最小实现。覆盖：真实适配器＋MockTransport四类调用及fast/detail同模型；三次溢出调用；多会话并发、子源；stream/ainvoke、工具/输出/thinking绑定；返回无usage/部分失败/网络异常/取消；观察故障及无敏感内容；旧callbacks/重复安装/共享模型；Web未知事件和当前顺序、预算、恢复、关闭。同步checker/精确序号保留既有事件顺序和配对，不改conftest。

专项通过后完整后端/前端/构建、语法/依赖/500行/分层/链接/diff验证，记录真实命令与结果。没有真实供应商调用/配置/数据迁移，回退不涉及用户数据。本片完成后只讲108-02，不擅自接数据库、队列、统计UI或项目重试。


## 6. 108-01当前主链与验收

六份生产文件已接线，其中新模块model_usage_observation.py共131行。_build_model准备配置身份；SessionService.__init__在Auto构造前安装一次callback，普通/持久应用共用；模型绑定工具、thinking副本与摘要输出限制继承原metadata/callbacks。on_chat_model_start发布有限开始事实，on_llm_end/on_llm_error按框架ID结束；仅在途记录补当前gather取消缺hook的终态并原子清理。events注册两种新事实，run_events日志名单消费，Web通用序列化继续交付，现有显示不会把累计调用事实替换为当前上下文用量。

现有测试消费者同步了新增事件导致的精确序号与总数；event_sequence测试辅助器新增invocation配对，原turn/工具/模型顺序和权限断言保留。四例middleware正常场景的直接假catalog绕过_build_model，因此在对应测试中补实际装配会生成的身份metadata，不移除原“无观察异常/不可用”检查。未改conftest或通用假模型装配。

初始25项因新模块/事件/接线缺失而正确红灯；首次停止测试发现当前框架gather取消遗漏异常hook，先补合同再加任务收尾保护。架构复核发现astream部分结果的两组格式，真实测试再次红灯，再修_usage。专项81 passed in1.33s（/tmp/naiyue-10801-focused-final.log）；旧消费者迁移47 passed in1.61s（/tmp/naiyue-10801-consumers.log）。这不是把所有新增测试都分别做过红灯的声明。

最终验证：

| 命令（本项目根目录，npm在frontend） | 实际结果 |
|---|---|
| PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q | 2717 passed in111.65s；/tmp/naiyue-10801-backend-final.log |
| npm test -- --run | 27文件190 passed；/tmp/naiyue-10801-frontend-final.log |
| npm run build | TypeScript成功、Vite210模块成功；/tmp/naiyue-10801-build-final.log |
| .venv/bin/python -m pytest -q tests/test_architecture_semantics.py | 16 passed in0.61s；/tmp/naiyue-10801-architecture.log |
| compileall / pip check / AST / 行数 / git diff --check | 无错误；417份Python AST/500行及40份前端生产源文件500行通过 |

第一轮全量7个失败均为旧消费者未迁移：事件类型总数、两条探针输出数量及四条正常middleware假模型身份缺失；上述断言含义保留并已迁移，最终全量通过。架构/安全已对切片基线差异复核：原部分用量问题修复后关闭，分别36项/42项独立复验通过，无确认遗留。0次真实供应商请求，未改真实配置/用户库/依赖或模型循环；无提交/push，原脏树保持。

## 7. 108-02实施合同（简化版，用户2026-10-18批准）

目标：保存已知的主/子/摘要/Auto用量，正常收尾可确认、重复交付不重复累计；接受异常退出造成的统计缺口。Pi a69bef789主链message_end→appendMessage→_persist同步JSONL、全条目汇总；AgentX 3fcfa0e完整响应→TracingMessageHandler→TraceEventListener.handleModelCalled→recordAiResponse，追踪写入捕获异常。该监听方法仅@EventListener，没有@Async，不能从注释推断后台。两条所查链未提供供应商补账保证。我们的小型写入者服务当前同步事件发布/异步SQLite边界，是项目选择；不增加采集周期表、自动重试/补账或恢复写入平台。

### 7.1 数据、范围与文件

同一工作区SQLite schema8→9：naiyue_model_invocations按invocation_id唯一；保存session/root run及根/子来源、配置model/purpose、开始/结束时间、status、usage_status、四项可空计数及有限error_type。正常开始状态started，结束returned/failed/cancelled，已知开始缺结束为unconfirmed，计数不补零。仅结束也保存已有事实，标记开始缺失；晚到开始可补身份但不得覆盖既有终态。同ID同事实幂等；来源或既有事实冲突拒绝覆盖并记缺口。超SQLite有符号64位范围的计数降为未知并诊断，不能导致主任务失败。

另存naiyue_model_usage_runs：每个根run一份已知缺口状态（session_id/run_id、capture_incomplete、reason、dropped_events、recording_closed_at）。只跟踪当前已有根运行，不构建新的采集周期。未发现缺口不等于完整账单。根/子及不同用途共享同根run状态，调用各自按ID独立。旧会话不反推旧账；UI/查询汇总归108-03。

| 文件 | 新增/修改职责 |
|---|---|
| application/model_usage_storage.py（新增） | schema9迁移、启动未确认核对、参数化调用写入/幂等、根run缺口收尾 |
| application/model_usage_persistence.py（新增） | 一个有界线程安全缓冲和一个异步写入task；accept、aflush_run、aclose，开始的数据库IO必须settle |
| application/session_storage.py | 沿现有连接/锁/事务调上面SQL函数；支持9；删除会话同事务清理调用及缺口；不新增公开应用接口 |
| application/chat_queries.py | 旧8迁移识别9，避免重复ALTER |
| application/async_persistent.py | 应用工厂接订阅并拥有写入者，启动失败/运行关闭/资源释放均收尾；本地临时应用不落库 |
| application/session_persistence.py | 注入可选用量保存者；afinish先独立确认本root待写，再按原必要保存分支处理checkpoint/聊天，不让用量错误加入blocked |
| tools/settings.py及docs/examples/settings.example.toml | 总配置usage_persistence.max_pending_events=1024、max_pending_bytes=4194304；非法预算按现有普通预算规则诊断回退，私有配置不修改 |
| tests | storage/缓冲/四类调用/同模型用途/溢出/停止/并发/关闭/保存故障/重启与迁移、删除；迁移旧schema精确断言，保留已有行为 |

涉及8份生产/样例文件，加Spec/测试超过8份，按深度复核；无新依赖、运行层或RunServices变更、真实配置/用户库修改、实网调用、提交或push。

### 7.2 缓冲、失败与生命周期

容量同时限制条数与有限事实的JSON UTF-8字节，含已交给IO但未确认的一条；不是整个进程RSS预算。满载不清空旧记录、不覆盖，新事实拒绝入队；按root合并诊断和丢失计数，缺口状态在缓冲之外直接置位。空位恢复后继续接受；不从checkpoint重新制造用量。事件不保留提示词/原始响应/headers/工具内容。

每条接受的事实只写一次。一个root发生数据库写入错误，该root已排队未写及后续事实进入降级、标缺口，不无限重试；其他root和后续新root仍可正常尝试。根收尾把缺口/未配对状态写一次，失败日志可见，不能覆盖主结果/异常或必要保存错误。数据库完全不可写时不承诺缺口标记能持久；本机日志和已提交记录为实际证据。

根run在子运行settle之后、释放busy之前aflush_run，只等待本root已接收记录得到明确结果，不等待其他root运行结束或全队列清空。仍可能经过此前排队的短事务。调用收尾保持原await_settled合同。应用关闭先按原链停止并等待运行，再退订/排空写入者/保存剩余缺口，最后释放库；重复关闭幂等。外部取消不能提前释放已开始SQL线程的连接；可降级写入task故障须唤醒待确认者，不能造成永久等待。

启动核对：已有started记录改unconfirmed；未闭合用量run标缺口；旧会话元数据state=running且有run_id时也登记可能缺口。均不重发模型/工具，不改变原会话恢复决定。记录中没有开始或整条未入库的消耗不推算；压缩不删已发生消耗，删除会话清理关联记录。

### 7.3 正常与失败调用链、验收

正常：模型callback→现有publisher→accept有限事实→缓冲→写入task→run_blocking→SessionStorage锁/短事务→SQLite。root finally先settle子任务→SessionPersistence.afinish确认本root待写→原checkpoint/聊天必要保存→释放busy→原终局。关闭回收写入task和订阅。主、子、摘要、快详审各一行；调用返回与逻辑门禁结果分开。

失败例：A的SQL写入失败→A保存者reason=storage_error、队列中A标丢失→aflush_run明确返回不完整→尽力保存A缺口→原任务成功或原异常保持；B照常运行和保存。崩溃已提交开始没提交结束→下一进程标unconfirmed并保留已知字段，不自动继续模型。

先写失败测试确认新表/保存者/接线缺失，再实现。专项覆盖幂等/冲突/partial/缺失及0、旧8→9迁移重开、非法配置、线程接收、条数/字节含inflight、root保存故障与原异常身份、异步任务取消收尾、正常关闭/启动失败/直接aclose、真实适配器MockTransport四类/同模型/三次溢出、不同会话互不等待、压缩重开/删除隔离、独立子进程真实崩溃。无真实供应商调用；专项后全量后端/前端/构建、AST/500行/分层/依赖/链接/diff及架构安全专项复核。108-03随后只讲方案，不直接实现。


## 8. 108-02实际实现与验收

新增两个应用层模块：model_usage_persistence.py（176行）与model_usage_storage.py（112行）。工厂只装一份全局用量订阅/写入task；Runtime原callback/publisher、共享模型和RunServices保持。SessionStorage沿现有连接/锁/事务保存，新增schema9两表及启动核对；SessionPersistence.afinish在原必要保存之前确认当前root，原blocked与异常优先级保持。直接aclose、工厂正常退出和装配失败均关闭写入者，先结算已开始IO再释放连接；原异步执行入口没有保留平行同步包装。

实际正常例：原适配器＋MockTransport经主调用→快审/详审（共用同一模型）→工具/子调用→主续轮→下一轮→手动摘要，8次发送保存为8个唯一ID，模型/purpose/根子来源匹配。压缩后这些已发生消耗仍在；重复交付不重复建行，重开可读，删除会话同时清理关联用量。溢出恢复另保存失败请求、摘要和重发3行，逻辑model_call仍只有原合同。

实际失败例：一会话SQL写入失败，该root降级并记storage_error，原成功结果或同一TimeoutError对象保持，另一root仍可保存；条数/字节溢出均保留旧记录并显式记缺口。取消写入task时等待已开始的线程IO，等待者收到不完整结果。独立子进程分别在开始已提交、结束已提交但收尾未提交时硬退出；下一进程保留已知100输入/5输出，将旧在途状态标unconfirmed并登记interrupted_execution，不重发模型工具，也不改变原checkpoint恢复决策。capture_incomplete描述已知采集缺口，供应商没报告某项计数仍为NULL，不能用该标志冒充完整账单。

测试顺序与必要调整：初始12项因缺表/接口/写入者而红灯后实现。新增取消测试复现pending先归零、任务done回调稍后才标失败的顺序问题；先在该条记录finally写入失败状态再唤醒，回归转绿。新增真实崩溃探针曾因假模型只在首次调用阻塞而超时，修正新探针的第二次调用门闩，生产逻辑不因该问题改变；两个硬退出场景均通过。共新增25项测试，没有把后补的全部案例称为先红后绿。

旧schema4/5/6/7夹具同步移除新用量表再模拟旧库，原数据/顺序/原子性断言保留。首轮全量2 failed/2740 passed，均为持久应用多了正常的应用期订阅，旧测试仍要求一轮结束后所有订阅为空；两处改为明确验证仅保留唯一用量订阅、应用关闭后全部清空，原持久化/断连/异常可见性断言保留。未改conftest或通用测试helper。

| 命令（根目录执行，npm在frontend） | 实际结果 |
|---|---|
| .venv/bin/python -m pytest -q tests/test_model_usage_storage.py tests/test_model_usage_persistence.py tests/test_model_usage_persistence_paths.py tests/test_model_usage_restart.py tests/test_async_stream_delivery.py tests/test_web_concurrency_failures.py | 37 passed in3.58s；/tmp/naiyue-10802-final-focused.log |
| PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q | 2742 passed in111.96s；/tmp/naiyue-10802-backend-final.log |
| npm test -- --run | 27文件190 passed；/tmp/naiyue-10802-frontend.log |
| npm run build | TypeScript成功，Vite210模块成功；/tmp/naiyue-10802-build.log |
| .venv/bin/python -m pytest -q tests/test_architecture_semantics.py | 16 passed in0.71s；/tmp/naiyue-10802-architecture.log |
| compileall / pip check / AST / 行数 / 样例解析 / git diff --check | 无错误；423份Python及40份前端生产源文件均≤500行，样例预算解析为1024条/4 MiB |

架构与安全按切片基线分别只读复核，无确认遗留；独立复跑25项/23项通过。639个本地链接目标及72个标题锚点通过。已接线的实际调用测试是本片L3证据，模块和DDL不是占位实现。只用临时SQLite和离线SDK传输，0次真实供应商请求，未打开/迁移用户库、未改真实配置或依赖、未提交push，既有脏树保留。

下一片108-03仍须先讨论：当前会话只读有界查询与按模型/用途/根子来源汇总，累计消耗与上下文占用分开；返回已知小计及未知/未确认/采集缺口，不把未知变0。查询入口、活动时刷新及UI位置尚未实施；费用/供应商补账/持久诊断/checkpoint修剪仍各自独立。


## 9. 108-03实施合同（2026-10-18用户批准）

### 9.1 范围、数据及查询

在已有上下文入口新增“会话累计”第三页签，当前预估/最近请求语义和百分比保持。累计只读schema9的调用/缺口表，包含主子、摘要及Auto，压缩后仍累计；旧会话没有记录则空态，不能反推旧账。无schema/模型采集/写入者/RunServices/恢复链改动，无费用或账单保证；不引入依赖、模型请求、真实配置或用户库操作，不提交push。

GET /api/sessions/{session_id}/usage?after_model=<配置模型ID>，沿现有认证和workspace范围检查。模型页大小由WebSettings.usage_page_models控制，默认20，1—100。after_model为1—128字符的可选ID，按model_id二进制字典序读取下一页，参数化SQL；NULL模型单列未归属组，不混入游标。每次响应总计/分组覆盖全部已保存记录，只有models.items分页，next_after表示剩余页。活动中跨页可以看到更新后的提交状态；刷新从第一页重新读，不承诺跨多次HTTP的冻结快照。

返回session_id/as_of/recorded_calls、status_counts（started/returned/failed/cancelled/unconfirmed）、usage_status_counts（final/partial/unavailable）；四项metrics各含known_sum（十进制字符串或NULL）、reported_calls、unknown_calls。缺少全部报告时known_sum=NULL，明确0仍是字符串0；部分报告计入已知小计，保留partial数量。输入已含缓存，不重复相加。还返回固定activities六组（root_agent/child_agent/summary/auto_fast/auto_detailed/unattributed）、scopes两组（root/child），以及models.items和models.unattributed。不同分组是同一批记录的不同投影，不能相加。capture含recorded_runs/incomplete_runs及十进制字符串dropped_events；未知计数与采集缺口分开，不宣称未发现缺口就是完整。

同一连接现有锁内开启只读事务，先确认session存在、选一页模型ID，再游标逐行读取本session有限字段，用Python整数累计总计及固定分组/当前模型页。避免SQLite SUM有符号64位溢出、JS Number精度损失以及fetchall整个历史；查询内存随页大小/固定分组有界，返回沿max_frame_bytes限制。全会话合计仍为本session O(n)扫描；本片无汇总缓存/新增读连接，若真实延迟证明不足再单独优化。

### 9.2 文件、类和接线

| 文件 | 改动及实际消费者 |
|---|---|
| application/model_usage_storage.py | 扩展read_summary及有限桶累加/格式化，查询复用已存在表，模块仍低于500行 |
| application/session_storage.py | read_model_usage(session_id, *, model_limit, after_model=None)，现有锁/连接/只读事务及固定失败日志 |
| application/async_persistent.py | aget_model_usage同名参数，受控操作+run_blocking，不依赖会话已加载或图状态 |
| web/settings.py、web/app.py、web/service.py | 可配置页大小、只读路由、usage方法，workspace门禁和响应预算；失败usage_view_unavailable且不操作运行 |
| frontend/src/api.ts、usageTypes.ts（新增） | usage API含AbortSignal和after_model；新增四项可空十进制字符串及汇总类型 |
| frontend/src/components/SessionUsage.tsx（新增） | 累计页本地请求状态、数字和固定分组、模型下一页/重读，未知/partial/缺口/空态/错误文案 |
| frontend/src/components/ContextPanel.tsx、App.tsx及现有CSS | 第三页签和方向键循环、usageContent节点延迟挂载，scope文案，复用字体/配色/数字对齐；现有两页错误不影响累计页 |
| tests、前端测试、Spec及当前索引 | 新增存储/应用/HTTP/界面行为回归，保留当前断言，更新Spec096版本边界及交接/剩余工作 |

涉及超过8文件，按深度复核。无需新增通用resolver、状态服务或模型middleware。SessionUsage只在选中累计页时挂载并读取，接api/sessionId/run（已有生产者App）；ContextPanel只接内容节点，保持既有上下文查询消费者。

### 9.3 正常、失败与刷新

正常例：主输入1000/输出100、子2000/200、摘要500/50、快审失败无报告，返回4调用、3returned/1failed、已知输入3500/输出350，各1未知；缓存各自保持可空及0。入口→SessionUsage→WebApi.usage→WebService.usage（会话范围）→AsyncPersistentApplication.aget_model_usage→run_blocking→SessionStorage.read_model_usage→read_summary→只读快照→数字/分类。读取不加载/更新图，不排空writer，不运行模型工具。

打开累计页、根运行完成、手动刷新读取；SSE token/子事件不逐条查询。进行中允许已提交记录延后，结束后沿108-02已确认边界刷新。切换session/模型页时AbortController及本次请求代次隔离，旧响应不能覆盖新会话；根run其他会话的完成不刷新本会话。分页页码在手动刷新/新run完成时重置；旧模型页正在读取时也须取消或隔离。

读取失败例：SQLite异常→固定类型诊断→503 usage_view_unavailable，累计页显示重试而不伪造0；运行、其他页签和已有必要保存保持。无数据显示空态；仅有缺口行也明确提示。未知身份单独汇总，异常正文/提示词/工具内容不进响应。临时应用不提供虚假持久累计。

### 9.4 验收

先新增失败测试确认缺少接口/页签，再最小实现。存储：四类来源/用途、partial/未知/0、同ID去重、未归属、页大小/参数及分页不改变总计、双会话隔离、单次读快照、零写入、SQLite大整数总和及JSON字符串、重开/压缩/缺口/删除。HTTP：认证/workspace/冷会话、页预算、输入非法/SQL样式ID、读取故障且运行保持、0模型调用/0写入。前端：只在页签打开读取、键盘三页、BigInt/空态/缺口/部分/错误重试、会话及页切换旧响应隔离、根完成刷新/其他run不刷、分页重置及原上下文保持。专项通过后全量后端/前端/构建、AST/依赖/500行/链接/diff、架构安全复核；用临时真实Web+离线模型记录在1440/375宽度验证页面，明确离线证据，无供应商请求。


## 10. 108-03实际实现与Spec108收尾

已接read_summary→SessionStorage.read_model_usage→AsyncPersistentApplication.aget_model_usage→WebService.usage→认证GET→WebApi.usage→SessionUsage；ContextPanel新增第三页并延迟挂载，App传当前sid/root run，原预估与最近请求入口保持。6份后端生产文件和6份前端生产文件，新增前端组件/类型文件各1，无新Python模块、依赖、schema迁移、写入者或Runtime修改。WebSettings.usage_page_models默认20，临时服务设1已证明配置接线；仍受max_frame_bytes约束。

查询在现有连接锁内显式BEGIN，有限字段游标遍历、Python整数累计及十进制字符串传输。单事务快照、未知/0/partial、未确认与采集缺口各自表达；模型分页只裁模型桶，总计/用途/运行来源覆盖全部本会话已提交记录。无记录显示空态，不能从旧消息补造。冷读取不装载图，权限、恢复和必要保存无新分支。每次累计仍是本会话O(n)扫描、占现有短读锁；多次HTTP分页不承诺冻结快照，这两项为已说明限制。

测试：最初一项新夹具把unavailable事件配了缓存0，与既有事件合同冲突，修正新夹具后10项均因缺少查询接口/配置而正确红灯；前端初始因组件不存在红灯。实现后HTTP范围测试曾直接改活会话库行，与已有Actor权威状态冲突，改用真实冷会话夹具，保留404及脱敏断言。另用红灯复现模型下一页重新折叠，后续页自动展开后通过。新增16项后端/8项前端测试；原ContextPanel测试只补新增内容节点参数，原行为断言保留，原8次适配器生产链新增累计断言而不替换原断言；未改conftest或测试基础设施。

| 命令（根目录执行，npm在frontend） | 实际结果 |
|---|---|
| .venv/bin/python -m pytest -q tests/test_model_usage_queries.py tests/test_web_model_usage.py tests/test_model_usage_persistence_paths.py | 23 passed in1.30s；/tmp/naiyue-10803-expanded.log |
| PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q | 2758 passed in114.04s；/tmp/naiyue-10803-backend-final.log |
| npm test -- --run | 28文件198 passed；/tmp/naiyue-10803-frontend-verified.log |
| npm run build | TypeScript成功，Vite211模块成功；/tmp/naiyue-10803-build-verified.log |
| .venv/bin/python -m pytest -q tests/test_architecture_semantics.py | 16 passed in0.66s；/tmp/naiyue-10803-architecture.log |

架构/安全按切片基线分别只读深度复核，无确认遗留；各自16项后端及12项前端定向独立复跑通过，安全另验证7项参数/访问边界。23项接线证明8次主子/摘要/快详审保存后查询输入800/输出80、分页总计不变且不增请求。新查询测试覆盖单次读取中另一连接提交仍读一致快照、超过SQLite有符号64位的合计、零写入、同ID去重继承、双会话/冷会话、SQL样式游标、缺口/未知/部分/0、响应预算与读取失败不停止根任务。

实际页面：临时Web/SQLite沿正式应用链生成4条调用（3returned/1failed），IAB1440和375px检查第三页、分页、未提供项、缓存0及长模型名；总输入4611686018427392196跨页保持。375px初次大整数高度47px，改小屏标签/数字上下排布后高度23.5px且完整单行；文档scrollWidth=clientWidth=375，桌面两者1440且面板440。空会话先显示无记录，再从UI发起第5次离线模型调用，根完成后无需刷新自动变为输入2347/输出79。控制台0告警，截图：[桌面](/tmp/naiyue-10803-desktop.png)、[手机](/tmp/naiyue-10803-mobile.png)、[空态](/tmp/naiyue-10803-empty.png)、[自动刷新](/tmp/naiyue-10803-completed.png)，有限证据/tmp/naiyue-10803-ui-evidence.json。小屏CSS调整后再次198项前端/TS/Vite211通过；后端代码自2758全量通过后未改。

最终静态检查：425份Python AST/500行、42份前端生产源文件500行、655个本地链接目标/79个标题锚点、compileall/pip check及git diff --check通过。本片相对实施前快照共26文件，12份生产/5份测试/9份文档，无脚本、依赖或真实配置改动。

临时服务和数据库、访问凭据及测试标签已回收，视口覆盖已重置，截图/有限证据保留。0次真实供应商请求，未打开/改写真实配置或用户库、未提交push，历史脏树保持。Spec108三片全部完成；唯一下一步先讨论持久诊断与Web交付缓存字节预算，正式schema/查询/故障和清理接线另写Spec。checkpoint安全修剪及项目侧模型重试、证明工具未执行后的安全重试仍为用户明确延期项；103其余UI、Windows/Linux原机和PyPI交付独立推进。
