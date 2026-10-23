# Spec128-05A：统一磁盘与临时会话的存储装配

状态：05A完成本机验收（2026-10-23），未提交。05B应用组合/子API、06会话mixin和实际worktree生命周期不在本片。基于01—04的当前隔离工作区，不修改原主目录并行成果、配置、依赖、版本或真实用户库，不提交/发布。

## 1. 问题、合同和当前链路

当前磁盘入口 `open_async_persistent_application` 打开ProjectStore和官方AsyncSqliteSaver，接入SessionPersistence、用量/诊断writer；临时入口 `build_async_local_application` 重复模型/MCP/会话装配，使用InMemorySaver，跳过仓储。因此临时会话缺少本实例档案分页、回复边界、重新生成和观测查询。05A复用04的仓储和业务算法，仅改变存储打开方式，暂时仍使用AsyncPersistentApplication承载共同能力；类的继承/名称和CLI/Web门面留给05B。

保留两项现有工厂的参数/调用方式、CLI命令和HTTP/SSE合同。临时入口仍返回需aclose的应用，磁盘入口仍是异步上下文管理器。新内部单一装配入口返回拥有全部资源的应用；aclose结束后连接也关闭，不再允许通过内部_storage读库。磁盘重开可核验保存，临时重开为空。

## 2. 数据和资源形状

- `Database(path)` 保持文件锁、安全文件、恢复guard、备份/迁移、事务/错误码。
- `Database.open_memory()`：`path=None`，独立UUID的 `file:/naiyue-<uuid>?vfs=memdb` URI；同步连接uri=True。仅初始化同一schema18，绝不做Path解析、mkdir、文件锁、备份或恢复guard。与磁盘共用事务、UDF、FTS、仓储和UOW。
- `ProjectStore.in_memory()` 与文件构造共用仓储组合，不复制SQL或模拟仓储。
- `StorageResources(store, checkpointer)`：内部资源上下文负责打开Store及aiosqlite连接。内存显式uri=True，磁盘路径仍是原文件；直接构造官方AsyncSqliteSaver并setup，之后才订阅writer。禁止shared-cache/plain :memory:双连接方案。
- 应用单一装配链：storage resources → project binding（仅磁盘可指定）→ memory（仅磁盘入口）→ catalog/MCP → SessionService → 两个writer → SessionPersistence → graph → 应用。临时模式不打开个人memory文件或创建身份文件；身份为project_id=None，执行目录仍可用。
- writer和检查点连接属于应用资源栈。正常close先停止/等待运行及必要保存，关闭模型/MCP，再drain writer，关闭memory、异步检查点连接和Store。栈上后续清理失败记录错误类型，不能覆盖执行或启动的原异常；取消要等待已开始的IO并回收资源。

## 3. 能力与权限边界

临时模式得到本实例会话历史/档案/重新生成/用量/诊断/审批/停止/子Agent所需的同一保存链。`--no-session`不创建`.naiyue/workspace.json`，不落盘聊天DB，不开放跨会话的项目history或memory工具端口，不打开个人memory库；即便目录已有UUID也不绑定。工具端口注入由run.project_id决定，不能用“有SessionPersistence”代替项目准入。项目普通模式与个人memory跨项目共享规则保持。

SQL内存库最后一个连接关闭即消失，每个应用URI不同。没有重启恢复承诺；同一实例恢复/查询用同一SQL链。工具输出档案仍按01保存到data_dir外部目录，不能宣称临时模式完全没有磁盘输出。真实worktree身份和执行目录失效合同保持。

## 4. 正常与失败链

正常：临时工厂 → 统一装配(memory profile) → create_session写RAM元数据 → arun同步checkpoint → SessionPersistence.afinish收集消息/回复 → RunUnitOfWork原子保存 → usage/diagnostic读取同库 → regenerate写新thread/会话revision → aclose收束并销毁两连接。整个流程不建项目marker或磁盘DB。

