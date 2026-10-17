# Spec093：架构语义、共享合同与同步冗余收敛

日期：2026-10-17。用户要求先提交push，再系统核对职责与实际调用、清理旧兼容。基线已推送：ef34d49（工具/权限/存储/恢复）、d6053f2（学习记录），origin/main核对一致。提交前后端1971通过、前端40通过及构建成功。

## 1. 审计事实与判断标准

当前主链：CLI → AsyncCliApplication → AsyncApplication，Web → AsyncPersistentApplication；都进入SessionService.arun/aresume_tool_approval → arun_steps → ainvoke_agent_run → 官方异步图。CLI同步方法是跨线程/事件循环的真实终端桥接，不是另一套同步Agent。

审计基线中的独立同步链为 LocalApplication/build_local_application → SessionService.run → run_steps → invoke_agent_run；PersistentApplication使用SqliteSaver。46份测试、6个探针和旧恢复设计实验消费它，正式CLI/Web不走这条链。用户已明确确认本轮迁移这些消费者并删除独立同步应用链；先迁移消费者，保持原断言，不把当前仍在用的代码直接当死代码删除。

按代码生产者、消费者、控制/保存责任判断：普通同步状态/锁/文件IO、确定性算法、框架确实注册调用的hook都可以保留；不要以def命名或ainvoke外观判断是否废弃。

## 2. contracts保留的理由与093A范围

四层仍是模型接入、Agent运行、测试策略、应用入口。contracts是只依赖标准库的少量共享事实与验证，不是第五个执行层：

- FailureDescription/ErrorClassifier/安全类型名、校验/标注/读取、分类回调规范同时服务模型/Runtime/App。分类公共函数执行调用者提供的规则，不决定重试/恢复/权限。
- RunFailureData由Runtime的模型/工具/turn与App终局共同消费。
- RunStarted/Finished由App生产；Control/ApprovalRequested由Runtime观察器在App发起的控制过程中生产；RuntimeEvent注册验证、CLI/Web共同消费。SESSION_PAYLOADS真实进入映射。
- 整包迁入App会让底层向上依赖；整包迁入Runtime会让模型层依赖运行层。现有定义没有完全无生产消费者的项。

093A做已明确的归属和导入收敛：

1. ModelResponseRejected及RejectionReason只由Runtime抛出/判断，迁回agent_runtime/errors.py；contracts删除旧定义，不留兼容alias。原因Literal补齐实际已用的missing_tool_call_id、duplicate_tool_call_ids、invalid_tool_call_ids。异常内容、继承、原异常传播与分类不变。
2. turn_events直接依赖contracts.RunFailureData，去掉从events间接读取的TYPE_CHECKING/函数内循环规避；字段与校验不变。
3. safe_error_type及RunFailureData的调用方直接从定义处导入；events中仍被union/注册表真实消费的类型导入保留，不因能被import就承诺外部兼容别名。
4. 清理确认无Name/注解/注册/重导出消费者的emit_event、PermissionSettings、AIMessage、run_blocking导入；RunServices的TYPE_CHECKING注解导入保留。
5. 测试由“LegacyFailure别名身份”转为真实RuntimeEvent载荷注册与共享身份；补依赖方向、独立import、turn注解可解析、响应拒绝原因与分类测试。事件序列化、DB、权限/停止/异常身份断言不放宽。

文件：contracts/{__init__,failures,session_events}.py、agent_runtime/{errors,turn_events,context_operation_observation,async_message_observation,control_observation,call_observation,message_observation,context_compaction,context_recovery,turn_observation}.py、application/{async_application,async_session,session_service,session_controls,run_events}.py及相关测试。当前文档更新入口/语义；历史课堂不重写，仅注明版本边界。

## 3. 同步链待收敛范围与保留项

已确认不可达：AsyncSessionMixin._review_partial_state只作为IOCall同步fallback存在；两个调用点都要求async_mode=True，arun_steps始终使用_areview_partial_state；同步run不会进入该条件。完整异步生命周期迁移时直接消除此伪fallback，不新增兼容包装。

