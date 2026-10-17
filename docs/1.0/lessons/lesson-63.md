# Lesson 63：已开始操作的settle

> 第五章第6主题，2026-10-13。等待已开始操作结束，再释放所有权；不等于成功、不等于回滚。用户本人调试/理解未评估。

## 关键合同

await_settled接收awaitable，用ensure_future取得内部Task，再shield等待。外层被取消时shield不把该取消传给内部操作，外层仍收到CancelledError；记录第一次外层取消并继续等待同一个内部Task。内部成功后若有已记取消就抛取消，无取消则返回值；内部自身cancelled直接传播；普通内部故障无已记取消则传播原故障，有已记取消则安全记录故障类型、传播先前取消。最后两种控制边界本课依据源码，不把所选集成测试说成所有分支覆盖。

```text
外层取消 → await_settled记住取消 → 内部操作继续
 → 内部Task结束 → 外层传播取消 → 流程finally/必要收尾 → busy释放
```

await_settled不重试业务操作，每次while重等同一Task；shield不能保护内部Task免受其他持有者的直接cancel，也没有无限阻塞操作的固定停止上限。run_blocking是to_thread(call,...)加await_settled；第2节arun_steps无async_call走它，critical异步操作走await_settled。acquire_blocking只有一句合同：取消资源获取时，等获取完成后释放已经取得的资源，内部留第8章。

同步工具的实际路径是工具观察wrapper.await_settled(handler(request))，框架StructuredTool.ainvoke对无coroutine工具用run_in_executor执行self.invoke；不能误称所有同步工具直接调用本项目run_blocking。原生async工具也在该wrapper保护范围内。用户停止不会任意取消已开始工具，工具调度在真正启动前检查run信号；外部取消等待才需要捕获/延迟传播。

## 状态与证据

| 状态 | 结果 |
|---|---|
| 操作已开始，用户stop | 等已开始工具结束，保留实际结果，未开始工具回填not_executed，再受控结束 |
| 操作已开始，外部cancel | 内部操作继续，busy保留且无终局，结束后仍传播CancelledError并记录failed/人工核对 |
| 真实写入已开始，用户stop | os.replace放行后新文件可见，工具success而run_stopped；不回滚已完成副作用 |

## 测试与走读

- tests/test_async_tool_batch.py::test_sequential_batch_preserves_full_chain_order[False]：无Event窗口，正常工具返回进入await_settled，适合慢速单步。
- 同文件test_fatal_batch_does_not_enter_later_tools：内部RuntimeError原对象传播，后续工具不启动。以上两例2 passed in0.77s。
- tests/test_async_builtin_io.py::test_sync_tool_runs_off_loop_and_cancel_waits_for_settlement：线程ID不同，外部cancel后wait_for(shield(run_task),1)预期超时，busy仍True、无终局；放行后finished=True，原取消传播，busyFalse、failed/人工核对。测试shield保护被检查的run Task；生产shield保护内部操作，两者对象不同。
- tests/test_active_stop.py::test_stop_drains_started_tool_but_skips_waiting_tool[True]：只选串行参数，已开始writing返回committed，later未执行，停止期间拒绝重叠run。
- 同文件test_stop_during_real_write_waits_for_atomic_replace：真实临时目录write，仅拦住os.replace；stop前后old→new，工具success，run_stopped。
- 后三例3 passed in1.87s；共5个既有用例，两次执行。无生产/测试实现、环境或实网变更。
- 必须展开await_settled的Task创建、shield、cancel分流、保存取消、重新等待、最终传播；run_blocking看两层组合。工具wrapper看调用点，调度/权限/写入算法留第6章；线程执行器/asyncio内部/模型/日志/收尾步过。
- 正常入口断点后到_record_tool_call.awrap_tool_call展开await_settled；WATCH task.done()、task.cancelled()、cancellation，result等赋值后再看。取消/阻塞集成有1/5秒协调窗口，连续运行窗口后读断言，不长时间停断点，不改超时。

下一步第7主题异步运行任务归属、事件消费者断开与应用关闭。