失败：内存/磁盘Store打开失败在模型/MCP启动前报错；检查点setup失败不安装writer/会话，回收连接和Store。run的必要保存失败仍session_save_unconfirmed，终局failed且不可发布completed；观测失败仍记录不完整并降级，不改变业务成功。启动取消在已开始的连接获取/IO完成后关闭资源。关闭时即便一个closer失败也继续关闭剩余资源，保留最初异常并记录次要错误类型（不记原始错误正文）。

并发：两个根会话并发checkpoint，两个可选writer与finish_run在同库交错，数据库busy等待沿用sqlite超时和现有锁；不使用shared-cache。在RAM的journal_mode=memory环境验证真实图运行，不能用设计探针代替整应用证据。

## 5. 影响文件与验收

生产：storage/database.py、project_store.py、新storage/resources.py；application单一assembly模块、两个现有工厂/应用close；sessions/session_execution.py的三处根run/审批和subagents/session_subagents.py的子调用/续审批端口注入；CLI删除依赖持久模式的旧观测查询拦截，让--no-session经原线程桥查询RAM事实。内部handler的persistent参数及所有消费者删除，不改CLI语法。适配器线程桥及Web协议职责保持。迁移旧测试针对AsyncSqliteSaver.from_conn_string/旧工厂位置的注入到真实新资源入口，保留取消、关闭顺序、异常身份、状态和安全断言；内部连接在app.aclose后不可读的用例改从关闭前捕获或磁盘重开核验。清理被统一仓储替代的AgentSession._subagent_records、子实例_get/_save/settle与列表的字典分支、finish_memory及其调用；生产仅assembly构造SessionService且两种profile都绑定SessionPersistence，影子仓储无剩余生产消费者。独立运行层InMemorySaver探针和直接SessionService根control单测不删除，仍有真实测试消费者；后者不再持有子实例影子表。

TDD先红后绿：RAM同库检查点/仓储、URI隔离、最后连接生命周期；整应用本实例档案和重新生成、无marker/个人库/项目工具端口；并发两个会话+writer、必要保存失败、取消/关闭顺序。复用现有磁盘恢复、审批/子Agent/CLI线程桥和安全回归。完成后全量后端、前端测试/构建、标准Mac ARM64 wheel/Twine/源码外安装、500行/compile/pip/diff及包内源码一致性。记录实际命令/输出，不声称真实模型质量或远端多平台证明。

参考：本机SQLite memdb和官方langgraph-checkpoint-sqlite3.1.1探针已验证共享两连接/隔离/释放；这是可行性证据。Pi同一SessionManager用persist开关控制保存，AgentX经消息/上下文仓储装配（版本与调用点见05讨论），借鉴业务API统一，SQLite资源与事务以本项目实际调用链验收。


## 6. 当前接线与行为测试迁移

实际对象：`StorageResources(store=ProjectStore, checkpointer=AsyncSqliteSaver)`；RAM模式`store.path=None`、`store.database.uri=file:/naiyue-<随机UUID>?vfs=memdb`。磁盘/临时两个工厂都只进入`application.assembly.build_application`，没有第二份业务装配。应用拥有AsyncExitStack，结束所有run/必要保存后关闭writer→memory→saver连接→Store。Database仅在最后连接关闭后失去RAM数据。

正常例：临时会话S在目录D内发one/two → schema18 RAM元数据与graph thread T → `SessionPersistence.afinish`读取已确认checkpoint并产生ChatUpdate/ReplyBoundary → `RunUnitOfWork.finish_run`原子写入 → `aget_chat_page`返回4条记录 → regenerate把最后回复替换到新thread，conversation_revision=1，同request_id复用原结果。执行权限仍是D，项目ID为空。关闭后另一个临时App找不到S。

失败例：根/子/子审批续跑的project_id为空时不注入history/memory；工具报告unavailable，不进入项目查询。目录D即使已有合法项目marker也不读取/绑定/改写。普通磁盘项目仍按01执行UUID/目录准入，个人memory路径不变。

测试迁移不是让保存降级：

