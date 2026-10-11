# Spec081：观察隔离与一次运行依赖收敛

状态：2026-10-11用户批准共同设计；两片已在当前工作区实现并通过本机回归，尚未提交。沿用Spec080原异常合同。验证记录见第6节。

后续接口更新：用户明确要求移除内部兼容桥，已按[Spec082](082-remove-obsolete-runtime-bridges.md)删除6套分项bind/getter、failure_data和旧交互/workspace包装，控制事件转发模块及模型层旧响应拒绝别名也已移除；本Spec中的保留兼容表述是当时验收边界。当前只有中心RunServices绑定/读取入口；最新后端1038/前端27与构建通过。

## 1. 现状与目标

当前观察publish可将成功模型返回变failed，Publisher重复检查五类生命周期；6个ContextVar分别注入运行依赖，失败描述另走reporter/id表。收敛为执行路径确定结果、观察路径只报告事实。Bash的HTTP404/非零命令退出仍回ToolMessage供模型分析，不增加自动重试/熔断。

执行链：App准入→运行→原结果或原异常→必要清理/保存→确定终局。
观察链：执行事实→安全观察入口→有序Publisher→CLI/Web/白名单日志。

## 2. 第一片：观察失败不改变执行

- Publisher保留相关ID、序号、不可变payload类型边界与顺序分发/监听隔离；移除run/call/turn/control/context跨事件状态表。不重命名27种事件，不变线字段。非法payload记录固定诊断并不发帧。
- 各观察调用通过一个明确helper执行：普通Exception记录固定安全诊断、禁用该run相关观察器、返回缺省；真实框架调用与控制、保存不进入helper。准备注入消息若观察失败，仍从原文本构造HumanMessage，不能丢输入。
- 发布保护包含payload工厂，不让构造失败遮盖结果。真实模型门禁、权限、外部输入及必要资源关闭不删。普通listener抛GraphBubbleUp属于listener故障，真实graph推进的控制信号原样传播；不catch BaseException吞停止/取消/进程控制。
- Message流必须先记录真实values/interrupt结果，再单独观察；观察失败不重放图/模型/工具。不完整的内容由结果/checkpoint视图补显，不伪造消息。
- Web若漏run_started，可从后续同会话事件绑定实际run ID并标incomplete；若所有事件丢失，实际任务结算后让启动等待得到安全的run_observation_unavailable（503），不悬挂、不编造ID。终局丢失时，成功任务的实际返回形成completed/stopped/interrupted及确认消息，标incomplete；失败仍保留原应用异常，Web安全展示，不猜未确认细节。
- 生命周期校验迁入tests/event_sequence.py：监听只收集，测试显式assert_valid；非法序列必须抛AssertionError/ValueError，不能被Publisher隔离后误绿。旧测试对应迁移检查位置，保留原非法序列与身份/内容/控制断言。

## 3. 第二片：集中依赖与原异常回传

- Runtime定义RunServices，组合publisher/错误策略/RunControl/turn/control/context观察。App持有并一次绑定；make_invocation_context读取一个匹配session/run的服务对象，之后从显式context使用，不持App/DB/UI引用。
- 6个独立ContextVar改为同一个中心变量。现有事件/控制绑定入口保留薄兼容调用，不再存独立状态；RunObservation.bind仅绑定一次。旧failure reporter内部入口按Spec080移除，旧测试迁移新policy绑定；应用/运行公开签名保持。
- 原异常的naiyue_failure=(run_id,D)标注：校验D格式/type/source，标注/读取故障固定诊断回退，不改args/cause/context/suppress/traceback。foreign run拒绝、合法unknown有效；不沿cause取标注。
- Runtime普通模型描述直接委托一次，去reporter/id表/报告锁；控制与业务自定义异常类型保持。App读当前标注，再按App/Runtime阶段规则兜底。
- Runtime保留自己上下文/turn/call/message定义；应用run/control/approval定义归组独立模块。统一RuntimeEvent/publisher协议只组合既有payload，不新增插件注册引擎，不改变DB/序列化字段。

## 4. 文件范围

