# Lesson 64：异步任务归属与消费断开

> 第五章第7主题，2026-10-13。执行任务与事件消费独立，应用登记并负责收尾；用户本人走读/理解未评估。

## 对象与调用链

iterator=app.astream_run(...)是调用方持有的async生成器；AsyncEventStream是内部有界事件桥；task是produce运行Task，app._tasks是应用登记的运行任务集合，与RunControl._model_tasks不同。调用生成器先取得对象，首次__anext__/async for才执行内部启动逻辑。

```text
astream_run → _stream
  → 新run_id、订阅匹配session/run的AsyncEventStream
  → create_task(produce)并登记app._tasks/done回调
produce → _execute → SessionService.arun → run生命周期
事件 → 桥队列 → async for yield给调用者
produce finally → stream.finish → 消费者排完队列 → await task取得失败或结果
```

_execute登记当前Task，already_owned决定是否由本层在finally撤销。直接arun使用调用方当前Task；_stream预先登记producer，_execute不提前撤销，_background_done负责移除并读取/安全记录失败。登记任务不自动免于取消；消费者通过独立桥读取事件，断开时不向生产Task发cancel。

## 分支

| 分支 | 合同 |
|---|---|
| 生产正常完成 | finish标记关闭并唤醒，保留已排队事件供消费者取完，然后迭代结束 |
| 生产失败，消费者仍读 | finish仍唤醒，消费尾部await task传播原异常；后台done回调读取异常用于诊断 |
| 调用方iterator.aclose | 外层生成器关闭内部迭代器，_stream finally调用桥close；退订/清队列，不取消独立producer，busy保留到实际收尾 |
| 有界队列overflow | 清空该桥、标记overflow，消费者收到EventStreamOverflow，生产继续；如何重同步留Web章 |
| 新stream请求被busy拒绝 | 新run_id过滤避免混入旧run；producer finally.finish唤醒空事件流，尾部await task传播SessionBusyError |

AsyncEventStream.finish是生产结束信号，保留队列；close是消费者清理，退订并清队列。退订不会改现存Publisher的handler快照，但关闭桥的__call__会直接返回，不再积累事件（第四章合同）。事件EOF不能当作业务成功，需看终局/原异常。

## 应用关闭

aclose先拒绝活动run上下文内调用，避免任务等待自己/未结束运行。随后_check_open核对所属事件循环和关闭状态，_closing=True阻止新的执行入口；await_settled(_close_resources)先gather已有_tasks（return_exceptions=True，异常安全记录），再catalog.aclose；finally标记_closed。它按等待顺序关闭资源，内部await_settled引用Lesson63。持久化saver/存储由外层context管理，留第8章；关闭标记本身不能证明每项资源关闭成功。

## 既有测试与证据

- tests/test_async_stream_delivery.py::test_stream_disconnect_keeps_owned_run_and_persistence：关闭iterator后producer未done、busy仍True；放行fake模型后回答saved、持久会话ready、订阅清空。
- 同文件test_overflow_is_explicit_and_does_not_cancel_run：capacity=1，消费显式溢出，producer继续，最终状态可读answer。
- 同文件test_busy_stream_rejects_without_hanging_or_observing_other_run：第二个同会话stream得到SessionBusyError，无旧run事件。
- 以上3例3 passed in1.04s；含本机临时SQLite保存，不是重启/线上证据。
- 同文件test_failed_partial_output_remains_unconfirmed：无Event等待窗口，适合重用作_stream主链单步；本课只追producer启动、finish、尾部await task传播同RuntimeError，delta/projection引用第四章。
- tests/test_runtime_entry_cleanup.py::test_central_run_services_still_prevent_closing_from_active_run：绑定合成RunServices上下文验证关闭门禁，退出上下文后成功关闭；没有真的并发活动run，不冒称本例覆盖关闭等待活动任务。
- 后两例2 passed in0.51s。关闭等待顺序另有当前源码依据，本轮未专门故障注入关闭过程中活动任务的组合。

## 阅读深度

必须展开_stream任务创建/登记、produce finally、消费尾部await task、finally.close；_execute的already_owned；_background_done；aclose/_close_resources顺序。桥只看finish/close差别与session/run过滤，线程唤醒/锁/deque/投影、asyncio内部、模型/存储/driver步过。

_stream帧看run_id、task.done()、task in self._tasks；消费者测试帧看producer.done()、会话busy、订阅数量；桥帧看_closed/_overflow/len(_items)。有5秒协调窗口的三例连续运行协调段，不长时间停断点。不要在WATCH调用__anext__/aclose/cancel等改变状态的方法。

无生产/测试实现、配置或实网更改。下一步第8主题并发/竞态与第五章全链路收束。
