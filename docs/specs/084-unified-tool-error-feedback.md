# Spec084：工具错误统一反馈

> 2026-10-15整合说明：实施设计已并入[Spec085](085-tool-system-rebuild.md)。本稿保留早期讨论证据；Bash排除、静态工具名过滤、无工具时不建ToolNode及保持旧审批metadata等过渡约束已被后续工具系统设计替代，不再按本稿单独实现。

状态：2026-10-15设计讨论稿。三块错误反馈方向及工具/运行机制职责布局已获用户基本认可；不做工具副本和旧格式器兼容。用户要求权限、审批、调度及工具设计都讨论清楚后一起实施，当前不得启动实现。具体搬迁与依赖收口随完整方案补齐，以下调用链是设计模拟。

## 1. 已确认目标与当前差异

1. 已有具体失败与尚未转换的普通执行异常统一交给官方ToolErrorMiddleware生成错误ToolMessage；保留原调用ID/name/status。
2. 具体原因和模型可见提示由工具准备，直接复用ToolException，不新增项目异常类、逐工具提示白名单或重复文本检查。ToolException正文是工具作者主动提供的公开反馈；原始异常不应未经整理塞入其中。
3. 普通意外异常只反馈安全类型与通用说明，不自动重试。动作已发生后整理结果失败也可反馈，不先建设额外核对流程。
4. write/edit提交边界原WriteOutcomeUnknown改为具体错误反馈，不再单凭它设置manual_review_required；保持目标冲突、原子替换、路径重验、临时文件清理。
5. GraphBubbleUp/GraphInterrupt、UserStop、外部CancelledError保持原控制效果；批次、模型门禁、上下文及必要保存错误归原层。
6. Bash生命周期与BashOutcomeUnknown本轮不改行为，保留到工具重新设计。

当前具体失败经工具映射为ToolException，再由BaseTool.handle_tool_error调用各工具的安全回调并构造消息；未处理普通异常则使批次failed。六个文件工具还维护SAFE_*_ERRORS和_safe_*_error；write/edit提交错误会进入应用/CLI人工核对路径。

## 2. 范围、消费者与兼容边界

正式调用方为CLI/Web装配的同步/异步应用，以及build_runtime_agent低层入口；公共函数签名、工具参数schema、工具名、审批metadata、事件线协议、数据库结构均保持。

错误反馈职责原估计涉及13个生产文件及多于8个测试/文档文件；若同时迁移目录，还须重新列明import与文档范围，不能继续使用该数字作为最终文件数。无新服务、依赖、账号或凭证；沿用本机LangChain1.4.2/LangGraph1.2.12。

六个文件工具脱离Agent直接调用BaseTool.invoke时，执行失败将抛ToolException；统一错误消息是Agent运行入口提供的能力。现有直接调用测试迁到实际统一入口验证消息，工具内部测试仍可断言原始raise；不为旧测试保留并行的旧转换。搜索未发现scripts直接调用这些具体工具，脚本中的模型/agent.invoke不是同一消费者。此行为边界在最终审阅时应明确接受。

用户明确不需要旧handle_tool_error配置兼容。内置与后续自定义工具统一按新合同：ToolException承载具体公开提示，普通意外异常由统一回调兜底；工具不再配置BaseTool.handle_tool_error。注册/装配时校验参与工具未启用旧入口，错误配置直接说明如何迁移，不复制对象、不自动覆盖其字段、不缓存旧格式器。动态工具/运行中注册不是当前能力，不为其建设注册平台。

## 3. 统一回调与装配（已撤回复制及兼容方案）

需要一个同步回调tool_error_feedback(error,request)，复用官方ToolErrorMiddleware，不新增自定义类或继承官方middleware。回调物理位置结合第10节目录归属讨论确定；不直接在runtime根目录再平铺一个tool_errors.py。

删除原提议prepare_tool_error_handling工厂、model_copy和旧格式器映射。现有_build_runtime_agent做必要的工具合同检查并直接创建官方ToolErrorMiddleware(on_error=tool_error_feedback,tools=参与工具名)。异步路径复用同步回调，不另写同职责aon_error。

### 3.1 装配准备

- 使用传入工具对象，不复制；沿用既有重复名称、metadata与审批声明校验。参与工具handle_tool_error须未启用，不通过时装配报清晰的配置错误。
- 工具名、schema、func/coroutine和metadata不因接线而改变，不新增格式器注册映射。
- 当前Bash延后边界仍需显式表达。原提议metadata开关naiyue_tool_error_feedback缺省True、Bash=False；是否随下一轮工具注册描述归并需在目录/注册讨论确认，不能默默改成通用运行模式。
- 没有参与工具时不注入ToolError，避免空工具Agent因额外wrap hook而新建ToolNode，或Bash-only场景顺带改变图结构。

