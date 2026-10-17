# Spec086 附录：文件、接口、清理与实施切片

日期：2026-10-16。属于[SubAgent Spec086](086-subagent-foreground.md)，仅设计、未实施。基于085迁移后的tools/tool_execution目录，不另留旧runtime/tools兼容路径。下表路径相对仓库根；目标新函数名可在实施发现真实重复职责后更新Spec，不能无接线地建占位文件。

本版已按独立子运行边界重写审批、停止和保存接点；原父中断重入执行账/空GraphInterrupt/等待B后开放审批方案删除。[P4/P5探针](../research/subagent-framework-probes-2026-10-16.md)验证框架机制，接口仍供审阅、不实施。角色及已确认的一层/前台/共用权限合同不变。

2026-10-16分工：正常排队期限移除及sync停止guard已由[090A](090-tool-wait-stop-and-author-contract.md)落实，工具进度归[091](091-tool-progress.md)且尚未实施。本附录相关行保留为086回归要求，不重复实施。090C另做四进程工具默认不限时及可选timeout。根会话[092恢复](092-session-restart-recovery.md)不扩展为子thread崩溃恢复；086继续声明该边界未交付。

## A. 新增三个生产文件

| 文件 | 必要职责、函数/数据 | 实际消费者 |
|---|---|---|
| `naiyue_test_agent/tools/subagent.py` | 冻结SubAgentSpec、公开agent/task输入schema、create_subagent_tool；原生BaseTool同步/异步薄入口，content_and_artifact | 可信代码登记、ToolRegistry、父ToolNode；通过context端口委派，不导入上层 |
| `naiyue_test_agent/application/session_subagents.py` | SessionSubagentControls：register/get、prepare_subagent_instance、invoke_subagent/ainvoke_subagent、_subagent_steps、assert_subagents_settled | 实例child_thread_id/唯一驱动者；局部失败只收尾该实例、转ToolException，不广播兄弟；根终止另作全体收尾；复用IOCall生命周期 |
| `naiyue_test_agent/agent_runtime/subagents.py` | build_subagent_runtime、invoke_subagent_step/ainvoke_subagent_step、SubagentModelFailureMiddleware、_finish_subagent_result | 单次独立thread图执行/观察/失败来源；显式共享saver；不拥有界面审批或另一套Agent循环 |

不新增AgentScope/SubAgentSession、子进程Agent、消息队列、独立重试/审批/事件平台、用于将来续跑的定位接口。新生产文件各≤500行；若实际复杂度超限，按明确职责拆分并先更新本表，不把业务混进通用utils。

## B. 扩展085既定模块及现有调用方

