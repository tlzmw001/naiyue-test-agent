# Spec128-01：Project、ExecutionRoot 与项目归档

交付编排说明：本节点从已保存合同和验收文档整理，移除了后续目录迁移注记；不是原始完整文档历史的精确快照。

日期：2026-10-22。状态：2026-10-23，01身份/消费者拆分完成本机验收，尚未提交；从v0.4.0（0ba38f3）建立独立分支，归档外迁沿已发布基线。用户已确认本片范围、目录失效与归档迁出方向。总约束见[Spec128](128-project-execution-and-run-context.md)。

## 1. 目标与本片边界

项目持有稳定身份、历史与记忆，执行目录决定本会话在哪里操作文件和启动命令。存量会话迁移为 Local；本片不创建、附着、切换、交付或回收 worktree，不扩为多项目 Web 服务。

继续复用现有 workspace UUID。Project 不因分支变化而改变，也不按 remote URL 合并。ExecutionRoot 的目录存在不等于属于项目；项目归属不等于任何工具操作已获许可。

应用生成的 Shell/后台/MCP 归档迁出执行目录。用户命令自己写入的报告、代码、依赖与构建缓存不在迁移范围。

## 2. 数据与权威来源

| 对象 | 权威数据与职责 |
| --- | --- |
| Project | 现有 UUID 作为 project_id；canonical_root 用于项目定位；database_path/data_dir 由可信应用配置派生，不进入模型可填参数 |
| Session | 保存 project_id 与自己的 ExecutionRoot；thread、会话状态和权限仍由当前 owner 持有 |
| ExecutionRoot | 实际规范路径和 local/worktree 类型；worktree 归属须来自应用登记。分支可变且可为空，不作为权限身份 |
| Run | 从当前会话或子实例取得并冻结本次执行范围，不从工具参数或模型文字选择目录 |
| 可续跑子实例 | 首次创建时保存执行目录；续跑以自身记录为准，不用父会话当前目录覆盖 |
| 后台任务 | 冻结的 ProcessRequest.cwd 与创建时执行范围；归档位置另行保存，任务管理不通过目录存在性决定是否允许停止 |
| 项目归档 | 从可信 data_dir/project/session/运行归属派生；不从当前 cwd 推导，不允许模型填写任意库/归档路径 |

业务内部明确 Project 与 ExecutionRoot。持久格式的 `workspace_id` 等 UUID 字段、公开 HTTP 的 `workspace_root` 或 CLI `--workspace`，若仍有实际消费者，可以作为边界映射保留，不能继续承担多种内部语义，也不能为旧内部调用保留平行实现。

`--no-session` 已由用户明确要求不创建身份文件，也不开放项目history/memory。临时会话的project_id为空，执行目录只做存在性/目录检查，不冒充有项目身份的Local；归档按可信session_id隔离，聊天仍不持久化。无目录会话继续支持聊天，不开放项目能力。

未绑定库保留自己的会话原文，但空project_id不能作为跨会话查询范围。现有非项目调用者包括build_async_local_application、直接SessionService及测试/安装探针；持久入口open_async_persistent_application仅在提供真实workspace_binding时关联项目，旧库迁移不访问或创建旧目录身份文件。显式项目绑定仍需核对原库/目录，不能把不相关旧会话批量吸收到同一项目。

## 3. 执行目录校验

使用统一错误码 `execution_root_unavailable`，附带 `missing / not_directory / not_registered / wrong_project` 原因。操作入口应能明确映射为工具失败或会话不可执行状态，不通过字符串路径回退。

校验顺序：路径不存在→missing；非目录→not_directory；再按类型核对身份。Local 的身份文件 UUID 必须等于 project_id。受管 worktree 必须在应用登记中属于该项目，且仍在 Git worktree list 中；未登记为not_registered，其他项目登记为wrong_project。Git事实是关系校验，不替代调用方授权。

本片只产生 Local 绑定。不能为尚未实现的创建/附着入口预建空服务或宣称 worktree 已可用；worktree 记录、Git核验的最终生产接线由后续功能片落实。类型未知或身份来源不可信时不能默认为 Local 成功。

“同路径删除后重建”不能只靠存在性视为身份保持；按已确认身份文件/登记规则重验。不以目录 inode 当跨平台永久身份，不新增文件系统监控。

