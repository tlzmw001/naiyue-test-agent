# Spec 062：应用 run 事件基础与订阅接口

状态：2026-10-08，用户授权后已按 TDD 实施；验证记录见第 9 节。仅同步应用 run 元数据事件，不代表整个事件/消息阶段完成。

后续 [Spec 063](063-model-tool-event-integration.md)已扩展模型/工具中间事件。当前完整 run 序号包括这些事件，纯文本应用终局为 4；本文件第一片的 1/2 示例只描述未接中间事件时的历史验证或独立发布器，不作为当前应用终局序号固定契约。

## 1. 本片目标

让调用方订阅 LocalApplication 的 run_started/run_finished，在应用清理完成后收到唯一终局；原 app.run/resume_tool_approval 返回值与异常保持。只接运行生命周期，不迁移模型/工具/正文事件、不改 CLI 展示、不实现活动取消/异步/持久化。

整个运行事件阶段分片见 [Spec 061](061-event-message-lifecycle-design.md)。本片元数据事件不包含消息、工具参数/结果或审批目标。

## 2. 新增接口与兼容

- LocalApplication.subscribe_events(handler)：注册轻量同步消费者，返回 unsubscribe 函数；原构造/装配/run/resume 方法签名保持。
- SessionService 内部新增相同订阅能力供 LocalApplication 转发。订阅属于一个应用实例，不是进程全局；每次 run 接受时取得订阅快照。取消或新增订阅只影响后续接受的 run，本片不做活动 run 热订阅。
- 回调接收一个 RuntimeEvent，返回 None。不支持 async 回调，注册时对可识别的协程函数明确拒绝；回调返回 awaitable 也作为消费者配置错误，不能假称已执行异步消费者。
- app.run 的正常结果/原异常不经事件重建。尚未订阅时运行仍正常；默认安全日志消费者只写白名单元数据。
- 旧 runtime run_event、模型/工具日志继续兼容；新增日志字段为 application_event，明确来源，不将旧 runtime 终局转发为新的应用终局。

## 3. 值对象与字段

RuntimeEvent 放在 agent_runtime/events.py，使用 frozen dataclass。data 使用有限的冻结 payload 值对象，不使用可任意修改的嵌套字典；本片不保存模型消息对象。

| 公共字段 | 含义 |
| --- | --- |
| event_type | run_started 或 run_finished |
| session_id | 已验证存在的应用会话 ID |
| run_id | 此次应用执行的新 ID；审批恢复也是新 run |
| sequence | 此 run 的发布序号，从 1 开始递增；首片正常路径为 1、2 |
| timestamp | 发布时生成的 UTC ISO 8601 时间字符串；顺序以 sequence 为准 |
| data | 下述固定 payload；没有正文或原异常 |

run_started.data：mode=message / approval_resume。message 是普通 app.run，不表示输入已经成功提交图状态。

run_finished.data：status=completed / stopped / interrupted / failed；stop_mode 仅 stopped 时为 after_turn，其余为 None；manual_review_required 为应用本次清理时观察到的布尔值；failure 在 failed 时为冻结的安全诊断，其余为 None。

failure 字段：stage、category、reason、error_type。stage 只表示捕获失败的应用阶段：prepare / execute / finalize / cleanup，不声称错误最初发生的内部函数。已有可信模型错误分类复用 classify_model_failure；无法确定时保留 unknown/unclassified。Write/Bash 结果未知用明确的 tool_outcome_unknown reason 与人工核对标志；普通异常类型不单独证明故障来源。error_type 是受限安全类型名，不写 str(error)、cause、traceback 或 exception 对象。

status 由结果结构和应用处理决定，优先识别有效 pending approval 为 interrupted，之后 run_stopped=True 为 stopped，否则 completed；处理结果本身失败则 failed。保留现有返回值，不用最后一条 AIMessage 文本推断终局。图返回后应用审批校验失败也必须是 failed。

thread_id/checkpoint_id 继续内部使用，终局不含 persisted=true 或可恢复承诺。run_id+sequence 标识当前运行的事件，不新增独立 event UUID/branch ID。

## 4. 发布、订阅与依赖

```text
LocalApplication.subscribe_events
            ↓
应用订阅注册表（application/run_events.py）
            ↓ 每 run 冻结消费者快照
每 run 发布器（agent_runtime/events.py）
            ↓
轻量回调 / 安全日志消费者

SessionService 开始/完成执行 → 同一个每 run 发布器
```

事件类型与发布器不 import application。应用负责装配并调用发布器。未来 runtime 内容生产点获得同一个发布器的注入，另片接线；本片无需把发布器放入 checkpoint 或改变 agent.invoke 输入。