第一片：agent_runtime/events.py、observation.py(新公共helper)、call_observation.py、turn_observation.py、message_observation.py、async_message_observation.py、active_stop.py、context_compaction.py、context_recovery.py；application/run_events.py、session_controls.py、session_service.py；web/run_delivery.py。Control/Context观察发布捕获只保留原故障隔离；实际观察调用在入口保护。

第二片：contracts/failures.py；agent_runtime/run_services.py(新)、errors.py、invocation_context.py、__init__.py、events.py、run_control.py、turn_observation.py、control_observation.py、context_operation_observation.py；application/run_events.py与应用payload拆分。相应tests/test_observation_isolation.py、test_web_delivery.py、test_run_services.py、test_failure_annotations.py、test_run_events.py、test_control_observation.py、test_failure_extension_boundaries.py与tests/event_sequence.py；docs设计/Spec080/handoff/future-work/plan。

payload具体归组：共享RunFailureData放contracts/failures.py，App生产的run/control/approval纯数据放新contracts/session_events.py（标准库，不导入Application）。Runtime组合CORE与SESSION映射，只做类型边界；旧events/control_events导入保留同一类别名，线字段/JSON不变。测试追加导入身份/层级与wire字段校验。RuntimeEvent留既有位置，避免仅为移动而改公开类型。

超过8文件已明确，按每个执行点同步迁移；单生产文件≤500行。发现额外必要接线先更新Spec。旧未提交工作按当前快照保留，不回滚/清理/切分支；不改.env、依赖、模型配置、前端协议或存储模式，不提交push。

实施中发现Web完全丢失事件时，已执行的请求返回503后若删除待定回执，同一`request_id`重试会再次执行。对应接线扩展至`web/run_delivery.py`的回执容量判断：保留无ID的待定回执，重试同一请求继续返回`run_observation_unavailable`，不同正文冲突，新请求在容量满时拒绝；不编造run ID。该回执仅在当前Web进程内有效，重启后的跨进程幂等不在本片保证范围。

## 5. 验收

第一片红灯：成功模型/终局发布故障、payload构造/turn/context/message故障、已有主异常不替换、控制收取失败仍保留原输入、listener GraphInterrupt隔离、不可配对实际模型仍拒绝；Web开始/终局/所有事件丢失有界收尾，必要保存失败仍失败。测试末严格检查实际序列，并发有序/重入策略明确。

旧tests/test_persistent_sessions的关键清理测试注入点从TurnObserver.snapshot迁至SessionService._cleanup_run，保持ValueError、非ready、busy释放原断言；另外新增观察snapshot失败成功测试。原因是快照是观测，不能再冒充关键清理。test_context_operation_observer也迁移至测试CheckedPublisher，非法subject断言不放宽。

第二片红灯：属性可写/不可写/坏getter/非法D/foreign run/合法unknown；身份与原因链，模型一次分类，原ModelRetry/ContextCompactionError捕获，双App/线程/task上下文还原和关联；同步/异步/审批恢复/stream/Web接线。共享异常被别run覆盖允许诊断降级，不声称精确并发归类。

每片定向pytest，最终全量pytest、compileall、pip check，frontend npm test -- --run与npm run build，git diff --check。假模型/故障注入与真实模型证据分开，无额外生产能力声明。无需新服务/凭证/DB迁移，回滚只涉及本片源码，不改既有会话数据。

## 6. 实施补充与证据边界

第一片和第二片的生产接线、显式序列检查器及回归测试已存在于当前未提交工作区。续接检查另发现`RunObservation.failed/cancelled`仍用`except BaseException`吞掉观察器发出的控制信号；新增两例红灯后移除该外层捕获，普通观察故障仍由`observe`隔离。Web无事件场景新增重复请求与容量回归，红灯实际重复执行模型两次，修复后定向测试转绿。完整命令和结果见`docs/handoff.md`最新单元；此处的本机假模型、故障注入、临时SQLite不能代替真实供应商或跨进程交付证据。

再次续接后的当前验收：全量`994 passed in 35.56s`，frontend9文件27通过及tsc/Vite构建成功，compileall/pip check/diff check退出0；实际单ContextVar位于run_services，生产文件均≤500行。应用/Runtime四入口模块51个既有公开参数AST保持。相对本轮原始快照23生产/9测试文件变化，此前未提交工作保留；不把中断期间其他续接修改覆盖回旧版本。

