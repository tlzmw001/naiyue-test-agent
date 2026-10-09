# Spec 066：控制请求的接受、交付／生效与清理事件

状态：2026-10-08，用户授权继续开发。本片已实施并验证；上下文过程、CLI 消费、异步或持久化不在本片范围。

## 当前链路与参照

应用 steer/follow_up/stop 在会话锁内验证并修改原字符串队列/停止标志，锁外仅写旧日志。runtime before_model/after_agent 调用 drain/finish 回调，将文本构造为 HumanMessage；队列取出不等于消息进入状态。审批在应用验证决定、创建 grant、准备资源后调用 Command(resume)，HITL 后置 hook 才处理决定。失败 tools 恢复可能跳过 HITL，不能把本次新决定伪报为已处理。

Pi 本机 a69bef789 的 agent.steer/followUp 入队，agent-loop 在模型轮次边界取 Steering、自然结束取 Follow-up，并通过消息生命周期报告交付；abort 用 AbortController，是本项目仍未实现的活动取消。当前源码未提供本片这套统一 control_id/accepted/discarded 事件，不声称照搬。AgentX 3fcfa0e 的 AbstractMessageHandler 将错误/模型完成回调、消息保存和 transport 终局分开，用于参照接受请求、实际处理和终局的不同观察层次；不是 Python 框架契约。

复用已验证 create_agent、middleware、updates/values 和应用发布器。审批生效取实际 ObservedHumanInTheLoopMiddleware.super.after_model 正常返回这一边界；消息交付以 graph_values/recovery_view 实际 HumanMessage ID 为准。所有原结果、异常、互斥、权限、工具调度、停止和优先级保持。

审批正常返回本身不够：最终文本响应的 HITL hook 也会返回 None。按当前 1.4.2 源码，只读覆写 _create_action_and_config，原方法结果原样返回，记录本次实际创建审批 action 的调用 ID；每次 after_model 先清空候选。仅实际 action ID 集合匹配当前已提交决定且 super 正常返回才 applied。没有重算权限 predicate 或改变 action/决定；依赖私有方法的这一小段需保留版本升级兼容性测试。

## 有限事件契约

新增冻结 ControlData：control_id、kind=steering/follow_up/stop/approval、可用 message_id/visibility、审批 tool_call_ids/decisions 元组及原请求 run ID、有限 boundary/reason。没有正文、文件目标、命令、grant 或凭证。默认日志只写有限元数据/批准拒绝计数，不写决定对象或内容。

| 事件 | 含义 |
| --- | --- |
| control_accepted | 应用检查通过并入原队列/设置停止，或接受完整审批决定。每条 Steering/Follow-up 各一 control_id；重复停止仍幂等。拒绝的 API 调用不产生 accepted。 |
| control_delivery_started | 原队列取出、开始构造消息，或审批 Command 即将交给既有运行入口；不保证已进入状态或被 HITL 处理。 |
| control_delivered | 关联 HumanMessage 已在状态视图可读，带真实 message_id/visibility；不表示供应商已经读取、回答或持久保存。 |
| control_applied | stop 对应已验证的 run_stopped=True，boundary=after_turn；approval 对应实际 HITL 决定处理正常返回，boundary=approval_hook。后者不保证工具执行或结果持久化。 |
| control_discarded | 未交付队列请求、未生效停止，或尚未提交的审批在本 run 清理时丢弃，原因区分 completed/stopped/interrupted/failed/prepare_failed/control_exit。 |
| control_outcome_unknown | 已取出但无法确认状态交付，或已提交审批却没有观察到 HITL 处理。不能因正常图返回就伪造已生效。 |
| approval_requested | pending 审批已由应用验证并安装，只含实际工具调用 ID，不含目标/参数。原请求 run ID 随 pending 保存，跨审批恢复引用。 |

仍用同一 subscribe_events/session/run/sequence；accepted → delivery_started → delivered 或 applied，以及 discarded/unknown 为有限时序。发布器核对阶段和 kind，不引入第二消息总线。普通观察消费者失败继续隔离；控制事件发布基础故障记录安全类型并禁用本 run 控制观察，不改变已入队／停止／审批授权的业务事实；框架/进程控制仍透传，不伪造 cancelled。

## 并发与所有权

应用新增短期 _active_runs：session_id → 本次 RunObservation，在会话锁内登记/移除，不能放入将来要保存的 session 元数据。CLI 无需继承 ContextVar，仍能捕获正确 run 的控制观察器。

