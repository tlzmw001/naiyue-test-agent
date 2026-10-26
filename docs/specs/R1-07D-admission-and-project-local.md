# R1-07D：跨项目执行准入、本机绑定与旧装配链清理

状态：2026-10-24 三项修复及补充约束已完成实现与本机验收；基于 07C 当前工作树。未提交、推送、合并或发布。此片实现已确认的 project_local 语义，不提前实现 R1-08 的文件后端或 Docker。

## 1. 当前链路与问题

RunAPI → SessionService.arun → 同步 _admit → validate_execution_root → RegisteredExecution.validate → ProjectService.get。get 和 create/add 共用 ProjectService 的 RLock；创建在锁内做 git init 和建库，事件循环可被另一项目的创建阻塞。工具重复走 get，还计算无关的展示信息。一次性 Event 探针已复现：B 初始化受阻时 A 的准入阻塞 owner loop，释放 B 后才恢复心跳。

注册项目旧会话/子实例冻结历史路径；重新定位后仍按旧位置校验，无法继续。原 R1-03/04 的此项合同由用户本轮明确替换。旧 Application 工厂另有默认宿主认领，修改 SessionService/actor 私有字段；正式服务已经 default=None，旧消费者在测试/脚本。

## 2. 注册事实与准入

注册表 user_version 升级为 2，projects 增加单调 revision（初始 1）。项目名称、来源/位置、信任、生命周期实际变化时，在同一事务中递增；重复已完成请求不递增。序列化 RegisteredProject 同步携带 revision。

ProjectRegistry.execution_facts(project_id) 只读项目执行所需列（ID、lifecycle、trusted、source_kind、规范 root/path_key、revision）；使用已有 Database.read_rows 的短锁，不经 ProjectService._lock，不做 _view/全项目枚举。注册表初始化与关闭仍受资源所有者管理，不能用轻量入口绕过关闭或自动重建库。

运行准入先在线程中解析/校验目录与注册事实，再核对 revision。返回 owner loop 后核对宿主/根应用没有开始关闭、actor/会话身份仍匹配，再做容量和 active-run 原子状态转换；状态转换不在线程中做。停止待审操作不要求失效目录恢复。常规新消息、压缩、审批恢复、重新生成均检查实际入口，不能保留另一条同步 IO 准入主链。文件/Shell/子实例检查使用相同轻量接口；每次工具操作仍校验本次 run 已冻结的目标和 revision。

正常：B 创建停在 git Event → A 线程读取短事务事实 → owner loop 仍处理 HTTP/事件 → A 准入并运行。失败：校验完成后宿主开始关闭，或注册 revision 改变 → 明确拒绝，不产生模型/工具执行或新的 active run。不保证检查后文件系统永远不变，工具 IO 保留原校验。

## 3. project_local 数据与恢复

项目库 schema 升级为 19：会话、子实例新增明确的 project_local 绑定类型及 last_execution_path。历史路径只供展示/审计，即使为空也能从可信项目绑定执行；旧物理路径列暂作审计镜像，不从它或 last_execution_path 还原 project_local 执行根。SessionState 独立保存 execution_binding。注册项目实际执行目标由可信注册记录解析，普通对话没有绑定，临时 Local 保持原无登记合同。schema 19 首先把明确的旧类型映射为 local；只有已取得执行 authority 的 assemble_host 才把本库同 UUID 的 local 行升级为 project_local，不覆盖未知绑定。只读旧库入口不做语义重绑，原 UUID 错误保持。迁移保留 UUID/历史/检查点/审批/子实例版本，不把不可信来源猜为注册项目。

每次 run 准入解析当前位置并冻结到 RunScope.identity；本次 run/子调用只使用这个冻结对象。子实例续跑依据保存的绑定类型与 project_id 解析，不能借用其他项目或沙箱的目标。重新定位仍要求关闭宿主、expected_root 匹配、显式信任、位置不冲突；不会搬动已有进程，后台 list/output/stop 合同保持。

新位置上的首次 run，如果 last_execution_path 与当前目标不同，加入系统上下文提示“项目已从 X 移到 Y，之前对话中的路径已失效。”路径正确转义。提示不能替代权限检查。必要保存成功后才更新历史路径，避免失败/取消丢掉提示；旧绝对路径仍按真实权限规则处理，不能宣称目录外一律拒绝。

审计路径仅在 ready/completed 的必要保存事务中更新，和终局、聊天、回复、子实例一起提交；事务失败全部回滚，未开始模型就停止时不消费迁移提示。恢复会话允许目录失效，历史仍可打开；正式准入重新解析并核验，不使用展示缓存兜底。

消费者核对包含未加载会话的 history/context 只读预览：先解析当前位置，再发现 AGENTS/Skills，不把审计路径交给资源发现。MemoryJobs 的注入、手动刷新与自动任务范围对 project_local 使用可信宿主装配时设置的 execution_location，不再和旧行路径比较或从它派生任务目录；宿主位置变更仍要求先关闭，实际 memory 工具继续校验 RunScope 的执行根和库 UUID。legacy Local 保留原路径比较，缺少可信本机位置则拒绝，不放宽个人记忆跨项目读取合同。

审批 subject 除 workspace 外包含本次注册 revision。恢复时 revision 不匹配视为审批过期，不执行旧许可；保留可读待审状态，允许停止/取消后重新发起调用审查。根/子审批及恢复后的调用均覆盖；同路径改名也能触发过期，不能只靠路径字符串或 ABA 位置变化。

