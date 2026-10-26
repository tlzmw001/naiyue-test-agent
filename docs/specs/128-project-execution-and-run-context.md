# Spec128：项目身份、执行目录与运行上下文拆分

日期：2026-10-22；当前对账：2026-10-23。原约定为六项重构拆成十个分片；当前01、02、03、04、05A、05B、06是七个实际实施单元，不能用现有Spec编号替代原十片验收。01的归档外迁已随v0.4.0发布，其余本分支改动尚未提交或发布。

原十片审查曾发现原1公开恢复和原7 memory接口两处差距，以及原5两类持久回归缺口，历史对账保留在第7节。用户分别批准补齐后，原1/7已按第8/9节验收；原5现已按第10节增加9项真实SQLite回归，最终4808后端/423前端和标准构建通过，生产代码保持原7验收版本。按本次逐项对账清单，原十片已完成收束；实际worktree生命周期仍不在其中。

用户已同意第1、2项先定合同再分别实施；第3项目录搬迁等待memory/history提交后实施的约束保持。实际worktree管理仍在原十片之外。用户已批准第11节10笔提交及同名开发分支推送范围；本单元完成Git交付后，唯一下一开发事项为worktree另起Spec、逐片讨论，不提前开发。

## 1. 已确认的范围与更正

1. 第1项拆开项目身份和会话执行目录；本次只完成数据与消费者拆分，存量会话按 Local 迁移。实际创建、附着 worktree 留给既有 worktree 方案，不新增创建/导入/切换/回收入口。
2. 第2项按生命周期整理 context：整个 run 的身份、能力与观察引用类型化只读；模型调用与批次临时状态显式管理；单工具许可继续复制 context 后局部注入。
3. 删除“新增根配置”的建议。`AppSettings` 已经组合各领域配置。
4. 只有 `ControlObserver` 和 `ContextOperationObserver` 具有相似 outbox/flush；`TurnObserver` 不同。本片不统一观察状态机。
5. CLI 的拥有者事件循环桥、Web 的归属校验、幂等、错误映射及应用关闭等待保持。以后采用子 API 也不删除这些职责。
6. 第3项领域分包等 memory/history 提交后再做；第5项存储职责拆分先于第4项门面组合；第6项 mixin 另立独立切片。
7. 不修改实际配置、依赖、版本或用户数据，不提交、push、发布；测试数据库和目录仅使用临时夹具。

## 2. 原定十片与实际实施记录

2026-10-23更正：此前将六个架构问题、十个实施分片和后来的Spec编号混用。最初十片安排来自本会话“先列出所有分片，然后讲第一片”的完整回复。以下保留原编号；当前记录的代码落地和组合验证，不等于已按原十片分别确认、实施、收束。

| 原分片 | 原定内容 | 当前实施记录与偏差 |
| --- | --- | --- |
| 1 | 项目身份与执行目录拆分 | 128-01；公开CLI/Web恢复遗漏已按第8节补齐验收 |
| 2 | 按生命周期整理运行上下文 | 128-02 |
| 3 | 领域分包，CLI移出application | 128-03 |
| 4 | Database连接、锁、迁移、事务与关闭 | 与原5合入128-04，原片职责已按第7节核对，未伪造独立实施提交 |
| 5 | 聚合仓储与工作单元 | 与原4合入128-04，两类持久回归按第10节补齐验收 |
| 6 | 统一Application，持久化改组合 | 128-05A统一存储装配，128-05B完成唯一Application与继承移除 |
| 7 | 领域子API与CLI/Web接入，原承诺含app.memory | 128-05B原九个API接线；遗漏的memory已按128-07及第9节补齐验收 |
| 8 | ControlQueue、ApprovalBook与Service控制mixin | 与原9/10一起合入128-06；本轮已按原片核对，第7节 |
| 9 | SubagentRegistry与子实例管理组合 | 与原8/10一起合入128-06；本轮已按原片核对，第7节 |
| 10 | RunLifecycle与最终mixin清理 | 与原8/9一起合入128-06；本轮已按原片核对，第7节 |

后续不改写原编号，不为编号重新实现已有代码；先核对各原片的当前落地、真实接线、验证和未完成项。worktree仍是这十片之外的独立功能，不能在缺少分片对账时直接宣布进入。AppSettings整理与观察器outbox统一也不属于原十片。

## 3. 当前实施合同文件

