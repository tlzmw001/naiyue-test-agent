# Spec 065：同步 turn 生命周期与审批延续

状态：2026-10-08，用户“👌做吧”授权上一轮范围。本片已实施并验证。以下保留执行契约与实际结果；工具调度、审批规则、模型重试、CLI 或持久化不在修改范围。

## 目标与现状

Spec 062–064 已有应用 run、模型/工具、完整消息事件。一个 turn 定义为一次逻辑助手响应及其对应工具批次的处理。SDK 重试、限定溢出恢复和摘要请求不增加普通 turn；同 run 可以多个 turn。审批恢复更换 run_id，但延续未完成 turn。

当前调用链：应用接受 run/准备资源 → 绑定 run 发布器 → runtime 建立 context → 私有同步状态 adapter → 模型选模 wrapper/模型响应门禁 → after_model（包含 HITL）→ tools → 再次 model 或结束 → 应用 finalize/cleanup/终局。完整消息以 values 确认，异常后只读 recovery_view；wrapper accepted 不表示状态写入或后置检查成功。

## 参照与框架依据

- Pi 本机 a69bef789：agent-loop.ts 在助手响应前 turn_start，在对应工具结果处理后 turn_end；error/aborted 也走终局事件。types.ts 的 turn_end 带 message/toolResults，没有通用 turn ID。Pi 没有本项目 LangGraph HITL 跨 invoke 的审批形式；稳定 turn ID/可序列化延续是本项目对此机制的适配，不声称照搬 Pi。
- AgentX 本机 3fcfa0e：AbstractMessageHandler.processChat 注册 onCompleteResponse/onToolExecuted，保存模型/工具展示记录并调用模型/工具完成与聊天终局 hook；用于对照结果、消息保存与运行结束的不同边界，不将 Java LangChain4j 回调当 Python 节点契约。
- 当前 LangChain 1.4.2 / LangGraph 1.2.12 的本机源码与 [middleware 文档](https://docs.langchain.com/oss/python/langchain/middleware/custom)、[状态流文档](https://docs.langchain.com/oss/python/langgraph/streaming)支持复用 node hooks、wrap_model_call 和 values/updates。after_model 按 middleware 反向运行；新增轻量边界 hook 放列表最前，等待现有 HITL/调度等后置检查通过。不重写 StateGraph 或 reducer。

## 事件与标识

仍使用 app.subscribe_events 和同一个 per-run sequence。新增：

| 事件 | 时点/载荷 |
| --- | --- |
| turn_started | 已选模型、即将进入原逻辑 model handler；turn_id、origin_run_id、model_call_id、kind=model。失败审批重试是新 turn，kind=approval_retry、previous_turn_id 可用时指向旧失败 turn，model_call_id=None，不伪造新模型请求。 |
| turn_resumed | 恢复既有 suspended turn、图执行前；原 turn_id/origin_run_id/model_call_id，kind=approval_resume；不重复 started。 |
| turn_suspended | 应用验证并安装 pending 审批后；未完成逻辑 turn，status=suspended、已观察助手/工具结果 ID 和工具错误计数。 |
| turn_finished | 消息状态已确认且后置检查通过、所有期待工具结果可读时 completed；普通主异常退出后 failed，带现有受控 RunFailureData。完成含 error ToolMessage 仍可 completed，error_result_count 单独报告。 |
| turn_observation_unavailable | 原生状态观察降级或正常图结果无法满足观察契约时安全诊断；不为满足配对伪造 completed，不改原结果/异常。 |

冻结载荷只含身份、有限状态、元组和安全失败字段，无正文/工具参数。完整内容仍通过 Spec 064 消息快照关联；默认日志白名单加入 turn 字段/计数，不记录延续对象。turn_id 由运行层生成；origin_run_id 表示首次开始该 turn 的 run。model_call_id 与 message_id/tool_call_id 分别保留其意义。

发布器验证同 run 的 started/resumed → suspended/finished 配对；跨 run 的延续由应用保存的记录验证，不靠全局事件缓存。进程/框架控制异常和发布基础故障仍可能无完整终局，不伪造 cancelled。

## 运行层实现

新增 TurnObserver，只保存本 turn 的有限关联/结果状态，用锁保护 hook 与状态消费者之间的访问。模型 wrapper 在现有 model_call_started 之前 begin，在门禁通过后记实际响应对象/期待调用 ID。响应对象只在本次活跃 turn 内用于取得框架真实 ID，不持久化。

新增 TurnBoundaryMiddleware.after_model，只标记所有后置检查已经运行到边界；返回 None，不更新消息或路由。完整消息观察 adapter 在 values 发布消息之后，将真实状态交给 TurnObserver，再尝试完成；updates 也允许尝试完成已确认结果。下一模型 begin 与应用 result 收尾再次复核边界，解决无状态更新的 hook 不产生新 values 的情况。

该额外图节点只由正式 build_local_application 通过私有装配开关注册。旧 build_runtime_agent/build_configured_runtime_agent 及公开交互装配保留原节点数，保护已有 recursion_limit=6 诊断契约；不开启 hook 的旧图不宣称具备 turn 完成观察。

只在当前助手消息之后查找对应 ToolMessage，不匹配旧历史里碰巧重复的 tool_call_id。结构化输出产生的人工 ToolMessage 和拒绝审批回填同样算对应结果，不要求 tools 节点。failed 恢复视图可以收集部分 ID/错误计数，但不产生 completed。

新增 per-run TurnObserver 的 ContextVar 绑定，runtime 仅在同一应用发布器匹配时写入 Runtime.context；工具线程从显式 context 获得关联，事件通知不承担权限。无应用发布器直接入口保持原行为。

私有同步 adapter 在开始审批恢复前激活延续；状态接口不支持/基线读取失败则禁用本次 turn 观察并报告不可用，原 invoke 继续。正常返回但不能确认完整边界也仅报告 unavailable。关闭/补读/报告次级异常不覆盖原主异常。

## 应用层与序列化

RunObservation 为每 run 构造 TurnObserver，新增 bind() 上下文管理器同时绑定发布器与 turn observer；三个调用点统一使用它，第三次重复时抽取公共绑定逻辑。SessionService 新增 pending_turn，初始 None；审批恢复接受前用 pending_turn 恢复 observer，不改公开签名。

pending_turn 是版本 1 的普通 dict/list/str/int/bool/None，只含 turn/origin/model-call/助手/工具结果 ID、错误结果计数、post_model_checks_passed 及 suspended/failed 状态。没有模型对象、消息正文、发布器、锁或 ContextVar。finally 会话锁内、释放 busy 前保存快照；没有 pending_tool_approval 时清空。暂停保持 suspended；执行失败但原审批仍可恢复时保存 failed 尝试记录，下一恢复生成新 turn，不能把旧 failed 改成 completed。准备阶段失败未开始 resumed，不终结原等待 turn。

失败工具从 LangGraph tools 节点恢复时会跳过已经完成的 after_model，因此恢复保存的 post_model_checks_passed，不能一律重置 False。审批尚未通过时该字段 False，恢复后才由边界 hook 标记。没有新增模型请求的 approval_retry 可以再次暂停/恢复，model_call_id 保持 None，不虚构请求 ID。

普通错误仍由应用记录 run_failed 并原样上抛；RunObservation.failed 在安全分类后补 turn_failed 终局，发布次级错误仅记安全类型。after_turn 停止允许当前 turn completed，而 run_finished 为 stopped。应用 finalize/cleanup 失败不会反向篡改已经完成的 turn。

```text
SessionService（延续 dict，锁内保存）
  → RunObservation（每 run 的 TurnObserver 与发布器）
  → ContextVar 桥接 → Runtime.context
  → 选模 wrapper：begin / 接受响应
  → 最后 after_model：标记检查通过
  → 状态 adapter：先消息事件，再结果关联/turn finish
  → 应用：暂停快照或主异常终局 → cleanup → run_finished
```

## 修改范围

- 新 agent_runtime/turn_events.py：有限冻结载荷，避免 events.py 超过 500 行。
- 新 agent_runtime/turn_observation.py：每 run 状态观察、延续序列化、绑定与 after_model hook。
- events.py：注册和配对，公开 RunEventPublisher.run_id 只读属性；不改构造/发布方法签名。
- call_observation.py、message_observation.py、runtime/__init__.py：在既有边界接入 turn，保留原逻辑。
- application/__init__.py、run_events.py、session_service.py：正式装配开启边界 hook，每 run observer、异常/结果收尾、pending_turn 保存。
- 新 turn 单测与实际图/失败测试；旧 Spec 062/063/064 测试按事件族或实际全量序号同步，不放宽业务断言，不修改 conftest。
- 现有 probe_message_events.py 增加 turn 配对断言；阶段 Spec/README/交接/计划/待办同步。

超过 8 个文件，是一个跨层接线切片。触及代码均保持 ≤500 行；没有依赖、环境配置、DB 或公开签名变更。可撤回观察接线而不迁移会话存储；当前仅进程内新增元数据。

## TDD 与验收

先写失败测试：文本回复一 turn、工具响应加结果一 turn/最终文本第二 turn、parallel/顺序批次、可恢复工具错误与截断、结构化人工结果、审批批准/拒绝沿用 turn、新 run/旧 origin、失败后部分恢复状态、停止、输入排队多轮、后置检查失败不提前 completed、限定恢复仍一逻辑 turn、两个会话隔离、只读延续 JSON 往返/不含对象、准备失败与失败审批重试、消费者故障/原异常保持。

核对 native invoke 返回/审批与原策略不变。每个已开始且普通终局可观测的 turn 正好一个 finished 或 suspended；跨审批 resumed 同一个 ID，failed 重试不同 ID。历史/摘要不生成助手 turn，未实际进入模型 handler 的准备故障不生成 turn。

最终全量 pytest、compileall、pip check、显式文件行数/空白检查；沿现有受控脚本最多 2 逻辑模型请求、1 纯工具、95 秒总时限做真实验证。真实模型结果仅为接线证据，不代表生产压力/业务质量。CLI 内容消费、活动取消、正式异步与最小持久会话保持后续独立工作。

## 实际实现与验证（2026-10-08）

- 初始全量基线：`.venv/bin/python -m pytest -q` → `635 passed in 15.89s`。
- TDD 首轮红灯：实际应用 turn 用例 → `7 failed in 0.73s`，原因是原链路没有 turn 事件；单测因新模块不存在收集失败。最小接线后 13 例通过。后补失败/控制/降级测试不全部称为先红。
- 实际普通一工具循环新增 4 个 turn 事件，完整序号为 run_started(1)、Human(2)、turn_started(3)、model_started/finished(4/5)、AI 请求(6)、tool_started/finished(7/8)、ToolMessage(9)、turn_finished(10)、turn_started(11)、model_started/finished(12/13)、AI 回答(14)、turn_finished(15)、run_finished(16)。消息先于对应 turn_finished；序号表示观察顺序，不是持久保存确认或跨会话全序。
- 审批初次暂停：started → suspended，run 为 interrupted；恢复：新 run_id、原 turn_id/origin_run_id/model_call_id，resumed → completed，再开启新模型 turn。应用释放 busy 前在同一锁内保存 pending_turn；其 JSON 往返和修改副本不影响活跃状态均有测试。
- 两个新增边界按红绿验证：没有新模型请求的审批重试再次暂停时，resumed 的 model_call_id 必须允许 None；旧简单运行入口多一个 after_model 节点导致 recursion_limit=6 失败，改为仅正式应用开启私有装配开关后通过，原诊断预算没有放宽。
- 真实失败审批重试测试初版在 create_agent 构造后替换工具 hook，已捕获的绑定方法不受影响，故没有抛出预期异常。修正故障注入时点到构造前，成功复现 tools 阶段失败，随后观察到重试 turn 无 completed；get_state.next 实际为 ('tools',)，后置 hook 不会重跑。补 post_model_checks_passed 的 JSON bool 保存/恢复后，同一反例通过。前一个是测试接线错误，后一个是实现缺陷，不隐藏或混为框架问题。
- 三处作用域绑定按第三次重复规则统一为 RunObservation.bind；缺方法测试先红，最小实现后验证原异常与两个 ContextVar 都正确复位。
- 全量最终：`.venv/bin/python -m pytest -q` → `671 passed in 16.12s`，新增 36 例。覆盖原结果/异常、后置检查失败、模型拒绝、fatal pending、截断/可恢复错误、结构化人工结果、两会话隔离、Steering/Follow-up、停止、审批准备失败/重试、内容脱敏、观察降级/缺确认、消费者/发布次级异常、延续原生 JSON 与别名隔离。旧 Spec 062/063 的全量序号及真实探针文本同步新增事件，原业务/安全断言保留，未改 conftest 或 skip 用例。
- `.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` → 退出 0；项目没有独立打包/应用构建目标，此项为语法编译验证。
- `.venv/bin/python -m pip check` → `No broken requirements found.`。
- 本片 21 个文件的存在/代码行数/尾部空白检查 → `touched_file_structure=PASS files=21 python_line_limits=PASS trailing_whitespace=none`。仓库仍无提交且文件未跟踪，git diff --check 的退出 0 不代替对这些文件的显式检查。
- `.venv/bin/python -m scripts.probe_message_events` → `real_message_events=PASS events=16 messages=4 model_calls=2 tool_calls=1 turns=2 pairing=verified terminal=completed`。默认配置 `heiyu-gpt-5.5` / `gpt-5.5`；两次逻辑请求、一纯工具、四条消息、两个不同 turn 的身份/结果/顺序配对通过。没有打印消息正文、配置或凭证，没有改 .env。

## 已知边界与唯一下一步

本片是正式同步应用的 turn 观察，仍不是 token 流或原生异步。不开启后置 hook 的旧入口、定制 invoke、不可用状态观察不会伪报 turn 完成。普通进程/框架控制和发布基础故障可能缺终局；没有新增 cancelled，也不能保证持久交付。观察到 recovery_view 部分结果不等于副作用成功、回滚或可安全自动重试，原人工核对阻断保留。

pending_turn 只是进程内 JSON 元数据，没有重启恢复/save/load；审批失败重试保留既有恢复入口语义，不新增通用副作用去重或改变工具重放策略。日志仍为元数据、回调仍本地受信任同步消费者；没有完整控制/上下文过程订阅、CLI 消费迁移或生产压力/业务效果评测。

唯一下一步：按 Spec 061 进入控制请求事件的最小切片设计，先收敛 Steering/Follow-up/停止/审批的“接受、实际交付或生效、丢弃”时点与安全载荷；随后再补上下文过程和 CLI 消费，进入最小持久会话。新增 turn 核心代码尚未一起阅读，保留学习待办，不将测试通过算用户掌握。