| 入口 | 执行前动作 | 失效结果 |
| --- | --- | --- |
| 新用户消息 / 新 run | 准入时核对冻结执行范围 | 拒绝开始执行；会话显示执行目录不可用 |
| Shell 前台 / 后台 submit | 用当前已授权目录检查，不以archive_root作为cwd | 不创建进程 |
| Read / Ls / Find / Grep | 读操作开始前检查 | 不读主目录或其他目录；用户描述的Glob对应当前项目Find |
| Write / Edit | 写操作开始前检查 | 不创建、覆盖或修改目标 |
| 子 Agent 续跑 | 读取子实例保存范围并检查 | 不调用子模型或工具，保存原记录供查看 |
| 重启恢复 | 分开历史打开与执行恢复 | 历史可读，新执行不可开始 |
| 已有后台 list/output/stop | 保留任务身份和调用范围检查，不做目录有效性门禁 | 正常可用；stop按进程组，aclose保持原回收 |

这里只在新的执行入口检查一次，不承诺完全消除检查后外部修改目录的竞态。底层路径/句柄、目标摘要、审批和原子替换等原有校验仍保留，不重复增加轮询或另一套文件沙箱。

## 4. 权限、审批与文件忙碌键

ToolPermission/FilePermission 生成 subject 时，其 workspace 字段明确表示执行目录；`${workspace}` 和相对路径规则也以执行目录展开，保留现有配置语法。本片不添加 `${project}` 规则或扩大可访问范围。

一次许可继续绑定 session、agent、batch、call、args、subject 与执行环境。批准A目录的操作不能在B目录消费；同项目不允许继承另一个执行范围的session allow。运行层批准后，工具执行仍核对当前许可及真实目标。

Write/Edit共用 `_reserve_target`，现有键为规范化绝对目标路径（包含既有Unicode/大小写归一化）。保留该算法：不同执行目录的同名文件不争同一个键；两个会话操作同一实际目标仍竞争。禁止改成 project_id+相对路径或 session_id+路径。本片只增加必要执行根检查，不改Windows时间戳、Edit摘要、替换或清理机制。

## 5. history、memory 与来源

查询数据范围从“同路径”改为“同项目”，准入仍要求：

1. 当前可信session.project_id等于库里绑定UUID。
2. 当前根/子运行的执行目录来自其已登记身份，确属这个项目。
3. 运行、角色、活动工具、当前工具许可及参数仍有效。
4. 目标会话/来源记录属于该项目，且符合现有可见性与版本规则。

`history_queries` 的列表、目标选择与游标范围同步迁移；不能只改列表SQL。memory保留现有项目UUID过滤、个人库和来源验证。

memory03新增的 `memory_sources.resolve` 也有“来源session.root等于库root”检查，必须改成来源session.project_id验证。message/thread、revision、conversation_revision、quote区间、时间、用户来源与tool_success核验保持。来源执行目录已失效不影响读取已保存原文证据；不能把读取历史来源变成恢复旧会话运行。

并行memory04—06新增的memory_batches来源范围、memory_overview_store概览/目录、memory_jobs请求/收尾入队，以及memory_context注入查询也必须按project_id迁移。后台整理是项目存储操作，不因为历史来源执行目录消失而换目录执行；任务停止/查看仍保留归属检查。实施时逐项核对其最终主链，不覆盖并行算法变更。

不改memory的FTS、版本快照、来源身份hash、遗忘抑制、排序或生命周期算法。字段词汇变化不是重写个人库的理由。

## 6. 子实例与恢复

新建子实例从父当前run复制执行范围并持久化；续跑先读自身记录，再核对session/role/agent_id/thread、role fingerprint、状态、版本与checkpoint。范围不匹配或目录失效时保留记录并拒绝执行，不能删记录后悄悄新建。

子实例资源准备和AGENTS/Skills必须与其保存执行范围一致，不能路径使用A而read_resource_roots/指令沿用父当前B。当前没有切换会话目录入口，本片不新增；未来引入时必须保留该合同。

旧子实例只能继承父目录，迁移可以据此回填，但损坏/矛盾记录必须失败。主目录整体搬迁与父会话选择另一执行目录不同：现有“搬迁保UUID”路径须独立核对，仅处理能证明属于原Local绑定的记录，不能再无差别重写所有会话目录。

