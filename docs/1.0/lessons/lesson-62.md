# Lesson 62：用户停止与外部取消

> 第五章第5主题，2026-10-13。先模型调用前停止，再原生异步模型等待与外部取消。线程IO/已开始工具的settle留第6主题。用户本人调试/理解未评估。

## 两条主链

```text
request_stop(session, run_id)
 → 会话锁内校验目标/窗口，stop_requested=True、steering_open=False
 → 当前run的RunControl.request（signal、取消已登记模型task）
 → 模型前检查跳end，或等待中CancelledError转UserStop
 → StopModelMiddleware返回空模型结果/更新run_stopped
 → StopBoundaryMiddleware跳end → 应用结果stopped → 必要收尾 → 调用者得到停止结果

外部 task.cancel()
 → CancelledError保持传播 → 应用cancelled/人工核对阻断
 → 必要收尾 → 终局failed/task_cancelled → 调用者仍收到CancelledError
```

## 请求数据与合同

RunObservation为每个run创建RunControl，其signal是线程Event、_model_tasks是该run登记的模型等待Task集合。request_stop检查busy，可选run_id必须匹配；重复请求在同一活动run内返回[]；关窗/finalize后拒绝新停止。首次返回队列中尚未处理的steering/follow_up正文供调用方保留，收尾会丢弃队列，不自动启动下一run。

RunControl.request锁内set信号并取task快照，锁外通过每个task的loop.call_soon_threadsafe安排_cancel；_cancel只对尚未结束Task调用cancel。cancel是异步取消请求，不是任意线程/远端模型服务器强杀保证。sync wrap_model_call直接调用handler；原生async模型由选模middleware使用await_model包住handler，fallback路径不能泛化主动等待取消。

## await_model关键判断

开始时信号已置位则raise UserStop，不创建新模型task。否则create_task(call())、登记、await task；收到CancelledError后，仅requested且当前等待任务cancelling()==0时转为UserStop，否则裸raise。两项分别确认本run的用户信号与等待者本身未收到外部取消请求；finally移除登记。

UserStop继承BaseException，普通except Exception不会自动捕获；项目在模型观测与StopModelMiddleware显式处理它。StopModel返回ExtendedModelResponse(ModelResponse(result=[]), Command(update={'run_stopped':True}))，不制造AI回答。after_model边界检查停止，必要时为未执行工具调用补解释性结果并跳end；工具细节下一课/第6章。

stopped来自受控退出结果；真实工具或必要保存错误仍可能failed，不能只看stop_requested就宣布stopped。外部取消当前策略还设置manual_review_required；解除busy不代表允许马上重新运行。

## 测试与阅读

- tests/test_active_stop.py::test_stop_before_model_sends_no_request：run_started回调直接停止，model.seen为空，仅Human消息；适合入口单步，无5秒等待窗口。
- 同文件test_stop_cancels_model_without_fake_answer_and_can_start_again：fake _next进入后等待release；先拒绝stale runID，正常stop返回保留正文，重复返回[]；取消等待后run_stopped=True、无AI回答、checkpoint安全、本run终局stopped；release后下一run消费未用的next回复。
- 同文件test_external_cancel_remains_failed_and_is_not_user_stop：直接cancel运行Task，调用者收到CancelledError，终局failed/reason=task_cancelled/manual_review_required=True。
- 本次上述3例3 passed in0.53s；均离线内存/假模型，无生产/测试实现或真实网络变化。
- 必须展开SessionControlMixin.request_stop的目标/状态，RunControl.request/_cancel/await_model，StopModelMiddleware的except UserStop及返回值；模型前hook/应用result/failed/收尾引用已学机制，after_model工具配对仅看合同。
- 步过asyncio Task/事件循环/ensure_future、模型与框架内部、日志与观察器、应用装配/aclose。等待型两例有5秒wait_for，协调区间连续运行后看结果，不能在超时窗口长时间停断点；不修改IDE/超时。
- request_stop帧看session['run_id']/stop_requested/steering_open与retained；RunControl方法帧看self.signal.is_set()、len(self._model_tasks)；_cancel帧看task.done()，取消调用后不能立即假定Task已结束。对cancelling的调用只读，注意当前Task就是await_model正在执行的等待者。

下一步第6主题run_blocking/await_settled与已开始工具IO的收尾。