## 4. 唯一装配链

删除 HostManager.default 认领、owned-store 默认宿主复用，以及已被正式服务替代的 async_application/async_persistent/assembly 工厂与 Application 默认领域别名。产品、协议集成、探针与 release smoke 使用 open_service_application → projects.open/chats.open/temporary.open 的显式入口。资源创建集中复用 assemble_host；不在新入口复制旧认领代码。

旧测试按职责迁移：产品/协议与注册集成走真实服务；低层 SessionService/存储/图的组件测试可复用现有 assemble_host 的明确 fixture，标明它不证明项目注册协议。共享 fixture 仅管理测试资源，不在生产保留兼容工厂或认领分支。现有顺序、配对、异常身份、权限、检查点、来源与 CAS 断言保留；仅替换已废弃行为的断言，使用新的正反例补足。

release_storage_smoke.py 逐项保留：旧 schema 升级与迁移备份、显式备份/还原、恢复前只读事实对比、history/usage/子实例/待审恢复、恢复不重放模型或工具、审批后恰好一次写入、CLI 入口、拒绝分支。输入固定模型，0 供应商；所有存储操作只在一次性目录。

## 5. 影响与验收

影响：contracts/projects、execution_roots、注册表格式/仓储、项目宿主装配/管理、会话准入/运行/恢复/状态、审批绑定、子实例、schema/备份验证/行映射、旧工厂消费者、release smoke、HTTP/前端错误映射及当前文档。workspace_root 外部字段与未授权五项命名清理不整体更名。

先红后绿：轻量读取不等写锁；B Event 阻塞时 A 能 run、HTTP 响应；校验后关闭/修订变化拒绝；工具用轻量读取；已有 schema/注册表迁移与幂等 revision；搬目录后旧会话/子实例在 Y 继续、旧路径仅审计；首次提示及保存失败重试；旧待审修订过期/停止可用；缺失目录不回退；新工厂接线与无旧导入；存储 smoke 覆盖不减少。聚焦回归后冻结输入，完整后端/前端、TS/Vite、标准 wheel/Twine/源码外新环境安装及包内容一致性验证。

## 6. 可追踪的当前主链

正常运行：ProjectAPI.open → HostManager.open → assemble_host（唯一图/仓储装配，受信迁移旧绑定）→ host.sessions.restore/create → host.runs.start → RunAPI._execute → SessionService.prepare_admission 在线程解析 project_local 的事实与目录 → confirm_admission 在线程再核对 revision → owner.check_admission_open → _admit 只做 actor/快照/容量原子状态变化 → RunLifecycle.admit_run 把当前位置冻结为 ActiveRun.execution_root → RunScope.identity → 文件/进程工具逐次 validate 走 execution_facts → 必要 finish_run 事务完成后确认审计路径。

关闭竞争：prepare/confirm 的 await 期间宿主开始关闭 → check_admission_open/_closing 明确拒绝，无 active run/模型/工具。项目 B 的 git init 卡在 Event 时，项目 A 的轻量读取只等 Database 的查询短锁，不经过 B 的 ProjectService 写锁；真实 ASGI 握手和 SSE 返回仍可完成。

重定位与审批：关闭 Host → ProjectAPI.relocate（expected_root、信任、冲突和 revision）→ 同 project_id 重开 → 旧会话从注册表解析 Y → 首轮提示旧 X 已失效。子实例从自己的 project_local＋project_id 与父 run 冻结绑定核对，保留 child_thread/checkpoint/版本与 CAS；审计路径可空。旧审批 subject 的 revision 不同则抛 SessionApprovalExpiredError，保留原待审事实供取消，禁止使用旧许可；取消不依赖目录可用。

资源与旧入口：根 Application 只提供 models/projects/chats/temporary；ProjectHost 提供会话领域 API，关闭不拥有模型。组件 fixture 位于 scripts，不进入安装包；它借正式根应用资源和同一 assemble_host，不再模拟默认宿主认领。真实多项目/协议测试与所有 release 业务 smoke 使用正式服务/明确宿主。release Web 故障探针仍使用固定宿主 fixture，不作为多项目协议证据。

## 7. 验证记录

实施中日志位于 build/r1-07d/，原始红灯与迁移诊断保留。完整后端按 collect-only 清单划为两批独立文件集合，校验无交集且并集为全部 464 个文件、5097 项；两批都必须退出 0 且通过项合计一致，不跳过失败用例。最终两批后端分别 **2549/248.09s、2548/176.88s**，均退出 0，合计 **5097 项**；前端 **56 文件/459 项**、TypeScript/Vite、标准 Mac ARM64 wheel/Twine/源码外干净安装全部通过。295 Python/4 Web 资源与候选一致，976 冻结输入无漂移；改动产品文件最大 483 行。安装包恢复阶段 model/write/auto 均为 0，批准写入恰好 1 次，0 供应商请求。完整命令、退出码与清单见 `build/r1-07d/verification.json`，安装报告 `install-smoke-complete.json`；最终候选位于 `wheel-complete/`。单批早期停止、替身遗漏及主动中断日志仅作过程证据，不计为通过。范围外仍包括 worktree、旧命名全量更名、Docker/远端执行、R1-08 文件后端，以及真实供应商/其他操作系统验证。
