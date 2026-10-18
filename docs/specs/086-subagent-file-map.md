# Spec086 附录：文件、接口、清理与实施切片

104-03当前补证见[组合验收](104-03-concurrency-acceptance.md#6-实际结果与104收尾)：真实Write目标busy、根Write→子Read审批及必要保存隔离通过；subagents.ainvoke_subagent_step转换已识别模型异常时复用现有同run失败标注，保留具体分类和原cause。没有新增工具、错误类、控制槽或持久化字段。

当前拥有者（105-05收尾）：[组合验收与当前调用链](105-05-session-lifecycle-acceptance.md)。State是会话事实，ActiveRun是当前根运行/观察/队列/子实例，均由AgentSession持有；共享角色/图/准入/SessionPort路由归Service，装配入口assemble_runtime。保存/恢复IO归SessionPersistence，根必要保存后释放_run。public request_stop直接路由Actor，无旧私有转发。应用只等待自身操作及producer后关闭共享资源；105-05最终全量验收通过；[104-01](104-01-application-concurrency.md)已接后端默认10根额度与按会话持久化，[104-02](104-02-web-session-concurrency.md)已接Web按会话任务和106 E1订阅，组合验收已随104-03完成。下列早期记录保留原时点。

版本对应（102-01/02，2026-10-18）：根State已受控封装，根运行/审批恢复及持久化直接async，共享阶段见[Spec102结果](102-session-state-lifecycle-refactor.md#18-102-02实际结果与下一片2026-10-18)。旧_resume_steps已并入aresume_tool_approval，应用IOCall/arun_steps删除；子驱动async _subagent_steps继续保留，G—L历史实施记录按当时入口理解。

102-03已移除configured旧工厂/record_selection，根与子装配不再传空回调；测试/探针消费者及HTTP发前预算已迁移，见[当前结果](102-session-state-lifecycle-refactor.md#20-102-03实际结果与下一片2026-10-18)。[102-04已接SessionPort](102-session-state-lifecycle-refactor.md#23-102-04实际结果与下一片2026-10-18)：agent_runtime/session_port.py声明9方法，根/子建图共用同一SessionService，保留sid/path与根队列消费边界；旧项目指令回退已迁移删除。[102-05文本投影已收敛](102-05-content-projection.md#5-实际结果2026-10-18)：model_access.messages.content_text统一完整/delta投影，CLI/Runtime重复解析和Web.text_content纯别名已删除，canonical及模型图片请求保留。[102-06设置处理已收敛](102-06-web-setting-errors.md#6-实际结果2026-10-18)：WebService._apply_setting由三项入口直接调用，原准入、具体错误码/日志与成功确认边界保持；[102-07已收尾](102-07-middleware-contracts.md#5-全量验收与spec102收尾2026-10-18)：agent_runtime/__init__.py与subagents.py仅补顺序/装配区别说明，AST及排序保持；真实生产链测试在test_runtime_middleware_contracts.py，当前后端2415/前端186及构建通过。
日期：2026-10-16。属于[SubAgent Spec086](086-subagent-foreground.md)。S1—S6已实施，实际范围与证据见G—L节。基于085迁移后的tools/tool_execution目录，不另留旧runtime/tools兼容路径。下表路径相对仓库根，A—F为当前文件/消费者合同，G—K保留各片当时的实施边界，L记录最终组合验收；本机证据不代表真实供应商或Windows原机。

本版已按独立子运行边界重写审批、停止和保存接点；原父中断重入执行账/空GraphInterrupt/等待B后开放审批方案删除。[P4/P5探针](../research/subagent-framework-probes-2026-10-16.md)是前期框架证据；实际接线以各片验收为准。角色及已确认的一层/前台/共用权限合同不变。

2026-10-17现行基线：090A/B/C、091A/B、092A/B/C及093 A/B/C均已实现。只使用异步Agent执行；已有run_blocking、短同步状态方法和CLI线程桥继续复用。串行排队已无固定期限，四进程工具默认不限时、正数timeout可限制；进度及根恢复不是086待补功能。S1—S5角色、执行、审批、CLI/Web来源与停止/保存/关闭已接；S6已补真实文件/重开、同步SDK线程和子中断后根恢复的组合验收，删除S2无消费者回退。

## A. S1/S2新增的三个生产文件（均已接；S4新增见J节）

| 文件 | 必要职责、函数/数据 | 实际消费者 |
|---|---|---|
| `naiyue_test_agent/tools/subagent.py` | S1冻结SubAgentSpec及结构校验；S2已接agent/task输入schema、create_subagent_tool、原生coroutine入口及content_and_artifact | 配置/可信代码登记；显式测试接入后父ToolNode通过context端口委派；S3已入builtin，不导入上层 |
| `naiyue_test_agent/application/session_subagents.py` | SessionSubagentControls：已接register/get、prepare_subagent_instance、ainvoke_subagent、settle_subagent_drivers、_subagent_steps、assert_subagents_settled | 实例child_thread_id/唯一驱动者；局部失败只收尾该实例、转ToolException，不广播兄弟；根终止另作全体收尾；根运行复用直接async公共阶段与受保护收尾 |
| `naiyue_test_agent/agent_runtime/subagents.py` | build_subagent_runtime、ainvoke_subagent_step、SubagentModelFailureMiddleware、_finish_subagent_result | 单次独立thread图执行/观察/失败来源；显式共享saver；不拥有界面审批或另一套Agent循环 |

不新增AgentScope/SubAgentSession、子进程Agent、消息队列、独立重试/审批/事件平台、用于将来续跑的定位接口。新生产文件各≤500行；若实际复杂度超限，按明确职责拆分并先更新本表，不把业务混进通用utils。

## B. 扩展085既定模块及现有调用方

| 文件/组 | 修改点与函数 | 全链约束 |
|---|---|---|
| `tools/__init__.py`、`tools/builtin.py` | S1导出SubAgentSpec；S2实现create_subagent_tool；S3已纳入可启用内置集合 | 可信代码登记角色后才能成功启动；首版不自动加载项目Python/角色文件 |
| `tools/settings.py` | S1增加顶层subagents定义列表；S2已接tools.subagent有界输出设置，默认2000行/65536字节 | 沿既有settings.toml读取，无新配置路径；本轮只改样例，不改真实配置 |
| `tools/permissions.py` | 许可与scope绑定；启动角色的权限对象含args.agent | 内部动作仍逐批审查，不把角色启动Allow当内部Allow |
| `agent_runtime/__init__.py` | RunContext增来源和委派端口；装配明确根/子hook选项 | build_subagent_runtime复用装配，不复制middleware列表/模型循环 |
| `agent_runtime/invocation_context.py`、`resource_invocation.py` | make_invocation_context及资源调用带agent_path/执行环境/委派端口 | 显式传入跨线程；项目指令/Skill读取权限与085一致 |
| `agent_runtime/run_services.py` | 子运行绑定独立观察与停止对象 | ContextVar finally还原，子不能直接沿用父services |
| `tool_execution/toolset.py` | scope选工具；S3接入动态短角色索引，只在本次subagent活动时展示 | 下一请求看到新登记；子工具集合不含subagent，实际入口也检查层数 |
| `tool_execution/permissions.py` | 子审查取真人来源投影，保存scope、环境、批次决定 | 排除delegated_task/助手/结果正文；暂停恢复不重调Auto |
| `tool_execution/approval.py` | 由已保存准备结果构造局部官方HITL；停止优先检查 | 每个子图仍用官方单批interrupt；来源由应用登记/事件附加，不复制prepared到官方payload；根严格前置证明保留，hook不重读目标 |
| `tool_execution/authorization.py` | 沿085消费精确scope许可，父启动与子内部调用各一次 | 删除原候选ExecutionEntry/entered-suspended放行器；父工具不因子审批重入 |
| `tool_execution/batch.py`、`async_batch.py` | 保留现有aafter_model/awrap_tool_call和AsyncBatchPlan；回归090已完成的无限排队及根HITL恢复 | 父prefix/A/tail持续活动；保留排队后/交给执行链前的既有停止guard，不增加同步执行分支，无空中断分支 |
| `tool_execution/feedback.py` | 根控制/全局机制故障继续放行；subagent局部失败在委派边界收尾后转具体ToolException，公共层生成同ID error | 不增加SubagentError包装层，不把所有子异常str输出；普通叶子工具保持085兜底 |
| `tool_execution/observation.py` | scope、子执行片段/暂停与实例终局区别 | 观察失败隔离，授权/保存不依赖投递；不再记录父重入作为正常子恢复 |
| `agent_runtime/active_stop.py`、`run_control.py` | finish_stopped_batch复用；request可选可信reason=user/failure/close；对应固定停止提示 | 默认用户控制不变；故障收尾不冒充人工拒绝/停止；UserStop/CancelledError身份及真实IO结果保留 |
| `agent_runtime/events.py` | 冻结AgentEventSource、RuntimeEvent.source与RunEventPublisher.for_agent来源视图 | 一条根队列/序号；匹配子逻辑run，外层流仍root run_id |
| `application/session_service.py` | 已混入SessionSubagentControls和SessionApprovalControls；扩展scope读取及当前实例/pending批次生命周期 | 根_afinish_run在必要保存前直接调用本会话子settle/assert；AgentSessionSubagents/AgentSessionApproval拥有实例/driver/live审批，Service只注册/解析共享角色并路由；不复制权威状态 |
| `application/session_service.py::resolve_model` | S2新增agent_path参数，根读会话选择、子读实例冻结模型 | 不为S1登记提前改变模型选择；活动工具/权限解析归下方现有session_controls |
| `application/session_approval.py`（现有） | SessionApprovalControls、PendingToolApproval、register_live_approval、validate_approval_submission、submit_subagent_approval（短同步投递）；根prepare_resume/is_pre_tool_approval复用 | 只提交一个实例当前批；Future唤醒唯一异步驱动者，不重新invoke父图；根/子共享校验 |
| `application/session_controls.py` | 已扩展resolve_active_tool_names/resolve_permission_context的scope分支及request_stop/_request_scope_stop | 失败不伪装用户停止；批准历史不改写，已有真实效果保留，根主异常不被收尾覆盖 |
| `application/run_events.py` | 子RunObservation来源视图、可选stop_control注入、turn快照/恢复及独立context观察 | 根会话生命周期仍由根持有；同实例control跨片段保留，观察快照不判授权 |
| `application/__init__.py`、`async_application.py` | 已接aregister_subagent/get_registered_subagents、共享装配、asubmit_subagent_approval和根暂停停止；子图适配共用根保存backend | 子审批后保持原root run/SSE；只有异步Agent执行；同步SDK回退留在图内部，不嵌套事件循环 |
| `application/cli_async.py` | 可信登记、活动子审批投递与根暂停停止薄facade | 复用既有唯一async loop，投递不等待父run结果 |
| `application/session_persistence.py`、`async_persistent.py` | 根finish前核对子驱动/未决审批/IO收尾及根自身必要保存，子图显式共用同saver；关闭先停止再settle | A失败已结束并有error反馈即已收尾；其自身保存未确认不自动阻断根；不加子active DB行，不恢复失败实例 |

## C. 事件、审批协议及界面最小接线

| 文件 | 消费变化 |
|---|---|
| `contracts/session_events.py` | ApprovalRequestedData增approval_batch_id；新增来源在外层RuntimeEvent.source，不让contracts依赖Runtime类型；前端事件触发pending查询，安全字段与序列断言同步 |
| `application/stream_projection.py`、`async_event_stream.py` | source优先分流；子消息不并入根，外层run过滤/sequence仍统一 |
| `application/cli_display.py`、`application/cli.py` | 子审批即时显示/按批选择；活动子决定直接投递，不worker.join；EOF/quit先停/唤醒再join |
| `web/schemas.py` | 新approval_batch_id、行approval_id；origin_run_id/request_id保持；控制输入可带agent_path |
| `web/app.py` | approvals同一路由按服务端kind分派；approval-stop只服务paused_root；原鉴权保持 |
| `web/service.py` | live_child在busy时按当前root/目标批准入；paused_root保留旧恢复准入；不放开第二个父run |
| `web/run_delivery.py` | 子决定回当前root run回执，不新建producer；子source不结束根；paused_root沿原根恢复方式创建新root producer |
| `web/serialization.py` | pending按批含身份/角色/path/kind；source序列化；内容仍先文字投影/帧预算 |
| `frontend/src/types.ts`、`events.ts` | 全部新字段/来源分流；不同实例同call_id不冲突，子终局不改根终局 |
| `frontend/src/components/ApprovalPanel.tsx`、`ToolCall.tsx` | 一个实例当前批为一组提交，行key为approval_id；运行中显示待审组与子进度；明确停子/根 |
| `frontend/src/api.ts`、`useConversation.ts`、`App.tsx` | 去live_child的!busy显示门槛；提交带batch；回当前run不重置SSE/投影；根恢复回新run沿原行为 |

这里必须改Web请求合同，覆盖085原“web/schemas.py保持不变”的阶段边界；该说法只适用于没有SubAgent的085基础片。仓库消费者同时迁移，不保留两个审批身份自动推断的兼容分支。当前前端消费者已按源码定位；扩展ApprovalPanel.test.tsx、ToolCall.test.tsx、approval-flow.test.tsx、events.test.ts及tool-events.test.ts。

不做Agent树、历史浏览/重开、角色文件管理、后台面板、工具注册上传、图片/diff专用视图。必需审批/停止接线不是这些专用视图的替代品。

## D. 状态及接口迁移清单

1. 内部resolve_model/活动工具/权限读取加入agent_path，既有根调用默认root；根据真实调用方迁移，不新增无消费者scope层。
2. AsyncApplication新增aregister_subagent/get_registered_subagents，CLI同步外观仅桥接；根arun/aresume的位置参数合同不变。应用间隔离定义表，不把Python模块全局dict当注册表。
3. ToolRuntime.context只有ainvoke_subagent异步委派端口，工具层不依赖上层；子消息入口标delegated_task并沿真实用户投影审查。
4. pending以approval_batch_id区分实例当前批、每行approval_id；Web/CLI/remember_approved和测试同片迁移。原内部call_id仍用于ToolMessage配对，不能用approval_id替换模型协议ID。
5. 父/子显式共用saver但thread_id各自独立；App持有实例、唯一驱动者和当前PendingToolApproval。删除旧候选execution_entries，观察/工具卡不能当恢复权威。
6. 持久会话ready/受限恢复合同不变，无DB schema升级。source是事件/显示结构变化，不能误称只改后台；检查序列化、SSE、前端fixture和事件日志消费者。
7. 090已删除batch_wait_seconds/旧env并接协作停止与可选timeout；086回归现有行为，不重复改配置或补不存在的同步计划。
8. 关闭只关闭自己拥有的资源；先停止/恢复待收尾图，再等待owned tasks；子不得关闭共享模型SDK/存储。

## E. 删除及保留的具体逻辑

| 迁移后删除 | 明确保留 |
|---|---|
| 把全部待审批对象当作一个根pending、必须父run结束才能提交的UI假设 | 根pending_tool_approval/is_pre_tool_approval的严格判据；每个子thread独立官方HITL/Command及一次性授权 |
| 审批恢复再次完整review/Auto | 绑定一致性检查、文件真实目标执行复核 |
| 子中断冒泡父图所需重入账/空GraphInterrupt协调候选 | 父工具持续等待，原parallel/sequential及真实失败/取消身份保留；根HITL独立核验 |
| 090已删除的排队期限/旧env不再建设 | 默认无限执行、正数timeout、模型请求配置及必要进程清理边界 |
| 仅session_id查找而把子当根的新增调用 | 根异步API的默认参数及CLI桥、根steering/follow-up/会话终局职责 |
| Web/CLI所有审批必须父run结束/busy=false/join之后的判断 | 按保存kind分派、批/行唯一性、模型call_id配对、origin_run_id和幂等/认证 |

不因为定义同名或可import就保留废弃内部兼容。旧测试迁移行为断言，不删失败用例/skip/放宽计数；历史085/研究保留版本说明。子驱动跨重启恢复、WIN1原机验收和后台任务仍是独立待办；根有效前置审批恢复及平台分支修复已经完成，不预建占位。

## F. 六个实施切片与验证

085与090—093现已完成，以下以当前代码为基线。每片先写并实际观察预期红灯，再最小接线；涉及三文件以上的片在本附录范围内列出实际测试/组件清单后开工，合同变更先更新Spec。

| 片 | 目标 | 必须通过的证据 |
|---|---|---|
| S1 定义/登记/查询/配置 | SubAgentSpec、共享角色定义表、异步App登记与查询、CLI桥；settings.toml启动登记，禁止子角色包含subagent | 重名/非法字段/工具/模型不发布；空工具集/None模型合法；同App跨会话共享、不同App隔离；登记不调用模型/图/IO；无效配置启动失败并清理资源 |
| S2 实例/正常结果 | 独立输入、冻结模型、子图编译、根/子装配、唯一异步端口、有界content/artifact/可选call limit | 两同角色不串历史；第N次终局成功/第N+1超限；无额外总结调用；正常/内部错误/机制异常身份 |
| S3 恢复核心/开放工具 | 独立thread/驱动者、PendingToolApproval、批/行身份、scope许可；通过后才开放subagent及动态短索引 | A在B运行时获准并完成；同图不同实例/相同call ID；prepare/Auto不重跑；父不重入；串行prefix/A/tail；动态登记下一请求可见、停用工具不显示索引 |
| S4 来源/交付 | 外层source、CLI/Web运行中审批、按实例提交 | A/B待审分别提交；busy不拒正确子批；CLI不join阻塞；同run回执不重置SSE；根resume回归 |
| S5 停止/保存 | 活动/待审批子停止、根广播、竞争、关闭、SQLite保存 | 批准后停/已开始IO保持真实结果；A局部保存失败转工具error、B继续/可独立批准、父继续且根可保存；根保存失败不倒改子结果；原异常/EOF/取消无孤儿Future |
| S6 终检/集成 | 清理被替代入口、配置样例/用户操作文档、整体运行证据 | 图内同步SDK回退、异步App/CLI桥/Web、根ready及092恢复不回归；真实模型仅获授权后有界验证；Windows依WIN1单独实测 |

S3/S4阶段门禁现已解除：通用subagent已纳入可选择内置集合，CLI/Web已能提交活动子审批及停止。角色默认仍为空，未登记角色不能成功委派；前端工具启停/模式切换专用管理不在086内。

S1/S2已新增五份角色/执行测试；S3新增审批恢复/边界、混合批次与角色提示词测试；S4新增来源事件、CLI和Web子审批测试。S5新增停止、停止边界、checkpoint故障、共享SQLite保存、暂停根停止、CLI退出及HTTP停止七份测试。Web/前端协议继续扩展现有测试，各文件≤500行。

每片定向命令按当片创建的测试运行；整体至少运行：

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q naiyue_test_agent scripts tests
.venv/bin/python -m pip check
npm --prefix frontend test -- --run
npm --prefix frontend run build
git diff --check
```

另用临时SQLite做同进程父子审批/必要保存故障验证，不触碰真实会话库；事件断言放测试末尾，检查器异常不能被生产观察隔离吞掉。安装/测试Windows的命令与实际结果属于WIN1，不能用macOS结果代替。

回退风险：S1未改DB、事件或Web协议，但带subagents节的配置无法被旧版本读取；回退代码时须同步移除该新节。后片Web客户端/服务端须成对回退，不能用旧UI提交新审批ID。跨版本续聊以ready会话为基线，不承诺活动子任务/子待审批驱动者跨升级或重启复活；根审批沿092复核恢复，不能扩大为任意旧版本批次可恢复。禁止默默重放工具来“修复”版本不匹配。


## G. S1的具体实现计划：定义、登记与查询

本节已按用户授权实施声明式配置和角色定义管理。S2增加编译与实际运行，S3才开放模型可调用的工具及角色索引；S1没有空runner、agent=None或缺少驱动者的可调用工具。角色共享定义和每次任务创建独立实例的最终方案保持不变。

### G.1 文件与方法

| 文件 | 新增/修改 | S1实际调用方 |
|---|---|---|
| 新增`tools/subagent.py` | 冻结dataclass `SubAgentSpec(name, description, system_prompt, tool_names, model_id=None, max_model_calls=None)`；__post_init__统一结构校验，只定义元数据 | 可信代码构造、配置解析、SessionSubagentControls查询；避免配置与API两套字段校验 |
| 修改`tools/__init__.py` | 导出SubAgentSpec | 本地Python登记代码；不改builtin工具数量/活动集合 |
| 新增`application/session_subagents.py` | `SessionSubagentControls.register_subagent(spec)`、`get_registered_subagents()` | AsyncApplication直接调用；复用SessionService已有catalog/tool_registry/lock |
| 修改`application/session_service.py` | 混入SessionSubagentControls；构造时加一个App级`_subagents={}` | 同App所有会话共享一张表，不在每个session中复制 |
| 修改`application/async_application.py` | `async aregister_subagent(spec)`调用_check_open后调用短同步登记；`get_registered_subagents()`检查owner loop后查询 | 可信Python调用；CLI桥调用 |
| 修改`application/cli_async.py` | `register_subagent(spec)`→_submit(lambda: _app.aregister_subagent(spec))；`get_registered_subagents()`→_submit(_app.get_registered_subagents) | 终端线程；只回到已有App loop |
| 修改`tools/settings.py` | ToolSettings.subagents为tuple[SubAgentSpec, ...]，缺省空；before validator将TOML表/数组转为冻结定义，拒绝未知字段/重复角色 | 现有load_tool_settings，一次启动快照；无效定义日志及固定异常，不走额度回退 |
| 修改`application/__init__.py`、`async_application.py`、`async_persistent.py` | _assemble_application在建根图前，循环调用同一register_subagent；两个builder传入settings.subagents | CLI/Web经既有builder自动接入；依赖工具/模型错误阻止启动，沿现有关闭链清理 |
| 新增`tests/test_subagent_registration.py`、`tests/test_subagent_settings.py` | 登记、配置、真实CLI桥/入口和持久App/Web接线测试，沿asyncio.run/现有async_test；不新增测试基础设施 | 实际AsyncApplication和现有模型/工具fixture |
| 修改`docs/examples/settings.example.toml`、README及交接 | 带注释的角色样例和公开示例启发的提示词；明确尚不能执行 | 用户编辑同一settings.toml，不修改真实用户配置 |

S1不改模型适配、RunContext、权限/审批、工具调度、事件/序列化、数据库或前端；只扩展配置读取及样例，不写真实用户配置。不创建`agent_runtime/subagents.py`或子任务实例。方法使用同步短算法不等于恢复同步Agent执行面。

### G.2 数据、校验、发布

可信代码用法（S1已支持，下面是示例输入）：

```python
spec = SubAgentSpec(
    name='tester', description='检查修改并运行测试',
    system_prompt='根据任务检查代码，用实际测试证据报告结果。',
    tool_names=('read', 'bash'), model_id=None, max_model_calls=None,
)
await app.aregister_subagent(spec)  # 返回None
roles = app.get_registered_subagents()  # 返回tuple[SubAgentSpec, ...]
```

`register_subagent`按以下顺序执行：

1. SubAgentSpec.__post_init__检查name沿现有工具安全名称格式；description/system_prompt为非空字符串；tool_names必须为无重复字符串tuple，可为空。max_model_calls为None或严格正整数，拒绝bool；model_id为None或非空字符串。配置与代码构造均走这一结构校验；register_subagent还须拒绝非SubAgentSpec对象。
2. 定义结构校验明确拒绝tool_names中包含subagent；这只约束角色声明，S2仍须在实际委派入口验证一层限制。
3. 复用`self._tool_registry.select(spec.tool_names)`确认原始工具已登记。这里不要求它们在父会话的active_tools中；子角色工具列表与父活动列表独立。
4. 显式model_id通过已有`self._catalog.get`验证；None保留为“实例启动时继承”，S1不提前改成默认模型。
5. 工具/模型检查结束后，在已有`self._lock`下检查重名并一次性写入`self._subagents[name] = {'spec': spec}`。不在会话锁内持有ToolRegistry锁、不发请求、不读文件、不写DB。任何失败均不改变表，重名不覆盖原定义。
6. `get_registered_subagents`在同一锁下按登记顺序取定义的tuple。frozen定义及tuple工具名不可原地改写，不把内部可变dict交给调用方。

S2扩展注册为“校验→App使用共享saver/callbacks编译子图→锁下复核并发布{spec, agent}”；S1不构造占位graph，不冻结尚不存在的子实例路径/模型。S3的ToolSet消费者才从已登记定义生成仅name/description的模型短索引，工具未活动时不展示。

### G.3 改前/改后链与失败例

改前：可信代码`await app.aregister_tool(ToolSpec(...))`→SessionControlMixin.register_tool→ToolRegistry；没有角色登记表/API。已注册工具与会话active_tools分别决定模型可调用的工具。

改后S1：可信代码构造SubAgentSpec→`await AsyncApplication.aregister_subagent`→`SessionSubagentControls.register_subagent`→现有ToolRegistry.select/ModelCatalog.get→锁下发布定义→`get_registered_subagents`取出同一冻结定义。CLI通过既有_submit执行完全相同的链。根模型调用链不变，不因登记就调用子模型。

失败例：输入`tool_names=('read', 'missing_tool')`→ToolRegistry.select抛ValueError→登记方法记录固定事件及安全error_type后原样上抛→角色表保持原样。不生成ToolMessage、不暂停审批、不主动停止根run；运行时受控调用者应处理请求异常，顶层不处理仍可能退出整个服务。显式未知模型沿ModelCatalog.get的现有异常传播，重名在发布点拒绝。启动配置登记失败使builder失败，沿原有catalog/storage清理链关闭；不能带着无效必要角色继续启动。

### G.4 第一片验收

- 正常定义可登记、返回None，查询返回同一冻结spec；顺序明确；支持空工具列表及model_id/max_model_calls省略。
- 非法字段、重复/未知工具、未知模型、子角色包含subagent、重名均失败且无部分发布；原定义不被覆盖。
- 同一App中不同session看到同一角色集合，两个App互不影响；异步持久App继承同一API，不增加DB角色记录，重启从当前配置或可信代码重新登记。
- 登记过程模型调用为0、根消息不变；不启动Task/子图、改变活动工具或请求审批。该片不把模型是否可调用subagent当成已交付。
- CLI方法经真实线程桥登记/查询，App关闭或跨owner loop按已有_check_open拒绝。
- 配置读写前后一致：当前App保持原定义，新App读到新增工具/提示词；默认不内建假角色，不自动启用新工具；CLI/Web真入口注册可查询。非法配置安全报错，启动依赖失败后资源能重新打开。
- 先运行新测试见到缺少Spec/API等预期红灯，再实施。定向命令：`.venv/bin/python -m pytest -q tests/test_subagent_registration.py tests/test_subagent_settings.py tests/test_tool_registry.py tests/test_dynamic_tools.py`；最终按上方全量命令回归。

### G.5 配置合同及装配位置

继续使用用户目录`~/.naiyue-test-agent/settings.toml`，新增顶层`[[subagents]]`，字段与SubAgentSpec同名。tool_names写TOML字符串数组；model_id/max_model_calls省略表示None。配置缺失或无本节默认无角色，格式/结构/重复角色非法直接启动失败，不沿预算配置回退；日志不含提示词、原配置值或原异常正文。ToolSettings直接用已冻结定义，TOML列表只在边界转tuple；不另设配置注册表、插件扫描或DB同步。

配置公共异常边界用`from None`隐藏含原始输入的Pydantic异常链；只对str(error)或普通日志做脱敏不够，Web/Uvicorn启动会打印完整traceback。新增格式化异常链回归曾复现2失败/11通过，单点修正后转绿；真实Uvicorn lifespan故障探针确认仍明确启动失败、日志不含合成提示词且未创建DB。

启动顺序：既有load_tool_settings→已登记启动tools→ModelCatalog→SessionService→共享_assemble_application逐项登记settings.subagents→建根图→返回App。本次登记不执行子模型/工具。配置引用自定义工具时，可信启动代码须先把ToolSpec放入builder的tools参数；之后再在角色配置中引用名称。默认CLI/Web只有内置工具，不因配置而import未知Python。

配置启动时读取，编辑工具列表/描述/提示词/模型后重启生效；运行中仅可信代码能新增角色，重名不覆盖。热替换、动态扫描角色文件或新配置路径均不在S1范围。description说明父应何时委派，system_prompt说明子的职责、步骤与返回要求；仅subagent是工具名，角色名作为agent参数。

### G.6 S1实际验证与边界

新增两份测试共55项。先观察缺少SubAgentSpec的2处导入红灯，再最小实现；安全复核增加完整traceback断言，确认2失败后修正配置异常公共边界。最终`.venv/bin/python -m pytest -q`（PATH加入本机rg/fd工具目录）为2001 passed in74.49s；前端40 passed、TypeScript/Vite186模块构建成功。compileall、pip check、diff、305份Python AST/≤500行及文档本地文件链接检查通过；真实CLI/Web/内存/SQLite入口的配置登记已验证。

没有真实供应商请求、子任务执行或Windows原机证据；本轮不修改真实用户配置、不增加DB角色记录或可调用工具。S2开始前需先向用户展示实例和图执行的具体调用链，取得该片授权。

## H. S2执行细案（2026-10-17，用户已授权）

范围：登记编译、独立实例/历史、冻结模型、原生异步执行、有界content/artifact、配置化模型调用次数。人工审批驱动/正式工具开放仍在S3，来源UI在S4，完整停止/保存竞争在S5。只新增运行层subagents.py生产模块，扩展S1模块及既有装配/上下文入口，不新增Scope/Session/Registry类或同步执行器。

### H.1 接口和所有者

- `SessionSubagentControls.configure_subagent_runtime(checkpointer, result_settings)`由共享_assemble_application传入既有保存器和tools.subagent设置；直接传依赖，不加编译回调或整个App。register_subagent校验结构/工具/模型后，锁外build_subagent_runtime、锁下复核重名并发布`{spec, agent}`；失败无半发布，动态API仍走同一方法。
- `agent_runtime/subagents.py::build_subagent_runtime`复用_build_runtime_agent，保留ToolSet/权限/批次/授权/反馈/输入/压缩及响应门禁，不安装根steering/follow-up/根终局hook。可选ModelCallLimitMiddleware(thread_limit=spec.max_model_calls, exit_behavior='error')；省略时不安装次数限制。
- `prepare_subagent_instance`验证活动根run、parent context/state中的批次、调用、参数及执行许可；ID由session/batch/call稳定派生，拒绝重复启动。App级_subagent_instances按(session_id, agent_path)持有模型/工具快照、child_thread_id、seed消息ID、父身份/状态/独立RunControl；实际入口也强制一层。
- `SessionSubagentControls.ainvoke_subagent`是显式Runtime端口，接agent/task及可信parent_context/parent_state/parent_tool_call_id。应用建实例并创建`asyncio.Task(..., context=Context())`，显式传子服务/工作区/资源/真人投影，不继承父LangGraph scratchpad/checkpoint namespace。只await本子任务；外部取消须等子任务收尾、还原父RunServices，不起新进程/事件循环。
- `ainvoke_subagent_step`绑定独立RunServices后，以durability='sync'/child_thread_id执行child.ainvoke，返回原图状态，含真实interrupt。S2工具不入builtin/默认集合；可信代码提前启用而出现Ask时，保留child checkpoint/awaiting_approval，明确PermissionMechanismError，不冒泡到父审批、不自动批准或切NoAsk。S3用真正等待/恢复替换该阶段保护。
- `_finish_subagent_result`只处理无interrupt的最终AI回复，复用content_text投影，默认2000行/65536字节、保留完整前行；超长首行取UTF-8字符前缀并标截断。artifact含身份/模型/状态及可用usage，未知为None；不复制完整子历史、不额外总结。现有Shell格式器是尾部语义，不能复用为报告头部截断；格式化留在结果提取函数。
- `SubagentModelFailureMiddleware.awrap_model_call`只包实际模型handler：清旧槽，已分类请求失败记录原异常对象/category/reason再原样raise；runner只有异常身份一致才转固定ToolException。精确捕获ModelCallLimitExceededError，其余机制/控制错误保持身份；subagent公共反馈只转换明确ToolException，不按TimeoutError名字猜来源。

### H.2 消费者迁移及配置

RunContext增父身份/异步端口，RunServices与RunObservation.bind显式传端口，make_invocation_context仍准备根上下文。子上下文由单次step准备，不携父publisher/turn/控制观察器，防止S4来源接好前混入根事件；contextvar finally恢复。

取消测试复现了独立Task的所有权边界：父图返回取消不代表干净Context中的子Task已经退出。S2必须在根_afinish_run释放会话/保存前调用settle_subagent_drivers，对该root run仍活动的子Task取消并等待收尾；保留根原异常。它只保证本片已有执行Task的收尾，不提前实现S3的审批Future或S5完整停止竞争。

实例表仅服务活动根run：报告提取/失败退出后finally释放driver，避免已完成Task.result持有完整子历史；根run终结时先取消并等待残留活动driver，再删除该run的实例记录。持久历史继续由child_thread_id对应checkpoint保存，删除运行记录不删除checkpoint。S2的awaiting_approval在根失败后同样释放内存记录，checkpoint的真实interrupt保留；S3等待审批期间根run仍活动，不提前触发终结清理。测试捕获当时实际创建的实例来验证模型/状态/历史，不能要求生产代码为测试长期保留记录。

现有_RecordToolCall对handler统一await_settled会屏蔽对子调度任务的取消。仅subagent调度入口改为直接await，子图内叶子工具IO仍沿现有await_settled；不能把纯等待当作不可取消的副作用。外部取消测试以事件确认进入模型，再取消并检查子Task已结束，不依赖极短时窗。

resolve_model加可选agent_path；_ModelSelectionMiddleware._prepare和_ContextCompactionMiddleware._arguments同时传路径。根低层callback、测试/脚本同步迁移为接第二路径参数，不按签名探测保留兼容分支。SessionControlMixin扩展活动工具/权限的root-only分支；项目指令使用同一根run准备的快照，子不消费根steering。

子Auto审查从应用显式提供的父真人消息投影取人类意图，不把delegated_task/父助手/工具结果当真人授权。Deny、模式、session_allow、保存批次及执行许可沿现有合同，启动角色不授予其内部动作。

工具层SubAgentInput/create_subagent_tool使用现有build_tool_schemas注入runtime；coroutine、parallel/unknown、content_and_artifact，先require_tool_permit再委派。新增SubAgentOutputSettings及tools.subagent.max_result_lines/max_result_bytes。max_model_calls仍在[[subagents]]，正整数或省略，无隐藏默认轮数上限。同步样例/README/Spec，不改真实配置或依赖。

### H.3 链路和测试

正常：登记→编译后发布→父测试图subagent调用→权限/授权→工具coroutine→prepare_subagent_instance→干净Context中的step→子模型/read/回填→报告→有界content/artifact→父同ID ToolMessage。子历史在独立thread，默认工具集合仍不含subagent。

失败：叶子工具错误留在子图；已识别子模型请求失败/超限转父同ID error，无原异常正文；模型门禁/压缩/保存错误、GraphBubbleUp/UserStop/CancelledError保持身份。Ask保存真实checkpoint并受阶段保护，不声明审批恢复已完成。

先写tests/test_subagent_execution.py及按职责拆分测试：配置max_model_calls实际生效/省略不限、N次终局/N+1零请求、实例独立计数；同角色并行历史/thread隔离；根切模型与子冻结、压缩一致；工具真实授权/普通错误、真人投影；编译失败不发布；内容块/UTF-8/行字节预算/artifact；异常身份/父RunServices还原；真实内存/SQLite共享saver隔离。现有S1零模型/工具调用、定义身份/顺序/失败清理断言保留，编译无外部执行。审批核心仍由S3验收。


### H.4 S2实施与验收记录

2026-10-17：H.1—H.3已接。新增agent_runtime/subagents.py，扩展既有角色模块/装配/上下文/权限投影/反馈与观察；没有新DB表、依赖、子进程或同步Agent入口。登记只编译不请求模型；S2工厂尚未加入builtin。模型选择与压缩同时读取agent_path，原单参数resolver的现有测试/探针均迁移，不按签名探测保留兼容路径。

新增三份S2测试共26项，与S1合计81项。初始导入缺实现产生预期红灯；额外红灯分别验证了取消被屏蔽、子并发值未传递、父permit绑定不完整及终结实例/Task常驻，均由对应生产修复转绿。测试捕获实际实例代替要求生产长期保存终结记录，历史/模型/状态/异常断言保持。

验证：五文件81 passed in1.55s；全量2027 passed in76.40s（/tmp/naiyue-086-s2-backend.log）；前端10文件40 passed，TypeScript/Vite186模块成功；compileall、pip check、diff、308 Python AST/≤500行通过。样例默认无角色、启用角色及显式12次上限均解析成功。全量另发现两条诊断测试的旧resolver签名，迁移后保留原异常身份/诊断断言。独立复核确认无残留架构/安全阻断。

只有本机框架/假模型、真实文件与SQLite证据；未发真实供应商请求、未验Windows原机，未修改真实用户配置或库。下一步只讲S3恢复核心、动态索引及正式工具开放；S3—S6未实施，当前Ask阶段保护不能表述成人工审批已可用。

## I. S3执行细案（2026-10-17，用户已授权）

范围：子当前批审批登记/投递/恢复、精确scope授权、动态角色短索引、通用工具纳入可注册内置集合。继续使用现有官方HITL与固定节点，不升级框架、不重入父工具、不改Web/前端协议；S4接交付，S5接完整停止/保存竞争。

### I.1 文件、接口及权威状态

- 扩展现有application/session_approval.py：PendingToolApproval保存子当前批的元数据、原pending事实、行审批ID、Future、pending/accepted/invalidated状态；SessionApprovalControls新增register_live_approval、pending_subagent_approvals、validate_approval_submission、submit_subagent_approval及失效清理。每个实例只持有当前对象，无独立全局审批表。审批对象由子返回结果及aget_state证明固定前置节点后登记，原批参数/subject不由提交者提供；不另向官方payload复制prepared_call，来源由应用记录及S4事件承载。
- 现有prepare_resume增加keyword agent_path='root'；校验决定及remember的短算法供根/子共用。根pending_tool_approvals和aresume_tool_approval的位置参数/返回合同保持，严格is_pre_tool_approval判据保持。S3新增pending_subagent_approvals查询分组，CLI薄桥同名；S4聚合到正式交付时再迁移旧UI消费者，不提前修改既有HTTP结构。
- 子提交asubmit_subagent_approval(session_id, *, approval_batch_id, decisions, origin_run_id, remember_approved=())：decisions为[{approval_id, decision:'approve'|'reject'}]，必须完整且无重复/额外字段；以保存顺序归一化。remember按approval_id，只允许本批批准行。锁下核对活动根run/实例/当前批/状态/停止标志，保存决定与精确grants，锁外set_result；只返回accepted及原root run_id，不调用图。request_id幂等留S4既有传输层，应用重复提交明确拒绝。
- session_subagents.py将ainvoke_subagent的Task改为驱动整个_subagent_steps：首次执行→若返回interrupt，aget_state验证并登记→await本对象Future→同实例恢复；无interrupt才格式化终局。Task在干净Context中，首次seed只加入一次，恢复不新建实例。异常/取消finally作废并释放自己的未决对象，根仍cancel/gather后释放实例，不遗留Future/driver。
- agent_runtime/subagents.py::ainvoke_subagent_step增加resume/grants输入，恢复使用Command(resume={interrupt_id:官方decisions})及原child_thread_id；RunServices/context按片段重建，冻结模型/工具、原真人投影及RunControl不变。官方ModelCallLimitMiddleware的thread计数跨恢复延续，Permission/Auto准备不重跑。
- AsyncApplication增加查询/投递，AsyncCliApplication只以原_submit桥接；SessionService混入SessionApprovalControls并复用公共决定校验，保持≤500行。
- ToolSetMiddleware.prepare_tool_view在subagent活动时从App当前角色表捕获name/description短索引；model_inputs复用同次快照供提示词/预算/模型，完整system_prompt不进父索引。根装配显式注入现有get_registered_subagents方法，子不暴露委派工具；动态登记只影响下一模型请求。tools/builtin.py纳入通用factory，沿现有活动集合决定是否给模型；不添加角色专用工具。

### I.2 正常与失败链

父并行A/B→A Permission保存Read allow/Bash ask→官方HITL保存后返回→验证checkpoint→登记A当前批/Future→父工具持续await、B继续→提交A批→校验/精确许可→只唤醒A驱动→Command恢复A原thread→同批工具执行一次→子最终报告→父同ID结果。串行prefix/A/tail沿原AsyncBatchPlan，审批期间tail仍普通排队。

拒绝仍由官方HITL生成工具错误交子模型；跨会话/错run/旧批/错行/不完整/重复/remember拒绝行不发许可、不唤醒；执行前真实目标复核不省略。保存/模型门禁/取消保留S2身份，局部保存故障隔离及根停止唤醒竞争仍留S5。根终结必须清理本轮拥有的全部等待对象，审批事件非S3授权权威；S4才发布带来源的交付事件。

### I.3 测试及迁移

先写新test_subagent_approval_resume.py及test_subagent_batch_resume.py，看到缺接口/未能登记的预期红灯；覆盖A获批且完成时B仍运行、同图同inner-call-ID互不批准、Read与Ask整批阻断、拒绝/多轮审批/计数延续/真人Auto一次、精确许可及remember会话范围、串行prefix/A/tail与父入口一次、SQLite状态、取消无孤儿和错误提交不改变pending。另写动态角色索引测试，验证新增下一请求可见/压缩与模型同索引/停用不显示/不含完整角色prompt及builtin注册。S2“Ask无驱动”和“非builtin”阶段测试迁移到新入口，保留checkpoint/零执行/根未伪造pending等行为断言；既有八工具集合断言增加subagent，不能删除其他工具验证。无conftest或通用测试生命周期改造。


### I.4 S3实施与验收记录

2026-10-17：I.1—I.3已接。S3仅扩展10个现有生产文件，无新生产模块/依赖/DB结构/Web协议/前端变化。通用factory已加入builtin，角色表仍App单份，实例仍只持当前审批对象。根API有实际正式消费者而保留；子新查询/投递及CLI桥是本片实际入口，S4再接正式交付。子checkpoint/interrupt/PreparedToolCall一致性证明通过后才登记；源事实不重复塞进官方action payload。

新增四文件23例，先观察缺注册方法、角色索引和builtin的预期失败；S1/S2阶段测试迁移到本片真实行为，原历史/配对/零执行/清理断言保留。已有三处全量集合断言及其他相关枚举精确加入subagent，Shell活动选择和其他工具行为断言保留。角色测试helper为构造不同工具集合而支持覆盖默认字段，不改conftest/生命周期。补测曾假设暂停时官方count已更新、每轮均做主动压缩；源码确认count在后续after_model节点增加、主动压缩只在invocation入口，改为在正确阶段检查真实响应/最终精确计数，以及实际压缩操作与对应模型同prompt；未改变生产行为掩盖错误。

实际验证：S1—S3九文件104 passed in2.36s；全量2050 passed in76.44s（/tmp/naiyue-086-s3-final-backend.log）；前端10文件40 passed，TypeScript/Vite186模块成功。compileall、pip check、312 Python AST/≤500行、diff及当前文档本地路径通过；当前样例默认/取消角色注释/显式12次上限均解析成功。独立架构及安全复核无残留阻断。

具体覆盖：A在B运行时获批/完成且父仍等待；同角色同inner-ID两批不串；一子两Ask完整提交、乱序按保存顺序应用、仅批准项可记住；拒绝交子模型；父启动grant只消费一次；子多批/次数N与N+1、seed一次；prefix/A/tail；错会话/run/批/行/额外字段/重复决定不改变pending；Auto只审一次真人投影；SQLite同thread与父摘要；同会话临时规则跨父子但不跨会话；CLI桥投递在根等待时返回；外部取消Future/driver释放。

证据为本机真实框架/IO/SQLite及假模型，没有真实供应商、Windows原机或业务效果声明。当前不提供正式界面子审批展示/投递，用户停止/关闭的完整唤醒和保存竞争仍由S5完成。S4—S6未实施；不把新接口存在当成CLI/Web子任务产品已交付。

## J. S4执行细案（2026-10-17，用户已授权）

范围：带来源的子事件、子工具进度、统一分组审批查询、CLI/Web活动子审批及原root回执。沿用现有CSS Modules/审批面板/工具详情，不新建Agent树或历史浏览。S5保留局部保存故障隔离、单独停子和完整批准/停止/关闭竞争；为避免本片交付死锁，S4接根停止对当前子Control及审批Future的最小唤醒，CLI EOF/quit在join前处理子待审。没有独立停止平台或线程Agent。

### J.1 来源与观察

- runtime/events.py新增冻结AgentEventSource(agent_path, agent_id, agent_run_id, parent_run_id, parent_tool_call_id)，RuntimeEvent增source=None（根语义）；RunEventPublisher.for_agent返回共享根queue/handlers/sequence的AgentEventPublisher来源视图。公共_emit显式接纳该视图并保留session/子片段run的matches校验，不能只改bind而让真实model/tool事件被类型检查过滤。子view.matches检查子片段，事件外层run_id仍root，未另起序号。contracts不依赖Runtime。
- application/run_events.py允许注入既有stop_control；session_subagents为每个图执行片段装RunObservation，保留turn快照及同实例Control/原错误策略。runtime/subagents step接收显式services，沿ainvoke_with_message_observation；该函数增加可选durability以保持子sync保存。根不改durability。子build打开既有turn hook。
- 子中断先完成sync checkpoint并通过原S3证明、登记pending，再发布approval_requested。载荷增加batch ID及冻结行展示值（approval_id、tool/call/target/workspace）；CLI可直接显示，不在observer中反向调用同步CLI桥造成loop死锁。事件只是展示，失效时查询仍可取当前组。子普通completed/stopped/failed仅影响子投影，审批暂停显示awaiting_approval。

### J.2 统一审批与交付

- session_approval新增pending_approval_batches聚合paused_root与live_child；原根公开arun/aresume/查询保留真实消费者合同，S3子查询复用同一聚合。根组ID由session/batch/origin/保存items稳定派生，查询不改控制状态、不加DB字段，保存事实变化即换ID；行ID与模型call ID分开。
- 统一查询仅在根会话非busy时显示可提交根批；恢复中的保存pending不是未提交审批。根/子共用审批行完整性/唯一性/顺序归一化及remember校验；新增prepare_root_approval_submission把公开行ID映射回原根接口所需顺序/调用ID。批准目标只取保存事实。
- Web ApprovalInput强制approval_batch_id，ToolDecision改approval_id，增加remember_approved；删除旧HTTP字段分支，仓库测试/前端同步迁移。SessionView用approval_batches替代单组approval_requests/approval_run_id；kind来自服务端。根应用返回字段仍有原Python消费者，不等同旧HTTP兼容。
- WebService.approve在既有workspace/auth准入后，RunDelivery在同start_lock内先查request_id回执，再查当前组。live_child只调用asubmit并记accepted/原root回执，无新DeliveredRun/producer；paused_root按保存顺序走原aresume及新root run。重复request同体返回原回执，异体冲突、新request旧批stale。
- 新增web/agent_projection.py只负责当前子工具的有界交付投影，避免DeliveredRun继续堆职责；frontend/agentEvents.ts承担对应浏览器投影。DeliveredRun/StreamingMessageProjection消费所有外层sequence，子消息不进根历史，子run_finished不改根status/done。RunView增加agents（按agent_path），只保留子状态/当前工具参数、结果及进度，无子聊天历史副本；不同实例同call ID隔离。事件帧和view沿现有限额/明确降级。

### J.3 CLI/前端

- cli.py已有486行，新增application/cli_approval.py集中CLI分组展示/命令解析/投递与退出等待，避免主文件超限；只放真实CLI职责，不复制控制状态。CliEventDisplay消费来源与冻结请求行，子消息不打印成根回答、子终局不置根terminal；子进度按scope隔离。
- 运行和输入独立；非TTY普通请求保持按序队列，处理后续审批命令前先推进排队请求，遇当前未决审批即停推进；CLI记录刚提交根批ID，等待旧批从可提交查询退出，覆盖恢复线程尚未开始的窗口，审批命令可直接处理，不能先join活动父。多批必须选ID，单批保留便捷approve/deny/review。EOF/quit收尾不遗留新审批Future；正常管道输入的已有逐轮行为保持。
- frontend/types/events接source与agents；全局sequence继续递增，子工具/终局只更新对应scope。Conversation/ToolCall在父委派详情内显示子状态和工具进度，不追加子聊天。
- ApprovalPanel按batch ID+approval ID隔离选择，可提交本会话“不再询问”；App显示所有组，live_child可在busy时提交。useConversation收到approval_requested即异步刷新当前组，失败/重连查询校准；同root回执仅刷新组，不重置watch/SSE序号，根新run仍原路径。旧组按钮不能批准下一组。

### J.4 验证与边界

先新增来源/投影/活动Web审批测试及前端source/分组用例，确认红灯；补CLI交互/管道审批、EOF、根旧审批/恢复、幂等、错session/run/batch/行、两子同call ID、子停止/终局不终止根、事件投递失败不影响已保存pending、原SSE连接/序号及frame/view预算。使用事件同步、真实框架/SQLite/HTTP及假模型，不调用供应商、不写真实配置。验证桌面与375px浏览器，包括目标长路径和多批布局。所有已有根事件/CLI/Web测试迁移保持身份、顺序、配对、失败及安全断言，不以skip或宽松断言兼容旧HTTP。完整S5停止/保存竞争不提前宣称完成。


### J.5 S4实施与验收记录

2026-10-17：J.1—J.4已接。新增两个Python生产模块（CLI审批适配、Web子投影）及frontend/agentEvents.ts；没有新依赖、DB schema、Agent进程或独立SSE。子图每片段绑定来源视图/观察器并保留sync durability；最终报告在子观察finalize内校验，空报告发布failed后再以ToolException反馈父模型，不能先发completed。根停止最小唤醒及CLI EOF/quit收尾已接，单独停子、完整批准/停止/关闭与保存故障竞争仍留S5。

新增三份Python测试10项，前端新增5项；其中最后一项先复现__proto__调用ID在普通对象中丢失，再改为无原型工具字典，保持模型ID为普通数据。来源缺失/根被子终局结束、审批通知未接、CLI等待子审批阻塞等红灯后接实现；审查新增空报告错误终局、排队请求审批提前丢失、旧根批在恢复线程启动前被第二条批准重选的确定性红灯，分别由实际生产修复转绿。真实浏览器再发现_emit类型过滤导致子model/tool开始结束事件丢失：先增强HTTP断言复现outcome缺失，再显式接纳来源视图。单个新通知故障用例为实现后的边界验证，不冒称全部新增用例均先红灯。

迁移所有实际旧HTTP消费者：前端、Web/恢复测试、probe_web_tools；保留原认证、顺序/结果配对、异常身份、拒绝/未执行和幂等断言。根Python返回/查询/恢复仍有测试及探针消费者而保留，不做旧HTTP兼容。CLI纯展示旧_print_pending_approvals无消费者后删除，双Shell转义测试迁到分组展示。测试事件fixture补source=None；原StreamingToolModel的_astream丢弃list content，而_agenerate保留，迁移至真实流式后补齐该测试替身输出，内容块/用量/无思考泄漏的预期断言不变；不修改conftest或生命周期。

实际验证：全量2060 passed in77.91s（/tmp/naiyue-086-s4-verified-backend.log）；前端11文件45 passed，TypeScript/Vite187模块构建成功。compileall/pip check、317 Python AST/≤500行、diff通过。架构及安全独立复核发现的问题均已补回归并关闭。浏览器在临时SQLite、假模型、真实Read/原生HTTP/SSE下验证1280×900和375×812：两子同inner-ID各自待审、先批准A而B继续待审、A真实工具结果/进度、拒绝B后根完成、等待时停止及重连查询；控制台无warn/error。多面板窄屏挤出停止按钮的布局复现后，用限高滚动及长路径换行修正；最终375px无横向溢出、停止按钮可见。

证据仅为macOS真实框架/文件/SQLite/HTTP/浏览器及假模型；未调用真实供应商，未验Windows原机，未修改真实配置/用户库，未提交push。唯一下一步先讲S5停止/必要保存与关闭，用户确认后实施；S6仍终检。

## K. S5执行细案（2026-10-17，用户已授权）

范围：单独停止活动/待审批子实例、根停止/故障/关闭拥有关系收尾、停止与批准竞争、已暂停根批的受控停止、子checkpoint故障隔离及正式CLI/Web操作。S6仍负责整体终检，不增加后台、多层、重试、DB元数据或依赖。

### K.1 当前链与文件职责

改前request_stop仅标根并由stop_subagent_waits唤醒；根失败收尾直接cancel子driver；aclose先等任务，未先通知停止；子保存异常和其他机制异常一样上抛。S5已将这些入口迁入同一scope停止/settle路径，删除被替代的最小唤醒方法，不保留双实现。G—J保留各片当时的验收边界，现行停止/保存语义以K为准。

- application/session_controls.py扩展request_stop(..., agent_path='root')及内部_request_scope_stop(..., reason='user'|'failure'|'close')：锁下核对根run、实例和phase，选目标、标stopping/作废pending，并用RunControl.mark_requested提交真实停止信号；锁外request发送模型取消通知并唤醒Future。可信reason不从HTTP/模型传入；内部failure/close不发布伪用户control_accepted。根标记阻止新委派；已finalize/终结的子停止拒绝，重复活动停止幂等。
- application/session_subagents.py保留唯一driver；实例增加phase与settled事实，execute/awaiting可停止、finalize不能新停。register_live_approval在停止已发生时不登记孤儿Future；真实interrupt仍由原driver立即走停止恢复。ainvoke_subagent的取消先请求其子停止并await_settled驱动收尾，再原样raise取消；根失败/关闭另广播，局部A失败不唤醒B。settle_subagent_drivers等真实driver结束，assert_subagents_settled在根必要保存前核对并释放记录。
- agent_runtime/run_control.py::request(reason='user')仅记录第一次可信来源；stop_tool_message、active_stop及batch停止guard使用同一来源生成固定未执行说明。UserStop/外部CancelledError/GraphBubbleUp身份保持；已放行IO沿既有settle，不伪造未执行。
- agent_runtime/subagents.py内新增SubagentCheckpointSaver薄适配（不新增散落模块）：仅代理真实用到的aget_tuple/aput/aput_writes及版本/config/serde；共用原backend/SQLite连接，不自行开库或关共享资源。保存边界普通Exception记录安全日志后以现有ToolException反馈，保留原cause，并用现有FailureDescription标明checkpoint来源；控制异常不上转换。get_next_version属于机制算法，仍原样上抛。只在子建图使用适配；根保存语义不变。原“子graph.checkpointer与根对象is同一”测试迁移为严格检查backend相同及同thread读写事实，不把适配对象当独立数据库。
- async_message_observation的baseline失败仅对已标记的子checkpoint失败直接上抛，避免观察降级重新执行/重试必要读取；其他原观察降级不变。子正式完成/停止前核对同步保存后的真实state无next/interrupt；未确认不能发布completed/stopped。
- application/session_service.py::_afinish_run先在原异常存在时请求scope故障收尾，再settle/assert/根必要保存；次级收尾异常不覆盖主异常。现有_resume_steps增加内部stop分支，复用原保存/资源/Command/配对，不伪造reject或发grant；新增astop_tool_approval及异步事件入口、CLI薄桥。停止恢复先置可信control，原HITL停止guard先于官方决定校验。
- AsyncApplication.aclose阻止新操作后，先停止活动根/子（reason=close），再等待owned tasks/必要保存，最后关catalog；子不能关共享客户端。Web关闭沿同一可信关闭来源处理。CLI交互EOF/quit及异常退出先停活动根再join；非TTY管道完成已提交普通请求，无剩余审批输入时唤醒子等待。单独停止用:stop <agent_path>，不join父worker。
- web/schemas/control增加仅stop可用agent_path；暂停根审批使用独立严格ApprovalStopInput（request_id/origin_run_id/approval_batch_id），复用start_lock/回执/同一producer入口。child仍使用原root job；根paused恢复新root run。web/app/service/run_delivery、CLI :stop [agent_path]及根待审停止、frontend/api/useConversation/ToolCall/ApprovalPanel同步接线；事件source停止只更新该子，不能把root设stop_requested。DTO/序列化/失败提示/测试一起更新。

### K.2 正常与失败链

停止A：request_stop(sid, root_run, A路径)→锁下A状态/pending失效→control.request(user)/唤醒→唯一A driver用原interrupt恢复→已有停止guard补未执行消息→子sync保存/无未决任务检查→同ID父工具error；B继续，父等本批收齐。批准先接受则历史决定保持accepted；停止只阻止尚未放行项。停止先接受则提交拒绝。晚到最后一步的停止以真实run_stopped/保存结果定终局。

A保存失败：子保存适配识别真实IO边界→安全ToolException(cause=原异常)→子failed、释放自身pending/driver→父同ID错误，B不变、父仍可保存。模型门禁/调度/权限机制等原异常保持上抛。根保存失败不倒改已确认子结果。根故障/关闭才广播并等全部拥有任务收尾；故障/关闭提示不能冒充用户停止。

### K.3 验证

先新增test_subagent_stop.py、test_subagent_persistence.py及必要的关闭/竞争分文件测试，确认缺接口/既有错误传播的红灯，再实现。覆盖两同名同ID子独立停止/批准、错session/run/path、finalize拒绝、批准前后停止、已开始IO保留结果、停止与保存/登记交错、根失败/外部取消/close无孤儿、子保存故障B仍可审批/父ready、根保存失败、通知失败、暂停根批停止无副作用/无Auto重跑及092恢复不回归。使用事件同步，真实内存/SQLite/HTTP与假模型；迁移S2阶段保存异常测试到已确认S5语义，保留cause/零泄漏/实际保存断言；不修改conftest或放宽断言。

### K.4 实际接线、迁移及验证边界

S5已接K.1—K.3，无新增Python生产模块、依赖或DB schema。唯一新增生产类是既有Runtime/subagents.py中的SubagentCheckpointSaver，只标识当前子图IO失败；SessionSubagentControls仍持有实例/driver/Future，RunControl仍持有停止事实。已删除S4临时stop_subagent_waits；未保留额外同步执行链或旧HTTP字段。

停止竞争的额外红灯复现：跨线程先标stopping、锁外request尚未执行，登记看到停止并用空决定恢复，官方HITL抛决定数量不匹配。现以RunControl.mark_requested在会话锁下提交Event/首个原因，request在锁外通知模型取消；状态与真实停止事实不再分离。回归明确延迟锁外通知，子仍正常停止、父继续，无工具副作用。failure/close仅是内部收尾来源，不发伪用户停止control事件，原控制事件顺序断言保留。

S2/S3阶段断言迁移：checkpoint对象身份改为同backend和真实thread读写；子checkpoint失败由上抛改为具体ToolException反馈，保留原cause/无秘密正文/父继续断言；外部取消改为协作停止并等driver结束，保留父CancelledError身份/Context还原、pending失效和零副作用。两条旧TTY测试原本在回答未结束就发quit；现在先等真实run_finished再quit，以继续覆盖原steering/follow-up顺序、恢复回答和完整保存断言。另用新CLI测试验证活动模型等待期间quit能结束，不放宽原断言。恢复门禁fixture补实际空闲事实busy=False；无conftest或共享测试基础设施变更。

测试既有红灯→实现，也有实现后边界回归，不能把所有新增用例称为TDD。真实IO已开始后保留success结果；最后checkpoint确认与停止交错以真实图结果决定终局；finalize阶段拒绝新停止。真实共享SQLite验证子保存失败后根ready可重新打开、根保存失败仍抛原异常且不改已完成子结果。浏览器使用临时库/真实Read/假模型：桌面停止A后B仍待审并可批准完成，375px停止入口可见、停止根后子全部收尾；console无warn/error，临时页/服务已关闭。本片新增七份Python测试24项和前端5项；最终后端全量2084 passed in77.75s、前端50 passed，TypeScript/Vite187模块构建成功；compileall、pip check、324份Python AST/≤500行、链接及diff检查通过。完整命令见handoff。

证据仅macOS本机框架/文件/SQLite/CLI/HTTP/SSE/浏览器；无真实供应商请求、Windows原机或业务效果证据，未修改真实配置/用户库，未提交push。S6仍须用户确认后实施，活动子驱动跨重启、后台、多层及已结束实例续跑不在本片承诺内。

## L. S6执行细案（2026-10-17，用户已授权）

范围：对S1—S5完整功能做组合验收、清理已无消费者的阶段分支、同步用户操作及文件地图。基线为未提交的S1—S5工作树；不重置、不提交push、不改真实配置/用户库/依赖，不新增生产类或执行平台。S6不是后台、多层、结束实例续跑、跨重启复活子驱动的实施授权。

### L.1 已有证据与本片缺口

现有approval_resume/batch_resume/approval_boundaries已覆盖A即时审批、B仍运行、同call ID隔离、整批决定、串行prefix/child/tail、Auto一次、临时记住与SQLite审批恢复；S5覆盖停止/保存/取消/关闭。保留并复用这些断言，不复制整套测试。本片新增组合边界：真实Read→Write→Read→报告→根ready→关闭重开→新委派；只有同步模型实现时，LangChain线程回退仍在原异步子驱动中，停止/父取消/关闭必须等线程真实结束；独立进程在子审批已保存或子文件效果已发生但父结果未保存时退出，重开只沿092整理根缺失结果，禁止旧子重放。

### L.2 文件、方法与清理

- 新增tests/test_subagent_integration.py：使用真实持久App、工具/SQLite与假模型，断言子完整历史不混入根、报告artifact身份、真实文件效果、根必要保存和下一次新实例。同步模型只用LangChain已支持的_generate回退，不建立同步App或第二条子驱动。
- 新增tests/test_subagent_restart.py：复用现有脚本内subprocess/os._exit模式，在明确边界退出；测试只写tmp_path。覆盖根恢复前后snapshot/ToolMessage/metadata、旧文件效果不重放、无模型调用/事件/Future复活、下一条新消息才能运行。进程工具超时仅为测试挂死保护，不成为生产工具期限。
- 扩展tests/test_subagent_settings.py核对当前注释样例的默认值，并去掉示例角色外层注释后实际加载；只更新样例中过时阶段注释，不改字段或真实配置。
- agent_runtime/subagents.py::ainvoke_subagent_step：当前只有SessionSubagentControls._subagent_steps一个消费者，始终传带publisher的RunServices；删除S2阶段services=None自建空服务及publisher=None直接ainvoke分支，保留明确服务传参、来源绑定和唯一观察执行入口。tools/subagent.py修正S3已纳入builtin后的旧opt-in注释。其余生产代码只在组合测试复现缺陷时最小修正，先记录根因/范围。
- 逐项搜索旧同步入口、stop_subagent_waits、父工具重入/空中断、旧HTTP审批字段及保存器旧身份消费者；实际根API、CLI薄桥、框架同步hook/SDK线程回退仍保留，不因同步命名删除。历史G—K和探针注明当时版本，主文/地图A—F/README只描述当前调用链。
- README、Spec086、文件地图、handoff、project-plan和future-work同步当前状态、操作与限制；配置样例仅核对既有key含义/加载/测试，不添加真实模型或角色默认。完成后将086从待实施顺序移到已完成，后续工具/模式UI、真实供应商、PyPI及WIN1分别列清。

### L.3 正常、失败链与验收

正常：open_async_persistent_application→启动登记/编译角色→arun→prepare_subagent_instance→唯一_subagent_steps→ainvoke_subagent_step→原权限/工具/保存→有界content/artifact→父工具消息→_afinish_run/根ready→关闭→arestore_session→下一次新实例。同步SDK仅在子图模型handler内部进入框架线程，App loop继续响应控制，线程完成前不释放driver或共享资源。

失败：独立进程在子前置审批或已发生文件效果的确定点退出→新App只读根history→092 classify_recovery/build_recovery_update→三次状态更新和必要metadata保存→根ready/旧工具结果未确认；既有已确认消息不改写、旧工具/Auto/模型不执行、子driver/Future不复活。下一条新输入才可调用新子任务。若测试暴露不符合合同的行为，先保留失败证据并作最小修复；纯组合验证不人为制造红灯或把绿灯补测冒称TDD。

验收运行新增定向及既有子/根恢复/CLI/Web/异步回归、全量pytest、前端测试/构建、compileall、pip check、AST/500行、文档链接和diff检查。当前无新增UI设计，复用S5浏览器证据；不发真实供应商请求、不把本机结果标为Windows/Linux原机或业务效果验收。

### L.4 S6实际结果与消费者清理

2026-10-17：本片新增两份测试、扩展既有配置测试，共7项新增用例；生产改动仅既有Runtime/subagents.py删除空services/无publisher分支、tools/subagent.py修正builtin注释。ainvoke_subagent_step只有_subagent_steps一个生产消费者，始终在RunObservation.bind内显式传入带来源服务；没有独立同步子循环或为旧测试保留的回退。现有正常执行/审批/保存/错误路径回归通过，未引入新生产类、模块、依赖或DB结构。

必要保留项及实际消费者：SessionService的短同步resolve/控制方法供middleware与CLI桥使用；StopBoundary/官方HITL同步hook仍由编译图调用；同步叶子IO/SDK在现有异步工具/框架线程内运行，不能按同步命名删除。根pending_tool_approvals/aresume_tool_approval仍供Python/CLI及探针调用，分组HTTP已迁移不等于根合同废弃。stop_subagent_waits、父工具重入执行账、空GraphInterrupt协调器、原独立同步App/Runtime执行器均不在当前主链；历史G—K/P1—P7不构成生产消费者。

新组合测试未发现功能缺陷，属于已有合同的验证补足；本片不虚构红灯或把全部补测称为TDD。正常测试检查真实文件内容、子叶子消息/报告artifact、根/子终局与无观察故障，重新打开后旧根历史不变、新任务获得不同实例身份；同步模型确认在另一线程、线程退出前不释放driver/根busy/共享关闭入口，父取消保持CancelledError。两处独立进程明确os._exit(91)后重开，根原任务配对为result_unconfirmed、原子图checkpoint不变、旧模型/工具不调用、审批Future不复活，已发生写入及执行次数均不重复。

验证命令及实际结果：

- `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` → **2091 passed in80.50s**，输出`/tmp/naiyue-086-s6-backend.log`。
- 新集成/重启及配置/动态工具接线四文件 → **49 passed in2.96s**；其中新增两文件6项全部通过，配置样例新增1项。
- frontend目录`npm test -- --run` → **11文件50 passed**；`npm run build` → **TypeScript/Vite187模块成功**，静态资源已重建。
- compileall、pip check、326份Python AST及每文件≤500行、当前文档本地链接、diff检查通过。架构/安全独立只读复核无新增发现，另跑根092恢复/崩溃/pending writes与子异常回归通过。

本机组合证据完成，不宣称供应商、Windows/Linux原机或业务效果已验收。S6未修改UI，浏览器证据明确沿用S5桌面/375px实测，不冒称本片重新浏览器测试。真实配置/用户库未动，未提交push；当前下一步为工具/模式管理UI的独立设计，真实供应商、PyPI、原生平台和后续能力按future-work分开安排。
