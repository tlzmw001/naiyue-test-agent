# Spec109：Web 重播字节预算与持久诊断

日期：2026-10-18。109-01/02均已实施并验收；109-02合同见第8节，当前结果和剩余工作见第9节。

## 1. 范围与现有链路

运行事件由现有RunEventPublisher发布，Web的DeliveredRun.observe负责当前消息/工具/子智能体视图及SSE补读。当前deque(maxlen=256)只限制条数，WebSettings.max_frame_bytes默认1 MiB限制单条，整队列尚无字节上限。RunDelivery在新运行登记时按retained_runs=16清理已结束运行，活动运行另计，本片保持此时机。

用量已在Spec108按调用身份持久化；聊天档案、用量、checkpoint分别保留原职责。109-01只补Web临时重播预算，不修改Agent循环、审批、必要保存、SQL schema、聊天历史或前端协议。109-02再讨论持久诊断，不借本片提前接线。

## 2. 109-01合同

- 每个根运行的事件队列同时满足event_capacity（默认256条）和event_cache_bytes（默认4194304，即4 MiB）。子智能体事件沿根运行同一队列计入；不同根运行分别计数。
- 字节使用现有encoded_size：投影后的wire_event经紧凑JSON、ensure_ascii=False、UTF-8编码计算。包含事件信封和正文，不含SSE格式前缀、Python对象/索引、已交给网络层的批次或当前消息/工具视图；不承诺整个进程RSS上限。
- 接受单条事件仍受max_frame_bytes约束。通过单条检查后，即使它超过整个缓存预算，也必须更新当前消息/工具/子智能体投影和终态；只是不保留在重播队列。
- 追加后，条数或字节任一超限都从最早事件移除，更新累计字节及最后淘汰的序号。禁止依赖deque自动淘汰，否则字节统计不能同步。
- 游标after小于最后淘汰序号时，HTTP预检返回409/resync_required；已打开SSE发送同名控制事件并结束。after等于最后淘汰序号仍可读取后继事件；全部事件被淘汰也要能识别旧游标。after等于当前last_sequence可以成功同步空尾部。
- 缓存淘汰不设置projection.incomplete；现有实际丢事件、单条过大及投影异常继续保持原缺口保护。重复、外部会话/运行和乱序旧事件不得改变缓存计数。
- 默认配置来自现有启动聚合ToolSettings的web_event_cache_bytes（严格正整数）。settings.toml缺省用4 MiB；非法值警告并只回退此字段，保留其他合法设置和权限。修改后重启生效，不自动改写私有配置。
- WebSettings.event_cache_bytes允许可信代码显式覆盖，优先于TOML；未显式提供时create_web_app从已加载的ToolSettings注入。默认值复用聚合字段，不维护两份独立数字。现有event_capacity/max_frame_bytes/retained_runs配置入口保持。

配置聚合仍沿用tools/settings.py已有的max_active_runs、storage、usage_persistence等启动参数容器。本片只增加一项数值，不加入Web行为、Web反向import或另建配置加载器；聚合位置的整体改名不在本片。

## 3. 文件与方法

| 文件 | 改动与实际消费者 |
|---|---|
| tools/settings.py | ToolSettings增加web_event_cache_bytes；load_tool_settings接已知顶层键、校验/局部回退并传入返回对象 |
| web/settings.py | WebSettings增加event_cache_bytes，默认值取现有启动聚合定义 |
| web/app.py | create_web_app/lifespan在已有启动读取后，将未显式覆盖的预算注入交付设置，交给RunDelivery与WebService；认证/路由配置保持 |
| web/delivered_run.py | DeliveredRun增加累计字节和淘汰序号；observe复用编码大小，_retain_event处理手工淘汰，_check_cursor覆盖空队列缺口 |
| tests/test_web_replay_budget.py | 精确边界/UTF-8/多次淘汰/空缓存/旧游标/单条限制/重复外部事件/实际HTTP执行隔离 |
| tests/test_web_replay_settings.py | 缺省/合法/非法局部回退/样例解析/启动加载与显式覆盖的实际接线 |
| docs/examples/settings.example.toml | 注释说明预算含义、范围及重启生效；只改仓库样例 |
| 本Spec、handoff、future-work、web-chat、implementation-roadmap | 当前状态与唯一下一步同步，历史证据保留 |