## 7. 归档迁出执行目录

本片采用现有可配置data_dir下的 `workspaces/<project_id>/artifacts/naiyue-agent-output/`；没有项目身份的临时会话使用 `sessions/<session_id>/artifacts/naiyue-agent-output/`，不创建项目标记或保存聊天。复用现有随机文件名、拥有者标记、配额和归档实现；session/run/工具调用及后台任务仍通过原结果/登记记录关联具体路径，不为了目录分层新增一套索引。该路径由应用装配，不能由模型提供；数据库显式放到别处时，归档仍由data_dir控制。独立非持久调用须由可信调用者提供明确归档范围，测试使用临时路径，不默认写用户全局目录。

“移出执行目录”是实际规范路径约束，不只是目录名变化。配置的data_dir若使归档仍位于执行目录内部，必须明确反馈配置不满足本合同，不静默改用户配置或宣称目录删除不影响归档；沿已有真实路径/链接校验处理，不能只比较未经解析的字符串。

Shell cwd、文件工具根与archive_root分开。后台任务交接时冻结archive_root；后续list/output/stop不重新从session当前目录计算。MCP长结果共用归档能力，不能遗漏。

应用启动装配从已绑定库读取项目UUID，再交给SessionService；不能在会话注册锁内读取SQLite。应用产生的data_dir先规范化，再派生并冻结归档根，任务与清理保护使用同一规范路径。MCP的归档属于已知远端结果的可选保存；归档范围不可用时保留有界正文、success/tool_error和来源，标记archive_root_unavailable，不重复远端调用，也不覆盖原结果。Shell真实归档故障的进程停止合同保持。

读取和清理要同步：结果返回真实绝对归档路径，保留Read的原审批与真实目标核验，返回路径本身不授予读取权限，也不把整个data_dir或数据库加入resource roots。后台output沿任务记录取已保存路径和有界尾部，不经过执行根门禁；CLI预览/确认清理使用真实项目归档及原精确快照检查。active任务的归档不能被清理；原按执行目录判断清理占用的调用方必须同步改为归档归属，不能只改目录字符串。

执行目录移走后，已启动任务仍能继续归档与读取输出；不加吞错分支。额度、磁盘故障及写入失败沿原有停止/明确反馈，不因本片变成best effort。记录中不能伪造完整输出。

旧归档引用保持可识别；本片不擅自搬动或删除真实用户旧文件。新归档切换、旧引用读取/缺失反馈和显式清理要有版本边界，不能声称只改新写入位置就完成全部迁移。

## 8. 持久化、备份与改动面

最新读取时并行memory概览迁移已使用schema17；本片必须从实际版本顺延，不能预占版本号。沿既有迁移前快照→事务迁移→结构验证链，回填已绑定session项目归属、Local执行目录及子实例创建时目录。未绑定旧库不凭路径猜项目UUID。保留thread/run、审批、历史、memory与版本字段，不在迁移时要求执行目录必须仍存在。

`storage_backup_schema` 当前对所有schema>=14复用最新subagent FIELDS。新增字段时必须按旧/新schema分别校验，避免schema14–16备份被错误拒绝。同步旧库fixture、迁移回滚、还原与标准安装smoke，不能只更新当前版本断言。个人format2保持。

预计消费者：workspaces/config_paths；session_state/agent_session/session_service/session_execution/session_persistence；session_storage/storage_migrations/backup_schema；history_queries；memory_access/memory_sources/memory_results；subagent_records/storage/runtime；permissions/permission_rules/paths及文件工具；shell/process_execution/output/background_tasks；MCP results；CLI/Web状态、归档读取与清理；对应测试和安装worker。只改实际消费者，不在本片搬包或重写门面。

## 9. 确定性验收

复用现有pytest和固定模型，不新增平行测试框架；计划新增集中执行目录合同测试文件，测试名随实际入口确定。每个新行为先看到正确红灯，再实现。