### 3.2 on_error分支

| 输入 | 处理 |
|---|---|
| BashOutcomeUnknown，或作为保护合同出现的ToolBatchSchedulingError/ModelResponseRejected/ContextCompactionError | 返回None，原异常继续传播；这些运行错误正常应发生在工具回调之外，保护分支不为其增加新运行策略 |
| ToolException | 返回工具已准备好的提示文本，不再查白名单、不调用逐工具格式器 |
| 其他已决定转换的普通Exception | 返回固定模板Tool execution failed (<safe_error_type>). No normal result is available. Use the available context to decide the next step. |

GraphBubbleUp及非Exception控制信号继续传播。无旧格式器调用及其额外故障分支，无白名单/内容扫描；不声称回调能自动脱敏工具主动提供的ToolException文本。

回调复用safe_error_type；记录一次tool_error_converted安全诊断，关联工具名（复用event_label）、tool_call_id、可用session/run ID和原异常/cause类型，不记录参数、异常正文或堆栈。不重发tool_call_started/finished，不调用故障分类resolver来决定执行动作。

## 4. 装配与真实调用顺序

修改agent_runtime.__init__._build_runtime_agent：沿用approval_policies_for_tools并检查新错误反馈合同，原工具直接交给create_agent和ToolBatchMiddleware；参与工具时创建官方ToolError，放在_record_tool_call之后。

工具包装进入顺序：

```text
ToolNode._arun_one
  -> ToolBatchMiddleware.awrap_tool_call
     -> aobserve_tool_dispatch（领域事件）
        -> _adispatch_tool_call / AsyncBatchPlan.execute（调度）
           -> _record_tool_call.awrap_tool_call（guard、日志、await_settled）
              -> ToolErrorMiddleware.awrap_tool_call
                 -> ToolNode._execute_tool_async
                    -> BaseTool.arun / 具体工具
```

返回沿相反方向：ToolError返回ToolMessage -> _record_tool_call记tool_failed -> plan正常前进 -> dispatch领域事件outcome=error_result -> ToolNode结果归并 -> 图路由回模型。ToolError在调度内侧，不能让调度先标failed再补消息；在guard内侧，不能将提前拒绝的调用误报为已执行。

after_model原顺序保持：模型门禁/停止 -> HITL实际中断或恢复处理 -> 批次计划（排除已有拒绝结果）-> ToolNode路由。新增ToolError没有after_model hook，不改变现有HITL/规划相对顺序。

无工具错误时原成功结果原样返回。schema参数校验继续由handle_validation_error/ToolNode处理；未知工具名、截断/不匹配、审批拒绝、未开始工具因停止而跳过，仍走现有消息或控制路径，不重复经过on_error。

## 5. 六个文件工具的迁移

| 文件/方法 | 修改 |
|---|---|
| tools/read.py：_read、create_read_tool、create_session_read_tool | 保留Unicode/ValueError/OSError具体原因映射和提示；删普通except Exception通用转换，使意外异常直达ToolError。保留控制传播。工厂保留handle_validation_error，不再设置handle_tool_error；删SAFE_READ_ERRORS/_safe_read_error/_set_safe_tool_errors及仅服务旧兜底的文本 |
| tools/ls.py：_list_directory、两个工厂 | 同上，保留目录/审批/IO语义，删SAFE_LS_ERRORS/_safe_ls_error/_set_safe_tool_errors和旧普通异常兜底 |
| tools/find.py：_find_files、两个工厂 | 保留glob、rg缺失、预算/目标/IO提示，删SAFE_FIND_ERRORS/_safe_find_error/_set_safe_errors和普通异常兜底 |
| tools/grep.py：_grep_files、两个工厂 | 保留regex/glob、权限、编码、预算/IO及单文件跳过分支，删SAFE_GREP_ERRORS/_safe_grep_error/_set_safe_errors和顶层普通异常兜底；不能删除内部必要的逐文件处理 |
| tools/write.py：_replace_temp、create_session_write_tool | _replace_temp记录提交阶段事实，抛ToolException，固定提示File write outcome is unknown; inspect the file before another write.，保留cause；删WriteOutcomeUnknown类及工厂针对它的日志再抛包装。删SAFE_WRITE_ERRORS/_safe_write_error/handle_tool_error设置，保留具体原因映射、限制、锁与清理 |
| tools/edit.py：_copy_to_temp调用链、create_session_edit_tool | 继续复用_replace_temp，替换失败自动采用同一新反馈；删WriteOutcomeUnknown导入及工厂捕获，删SAFE_EDIT_ERRORS/_safe_edit_error/handle_tool_error设置。匹配、快照、重叠、冲突及清理保持 |