| 文件/组 | 修改点与函数 | 全链约束 |
|---|---|---|
| `tools/__init__.py`、`tools/builtin.py` | 导出SubAgentSpec/create_subagent_tool，内置集合纳入可启用subagent | 可信代码登记角色后才能成功启动；首版不自动加载项目Python/角色文件 |
| `tools/settings.py` | 增加subagent有界输出设置，校验2000行/65536字节工程默认建议 | schema/执行/反馈读同一设置；本轮不改真实配置 |
| `tools/permissions.py` | 许可与scope绑定；启动角色的权限对象含args.agent | 内部动作仍逐批审查，不把角色启动Allow当内部Allow |
| `agent_runtime/__init__.py` | RunContext增来源和委派端口；装配明确根/子hook选项 | build_subagent_runtime复用装配，不复制middleware列表/模型循环 |
| `agent_runtime/invocation_context.py`、`resource_invocation.py` | make_invocation_context及资源调用带agent_path/执行环境/委派端口 | 显式传入跨线程；项目指令/Skill读取权限与085一致 |
| `agent_runtime/run_services.py` | 子运行绑定独立观察与停止对象 | ContextVar finally还原，子不能直接沿用父services |
| `tool_execution/toolset.py` | scope选工具，compose_tool_prompt接短角色索引 | 下一请求看到新登记；子工具集合不含subagent，实际入口也检查层数 |
| `tool_execution/permissions.py` | 子审查取真人来源投影，保存scope、环境、批次决定 | 排除delegated_task/助手/结果正文；暂停恢复不重调Auto |
| `tool_execution/approval.py` | action_request附来源；停止优先检查；局部官方HITL | 支持多个interrupt；审批hook只消费保存对象，不重读目标 |
| `tool_execution/authorization.py` | 沿085消费精确scope许可，父启动与子内部调用各一次 | 删除原候选ExecutionEntry/entered-suspended放行器；父工具不因子审批重入 |
| `tool_execution/batch.py`、`async_batch.py` | 保留各Agent自身串并行与正常根HITL恢复核验；去正常排队60秒故障阈值 | 父prefix/A/tail持续活动；保留排队后/交给执行链前的停止guard并同步对齐，无空中断分支 |
| `tool_execution/feedback.py` | 根控制/全局机制故障继续放行；subagent局部失败在委派边界收尾后转具体ToolException，公共层生成同ID error | 不增加SubagentError包装层，不把所有子异常str输出；普通叶子工具保持085兜底 |
| `tool_execution/observation.py` | scope、子执行片段/暂停与实例终局区别 | 观察失败隔离，授权/保存不依赖投递；不再记录父重入作为正常子恢复 |
| `agent_runtime/active_stop.py`、`run_control.py` | finish_stopped_batch复用；request可选可信reason=user/failure/close；对应固定停止提示 | 默认用户控制不变；故障收尾不冒充人工拒绝/停止；UserStop/CancelledError身份及真实IO结果保留 |
| `agent_runtime/events.py` | 冻结AgentEventSource、RuntimeEvent.source与RunEventPublisher.for_agent来源视图 | 一条根队列/序号；匹配子逻辑run，外层流仍root run_id |
| `application/session_service.py` | 混入SessionSubagentControls/SessionApprovalControls；scope读取；当前实例/pending批次生命周期 | 现473行新增职责放分工模块；任务与审批对象不得在必要收尾前清空 |
| `application/session_tools.py`（085新文件） | resolve_active_tool_names/resolve_permission_context增加agent_path | 根默认值保留；子读自身工具名、共用会话模式/session_allow |
| `application/session_approval.py`（085新文件） | SessionApprovalControls、PendingToolApproval、register_live_approval、validate_approval_submission、submit_subagent_approval；根prepare_resume复用 | 只提交一个实例当前批；Future/Event唤醒唯一驱动者，不重新invoke父图；根/子共享校验 |
| `application/session_controls.py` | request_stop及内部_request_scope_stop；pending失效/控制/唤醒由用户停止、机制失败、关闭三处共用 | 失败不伪装用户停止；批准历史不改写，已有真实效果保留，根主异常不被收尾覆盖 |
| `application/async_session.py` | 根收尾先assert_subagents_settled；清理/取消传播覆盖拥有的子驱动者 | io_steps实现直接复用：审批等待critical=False，必要IO按原critical合同，不新增调度平台 |
| `application/run_events.py` | 子RunObservation来源视图、可选stop_control注入、turn快照/恢复及独立context观察 | 根会话生命周期仍由根持有；同实例control跨片段保留，观察快照不判授权 |
| `application/__init__.py`、`async_application.py` | register/aregister、get；submit/asubmit_subagent_approval回接收回执；保留根resume，paused_root停止入口 | 子审批后保持原root run/SSE；同步SDK与async facade共用步骤，不嵌套事件循环 |
| `application/cli_async.py` | 可信登记、活动子审批投递与根暂停停止薄facade | 复用既有唯一async loop，投递不等待父run结果 |
| `application/session_persistence.py`、`async_persistent.py` | 根finish前核对子驱动/未决审批/IO收尾及根自身必要保存，子图显式共用同saver；关闭先停止再settle | A失败已结束并有error反馈即已收尾；其自身保存未确认不自动阻断根；不加子active DB行，不恢复失败实例 |