不新增生产模块/依赖/公共方法签名，不改conftest或现有测试基础设施。测试临时TOML/SQLite通过既有tmp_path与web_client隔离；不读取真实模型配置或用户会话库。

## 4. 改前改后与数据例子

改前：load_tool_settings → Web启动 → RunDelivery订阅 → DeliveredRun.observe → 单条检查 → deque自动按条数淘汰 → 更新当前投影 → SSE。

改后：load_tool_settings准备web_event_cache_bytes → create_web_app注入event_cache_bytes → 原RunDelivery订阅 → observe生成payload及其UTF-8字节数 → 原单条检查 → _retain_event同步条数/字节/淘汰序号 → 原投影/终态 → 原SSE或resync_required。

例：缓存序号1—8共900字节，预算1000；序号9为300字节。移除最早若干条直到不超过1000，当前进度仍由9更新。若最后淘汰到3，after=2要求重同步，after=3可以补读4—9。某条本身大于1000但未超单条上限，则更新当前视图后队列可为空，淘汰序号包含该条；其后的正常小事件仍可补读。

失败场景：浏览器落后，旧游标已淘汰 → HTTP409或SSE resync_required → 现有客户端读取当前状态/最近消息与历史页，不重跑模型/工具、不改变批准决定。单条超过max_frame_bytes仍沿原投影缺口保护，不借预算改写该行为。

## 5. 验收

先写失败测试，确认缺少预算/配置接线导致正确红灯；再最小实现。测试检查真实JSON UTF-8字节而非字符数、条数和字节各自/共同触发、精确等值边界、单事件大于整个预算、空队列和恢复后的游标、实时根子/工具/停止视图保留、跨根隔离、重复/外部事件无变化。HTTP离线模型验证缓存缺口返回409、刷新内容完整、按当前序号同步且调用次数不增加。

配置测试覆盖默认4 MiB、合法临时TOML启动实际消费、无配置默认、非法0/负数/bool/字符串/未知值局部回退，合法权限保留、显式Web设置优先和样例可解析。所有已有后端/前端测试、TS/Vite构建、compileall、依赖、分层/500行、文档链接和diff检查通过后记录结果。未运行真实供应商请求或Windows/Linux原机验收，不据此声明它们通过。

## 6. 109-02原讨论方向，实施合同见第8节

复用工作区SQLite的根运行摘要与关键事件表，应用层订阅现有run/model/tool/approval/compaction/failure边界。保存关联ID、时间、状态和有限结构化错误；不保存delta/高频进度或完整提示词、供应商响应、消息/图片正文。正文与用量通过已有身份关联对应档案。

初始方向：单根运行含全部子智能体共1 MiB、预留终态摘要；工作区30天/256 MiB有效载荷，先过期再最早结束的根运行整组清理，不淘汰活动运行。异步小批写入、正常关闭排空，崩溃允许尾部缺失并显式报告未闭合，诊断失败不覆盖原结果/异常；必要保存失败继续按原合同。run_finished晚于必要保存发布，不能照搬用量的finish刷新位置。精确schema、队列/清理及查询接口待下一片讨论确认。SQLite有效载荷预算不等于物理文件上限；checkpoint修剪独立延期。

## 7. 109-01实际结果与下一片

四份生产文件已接线，无新增生产模块。ToolSettings.web_event_cache_bytes沿原TOML解析/局部回退，WebSettings默认复用其字段定义，create_web_app只在未显式配置时注入同一交付设置给RunDelivery/WebService。认证和HTTP路由继续使用原配置，未修改业务入口签名。

DeliveredRun.observe在现有图片/进度投影后计算payload_bytes，原单条限制保留。_retain_event同步追加/手工淘汰、event_bytes和_evicted_through，均在原锁内。_check_cursor直接比较最后淘汰序号，覆盖完全空队列；正常淘汰不会设置projection.incomplete。实际丢失、投影失败和单条超限保护保持。新方法是当前队列三个状态的同一修改入口，不是通用缓存层。