| 场景 | 必须断言 |
| --- | --- |
| Local新建/保存/重开 | UUID不变，项目与执行目录可分别追踪，原正常工具/历史/memory行为保持 |
| 同项目/不同项目 | 数据选择按project_id；错项目拒绝；ID相同但执行登记不符仍拒绝 |
| 三入口目录消失 | 保存运行/子实例后移动临时执行目录；历史仍可读，恢复后新run、子实例续跑、后台submit均明确拒绝且不fallback |
| 四种错误原因 | missing/not_directory/身份错误/未登记分别得到统一code和对应reason；不调用模型、runner或文件操作 |
| 审批跨目录 | 相同call/args在另一执行根不可消费原许可；相对规则基准正确 |
| 子实例冻结目录 | 改父当前目录不改旧子实例记录；续跑使用旧范围或明确失败，绝不偷换 |
| 文件忙碌键 | 两目录同名文件可独立reserve；同一实际路径仍互斥 |
| 既有后台任务 | 用事件同步确认进程启动后移走执行目录；进程不被目录逻辑停止，输出继续写项目归档，list/output/stop及aclose可用 |
| 真实归档故障 | 故障注入仍触发现有停止/错误与清理合同，不伪报archive_complete |
| 来源与旧数据 | 已删除目录的保存来源仍可引用；错项目/陈旧版本拒绝；旧库与旧备份可校验、迁移、还原 |

三入口反例不靠sleep时序：只在确定的保存/启动事件后移走TemporaryDirectory中的执行目录，保留另一个可回退目录的哨兵；断言哨兵未变、没有cwd回退和额外模型/进程调用。计划测试分别命名为 `test_missing_execution_root_keeps_history_but_blocks_new_run`、`test_missing_child_execution_root_rejects_continuation_without_rebinding`、`test_missing_execution_root_rejects_background_submit_without_spawn`；必须从对应真实应用入口进入，不能仅测同一个validate函数三次。已有后台进程另测 `test_running_background_task_keeps_project_archive_after_execution_root_disappears`。这些是待实现测试，不是已执行结果。真实Git worktree创建/登记的端到端证据留后续功能片，不能将Local夹具报告成完整worktree支持。

完整身份迁移完成后运行受影响专项、全后端、前端测试/构建及标准wheel/源外安装。以下记录归档子步骤的独立证据，不能替代整片验收。

## 10. 归档子步骤实际接线与边界（历史验收）

当时主链：持久工厂读取库绑定→SessionService从配置data_dir和项目UUID派生归档根；临时应用按session_id派生→AgentSession保留归档根→根执行、审批恢复、子执行的RunObservation.bind→RunServices与invocation context→Shell/后台/MCP归档器。后台任务单独保存archive_root与ProcessRequest.cwd；list/output/stop使用原任务记录，清理门禁比较归档根。AITest消费共用execute_process_request。这里的RunServices.archive_root是01的接线；02实施时随其余运行依赖一起迁移，不保留第二条注入路径。

正常例子：在execution目录启动后台进程，等待输出握手后移走execution目录，再放行第二次输出；进程保持running，输出落在项目data_dir，list/output/stop仍能通过原会话范围访问。临时应用同样能归档，测试明确断言没有.naiyue身份文件或SQLite，history/memory调用返回不可用。

失败例子：真实归档写失败沿现有archive_error停止并确认回收；归档根位于执行目录内时Shell启动前拒绝。MCP已经成功返回后才发现归档根不可用，则保留成功结果并明确标记归档降级；正式SDK回归断言远端只调用一次。含`alias/../data`配置的清理回归，断言主run已空闲、后台任务仍活跃时预览仍被拒绝，任务停止后才可预览。

新增测试位于`tests/test_execution_root_archives.py`；目录失效后三入口拒绝、身份/schema迁移和02尚未实现。旧归档不自动移动；外部归档的Read仍受原审批约束，未扩大全局数据目录权限。当前归档子步骤未提交或发布。

TDD证据：执行目录移走后的第二次输出在原实现触发FileNotFoundError并导致archive_error，新增行为测试首先失败；路径规范化及MCP归档降级的两个回归也先分别得到“未拒绝清理”和ToolException红灯，再修改生产实现转绿。测试夹具显式提供归档范围；HOME隔离避免写开发者数据。旧取消收尾fake补齐当前memory准备/任务查询依赖，保存顺序、取消、异常身份与终局断言保持。

本机验收（2026-10-22）：