每 run 的 ControlObserver 用短记录锁维护有限待交付 ID 和 FIFO 通知 outbox，锁内只登记事实，发布在会话/记录锁外；独立发送锁保证 FIFO。RunObservation.start 发布 run_started 后才打开控制通知，保护 busy 已设置但 started 尚未发布的窗口。任何后续发送者可先发送前面已登记的事实，避免 delivered 超过 accepted；默认消费者仍轻量同步且不得递归控制调用。

队列仍是原 deque[str]，公开 drain/finish 返回 list[str] 不变。记录只存 ID、阶段和队列位置，不保留第二套正文。取出后通过私有 prepare_control_messages 回调给 HumanMessage 分配独立 UUID；runtime 只被动接收消息。控制观察器将实际状态中的 ID 与待交付记录关联。没有新增工具、模型可见标记或按文本匹配，重复相同文本仍有不同身份。直接修改私有队列不伪造 accepted，未追踪项按普通消息处理。

正式循环每次 select 后立即 prepare；如果旧批次被外部调用取走却没有 prepare，新的 select 只保留最新可准备批次，旧请求仍留作 unconfirmed，不能把下一条文本错关联成上一控制的交付。

只有正式应用装配接该回调；旧公开装配、仅 invoke 的 stub 和不可用状态观察维持原运行，不能宣称具有已确认交付。ContextVar 只用于运行侧显式 Runtime.context 传递，仍核对发布器同一身份；工具/模型线程不查应用对象。

终局清理在会话锁内登记 queued/selected/stop/approval 未处理请求的最终状态，捕获旧 observer，再移除 _active_runs 并释放 busy；锁外发送，早于 run_finished。旧 run 清理通知延迟时即使新 run 已开始，也使用旧观察器/ID，不读取新 run 状态。

## 分层与文件

SessionService 已 480 行，不能继续堆控制实现。把既有控制队列方法和旧日志按职责迁至 application/session_controls.py 的内部 mixin；SessionService 保留异常定义与公开导入/方法签名，通过原异常类属性实现同样拒绝行为。run/resume 与会话生命周期仍留原文件，旧日志名称/时点/计数保持。

- 新 agent_runtime/control_events.py：有限冻结载荷。
- 新 agent_runtime/control_observation.py：待交付元数据、FIFO 锁外发送、状态确认、停止/审批/清理与绑定。
- 新 application/session_controls.py：原控制方法、位置登记、私有消息准备回调；不独立拥有会话存储。
- events.py：注册/阶段验证。
- runtime/__init__.py、message_observation.py、call_observation.py：消息回调/观察接线、真实 HITL 生效观察。
- application/__init__.py、run_events.py、session_service.py：正式装配、开始门禁/绑定、活跃 run 映射、审批请求与清理。
- 控制状态单测、真实图/并发/失败边界测试；更新精确事件序号和旧元数据测试族的过滤，保留原业务断言，不改 conftest。
- 现有有界真实探针新增 --with-control：纯工具返回时通过正式 app.steer 入队一条合成指令，验证其实际 HumanMessage 交付；原默认模式不变。仍最多 2 逻辑请求、1 工具和 95 秒时限，不改模型配置。

超过 8 文件，是本片必要的跨层接线与 500 行限制拆分；不改公开 API、原异常类型、依赖、配置或存储选型。

```text
CLI/API → SessionService 控制方法（原验证/队列/flag）
            └→ _active_runs 捕获原 RunObservation
                 └→ ControlObserver 登记有限事实 → 锁外 FIFO 发布
runtime drain/finish → 私有消息准备回调 → HumanMessage 真正入状态
                                         └→ control_delivered
实际 HITL 正常返回 / run_stopped → control_applied
应用锁内清理登记 → 锁外旧 observer 通知 → run_finished
```

## TDD 与验收

先红：运行中接受 Steering/Follow-up 后按优先级实际交付、同文不同 ID、停止幂等与丢弃、失败清理/部分交付、审批批准拒绝与准备失败、失败 tools 恢复未经过 HITL 的 unknown、未实际生效停止、消费者/发布故障、启动前接受窗口、接受登记后延迟通知、双会话隔离和旧 run 清理与新 run 重叠。用 Event/Barrier 同步，禁止紧 sleep。

观察元数据不可变、无正文/目标/决定原参数；delivered 晚于对应消息可读，accepted 早于终局，所有事件序号连续；原结果/异常/旧日志/消息优先级和权限检查不变。全量 pytest、compileall、pip check、代码行数/尾空白与实际接线检查；有界真实模型验证。框架/进程控制和发布基础故障的交付保证、持久化/异步压力保持既有边界，不假报生产成熟。