正常例：浏览器落后，缓存淘汰后GET events返回409/resync_required；读取run视图仍有完整答复且incomplete=false；按last_sequence补读成功，历史页可读，重复request_id返回同一回执，模型只执行一次。失败例：单条通过frame上限却超过整个缓存预算，队列可空，旧游标仍被拒绝；已打开SSE会发送resync_required并结束。工具最新进度、停止标记和完成状态仍保留，子事件更新独立子视图但计入同一根队列。真实超过frame上限继续标记实际投影缺口。

新增两份测试共26项。首批23项中19项因缺少预算字段/计数/接线正确红灯，4项原有extra-forbid校验已通过；实施后23项全绿，再补3项已开SSE/进度停止/根子混合边界。精确等值测试重放同一组已发布事件，避免依赖时钟字符串格式。未改conftest、既有测试helper或放宽断言。

| 命令/范围 | 实际结果 |
|---|---|
| .venv/bin/python -m pytest -q tests/test_web_replay_budget.py tests/test_web_replay_settings.py | 26 passed in0.94s；/tmp/naiyue-10901-replay-final.log |
| 上述两份及web_api/web_delivery/web_tool_progress/web_images/tool_settings/tool_settings_entries | 68 passed in1.90s；/tmp/naiyue-10901-focused-final.log |
| PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q | 2784 passed in116.25s；/tmp/naiyue-10901-backend.log |
| npm --prefix frontend test -- --run | 28文件198 passed；/tmp/naiyue-10901-frontend.log |
| npm --prefix frontend run build | TypeScript无错误，Vite211模块成功；/tmp/naiyue-10901-build.log |
| .venv/bin/python -m compileall -q naiyue_test_agent；.venv/bin/python -m pip check | 通过；No broken requirements found |

独立架构复核36项、安全复核26项定向通过，无本片新增确认发现。主代理复查空队列后续小事件恢复、UTF-8多字节、条数/字节同时淘汰、重复/外部事件及各根隔离；预算不保证SSE在途列表或整个当前投影的总内存。427份Python AST/500行、42份前端生产文件500行、当前文档链接和git diff --check均通过；未修改前端源码，故未新增页面视觉验收声明。

0次真实供应商请求；只用临时TOML/SQLite和离线模型。未修改真实配置、用户会话库、依赖或其他项目；无提交/push。checkpoint修剪、原机/更多供应商和103其余UI保持独立。

下一片先展开109-02：应用层DiagnosticPersistence订阅有限关键事实，DiagnosticStorage负责同库摘要/事件与根组清理，原持久工厂拥有写入器并在执行任务结算后关闭；冷查询经已有应用/Web门禁读取。具体接口、schema迁移、有限写队列、缺口及旧进程未闭合记录的处理须在确认后写为实施合同，当前无任何诊断生产实现。

## 8. 109-02实施合同

用户授权本片持久诊断的保存、淘汰和只读查询，不新增追踪界面或改动运行/恢复策略。第一片第7节“下一片”是当时边界；本节取代待确认状态。沿现有应用层事件订阅和SessionStorage锁/连接接线，无新ContextVar、模型调用包装、通用消息队列或依赖。

### 表结构与归属

工作区schema9事务迁移到10，新建naiyue_diagnostic_runs和naiyue_diagnostic_events。运行表以自增capture_id供只读倒序分页，(session_id,run_id)唯一；保存有限summary_json及其UTF-8字节数、事件字节数、最后已观察序号、capture_state、closed_at和预留摘要额度。事件表以(session_id,run_id,sequence)唯一保存有限body_json和其字节数。主子共用根run组，source保留agent_path/agent_id/agent_run_id/parent_run_id/parent_tool_call_id。事件顺序允许跳号，不将主动过滤的delta误判为缺失。

summary包含根开始/结束时间、最后观察时间、原根status/failure、是否未闭合、捕获缺口原因/丢弃数、最后序号及已保存事件数。capture_state=open/closed/interrupted是诊断记录状态，不能替代会话状态或推断工具执行。结束事件只更新根摘要；子结束作为普通组内事实。