## C. 事件、审批协议及界面最小接线

| 文件 | 消费变化 |
|---|---|
| `contracts/session_events.py` | ApprovalRequestedData增approval_batch_id；source已有scope；前端事件触发pending查询，安全字段与序列断言同步 |
| `application/stream_projection.py`、`async_event_stream.py` | source优先分流；子消息不并入根，外层run过滤/sequence仍统一 |
| `application/cli_display.py`、`cli.py` | 子审批即时显示/按批选择；活动子决定直接投递，不worker.join；EOF/quit先停/唤醒再join |
| `web/schemas.py` | 新approval_batch_id、行approval_id；origin_run_id/request_id保持；控制输入可带agent_path |
| `web/app.py` | approvals同一路由按服务端kind分派；approval-stop只服务paused_root；原鉴权保持 |
| `web/service.py` | live_child在busy时按当前root/目标批准入；paused_root保留旧恢复准入；不放开第二个父run |
| `web/run_delivery.py` | 子决定回当前root run回执，不新建producer；子source不结束根；paused_root仍原生产者 |
| `web/serialization.py` | pending按批含身份/角色/path/kind；source序列化；内容仍先文字投影/帧预算 |
| `frontend/src/types.ts`、`events.ts` | 全部新字段/来源分流；不同实例同call_id不冲突，子终局不改根终局 |
| `frontend/src/components/ApprovalPanel.tsx`、`ToolCall.tsx` | 一个实例当前批为一组提交，行key为approval_id；运行中显示待审组与子进度；明确停子/根 |
| `frontend/src/api.ts`、`useConversation.ts`、`App.tsx` | 去live_child的!busy显示门槛；提交带batch；回当前run不重置SSE/投影；根恢复回新run沿原行为 |

这里必须改Web请求合同，覆盖085原“web/schemas.py保持不变”的阶段边界；该说法只适用于没有SubAgent的085基础片。仓库消费者同时迁移，不保留两个审批身份自动推断的兼容分支。当前前端消费者已按源码定位；扩展ApprovalPanel.test.tsx、ToolCall.test.tsx、approval-flow.test.tsx、events.test.ts及tool-events.test.ts。

不做Agent树、历史浏览/重开、角色文件管理、后台面板、工具注册上传、图片/diff专用视图。必需审批/停止接线不是这些专用视图的替代品。

## D. 状态及接口迁移清单

1. 内部resolve_model/活动工具/权限读取加入agent_path，既有根调用默认root；根据真实调用方迁移，不新增无消费者scope层。
2. App新增可信register_subagent与异步对等入口，不改现有run/resume的位置参数合同。应用间隔离定义表，不把Python模块全局dict当注册表。
3. ToolRuntime.context有同步/异步委派端口，工具层不依赖上层；子消息入口标delegated_task并沿真实用户投影审查。
4. pending以approval_batch_id区分实例当前批、每行approval_id；Web/CLI/remember_approved和测试同片迁移。原内部call_id仍用于ToolMessage配对，不能用approval_id替换模型协议ID。
5. 父/子显式共用saver但thread_id各自独立；App持有实例、唯一驱动者和当前PendingToolApproval。删除旧候选execution_entries，观察/工具卡不能当恢复权威。
6. 持久会话ready/受限恢复合同不变，无DB schema升级。source是事件/显示结构变化，不能误称只改后台；检查序列化、SSE、前端fixture和事件日志消费者。
7. 删除execution.batch_wait_seconds设计入口及旧环境读取；不保留无意义的超时key。样例注释、load校验、脚本和旧批次测试同时迁移。
8. 关闭只关闭自己拥有的资源；先停止/恢复待收尾图，再等待owned tasks；子不得关闭共享模型SDK/存储。