- [128-01：项目与执行目录](128-01-project-execution-root.md)：数据归属、权限、恢复、子实例、目录失效与项目归档。
- [128-02：运行上下文](128-02-typed-run-context.md)：类型化依赖、调用临时状态、逐工具许可、根子与审批恢复。
- [128-03：领域分包与CLI](128-03-domain-packages-and-cli.md)：纯目录/导入/入口迁移，保留公开命令和原行为。
- [128-04：数据库、仓储与工作单元](128-04-database-repositories-and-uow.md)：存储机制/聚合/必要原子保存，保留CLI/Web协议和数据库格式。
- [128-05A：统一存储装配](128-05a-unified-storage-assembly.md)：两种SQLite profile复用业务链，保留临时项目能力隔离和必要保存。
- [128-05B：应用组合与领域子API](128-05b-composed-application-and-domain-apis.md)：唯一Application和共享生命周期，保留CLI派发及Web协议职责。
- [128-06：会话协作对象](128-06-session-collaborators.md)：四协作者共享唯一state/run/锁，删除会话与Service mixin，保留审批/控制/保存语义。
- [128-07：原第7片memory接口补齐](128-07-memory-api.md)：同一MemoryAPI承接运行中的记忆读写和整理，保留来源共享锁、目录/身份/许可及生命周期。

各片分别 TDD、迁移消费者、验证，不能在一次无法区分原因的变更里同时搬包、重写存储门面、改锁和改变运行行为。类型放在允许上下层共享的现有边界；运行层不导入 Application、数据库或 UI。

## 4. 设计基线事实与数据流（01/02实施前）

本节保留2026-10-22重构前的事实；当前实现与入口以01、02、03的实际主链及验收章节为准。

现有 `WorkspaceLocation` 已有 UUID/root/database_path；数据库在 data_dir 下，绑定仍限制单个 root。session、history 与 memory 来源校验仍把 root 用作项目范围。工具、审批、Shell cwd、项目指令/Skills 同样消费该 root。

```text
prepare_workspace(UUID, root, database)
  -> SessionStorage.bind_workspace(UUID, root)
  -> SessionState.workspace_root
  -> SessionExecution / RunObservation.bind(RunServices)
  -> make_invocation_context(扁平字典)
  -> 模型准备 / 许可 / 批次 / 工具
  -> session/history/memory 查询或必要保存
```

当前上下文除依赖外还保存模型轮次、批次计划和逐工具许可，不能把所有字段一起变成共享 RunScope。

最新读取时并行memory概览已使用项目schema17；16属于memory写入与来源时间。个人记忆为format2。128-01迁移必须从实施时实际版本顺延，不预占版本号或覆盖并行memory改动。个人记忆的检索、版本、遗忘抑制和格式不因本片词汇变化重做。临时`--no-session`不创建项目身份文件，也不开放项目history/memory；其归档按临时session_id置于data_dir。

## 5. 用户确认的执行目录失效合同

错误码统一为 `execution_root_unavailable`，原因仅使用本片已确认的枚举：

| 原因 | 条件 |
| --- | --- |
| missing | 路径不存在 |
| not_directory | 路径存在但不是目录 |
| not_registered | worktree 不在 Git worktree list 中，或没有应用登记记录 |
| wrong_project | Local 身份文件 UUID 与 project_id 不符，或受管 worktree 登记不属于项目 |

只在新的执行入口检查，不定时扫描、不监听文件系统。不把 Path.is_relative_to、同分支或同 remote 当项目身份。首片不创建 worktree，未登记的 worktree 不能靠传入路径成为受管目录；未来登记与 Git 校验的生产接线按 worktree Spec 验收。

目录失效后：新 run、Shell/后台新命令、文件读写、子实例续跑拒绝；历史档案仍可打开。已经运行的后台进程保持现有生命周期，list/output/stop/aclose 不依赖执行目录有效。任何入口都不得改用主目录或进程当前目录。

用户随后明确：应用归档迁出执行目录，放到项目拥有的数据位置。这样删除执行目录不影响后台输出；磁盘写入等真实归档故障继续沿现有停止/反馈合同，不引入忽略归档错误的特例。

## 6. 验收证据的区分

本节以下保留01实施前的证据边界说明；当前实现与验收以顶部状态和各片记录为准。