完整同步应用迁移候选：迁移六个探针（live_workspace_tools、compaction_quality、persistent_session、message_state_boundaries、skill_loading、message_events）、46个应用测试与恢复实验的IO入口，再删除LocalApplication/PersistentApplication和同步工厂、SessionService.run/resume/同步finish、仅供双驱动的run_steps与async_mode差异。必要保存、异常优先级、busy释放、IO取消settle保持；不能为旧测试新增代理壳把同步接口藏起来。

原运行层待定范围：invoke_agent_run及resource同步执行、同步流观察驱动、同步摘要IO/Auto审查/批次调度。用户2026-10-17追加确认删除Discount历史业务及测试，并迁移其余低层消费者；当前范围以第9节为准。

明确保留：ObservedHITL.aafter_model用run_blocking调用其after_model，StopBoundary.after_model还被审批停止复核调用；TurnBoundary和reset/inject装配hook由框架RunnableCallable在async图调用sync实现。Read/Write/Edit/Ls文件主体、配置/SQLite/路径IO由线程卸载。未知模型同步SDK回退负责安全停止，不能移除。Shell构造期probe_version实际使用run_async_sync，不能随执行期包装一起删。

## 4. 正常与失败链、验证

正常：模型失败 → model_access生成共享FailureDescription → Runtime在原异常上绑定run_id描述 → App读取同一原异常上的描述 → 终局/turn事件使用共享RunFailureData → RuntimeEvent注册验证 → Web序列化。响应结构拒绝则由Runtime创建自己的ModelResponseRejected → RuntimeErrorPolicy保护分类 → 原异常继续上抛。不绕经App定义底层错误。

失败：描述校验/标注/观察出错记录安全诊断并保留原异常；必要保存/清理失败不降级成观察成功。turn_events在独立导入和get_type_hints时能直接解析RunFailureData，无需先加载events。

先新增失败测试再移定义/导入，复用当前事件、异常、取消/保存回归。最终命令：

```sh
PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m compileall -q naiyue_test_agent scripts tests
.venv/bin/python -m pip check
git diff --check
```

Pi本机a69bef789把AgentEvent放核心types并由AgentSession消费；AgentX本机3fcfa0e让TraceCollector生产domain/trace/event并由App listener保存，应用专用WorkflowEvent留App。两者都按真实语义和依赖组织，而非统一要求名为contracts的目录。本项目保留跨层共享事实的原因来自自身依赖，不照搬参考目录。框架sync hook已按当前安装LangChain1.4.2/LangGraph1.2.12的factory/RunnableCallable核对。

## 5. 093B：用户已确认的应用异步收敛

保留公开CLI命令/Web协议、AsyncApplication/AsyncPersistentApplication、AsyncCliApplication真实终端桥。删除仓内同步应用类/工厂、persistent.py以及SessionService.run/resume、重复同步finish和async_mode分支；这些是被正式主链替代的内部入口，不因测试可import而保留兼容。

SessionService只由原生arun/aresume驱动，复用现有生成器准入/结果流程；状态复核与finish只保留一个async实现。将async_session中仍必要的实际方法合并回SessionService（须≤500行），移除无消费者mixin模块。SessionPersistence只保留afinish/arestore/ahistory/arecover，原三次恢复、必要保存与IO取消settle不改。

IOCall继续表达真实阻塞IO或原生async IO；移除run_steps。async_call存在的操作不再同时填写永远不会执行的同步候选。call与async_call明确二选一；arun_steps对同步IO仍run_blocking、关键async仍await_settled。不移除SessionStorage等必要同步IO或底层Runtime同步策略路径。