每个普通诊断不机械记录两遍：移除被统一回调接管的普通类型日志；有phase/资源清理事实的日志保留。移除提示前先搜索实际消费者，handle_validation_error仍使用的文本不得删除。

tools/bash.py仅在现有metadata添加延后开关False，不改执行、异常、handle_tool_error或输出。这里的一行装配声明是确保Bash延后边界所需，不是Bash重设计。

## 6. 四个模拟分支与控制反例

### 6.1 成功

read#r返回正文 -> BaseTool构造success消息 -> ToolError原样返回 -> 调度前进/成功事件 -> 模型读到正文。正常结果/工具schema和提示词指纹不因准备副本变化。

### 6.2 已识别失败

read#r解码抛UnicodeDecodeError -> _read映射为ToolException(INVALID_ENCODING)，保留cause -> 副本BaseTool不内部转换 -> on_error返回原INVALID_ENCODING -> 官方生成同ID error消息 -> 领域事件error_result -> 后项/模型可继续。具体提示与迁移前逐字一致，日志可关联原原因类型，不回传原字节。

### 6.3 意外异常

串行批次A/B/C，B工具抛KeyError -> on_error返回通用说明 -> B为同ID error消息，plan.failed仍False，position继续 -> C正常执行 -> 模型看到A成功/B失败/C成功。没有依赖图，不推断C应取消；并行场景仍等待已开始的IO按现有settle收尾。

### 6.4 写入提交未确认

write#w或edit#e调用_replace_temp -> os.replace抛OSError -> 固定说明的ToolException -> on_error/ToolError生成错误消息 -> 后项/模型继续。目标可能已改也可能未改，测试分别注入提交前失败和替换后失败，不伪称目标一定未动。不设置manual_review_required；下一次同会话调用可进入；不会自动重试本次写入。

### 6.5 保留路径

审批GraphInterrupt -> 框架保存/暂停，原HITL处理；UserStop -> StopModelMiddleware/边界结束；外部CancelledError -> settle后原样传播。ToolBatchSchedulingError在调度层失败；模型门禁/上下文/必要保存分别在原层处理。未完成工具配对或真正取消仍可能触发原人工核对，不删除整个会话门禁。

## 7. 删除旧写入异常的全链路接线

WriteOutcomeUnknown只有内部工具与已搜索到的项目测试消费者，不把可import视为长期外部API；按已确认行为迁移删除，不留别名/废弃包装。

| 文件/类/方法 | 必要同步 |
|---|---|
| agent_runtime/errors.py：RuntimeErrorPolicy.describe | 删除WriteOutcomeUnknown导入与保护分类，仅保留BashOutcomeUnknown的未知结果合同 |
| agent_runtime/call_observation.py：observe_tool_dispatch/aobserve_tool_dispatch | 删除WriteOutcomeUnknown分类；已转换写入错误正常记录error_result，Bash仍可outcome_unknown；不修改事件schema |
| application/session_service.py：SessionService._run_steps/_resume_steps | 删除WriteOutcomeUnknown导入/显式锁会话条件及write_event残余选择。保留BashOutcomeUnknown、partial_tools和外部取消所需门禁 |
| application/cli.py：_run_and_print/_resume_and_print | 删除WriteOutcomeUnknown捕获/导入，保留Bash部分输出和人工核对展示。写入错误从消息/工具卡展示，不再走run失败文案 |

不改SessionPersistence/SQLite schema，不自动清除既有review_required数据。新的写入失败完整配对并且必要保存成功时按正常完成路径保存；若随后保存失败，仍是SessionPersistenceError，不能由工具成功反馈覆盖。

Web/前端已有error_result和错误消息正文投影，无需新增事件type/字段或改组件；写入失败从原run_failed+待核对变为工具error_result+正常模型续跑。Bash仍会使用outcome_unknown。原生LangChain tracing会由BaseTool内部handled/end变为原始tool-error后外层ToolError恢复，需明确这是框架trace变化；项目领域事件保持上述合同。

## 8. 自定义工具与直接调用测试