01 的归档迁出已有新红灯和专项转绿，结果按其验收记录单列；身份迁移与02仍只有合同。前一轮120项定向测试与 typed-context/Git 临时探针只证明当时现状和框架可行性，不能充当128实施验收。

实施后分别交付：当前正常/失败调用链、测试红灯与转绿、完整回归和标准构建结果、旧消费者清理清单、公开字段/持久格式映射、未实现的 worktree 功能边界。助手验证不等于用户已独立实现或调试。

## 7. 原十片逐项对账（2026-10-23，本轮只读检查）

依据是本会话最初十片计划、批准前的具体说明、当前代码和实际运行证据。后写的Spec不能自行取消原先承诺。功能已接通、接口遗漏和测试证据不足分别记录；合并实施带来的流程偏差仍保留，不为编号重新实现已有功能。

| 原片 | 当前真实生产链与清理结果 | 本轮测试核对与结论 |
| --- | --- | --- |
| 1 | `Project/ExecutionRoot → SessionState → RunIdentity`；项目history/memory用project_id，文件/审批/Shell/AGENTS/Skills用执行目录；子实例冻结自己的目录，归档在data_dir；绝对路径忙碌键保持 | 迁移/备份、三入口失效、读写/审批、既有后台输出、个人跨项目及临时隔离用例通过。**未完全兑现**：应用层重开可读历史，公开CLI/Web却先严格解析已消失的Local根，读库前退出；见下文 |
| 2 | 冻结`RunScope(identity/capabilities/observation)`；`InvocationState/ModelCallState`每次重建；`ToolAuthorization`复制后局部注入；唯一ContextVar窄桥；删除RunServices/扁平键 | `test_run_context_contract.py`、`test_run_context_integration.py`及根/子/审批集成覆盖类型、隔离、reset、无publisher身份匹配和逐工具许可。未查到确定实现遗漏 |
| 3 | application按sessions/conversation/subagents/observability/memory分包；顶层storage/cli与web平级；仅保留有公开消费者的application.cli启动别名 | `test_domain_packages.py`的16项验证冷导入、唯一实现路径、五种公开命令、领域不反向依赖适配器。未查到确定实现遗漏 |
| 4 | Database持有应用连接/锁/迁移/事务/close；`open_storage`另持aiosqlite连接交官方Saver，应用事务不包含该连接已提交的checkpoint | 迁移DDL失败/重试、BEGIN/COMMIT失败回滚、嵌套拒绝、持锁close、备份还原、取消清理验证通过。未查到确定实现遗漏；128-04的from_conn_string描述是历史时点 |
| 5 | ProjectStore组合七个聚合仓储和RunUnitOfWork；必要finish/delete进同一应用事务；项目/个人共用MemoryRepo，个人使用独立Database；删除旧SessionStorage和PersonalMemory所有者 | 中途SQL失败后四组事实回滚、重生成/删除/父run确认失败、个人格式/CAS/来源验证通过。未查到确定实现遗漏；**直接子实例过期CAS、个人来源/中途失败的持久回归仍有缺口**，见下文 |
| 6 | 两工厂进入同一assembly，RAM/磁盘复用ProjectStore、Saver、SessionPersistence与UOW；唯一Application组合共享Lifetime，不再继承内存/持久门面 | RAM自身历史/重新生成、必要保存失败、磁盘重开、跨API关闭及首异常通过；临时不建身份、不注入项目history/memory边界保持。未查到确定实现遗漏 |
| 7 | 九个API：sessions/runs/history/tasks/observability/models/tools/subagents/mcp；CLI保留owner-loop桥，Web保留认证/归属/幂等/错误映射；旧平铺门面清理 | 真实CLI、HTTP认证/回执、MCP刷新、任务、动态注册、Skills与子实例历史通过。**未完全兑现**：原承诺的app.memory属性不存在；现有memory工具/整理功能仍接通，不是记忆功能丢失 |
| 8 | AgentSession组合ControlQueue和ApprovalBook；二者只借用同一个SessionRuntime；Service控制mixin删除，实际端口转到拥有者 | 两会话隔离、同ID旧run拒绝、队列优先级、审批批次/未来对象、停止和锁外观察、拒绝不发接受事件通过。未查到确定实现遗漏 |
| 9 | SubagentRegistry管理实例/driver/审批收束；records接显式runtime/catalog/repos；保存目录、checkpoint/CAS与父run确认保留 | 继续/重开/崩溃/取消、权限、配置变化、独立审批、外来实例拒绝和父保存失败不ready通过。未查到确定实现遗漏；SQL过期CAS直接回归计入原5缺口 |
| 10 | RunLifecycle负责admit/执行/失败/子收束/必要保存/release/终局；四会话及两个Service mixin已删；AgentSession保留已说明的15个配置/快照入口及三把配置提交锁 | 根审批→子审批→必要保存集成五种终局、保存前保持busy、取消/关闭、旧run不清理新run、压缩重开通过。未查到确定实现遗漏，不等于AgentSession只剩两个字段 |