- 旧graph代理增加durability参数并委托真实状态接口，仍验证recursion/stop/控制异常原身份、事件和状态。
- stream关闭只移除该订阅，精确保留usage/diagnostic两个应用writer；app关闭再断言订阅为空。
- app.aclose后连接关闭，磁盘状态在catalog关闭回调（必要保存已经收束、连接尚打开）捕获后断言；重开验证依旧保留。
- 两种profile都使用sync图durability，完整AI消息出现时图checkpoint可能已确认，但应用UOW仍未完成；通过阻塞真实finish_run，断言graph有AI、元数据running、档案head为空、没有run_finished，解除后ready/head/终局一致。独立框架探针仍保留异步checkpoint语义。
- 自定义任意返回值的保真测试放到真实runtime/RunObservation边界；新增临时应用反例证明没有确认checkpoint不能成功，持续必要读取失败同样session_save_unconfirmed/failed/blocked。
- baseline观察失败只注入首个可选读取，之后真实必要读取正常，仍验证只fallback一次、不编造消息及原降级事实。永久子checkpoint读取故障在执行时成为子tool feedback；必要档案读取仍失败时根不能报告已保存成功，两次尝试、子错误/秘密不外泄、根failed/blocked/running均有断言。

新准入反例先红再修：启动图装配失败的memory jobs回收；临时子scope门控（普通/审批续跑）；CLI旧持久模式guard。原始红灯和中途全量失败保存在build/spec128-05a，不把中途失败当作通过。没有修改conftest或公共测试helper，没有删断言/skip/伪造保存成功。

删除项：应用InMemorySaver装配、重复模型/MCP/graph装配、基于持久模式拦截CLI观测的参数与全消费者、子实例字典影子仓储/finish_memory。保留项：两个已使用的工厂调用形式、CLI拥有者循环/Web协议门面及当前应用继承类（05B迁移）；直接SessionService根状态/control测试的可选Persistence入口和独立runtime探针（有现有消费者），不再提供另一套子实例保存算法。


## 7. 实际验收（2026-10-23）

- 最终后端：`PATH="/Users/zmw/naiyue-test-agent/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short` → **4737 passed in335.12s，exit0**，见`build/spec128-05a/backend-verified.log`。比04净增23个参数分支：四份新文件22项及原线程桥SDK事实测试新增RAM分支。71项合同/入口、88项消费者迁移、44项消息/故障边界、清理后266项根控制/子实例等专项通过。
- 前端：`npm test -- --run` → 50文件423 passed；`npm run build` → TypeScript/Vite成功。未修改前端源码，保留原大chunk提示。
- 标准本机包：`.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec128-05a/verified-dist`成功；`.venv/bin/python -m twine check build/spec128-05a/verified-dist/*.whl`通过。包内232Python、4前端资源、README元数据逐字节匹配，包含两个新增模块。SHA256为`4d3a18e54b3a00015866c04ac3c98cf52979b307c36e648ebb9003d5330fa169`。
- 源码外安装：`.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec128-05a/verified-dist --output build/spec128-05a/install-verified.json`，exit0；CLI/Web/文件/审批、SQLite重开、备份迁移还原、子实例与记忆smoke通过，0供应商请求。仅本机Mac ARM64安装证据，无新真实模型或其他原机证明。
- compileall、pip check、git diff --check通过；生产AST/500行通过，6主要模块正逆序冷导入成功。最终冻结829源码/测试/scripts/前端源码/README无漂移，scripts/pyproject相对04未改。
- 架构与安全深度独立复核，子scope门控发现修正后复查关闭；测试迁移及废弃仓储消费者也经只读对照。无未关闭具体发现，见`review-findings.json`。原红灯/失败日志保留：首个测试收集路径纠正、五核心合同红灯、资源/CLI/子scope反例、首轮19失败4700通过，以及清理前4737通过332.98s中间轮。最终只认backend-verified冻结轮。包检查器首用错误的_assets/frontend路径，读取实际zip后改为_assets/web，最终全部匹配；未以此修改产品。

05B应用组合/领域子API与06会话协作对象尚未实现，实际worktree生命周期独立。原主目录并行成果、真实用户库、配置、依赖、版本未改，未提交/push/发布，没有新增用户掌握证据。唯一下一步：讲解并确认05B，批准后再迁移门面。
