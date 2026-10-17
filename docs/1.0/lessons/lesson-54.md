# Lesson 54：发布器队列、重入与监听交付

> 第四章第3主题，2026-10-12。用户自述已跑主分支与交付分支测试，并复述订阅→发布器快照→消费者调用；本轮校准创建频率、取消订阅返回位置/作用范围及健康监听条件。属用户报告的本人走读与复述，不等同独立实现或直接观察终端证据。

## 三份状态与锁

RunEventPublisher的_pending是待交付事件deque，_delivering标识本发布器已有排空循环，_failed_handlers存本发布器失效监听者下标。publish在RLock内校验数据、编号、入队，若已经交付则返回；否则标true、while队列非空popleft并_deliver，finally恢复false。_deliver依次调用可用监听者。

RLock允许同线程回调内再次publish；普通不可重入Lock会在再次取得同一锁时卡住。其他线程须等待锁；当前锁覆盖同步监听调用，因此慢监听者会阻塞该发布线程及等待同一Publisher锁的线程。序列化事件不等于工具执行串行，不承诺固定延迟或异步投递。

## 主例：监听者内部发布新事件

测试：[test_observation_isolation.py](../../../tests/test_observation_isolation.py)::test_reentrant_notifications_preserve_listener_order。

两个监听者first/second；first收到seq1时publish第二事件。测试用run_started/run_finished作合法payload排队示例，没有实际运行/模型，也不作为生产中提前发布终局的建议。

```text
first(1)
  → 内层publish把2入队，见_delivering=True即返回
second(1)
  → 外层循环继续，取出2
first(2)
second(2)
```

实际observed=[('first',1),('second',1),('first',2),('second',2)]。避免first(1)中递归交付2导致second先收到2后收到1。first函数定义时publisher=None，调用时读取外层已经赋值的对象，不是把定义时的None固定保存。

## 分支与证据

| 条件 | 行为 | 既有测试 |
|---|---|---|
| 首次publish、无交付循环 | 当前线程启动排空，每个事件依次交付 | 重入测试的外层正常路径 |
| 同线程监听内部publish | 只编号/入队，不另开交付循环 | test_reentrant_notifications_preserve_listener_order |
| 监听抛普通Exception | 安全日志，登记失效下标，继续其他监听；该run后续跳过，新Publisher可再尝试 | test_faulty_observer_is_isolated_per_run_and_not_exposed_in_logs |
| 注册/取消发生在Publisher创建后 | 既有Publisher保留监听tuple快照；后续Publisher用新名单，取消可重复调用 | test_subscription_snapshot_and_idempotent_unsubscribe |
| 异步函数或不合法回调返回 | 注册时拒绝明确async；普通函数返回awaitable/非None在交付时失败隔离，coroutine会close | test_async_callbacks_are_rejected_without_running_them（覆盖async/伪装coroutine） |

后三例在[tests/test_run_events.py](../../../tests/test_run_events.py)。普通返回非None另由生产代码合同限制，本测试未独立覆盖该返回分支；不把已测范围扩大。BaseException不属于普通隔离范围，after finally恢复标记不表示吞异常。

## 验证与观察

```bash
.venv/bin/python -m pytest -q tests/test_observation_isolation.py::test_reentrant_notifications_preserve_listener_order
```

优先该单元入口observed=[]断点，无参数化。publish帧看self._sequence/self._delivering及[(e.event_type,e.sequence) for e in self._pending]；first/second帧看event.sequence和observed；_deliver帧看index、self._failed_handlers、handler名称。内部publish返回时第二事件仍待交付；外层publish返回后_pending空、_delivering=False。

本轮上述4个用例4 passed in0.87s；无生产/测试实现更改、实网或性能证据。测试末断言在监听隔离之外，不能把监听中断言失败被吞当测试成功。

下一主题完整消息观察：message_completed/revised、历史基线/移除/去重；delta留随后第5主题。

## 用户复述与同步/异步取舍补充

- 用户后续明确确认先保留当前实现，并继续完整消息观察；当前无异步发布重写授权或新增实现范围。
- handler可称消费者的调用入口，支持函数、绑定方法或同步可调用对象。订阅管理器由应用实例拥有，publisher按run创建；subscribe返回unsubscribe，publisher方法返回Publisher，旧快照不受后来取消/新增影响。每次事件仅调用本run快照中的可用回调，过滤回调可忽略事件，失效回调跳过。
- 正确性先定义合同：同run每个消费者看见有序事件、重入不乱序、普通观察故障不改执行、取消影响范围准确；单测证明实现符合这些合同，不自动证明整体需求选对或性能合格。A1→B1→A2→B2是当前更强的本地回调顺序，后台任务/网络客户端完成不由它保证。
- 当前建议保持同步有序核心，慢工作通过消费者异步边界处理。前提是原地回调开销实际可接受；尚无代表性延迟证据。CLI打印、安全日志后端、Web序列化/投影仍在回调内，不能称完全非阻塞。不要把必要保存迁到可降级观察队列。
- 已有AsyncEventStream：同步__call__写有界deque、Lock保护并call_soon_threadsafe唤醒；__anext__异步读取。Web DeliveredRun.observe同步序列化/投影/缓存并唤醒，stream异步输出SSE，网络发送与Publisher回调分开。不存在新增通用异步dispatcher实施授权。
- 本轮既有test_async_stream_delivery.py::test_thread_delivery_wakes_async_consumer_and_preserves_order与test_overflow_is_explicit_and_does_not_cancel_run：2 passed in0.81s。另Event/Thread临时探针确认慢回调未放行前publish与第二监听都等待，放行后完成；这是行为限制证据，不是性能基准，无生产/测试实现修改。
- 本机Pi a69bef789：agent-loop await emit→Agent.processEvents逐个await listener(event,signal)，run idle等待监听结算；AgentSession._handleAgentEvent先await扩展、再_emit同步通知自身监听，message_end还处理保存。AgentX3fcfa0e：onPartialResponse→transport.sendMessage→SseMessageTransport.safeSendMessage→emitter.send，回调内直接调用且处理连接异常。静态源码核验，不宣称端到端非阻塞或已运行参考项目。
- 官方核对：Python3.12 asyncio.Queue非线程安全，跨线程调度用call_soon_threadsafe；LangGraph streaming文档与当前项目模型流消费方式一致。异步顺序await仍等待慢消费者；独立有序队列可隔离，但须先定义容量/溢出/关闭排空/故障/完成时点，不按函数名直接选型。
