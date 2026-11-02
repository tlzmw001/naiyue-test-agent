# Spec143I：S04 装配边界只读分析

日期：2026-11-01。状态：**当前源码调用链/依赖核对与80项既有确定性回归已执行，确认可分离边界；尚未实现B装配入口或部署Runner。** 总方案见[143](143-cloud-task-b.md)，权限继承见[143a第8节](143a-runtime-and-state.md#8-a权限实现的继承清单与保证边界)，实施顺序见[143f](143f-implementation-slices.md)。

本次对应用户选择“直接做第2项”。原S00的Linux/Windows三段已由用户报告通过，不要求重跑；审阅后新增的无TTL、身份与网络/模型门禁仍单独按[143h第8节](143h-s00-backend-gate.md#8-s00补充门禁与下一步未执行)记录，未被这些图片自动覆盖，不阻碍本次分析。只读指不修改产品/测试/探针/真实配置或真实数据库；既有测试仅使用临时夹具，本次新增文档和本地证据。

## 1. 结论与最小拆分线

可以复用现有Python/create_agent循环与运行/保存机制，拆出固定task的Runner装配。已有`SessionService`通过构造参数接catalog/工具/执行根、通过`bind_persistence`接必要保存、通过`assemble_runtime(checkpointer)`接图与根子运行，具备可检查的依赖边界。

但当前没有可直接发布的Runner入口。`assemble_host(environments=None)`只关闭部分A分支，仍会装配MemoryAccess/MemoryJobs、ProjectHost API和原执行绑定/恢复；`open_storage`会创建仓储并执行saver.setup，不是Coordinator只读历史入口。不能把“参数可传None”或“源码能import”算成B接线完成。

最小落地采用现有装配顺序和对象，S04只按真实双消费者抽出必要组合步骤，避免复制完整host_assembly、创建第二套SessionService或先造通用容器/仓储插件平台。普通本机仍沿assemble_host工作；Coordinator不调用该入口创建Agent图，Runner不调用open_service_application建立全局项目/个人记忆服务。

## 2. 当前装配顺序与拥有者

```text
service_application.open_service_application
  ├─ ApplicationLifetime / DeferredModels / PersonalMemory
  ├─ ProjectService / HostManager / 共享RunAdmission
  └─ HostManager打开所选项目
       → host_assembly.assemble_host
          → prepare_tools
          → open_storage：ProjectStore + 独立saver连接/setup
          → 项目workspace绑定 + MemoryAccess
          → MCP资源/工具 + 权限规则校验
          → SessionService
          → 用量/诊断订阅 + MemoryJobs
          → SessionPersistence → bind_persistence
          → assemble_runtime(checkpointer) → create_agent
          → ProjectHost：MemoryAPI回填sessions.memory及各领域API
```

主要源码：[host_assembly.py](../../naiyue_test_agent/application/projects/host_assembly.py)、[service_application.py](../../naiyue_test_agent/application/service_application.py)、[resources.py](../../naiyue_test_agent/storage/resources.py)、[session_service.py](../../naiyue_test_agent/application/sessions/session_service.py)、[host.py](../../naiyue_test_agent/application/projects/host.py)。

关键先后：存储先可用，再建立SessionService；必要保存须在创建/恢复任何会话和装配图之前绑定且只能绑定一次。根子图复用同一checkpointer；ModelCatalog由外层拥有，关闭一个项目Host不关闭应用共享模型。异常时已启动memory jobs/MCP、usage/diagnostic和数据库必须结算，清理失败记录但不覆盖首个异常。

## 3. B 依赖归属表

| 当前对象/实际消费者 | Runner内保留或改接 | Coordinator拥有 | 普通本机仍使用 |
| --- | --- | --- | --- |
| ModelCatalog、SessionService.resolve_model/请求配置、权限auto reviewer | 只含授权provider/model的内部代理配置，根子/评审调用也走相同边界；不加载真实根模型配置 | 根凭证/固定上游/模型转发；有限记忆作业按原模型接入调用 | 当前真实模型catalog和共享生命周期 |
| ProjectStore + AsyncSqliteSaver；SessionPersistence、根子checkpoint、RunUnitOfWork | 每task自己的DB/连接，保留必要保存事务/恢复算法；不必先把所有SQL表拆成新框架 | 新任务目录、命令/审批、独占history投影；不打开task DB或调用其saver.setup | 当前项目库/临时库及存量schema消费者 |
| Project、ExecutionRoot、execution_authority；create/prepare_admission/恢复 | 从启动已校验task/project/generation与固定workspace构造执行事实，恢复逐项对照；禁止任意切目录/创建第二task | 注册身份、当前generation、准入和容器事实 | 本机项目重定位/authority及既有目录校验 |
| prepare_tools、SessionService/AgentSession、RunLifecycle、根子图 | 复用；参数显式绑定本task，根运行独占与子实例归属保留 | 命令转发与全局额度，不运行工具图 | 现有单会话运行/根子能力 |
| HostFileSystem、HostProcesses、BackgroundProcessManager | 容器内直接调用；复用工作根/授权、受管进程句柄、回收/输出边界 | 后端确认环境终止；不给任意宿主Shell/kill入口 | 当前本机文件/进程能力，不整包删除 |
| MemoryAPI、MemoryAccess、MemoryJobs；prepare_memory/save_memory_sources | 换任务作用域客户端；必要来源先在本task稳定保存/提交；不挂共享库或启动本地全局记忆作业 | 个人/项目记忆、来源投影和有限作业；查询适配自有投影，不能原样搬MemoryJobs假装字段已兼容 | 当前直接本机存储模式 |
| RunEventPublisher、ApplicationEventSubscriptions、AsyncEventStream | 继续做有界观察/临时展示；必要历史新增显式持久投递路径，不能只注册observer | 校验epoch/seq/摘要并事务写投影/ACK，提供快照/SSE | 当前同步观察及浏览器流 |
| ModelUsagePersistence、DiagnosticPersistence、checkpoint retention、自动记忆上下文 | 复用“可观测降级”的语义，不因新增转发误升级为全部必需；缺失/丢弃有状态 | 接收经过范围校验的可选观察数据，根计费/真实供应商回执不由任务自证 | 既有usage/diagnostic与后台优化 |
| MCP资源、Skills/AGENTS、archive_root | 仅任务授权的配置/项目资源；归档写任务state域，关闭/占用语义保留；不能复制全局settings/凭证 | 提供必要授权配置元数据；不执行任意MCP/项目脚本 | 现有本机MCP/资源与归档目录 |
| RunAdmission、ApplicationLifetime、ProjectHost领域API | Runner本地单根/资源收尾保留；只装配任务实际消费者，不照搬全局HostManager | 全局任务/容器额度和所有者API；命令ID去重、task/generation鉴权 | 当前跨项目共享准入、API及有序关闭 |

`HostFiles`在总方案中是职责简称，当前真实文件适配类是`execution/host_files.py:HostFileSystem`。复用的是容器内IO能力，不把本机环境标签误当Windows，也不恢复A的远端工具代理。

## 4. 正常运行的真实调用链

以现有本机组件的一轮确定性模型工具调用为例，以下是当前源码行为，不冒称B已实现：

1. `SessionService._create_session`构造SessionState/AgentSession并写sessions记录；存储创建失败则移除刚登记的内存会话。SessionState含thread_id/model、project/执行根、工具和权限，根task固定身份是B后续增加的启动校验。
2. `RunAPI.start → _execute → prepare_admission/confirm_admission → SessionService.arun → _admit → RunLifecycle.admit_run`校验根、权限/占用并建立ActiveRun。当前`admit_run`在普通本机分支构造HostFileSystem与HostProcesses，根子从RunScope消费同一执行能力。
3. `RunLifecycle.arun`先`SessionPersistence.start → storage.sessions.mark_running`，再准备项目资源/可选记忆，进入`ainvoke_agent_run_with_resources`；图用`durability=sync`，模型工具循环复用当前create_agent。
4. 工具执行从RunScope取得文件/进程适配器，模型收到ToolMessage后继续或结束。工具结果是运行数据，不等于最终必要保存已成功。
5. `arun`的finally通过`await_settled(_afinish_run)`收束子实例/控制，再调用`SessionPersistence.afinish`。即便try中已准备返回result，也要等待此finally完成。
6. `afinish`对ready状态读取并核对checkpoint无next/interrupt/error，`collect_chat_updates`生成档案更新；`RunUnitOfWork.finish_run`在单事务内写聊天、回复边界、子实例与会话状态/执行路径。图checkpoint由独立saver连接写，不与该事务冒称跨连接原子。
7. 必要保存确认后释放根run、发布run_finished，调用者才得到结果；usage/diagnostic、自动记忆和裁剪按各自可选失败合同处理。

主要源码：[run_api.py](../../naiyue_test_agent/application/sessions/run_api.py)、[session_execution.py](../../naiyue_test_agent/application/sessions/session_execution.py)、[session_persistence.py](../../naiyue_test_agent/application/sessions/session_persistence.py)、[unit_of_work.py](../../naiyue_test_agent/storage/unit_of_work.py)。

B在第1/2步增加固定task/generation身份，在第6步的必要保存边界增加可重发历史记录及Coordinator投影确认；不从第4步的普通事件回调推导“已保存”。本次只确认接线位置，没有新增outbox表或Runner入口。

## 5. 故障、审批恢复与观察降级

| 分支 | 当前真实链/状态 | B必须保持或新增的断言 |
| --- | --- | --- |
| 图装配失败 | assemble_host捕获主异常，已启动的MemoryJobs/MCP收尾，资源栈释放已进入对象；共享模型仍由外层拥有 | 只清理本任务对象/预留，不关闭其他任务模型或误删持久目录；不返回半初始化ready |
| 模型已返回，finish_run失败 | afinish将session加入_blocked并抛session_save_unconfirmed；_afinish_run将运行标失败；无原始主异常时保存异常向上返回 | 不能发云端成功终局或启动新run绕过待核对；重试投递/对账，不重放工具 |
| 模型主异常＋保存也失败 | arun保留primary_error；_afinish_run记录persistence_secondary_failed，原异常身份向上传播 | 保留主异常，同时可见必要保存缺口；不能为了网络同步覆盖原模型/工具异常 |
| 正在必要保存时取消/关闭 | await_settled保护收尾；根仍占用直到保存/子资源结算，重复取消不提前释放 | 暂停连接/Coordinator drain不能变成杀Runner；pending/unknown继续阻止空闲停止 |
| 根/子待审批及重开 | graph中断→pending保存；aprepare/_recover_checkpoint/validate_saved_approval核对工具、环境/subject与checkpoint，再显式恢复审批；旧未知副作用不重放 | Coordinator保存用户决定与batch/run/subject绑定；待审批请求先进入已确认投影才可接受云端决定，临时observer通知不是批准权威 |
| usage/diagnostic保存失败 | ModelUsagePersistence记录缺失/丢弃；afinish的usage flush失败记录日志后继续必要事务；diagnostic及普通observer隔离失败 | 保留可观测降级，不将所有模型观察事件改为必要历史事务，也不伪造完整用量 |
| 普通事件observer抛错 | RunEventPublisher._deliver记录并禁用该handler，继续运行 | 不能把必要历史outbox实现为唯一observer；它的错误会被原机制隔离，必须从必要保存路径显式确认 |

审批/恢复源码：[session_recovery.py](../../naiyue_test_agent/application/sessions/session_recovery.py)、[session_approval.py](../../naiyue_test_agent/application/sessions/session_approval.py)。观察源码：[events.py](../../naiyue_test_agent/agent_runtime/events.py)、[model_usage_persistence.py](../../naiyue_test_agent/application/observability/model_usage_persistence.py)、[diagnostic_persistence.py](../../naiyue_test_agent/application/observability/diagnostic_persistence.py)。

## 6. 已定位的四处实际手术

| 位置/证据 | 不能采用的捷径 | 后续明确处理 |
| --- | --- | --- |
| SessionService.prepare_admission/恢复、SessionPersistence顶层导入execution_binding；其模块导入A的Git输入/沙箱DTO | 只设置environments=None，宣称B已不依赖A；给任务伪造project_local注册来绕过校验 | S04分离普通本机与固定任务执行事实/恢复，再按真实消费者移除A分支；启动及恢复核对固定task，不引入假的全局项目根 |
| MemoryAccess直接取storage.memory/PersonalMemory；MemoryJobs直接取sessions/conversation及项目binding | 把整份项目库/个人库或原MemoryJobs对象挂进Runner；仅替换一个query方法便宣称整理链迁完 | S06分别改任务客户端、来源同步与Coordinator投影查询/作业装配，保留质量/遗忘/作用域检查 |
| 普通observer允许失败并禁用handler；必要保存有独立事务 | 用subscribe_events的一次HTTP POST替代可靠历史；在SSE回调里阻塞等待云端ACK | S04必要记录落盘/投影确认进入保存合同，观察沿旧有界降级链；两种流不混用完成条件 |
| ProjectHost最后才将MemoryAPI回填sessions.memory，负责领域API和生命周期 | 绕过ProjectHost后忘记接memory/任务查询/关闭；复制完整本机Application建立另一套全局服务 | S04列出真正需要的本地运行对象并显式接线，S06接scope客户端；每个声明对象有调用者/关闭者，Coordinator不用项目Host执行图 |

以上不是现有A的bug报告，也不要求现在修改这些代码。最小Runner首轮可关闭未接入的记忆能力并使用确定性模型，但禁止向任务临时注入真实根密钥或用无持久保存的假入口冒充完成。

## 7. 验证命令与证据

已使用当前`.venv`，不安装/升级依赖。8个现有测试文件78项通过（6.49秒），另外2项真实文件/Bash及重开测试通过（0.94秒），合计80项，无失败/跳过。它们证明A现有可复用行为基线，不证明新B组装、模型出站、容器身份或公司业务。

```sh
.venv/bin/python -m pytest tests/test_session_lifecycle_integration.py tests/test_application_assembly_resources.py tests/test_multi_project_boundaries.py tests/test_storage_startup_entries.py tests/test_active_run_lifecycle.py tests/test_model_usage_persistence_paths.py tests/test_architecture_semantics.py tests/test_restart_approval.py -q
.venv/bin/python -m pytest tests/test_host_process_environment.py::test_registered_child_and_restored_root_keep_process_environment tests/test_host_file_environment.py::test_registered_run_and_restored_run_use_same_environment_binding -q
```

| 现有测试入口 | 本次核对的行为 |
| --- | --- |
| test_session_lifecycle_integration.py | 根子审批/占用、保存成功与失败、主异常、取消/关闭和SQLite重开 |
| test_application_assembly_resources.py、test_multi_project_boundaries.py | 图装配失败资源释放、首异常、其他Host/共享模型不被误关 |
| test_storage_startup_entries.py | 存储初始化/备份失败阻止后续模型/MCP/saver；迁移后模型失败不乱回滚 |
| test_active_run_lifecycle.py、test_architecture_semantics.py | 绑定persistence的先后、根占用、子资源未收束与重复取消下必要保存 |
| test_model_usage_persistence_paths.py | usage正常保存与故障降级、异步writer收束，不覆盖结果/主异常 |
| test_restart_approval.py | 持久审批重开后显式决定只执行一次，策略变化/保存失败拒绝与可恢复 |
| 两项host file/process测试 | 实际读取与Bash、根子共享能力及重开后的新适配器/相同环境归属 |

日志/XML/源码指纹在`build/spec143-assembly-spike-20261101/`。执行前冻结1079个产品/测试/脚本Python文件，执行后核对无源码漂移；新增分析文档不计产品实现。没有真实模型、Docker、SSH或配置操作，未运行产品全量/前端构建或发布。

## 8. 对实施顺序的影响

第2项前置分析完成，S04不必重新选择框架或先重写Agent循环。S01先形成task/generation/命令/审批/投影合同；S02接生命周期；之后按S03.1→S04.1→S07.1完成最小CLI闭环。该分析不会自动启动S01/S04产品实现。

原S00已测范围按用户报告与截图保留。新增实机验证各自有名称/证据状态，不与原三段“是否通过”合成一个模糊待办，也不要求等报告归档才允许读取当前代码。进入产品片的门禁仍按143f/143h逐项核对，不能将80项A测试当作新增实机项通过。