## E. 删除及保留的具体逻辑

| 迁移后删除 | 明确保留 |
|---|---|
| 仅一个interrupt且必须匹配父AI工具名的审批解析 | 官方interrupt与Command、一次性授权、原拒绝消息协议 |
| 审批恢复再次完整review/Auto | 绑定一致性检查、文件真实目标执行复核 |
| 子中断冒泡父图所需重入账/空GraphInterrupt协调候选 | 父工具持续等待，原parallel/sequential及真实失败/取消身份保留；根HITL独立核验 |
| 以60秒串行排队判调度失败、相关旧env读取 | 命令执行超时、模型请求超时、进程清理期限 |
| 仅session_id查找而把子当根的新增调用 | 根SDK默认参数、根steering/follow-up/会话终局职责 |
| Web/CLI所有审批必须父run结束/busy=false/join之后的判断 | 按保存kind分派、批/行唯一性、模型call_id配对、origin_run_id和幂等/认证 |

不因为定义同名或可import就保留废弃内部兼容。旧测试迁移行为断言，不删失败用例/skip/放宽计数；历史085/研究保留版本说明。跨重启pending、WIN1平台锁/ACL和后台任务仍是独立待办，不预建占位。

## F. 六个实施切片与验证

先完成085的注册/动态集合、统一错误、保存权限与HITL合同，再实施下列切片。每片先写并实际观察预期红灯，再最小接线；涉及三文件以上的片在本附录范围内列出实际测试/组件清单后开工，合同变更先更新Spec。

| 片 | 目标 | 必须通过的证据 |
|---|---|---|
| S1 定义/注册 | SubAgentSpec、角色表、注册API、动态短索引、层数限制 | 重名/非法工具或模型不发布；动态登记下一请求可见；空工具集；独立App隔离 |
| S2 实例/正常结果 | 独立输入、冻结模型、根/子装配、同步/异步端口、有界content/artifact/可选call limit | 两同角色不串历史；第N次终局成功/第N+1超限；无额外总结调用；正常/内部错误/机制异常身份 |
| S3 恢复核心 | 独立thread/驱动者、PendingToolApproval通道、批/行身份、scope许可 | A在B运行时获准并完成；同图不同实例/相同call ID；prepare/Auto不重跑；父不重入；串行prefix/A/tail |
| S4 来源/交付 | 外层source、CLI/Web运行中审批、按实例提交 | A/B待审分别提交；busy不拒正确子批；CLI不join阻塞；同run回执不重置SSE；根resume回归 |
| S5 停止/保存 | 活动/待审批子停止、根广播、竞争、关闭、SQLite保存 | 批准后停/已开始IO保持真实结果；A局部保存失败转工具error、B继续/可独立批准、父继续且根可保存；根保存失败不倒改子结果；原异常/EOF/取消无孤儿Future |
| S6 终检/集成 | 清理被替代入口、配置样例/用户操作文档、整体运行证据 | 同步SDK、异步CLI/Web、会话ready重启不回归；真实模型仅获授权后有界验证；Windows依WIN1单独实测 |

S3完成前不把通用subagent暴露到正式默认工具集；S4完成前不宣称界面可用。开发分片可用测试构建应用注入角色，不需要发布未完成的平台入口。

建议新行为测试文件：`tests/test_subagent_registration.py`、`test_subagent_execution.py`、`test_subagent_approval_resume.py`、`test_subagent_batch_resume.py`、`test_subagent_stop.py`、`test_subagent_events.py`；Web/前端协议优先扩展现有测试，各文件≤500行。命名是计划，当前没有这些测试。

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

回退风险：当前只有文档，无数据改动；将来实现后的Web客户端/服务端须成对回退，不能用旧UI提交新审批ID。只有ready会话可安全跨版本续聊，待审批任务不支持升级/重启恢复；禁止默默重放工具来“修复”版本不匹配。