应用DiagnosticPersistence.accept只投影有限字段：根子run、物理/逻辑model、tool、approval、compaction/context recovery以及控制/观察失败边界；不落盘message_delta、tool_progress、model_context_snapshot、消息快照、审批target/workspace、命令参数或SDK原文。字符串/ID有限，调用ID列表截为有限引用并标记fields_omitted；不对任意dataclass做完整asdict。错误只保存既有stage/category/reason/error_type。原文和用量仍由对应档案负责。

消息完成/修订仅保存message_id/message_type/visibility/tool_call_id/status等身份事实，不保存snapshot_json/正文/图片，供诊断关联聊天档案。原审批恢复的origin_run_id保留以关联前次根运行。身份引用不保证崩溃前原文已归档，诊断不会补造缺少的正文。

### 配置、队列与收尾

沿ToolSettings启动聚合增加diagnostics节，严格正整数，非法整节警告回退且保留其他合法权限。默认max_pending_events=1024、max_pending_bytes=4 MiB、max_run_bytes=1 MiB、summary_reserve_bytes=4096、max_workspace_bytes=256 MiB、retention_days=30、cleanup_batch_runs=32。摘要预留至少2048，单run须大于预留且工作区至少容纳单run；不支持0/无限。待写条数/字节包含已开始但尚未确认的事件；在途根摘要状态数量也受max_pending_events约束。有限终态元数据另保留在该根状态中，不与高频事件竞争。

原持久工厂创建写入器并订阅；只有持久应用启用，不为内存应用伪造持久化。根run_finished晚于必要保存，必须由本订阅消费，不能复用usage.aflush_run。_drain顺序写已接受事实，根在途事件结算后写终态摘要/缺口并释放内存根状态。正常关闭先等待原应用运行结算，再解除订阅并排空诊断，最后关闭数据库；保留原主要异常。写入/清理普通异常日志可见，降级受影响根的诊断，不覆盖运行结果/异常；取消等BaseException不伪装成成功。

队列满丢弃新细节、标记该根缺口，已接受事实继续写；单根存储满不再追加细节但仍更新有限摘要。终态细节可因额度省略，根摘要不能因此被错误记为执行失败。记录不可用时可能只有日志而没有该run行，不宣称完整审计。正常查询允许短暂滞后，不为了诊断让每个arun等待额外flush。

### 淘汰与崩溃

所有预算基于有限JSON UTF-8，不是SQLite/WAL/索引或RSS。每个open根按事件字节加摘要预留计入工作区容量，closed按实际载荷计入；保守预留保证根收尾更新有空间。单根细节最多max_run_bytes-summary_reserve_bytes。无法腾出空间时拒绝新细节/新诊断组，日志或已存在摘要标缺口；不删除活动根、不让预算阻止工具执行。

prepare_diagnostics在应用启动时将旧open诊断标为interrupted/missing_terminal，原run status仍未知，checkpoint/元数据不变。正常关闭缺少终态也只标未闭合。启动、根收尾、追加将超预算时，在短事务中清理最多cleanup_batch_runs组：先过期，再从最早closed_at的非open根整组删除。单次可能仍有过期组，后续触发继续；容量未腾出就拒绝追加，不无限循环。诊断清理绝不删除聊天、用量、checkpoint；删除会话则在既有删除事务中清理所属诊断，迟到观察不能重建已删除会话。

### 文件、接口及实际调用链

新增application/diagnostic_persistence.py：有限投影、DiagnosticPersistence.accept/_drain/aclose；新增application/diagnostic_storage.py：initialize/prepare/save_event/finish_run/prune/read_runs/read_events等有限SQL算法。SessionStorage在锁/短事务内调用这些函数；不是独立存储类或新数据库。修改原持久工厂创建/关闭以及AsyncPersistentApplication的只读alist_diagnostic_runs/aget_diagnostic_events，内部构造注入诊断依赖，既有公开工厂签名保持。

