# Spec128-03：领域分包与CLI适配器迁移

> 版本边界（2026-10-24）：本文保留当时迁移的说明。当前终端已由 [R1-06](R1-06-cli-service-client.md) 改为 HTTP/SSE 客户端，旧 cli_async/app.call 拥有者桥已删除；默认 Web 的共享接入在 R1-07。

路径版本边界（2026-10-24）：R1-02 已将 application 内的 SQL/schema 下沉到 storage，当前逐模块映射及事务合同见 [R1-02](R1-02-storage-dependency-direction.md)。本文件保留 Spec128 实施时点，不将旧内部导入路径当作兼容合同。

日期：2026-10-23。03已完成本机验收，未提交/push/发布；本片只迁移目录、导入和入口接线，不重写业务行为。基于v0.4.0标签的codex/project-execution-root工作区，01/02成果仍未提交；02已通过4686后端/423前端、标准构建和源码外安装。memory/history已随v0.4.0提交发布，满足搬迁前置条件。

版本边界：本表和验收描述03完成时的模块。04随后删除SessionStorage/PersonalMemory两个旧资源所有者并拆分仓储，当前存储入口见[04对象与调用链](128-04-database-repositories-and-uow.md#6-当前对象调用链和删除项)；03的纯搬迁证据保持。

## 1. 当前结构与目标

application目前平铺63份Python文件、10971行；根目录另有7份storage_*，终端CLI实现的7份文件位于application。入口与业务、存储交错，后续新增能力难以定位。

按职责迁移现有实现，不新增另一套接口或执行链：

| 目标 | 现有模块 |
| --- | --- |
| application/sessions | agent_session、session_service/state/execution/controls/approval/recovery/persistence/context，project_instructions、skills，background_tasks、task_queries |
| application/conversation | chat_history/queries、history_queries/projection、reply_storage、session_regeneration、stream_projection |
| application/subagents | session_subagents、subagent_history/queries/records/storage |
| application/observability | run_events、async_event_stream、model_usage_persistence/storage、diagnostic_persistence/storage、failure_policy |
| application/memory | 全部现有memory_*，包括查询/来源/写入/版本/概览/后台执行/个人库；本次不改变它们的职责或算法 |
| 顶层storage | SessionStorage整体、migrations、execution_storage、checkpoint_retention，以及原根目录backup/backup_schema/files/maintenance/restore/restore_record |
| 顶层cli | 原cli.py主命令转为包入口；chat.py接原application.cli；原7个CLI模块和startup_display、离线storage命令、MCP发现命令迁入此包 |
| application根保留 | __init__、async_application、async_persistent、errors；保留当前装配与真实应用出口 |

storage放在顶层，避免把离线维护直接挂在应用装配下面。聚合已有的chat/usage/diagnostic/subagent/memory存储函数随各自领域归位，SessionStorage依赖这些模块的关系保持。Database、仓储和unit of work留到04。

完整文件对应表保存为build/spec128-03/module-map.json；02源码快照在build/spec128-03/baseline，校验manifest覆盖801份源文件。本片差异与01/02分开核验，不通过Git重置或stash整理已有工作。

## 2. 外部合同与内部迁移

保留nyt默认Web、nyt chat、nyt mcp list、nyt storage、--help/--version及参数/返回码；pyproject中的naiyue_test_agent.cli:main保持可用。cli由模块改为包，增加__main__.py保留python -m naiyue_test_agent.cli；python -m naiyue_test_agent.web及包根__main__不变。

README明确公开了python -m naiyue_test_agent.application.cli。这是真实已记录命令，保留一个仅导入chat.main并执行的兼容启动文件，不保留旧CLI实现或内部辅助函数转发。其他已迁移内部模块、测试/脚本导入和monkeypatch路径同步迁移，不为仅可import的旧路径添加兼容层。application现有导出和AsyncApplication/AsyncPersistentApplication方法签名保持。

CLI拥有者事件循环、线程安全派发、审批/停止/关闭等待照搬；Web的认证、归属校验、幂等、错误映射与事件交付照搬。文件权限、一次性grant、存储SQL/迁移版本/事务、checkpoint状态、memory语义与用户文件位置不改。移动引起的模块日志分类路径更新，事件名与安全字段不改。

迁移同时覆盖普通/相对导入、模块别名、延迟导入、动态import_module、子进程中的Python代码、monkeypatch字符串、包资源/发行检查及当前文档。相对导入按搬迁前所属包解析，再生成准确的新引用，不能统一加一层点号或盲目替换短模块名。

## 3. 正常与失败调用链

正常例子：nyt chat → cli.main选择chat → cli.chat.main → cli.cli_async.AsyncCliApplication._submit → 拥有者事件循环 → application.async_application/async_persistent → sessions.SessionService → sessions.AgentSession → 原模型与工具链。结果经conversation.stream_projection和cli.cli_display投影到终端；原history/memory/observability保存继续走同一SessionStorage实例。只改变模块位置，数据形状、顺序与副作用保持。

失败例子：CLI发起操作时会话忙碌 → sessions服务抛同一个SessionBusyError → _submit把异常传回终端线程 → cli.chat原错误提示与返回行为；不由新包捕获或改写异常。Web同一异常仍交给原Web错误映射。离线restore失败仍由cli.storage报告原有限错误码，不创建Agent或发模型请求。

审批恢复例子：重开 → SessionStorage读取已保存身份/状态 → sessions.session_recovery重验 → 用户批准后新invoke → 02类型化scope及独立调用状态 → 原工具许可与执行。不能因为入口搬迁而新建平行服务、绕过恢复/审批或改变执行目录。

## 4. 实施及验收

1. 先保存02快照、列完整映射，再添加新路径、真实命令和包边界的失败测试，确认失败原因是迁移尚未实施。
2. 搬迁现有文件并同步所有消费者；领域包__init__只标明职责，不创建额外门面或提前装配服务。除已公开CLI启动命令外，旧实现文件不再保留。
3. 先跑导入/CLI/存储启动/生命周期专项，保留原行为断言；再跑全部后端、前端和构建。测试helper与monkeypatch只迁移入口，不放宽检查、skip或改业务预期。
4. 逐模块核对迁移前后函数/类实现，允许导入和模块引用变化；若发现行为问题，先定位是否搬迁引起，不在本片顺手重写领域逻辑。
5. 标准Mac ARM64 wheel、Twine、包内源码/前端字节检查及源码外干净安装，覆盖CLI/Web、审批、SQLite重开、MCP、后台/子实例和memory主链。包内不得残留被替代的旧模块。
6. 更新handoff顶部、project-plan和当前入口指引；历史Spec/课堂保留历史路径并说明版本边界，不改写已发生的运行记录。

不改真实配置、依赖、版本或用户数据库；不提交/push/发布，不实施04。验证证据写build/spec128-03；本节仍为验收计划，不是已通过声明。

## 5. 完整文件迁移表

以下路径相对于naiyue_test_agent；函数与类主体保持，导入及真实入口同步更新。application/cli.py另保留公开命令启动文件。

| 02位置 | 03位置 |
| --- | --- |
| application/agent_session.py | application/sessions/agent_session.py |
| application/async_event_stream.py | application/observability/async_event_stream.py |
| application/background_tasks.py | application/sessions/background_tasks.py |
| application/chat_history.py | application/conversation/chat_history.py |
| application/chat_queries.py | application/conversation/chat_queries.py |
| application/checkpoint_retention.py | storage/checkpoint_retention.py |
| application/cli.py | cli/chat.py |
| application/cli_approval.py | cli/cli_approval.py |
| application/cli_async.py | cli/cli_async.py |
| application/cli_background.py | cli/cli_background.py |
| application/cli_display.py | cli/cli_display.py |
| application/cli_mcp.py | cli/cli_mcp.py |
| application/cli_observation.py | cli/cli_observation.py |
| application/diagnostic_persistence.py | application/observability/diagnostic_persistence.py |
| application/diagnostic_storage.py | application/observability/diagnostic_storage.py |
| application/execution_storage.py | storage/execution_storage.py |
| application/failure_policy.py | application/observability/failure_policy.py |
| application/history_projection.py | application/conversation/history_projection.py |
| application/history_queries.py | application/conversation/history_queries.py |
| application/memory_access.py | application/memory/memory_access.py |
| application/memory_batches.py | application/memory/memory_batches.py |
| application/memory_context.py | application/memory/memory_context.py |
| application/memory_job_runner.py | application/memory/memory_job_runner.py |
| application/memory_jobs.py | application/memory/memory_jobs.py |
| application/memory_lexical.py | application/memory/memory_lexical.py |
| application/memory_lifecycle_schema.py | application/memory/memory_lifecycle_schema.py |
| application/memory_mutations.py | application/memory/memory_mutations.py |
| application/memory_overview_store.py | application/memory/memory_overview_store.py |
| application/memory_personal.py | application/memory/memory_personal.py |
| application/memory_personal_upgrade.py | application/memory/memory_personal_upgrade.py |
| application/memory_queries.py | application/memory/memory_queries.py |
| application/memory_results.py | application/memory/memory_results.py |
| application/memory_schema.py | application/memory/memory_schema.py |
| application/memory_sources.py | application/memory/memory_sources.py |
| application/model_usage_persistence.py | application/observability/model_usage_persistence.py |
| application/model_usage_storage.py | application/observability/model_usage_storage.py |
| application/project_instructions.py | application/sessions/project_instructions.py |
| application/reply_storage.py | application/conversation/reply_storage.py |
| application/run_events.py | application/observability/run_events.py |
| application/session_approval.py | application/sessions/session_approval.py |
| application/session_context.py | application/sessions/session_context.py |
| application/session_controls.py | application/sessions/session_controls.py |
| application/session_execution.py | application/sessions/session_execution.py |
| application/session_persistence.py | application/sessions/session_persistence.py |
| application/session_recovery.py | application/sessions/session_recovery.py |
| application/session_regeneration.py | application/conversation/session_regeneration.py |
| application/session_service.py | application/sessions/session_service.py |
| application/session_state.py | application/sessions/session_state.py |
| application/session_storage.py | storage/session_storage.py |
| application/session_subagents.py | application/subagents/session_subagents.py |
| application/skills.py | application/sessions/skills.py |
| application/startup_display.py | cli/startup_display.py |
| application/storage_migrations.py | storage/migrations.py |
| application/stream_projection.py | application/conversation/stream_projection.py |
| application/subagent_history.py | application/subagents/subagent_history.py |
| application/subagent_queries.py | application/subagents/subagent_queries.py |
| application/subagent_records.py | application/subagents/subagent_records.py |
| application/subagent_storage.py | application/subagents/subagent_storage.py |
| application/task_queries.py | application/sessions/task_queries.py |
| storage_backup.py | storage/backup.py |
| storage_backup_schema.py | storage/backup_schema.py |
| storage_cli.py | cli/storage.py |
| storage_files.py | storage/files.py |
| storage_maintenance.py | storage/maintenance.py |
| storage_restore.py | storage/restore.py |
| storage_restore_record.py | storage/restore_record.py |
| mcp_client/cli.py | cli/mcp.py |
| cli.py | cli/__init__.py |

## 6. 实际接线与验收（2026-10-23）

68个既有模块已搬迁。application根现在只有__init__、async_application、async_persistent、errors及公开旧chat命令的7行启动文件；5个应用领域包有实际消费者。storage为顶层11文件，CLI为顶层12文件；领域包__init__只声明职责，不做服务装配。原SessionStorage的44个方法整体保留，应用门面继承和会话mixin未改。

CLI正常链现在是 `cli.main → cli.chat.main → cli.cli_async.AsyncCliApplication._submit → 原拥有者事件循环 → application装配 → sessions.SessionService/AgentSession → 原runtime`。失败仍将同一业务异常返回CLI或Web原映射层。离线storage/MCP命令沿延迟导入进入原实现；认证、幂等、审批、控制、必要保存和异常身份不改。

对02快照逐文件比较：忽略导入/模块引用及已移到cli/__main__.py的主命令启动块后，**211份既有生产文件的实现AST全部一致**；独立复核解析导入目标的集合差异为0。68个模块在新进程中正/逆顺序导入均通过，没有发现依赖旧__file__位置的资源或失效的checkpoint类路径。

新增包边界/命令16项，先11失败/5通过，接线后全通过。首轮全量7失败/4695通过，均是两份源码自检测试仍拼接旧目录；已迁移动态模块名及参数化文件位置，保留原异步/禁止旧执行入口/封装断言。定向32通过后最终全量通过；一次在定向结果未确认前启动的全量被明确中止（630通过，不作为完整验收），原日志保留。

最终后端命令：

```bash
PATH="/Users/zmw/naiyue-test-agent/build/release-native-0ff7df8/bin:$PATH" \
.venv/bin/python -m pytest tests -q --tb=short --maxfail=10
```

实际结果：**4702 passed in309.81s**。前端50文件423通过，TypeScript/Vite构建通过；Python语法/生产文件500行/compileall/pip check/git diff --check通过。811份源码与README冻结记录无漂移。

标准Mac ARM64 wheel、Twine、源码外干净安装通过。219份Python和4份前端资源字节一致，README元数据UTF-8字节一致；68个新模块全部入包，被替代旧模块仅保留已公开chat启动入口。安装覆盖真实CLI/Web、原生工具、审批/SQLite/历史重开、MCP8次合成调用及清理、后台任务、子实例续跑和记忆主链；0供应商请求。架构专项25项独立通过，无剩余具体发现。未验证其他平台原机或真实模型效果。

证据：build/spec128-03/verification.json、backend-final.log、frontend-tests.log、frontend-build.log、implementation-comparison.json、artifact-verification.json、install-verified.json及review-findings.json。02快照与68模块映射保留；未改依赖、版本、真实配置或用户库，未提交/push/发布。下一步仅讲解并确认04数据库/仓储/工作单元；本片不自动开始04。