- 所有参与工具使用同一新合同：ToolException明确提供公开具体提示，普通异常走通用反馈，不要求自定义工具增加副本、格式器、白名单或异常子类。
- 启用旧handle_tool_error配置时装配拒绝，并提示将具体反馈放回ToolException产生处；这是用户明确选择的新合同，不提供兼容层。
- 不原地修改工具；直接invoke执行失败按第2节抛ToolException。tools=[]和Bash-only不额外注入ToolError/ToolNode。
- 后续新工具注册只需提交工具对象与真实能力/权限信息，统一挂载ToolError；不再为每个工具登记转换函数。当前不提前实现注册表。

Read/ls/find/grep现有call_*测试辅助函数直接invoke工具。消息反馈断言改从真实build_runtime_agent编译图进入，复用test_agent_runtime.ToolCapableFake；新增tests/tool_feedback_helpers.py中的invoke_tool_feedback集中这一重复入口（已有四组同职责消费者），不是复制错误转换或新测试框架。同步schema/文件内容/权限/预算/脱敏断言保留。GraphInterrupt的底层传播测试仍直接调用；另以Agent测试审批暂停，不要求外层一定raise。

旧白名单测试改为新合同：ToolException具体提示保留，普通RuntimeError正文不回传；不继续断言ToolException的明确公开文本必须被隐藏。旧fatal普通RuntimeError测试改为错误结果与后项继续，同时将必须上抛的覆盖迁至真实调度/取消/保存故障注入，保留原异常身份和IO收尾断言；不拿在工具体伪抛调度异常替代真实调度测试。

## 9. 验证、实施顺序与证据

实施时先红灯：新增tests/test_tool_error_feedback.py覆盖新工具合同校验、不改原对象、ToolException具体文本、意外异常、sync/async同合同、空工具、Bash排除与控制传播；不增加已撤回的副本/旧格式器适配测试。再接装配和六工具迁移，最后同步旧WriteOutcomeUnknown消费者与测试；每片出现非预期红灯先定位，不在红灯上扩展。

迁移/扩展测试文件：test_read_tool、test_ls_tool、test_find_tool、test_grep_tool、test_write_tool、test_edit_tool、test_application_write、test_application_run_events、test_async_tool_batch、test_tool_batch_scheduling、test_tool_subscription_boundaries、test_async_failures、test_active_stop_failures、test_classifier_contract、test_layered_failure_contract、test_agent_runtime_tool_events、test_cli_event_display。审批注册/恢复、消息与turn、持久化、Web投影及Bash既有测试作回归保护。

最终命令：

```sh
.venv/bin/python -m pytest -q --tb=short
.venv/bin/python -m compileall -q naiyue_test_agent scripts tests
.venv/bin/python -m pip check
npm --prefix frontend test -- --run
npm --prefix frontend run build
git diff --check
```

再用rg核查生产/scripts/tests不再消费WriteOutcomeUnknown、SAFE六工具ERRORS或旧安全回调；Bash和用户原生handle_tool_error适配引用必须保留，不以全局删除该名字作为验收。生产文件≤500行，新增函数真实接入同步/异步装配。

文档同步README当前错误入口、future-work/handoff；旧Lesson66/68/69/71加版本边界指向本Spec，保留历史事实，不重写用户掌握记录；不修改关键知识点文档。Spec为本次权威设计，不扩大到权限模式、ToolSpec注册表或沙箱。

本轮仅设计验证：已沿当前源码核对接线；早先副本/旧格式器临时探针只证明已撤回方案的技术可行性，不作为采用理由。此前六个控制/写入/保存既有测试6 passed in0.86s是旧行为证据，不是本Spec实现验收。无供应商请求、生产效果或全量新行为通过的声明。

本方案本身不改外部状态或数据。实施后若方向调整，按原子代码提交回退；已经真实执行的文件/外部副作用不能由代码回退撤销，回退也不自动迁移旧会话状态。此处不安排提交、push、发布或部署。

## 10. 已认可的职责布局，待完整方案收束后实施

用户认可先分工具能力与运行机制、按职责归组：具体read/write等能力及其权限/审批描述归独立naiyue_test_agent/tools包；框架ToolError接线、批次调度和工具调用观察归agent_runtime/tool_execution职责组。前者建议保留read/ls/find/grep/write/edit/bash/bash_output并归入approval/permissions；后者归组batch/async_batch/feedback/observation。不新增逻辑层、空包或注册平台，原runtime根目录平铺tool_errors.py方案撤回。

现有工具依赖tool_approval/file_permissions，Bash还依赖run_services；需要结合后续工具注册与Bash设计确认依赖传递，再列具体搬迁方法/消费者。当前不新建空包、不移动工具、不顺便重组模型/上下文/事件整个运行层。2026-10-15用户要求继续讨论权限管线、是否保留批量审批及串行/并行调度，所有相关设计收束后再一起实施。