测试基础设施说明：当前依赖没有pytest异步插件，已有测试用asyncio.run。迁移沿用该执行方式，允许复用一个tests/native_application_helpers.py的async测试入口与AsyncExitStack资源回收辅助；返回真实AsyncApplication/AsyncPersistentApplication，测试直接await原生方法，不提供旧App.run或同步图代理。持久测试显式切换至实际AsyncPersistentApplication的storage，避免以假存储或旧连接断言。辅助只处理pytest执行/资源所有权，不实现生产语义，不增加依赖。

普通测试、权限/进度/恢复/故障测试按真实async API迁移；线程竞争改为同loop任务或在必要处await asyncio.to_thread等待线程Event，不能阻塞事件循环。原故障身份、busy、保存、配对、顺序/重复执行、scope和诊断断言保留。原sync/async双路径用例迁为单一应用路径；同步模型SDK回退与原生async模型差异仍有实际意义，不能误删该覆盖。

六个当前探针改原生async装配/执行与显式aclose/async with；保持脚本参数和退出码。恢复设计实验如仍保留底层框架sync探针，要与产品sync入口移除区分；不得让其引用已删除应用模块。当前使用文档和学习地图注明版本边界，历史lesson保留，不伪造新的学习完成记录。

## 6. 最终实际结构与调用链

本节为093实施后的当前入口；上文“独立同步链”是审计基线。除测试辅助外不新增生产模块、依赖、表或配置；删除两个应用模块。

| 文件 | 当前职责/实际变化 |
|---|---|
| application/__init__.py | 保留共享装配、注册表准备及真实错误/SessionService导出；删除LocalApplication/build_local_application |
| application/persistent.py | 删除。正式持久入口只有async_persistent.open_async_persistent_application |
| application/async_session.py | 删除。有效方法合入SessionService，移除死同步partial-review包装与重复finish |
| application/session_service.py | 单一arun/aresume_tool_approval，复用_run_steps/_resume_steps准入与结果；一个_afinish_run，取消/主次异常/必要保存/释放busy次序保持 |
| application/io_steps.py | 删除run_steps；IOCall.call与async_call恰好二选一。保留arun_steps/send/throw及必要IO settle |
| application/session_persistence.py | 仅afinish/arestore/ahistory/arecover执行入口；metadata阻塞IO仍线程卸载，图IO只有原生async |
| application/session_recovery.py | history/recovery只调用aget_state/aupdate_state，三次更新和原checkpoint保护保持 |
| application/session_approval.py | 接收原recovery严格前置审批判据is_pre_tool_approval；运行失败复核和重启历史共用，不重复发明宽松判断 |
| contracts/{failures,session_events,__init__}.py | 仅保留跨层共享事实/验证/事件载荷；移除Runtime专用异常与误导性归属说明 |
| agent_runtime/errors.py、turn_events.py | 响应拒绝异常与完整reason归Runtime；turn字段直接依赖共享失败类型，独立类型解析无惰性循环 |
| Runtime/App其他相关导入模块 | safe_error_type/RunFailureData直接从定义处导入；删除确认无消费者的导入，真实事件registry导入保留 |
| 六探针及docs/examples/probe_session_recovery_design.py | 使用正式异步App/原生图IO；脚本入口asyncio.run，CLI展示路径使用真实AsyncCliApplication，资源显式关闭 |
| tests/native_application_helpers.py | pytest沿用asyncio.run，AsyncExitStack回收真实App；没有旧App代理、同步执行器或新依赖 |
| tests/test_architecture_semantics.py及既有行为测试 | 新架构边界/关键IO取消/工具内中断回归；旧消费者沿真实await链迁移，故障注入改到实际async方法 |

直接导入旧工厂的46份测试之外，还必须迁移共享fixture的间接消费者（事件、上下文、恢复、配置、Write与CLI等）；因此实际修改测试文件数多于最初静态工厂计数。三个Bash终端用例移至既有test_cli_event_integration，Write应用权限用例移至既有test_application_write，保持各文件≤500行，未增加平行测试框架。

### 6.1 正常输入和审批暂停