## 实际验证（2026-10-08）

- 基线 `.venv/bin/python -m pytest -q` → `671 passed in 16.54s`。
- 首批 TDD：实际应用 4 个场景因没有 control/approval_requested 事件红灯（`4 failed in 0.79s`）；单测因新模块不存在收集失败。最小接线后 7 例通过，后补失败/竞态测试不全部算先红。
- 原 run 元数据测试继续按事件族过滤，审批的全量序号由 `[1,8,1,12]` 同步为 `[1,9,1,15]`；新增审批请求与决定事件导致，原 pending 安装/恢复/权限/结果断言不变。没有 skip 或修改 conftest。
- 四项反例先红再修：旧未 prepare 批次错误认领新批次的交付；失败 tools 恢复跳过 HITL、最终文本 hook 返回 None 却被算作新决定 applied；控制发布关闭后 outbox 继续积累历史；发布器仅核对 kind/阶段、没有核对原审批调用/决定/来源。分别采用最新准备批次、实际审批 action 集合、禁用后清空通知、完整 subject 配对修正，业务规则不变。
- 延迟接受通知测试初版重复使用同一个 Fake AIMessage，LangGraph 同 ID 更新旧位置，最终列表末尾变成新 HumanMessage；改为两个独立响应 ID 的固定数据，保留“最终回答 done”和全部通知顺序断言，未修改生产逻辑或放宽验证。新 blocked_app 测试 helper 仅增加可选 observer，让消费者在运行前通过正式订阅注册，不修改产品订阅快照或 conftest。
- 新 26 例覆盖：启动窗口、接受登记后延迟通知、旧 run 清理与新 run 重叠、双会话、重复文本、队列优先级、停止幂等/审批中断前未生效、Python 主异常与部分已交付、审批准备失败/HITL 跳过、锁外消费者、消费者及发布故障、关闭后内存和元数据 subject 不可改。Event/Barrier 同步，无紧 sleep。
- 最终 `.venv/bin/python -m pytest -q` → `697 passed in 16.20s`。
- `.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` → 退出 0；项目没有独立应用打包构建目标，此项是语法编译检查。
- `.venv/bin/python -m pip check` → `No broken requirements found.`；实际版本 LangChain 1.4.2 / LangGraph 1.2.12。
- 本片 21 文件的存在/代码行数/尾空白检查 → `touched_file_structure=PASS files=21 python_line_limits=PASS trailing_whitespace=none`。git diff --check 退出 0；仓库仍无提交且文件未跟踪，该命令不代替显式文件检查。
- `.venv/bin/python -m scripts.probe_message_events --with-control` → `real_message_events=PASS events=20 messages=5 model_calls=2 tool_calls=1 turns=2 controls=1 pairing=verified terminal=completed`。默认 heiyu-gpt-5.5；纯工具内调用正式 app.steer，实际多一 HumanMessage，控制 accepted/delivery_started/delivered 与消息 ID 配对通过。仍最多 2 逻辑请求、1 纯工具、95 秒，未改配置或输出正文/凭证。默认探针模式与新模式均有离线测试；这不是其他供应商、真实审批/停止或业务效果评测。

## 完成范围与下一步

ControlObserver 不保存正文历史，活跃映射和通知仅在进程内使用。SessionService 从 480 行按控制职责拆分，最终 391 行；异常类和所有既有公开方法签名/日志保持。没有 SDK/工具权限、重试、停止优先级、恢复工具策略或 API 返回结构变更；私有 pending 增加 requested_run_id，并在两处安装/审批恢复时同步。

回调仍为轻量受信任同步消费者，慢或递归控制回调没有非阻塞保证；通知队列不是持久事件总线。状态交付不表示供应商已读或 checkpoint 保存；actual approval_hook 不表示工具执行；无法证实时 unknown。私有 HITL action 观察点依赖已验证框架版本，升级需要先运行批准/拒绝/失败重试兼容性测试。活动取消、跨重启恢复、多租户/远端权限均未由此实现。

唯一下一步：设计上下文过程事件切片，把已有请求预算、压缩候选/摘要开始结束、替换确认与溢出恢复从独立日志接入同一订阅，先明确原始事实和状态确认的不同边界；随后迁移 CLI 消费并进入最小持久会话。本片核心代码待一起学习，不把助手实现算用户掌握。
