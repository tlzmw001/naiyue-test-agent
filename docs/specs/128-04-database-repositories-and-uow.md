# Spec128-04：数据库、领域仓储与显式工作单元

路径版本边界（2026-10-24）：R1-02 已将 application 内的 SQL/schema 下沉到 storage，当前逐模块映射及事务合同见 [R1-02](R1-02-storage-dependency-direction.md)。本文件保留 Spec128 实施时点，不将旧内部导入路径当作兼容合同。

当前验收补充（2026-10-23）：原第5片的SQL过期CAS、personal来源失效与中途写入回滚两类持久回归已补齐，合同见第8节，实际结果见第9节。此前实现和各历史验收记录保留。

> 后续版本：05A已统一磁盘/RAM装配并删除子实例影子仓储，见[当前主链](128-05a-unified-storage-assembly.md#6-当前接线与行为测试迁移)；本文验收数字与下一步保留04完成时点，当前状态以handoff顶部为准。

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

## 8. 原第5片持久回归补齐合同（2026-10-23）

用户在原7补齐验收及下一片说明后批准实施。本节补原十片审查发现的两类覆盖缺口，不重做Database/仓储/UoW，不新增存储API或修改schema。当前调用链和错误分支已存在，临时探针通过；新增持久用例预计直接通过，不制造红灯、不冒称新功能TDD。若实际复现产品缺陷，先记录原因及最小修复范围。

### 8.1 子实例SQL过期版本

输入：真实ProjectStore和已保存子实例记录；A拿到旧running记录，B通过SubagentRepo.save_subagent推进version、状态/任务、checkpoint及拥有者字段。A再保存或撤销占用。

真实链：SubagentRepo.save_subagent/cancel_subagent_claim → Database.transaction(write=True) → subagent_storage.save/cancel_claim → 带session_id/agent_id/version及必要状态条件的SQL → rowcount不符抛SessionPersistenceError('subagent_record_changed') → Database回滚并释放事务。检查完整新记录不变，不能只断言异常或上层busy。覆盖旧版本保存、初次占用的旧撤销、续跑占用的旧撤销三条分支。随后以正确版本更新成功，验证事务可继续使用。用真实顺序A旧读/B提交/A旧写，不引入线程和时间窗口；不宣称这覆盖全部多进程竞争。

### 8.2 个人记忆来源失败和写入回滚

输入：复用现有storage_memory/memory、source、proposal构造真实项目来源及一条已提交的个人记忆。通过MemoryAccess.query提交既有MemoryInput请求；source在项目Database，target为独立data_dir/personal/memory.sqlite，断言两个Database/连接/路径不同。

正常链：MemoryAccess.query → MemoryRepo.mutate_with_sources → 项目读事务 → 个人写事务 → memory_mutations.commit验证来源 → 写正文、FTS、版本、来源 → 个人提交、项目读事务结束。

失败链一：在临时项目库中明确推进来源message revision或conversation revision，保留原引用；record/update均须抛准确memory_source_changed。个人正文/版本/来源/搜索/抑制内容与失败前一致，项目归档保持推进后的真实值且记忆请求对项目库零写入；用新引用重试成功。

失败链二：个人库建立临时SQL触发器，在插入naiyue_memory_versions时报错，此时正文/FTS已执行。record/update均须抛准确memory_write_failed，真实目标total_changes和底层IntegrityError证明到达中途故障；所有个人投影与原数据回滚，重开个人库后仍一致。原项目档案不改，两个事务结束；错误日志不泄漏故障内容。移除故障后同请求写入成功并可重开读取，验证不是永久拒绝或只清理内存。仅个人库原子性，不作跨库原子提交或跨项目多进程并发保证。

### 8.3 文件、重复与验证

只扩展tests/test_database_uow.py、tests/test_memory_lifecycle_storage.py，新增三种子实例情形及record/update×两种来源版本、record/update×版本写入故障，共9项。保留已有测试。个人表内容在失败前、失败后及重开后要重复比较，在本文件增加一个memory_rows快照函数，返回固定SQL读取的五组真实投影；这是本次多处断言复用，不修改conftest、已有fixture或其他测试基础设施。

本次基线为build/spec128-05-regressions/baseline，保护此前所有未提交改动。先执行现有两文件，再跑新增与周边仓储/记忆用例、全后端；前端和标准本机构建验收按既有工程命令执行。若生产/脚本/前端/构建输入与原7最终候选字节相同，可用字节核验复用其安装证据，必须明确它是先前已验证候选，不冒称本轮重新安装。检查本轮只有两测试及当前验收/交接文档变化。下一步只说明原十片收束与worktree的独立安排，不自动提交或开始新功能。

## 9. 原第5片补测验收（2026-10-23）

本轮只追加两个既有测试文件：test_database_uow增加三种过期SQL CAS场景；test_memory_lifecycle_storage增加record/update×message/conversation revision四种来源失效、record/update×版本插入失败两种回滚场景。合计9项，首轮直接全部通过，没有已复现的生产缺陷，也没有伪造TDD红灯。原两文件9/17个已有函数定义的AST完整保持；没有改conftest、已有fixture、生产、脚本、配置、依赖、版本或用户库。新增文件内memory_rows只复用五组表内容的比较，非新测试框架。两个文件分别231/319行。

失败语义均经真实仓储验证：旧保存及两种旧撤销抛准确subagent_record_changed，完整新记录不变，正确版本继续提交；personal两种来源版本推进抛准确memory_source_changed，所有已有个人投影和项目档案不变，新来源可重试；版本INSERT触发IntegrityError并转为memory_write_failed，total_changes证明此前正文/FTS已执行，五组投影回滚，独立重开仍保持旧记录，撤销故障后正常提交并再次重开验证。两库事务都结束，项目库零记忆请求写入；日志不含临时故障内容。没有新增跨库原子性或多进程竞争承诺。

| 验证 | 实际命令与结果 |
| --- | --- |
| 实施前基线 | `.venv/bin/python -m pytest tests/test_database_uow.py tests/test_memory_lifecycle_storage.py -q --tb=short` → 45 passed in1.41s |
| 新增三函数/参数场景 | 两文件三个新增节点 → **9 passed in2.36s**，完整命令见verification.json |
| 邻近仓储/记忆/子实例 | 两文件加memory_lifecycle、memory_personal、subagent_continuation_persistence/boundaries、memory_api_persistence → **94 passed in6.37s** |
| 全后端 | `PATH="/Users/zmw/naiyue-test-agent/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short` → **4808 passed in342.03s**，exit0 |
| 前端 | `npm test -- --run` → 50文件423 passed in4.90s；`npm run build` → TS/Vite exit0，既有chunk提示保留 |
| 标准构建/Twine | `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec128-05-regressions/verified-dist`及Twine exit0 |
| 产物及安装边界 | 新wheel全部372个成员内容与原7已经全新安装验证的候选一致；242 Python、4前端资源及README与当前源一致。本轮不重复安装，复用已核对的原7安装报告，明确保留原wheel SHA与新候选SHA区分 |
| 结构/范围 | 851冻结文件无漂移；AST/500行、compileall、pip check和git diff --check通过；原已有测试定义未改，当前片仅两测试及四份验收/交接文档变化 |

完整证据为`build/spec128-05-regressions/verification.json`、artifact-verified.json及prior-installed-candidate-equivalence.json。新wheel SHA256为`ae1a5e16de7984492e72d7c7b352f082837c530aae9c4386c4adfcab41c5e450`；复用安装候选为原7的`655b1c87ca4efb21de4a67463eff8adf960643384d7190cf7dccded8fb6b1ff5`，文件内容一致不等于ZIP字节SHA相同。

本次逐项对账发现的原1/7实现遗漏和原5持久回归缺口均已分别验收，原十片按当前清单收束。仍是本机确定性测试和Mac ARM64构建证据，没有真实模型质量、其他平台实机或新增用户掌握证据；未提交/push/发布。唯一下一步说明重构整体交付与提交范围，确认后才执行Git动作；worktree生命周期作为独立功能重新按片讨论。