示例输入：`await app.arun(sid, [('user', 'read external')])`。App的_sessions已保存`{thread_id, model_id, workspace_root, permission_mode, busy, pending_tool_approval, ...}`，不会每次创建新Agent。

```text
CLI输入线程 → AsyncCliApplication._submit → AsyncApplication.arun
Web运行任务 ────────────────────────────────┘
  → _execute → SessionService.arun → arun_steps(_run_steps)
  → 同步准入设置busy/run_id，RunObservation.start
  → IOCall(call=persistence.start) / IOCall(call=prepare_run_resources)
     arun_steps以run_blocking完成真实阻塞IO，结果send回原生成器
  → with observation.bind():
      IOCall(async_call=ainvoke_agent_run[_with_resources], args=(agent, messages), critical=False)
  → 已装配Agent的astream：选模/权限准备/官方批量HITL/工具/模型
  → 官方结果 {messages: [...], __interrupt__: (...)}
  → pending_tool_approval解析batch/动作和调用ID，保存pending并返回approval_requests
  → IOCall(async_call=_afinish_run, critical=True)
  → cleanup → 必要checkpoint/metadata保存 → 持久应用释放busy → 终局事件 → App返回
```

不需要同步候选`invoke_agent_run`：arun_steps以前也始终选async_call。现在IOCall的数据明确表达唯一实际操作，例如`IOCall(args=(agent, messages), kwargs={...}, async_call=partial(ainvoke_agent_run, max_concurrency=4), critical=False)`。

恢复：`await app.aresume_tool_approval(sid, ['approve'])`进入相同驱动的_resume_steps；读取原pending、校验决策、准备一次性grant，再aresume_agent_run_with_resources → 原thread的官方Command(resume=...)。原批权限事实、串并行计划和Auto不重跑的合同不改；返回后走同一_afinish_run。冷恢复则由arecover_session → arun_steps(recovery_steps)驱动原三次aupdate_state，旧工具不自动重放。

### 6.2 失败和中断边界

- IO失败通过steps.throw回到原try/except/finally；原异常身份、分类、阶段及清理保存顺序不因移除同步驱动改变。必要保存失败仍阻止错误地报告成功，观察失败仍有诊断且不覆盖主异常。
- 关键async写入和所有已启动blocking IO仍await_settled，取消不会提前释放资源/写入所有权；正式图执行保持可取消。测试用事件同步验证“写入完成→生成器关闭→取消向外传播”。
- 仅固定`BatchApprovalMiddleware.after_model`任务路径、唯一interrupt、最新AI批次/参数和官方待审动作全部匹配时，is_pre_tool_approval才证明工具尚未执行。审批准备或执行入口失败仍停在该点，可保留pending重试。
- 任意工具内部interrupt不能充当前置审批。先产生副作用再interrupt的真实图回归，验证缺结果进入manual_review_required，第二次普通输入/审批不能再次执行。
- 用户本轮明确确认：工具阶段已失败、结果缺失时沿用正式异步链的核对保护，不直接再审批重放。两条旧同步App测试相应迁移为原异常/失败turn/第一次approval_applied保留、后续准入被阻止且无新增执行/事件。底层TurnObserver.restore/activate_resume仍独立验证失败旧turn与新retry身份/完成；ControlObserver单测验证未经过HITL不能伪造approval_applied。证明未执行后安全重试留后续设计。

## 7. 保留项与测试迁移的准确含义

本次删除独立应用执行器，不承诺所有def均为冗余：

- cli_async两个同步外观工厂由正式终端使用，产物是真实AsyncCliApplication；它把输入线程调度到一个专属loop，不执行第二套图。
- SessionService的状态、锁、配置选择和prepare_resume等是当前异步App直接调用或线程卸载的算法/IO；SQLite metadata、Read/Write/Edit/Ls实际文件IO保留。
- ObservedHITL.aafter_model仍在线程调用其真实after_model，StopBoundary.after_model还供审批停止复核；框架注册的TurnBoundary和控制注入sync hook在async图仍会执行。
- 093A/B时低层同步Runtime仍有消费者；随后用户追加授权093C删除Discount并迁移消费者，当前执行面见第9节，共享建图保留。

