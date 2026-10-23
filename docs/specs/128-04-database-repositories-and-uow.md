# Spec128-04：数据库、领域仓储与显式工作单元

状态：2026-10-23已完成本机验收，未提交/push/发布。基线为隔离分支上已验收的01—03；快照 `build/spec128-04/baseline/`。本片不提交、发布，不开发worktree创建能力。

## 1. 当前问题与边界

SessionStorage有44个方法，混合连接/进程锁、项目绑定、会话、档案、回复、子Agent、用量、诊断和跨表事务。部分SQL辅助函数还自行开启事务；直接搬入多个类会让复合操作提前提交。MemoryAccess、概览/候选批次直接取得私有锁和连接。个人库另有初始化、格式升级和锁策略，不能强行套用项目迁移。

外部CLI/Web方法、数据库schema18、文件位置、Project/ExecutionRoot与权限合同不变。内部SessionStorage并非长期外部API；迁移当前消费者及测试后删除，不保留44方法的转发层。框架仍通过AsyncSqliteSaver独立连接管理checkpoint；应用事务不承诺提交框架连接的写入。

## 2. 对象与事务合同

- Database负责应用连接、线程锁、事务、资源关闭和初始化接入。项目打开保持安全文件检查、进程排他锁、还原标记、迁移备份和错误映射；个人打开保持共享SQLite并发、超时、DELETE journal、FULL同步、独立格式/备份升级。两者是同一种Database的显式打开入口，不建设通用后端注册器。
- ProjectStore只组合database及sessions、conversation、replies、subagents、usage、diagnostics、memory仓储，以及runs工作单元；无旧平铺方法或动态兼容代理。仓储的独立操作通过Database.transaction进入一次事务，SQL核心接收该连接且不commit。复合工作单元直接复用这些SQL核心，不调用会另起事务的仓储入口。
- Database.transaction(write=False)是同步上下文，写操作BEGIN IMMEDIATE，读操作BEGIN；只允许最外层所有者，拒绝嵌套，不隐式savepoint。error_code只在仓储明确指定时映射SQLite边界错误；异常回滚并保留主异常；整段事务在一次run_blocking任务内完成，不跨模型/网络await。
- SessionRepo管理会话及项目绑定；ConversationRepo管理档案/head/history；ReplyRepo管理回复及重生成；SubagentRepo管理实例CAS；UsageRepo、DiagnosticRepo管理观测事实；同一个MemoryRepo实现服务项目库与个人库。已有SQL优先复用。
- RunUnitOfWork.finish_run将档案更新、子Agent父run确认、回复状态、会话终态/恢复指针写入同一事务。子run/版本/来源失败或SQLite故障全部回滚；应用仍报session_save_unconfirmed并禁止未确认会话继续。删除会话的相关表和本连接上的checkpoint/writes也保持一笔事务。
- 用量/诊断flush与保存不加入run必要事务，保留已有降级与故障诊断。
- 项目记忆写入：同一项目事务内验证档案来源、CAS、版本/来源/FTS/抑制记录。个人写入：先项目读事务再个人写事务，保持项目→个人锁顺序、原验证时点和原错误码。仅个人库写入，无跨库原子提交承诺。MemoryAccess使用仓储/工作单元，不拿私有锁和连接。
- 迁移专属函数保留各自显式BEGIN，DDL和版本在同一步回滚；不把业务SQL的事务迁移应用到schema初始化。close也使用数据库锁，等待当前事务释放连接。
- 删除无生产消费者的同步checkpoint_connection。唯一旧测试改用正式AsyncSqliteSaver读取，保留恢复状态断言。

## 3. 真实主链

正常：open_async_persistent_application → acquire_blocking(ProjectStore) → Database(path) → 既有迁移 → AsyncSqliteSaver.setup。用户run结束 → SessionPersistence.afinish → 收集ChatUpdate → run_blocking(RunUnitOfWork.finish_run) → 一个应用事务写入四组必要事实 → 提交 → 可选记忆后台任务/修剪。聊天/history通过ConversationRepo读快照。

失败：归档已写到本事务、后续子Agent checkpoint确认或回复/会话SQL失败 → Database回滚整个事务 → 既有SessionPersistence保存未确认错误及blocked状态；已独立保存的用量/诊断保持原语义。个人写入来源不符 → 项目来源验证raise → 个人事务回滚 → MemoryReadError保持，项目档案无写入。

## 4. 影响范围

新增storage/database.py、project_store.py、sessions.py、conversation.py、replies.py、subagents.py、usage.py、diagnostics.py、personal_database.py、chat_writes.py、queries.py、memory.py、unit_of_work.py及必要共享SQL小模块。拆除storage/session_storage.py与独立PersonalMemory资源所有者；SQL辅助函数按真实事务职责迁移。同步application/async_persistent、sessions/session_persistence、conversation/history与重生成、subagent持久化、observability持久化、memory读写/任务、CLI启动、checkpoint修剪、脚本和所有当前测试消费者。迁移测试helper仅为使用新内部接口、构造原坏库/故障注入，不减断言、不放宽失败条件。

## 5. 验证