| 验证 | 实际结果 |
| --- | --- |
| `PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short` | 4544 passed in 308.68s；这是当次整个工作区测试结果，不是本片新增数 |
| CLI入口与临时HOME隔离专项 | 118 passed in 13.36s |
| 归档、生命周期与MCP复核专项 | 85 passed in 19.85s；随后增加的正式MCP单次调用场景包含在全量中 |
| `npm --prefix frontend test -- --run` | 50文件、422项通过 |
| `npm --prefix frontend run build` | exit 0，TS/Vite成功 |
| `python -m scripts.build_release --target macos-arm64 --output build/spec128-archive/dist` | exit 0；Twine check通过 |
| `python -m scripts.verify_wheel_install --wheel-dir build/spec128-archive/dist --output build/spec128-archive/install-smoke.json` | exit 0；源码外新环境安装、后台与memory等共享smoke通过，provider_requests=0 |
| compileall / git diff --check | exit 0 |

日志与安装结果保存在`build/spec128-archive/`。候选包之后并行`memory_jobs.py`继续变化；源码比较确认该文件与候选不同，不将候选称为整个当前工作区的最终发布产物。本片归档生产文件保持一致；没有发布、提交或升级依赖。专项复核的两个发现（清理路径规范化、MCP已知结果保留）均已用红绿回归关闭。第1项其余验收仍待实施，不能用这里的绿色结果代替。

## 11. 01实施映射（2026-10-23）

基线v0.4.0/0ba38f3；分支codex/project-execution-root，开发检出与原发布/学习目录隔离。不提交、推送或发布本片，不修改真实配置、用户数据库或依赖。下列是本次实施合同；当前实现、验收和限制见第12节。

- Project复用现有workspace UUID，持有canonical_root与database_path；ExecutionRoot为冻结的path/kind/可空branch。SessionState保存project_id与execution_root，ActiveRun冻结本次范围；无目录、无项目身份分别表达，不生成虚构项目。
- 项目schema17→18：会话表新增project_id、execution_root_kind；现有workspace_root列只映射执行路径。子实例新增project_id、execution_root、execution_root_kind，旧实例从可信父记录回填。绑定库迁移验证旧范围，不访问旧目录；未绑定库保留NULL项目身份。备份14—17使用冻结的旧子实例字段，18检查新字段，个人库format2保持。
- 执行身份校验置于上下层可使用的模块，不导入Application。Local校验路径、类型及项目marker；无项目临时目录仅校验存在/目录；worktree尚无生产登记入口，明确not_registered。不得为此提前建设worktree服务、监听或恢复平台。
- 本片仍沿现有显式context传递执行信息；workspace_root在工具/公开边界中只表示执行路径，项目范围必须使用project_id。新的冻结执行范围随当前RunServices/根子恢复传递，02再整体迁移类型化context，不在01引入RunScope。
- 停止待审批批次只做控制恢复和必要保存，不重新发现AGENTS/Skills，也不调用模型或执行被暂停的工具；即使目录已失效，仍能补齐停止结果并清除pending。批准/继续仍执行目录门禁。
- 工具执行许可核对实际执行目录；文件准备与实际IO、Shell、后台submit、新run和子实例续跑分别在其新执行入口校验。管理既有任务、取消/停止、必要收尾和打开历史不受目录有效性门禁阻止。
- history列表/目标/游标，memory准入/来源/batch/概览目录/worker收尾均按项目ID核验。个人scope=personal继续使用data_dir/personal/memory.sqlite跨项目共享；kind=user不代替scope。项目来源原文仍不跨项目开放。
- 已保存来源与后台记忆整理是项目存储操作，不要求历史来源目录仍存在；工具调用的当前执行范围由应用准入核验，不能因此删除来源版本、权限或角色检查。
- 子实例续跑先读取自身冻结范围，校验其有效性及与父当前范围一致；不覆盖旧记录，不允许父B子A。第一次创建与当前运行资源、AGENTS/Skills一致。整体Local搬迁只更新能证明属于原绑定的Local记录，不批量改写其他执行范围。
- HTTP的新执行准入将execution_root_error传入现有RunDelivery；先处理重复请求回执，再映射execution_root_unavailable/reason，不破坏原幂等顺序。
- CLI/HTTP公开字段和工具JSON参数保持；新增有限执行目录错误状态，历史可读但can_run=false。普通目录检查与归属失败使用execution_root_unavailable及既定reason，不把原始IO异常或配置正文送到前端。
- 归档仅沿0.4.0已发布的外迁布局，不在01改为未来session/task布局；绝对路径忙碌键保持；新分片不创建、合并、附着、切换或回收产品worktree。