原生流多发message_delta，相关序号测试按实际事件插入位置更新精确序列，消息ID/调用配对/次序/唯一终局/脱敏断言保留。模型fixture保留原生流与同步SDK回退覆盖；异步fake的线程传输显式携带原异常，避免stdlib executor重建精确builtin TimeoutError使身份测试失真。专门的同步SDK故障/停止场景仍使用BaseChatModel._agenerate回退；TimeoutError子类保留超时分类和同一原对象检查。

KeyboardInterrupt在asyncio内部Task有Runner级处理；应用层控制合同在实际await的stream入口注入同一控制对象并启动真实TurnObserver，验证不伪造失败/取消终局；低层同步观察器的原始KeyboardInterrupt传播覆盖保留。没有把此类测试的旧同步App继续作为产品能力。

TDD证据：093A结构/合同初始9失败、相关29通过；093B单执行面/IOCall初始2失败，核心24通过；工具内部interrupt保护先失败，再复用严格判据转绿。重启真实进程强退、源checkpoint/pending writes保留、必要保存失败、同步工具进度、并发Write busy、审批前整批不运行等行为继续沿真实生产入口验证。

## 8. 最终验收与交付状态

2026-10-17最终代码验证：

| 命令/范围 | 实际结果 |
|---|---|
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | **1967 passed in73.62s**；/tmp/naiyue-093-backend-verified.log |
| `npm --prefix frontend test -- --run` | **10 files / 40 passed**；/tmp/naiyue-093-frontend.log |
| `npm --prefix frontend run build` | TypeScript/Vite成功，186模块；/tmp/naiyue-093-build.log |
| `python -m compileall -q naiyue_test_agent scripts tests docs/examples` | 成功；全部现存生产模块106份、全部本轮修改Python文件均≤500行 |
| `python -m pip check` / `git diff --check` | 无依赖冲突／无差异格式错误 |
| `python -m scripts.probe_message_state_boundaries` | 10场景PASS，无网络；/tmp/naiyue-093-state-probe.log |
| `python -m scripts.probe_persistent_session` | 离线两进程PASS，同session/thread、新run/checkpoint、历史实际使用；/tmp/naiyue-093-process-probe.log |
| `PYTHONPATH=tests:. python docs/examples/probe_session_recovery_design.py` | 三个运输/恢复探针PASS；/tmp/naiyue-093-design-probe.log |

python命令均使用项目.venv/bin/python。测试数由1971变1967：合并重复的旧同步/异步App入口变体，新增13个架构/关键IO/中断用例及1个控制观察用例；保留场景与实际模型/工具IO差异覆盖，不以数量代替行为证据。两个执行失败重试用例按用户本轮确认改为验证正式异步的阻止重放合同，底层retry另有独立覆盖。

最终只读复核确认严格前置审批判据、主次异常、busy/保存、IO settle和异步故障注入；复核指出三个手工构造App的测试未登记资源关闭，已补aclose并经19例定向验证。迁移后移除确认无本地使用、共享fixture导出或字符串引用的80个测试/探针导入。改动扫描123个现存文件无高置信凭证命中，当前文档相对文件链接有效。配置、依赖和用户数据库未改；证据限macOS/假模型/真实本地IO及离线子进程，不代表Windows/Linux原机或供应商生产请求。

此前两笔提交ef34d49/d6053f2已push，远端main精确核对d6053f2fde7927d6742b8bad07cc2366fc9d28d8。093清理为此后的工作区改动，尚未commit/push，没有把它混入已推送的旧验证声明。

## 9. 093C：历史Discount移除与Runtime异步执行收敛