HTTP404本机探针已验证：普通curl的exit0/success与--fail-with-body的exit22/error都把404及正文放同ID ToolMessage进入下一次假模型输入，两场景HTTP请求共2次，没有自动重试；合成done只证明循环继续，不证明语义判断正确。外部临时网络/超时/限流/MCP插件不可用才进入未来重试资格检查，404等测试结果本身不重试。

## 7. 获批补充：可调用观察入口与严格观察验收（2026-10-11）

用户在设计复审第13节核验后明确“做吧”。当前数据流为字符串operation→helper内getattr→普通异常隔离；方法拼错因此被当作观察故障并使观察器本run降级。改为先绑定方法、再由helper执行：`observe(turns, turns.begin, call_id)`。缺失方法在进入helper前暴露；方法内部普通AttributeError/TypeError仍按观察合同隔离，测试显式验收观察健康。

内部observe参数名/位置保持，operation改接收同步可调用对象及原args/kwargs；非可调用操作在保护边界前抛固定TypeError。None/已降级观察器返回fallback，普通故障记录固定码/受限方法名/安全异常类型并降级，BaseException传播。调用点保留既有None检查，真实模型/工具/权限/控制/保存不移入helper。

生产范围：agent_runtime/observation.py、call_observation.py、message_observation.py、async_message_observation.py、turn_observation.py、active_stop.py、context_compaction.py、context_recovery.py；application/run_events.py、session_controls.py、session_service.py。所有现有observe调用迁移绑定方法，包含for循环和异常路径。errors.py移除_runtime_classification中被保护分支提前命中的compaction/tool_batch两条重复规则，保护分支及source/原因合同不变。emit_event注释明确publisher存在时payload工厂同步立即求值，不缓存工厂；events.py/turn_observation.py的既有局部导入移动至标准库/第三方之后。此次只调整这两个文件的导入位置。

测试范围：新增tests/test_observation_calls.py验证可调用参数/返回值、非法字符串拒绝、方法缺失、内部普通故障隔离/重复调用跳过/脱敏、BaseException传播。扩展现有tests/event_sequence.py加入显式assert_healthy：校验序列、拒绝非预期观察诊断/不可用事件；修改该测试helper是为防止执行成功掩盖观察故障，不改conftest/测试运行模式。正常同步工具循环、原生异步控制、审批恢复、压缩和溢出恢复在测试末调用；注入观察故障后仍返回成功的负例必须被该断言拒绝。既有保护分类测试加入compaction/tool_batch，保持自定义规则不可覆盖。不会把本来应隔离的观察故障改成生产失败。

先运行可调用入口红灯，再最小实现与全调用点迁移、定向回归；去重是保行为整理，由保护分类回归约束，不伪造红灯。最终全量pytest、compileall/pip check、frontend测试/构建、git diff --check；AST核验生产observe无字符串/延迟lambda及单生产文件≤500行，公开应用/Runtime参数签名保持。

沿用前两片的原异常、共享resolve、单参/双参兼容、failure_data及bind入口；本轮不改分类接口/配置、依赖、.env、DB、公开运行签名，不引入测试模式生产分支或自动重试。本补充不代表用户已掌握，课堂仍在Lesson38。

实施结果：新增17个测试场景，先运行出现`7 failed, 11 passed`（包含新增可调用入口红灯与保护分类基线），迁移后定向`51 passed in 1.56s`。86个调用点在10个文件中从字符串改为绑定方法；AST与修改前快照比对确认这些调用文件除获批导入调整外仅发生该替换。本轮13生产文件/3测试文件变化；移除两条不可达分类规则，保护分类扩展不可覆盖仍通过。

最终`.venv/bin/python -m pytest -q --tb=short`→`1011 passed in 35.97s`；compileall/pip check/diff check通过，frontend9文件27通过及tsc/Vite构建成功。生产文件≤500行，快照中的93个既有App/Runtime公开参数签名保持。仅本机假模型/故障注入/临时文件证据，无真实模型新请求，未提交push。测试健康检查显式在正常路径末调用，两个负例确认成功结果不能掩盖观察诊断或不可用事件。