Web新增GET /api/sessions/{sid}/diagnostics?before={capture_id}及GET /api/sessions/{sid}/diagnostics/{rid}?after={sequence}。沿现有认证和_session_status工作区检查，只读冷查询，不装载图或修改权威状态。WebSettings.diagnostic_page_size默认50、最大100，复用max_frame_bytes响应限制。运行页next_before倒序；事件页next_after升序且包含当前摘要。缺失会话/运行404，数据库读取失败固定503/diagnostic_view_unavailable，非法游标422，过大响应保持view_too_large；不输出异常/SQL正文。

正常：arun → 原发布器 → accept筛选入队 → _drain → SessionStorage.save_diagnostic_event → 同库有限事件；原必要保存 → 原run_finished → 写终态摘要。关闭重开后只读API按sid/rid分页，不发起模型/工具。失败：数据库写入抛OSError → 日志仅error_type，记录缺口/禁用该根细节，原Bash ToolMessage或原模型异常继续；另一根诊断仍可保存。真实硬退出只留下已提交诊断，重开标诊断未闭合，不重放调用。

首轮全量发现并用当前框架探针确认：AsyncSqliteSaver.setup首次执行PRAGMA journal_mode=WAL，与run_started触发的元数据写事务并发会报database is locked。装配必须在订阅任何运行事件前await saver.setup()，只创建官方表/设置日志模式，不创建虚假checkpoint或调用模型。不是通过重试/延迟诊断绕过竞争。

既有消费者同步：订阅器精确列表加入诊断订阅，仍检查流断开/故障时应用订阅保留、关闭后全部解除；schema断言从9升级10，数据保留断言不变。整库只读快照测试必须先结算之前运行的异步诊断，否则合法后台写入会被归因到GET。tests/native_application_helpers新增await_diagnostics_idle，三份只读测试在快照前使用；不关闭诊断、不排除诊断表、不放宽total_changes/iterdump断言，也不在生产链新增测试专用flush或兼容入口。

### 验证与迁移纪律

先补失败测试确认缺少诊断/字段/接线；覆盖字段过滤与秘密哨兵、物理/逻辑/根子/摘要/Auto来源、正常/工具错误/原异常/取消、队列满含在途、writer/清理失败及关闭排空、重复序号、单根/全库预算与摘要预留、TTL整组淘汰/活动保护、schema9迁移/会话删除、真实子进程硬退出重开、HTTP认证/跨工作区/分页/冷读取/故障。仅迁移旧测试对“当前schema版本”的断言和旧版构造数据，不减少原行为断言或改conftest。

完整后端/前端/构建、compileall/pip check、500行/分层/链接/diff，以及架构/安全复核后收尾。所有SQLite和TOML使用临时文件，不改用户库/真实配置；0次真实供应商请求，不提交push。checkpoint修剪、模型重试/安全工具重试和103其余界面均独立。

## 9. 109-02实际结果与Spec109收尾

两份新增生产模块已接线：DiagnosticPersistence约238行，负责字段投影/有限队列/根状态/异步排空；diagnostic_storage约190行，负责同库摘要/事件/整根淘汰/分页。原SessionStorage锁与事务拥有数据库；原持久工厂先完成官方saver.setup，再创建用量/诊断订阅。内存应用不启用持久诊断。AsyncPersistentApplication关闭在原任务/操作结算后排空两类观察写入器，公开持久工厂签名保持，内部构造注入诊断依赖。

实际链：原run_started→accept投影→队列→save_diagnostic_event→有限事实；原必要保存→run_finished→终态摘要→释放捕获名额。当前写入中的终态摘要也计入根状态额度；writer已接收事实与在途IO计入待写预算。存储拒绝追加只标诊断缺口，正常结果和原异常身份保持。事件过滤允许序号跳跃，API同时返回执行status与capture_state/capture_incomplete，不把工具error_result、诊断截断或未闭合混为整个运行失败。

SQLite当前schema10，旧工作区4—9沿原迁移链保留会话/聊天/用量；旧全局1—3继续拒绝。prepare仅将旧open捕获标未闭合，不改权威会话状态/checkpoint。根子共享根组额度，非活动组按TTL/容量整组淘汰，清理数量受批次限制，活动组保留；摘要预留参与全库容量。清理后原文、用量、checkpoints/writes条数保持；迟到写入不重建删除会话。