用户追加授权：Discount用例设计属于历史需求，删除`test_agent/discount_case_design.py`、只承载该原型的包初始化文件和`tests/test_discount_case_design.py`；迁移同步Runtime的实验/测试至现有异步入口。没有实际迁移办法的同步路径可以保留，但必须列明消费者、原因和冗余行数。历史Spec/课堂作为历史记录保留，当前入口说明同步更新。

### 9.1 改前链和修改边界

当前正式产品为`SessionService.arun → ainvoke_agent_run → Agent.astream/ainvoke`。低层测试/探针仍通过`invoke_agent_run/_invoke_agent_run`、resource同步入口或直接`Agent.invoke`进入同一图的同步执行分支，因而消费同步消息流驱动、批次Condition计划、摘要IO驱动和Auto审查。Discount只有历史测试引用，没有CLI/Web接线。

先把消费者迁到`ainvoke_agent_run`、resource异步入口和`Agent.ainvoke/astream`，测试继续检查消息配对/顺序、事件、原异常身份、审批批准/拒绝及故障保存。pytest沿用现有`async_test`和`asyncio.run`，不新增插件或生产兼容代理；线程Event等待通过线程卸载或改事件同步，避免堵住事件循环。同步SDK的异步线程回退仍需覆盖。

随后移除确认没有消费者的同步执行面：`agent_runtime/__init__.py`的同步invoke；`resource_invocation.py`的同步invoke/resume；`message_observation.py`的同步流驱动（保留异步流共用的MessageStateObserver）；`tool_execution/batch.py`的同步计划和dispatch；`context_summary_io/context_compaction/context_recovery`的同步摘要/重试；`permissions.py`的同步review/执行hook；模型/工具观察、授权、toolset和停止中仅同步执行调用的wrap方法。共享建图、请求/权限算法、文件IO、模型SDK同步回退，以及异步HITL确实调用的同步after_model和短状态hook保留。

主要消费者：`test_agent_runtime*`、`test_tool_batch_scheduling`、`test_message_observation_failures`、`test_model_request_context`、`test_model_limits`、`test_auto_permission_review`、`test_tool_error_feedback`、`tool_feedback_helpers`和对应故障注入测试；探针`probe_tool_batch/tool_serial_config/tool_loop/configured_tool_loop/context_compaction`；`docs/examples`中仍直接使用已移除入口的当前实验。独立模型协议探针、HTTP客户端stream、BaseTool文件IO测试不因同名invoke而迁移。

### 9.2 正常与失败链

正常：测试/探针`await ainvoke_agent_run/agent.ainvoke` → 原异步middleware → Permission准备/Auto → 固定审批暂停（整批未执行） → resource异步resume+原one-use grant → AsyncBatchPlan → 工具 → ToolMessage → 下一轮模型；事件/结果断言保留。摘要生成继续`_compaction_steps → arun_summary_steps → model.ainvoke`，只删除第二个IO驱动。

失败：模型/工具/摘要IO原异常通过异步handler或steps.throw返回原策略 → 观察记录不覆盖主异常 → 原终局和资源收尾；控制信号不转换成普通工具错误。批次中前调用失败，后调用仍按AsyncBatchPlan生成未执行结果；审批暂停/恢复及原异常身份继续由现有测试验证。不能通过放宽断言、删除非Discount场景或保留只服务旧测试的同步代理制造通过。

先增加执行面收敛红灯断言，再迁移消费者/删同步实现。验证命令沿第4节，并运行已迁移的离线探针；真实模型探针只做编译/入口检查，不触发未授权外部请求。最终记录相对本片开始的新增/删除行、保留的同步实际调用方与限制。未改配置、依赖、用户数据、事件/存储协议；实现阶段不提交/push，随后按用户明确授权交付。

### 9.3 迁移中的实际边界