每 run 发布器有独立序号/消费者故障状态；没有无限历史缓存。每 run 发布按序串行调用消费者，发布锁不与应用会话锁共用。不能持有会话锁调用任意消费者，回调可读应用会话状态；回调不得运行 Agent、提交控制请求、调用工具或承担关键存储/网络工作。此限制属于消费者契约，本片不保证任意第三方 callback 无阻塞。

不同 run 不使用一把发布锁；不同会话可并发发布，消费者自身若共享集合须线程安全。同 run 序号是观测顺序，不是并发工具执行全序的证明。

可选消费者抛普通异常时：记录固定诊断和安全异常类型、本 run 停止调用此消费者，继续通知其他消费者并保留运行结果/原异常。取消订阅幂等。框架控制和进程控制异常不按普通 observer 错误吞掉。同步回调没有可靠强制超时保证；慢/远端消费者接入须另做有界异步传输。

## 5. 普通 run 的接线时点

1. 按现有顺序验证 session 存在、人工核对/待审批/busy 门禁；失败不生成新 run 事件。
2. 在锁内安装 busy/run_id/队列状态并取得会话字段；安装过程无消费者调用。
3. 发布器/订阅快照在门禁通过后、安装 busy 前准备；此处不调用消费者，准备失败不留下已接受的 busy 状态。出锁后进入受保护的执行生命周期并报告 started；消费者异常按第 4 节隔离。
4. prepare：准备本 run 的项目指令/Skill，保持原来冻结规则。
5. execute：调用现有 runtime 同步入口，保留 checkpoint/模型/工具行为。
6. finalize：验证并安装待审批，整理返回结果，或设置副作用未知阻断；确定终局。
7. cleanup：清理 busy/run_id/指令快照、队列和停止标志，保留现有丢弃日志；待审批和人工核对标志按现有规则保留。
8. 出会话锁后报告 finished；消费者此时可以观察到正常路径 busy=False。返回原结果或继续抛原异常。

finished 的冻结数据描述已结束的这个 run。若其他调用方已开始同会话的新 run，读取实时 session 可能看到它再次 busy；不能用旧事件推断当前会话必然空闲，也不保证不同 run 的全局事件全序。清理完成的断言针对对应 run，callback 的 busy=False 检查使用无第二运行竞争的确定性场景。

即使 prepare 或 finalize 失败，也有相同 run_id 的 failed。failed 路径不产生 completed。主异常优先，清理/观测次级异常有安全诊断，不覆盖原异常；只有清理失败也应 failed，不虚报 busy 已清理。进程突然退出/发布基础损坏的终局交付不能由普通 callback 保证。

## 6. 审批恢复接线

先验证待审批、忙碌、manual_review 和每项决定。一次性 grant 的构造放在可清理的 prepare 范围，避免已设置 busy 后构造异常却绕过清理。非法决定启动前拒绝；合法决定接受后 grant 构造失败为 prepare failed。

接受后生成新 run_id，started.mode=approval_resume。原 pending/message_count 保留供既有恢复逻辑使用；恢复后安装 next_pending，再 cleanup/finished。再次中断是 interrupted；正常是 completed 或 stopped；失败保留原异常及原有人工核对规则。首片不重新定义审批失败后安全重放，也不增加自动恢复。

## 7. 影响文件与必要拆分

当前 application/__init__.py 为 475 行，agent_runtime/__init__.py 为 468 行。事件接线不能继续堆到大文件；预计 4 个产品文件、2 个新测试文件、1 个既有测试定位调整及阶段文档，超过 8 个文件，但不改变多项业务能力。

- 新 agent_runtime/events.py：有限事件值对象、每 run 发布器和发布约束；不改现有大 runtime 文件。
- 新 application/run_events.py：应用实例的订阅注册、快照和白名单日志适配。
- 新 application/session_service.py：迁移 SessionService 及它实际使用的审批解析/错误类型，以会话职责拆分；在原 run/resume 两条链路接入生命周期。现有方法和错误类型从 application/__init__.py 重导出，公开导入路径保持。
- application/__init__.py：保留 LocalApplication 与原装配入口；新增 subscribe_events 转发，导入会话服务。load_model_catalog 原入口及 tests 的装配 monkeypatch 保持原位置。
- 新 tests/test_run_events.py：冻结字段/载荷、序号、unsubscribe、隔离及日志脱敏。
- 新 tests/test_application_run_events.py：经实际应用/create_agent 的 run/stop/HITL/恢复/并发测试。
- 新 tests/test_application_run_event_failures.py：失败、控制异常、清理次级故障及审批 grant 构造故障测试；为保持测试文件低于 500 行，从本片新增测试中按职责拆分，复用 make_app，不改 conftest。
- tests/test_application_skills.py：现有发现后文件消失反例在迁移后仅将 invoke_agent_run_with_resources 的 monkeypatch 指向实际调用模块；保留异常、消息与内容断言，不能把源代码迁移变成放宽测试。