新增两条已认证只读HTTP入口，见第8节；按capture_id/sequence分页，冷查询不装载图、query_only下成功，错误为固定404/422/503或view_too_large。没有新增前端诊断入口，前端现有表现/协议保持。snapshot正文、审批target/workspace、工具输出和SDK原始异常均未持久化，消息完成/修订仅带原消息身份。

测试与修复记录：首批15项正确红灯后转绿，接线7项正确红灯后转绿；另外补字段子类泄漏、投影失败缺口、摘要在途名额、消息身份、取消、各额度、真实硬退出及现有主子/摘要/Auto组合。新测试总计33项，不宣称每项都单独做过红灯。真实框架WAL探针确认冲突，生产初始化前置测试先红后绿；原失败的压缩、重开和删除回滚随后通过。两处精确订阅列表/三处schema版本同步，三个只读消费者使用结算helper，原断言均保留，未改conftest。

| 验证命令/范围 | 实际结果 |
|---|---|
| .venv/bin/python -m pytest --collect-only -q tests/test_diagnostic_storage.py tests/test_diagnostic_persistence.py tests/test_diagnostic_application.py tests/test_web_diagnostics.py tests/test_diagnostic_restart.py | 33项新测试 |
| 上述5份＋test_model_usage_persistence_paths.py＋test_architecture_semantics.py | 56 passed in2.39s；/tmp/naiyue-10902-focused-final2.log |
| 原流断开/分页/压缩/历史/重开/订阅故障/删除/长历史8份回归 | 83 passed in6.56s；/tmp/naiyue-10902-consumers-final.log |
| PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q | 2817 passed in115.33s；/tmp/naiyue-10902-backend-final.log |
| npm --prefix frontend test -- --run | 28文件198 passed；/tmp/naiyue-10902-frontend.log |
| npm --prefix frontend run build | TypeScript无错误，Vite211模块成功；/tmp/naiyue-10902-build.log |
| .venv/bin/python -m compileall -q naiyue_test_agent；.venv/bin/python -m pip check | 通过，No broken requirements found |

实际离线组合的8次MockTransport请求覆盖主/子/摘要/Auto快详审，全部记录invocation身份和purpose，诊断查询不增调用；工具错误后根正常完成、模型原TimeoutError不被观察IO故障替换。额外临时Event阻塞写入探针：arun在诊断暂停时仍返回，删除会话后放行迟到IO，诊断及会话表均空，只有1次离线模型调用。子进程os._exit(37)后保留已提交关键边界，重新prepare只标捕获未闭合。

首轮整套7项失败归因：3项首次WAL竞争、2项旧订阅名单、2项合法异步诊断写入被只读快照归因；均已按前述合同修复/迁移，未跳过或放宽测试。434份Python AST/500行、Runtime反向应用导入0、前端生产500行、当前文档链接和diff检查通过。深度复核由主代理按check的架构/安全及对抗清单完成；两项独立子审查因额度限制未执行，不计作独立审查通过。

限制：诊断是可降级观察，允许查询滞后和崩溃尾部缺口，极端故障可只有日志；正文引用不保证其原文已归档。容量计算当前按根摘要求和，不承诺大规模运行数下固定耗时；清理每次仅有限批次。有效载荷预算不等于物理SQLite/WAL/索引文件或RSS，不自动VACUUM，不修剪checkpoint。

Spec109两片收尾。唯一下一步建议先讨论Spec103的GFM消息呈现（表格/任务列表/删除线），再逐片处理其余UI；已完成的U2普通工具摘要、U5输入/指令、U8搜索/折叠不重复实施。Windows/Linux原机、更多供应商真实矩阵及纯pip的nyt交付仍需独立推进。checkpoint安全修剪、项目模型重试和证明未执行后的工具重试保持明确延期；沙箱/长期记忆/后台及多层子智能体保持原独立范围。

0次真实供应商请求；未改真实配置、用户会话库、依赖或其他项目，没有提交/push。仅仓库配置样例更新；前序脏树完整保留。本轮为助手实现/验收，不新增用户掌握声明。