- 保留`probe_tool_serial_config.py`这一127行独立同步框架对照实验：它直接使用官方`create_agent`验证同步max_concurrency、HITL及失败后续调用的历史行为，不调用本项目同步Runtime。原同步失败trace包含后调用启动，异步实验观察不同；不通过改断言把两者混成同一合同。其余四个直接运行图的探针改异步。该保留项不产生任何生产同步执行分支。
- 异步流收尾原来无条件调用aclose；迁移既有“迭代器没有可选close”测试先红，改为只在存在aclose时调用。保留原主异常/关闭次异常及原结果合同，不要求测试stub伪造close。
- Runtime相关开始/终局/原异常/空ID的三组测试完整迁入既有`test_agent_runtime_tool_events.py`，避免原文件因增加async装饰器超过500行；没有删行为断言。工具反馈共用helper改async后，文件工具/图的测试沿真实ainvoke入口迁移；恢复测试的禁止Auto探针指向仍实际存在的areview。
- 同步/异步入口的重复参数组合收敛；真正的同步模型SDK回退与原生异步模型仍分别覆盖。KeyboardInterrupt从异步流边界注入，避开asyncio内部Task的进程级特殊处理；原控制对象身份、已观察输入和不伪造终局的断言保持。

- 配置工具循环探针仍使用旧12步预算，离线回归先复现GraphRecursionError，再对齐已有Runtime图14步合同；模型调用仍严格2次、工具执行严格1次，不扩大网络或模型调用预算。新增test_runtime_entry_cleanup中的离线接线测试，原始工具循环入口也用假模型完成实际接线验证。

### 9.4 最终验收（2026-10-17）

相对093C开始时的Python快照：Runtime 16文件+10/-441，净减431行；Discount原型98行、包初始化1行、历史测试149行合计248行删除。43份测试文件迁移/补充，4个探针改异步；另保留127行独立同步框架对照探针，无本项目同步执行依赖。生产代码净减530行，现存104个生产Python模块，所有Python文件≤500行。

| 验证 | 当前结果 |
|---|---|
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | **1946 passed in73.63s**；/tmp/naiyue-093c-backend-complete.log |
| `npm --prefix frontend test -- --run` | 10文件/40 passed；/tmp/naiyue-093c-frontend.log |
| `npm --prefix frontend run build` | TypeScript/Vite成功，186模块；/tmp/naiyue-093c-build.log |
| compileall / pip check / git diff --check / Python行数 / 当前文档本地链接 | 全部通过；无依赖或配置修改 |
| probe_tool_batch / probe_context_compaction | 并行配对、执行前拦截、摘要成功/失败及原checkpoint均通过 |
| probe_tool_serial_config | 保留同步框架对照原断言，批准前零执行、批准后顺序及失败trace通过 |
| probe_message_state_boundaries / probe_persistent_session / probe_session_recovery_design | 10场景、两进程会话、三个恢复运输探针通过 |
| probe_tool_loop / probe_configured_tool_loop | 两入口使用假模型离线运行通过；/tmp/naiyue-093c-live-entry-offline.log |

测试数1967→1946：删除6个Discount历史测试，合并17个仅重复同步/异步入口的参数组合，新增2个执行面/配置探针回归；同步SDK线程回退与原生async、原异常/消息配对/事件顺序/权限/保存/控制断言保持。文件工具中途停止、恢复不重放和官方审批仍沿真实异步链验证。

两位只读复核核对本机LangChain异步hook仍注册为固定after_model节点，保留can_jump_to；官方HITL同步协议仍实际接线。发现的摘要探针asyncio导入位置已修复并执行通过，安全专项93例通过，无遗留阻断。证据限本机假模型/本地IO/离线进程，不代表供应商请求或Windows/Linux原机。上述为实现验收快照；随后用户已授权将093 A/B/C提交并push，实际交付状态以Git和handoff为准。

提交前本轮复验：后端1946 passed in74.71s（/tmp/naiyue-093-ship-backend.log），前端10文件40 passed，TypeScript/Vite186模块构建成功；compileall/pip check/diff及当前文档链接通过。剩余工作已在future-work顶部按当前基线整理，086旧同步设计只作基线待同步提示，不开始新功能。