### 7.1 两处合同差距

1. **原1：公开恢复入口仍依赖执行目录存在。** `cli/chat.py:169`先调用`resolve_workspace_root`，再prepare_workspace、打开库和选会话。因此Local执行目录删除/移走后，即便显式指定仍存在的`--session-db`和已保存的`--resume`，也在读取历史前初始化失败。本轮临时目录探针保存一轮、关闭、移走执行目录后得到退出码1，库仍存在。现有`test_missing_execution_root_keeps_history_but_blocks_new_run`重开时不传workspace_binding，证明的是Application层能力；不能作为公开CLI恢复已满足合同的证据。公开Web启动同样先prepare_workspace，规范Local根消失时退出1；即使直接构造WebSettings也严格解析配置的根；服务已启动后目录失效的HTTP用例则已覆盖。本次不据此声称尚未实现的真实worktree丢失恢复已验收或有回归，也不改变“不得回退目录”的要求。
2. **原7：app.memory承诺未落地。** 批准前曾明确列“现有记忆查询、写入和整理入口，保留调用身份与权限校验”。当前Application只组装九个API，访问memory得到AttributeError；现有子API合同测试也只列这九个，没有覆盖原memory接口承诺。当前真实链为`memory工具 → runtime.context.scope.capabilities.memory → SessionPersistence.aquery_memory → MemoryAccess/MemoryJobs`，任务管理走tasks；不是功能不可用。05B的Spec和实施后说明“不新增记忆管理API”没有明确取消此前接口承诺，不能作为已获确认的范围缩减。本轮保留为接口遗漏／未确认设计调整，不补空壳或自动新增CRUD。Skills归sessions、子实例历史归history有真实消费者，属于职责归位，没有功能遗漏。

### 7.2 测试证据缺口

- 子实例SQL CAS有rowcount检查，但当前没有直接断言过期版本产生`subagent_record_changed`的持久测试；上层并发busy测试不能替代数据库这一分支。
- 个人记忆来源失效及写入版本SQL中途失败，本轮临时探针均通过并验证个人表不落地、项目档案不变；仓库持久回归主要验证workspace分支，应补personal分支。两owner个人CAS测试共享项目源锁，不独自证明不同项目进程的并发场景。

这两项是覆盖不足，不是已复现产品bug。跨表原子性未丢失；`ReplyRepo.activate`和`SessionRepo.bind_workspace`仍拥有其真实事务，不把“所有跨表业务都已在RunUnitOfWork”作为完成结论。

### 7.3 本轮验证与证据边界

- 全后端：`PATH="/Users/zmw/naiyue-test-agent/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short`，**4745 passed in343.34s**。
- 前端：`npm test -- --run`，**50文件423 passed**；`npm run build`通过，保留既有chunk大小警告。
- 原3/8/9/10共15个文件专项：**139 passed in14.08s**；原4/5专项**125 passed**；原6/7专项**57 passed**。专项与全量重叠，不相加成新增测试数。原1/2专项**140 passed**，公共入口探针确认库级成功/CLI与Web失败。
- 842份生产/测试/脚本/前端源和README/pyproject/AGENTS冻结对比无新增、删除或字节漂移。240份生产Python均可AST解析且不超500行；git diff --check通过。当前240份Python、4份新构建前端和README与06已验证wheel一致；本轮没有重建wheel或重跑安装，不把06历史安装结果说成本轮结果。
- 详细命令、原十片代码/测试索引、探针与结果置于`build/spec128-ten-slice-audit/`；检查中一次专项使用不存在的文件名未收集测试，修正文件清单后的139项通过，原日志保留。0真实供应商请求，无用户库/环境/依赖/版本修改，未提交/push/发布。