实现前先核对所有使用 SessionService 所在模块全局函数的测试和脚本；若发现其他依赖注入入口，更新本 Spec 的定位迁移清单后执行，不保留无效 monkeypatch。迁移仅为模块上限与职责，不删兼容入口或升级依赖。若新文件接近 500 行，先调整接线粒度，不把无关权限/模型/工具重构带入。

## 8. TDD 与验收

先新增最小事件接口/订阅测试并确认因缺少实现失败，再实现事件基础；随后应用生命周期测试先红后绿。文件迁移后的定位调整属于测试接线修正，需保留反例有效性，不当作 TDD 红灯。

至少覆盖：

1. 正常 started(seq=1) → finished(seq=2, completed)，ID 一致，结果保持，终局 callback 观察 busy=False。
2. prepare、execute、finalize 异常分别 failed，保留原异常对象；存在主异常和清理次级失败时仍保留主异常，只有清理失败不报告 completed。
3. 错误 ToolMessage 可继续到 completed；未处理工具异常到 failed。
4. turn 后停止是 stopped/after_turn；不产生 cancelled。
5. HITL 安装 pending 后 interrupted；恢复新 run_id/approval_resume；恢复再次中断或异常终局明确。
6. 启动前 session/busy/待审批/人工核对/非法审批决定拒绝没有新 started；接受后 grant 构造失败清理并 failed。
7. 副作用未知 manual_review_required=True，后续运行继续被拒绝，不重放工具。
8. 一个 observer 失败不影响另一个及业务结果；故障 observer 只在当前 run 隔离；unsubscribe 只影响后续快照。
9. 同应用双会话事件不会交叉 ID/序号；不同应用订阅隔离；测试用屏障或事件同步，无极短 sleep。
10. 日志和元数据没有合成敏感正文/参数/异常字符串/thread_id；冻结 payload 不可被前一个消费者篡改。
11. 原公开构造/方法/导入保持，旧日志断言保持；CLI 没有新增重复输出。

定向：.venv/bin/python -m pytest -q tests/test_run_events.py tests/test_application_run_events.py tests/test_application_run_event_failures.py。全量：.venv/bin/python -m pytest -q；.venv/bin/python -m compileall -q naiyue_test_agent tests scripts；.venv/bin/python -m pip check。检查产品单文件行数。真实模型可在确定性测试之后按当前授权独立验证，本片不依赖新账号。

## 9. 实施与验证记录（2026-10-08）

- 基线全量 551 passed in 15.76s。新增事件测试首次因模块缺失 collection error；独立应用测试 10 failed in 0.70s，均因 subscribe_events 缺失。实现后初始专项 16 passed，再覆盖清理、审批 grant/执行失败、再次中断、进程控制和诊断数据约束。
- 失败 payload 的四个反例先 4 failed，补安全字段检查后转绿；诊断代码异常/非法码覆盖原异常的反例先 2 failed, 1 passed，补安全诊断回退并保留原异常；旧 run 在清理后读到新 run 的人工核对状态，确定性反例先 1 failed，改为清理锁内快照后转绿。不修改旧业务断言或重试策略。
- 最终专项 30 passed in 0.64s。模块迁移后的会话/组合/Read 审批/停止/Skill/CLI 52 passed in 0.97s。最终全量 581 passed in 15.42s；compileall 退出 0，pip check 为 No broken requirements found.
- AST 核对原 SessionService 既有方法签名全部一致，19 个非生命周期方法体保持；application 仍重导出原会话类/错误，build_local_application 的模型加载位置保持。test_application_skills 只更换发现后文件消失探针的真实调用模块，原错误 ToolMessage/最终回答断言保留。
- 生产文件行数：events 129、run_events 105、session_service 472、application/__init__ 96；三个新测试均低于 500 行。source snapshot 仅用于本次迁移核对，不是产品存储。没有提交或清理整个 untracked 工作树。
- 有界真实模型验证：关闭托管 tracing、读取既有 .env/models.json 默认网关，在父进程 120 秒上限的子进程内调用一次无工具 app.run。实际输出 real_run_events=PASS events=2 terminal=completed correlation=matched cleanup=verified。验证完整非空 AI 响应、两个事件同 session/run、序号及 busy 清理；不验证网关模型身份、业务质量或失败恢复。随后两项修复只改变失败诊断保护/终局快照，正常调用路径保持。
- 非 Exception 进程控制或 GraphBubbleUp 原样传播并清理，不伪造 completed/failed/cancelled；可能只有 started，无终局交付保证。这是当前非活动取消边界，下一阶段需单独定义取消终局。发布器失效也不能保证消费者收到 finished，但次级报告异常不覆盖主异常。
- 本片只有轻量同步消费者；慢 callback 可能阻塞调用方。没有消息正文/turn/模型工具事件订阅、热订阅、活动取消、异步、数据库或 CLI 展示改造。用户尚未阅读新增代码，功能验证不能视为已掌握。