先红灯验证新对象接线、嵌套拒绝、跨仓储中途故障回滚、父run不符回滚、个人来源失败不落地；再实现最小通过。既有备份/还原/迁移/重启/审批/续跑/记忆CAS与可选观测故障测试保留。最终全量后端、前端测试/构建、compile/pip/diff、500行、标准wheel/Twine/源码外安装和包内源码字节核对；0真实供应商。当前调用链及删除/保留接口清单在验收节追加。


## 6. 当前对象、调用链和删除项

`ProjectStore(path)`只负责组合与关闭资源；实际对象为`database: Database`、`sessions: SessionRepo`、`conversation: ConversationRepo`、`replies: ReplyRepo`、`subagents: SubagentRepo`、`usage: UsageRepo`、`diagnostics: DiagnosticRepo`、`memory: MemoryRepo`、`runs: RunUnitOfWork`。单个仓储操作开启一次有边界的事务；复合run工作单元直接调用同连接的SQL核心，拒绝重新进入仓储事务，不存在隐式savepoint或动态旧方法代理。

必要保存的真实顺序：`SessionPersistence.afinish`先在事务外flush可选usage、读图状态并用`collect_chat_updates`构造不可变`ChatUpdate(thread_id,checkpoint_ns,agent_path,expected_revision,checkpoint_id,messages,stopped,boundaries)`；一次`run_blocking(storage.runs.finish_run,...)`内检查档案revision、写消息/边界/head、核对父run并确认子指针、结束重生成、更新父终态和checkpoint指针，再统一提交。成功返回后才启动可选记忆工作及checkpoint修剪。失败回滚后，`afinish`记录诊断、加入blocked并保留`session_save_unconfirmed`。

个人写入为`MemoryAccess.query → MemoryRepo.mutate_with_sources`；先锁住项目读事务，再开启个人写事务，`memory_mutations.commit(target,source,...)`仍在本次事务中校验来源、CAS、状态和输出预算。同库写入只开启一个写事务；个人写入只修改个人库，没有新增跨库原子提交承诺。用户记忆位置`data_dir/personal/memory.sqlite`及全项目可见性不变；`--no-session`仍不创建身份文件、不开放项目history/memory。

删除`storage/session_storage.py`和`application/memory/memory_personal.py`两个资源所有者、44个平铺入口及无生产消费者的同步checkpoint_connection。`ProjectStore`只有初始化/关闭/上下文管理，没有转发业务方法。所有当前生产、脚本和测试已迁移。旧schema初始化函数仍由`migrations.initialize_store`调用并拥有每个迁移步骤的显式事务；业务SQL核心由当前仓储/UoW调用，保留它们不属于旧入口兼容。`AsyncSqliteSaver.from_conn_string`仍拥有独立异步连接，不纳入应用事务。唯一旧同步checkpoint读取测试已经改用该正式异步入口，保留实际恢复消息断言。

测试基础设施的范围：`storage_backup_fixture.source_store`现在返回实际`Database`，供既有备份/还原测试使用原连接与路径；会话、观测的SimpleNamespace替身和故障注入目标迁到实际领域接口。未修改conftest、删断言、skip、放宽权限或版本/异常/顺序条件。新增关闭测试用事件和所有权锁同步；强化必要保存测试在实际执行四组写入后触发最终SQL失败，确认每组回滚并成功重试，独立SQL trace复核无提前COMMIT。

## 7. 实际验收（2026-10-23）

- 后端：`PATH="/Users/zmw/naiyue-test-agent/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short` → **4714 passed in309.79s**，`build/spec128-04/backend-verified.log`。比03新增12项合同分支；记忆/生命周期289项、存储/CLI/概览进度260项、最终合同16项均通过，已有断言保留。
- 前端：`npm test -- --run` → 50文件423 passed；`npm run build` → TypeScript/Vite成功。保留已有大chunk提示，未将其当成错误或额外改打包配置。
- 标准本机目标：`.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec128-04/verified-dist`、Twine通过；包内230份Python、4份前端和README元数据逐字节一致，已删除的两个资源所有者不在包内。源码目录外的全新环境通过`verify_wheel_install`，包含真文件、审批、SQLite重开、备份/迁移/还原、续聊、子实例和项目/个人记忆，0供应商请求。详见artifact-verification.json、install-verified.json。
- compileall、pip check、AST/500行、git diff --check通过；823份冻结源码/测试/README无漂移，10个主要新模块正逆序冷导入通过。四个迁移函数AST主体与03基线完全一致。
- 架构/安全独立复核发现的迁移DDL事务、闭库错误映射、批次BEGIN错误映射已先复现再修正并复核关闭；主实现新增close持锁回归。失败日志和旧测试接口迁移过程全部保留，不能将途中失败轮次算作完成。详见review-findings.json、verification.json。

边界：仅Mac ARM64本机安装证据；没有新增真实模型质量或其他原机/远端CI证明。未改真实配置/用户数据库/依赖/版本，没有提交、push或发布，不新增用户掌握声明。唯一下一步是讲解并确认05应用组合与领域子API；06会话协作对象和实际worktree生命周期继续独立。