本轮只更新对账与交接记录。先就原1公开历史恢复、原7接口合同和原5失败回归收束，再把原十片标记全部完成并讨论实际worktree；不把临时探针冒充已加入仓库的回归用例。

## 8. 原1公开恢复遗漏已补齐（2026-10-23）

用户在第7节审查及公开恢复方案说明后批准实施。当前主链、失败分支、清理与验证见[128-01第13—14节](128-01-project-execution-root.md#14-公开恢复实际主链与验收2026-10-23)。明确session-db读取既有Project，正常绑定与已有身份核验分离；两个SQLite连接只打开现存库，正式所有权后重核。CLI按执行目录错误展示历史，Web保留项目归属读取；新执行不放宽、不改目录、不重绑。正常创建/搬迁、空resume、临时边界保持。

新增33回归，最终61专项、4778后端、423前端、TS/Vite、标准Mac ARM64 wheel/Twine/源码外安装通过；846源无漂移，241Python＋4前端/README字节一致，0供应商，未提交/push/发布。初轮失败与当前结果分别保存在build/spec128-01-recovery。第7节的两处差距是审查时点，不再代表原1仍未修复。

本节收束时原7 app.memory接口及原5两类持久回归仍未实施；原7随后经用户批准补齐，当前见第9节。原5仍待下一片确认，不进入worktree。

## 9. 原7 app.memory遗漏已补齐（2026-10-23）

用户批准后按[128-07合同/真实主链/验收](128-07-memory-api.md)补齐。Application创建共享Lifetime的MemoryAPI，SessionService传给新建与恢复会话；根消息、根审批、子Agent及其审批的能力均绑定同一query，MemoryQuery显式传RunScope。工具保留逐调用许可，API在共享状态锁内核对当前run/role/publisher/Project/ExecutionRoot，随后验证执行目录和既有MemoryInput。写操作持有原history_lock依次await来源保存和MemoryAccess.query；refresh仍只登记，根必要保存成功后释放，任务管理继续走tasks。

删除SessionPersistence.aquery_memory、bind_memory_agent及旧资源字段；保留实际来源保存、共享锁、提示准备和最终任务释放。新增21项，最终26专项、**4799后端/423前端**、TS/Vite、标准Mac ARM64 wheel/Twine/源码外全新安装通过；242Python＋4前端/README字节一致，850源无漂移，0供应商，未提交/push/发布。首轮1 failed/4798 passed暴露asynccontextmanager违反原直接async结构约束，最小修源为直接锁区间，原结构测试字节未改；最终全量和包重新验收，证据`build/spec128-07-memory-api/verification.json`。

第7节为审查时点，原1/7两处实现遗漏均已分别验收；本节收束时原5测试仍未补，随后经用户批准按第10节补齐。不把临时探针算仓库回归，不提前开发worktree。

## 10. 原5两类持久回归已补齐（2026-10-23）

合同及真实调用链见[128-04第8—9节](128-04-database-repositories-and-uow.md#8-原第5片持久回归补齐合同2026-10-23)。只扩展两个现有测试文件，新增三种子实例SQL过期保存/撤销、四种personal来源失效和两种personal中途写入回滚，共9项。精确错误码、完整新记录/已有投影/项目档案不变、事务结束和正常重试/独立重开均验证。新用例首轮直接通过，未发现需修生产的缺陷，不伪造红灯；已有函数AST和生产/脚本/前端/构建输入保持原样。

本轮9新增、94邻近专项、**4808 passed in342.03s**全后端、50文件423前端、TS/Vite、标准Mac ARM64构建/Twine通过。851冻结源无漂移，新wheel全部372成员内容与原7已全新安装的候选一致；本轮明确复用原安装报告而非声称重装。证据`build/spec128-05-regressions/verification.json`及产物/安装等价JSON。未提交/push/发布，无新增用户掌握证据。

原十片对账发现的遗漏与测试缺口现已分别闭合；这不包含worktree创建、登记、附着、切换、合并或回收，也不新增观察器统一工作。唯一下一步说明重构整体交付与提交范围，确认后再执行Git；稳定提交后继续单独讨论worktree分片。

## 11. Git交付编排（2026-10-23，已确认）

用户已确认本方案并批准创建10笔提交、推送origin同名开发分支。交付基线v0.4.0对应0ba38f30b8ead7aa7614de34492eb81a856ae5fa；计划准备时HEAD等于该标签、暂存为空。排除本机.venv链接后，最终差异628路径：427修改、67删除、134新增；包括生产230、测试352、脚本28、前端3、文档14、README1。被忽略构建产物不入清单，最终源码和工作目录保留。

下表C1—C10是按真实源码依赖组织、已获确认的Git提交节点，不是重新编号原十片。7个实际实施节点和3个补齐节点均可从保存的冻结清单定位源码。

| Git节点 | 对应原片 | 交付内容 |
| --- | --- | --- |
| C1 | 1 | Project/ExecutionRoot及身份、权限、工具、历史/记忆和归档消费者 |
| C2 | 2 | RunScope/InvocationState/单工具许可及根/子/审批上下文迁移 |
| C3 | 3 | 领域分包、CLI并列适配器及全链路导入/公开启动入口 |
| C4 | 4、5 | Database、仓储、RunUnitOfWork和个人库，删除旧资源所有者 |
| C5 | 6 | 磁盘/RAM统一装配和临时项目能力边界 |
| C6 | 6、7 | 唯一Application、共享Lifetime、领域API和CLI/Web接线 |
| C7 | 8、9、10 | 四个会话协作者，清理mixin并保留控制/审批/必要保存顺序 |
| C8 | 1补齐 | 公开CLI/Web在执行目录失效后仍可打开项目历史 |
| C9 | 7补齐 | 真实app.memory及根/子/审批能力迁移 |
| C10 | 5补齐及最终对账 | 9项存储回归和原十片最终验收/交接 |

源码节点逐份核对SHA256，10套清单均能定位匹配的保存文件或wheel README元数据，逐套AST/生产500行检查通过，最终源码和构建元数据等于当前版本。同一文件可能跨多个节点，不能把最终目录直接分组暂存；按本轮确认用独立临时索引/树组装，保持最终工作目录不回退、不覆盖。每个实际暂存树和文档版本均在创建提交前核验，最终树与当前文件逐项比较。完整提交树的格式检查包含此前未跟踪的新文件：5份冻结源存在末尾空行，分别为application/catalogs.py、application/subagents/subagent_records.py和test_existing_project_open.py、test_history_recovery_boundaries.py、test_public_history_recovery.py。按原字节保留并在full-tree-whitespace.json记录，不将默认格式检查报告为全绿；这不是功能测试失败，也未放宽测试断言。

早期未保存完整文档历史，因此本轮没有声称精确还原十套完整历史Git树。对应Spec随功能节点，优先使用保存的Markdown；缺失的早期设计版本依据已批准合同整理，明确为本次交付编排，不放入未来节点已完成的描述。最终handoff/project-plan和十片验收统一放C10。完整文档归属和规则保存于documentation-routing.json。

交付历史另含3个最终已不存在的中间路径：application/execution_storage.py在C1新增、C3迁走；application/memory/memory_personal.py和storage/session_storage.py在C3迁入、C4由新资源所有者替换。它们仅用于中间历史；最终源码仍与当前冻结版本一致。最终清单628路径，历史涉及631个不同路径。

本地目标为当前codex分支，用户已批准推送到origin同名分支，仓库github.com/tlzmw001/naiyue-test-agent.git。推送前已确认没有同名远端分支；推送采用普通快进规则并建立上游，实际结果以Git为准，避免覆盖他人变化；main合并、版本/标签/发布及worktree产品功能不属于此方案。

可审阅产物集中在build/spec128-delivery-plan：commit-plan.md/json为方案和建议提交标题，commit-files.tsv/json为最终完整白名单，commit-source-checkpoints.json和C1—C10-source-state.json为节点变化，checkpoint-sources.json记录源码来源及哈希，source-checkpoint-proof.json和verification.json记录当前检查。它们是本机审阅证据，不作为构建产物提交。

本轮重新运行最终版本完整后端：4808 passed in352.10s；前端50文件423 passed，TypeScript/Vite和标准Mac ARM64 wheel构建通过，日志保存在本机证据目录。851冻结源保持一致；中间节点复用已记录的逐份源码哈希和历史验收，不声称本轮重新完整运行十套后端。新构建包另做源码/资源与README一致性检查，真实供应商、其他平台原机和发布不在本轮。用户已明确授权这10笔提交及同名开发分支推送，main合并、标签、发布未获本轮授权；实际提交与远端结果记录到commit-execution.json及最终回复，不在自身提交里预写未知哈希或成功。