先以真实持久入口、新后台submit和子实例续跑写目录消失反例，再补身份迁移、项目查询、权限/文件和旧备份回归。测试夹具如需项目身份，使用临时marker/正式绑定，不在生产代码为测试自动生成身份或跳过校验。验收包括专项、全后端、前端/构建、标准wheel和源码外安装，另交付正常/失败调用链及旧消费者清理结果。

## 12. 当前实现、实际主链与验收（2026-10-23）

本节对应`codex/project-execution-root`，从v0.4.0/0ba38f3开始；归档外迁已在发布基线中，本片没有再次改归档布局。身份和消费者迁移已接入且完成本机验收，以下分别列出完整回归、专项和候选安装证据；真实worktree产品能力及真实模型质量不在此结论内。

### 数据形状与责任

```text
Project(project_id=P, canonical_root=/project/main, database_path=<data>/workspaces/P/sessions.sqlite)
SessionState(project_id=P, execution_root=ExecutionRoot(path=/project/main, kind=local, branch=None))
ActiveRun(project_id=P, execution_root=<准入时取得的冻结对象>, ...运行中的控制状态)
历史/项目记忆/概览/来源 -> 用P限定存储范围
Shell cwd/文件根/相对权限规则/AGENTS与Skills -> 用ExecutionRoot.path
个人记忆 -> <data>/personal/memory.sqlite，scope=personal
```

`Project`不传入模型或文件工具；执行对象不携带数据库连接。当前产品仅产生Local，没有新增worktree创建/附着/切换入口。已有受管开发检出是本次代码隔离方式，不等于产品worktree功能。尚无产品登记的worktree类型明确返回not_registered，真实Git登记及worktree list核验留后续功能。

存储schema18保留公开路径列`workspace_root`，语义仅为执行路径；新`project_id/execution_root_kind`和子实例的三个身份字段全链路保存/回填。个人format2没有改变。未绑定旧库不会凭路径生成项目身份，迁移不读取或新建旧目录marker。

### 正常链

1. 持久工厂从库绑定生成Project，装配SessionService；创建Local会话时保存项目UUID与执行对象，持久化映射写入schema18。
2. `SessionService._admit`取得会话事实，`validate_execution_root`检查路径/类型/marker后进入原准入；`AgentSession.admit_run`将项目ID及执行对象放入ActiveRun。资源发现使用本次执行路径。
3. `RunObservation.bind`携带同一范围，`make_invocation_context`只投影显式执行身份；文件、Shell及后台submit使用实际执行目录。许可subject中的workspace表示这一目录，工具执行重新核对；相对路径和`${workspace}`规则仍沿现有解析器。Write/Edit的绝对目标忙碌键算法未改。
4. history/memory工具先保留原角色、活动工具和运行校验，再核对调用会话的执行范围；底层查询使用项目UUID。列表、目标、游标、来源、记忆batch、概览目录和后台记忆worker同步迁移，不再以来源会话路径决定项目归属。来源消息/版本/引文/工具成功核验保持。
5. 子实例首次创建保存自身项目/目录；续跑先从记录取出范围，检查可用性和与父当前范围一致，再沿原checkpoint/CAS继续。记录不会被父当前路径覆盖。新后台进程冻结cwd与身份，既有任务的list/output/stop继续沿自身记录及原调用范围工作。

### 失败链与必要收尾

- 关闭应用后移走原目录：`aget_session_history`仍取保存正文；`arestore_session`可安装保存身份，status给出`can_run=false/execution_root_error`；新run在准入前抛有限`ExecutionRootUnavailable`，不调用模型、不占用会话、不切换目录。
- 新文件IO在准备时和实际使用许可前校验；即使已经为外部目标准备许可，目录随后消失也不能读写该目标。后台submit校验失败不登记任务或调用runner。前台Shell同样在进程启动前拦截。
- 子实例续跑时移走其保存目录：原记录保留，结果为明确工具错误，子模型调用次数不增加。
- 停止待审批批次绕过新执行门禁，使用空read_resource_roots进入原停止控制链，不再发现失效目录的AGENTS/Skills；补齐user_stop配对结果、清空pending、保存终局，不执行工具、不追加模型调用。批准/继续仍校验目录。
- HTTP保持鉴权与项目归属校验；RunDelivery先处理旧回执和活跃运行，再检查can_run。新请求映射409/`error.code=execution_root_unavailable`及reason，原请求重复提交返回既有202回执，不新建job。前端展示失效原因、保留历史，并禁用新发送。
- 目录检查只发生在新执行入口；不新增文件监听或自动终止。已运行进程继续使用项目外部归档；真实归档写失败的既有停止合同保持。没有新增Shell沙箱保证。

