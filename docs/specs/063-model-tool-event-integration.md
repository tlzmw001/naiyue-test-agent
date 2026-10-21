# Spec 063：模型与工具同步生命周期事件

状态：2026-10-08，已按用户授权 TDD 接线；验证见第 7 节。仅同步生命周期元数据，不代表消息、turn、异步或活动取消已完成。

版本边界：本文件保留063时点的逻辑模型事件合同。2026-10-20的[Spec118-01](118-request-preparation-and-context-budget.md#13-118-01实际实现与验收)已统一为callback来源model_call_*，按invocation_id配对，终态returned/failed/cancelled；不再使用本文旧逻辑accepted模型载荷。选模、门禁及turn/run失败职责仍保留，工具合同不因本次改名改变。

## 目标与来源

沿 Spec 061 第二片，把 app.subscribe_events 的同一每 run 发布器接到模型和工具处理。保持原结果、异常、消息、调度、审批、旧日志和公开函数签名。本片没有正文、token delta、工具实时输出、turn/消息完成、CLI 展示迁移、异步或活动取消。

本机前置探针：HITL 暂停和拒绝的旧 tool wrapper 事件均为空；拒绝后出现同 ID error ToolMessage。当前 HumanInTheLoopMiddleware.after_model 在恢复后返回人工 ToolMessage，工具调度过滤已完成调用。因此只在工具 wrapper 打点会漏掉拒绝。

Pi a69bef789 的 loop 区分模型消息和工具执行 start/end/error；AgentX 3fcfa0e 的 TokenStream 工具完成回调与追踪分离。本项目事件边界按 Python 实际链路定义，不把调度入口等同执行器入口。模型 wrapper 结束在 after_model/HITL/工具计划之前，不能把它解释为整个 run 或 checkpoint 成功。

## 注入与兼容

- 应用只在现有 runtime 调用外围绑定每 run 发布器；用 ContextVar 的作用域桥接保留 invoke_agent_run/resource/resume 公开签名及既有测试注入点。绑定结束必须 reset(token)。
- _invoke_agent_run 在调用线程读取匹配 session_id/run_id 的绑定，再显式放入 Runtime.context 的私有 event_publisher 字段。模型/工具线程只从该 context 读取，不依赖线程池继承 ContextVar。
- 不匹配 ID 或没有绑定的直接旧入口不生产新订阅事件、不伪造关联。嵌套作用域恢复父绑定，跨应用/线程隔离。发布器不进入消息状态/checkpoint。
- 新事件仍经默认 application_event 日志白名单输出；普通用户消费者故障沿 Spec 062 隔离。原异常优先，次级事件报告故障安全记录。

## 事件载荷

扩展 RuntimeEvent.data 联合与发布器合法时序；所有载荷冻结，仅包含标识、固定枚举、计数、用量和安全诊断。模型调用 ID 由项目生成，非供应商 HTTP request ID。工具沿用 tool_call_id，活跃同 ID 不可重复开始，结束后允许后续模型轮次重用；消费者按 run/序号配对，不依赖时间戳排序。

| 事件 | 字段与含义 |
| --- | --- |
| model_call_started | model_call_id、model_id、purpose=agent；进入选模后的模型 wrapper，可能包含其内部限定恢复，不是 SDK 的每次 HTTP 尝试 |
| model_call_finished | 同 ID/model_id；status=accepted/failed；accepted 表示当前 wrapper 响应门禁通过，含 normalized stop_reason、tool_call_ids、可用 input/output/cache_read_tokens；failed 只有安全 failure |
| tool_call_started | tool_call_id、tool_name、boundary=dispatch/approval_resolution；开始处理一个调用，不保证函数实际执行 |
| tool_call_finished | 同 ID/name/boundary；outcome=success/error_result/not_executed/exception/outcome_unknown；固定 reason 和可选安全 failure |

accepted 不代表 graph state 已提交，也不包括后置 ToolBatch 的重复 ID 等检查。模型调用可能 accepted 后因 after_model/工具/状态故障 run_failed，两个事实并不冲突。真实摘要和重发尝试详细事件不在本片新增，已有上下文日志保留；SDK 内部重试不可观察时不伪造尝试事件。

模型/工具标签采用受控元数据投影；无法用作安全标识的名称用空值表达，不抛事件校验错误改变实际调用。计数/用量仅保留非负整数，未知为 None。不得输出响应内容、工具参数/结果、审批目标、原始错误、thread_id。

## 接线位置

1. 选模 wrapper：model_selected 后发 started，继续调用原 handler、stop reason 归一化与门禁；检查通过才 finished(accepted)，已知普通异常 finished(failed) 后原样 raise。未选出模型前失败只有应用 failed。控制异常不伪造 failed。
2. ToolBatchMiddleware 外围观察 dispatch，覆盖等待、正常内层调用、批次跳过和调度异常。只读并发/整批顺序行为不变。内层 _record_tool_call 继续原日志和截断/ID 守卫；观察共享纯守卫原因，不修改返回 ToolMessage。
3. 调度未进入内层链且返回错误结果是 prior_batch_failed/not_executed；模型截断或绑定不一致是明确 not_executed；参数/工具可恢复错误是 error_result，不声称执行器已运行；未处理异常仍上抛，Write/Bash outcome unknown 保持人工核对阻断。
4. 观察型 HITL 子类仅包装 after_model 的原返回值；返回的人工错误 ToolMessage 产生 boundary=approval_resolution 的 start/end(not_executed, approval_rejected)。初次 interrupt 不发工具 start；approve 后由正常 dispatch 报告。不解析自然语言拒绝内容，不修改框架结果。

正常一工具链：run_started → model start/accepted → tool dispatch start/finish → model start/accepted → run_finished。拒绝恢复：新 run_started → approval_resolution start/not_executed → 后续 model → run_finished。顺序跳过仍报告其未执行原因；并发只约束每个调用 start 先于 finish，不约束工具之间的完成顺序。

## 文件范围

- agent_runtime/events.py：新增有限模型/工具载荷、开始/结束配对、绑定作用域；保持 run 生命周期和 observer 行为。
- 新 agent_runtime/call_observation.py：迁移已有模型选型/工具日志 wrapper 保持兼容，并增加观察帮助函数及 HITL 返回观察；root 文件已 468 行，不能继续堆进去。超过 500 时按真实职责拆模块并更新 Spec。
- agent_runtime/__init__.py：重导出原模型工厂/_record_tool_call，扩展 RunContext、调用前读取绑定，装配观察型 HITL；原公开签名不变。
- agent_runtime/tool_batch.py：在原调度外增加观察；借助内层进入标志和共享守卫区分 not_executed；不修改调度模式/等待策略。
- application/session_service.py：三种普通/资源/审批恢复调用外围绑定 observation.publisher，无公开 API 改动。
- application/run_events.py：模型/工具元数据日志白名单；application_event 仍为同一来源。
- 新 tests/test_model_tool_subscriptions.py 与 tests/test_tool_subscription_boundaries.py：真实应用编译图/假模型、审批、并发/顺序与故障测试。
- tests/test_run_events.py 增加配对/冻结/作用域测试；tests/test_application_run_events.py 的 run-only 收集 helper、失败测试复用 helper：Spec 062 测试只过滤新增的中间事件，保留对应用 run 生命周期的全部原断言。此测试 helper 改动因合法中间事件新增，不能去掉原异常/清理/关联断言。

涉及超过 8 个文件；核心是事件接线和为 500 行限制迁移已有 wrapper，不改变工具业务功能，不升级依赖或改 .env。未新增服务。

## TDD 验收

先保存旧源码以对照迁移，再确认新订阅测试因只有 run 事件而红灯；配对/作用域的模块 API 也须观察预期失败。实现后定向与全量回归、compileall、pip check、行数/公开签名检查。

覆盖：一次工具完整事件与同 run 序号、纯文本/工具错误继续、模型异常/门禁拒绝无 accepted、元数据脱敏、双会话/嵌套绑定隔离、直接旧入口不生产新关联、消费者普通故障不改变结果；审批暂停/approve/reject/混合决定/再次中断；截断保护、不匹配 ID、参数错误、顺序批次 fatal 后跳过、并发 barrier；副作用未知终局阻断；限定输入恢复只报告一个逻辑 model call，原工具不重放。

真实模型验证沿现有授权，父进程限时、关闭托管 tracing、使用现有配置，一次无副作用探针工具；单独核对模型/工具事件和调用 ID，不以真实单次成功代替故障测试。旧 run 事件和原消息/异常语义的测试保持。

## 7. 实际结果与边界

- 基线全量 581 passed in 15.70s。新增 15 场景红灯 15 failed in 0.76s，暴露只有 run 事件/缺失作用域 API；实现后 15 passed in 0.73s。载荷可变 subject 的 4 个反例先 4 failed，补有限字段校验后转绿。补配对、嵌套绑定、混合审批和临时真实文件 read/write/read，最终定向 `.venv/bin/python -m pytest -q tests/test_run_events.py tests/test_model_tool_subscriptions.py tests/test_tool_subscription_boundaries.py --tb=short` 为 34 passed in 0.67s。
- 既有组合定向初轮 3 failed, 81 passed，因为合法中间事件使 run-only 序号不再连续。Spec 062 helper 只收应用 run 生命周期；成功精确序号从 1/2 改为 1/4，审批暂停/恢复从 1/2/1/2 改为 1/4/1/6，双会话各自 1/4。保留原结果、关联、清理、异常对象和日志白名单断言，不改业务验证。更新后 84 passed in 2.91s；新完整事件测试逐项验证全部序号连续。
- 最终全量 `.venv/bin/python -m pytest -q` 输出 605 passed in 15.73s；`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` 退出 0；`.venv/bin/python -m pip check` 为 No broken requirements found.。相对基线新增 24 项，其中 18 个新集成测试和 6 个事件配对/作用域/冻结校验。
- 已接线：应用三条 runtime 调用绑定同一发布器，_invoke 在当前调用线程读取匹配 ID 的绑定并放入 context；并发工具从 ToolRuntime.context 消费。新的 call_observation.py 保存原门禁/日志与同步观察，ToolBatch 外围包住原调度，观察型 HITL 处理拒绝人工消息。公开 runtime 既有函数签名经 AST 对照不变；原资源/审批入口、CLI、异常与工具结果保持，无 .env/依赖更改。
- 当前 InMemorySaver 的无网络 checkpoint 探针检查 checkpoint 和 metadata 均无 _run_event_publisher，输出 runtime_publisher_checkpoint_probe=PASS absent_from_checkpoint_and_metadata。只覆盖本机内存后端，不作为未来持久 saver 的序列化/跨重启证明。
- 真实父进程 120 秒上限探针（关闭托管 tracing、既有默认配置）实际输出：real_model_tool_events=PASS events=8 model_calls=2 tool_calls=1 pairing=verified terminal=completed。工具仅返回新随机值，无文件/外部副作用；验证工具恰好一次、模型最终值来自工具、实际 ToolMessage ID 与工具事件一致、统一 run 和序号 1–8。一次正常网关请求不是模型身份、任意供应商、失败恢复或业务质量证据。
- 产品行数：runtime root 346、events 262、call_observation 257、tool_batch 158、session_service 475、run_events 112，均低于 500；新测试也低于 500。没有提交/发布/清理用户 untracked 工作树，没有修改关键知识点。
- 限制：模型 accepted 仅是 wrapper 门禁通过，后续 after_model/审批/状态可能失败。用量来自该返回 AIMessage，不是包含摘要、限定重发或 SDK 隐藏重试的总费用；恢复测试证实两次正常请求+一次摘要只报告一个逻辑 model call。工具 started 是 dispatch/approval_resolution 处理入口，参数错误也不证明执行器进入。工具内部控制中断/进程控制和发布基础故障可能只有 start，不能强行补 failed/success；活动取消终局仍独立实施。不同工具不保证全序，元数据事件不携带正文，不宣称整个事件系统或沙箱完成。
- 用户尚未逐行阅读本片新增代码；按最新指令暂停教学、推进工程。下一片先讨论消息/turn 的状态可见点，不能把本片 wrapper 返回事件冒充消息已提交或持久保存。
