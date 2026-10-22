# Spec126文件地图：主Agent复用子实例

状态：126-01—04均已完成本机验收，见[01](126-01-subagent-continuation.md)、[02](126-02-subagent-continuation-lifecycle.md)、[03](126-03-subagent-discovery-delivery.md)和[04](126-04-subagent-acceptance.md)。主合同见[Spec126](126-subagent-continuation.md)。max_model_calls按本次委派计数，审批恢复共用本次额度；真实供应商/其他原机另验，当前未发布。

## 1. 当前模块与变更理由

复用原SessionService/AgentSession、共享角色图、唯一子driver、权限/审批、saver和SessionStorage。新增的长期数据只有轻量实例记录，短期执行仍属于当前ActiveRun；不把Shell后台任务manager改成通用Agent平台。

两类新增模块均先核对过现有session_state、subagent_history、chat_queries、reply_storage等消费者：

- application/subagent_records.py：定义实例字段/状态验证和角色合同指纹，供AgentSession准入、SQL行映射和查询投影使用。独立于session_state以免把整份子历史或所有逻辑塞进已承担根状态职责的文件。
- application/subagent_storage.py：一张表的初始化/参数化查询/claim/子完成/父事务确认/退役/删除；沿既有功能函数+SessionStorage锁/连接模式，不加Storage子类或新通用数据库层。

不新增异步协调服务或后台worker。若实施发现单文件将超过500行，只按本地图既有职责拆分并先更新Spec，不以行数为由删检查。

## 2. 126-01：调用/执行/保存基本闭环

| 文件 | 必须变更与真实消费者 |
| --- | --- |
| tools/subagent.py | SubAgentInput增加可选agent_id，继续保持agent/task；delegate传递完整目标；root-only检查/角色禁止列表含新只读工具 |
| application/session_subagents.py | SessionSubagentControls转发可选目标；准备阶段分新建/查记录，预占/条件claim与父permit重验；新input身份；本次必要收尾后释放driver，不丢续跑记录 |
| application/agent_session.py、session_state.py | AgentSession临时模式记录；ActiveRun本次调用去重/触碰集合，原subagent_instances仅放当前驱动；所有停止/审批/resolve_*仍找活动实例 |
| 新subagent_records.py / subagent_storage.py | 有实际执行准入、保存及读取消费者，不留占位方法；新格式与legacy状态明确 |
| agent_runtime/subagents.py | 同图/同thread新消息，不用审批Command冒充续跑；模型可见ID信封、完整预算、本次usage；子限额中间件abefore_agent按可信新委派标记重置计数，审批恢复保留，检查/递增沿官方逻辑 |
| application/session_execution.py | 保留本run触碰记录直到必要保存；临时模式成功收尾才ready，失败不释放为可续跑 |
| application/session_storage.py | 小型存储方法沿原锁/连接；finish_run同事务校验子head并确认ready；无第二存储权威 |
| application/storage_migrations.py、storage_backup_schema.py | schema14单表迁移，预备份、结构/引用校验；未知版本拒绝与迁移失败阶段报告保持 |

测试：新增test_subagent_continuation.py/test_subagent_continuation_persistence.py的内存/SQLite闭环、参数许可、同ID并发/重复调用、各次委派独立次数/同次审批共享次数/报告预算、故障写入和schema13迁移；迁移既有精确报告断言以匹配新增公开信封，不删除业务或异常检查。

L3接线检查：模型能从ToolMessage.content读到真实agent_id；同一个父任务后续模型步骤及下一轮真的调用同一thread；新建仍是另一thread；保存方法在真实finish_run被调用，而非只有类/表存在。

01必要接线补充：原ID历史解析/归档范围、基础引用保护与删除提前到本片；显式ID的所有读/归档/删除引用须核对可信会话登记，不能认领失败的越界调用。Web ToolResult先接新信封正文/原始结果，03再做完整多次委派事件投影。旧schema测试fixture与release_storage_smoke同步移除新表来构造真实旧版本，保留原行为断言。

## 3. 126-02：归档/重开/生命周期一致性

| 文件 | 必须变更与真实消费者 |
| --- | --- |
| application/subagent_history.py、chat_queries.py | 从创建与续跑的实际参数/结果映射稳定ID；同一实例聚合，旧记录仍可读；不再对每个续跑调用生成假新实例 |
| application/chat_history.py | 使用本run触碰的登记范围补齐child scopes，增量游标保持；父压缩后不失联 |
| application/session_persistence.py、session_storage.py | 正常重开只读载入；ready/子head确认与父finish原事务；删除集合包括登记child，root保存失败仍blocked |
| application/session_subagents.py | 子委派从可信父图active_reply_id保留跨根审批恢复的逻辑回复归属；新followup仍是独立回复 |
| application/async_application.py、async_persistent.py | 现有aget_subagents/aget_subagent_history/aget_chat_subagents统一新登记与legacy读取；签名保持，无仅给旧测试的平行推导入口 |
| application/reply_storage.py | activate原事务将丢弃reply触碰实例退役，保留前缀引用也不再允许复用脏未来上下文；完整孤儿沿旧规则处理 |
| application/checkpoint_retention.py | 登记引用和独占owner加入保护，现有小批限额/任务收尾时点保持 |
| storage_backup_schema.py | 新表非空checkpoint指针检查、迁移/备份/还原后的同样验证；不能只检表名 |

实际测试：新增test_subagent_continuation_boundaries.py、test_subagent_continuation_storage.py和test_subagent_continuation_crashes.py；正常重开和三个确定崩溃点、根审批恢复组合、归档故障、父压缩/子压缩、重生成前缀保留与退役、修剪、删除、备份还原及旧格式只读。基础归档/旧格式保护沿01复用，不重复新建入口。