### 迁移清理与保留的边界

删除SessionState的workspace_root字段，内部使用execution_root，执行路径投影为execution_path；ActiveRun保留独立快照。history/MemoryAccess/prepare_memory_parts及其脚本、worker与测试消费者改为项目UUID，不保留同时接受Path/UUID的兼容分支。probe_memory_semantics的已失效root参数同步删除。备份14—17使用冻结LEGACY_FIELDS，18使用新字段；旧schema夹具在临时库上准确移除新列，安装/还原smoke显式检查新增字段回填后再比较其余所有原事实。

保留的workspace命名都有实际边界消费者：CLI `--workspace`、HTTP/任务字段、数据库历史列、workspace身份marker与绑定、公开scope=workspace和目录布局。它们不再共同决定内部项目与执行语义。01仍沿现有RunServices/扁平context接线；02负责整体类型化，不在本片混入新上下文容器或保留动态兼容代理。没有搬包、门面/仓储/mixin重写、依赖/版本/真实配置变更。

测试夹具变更是新身份字段/项目UUID/真实旧schema的合同迁移：正式项目夹具使用临时marker及库绑定；外国项目使用其他UUID；HTTP路径隔离夹具使用合法绝对路径及类型；直接Shell许可夹具带审核目录；Web设置假对象补齐WebSettings已有workspace_id字段。旧顺序、配对、隔离、异常身份、保存与回滚断言保持；未删测试、放宽断言或skip。

### 验证证据

新增集中测试：`test_execution_root_contract.py`、`test_execution_identity_storage.py`、`test_project_data_scope.py`、`test_execution_root_files.py`、`test_execution_root_web.py`。三入口目录丢失反例先得到正确红灯，再实现门禁；schema回填与项目查询也先红后绿。停止审批红灯是Skills发现的FileNotFoundError；HTTP红灯是通用session_recovery_blocked，随后分别转绿。补充四种reason、许可跨目录、真实文件体二次检查、忙碌键、相对规则、Local限定搬迁、旧子实例回填和事务回滚。普通验收补充断言不冒称独立TDD红灯。

- 根/子/后台三入口、身份与项目专项的日志在`build/spec128-01/`。独立安全复核无新增高置信问题；架构复核发现的停止审批问题已按真实入口回归关闭，最新停止/HTTP/并发专项12 passed。
- 前端：`npm --prefix frontend test -- --run` → 50文件、423 passed；`npm --prefix frontend run build` → exit 0。保留既有chunk大小提示，没有TS或构建错误。
- 标准候选：`python -m scripts.build_release --target macos-arm64 --output build/spec128-01/final-dist` → exit 0；Twine PASSED。最初复用依赖目录的链接被现有打包检查拒绝，改为本地依赖副本后通过，未放宽打包检查。未发布此候选，版本仍沿用基线0.4.0。
- `python -m scripts.verify_wheel_install --wheel-dir build/spec128-01/final-dist --output build/spec128-01/install-smoke.json` → exit 0；干净环境源码外安装的CLI、原生工具、文件、后台、审批、迁移/备份/还原、父子重开和memory主链均通过，provider_requests=0。
- 包内208份Python、4份前端资源与当前源码字节一致；795份测试/生产/脚本/前端源码manifest保存在`source-manifest.json`。compileall、pip check、500行检查和git diff --check均通过。
- 完整后端：`PATH="/Users/zmw/naiyue-test-agent/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short --maxfail=10` → **4674 passed in 308.36s**，exit 0；见`backend-final.log`。初轮旧夹具/脚本消费者失败日志保留，完成字段/身份/许可及合法路径迁移后重新完整执行。

真实模型质量、Windows/Linux原机及产品worktree登记/创建不属于本片已验证证据。当前没有提交、推送或发布；下一片仅讨论128-02，用户同意后才能实施。