先沿测试真实断点验证父调用/子thread/保存头/登记字段一致，不能以仅可读AI报告证明能安全续跑；恢复阶段旧模型/工具/Auto/Future均0次。

## 4. 126-03：主Agent查找与原界面展示

| 文件 | 必须变更与真实消费者 |
| --- | --- |
| tools/subagent.py、application/subagent_queries.py | 前者放schema/工具工厂/选择派生；后者放候选元数据和完整JSON预算/分页，与执行准入、历史解析分开 |
| tools/builtin.py、tools/__init__.py、agent_runtime/tool_execution/toolset.py | 只读工具注册/导出，根请求内按subagent开关派生list，定义与执行使用同一视图；用户选择不保存list |
| tools/settings.py、docs/examples/settings.example.toml | tools.subagent的max_list_items/max_list_bytes/task_preview_chars；合法/非法/默认和真实构造传递都测试；不改用户真实配置 |
| application/session_subagents.py、session_execution.py、run_events.py | alist_subagents能力由同一会话owner生产，根run绑定，无跨会话查询 |
| application/session_service.py、agent_session.py、session_persistence.py、session_recovery.py、async_application.py、async_persistent.py、cli.py、cli_mcp.py | 初始/交互/恢复选择规范化；冷审批按当前派生请求集合校验旧批次，逐call权限重验不放宽；查询统一候选元数据，控制选项只显示subagent |
| application/session_context.py | 下一请求预览及一致性复查与实际模型请求使用同一工具派生；手动压缩入口同样对齐 |
| agent_runtime/run_services.py、invocation_context.py、__init__.py | 同一运行服务/显式上下文投影和类型合同；不加ContextVar或应用对象，子入口不投影根查询能力 |
| web/service.py、web/agent_projection.py | 列表聚合/新元数据，普通委派message重置当前态/父批，approval_resume不误清空；原大小/鉴权/范围检查保持 |
| application/cli_display.py | 原source显示支持同path新委派/审批恢复和旧run迟到隔离，不新增用户直聊命令 |
| application/session_controls.py、cli_async.py、web/schemas.py、run_delivery.py、frontend/src/api.ts、subagentControls.ts、useConversation.ts及App/Conversation/ToolCall | 可选子执行/审批身份逐层透传，实际同锁业务检查拒绝旧绑定；新前端模块用于原500行hook职责拆分，通知/刷新仍归原hook |
| frontend/src/types.ts、agentEvents.ts、useSubagentList.ts、useSubagents.ts | 同一ID多次结果、当前来源和父批更新；草稿重置、读取失效/迟到隔离，现有列表/详情分页消费者同步 |
| frontend/src/components/SubagentSidebar.tsx | 只增不可续跑原因和必要文案，复用消息流/停止；不加输入框/继续按钮、不改统一侧栏框架 |

测试：新增test_subagent_discovery.py，扩展test_web_subagent_stop/test_subagent_events、目录消费者及相应frontend测试。覆盖重用同tool_call_id但不同batch、新普通委派/审批继续、旧run终局/停止/详情晚到、预算分页/非法游标、上下文预览与零模型只读。冷审批旧批次及release_storage_smoke旧格式候选按新合同迁移，完整历史/次数/副作用断言保留。

用户确认子智能体单开关：当前启用subagent即配套开放list；旧会话不改库即可获得查询，不自动调用。Web只显示subagent开关，CLI/应用的用户选择规范化同样生效，实际工具调用和结果继续展示。

## 5. 126-04：验收与旧消费者迁移

- scripts/release_smoke.py沿既有共享worker模式接新增scripts/release_subagent_smoke.py：正式图/临时配置/真文件和SQLite，新建→同ID第二次委派→正常关闭重开→按ID第三次委派，原报告/文件次数/模型真实调用数明确；仅用固定模型，安装环境独立于源码。新worker有源内测试和标准安装两个消费者。
- 脚本单元验证既有report检查器的实际拒绝路径，故意篡改thread/次数或省略新用例不能通过安装门禁；不靠“脚本文件存在”。
- 测量在上述worker或独立临时探针执行，记录纯元数据和完整固定模型路径，不在产品中加基准服务/常驻采样器；所有临时子进程/服务/页签完成后回收。
- README.md、docs/web-chat.md、主Spec/交接/待办同步已实现范围；Spec086/095加版本入口说明，保留历史证据，不改学习笔记。
- 旧subagent_records扫描、旧prepare硬两字段比对、旧报告格式、错误累计usage及一次性parent_batch缓存必须迁移真实消费者。角色共享图、必要save适配、内部审批Command、同步短IO/锁和SDK线程回退保留；不能因“续跑”改写全部执行层。

全量检查用主Spec第12节命令；每片只报告拿到最终结果的验证。最终四目标构建/本机源外安装和UI真实浏览器证据分开；跨平台原机、真实供应商和用户理解不由本机固定模型替代。

## 6. 当前规划证据

- 只读核对过本表既有文件和调用点，当前session_storage393行/agent_session311行，新增SQL模块用于保持各自职责及500行约束。
- 规划基线23 passed in3.47s；原框架探针JSON见build/spec126-plan-J4ECMH4T/framework-probe.json。计数修订探针per-delegation-budget-probe.json确认同thread两次独立计数2/2、历史4→8、审批不重置；均无供应商请求。未执行本地图拟议新测试，不把新文件名当作已实现组件。
- 用户随后授权126-01—04，实际实现/验证见各片记录。04实际新建release_subagent_smoke/benchmark_subagents及两个对应测试文件，扩展release_smoke与verify_wheel_install；没有新增产品入口或学习文件。四片本机验收收束，其他未验范围不提前声明通过。
